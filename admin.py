"""Логика админ-панели (§ docs/admin.md).

Собирает данные для каждого экрана из access/store/tracking/ai и отдаёт готовый
MessageSpec из ui.admin. Роутинг (settings.dispatch) делегирует сюда через send_*.

Все функции — async send_*(bot, cid); гард на владельца — в settings._admin_guard.
"""
import asyncio
import hashlib
import html
import logging
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import access
import api_usage
import config
import secure
from ui.constants import delete_label, ui_label
import store
import tracking
from ui import admin as ui

_log = logging.getLogger(__name__)

DAY = 86400


def _back(target="set_admin"):
    return [InlineKeyboardButton("⬅️ Назад", callback_data=target), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]


async def _show(bot, cid, msg, reply_markup=None, q=None):
    if q is not None and getattr(q, "message", None) is not None:
        try:
            await q.message.edit_text(text=msg.text, entities=msg.entities, reply_markup=reply_markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=reply_markup)


def _hhmm(ts) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except Exception:
        return "—"


def _updated_at() -> str:
    return datetime.now(config.TZ).strftime("%H:%M")


def _num(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 10000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _today_errors(source=None) -> list:
    cutoff = time.time() - DAY
    return [e for e in tracking.get_errors(source=source, limit=200) if e.get("ts", 0) >= cutoff]


def _snapshot_service(snapshot, service):
    for svc in (snapshot or {}).get("services") or []:
        if svc.get("service") == service:
            return svc
    return {}


def _configured_service(service: str) -> bool:
    return {
        "openweather": bool(config.WEATHER_API_KEY),
        "gemini": bool(config.GEMINI_API_KEY),
        "pexels": bool(config.PEXELS_API_KEY),
        "tavily": bool(config.TAVILY_API_KEY),
        "firecrawl": bool(config.FIRECRAWL_API_KEY),
        "cloudflare": bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID),
        "groq": bool(config.GROQ_API_KEY),
        "cohere": bool(config.COHERE_API_KEY),
        "github_models": bool(config.GITHUB_MODELS_TOKEN),
        "openrouter": bool(config.OPENROUTER_API_KEY),
        "google_books": bool(config.GOOGLE_BOOKS_API_KEY),
        "languagetool": bool(config.LANGUAGETOOL_API_URL),
        "spoonacular": bool(config.SPOONACULAR_API_KEY),
        "themealdb": bool(config.THEMEALDB_API_KEY),
        "azure_speech": bool(config.AZURE_SPEECH_KEY and config.AZURE_SPEECH_REGION),
        "telegram": bool(config.TELEGRAM_TOKEN),
        "tmdb": bool(config.TMDB_API_KEY),
        "ticketmaster": bool(config.TICKETMASTER_API_KEY),
        "zeroentropy": bool(config.ZEROENTROPY_API_KEY),
        "restcountries": bool(config.RESTCOUNTRIES_API_KEY),
        "database": bool(config.DATABASE_URL),
    }.get(service, False)


def _store_health_line():
    try:
        store._load("__health__")
    except Exception as e:
        return f"{ui.BAD} Данные · {str(e)[:42]}"
    return f"{ui.OK} Данные · работает"


def _bad_services(snapshot):
    return [s for s in snapshot.get("services", []) if s.get("status") == "bad"]


# ================= ДОМ =================

