"""Логика админ-панели (§ docs/admin.md).

Собирает данные для каждого экрана из access/store/tracking/ai и отдаёт готовый
MessageSpec из ui.admin. Роутинг (settings.dispatch) делегирует сюда через send_*.

Все функции — async send_*(bot, cid); гард на владельца — в settings._admin_guard.
"""
import logging
import asyncio
import glob
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import access
import config
import deploy_report
import provider_runtime
import rich_delivery
import service_monitor
from ui.constants import delete_label, ui_label
import store
import tracking
from ui import admin as ui
from ui.navigation import nav_row

_log = logging.getLogger(__name__)

DAY = 86400
_MONTHS_RU = ("", "янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

async def _show(bot, cid, msg, reply_markup=None, q=None):
    await rich_delivery.show(
        bot, cid, msg, reply_markup=reply_markup, query=q,
    )


def _log_date_time(ts) -> str:
    """Календарная отметка инцидента: в журнале нельзя оставлять одно время."""
    try:
        moment = datetime.fromtimestamp(ts, config.TZ)
        return f"{moment.day} {_MONTHS_RU[moment.month]} · {moment:%H:%M}"
    except Exception:
        return "—"


def _updated_at(ts=None) -> str:
    moment = datetime.now(config.TZ) if ts is None else datetime.fromtimestamp(ts, config.TZ)
    return moment.strftime("%H:%M")


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def _repeat_suffix(count):
    count = int(count or 0)
    return f" · повторилось {count} {_plural(count, 'раз', 'раза', 'раз')}" if count > 1 else ""


def _error_signature(entry):
    if str(entry.get("source") or "") == "llm":
        kind = str(entry.get("kind") or "")
        # Одна сорвавшаяся цепочка может содержать разные технические ответы
        # Groq, Cloudflare и OpenRouter. Для владельца это один инцидент, а не
        # несколько якобы разных ошибок ассистента.
        if kind == "all-providers-failed":
            return "llm", kind, str(entry.get("section") or "Система")
    return (
        str(entry.get("source") or ""), str(entry.get("kind") or ""),
        str(entry.get("file") or ""), str(entry.get("error") or entry.get("msg") or "")[:240],
    )


# ================= ДОМ =================

# «Инвайт» — зелёная кнопка (явный цвет, не по подписи).
_GREEN = {"style": "success"}


async def send_home(bot, cid, q=None):
    monitor_rows = service_monitor.rows()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить карточки", callback_data="adm_refresh_cards"),
         InlineKeyboardButton("⚠️ Ошибки", callback_data="adm_logs")],
        [InlineKeyboardButton("💾 Прислать копию базы", callback_data="adm_backup"),
         InlineKeyboardButton("🔁 Перезапустить бота", callback_data="adm_restart")],
        [InlineKeyboardButton("👥 Пользователи", callback_data="adm_users")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    msg = ui.home(
        system_rows=monitor_rows,
        version_line=deploy_report.version_line(),
        server_line=server_line(),
        night_line=night_line(cid),
        idle_line=service_monitor.idle_line(),
    )
    await _show(bot, cid, msg, kb, q)


# ================= СЕРВЕР =================

_MB = 1024 * 1024


def _disk_part():
    usage = shutil.disk_usage(os.path.dirname(config.DATABASE_PATH) or "/")
    return f"Диск {round(usage.used * 100 / usage.total)}%"


def _memory_part():
    with open("/proc/meminfo", encoding="utf-8") as file:  # только Linux; на другой ОС строка без памяти
        info = {line.split(":")[0]: int(line.split()[1]) for line in file if ":" in line}
    return f"память {round(100 - info['MemAvailable'] * 100 / info['MemTotal'])}%"


def _database_part():
    path = config.DATABASE_PATH
    size = os.path.getsize(path) + (os.path.getsize(f"{path}-wal") if os.path.exists(f"{path}-wal") else 0)
    return f"база {max(1, round(size / _MB))} МБ"


def _day_and_time(moment):
    today = datetime.now(config.TZ).date()
    day = ("сегодня" if moment.date() == today else "вчера" if moment.date() == today - timedelta(days=1)
           else f"{moment.day} {_MONTHS_RU[moment.month]}")
    return f"{day} {moment:%H:%M}"


def _backup_part():
    newest = max(glob.glob(os.path.join(config.BACKUP_DIR, "*")), key=os.path.getmtime)
    return f"копия базы {_day_and_time(datetime.fromtimestamp(os.path.getmtime(newest), config.TZ))}"


def _uptime_part():
    minutes = int((datetime.now(config.TZ) - deploy_report._STARTED_AT).total_seconds() // 60)
    days, hours = minutes // 1440, minutes % 1440 // 60
    if days:
        return f"работает {days} дн. {hours} ч"
    return f"работает {hours} ч {minutes % 60} мин" if hours else f"работает {minutes % 60} мин"


def server_line():
    """🖥 Диск 41% · память 63% · база 18 МБ · копия базы сегодня 03:00 · работает 2 дн. 4 ч.

    Недоступный показатель (не Linux, нет базы или копий) пропускается."""
    parts = []
    for part in (_disk_part, _memory_part, _database_part, _backup_part, _uptime_part):
        try:
            parts.append(part())
        except Exception:
            _log.debug("server line: %s unavailable", part.__name__, exc_info=True)
    return "🖥 " + " · ".join(parts) if parts else None


_NIGHT_SECTIONS = (
    ("wardrobe", "Гардероб"), ("cooking", "Кухня"), ("learning", "Обучение"),
    ("leisure", "Досуг"), ("myday", "Мой день"),
)
_ARTIST_SOURCES = {"local": "артист дня — из запасного списка", "chart": "артист дня — из чарта, без AI"}


def night_line(cid):
    """🌙 Ночь: всё собралось / не собралось — Досуг (концерты), Кухня; артист дня из резерва — отдельно."""
    night = store._load(config.NIGHT_WARM_KEY) or {}
    cutoff = time.time() - DAY
    fresh = {name: entry for name, entry in night.items()
             if isinstance(entry, dict) and float(entry.get("ts") or 0) >= cutoff}
    failed = [f"{label} ({', '.join(fresh[name]['steps'])})" if fresh[name].get("steps") else label
              for name, label in _NIGHT_SECTIONS if name in fresh and not fresh[name].get("ok")]
    parts = []
    if fresh:
        parts.append(f"не собралось — {', '.join(failed)}" if failed else "всё собралось")
    artist = ((store._load(config.MUSIC_RECO_CACHE_KEY) or {}).get(str(cid)) or {}).get("item") or {}
    if source := _ARTIST_SOURCES.get(str(artist.get("source") or "")):
        parts.append(source)
    return "🌙 Ночь: " + " · ".join(parts) if parts else None


# ================= КОПИЯ БАЗЫ И ПЕРЕЗАПУСК =================

_TELEGRAM_FILE_LIMIT = 50 * _MB


def _db_snapshot(path):
    """Согласованный снимок SQLite (backup API): запись в базу во время копирования не портит файл."""
    source = sqlite3.connect(config.DATABASE_PATH)
    target = sqlite3.connect(path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return os.path.getsize(path)


async def send_backup(bot, cid, q=None):
    back = InlineKeyboardMarkup([nav_row("adm_home")])
    if not config.DATABASE_PATH:
        await _show(bot, cid, ui.notice("💾 Копия базы", "Копия недоступна: база не на диске."), back, q)
        return
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, f"morning-bot-{datetime.now(config.TZ):%Y-%m-%d-%H%M}.db")
        try:
            size = await asyncio.to_thread(_db_snapshot, path)
        except Exception:
            _log.exception("admin backup failed")
            await _show(bot, cid, ui.notice("💾 Копия базы", "Не удалось сделать копию базы."), back, q)
            return
        if size > _TELEGRAM_FILE_LIMIT:
            text = f"База {round(size / _MB)} МБ — больше лимита Telegram в 50 МБ, прислать нельзя."
            await _show(bot, cid, ui.notice("💾 Копия базы", text), back, q)
            return
        with open(path, "rb") as file:
            await bot.send_document(chat_id=cid, document=file, filename=os.path.basename(path),
                                    caption=f"💾 Копия базы · {max(1, round(size / _MB))} МБ")


def _auto_restart_enabled():
    """Ненулевой код выхода поднимает сервис снова только при Restart=always или on-failure."""
    try:
        result = subprocess.run(
            ["systemctl", "show", config.SERVICE_NAME, "-p", "Restart", "--value"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception:
        _log.warning("admin restart: systemctl unavailable", exc_info=True)
        return False
    return result.stdout.strip() in ("always", "on-failure")


async def send_restart_confirm(bot, cid, q=None):
    # Время в callback: после рестарта Telegram может повторить нажатие — старое не перезапускает снова.
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Перезапустить", callback_data=f"adm_restart_ok_{int(time.time())}"),
         InlineKeyboardButton("Отмена", callback_data="adm_home")],
    ])
    text = "Бот остановится и через несколько секунд поднимется сам."
    await _show(bot, cid, ui.notice("🔁 Перезапустить бота", text), kb, q)


_RESTART_DELAY_SECONDS = 3  # Telegram успевает получить ответ и подтвердить нажатие до выхода


async def restart(bot, cid, pressed_at, q=None):
    back = InlineKeyboardMarkup([nav_row("adm_home")])
    if pressed_at < int(deploy_report._STARTED_AT.timestamp()):
        await send_home(bot, cid, q)  # повтор нажатия из прошлого запуска
        return
    if not await asyncio.to_thread(_auto_restart_enabled):
        text = "Перезапуск недоступен: сервис не поднимется сам."
        await _show(bot, cid, ui.notice("🔁 Перезапустить бота", text), back, q)
        return
    await _show(bot, cid, ui.notice("🔁 Перезапустить бота", "Перезапускаюсь…"), None, q)
    _log.warning("admin restart requested cid=%s", cid)
    await asyncio.sleep(_RESTART_DELAY_SECONDS)
    logging.shutdown()
    os._exit(1)  # ненулевой код: systemd (Restart=on-failure) поднимает сервис


async def send_api_check(bot, cid, q=None):
    """Живая проверка всех API: статус ожидания → одна строка на сервис."""
    import util
    status = None
    if q is not None:
        status = await util.StatusManager.start_inline(
            q, bot=bot, cid=cid, stages=((0, "🩺 Проверяю API..."),), preserve_message=True,
        )
    try:
        results = await service_monitor.live_check_all()
    finally:
        if status is not None:
            await status.stop(delete=True)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🩺 Проверить снова", callback_data="adm_api_check")],
        nav_row("adm_home"),
    ])
    await _show(bot, cid, ui.api_check(results), kb, q)


