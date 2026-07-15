"""Логика админ-панели (§ docs/admin.md).

Собирает данные для каждого экрана из access/store/tracking/ai и отдаёт готовый
MessageSpec из ui.admin. Роутинг (settings.dispatch) делегирует сюда через send_*.

Все функции — async send_*(bot, cid); гард на владельца — в settings._admin_guard.
"""
import logging
import re
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import access
import api_usage
import config
from ui.constants import ui_label
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


def _when(ts) -> str:
    """Компактное «X назад» / время из timestamp."""
    if not ts:
        return "—"
    delta = time.time() - ts
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{int(delta // 60)} мин назад"
    if delta < DAY:
        return f"{int(delta // 3600)} ч назад"
    return f"{int(delta // DAY)} дн назад"


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
        "telegram": bool(config.TELEGRAM_TOKEN),
        "tmdb": bool(config.TMDB_API_KEY),
        "ticketmaster": bool(config.TICKETMASTER_API_KEY),
        "zeroentropy": bool(config.ZEROENTROPY_API_KEY),
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
        rows.append([InlineKeyboardButton("❌ Удалить пользователя", callback_data="adm_user_del")])
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
        [InlineKeyboardButton("❌ Удалить", callback_data=f"adm_user_delok_{target_cid}"),
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

# providers — реальная цепочка автопереключения (§ ai.MODULE_POLICY): Gemini -> Groq ->
# Cloudflare для всех этих разделов. Groq отдельно подписан как "основной" только там,
# где это осознанный продуктовый выбор (простые задачи), а не просто факт из order-цепочки.
_FEATURE_ROWS = [
    ("Мой день", "OpenWeather + Wiki + Gemini", ("gemini",)),
    ("Погода", "OpenWeather", ()),
    ("Гардероб", "OpenWeather + Gemini", ("gemini",)),
    ("Готовка", "Gemini → Groq", ("gemini",)),
    ("Здоровье", "Gemini + ZeroEntropy", ("gemini",)),
    ("Обучение", "Gemini → Groq", ("gemini",)),
    ("Поездки", "Tavily + Gemini", ("gemini",)),
    ("Досуг", "TMDB/Tavily/Ticketmaster + Gemini", ("gemini",)),
    ("Ассистент", "intent-router + Gemini", ("gemini",)),
]

_HTTP_REASONS = {
    401: "неверный ключ", 402: "закончились кредиты", 403: "доступ запрещён",
    404: "не найдено", 408: "таймаут", 429: "лимит запросов",
    500: "сервис недоступен", 502: "сервис недоступен",
    503: "сервис недоступен", 504: "таймаут сервиса",
}

def _friendly_error(reason):
    """'HTTP 402' -> 'HTTP 402 · закончились кредиты'; иначе не трогает текст."""
    m = re.match(r"^HTTP (\d+)$", str(reason or "").strip())
    if not m:
        return reason
    code = int(m.group(1))
    hint = _HTTP_REASONS.get(code)
    return f"HTTP {code} · {hint}" if hint else f"HTTP {code}"


def _used_by(name_substr):
    return [name for name, providers, _deps in _FEATURE_ROWS if name_substr in providers]


def _pluralize(n, one, few, many) -> str:
    """Единая функция склонения по остатку от деления - используется и для
    сервисов, и для запросов, чтобы не плодить одинаковые if/elif в каждом месте."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return few
    return many


def _plural_services(n) -> str:
    return _pluralize(n, "сервис", "сервиса", "сервисов")


def _plural_requests(n) -> str:
    return _pluralize(n, "запрос", "запроса", "запросов")


def _join_and(items) -> str:
    """['Готовка', 'Обучение'] -> 'Готовка и Обучение'; ['A','B','C'] -> 'A, B и C'."""
    items = [str(x) for x in items if x]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " и " + items[-1]


def _thousands(n) -> str:
    """20000 -> '20 000' - реальные цифры, без сокращений вроде '20k' (экран диагностики,
    точность важнее компактности)."""
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


_PERIOD_RU = {"day": "в день", "hour": "в час", "month": "в месяц", "minute": "в минуту"}
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


def _quota_or_requests(label, svc) -> tuple[str | None, str]:
    """Общая логика 'что показать в конце строки' для любого сервиса с локальными
    квотами (api_usage.snapshot). Возвращает (переопределение точки статуса | None, текст).
    Один формат на весь экран: 'осталось X / Y{ период}', без смешения с 'использовано'."""
    quotas = [q for q in (svc.get("quotas") or []) if q.get("limit")]
    if quotas:
        quota = min(quotas, key=lambda q: _PERIOD_PRIORITY.get(q.get("period"), 9))
        valid = _valid_quota(used=quota.get("used"), limit=quota.get("limit"))
        if valid is None:
            _log.warning("api_ai: invalid local quota for %s used=%r limit=%r",
                        label, quota.get("used"), quota.get("limit"))
            return "warning", "ошибка данных лимита"
        remaining, limit = valid
        period_ru = _PERIOD_RU.get(quota.get("period"), "")
        suffix = f" {period_ru}" if period_ru else ""
        return None, f"осталось {_thousands(remaining)} / {_thousands(limit)}{suffix}"
    n = int(svc.get("day_requests") or 0)
    if n:
        return None, f"{_thousands(n)} {_plural_requests(n)} сегодня"
    return None, "доступен"


def _remote_quota_or_requests(label, remote, requests_today=None):
    """Тот же формат, что и _quota_or_requests, но для сервисов с удалённой квотой
    (Firecrawl/OpenRouter) - не из локального счётчика, а из ответа самого API."""
    if remote and remote.get("limit") is not None:
        valid = _valid_quota(remaining=remote.get("remaining"), limit=remote.get("limit"))
        if valid is None:
            _log.warning("api_ai: invalid remote quota for %s remote=%r", label, remote)
            return "warning", "ошибка данных лимита"
        remaining, limit = valid
        return None, f"осталось {_thousands(remaining)} / {_thousands(limit)}"
    if requests_today:
        return None, f"{_thousands(requests_today)} {_plural_requests(requests_today)} сегодня"
    return None, None


def _compose_line(state, label, detail, role=None) -> str:
    dot = {"ok": ui.OK, "warn": ui.WARN, "bad": ui.BAD, "off": ui.OFF,
           "unknown": ui.UNKNOWN, "warning": ui.WARNING}[state]
    parts = [label] + ([role] if role else []) + [str(detail)]
    return f"{dot} " + " · ".join(parts)


def _service_state_detail(service, label, snapshot):
    """Единая точка принятия решения о статусе локально отслеживаемого сервиса:
    disabled(нет ключа) / bad(ошибка) / rate_limited(лимит/cooldown) / stale(давно не
    проверялся) / healthy. Возвращает (state, detail, quota_override_state|None)."""
    if not _configured_service(service):
        return "off", "нет ключа", None
    svc = _snapshot_service(snapshot, service)
    status = svc.get("status") or "off"
    if status == "bad":
        return "bad", _friendly_error(svc.get("last_error_reason")) or "ошибка", None
    cooldown_until = int(svc.get("cooldown_until") or 0)
    if status == "warn" and cooldown_until > time.time():
        return "warn", f"пауза до {_hhmm(cooldown_until)}", None
    if status == "warn":
        return "warn", "было превышение лимита", None
    if status == "stale":
        return "unknown", f"статус неизвестен · было {_when(svc.get('last_request_at'))}", None
    override, detail = _quota_or_requests(label, svc)
    return "ok", detail, override


def _ai_line(service, label, snapshot, *, role=None):
    state, detail, override = _service_state_detail(service, label, snapshot)
    return _compose_line(override or state, label, detail, role)


def _data_line(service, label, snapshot):
    state, detail, override = _service_state_detail(service, label, snapshot)
    return _compose_line(override or state, label, detail)


def _gemini_line(snapshot):
    return _ai_line("gemini", "Gemini", snapshot, role="основной AI")


def _openrouter_line(snapshot, ai_module):
    if not config.OPENROUTER_API_KEY:
        return None
    remote = api_usage.openrouter_key_usage()
    override, detail = _remote_quota_or_requests("OpenRouter", remote)
    if detail is None:
        fallback_stats = ai_module.get_openrouter_fallback_stats(1)
        errors = int(fallback_stats.get("errors") or 0)
        state, detail = ("warn", f"{errors} {_plural_requests(errors)} с ошибкой сегодня") if errors else ("ok", "доступен")
        return _compose_line(state, "OpenRouter", detail, "резерв")
    return _compose_line(override or "ok", "OpenRouter", detail, "резерв")


def _openweather_line():
    import weather
    label = "OpenWeather"
    if not _configured_service("openweather"):
        return _compose_line("off", label, "нет ключа")
    usage = weather.get_weather_usage()
    if usage.get("last_error_reason") and usage.get("last_error_at"):
        reason = _friendly_error(str(usage.get("last_error_reason")))
        return _compose_line("bad", label, str(reason)[:40])
    valid = _valid_quota(used=usage.get("requests_total"), limit=config.WEATHER_FREE_DAILY_LIMIT)
    if valid is None:
        _log.warning("api_ai: invalid OpenWeather quota requests_total=%r limit=%r",
                    usage.get("requests_total"), config.WEATHER_FREE_DAILY_LIMIT)
        return _compose_line("warning", label, "ошибка данных лимита")
    remaining, limit = valid
    return _compose_line("ok", label, f"осталось {_thousands(remaining)} / {_thousands(limit)} сегодня")


def _firecrawl_line(snapshot):
    label = "Firecrawl"
    if not _configured_service("firecrawl"):
        return _compose_line("off", label, "нет ключа")
    remote = api_usage.firecrawl_credit_usage()
    override, detail = _remote_quota_or_requests(label, remote)
    if detail is not None:
        return _compose_line(override or "ok", label, detail)
    return _data_line("firecrawl", label, snapshot)


def _fallback_active(snapshot) -> bool:
    """Сработает ли локальная резервная модель, если Gemini сейчас недоступен -
    честный ответ на 'Groq/Cloudflare реально подхватят запрос', а не наличие ключа."""
    for service in ("groq", "cloudflare"):
        if not _configured_service(service):
            continue
        svc = _snapshot_service(snapshot, service)
        if (svc.get("status") or "off") != "bad":
            return True
    return False


async def send_api_ai(bot, cid, q=None):
    import ai
    snapshot = api_usage.snapshot()

    ai_rows = [
        _gemini_line(snapshot),
        _ai_line("groq", "Groq", snapshot),
        _ai_line("cloudflare", "Cloudflare AI", snapshot, role="резерв"),
    ]
    openrouter_line = _openrouter_line(snapshot, ai)
    if openrouter_line:
        ai_rows.append(openrouter_line)

    api_rows = [
        _openweather_line(),
        _data_line("tavily", "Tavily", snapshot),
        _firecrawl_line(snapshot),
        _data_line("tmdb", "TMDB", snapshot),
        _data_line("ticketmaster", "Ticketmaster", snapshot),
        _data_line("zeroentropy", "ZeroEntropy", snapshot),
        _data_line("pexels", "Pexels", snapshot),
    ]

    gemini_svc = _snapshot_service(snapshot, "gemini")
    gemini_down = (gemini_svc.get("status") or "off") in ("bad", "warn")
    fallback_ok = _fallback_active(snapshot)
    n_bad = sum(1 for line in ai_rows + api_rows if line.startswith(ui.BAD))

    if gemini_down and not fallback_ok:
        status_dot, status_text = ui.BAD, "Критический сбой"
    elif gemini_down or n_bad:
        status_dot, status_text = ui.WARN, "Работает с ограничениями"
    else:
        status_dot, status_text = ui.OK, "Все системы работают"

    if gemini_down:
        groq_role = _join_and(_used_by("Groq"))
        parts = ["Gemini недоступен"]
        if fallback_ok:
            parts.append("AI-функции автоматически работают через резерв")
            if groq_role:
                parts.append(f"{groq_role} переключены на Groq")
        else:
            parts.append("резервных моделей нет — AI-функции сейчас не отвечают")
        impact_line = " · ".join(parts)
    elif n_bad:
        impact_line = (f"{n_bad} {_plural_services(n_bad)} {'недоступен' if n_bad == 1 else 'недоступно'} · "
                       "соответствующие функции могут не отвечать")
    else:
        impact_line = "Все ключевые сервисы доступны"

    fallback_line = "включено" if fallback_ok else "выключено"
    unavailable_line = (f"Недоступно: {n_bad} {_plural_services(n_bad)}" if n_bad else None)

    errors = _today_errors()
    last_failure = None
    if errors:
        last = errors[0]
        kind_line = f"{_hhmm(last.get('ts', 0))} · {str(last.get('kind') or 'ошибка')}"
        raw_msg = str(last.get("msg") or "")[:180]
        last_failure = (kind_line, raw_msg)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.api_ai(status_dot, status_text, impact_line, fallback_line, unavailable_line,
                     ai_rows, api_rows, last_failure, _updated_at())
    await _show(bot, cid, msg, kb, q)


def _issue_summary(source, msg):
    msg = str(msg or "")
    low = msg.lower()
    if source == "llm":
        if "gemini · лимит" in low or "gemini cooldown" in low or "resource_exhausted" in low:
            scope = ""
            for candidate in ("RPM", "RPD", "TPM"):
                if candidate.lower() in low:
                    scope = f" {candidate}"
                    break
            return f"Gemini: лимит{scope}"
        providers = []
        for provider in ("openrouter", "gemini", "groq", "cf"):
            if f"{provider}:" in low or f"{provider} " in low:
                providers.append(provider)
        prefix = ", ".join(dict.fromkeys(providers)) if providers else "LLM"
        if "429" in msg or "too many requests" in low or "rate limit" in low:
            return f"{prefix}: лимит запросов"
        if "400" in msg or "bad request" in low or "invalid" in low:
            return f"{prefix}: ошибка запроса"
        if "json" in low:
            return f"{prefix}: невалидный JSON"
        if "no " in low:
            return f"{prefix}: нет ключа или провайдер выключен"
        return f"{prefix}: сбой генерации"
    if source == "service":
        return "Сервис недоступен"
    if "перегружен" in low:
        return "ИИ временно перегружен"
    if "json" in low:
        return "Не удалось разобрать JSON"
    return msg[:50]


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


async def send_logs(bot, cid, q=None):
    import ai
    cutoff = time.time() - DAY
    errors = [e for e in tracking.get_errors(limit=200) if e.get("ts", 0) >= cutoff]
    rows = []
    for e in errors[:5]:
        source = str(e.get("source") or "app")
        msg = str(e.get("msg") or "")
        if source == "llm" and str(e.get("kind") or "").startswith("gemini_rate_limit"):
            lines = [line.strip() for line in msg.splitlines() if line.strip()]
            title = (
                lines[0].replace("Gemini ·", f"{_hhmm(e.get('ts', 0))} · Gemini ·", 1)
                if lines else f"{_hhmm(e.get('ts', 0))} · Gemini · лимит"
            )
            rows.append("\n".join([title] + lines[1:2]))
        else:
            rows.append(f"{_hhmm(e.get('ts', 0))} · {source} · {_issue_summary(source, msg)}")
    gemini = ai.get_gemini_rate_limit_stats(1)
    summary = {
        "errors": len(errors),
        "rate_limits": int(gemini.get("rate_limits") or 0),
        "fallbacks": int(gemini.get("fallbacks") or 0),
        "last_429_at": int(gemini.get("last_429_at") or 0),
        "cooldown_active": bool(gemini.get("cooldown_active")),
        "cooldown_until": int(gemini.get("cooldown_until") or 0),
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Обновить", callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_system"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.logs(rows, len(errors), _updated_at(), summary)
    await _show(bot, cid, msg, kb, q)