async def send_home(bot, cid, q=None):
    stats = _user_stats()
    snapshot = api_usage.snapshot()
    bad = _bad_services(snapshot)
    notif = _notification_stats(cid)
    notif_bad = notif["errors_today"] > 0
    dot, txt = (ui.BAD, "Есть проблема") if (bad or notif_bad) else (ui.OK, "Всё работает")
    api_line = f"{len(bad)} недоступно" if bad else "OK · лимиты в норме"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛠 Система", callback_data="adm_api_ai")],
        [InlineKeyboardButton(ui_label("users", "Пользователи"), callback_data="adm_users")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.home(
        system_dot=dot,
        system_text=txt,
        system_line=api_line,
        notif_line=f"OK · ошибок {notif['errors_today']}" if not notif_bad else f"ошибок {notif['errors_today']}",
        users_line=f"{stats['total']} · активны {stats['active_7d']} · новых {stats['new_7d']}",
        data_line="OK" if _store_health_line().startswith(ui.OK) else "ошибка",
        updated_at=_updated_at(),
    )
    await _show(bot, cid, msg, kb, q)


# ================= ПОЛЬЗОВАТЕЛИ =================

def _user_stats():
    from settings import notif_on, NOTIF_TYPES
    cids = access.get_allowed_cids()
    with_notifications = sum(1 for c in cids if any(notif_on(c, k) for k, _ in NOTIF_TYPES))
    cutoff = time.time() - 7 * DAY
    activities = tracking._all()
    new_7d = sum(1 for r in activities.values() if r.get("first_ts", 0) >= cutoff)
    return {
        "total": len(cids),
        "new_7d": new_7d,
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
        [InlineKeyboardButton(ui_label("invite", "Инвайт"), callback_data="adm_invite")],
    ]
    if _removable_users():
        rows.append([InlineKeyboardButton(delete_label("Удалить пользователя"), callback_data="adm_user_del")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = ui.users(stats, users_list[:_USERS_LIST_LIMIT], len(users_list), _updated_at())
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


async def send_user_delete_list(bot, cid, q=None):
    removable = _removable_users()
    rows = [
        [InlineKeyboardButton(f"{name} · {last_seen}", callback_data=f"adm_user_delconfirm_{u_cid}")]
        for u_cid, name, last_seen in removable
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_users"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
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
        [InlineKeyboardButton("✅ Создать", callback_data="adm_invite_create")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.invite_prompt()
    await _show(bot, cid, msg, kb, q)


async def create_invite(bot, cid, q=None):
    code = access.create_invite()
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
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
            pass
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
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.welcome_admin()
    await _show(bot, cid, msg, kb, q)


def _notification_stats(cid):
    from settings import notif_on, NOTIF_TYPES
    active_types = sum(1 for kind, _label in NOTIF_TYPES if notif_on(cid, kind))
    snapshot = api_usage.snapshot()
    telegram = _snapshot_service(snapshot, "telegram")
    sent_today = int(telegram.get("day_messages") or 0)
    cutoff = time.time() - DAY
    errors = [
        e for e in tracking.get_errors(limit=200)
        if e.get("ts", 0) >= cutoff
        and (e.get("source") == "broadcast" or str(e.get("kind", "")).startswith("notif"))
    ]
    return {
        "sent_today": sent_today,
        "errors_today": len(errors),
        "active_types": active_types,
    }


# ================= API И AI (единый экран, § docs/admin.md) =================

# Один продуктовый реестр для системного экрана. В строке нет технического
# назначения API: только имя раздела из интерфейса и реальный путь деградации.
_SYSTEM_SERVICES = (
    ("cohere", "Cohere", "Обучение", "Gemini"),
    ("gemini", "Gemini", "Разные категории", "GitHub Models"),
    ("github_models", "GitHub Models", "резерв", "Groq"),
    ("groq", "Groq", "Готовка, Обучение", "GitHub Models"),
    ("openrouter", "OpenRouter", "Готовка", "шаблон без AI"),
    ("cloudflare", "Cloudflare AI", "резерв", "GitHub Models"),
    ("openweather", "OpenWeather", "Погода", "сохранённый прогноз"),
    ("tavily", "Tavily", "Поиск", "Firecrawl"),
    ("firecrawl", "Firecrawl", "Поиск", "Tavily"),
    ("tmdb", "TMDB", "Кино", "Gemini"),
    ("google_books", "Google Books", "Книги", "Open Library"),
    ("languagetool", "LanguageTool", "Обучение", "проверка в коде"),
    ("spoonacular", "Spoonacular", "Готовка", "TheMealDB"),
    ("themealdb", "TheMealDB", "Готовка", "Spoonacular"),
    ("azure_speech", "Azure Speech", "Озвучка", "текстовая карточка"),
    ("ticketmaster", "Ticketmaster", "Концерты", "Tavily"),
    ("zeroentropy", "ZeroEntropy", "Здоровье", "поиск в базе"),
    ("pexels", "Pexels", "Изображения", "изображение источника"),
    ("restcountries", "REST Countries", "Поездка", "Поиск"),
    ("telegram", "Telegram", "Сообщения", ""),
    ("database", "PostgreSQL", "Данные", "локальное хранилище"),
)

def _friendly_error(reason):
    """Преобразовать техническую ошибку в короткий текст для экрана системы."""
    value = str(reason or "").strip()
    low = value.casefold().replace("_", " ")
    mappings = (
        (("http 429", "rate limit exceeded", "too many requests"), "лимит запросов"),
        (("quota exceeded", "quota exhausted"), "лимит исчерпан"),
        (("http 402",), "закончились кредиты"),
        (("http 401", "invalid api key", "unauthorized"), "неверный API-ключ"),
        (("http 403", "forbidden"), "доступ запрещён"),
        (("http 404",), "адрес API не найден"),
        (("http 432",), "сервис отклонил запрос"),
        (("http 408", "timeout", "timed out"), "сервис не ответил вовремя"),
        (("json parse", "jsondecode", "не удалось разобрать json", "невалидный json"), "некорректный ответ"),
        (("nameerror",), "ошибка в коде бота"),
        (("connection error", "connectionerror", "network error", "networkerror"), "нет подключения"),
        (("service unavailable", "http 500", "http 502", "http 503", "http 504"), "сервис временно недоступен"),
    )
    for needles, text_value in mappings:
        if any(needle in low for needle in needles):
            return text_value
    if "limit" in low or "лимит" in low:
        return "лимит запросов"
    return "не работает" if value else "нет данных"


def _pluralize(n, one, few, many) -> str:
    """Единая функция склонения по остатку от деления - используется и для
    сервисов, и для запросов, чтобы не плодить одинаковые if/elif в каждом месте."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


def _plural_requests(n) -> str:
    return _pluralize(n, "запрос", "запроса", "запросов")


def _thousands(n) -> str:
    """20000 -> '20 000' - реальные цифры, без сокращений вроде '20k' (экран диагностики,
    точность важнее компактности)."""
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


_PERIOD_PRIORITY = {"day": 0, "month": 1, "hour": 2, "minute": 3}


def _valid_quota(used=None, remaining=None, limit=None):
    """Проверяет согласованность лимита перед показом и возвращает (remaining, limit),
    либо None при противоречивых данных.

    used задан -> лимит наш собственный счётчик (всегда >=0); превышение лимита -
    нормальная ситуация (клампим остаток к 0, это не ошибка данных).
    remaining задан напрямую -> это цифра из внешнего API (Firecrawl/OpenRouter),
    которой мы не управляем - remaining>limit или remaining<0 там означает
    реально противоречивый ответ, а не "лимит исчерпан", это и нужно поймать (см. п.4)."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return None
    if limit < 0:
        return None
    if remaining is None:
        try:
            used = int(used)
        except (TypeError, ValueError):
            return None
        if used < 0:
            return None
        return max(0, limit - used), limit
    try:
        remaining = int(remaining)
    except (TypeError, ValueError):
        return None
    if remaining < 0 or remaining > limit:
        return None
    return remaining, limit


def _usage_stat(service, svc):
    """Короткая единая статистика: «использовано из лимита» или число запросов."""
    quotas = [q for q in (svc.get("quotas") or []) if q.get("limit")]
    if quotas:
        quota = min(quotas, key=lambda q: _PERIOD_PRIORITY.get(q.get("period"), 9))
        try:
            used, limit = int(quota.get("used") or 0), int(quota.get("limit"))
        except (TypeError, ValueError):
            return ""
        if used < 0 or limit <= 0:
            return ""
        return f"{_thousands(used)} из {_thousands(limit)}"
    requests_today = int(svc.get("day_requests") or 0)
    if requests_today:
        return f"{_thousands(requests_today)} {_plural_requests(requests_today)}"
    return ""


def _remote_stat(service):
    remote = None
    if service == "firecrawl":
        remote = api_usage.firecrawl_credit_usage()
    elif service == "openrouter":
        remote = api_usage.openrouter_key_usage()
    if not remote or remote.get("limit") is None:
        return ""
    valid = _valid_quota(remaining=remote.get("remaining"), limit=remote.get("limit"))
    if valid is None:
        return ""
    remaining, limit = valid
    return f"{_thousands(limit - remaining)} из {_thousands(limit)}"


def _system_service_line(service, label, area, fallback, snapshot, remote_stats=None):
    """Собрать одну продуктовую строку без raw-кодов и технических ролей."""
    if service == "database":
        try:
            store._load("__health__")
            state, detail = "ok", ""
        except Exception as exc:
            state, detail = "warn", _friendly_error(exc)
        configured = bool(config.DATABASE_URL)
        if not configured and state == "ok":
            state, detail = "warn", "не настроен"
    else:
        configured = _configured_service(service)
        svc = _snapshot_service(snapshot, service)
        if service in ("firecrawl", "openrouter"):
            remote_stat = (
                remote_stats.get(service, "") if remote_stats is not None else _remote_stat(service)
            )
        else:
            remote_stat = ""
        if not configured:
            state, detail = "warn", "не настроен"
        elif remote_stat:
            state, detail = "ok", remote_stat
        elif not svc:
            state, detail = "warn", "нет данных"
        else:
            raw_status = svc.get("status") or "off"
            cooldown_until = int(svc.get("cooldown_until") or 0)
            if cooldown_until > time.time():
                state, detail = "warn", f"пауза до {_hhmm(cooldown_until)}"
            elif raw_status in ("bad", "warn"):
                state = "warn" if fallback else "bad"
                detail = _friendly_error(svc.get("last_error_reason") or svc.get("status_text"))
            elif raw_status in ("off", "stale"):
                state, detail = "warn", "нет данных"
            else:
                state = "ok"
                detail = _remote_stat(service) or _usage_stat(service, svc)

    dot = {"ok": ui.OK, "warn": ui.WARN, "bad": ui.BAD}.get(state, ui.UNKNOWN)
    parts = [f"{dot} {label}", area]
    if detail:
        parts.append(detail)
    if state != "ok" and fallback:
        parts.append(f"используется {fallback}")
    return " · ".join(parts)


async def send_api_ai(bot, cid, q=None):
    snapshot = api_usage.snapshot()
    firecrawl_stat, openrouter_stat = await asyncio.gather(
        asyncio.to_thread(_remote_stat, "firecrawl"),
        asyncio.to_thread(_remote_stat, "openrouter"),
    )
    remote_stats = {"firecrawl": firecrawl_stat, "openrouter": openrouter_stat}
    rows = [
        _system_service_line(service, label, area, fallback, snapshot, remote_stats)
        for service, label, area, fallback in _SYSTEM_SERVICES
    ]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Логи", callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.api_ai(rows, _updated_at())
    await _show(bot, cid, msg, kb, q)


async def clear_cache(bot, cid, q=None):
    import research
    import tracking
    import util
    import weather
    util._TTL_CACHE.clear()
    weather._WX_CACHE.clear()
    research._CF_CACHE.clear()
    research._WDF_CACHE.clear()
    research._GSR_CACHE.clear()
    tracking.clear_errors()
    await send_logs(bot, cid, q)


def _log_token(entry):
    if entry.get("id"):
        return str(entry["id"])[:16]
    raw = f"{entry.get('ts')}|{entry.get('kind')}|{entry.get('msg')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


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
    error = " ".join(_log_error_text(entry).split())[:170]
    location = f"{file_name}:{line}" if line else file_name
    return f"{_hhmm(entry.get('ts', 0))} · {section} · {action} · {error} · {location}"


def _full_log_text(entry):
    file_name, line = _log_location(entry)
    section = str(entry.get("section") or tracking._section_for(file_name, entry.get("source")))
    action = str(entry.get("action") or tracking._action_for(entry.get("function"), entry.get("source")))
    dt = datetime.fromtimestamp(int(entry.get("ts") or 0), config.TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    fields = (
        ("Время", dt), ("Раздел", section), ("Действие", action),
        ("Ошибка", _log_error_text(entry)), ("Traceback", entry.get("traceback") or _log_error_text(entry)),
        ("Файл", file_name), ("Строка", line or "—"),
        ("Функция", entry.get("function") or "—"), ("Сервис", entry.get("service") or "—"),
        ("Резерв", entry.get("fallback") or "—"),
        ("Версия", entry.get("version") or config.APP_VERSION or "—"),
    )
    return secure.redact("\n".join(f"{label}: {value}" for label, value in fields))


async def clear_logs(bot, cid, q=None):
    tracking.clear_errors()
    await send_logs(bot, cid, q)


async def send_log_copy(bot, cid, token, q=None):
    entry = next((row for row in tracking.get_errors(limit=200) if _log_token(row) == token), None)
    if entry is None:
        await send_logs(bot, cid, q)
        return
    if q is not None:
        try:
            await q.answer()
        except Exception:
            pass
    body = _full_log_text(entry)[:3800]
    await bot.send_message(
        chat_id=cid,
        text=f"📋 Данные ошибки\n\n<pre>{html.escape(body)}</pre>",
        parse_mode="HTML",
    )


async def send_logs(bot, cid, q=None):
    cutoff = time.time() - DAY
    errors = [e for e in tracking.get_errors(limit=200) if e.get("ts", 0) >= cutoff]
    shown = errors[:5]
    rows = [_compact_log_row(entry) for entry in shown]
    buttons = [
        [InlineKeyboardButton(f"📋 Скопировать · {_hhmm(entry.get('ts', 0))}", callback_data=f"adm_log_copy_{_log_token(entry)}")]
        for entry in shown
    ]
    if errors:
        buttons.append([InlineKeyboardButton(delete_label("Очистить логи"), callback_data="adm_logs_clear")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_system"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(buttons)
    msg = ui.logs(rows, len(errors), _updated_at())
    await _show(bot, cid, msg, kb, q)