_REFRESH_CARDS = (
    ("myday", "☀️ Мой день"),
    ("wardrobe", "👔 Гардероб"),
    ("cooking", "🥣 Кухня"),
    ("learning", "🧠 Обучение"),
    ("leisure", "🍿 Досуг"),
)


async def send_card_refresh_menu(bot, cid, q=None, *, status=""):
    rows = [
        [InlineKeyboardButton(label, callback_data=f"adm_refresh_card_{key}")]
        for key, label in _REFRESH_CARDS
    ]
    rows.append(nav_row("adm_home"))
    await _show(
        bot, cid, ui.card_refresh_menu(status), InlineKeyboardMarkup(rows), q,
    )


async def _refresh_card_cache(cid, key):
    if key == "wardrobe":
        import wardrobe
        store.clear_wardrobe_daylook(cid)
        return await wardrobe.warm_home_cache(cid)
    if key == "cooking":
        import day_menu
        return await asyncio.to_thread(day_menu.get_day_menu, cid, None, True)
    if key == "learning":
        import learning
        learning.reset_daily_material_cache(cid)
        return await asyncio.to_thread(learning.warm_home_cache, cid)
    if key == "leisure":
        import leisure_concerts
        import leisure_hub
        import leisure_movies
        import vacation
        leisure_concerts.invalidate_user_concerts_cache(cid)
        vacation._clear_leisure_cards(cid)  # фильм, сериал и артист дня — по-настоящему новые
        await leisure_movies.warm_movie_premieres_cache(cid)
        return await leisure_hub.warm_hub_cache(cid)
    if key == "myday":
        for dependency in ("wardrobe", "cooking", "learning", "leisure"):
            await _refresh_card_cache(cid, dependency)
        import myday
        myday.reset_day_cache(cid)
        return await myday.warm_day_cache(cid)
    raise ValueError("unknown admin card")


async def refresh_card(bot, cid, key, q=None):
    labels = dict(_REFRESH_CARDS)
    label = labels.get(key)
    if not label:
        await send_card_refresh_menu(bot, cid, q, status="⚠️ Карточка не найдена.")
        return
    import util
    status = None
    if q is not None:
        # Зелёная плашка ожидания под меню, как в разделах, пока карточка собирается.
        status = await util.StatusManager.start_inline(
            q, bot=bot, cid=cid, stages=((0, f"Обновляю «{label.split(' ', 1)[-1]}»..."),),
            preserve_message=True,
        )
    try:
        result = await _refresh_card_cache(cid, key)
        if result is False:
            raise RuntimeError("card cache is incomplete")
        outcome = f"✅ {label} обновлена."
    except Exception:
        _log.exception("admin card refresh failed cid=%s card=%s", cid, key)
        import leisure_hub
        failed = leisure_hub.failed_steps.get(str(cid)) if key in ("leisure", "myday") else None
        outcome = f"⚠️ {label}: не обновились — {', '.join(failed)}" if failed else f"⚠️ {label} не обновилась."
    if status is not None:
        await status.stop(delete=True)
    await send_card_refresh_menu(bot, cid, q, status=outcome)


# ================= ПОЛЬЗОВАТЕЛИ =================

def _user_stats():
    from settings import notif_on, NOTIF_TYPES
    cids = access.get_allowed_cids()
    with_notifications = sum(1 for c in cids if any(notif_on(c, k) for k, _ in NOTIF_TYPES))
    now = datetime.now(config.TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    activities = tracking._all()
    allowed = {str(cid) for cid in cids}
    new_today = sum(
        1 for user_cid, record in activities.items()
        if str(user_cid) in allowed and float(record.get("first_ts") or 0) >= today_start
    )
    return {
        "total": len(cids),
        "new_today": new_today,
        "active_today": tracking.active_today_count(cids),
        "new_7d": sum(1 for r in activities.values() if r.get("first_ts", 0) >= time.time() - 7 * DAY),
        "active_7d": tracking.active_count(7),
        "with_notifications": with_notifications,
        "admins": sum(1 for c in cids if access.is_owner(c)),
        "active_invites": len(access.pending_invites()),
    }


_USERS_LIST_LIMIT = 15


def _users_list():
    """Все пользователи, отсортированные по свежести активности -> [(dot, name, last_seen)]."""
    cids = access.get_allowed_cids()
    rows = []
    for c in cids:
        ts = tracking.get_activity(c).get("last_ts", 0)
        prof = store.get_profile(c)
        name = prof.get("name") or f"ID {str(c)[:4]}…"
        rows.append((ts, tracking.churn_dot(c), name, tracking.human_last_seen(c)))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(dot, name, last_seen) for _ts, dot, name, last_seen in rows]


def _removable_users():
    """Пользователи, которых можно удалить из allowlist (без owner) -> [(cid, name, last_seen)]."""
    cids = [c for c in access.get_allowed_cids() if not access.is_owner(c)]
    rows = []
    for c in cids:
        ts = tracking.get_activity(c).get("last_ts", 0)
        prof = store.get_profile(c)
        name = prof.get("name") or f"ID {str(c)[:4]}…"
        rows.append((ts, c, name, tracking.human_last_seen(c)))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(cid, name, last_seen) for _ts, cid, name, last_seen in rows]


async def send_users(bot, cid, q=None):
    stats = _user_stats()
    users_list = _users_list()
    rows = [
        [InlineKeyboardButton(ui_label("invite", "Инвайт"), callback_data="adm_invite", api_kwargs=_GREEN)],
    ]
    if _removable_users():
        rows.append([InlineKeyboardButton(delete_label("Удалить пользователя"), callback_data="adm_user_del")])
    rows.append(nav_row("adm_home"))
    msg = ui.users(stats, users_list[:_USERS_LIST_LIMIT], len(users_list), _updated_at())
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


async def send_user_delete_list(bot, cid, q=None):
    removable = _removable_users()
    rows = [
        [InlineKeyboardButton(f"{name} · {last_seen}", callback_data=f"adm_user_delconfirm_{u_cid}")]
        for u_cid, name, last_seen in removable
    ]
    rows.append(nav_row("adm_users"))
    msg = ui.user_delete_list(removable)
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


async def send_user_delete_confirm(bot, cid, target_cid, q=None):
    prof = store.get_profile(target_cid)
    name = prof.get("name") or f"ID {str(target_cid)[:4]}…"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"adm_user_delok_{target_cid}"),
         InlineKeyboardButton("Отмена", callback_data="adm_user_del")],
    ])
    msg = ui.user_delete_confirm(name)
    await _show(bot, cid, msg, kb, q)


async def do_user_delete(bot, cid, target_cid, q=None):
    if access.is_owner(target_cid):
        await send_user_delete_list(bot, cid, q)
        return
    access.revoke_user(target_cid)
    await send_user_delete_list(bot, cid, q)


async def send_invite(bot, cid, q=None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать инвайт", callback_data="adm_invite_create")],
        nav_row("adm_users"),
    ])
    msg = ui.invite_prompt()
    await _show(bot, cid, msg, kb, q)


async def create_invite(bot, cid, q=None):
    code = access.create_invite()
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    kb = InlineKeyboardMarkup([
        nav_row("adm_users"),
    ])
    msg = ui.invite_created(link)
    if q is not None and getattr(q, "message", None) is not None:
        try:
            await q.message.edit_text(
                text=msg.text,
                entities=msg.entities,
                disable_web_page_preview=True,
                reply_markup=kb,
            )
            return
        except Exception:
            _log.debug("create_invite: ignored error", exc_info=True)
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        disable_web_page_preview=True,
        reply_markup=kb,
    )


async def send_welcome(bot, cid, q=None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить", callback_data="adm_welcome_edit"),
         InlineKeyboardButton("Предпросмотр", callback_data="adm_welcome_preview")],
        nav_row("adm_users"),
    ])
    msg = ui.welcome_admin()
    await _show(bot, cid, msg, kb, q)


# ================= API И AI (единый экран, § docs/admin.md) =================

async def send_api_ai(bot, cid, q=None):
    """Compat redirect for buttons in already-sent admin messages."""
    await send_home(bot, cid, q)


def _log_error_text(entry):
    if entry.get("error"):
        return str(entry["error"])
    kind = str(entry.get("kind") or "Ошибка")
    msg = str(entry.get("msg") or "")
    if ":" in kind and kind.rsplit(":", 1)[-1].strip().endswith("Error"):
        kind = kind.rsplit(":", 1)[-1].strip()
    return f"{kind}: {msg}" if msg and not msg.startswith(f"{kind}:") else (msg or kind)


def _log_location(entry):
    file_name = str(entry.get("file") or "")
    line = int(entry.get("line") or 0)
    if not file_name:
        kind = str(entry.get("kind") or "")
        candidate = kind.split(":", 1)[0].strip()
        if candidate and candidate not in ("app", "llm", "service"):
            file_name = candidate if candidate.endswith(".py") else f"{candidate}.py"
    return file_name or "—", line


def _compact_log_row(entry):
    file_name, line = _log_location(entry)
    section = str(entry.get("section") or tracking._section_for(file_name, entry.get("source")))
    action = str(entry.get("action") or tracking._action_for(entry.get("function"), entry.get("source")))
    if str(entry.get("source") or "") == "llm":
        if str(entry.get("kind") or "") == "json-parse":
            action = "не удалось обработать ответ"
        else:
            action = "не удалось подготовить ответ"
        reason = _llm_failure_reason(entry)
        suffix = f" · причина: {reason}" if reason else ""
        return f"{_log_date_time(entry.get('ts', 0))} · {section} · {action}{suffix}"
    error = " ".join(_log_error_text(entry).split())[:170]
    location = f"{file_name}:{line}" if line else file_name
    return f"{_log_date_time(entry.get('ts', 0))} · {section} · {action} · {error} · {location}"


def _llm_failure_reason(entry):
    """Сводит технические ответы AI-цепочки к безопасной причине для админки."""
    raw_values = list(entry.get("_reason_messages") or [])
    raw_values.append(str(entry.get("error") or entry.get("msg") or ""))
    raw = " ; ".join(raw_values).casefold()
    providers = (
        ("groq", "Groq"), ("gemini", "Gemini"), ("cerebras", "Cerebras"), ("github", "GitHub Models"),
        ("cloudflare", "Cloudflare AI"), ("cf:", "Cloudflare AI"),
        ("openrouter", "OpenRouter"),
    )
    reasons = []
    seen = set()
    for marker, label in providers:
        parts = [part.strip() for part in raw.split(";") if marker in part]
        if not parts or label in seen:
            continue
        seen.add(label)
        provider_text = " ".join(parts)
        if any(token in provider_text for token in ("429", "rate limit", "quota", "лимит")):
            detail = "лимит"
        elif any(token in provider_text for token in ("timeout", "503", "502", "504", "network", "сервис не ответил")):
            detail = "сервис не ответил"
        else:
            detail = "не ответил"
        reasons.append(f"{label} — {detail}")
    return "; ".join(reasons[:4]) or "резервы не дали ответ"


def _monitor_error_message(message, status_code):
    code = int(status_code or 0)
    if code == 429:
        return "слишком много запросов"
    if code == 400:
        return "ошибка запроса"
    if code in (408, 502, 503, 504):
        return "временно недоступен"
    if message in provider_runtime._LEGACY_ERRORS:
        return provider_runtime._LEGACY_ERRORS[message]
    return message


def _monitor_error_row(entry):
    service = str(entry.get("service") or "")
    spec = provider_runtime.SPEC_BY_KEY.get(service)
    label = spec.label if spec else service or "Сервис"
    message = str(entry.get("text") or entry.get("message") or "Ошибка").strip().rstrip(".")
    if message.startswith(f"{label}:"):
        message = message.split(":", 1)[1].strip()
    status_code = entry.get("status_code")
    message = _monitor_error_message(message, status_code)
    details = []
    fallback = str(entry.get("fallback_target") or "")
    fallback_spec = provider_runtime.SPEC_BY_KEY.get(fallback)
    if fallback_spec:
        details.append(f"резерв {fallback_spec.label}")
    recovered_at = int(entry.get("recovered_at") or 0)
    started_at = int(entry.get("started_at") or entry.get("ts") or 0)
    if recovered_at:
        duration = max(0, recovered_at - started_at)
        details.append(f"восстановлен за {max(1, round(duration / 60))} мин")
    suffix = f" · {' · '.join(details)}" if details else ""
    return f"{_log_date_time(entry.get('ts', 0))} · Система · {label} · {message}{suffix}"


def _collapse_monitor_errors(entries, window_seconds=600):
    """Сворачивает одинаковые сбои фоновой проверки в один видимый инцидент."""
    groups = []
    for entry in sorted(entries, key=lambda item: int(item.get("ts") or 0), reverse=True):
        key = (
            str(entry.get("service") or ""),
            str(entry.get("text") or entry.get("message") or "").strip().casefold(),
            int(entry.get("status_code") or 0),
            str(entry.get("exception_type") or ""),
            str(entry.get("fallback_target") or ""),
        )
        ts = int(entry.get("ts") or 0)
        match = next((group for group in groups
                      if group["key"] == key and group["latest_ts"] - ts <= window_seconds), None)
        if match is None:
            groups.append({"key": key, "latest_ts": ts, "entry": entry, "count": 1})
        else:
            match["count"] += 1
    return [(group["entry"], group["count"]) for group in groups]


def _collapse_app_errors(entries):
    """Группирует одинаковые ошибки за TTL, сохраняя последнюю точную запись."""
    groups = {}
    for entry in sorted(entries, key=lambda item: int(item.get("ts") or 0), reverse=True):
        key = _error_signature(entry)
        if key not in groups:
            groups[key] = [entry, 1, [str(entry.get("error") or entry.get("msg") or "")]]
        else:
            groups[key][1] += 1
            groups[key][2].append(str(entry.get("error") or entry.get("msg") or ""))
    result = []
    for entry, count, reasons in groups.values():
        if str(entry.get("source") or "") == "llm":
            entry = {**entry, "_reason_messages": reasons}
        result.append((entry, count))
    return result


async def clear_logs(bot, cid, q=None):
    tracking.clear_errors()
    provider_runtime.clear_history()
    await send_logs(bot, cid, q)


def _active_monitor_errors(cutoff):
    # A recovered provider incident or a request completed by a reserve did not
    # break the user-facing feature. It remains in the runtime history for
    # diagnosis, but must not spam Errors.
    return [
        entry for entry in provider_runtime.history(limit=200)
        if (
            entry.get("ts", 0) >= cutoff
            and entry.get("event_type") == "error"
            and not entry.get("recovered_at")
            and not entry.get("fallback_target")
        )
    ]


def _active_error_rows(*, limit=40, include_hidden=True):
    cutoff = time.time() - DAY
    errors = [e for e in tracking.get_errors(limit=200) if e.get("ts", 0) >= cutoff]
    monitor_errors = _active_monitor_errors(cutoff)
    combined = [
        (int(entry.get("ts") or 0), _compact_log_row(entry) + _repeat_suffix(count))
        for entry, count in _collapse_app_errors(errors)
    ] + [
        (int(entry.get("ts") or 0), _monitor_error_row(entry) + _repeat_suffix(count))
        for entry, count in _collapse_monitor_errors(monitor_errors)
    ]
    combined.sort(key=lambda item: item[0], reverse=True)
    rows = [row for _ts, row in combined[:limit]]
    hidden_count = max(0, len(combined) - len(rows))
    if hidden_count and include_hidden:
        rows.append(f"… ещё {hidden_count} ошибок скрыто")
    return rows


async def send_logs(bot, cid, q=None):
    cutoff = time.time() - DAY
    errors = [e for e in tracking.get_errors(limit=200) if e.get("ts", 0) >= cutoff]
    monitor_errors = _active_monitor_errors(cutoff)
    combined_count = len(_collapse_app_errors(errors)) + len(_collapse_monitor_errors(monitor_errors))
    rows = _active_error_rows(limit=40, include_hidden=False)
    text_size = 0
    visible_rows = []
    for row in rows:
        next_size = text_size + len(row) + 1
        if visible_rows and next_size > 3500:
            break
        visible_rows.append(row)
        text_size = next_size
    hidden_count = max(0, combined_count - len(visible_rows))
    if hidden_count:
        visible_rows.append(f"… ещё {hidden_count} ошибок скрыто")
    buttons = []
    if combined_count:
        buttons.append([InlineKeyboardButton(delete_label("Очистить ошибки"), callback_data="adm_logs_clear")])
    buttons.append(nav_row("adm_home"))
    kb = InlineKeyboardMarkup(buttons)
    now = int(time.time())
    msg = ui.logs(visible_rows, len(visible_rows), _updated_at(now), now)
    await _show(bot, cid, msg, kb, q)
