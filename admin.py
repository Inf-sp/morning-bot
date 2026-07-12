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
    return [InlineKeyboardButton("⬅️ Назад", callback_data=target)]


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


def _data_line():
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
        [InlineKeyboardButton("🔌 API и AI", callback_data="adm_api_ai")],
        [InlineKeyboardButton(ui_label("notifications", "Уведомления"), callback_data="adm_notif")],
        [InlineKeyboardButton(ui_label("users", "Пользователи"), callback_data="adm_users")],
    ])
    msg = ui.home(
        system_dot=dot,
        system_text=txt,
        system_line=api_line,
        notif_line=f"OK · ошибок {notif['errors_today']}" if not notif_bad else f"ошибок {notif['errors_today']}",
        users_line=f"{stats['total']} · активны {stats['active_7d']} · новых {stats['new_7d']}",
        data_line="OK" if _data_line().startswith(ui.OK) else "ошибка",
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


async def send_users(bot, cid, q=None):
    stats = _user_stats()
    users_list = _users_list()
    rows = [
        [InlineKeyboardButton(ui_label("invite", "Инвайт"), callback_data="adm_invite")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_home")],
    ]
    msg = ui.users(stats, users_list[:_USERS_LIST_LIMIT], len(users_list), _updated_at())
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


async def send_invite(bot, cid, q=None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать", callback_data="adm_invite_create")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users")],
    ])
    msg = ui.invite_prompt()
    await _show(bot, cid, msg, kb, q)


async def create_invite(bot, cid, q=None):
    code = access.create_invite()
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users")],
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
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users")],
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

_USER_FALLBACK_TEXT = "Сейчас не удалось подготовить ответ. Попробуй ещё раз."


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


def _plural_services(n):
    if n % 10 == 1 and n % 100 != 11:
        return "сервис"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "сервиса"
    return "сервисов"


_PERIOD_RU = {"day": "в день", "hour": "в час", "month": "в месяц", "minute": "в минуту"}
_PERIOD_PRIORITY = {"day": 0, "month": 1, "hour": 2, "minute": 3}


def _quota_left_line(svc) -> str | None:
    """'X из Y в период осталось' по квоте с известным лимитом (config.API_QUOTAS) - день/месяц
    важнее часа/минуты для беглого взгляда на экран, поминутный берём только если больше нечего.
    Считаем сами каждый исходящий запрос - для дневных/часовых квот это точная цифра."""
    quotas = [q for q in svc.get("quotas", []) if q.get("limit")]
    if not quotas:
        return None
    q = min(quotas, key=lambda q: _PERIOD_PRIORITY.get(q.get("period"), 9))
    limit = int(q["limit"])
    used = int(q.get("used") or 0)
    period_ru = _PERIOD_RU.get(q.get("period"), "")
    suffix = f" {period_ru}" if period_ru else ""
    return f"{max(0, limit - used)} из {limit}{suffix} осталось"


def _requests_today_line(svc) -> str | None:
    n = svc.get("day_requests") or 0
    return f"{n} запросов сегодня" if n else None


def _gemini_ai_line(snapshot):
    label = "Gemini"
    if not _configured_service("gemini"):
        return f"{ui.OFF} {label} · нет ключа"
    state = api_usage.gemini_state(1)
    svc = _snapshot_service(snapshot, "gemini")
    left = _quota_left_line(svc) or "лимит OK"
    if state.get("cooldown_active"):
        return f"{ui.WARN} {label} · пауза до {_hhmm(state.get('cooldown_until'))} · {left}"
    if svc.get("status") == "bad":
        reason = _friendly_error(svc.get("last_error_reason")) or "ошибка"
        return f"{ui.BAD} {label} · основной · {str(reason)[:40]}"
    return f"{ui.OK} {label} · основной · {left}"


def _simple_ai_line(service, label, snapshot, used_by=None):
    role = f"основной для {', '.join(used_by)}" if used_by else "резерв"
    if not _configured_service(service):
        return f"{ui.OFF} {label} · {role} · нет ключа"
    svc = _snapshot_service(snapshot, service)
    if svc.get("status") == "bad":
        reason = _friendly_error(svc.get("last_error_reason")) or "ошибка"
        return f"{ui.BAD} {label} · {role} · {str(reason)[:40]}"
    left = _quota_left_line(svc) or _requests_today_line(svc)
    return f"{ui.OK} {label} · {role}" + (f" · {left}" if left else "")


def _openrouter_ai_line(snapshot, ai_module):
    if not config.OPENROUTER_API_KEY:
        return None
    usage = api_usage.openrouter_key_usage()
    if usage and usage.get("limit"):
        return f"{ui.OK} OpenRouter · резерв · {usage['remaining']} из {int(usage['limit'])} осталось"
    fallback_stats = ai_module.get_openrouter_fallback_stats(1)
    errors = int(fallback_stats.get("errors") or 0)
    return (f"{ui.OK} OpenRouter · резерв" if not errors
            else f"{ui.WARN} OpenRouter · резерв · {errors} ошибок сегодня")


def _api_line(service, label, snapshot):
    if service == "openweather":
        import weather
        usage = weather.get_weather_usage()
        total = int(usage.get("requests_total") or 0)
        limit = int(config.WEATHER_FREE_DAILY_LIMIT)
        if usage.get("last_error_reason") and usage.get("last_error_at"):
            return f"{ui.BAD} {label} · {_friendly_error(str(usage.get('last_error_reason')))[:40]}"
        return f"{ui.OK} {label} · {max(0, limit - total)} из {limit} осталось"
    if service == "firecrawl":
        remote = api_usage.firecrawl_credit_usage()
        if remote and remote.get("limit"):
            return f"{ui.OK} {label} · {remote['remaining']} из {int(remote['limit'])} осталось"
    if not _configured_service(service):
        return f"{ui.OFF} {label} · нет ключа"
    svc = _snapshot_service(snapshot, service)
    if svc.get("status") == "bad":
        reason = _friendly_error(svc.get("last_error_reason")) or "ошибка"
        return f"{ui.BAD} {label} · {str(reason)[:40]}"
    left = _quota_left_line(svc) or _requests_today_line(svc)
    return f"{ui.OK} {label} · {left}" if left else f"{ui.OK} {label} · лимит OK"


async def send_api_ai(bot, cid, q=None):
    import ai
    snapshot = api_usage.snapshot()
    gemini_state = api_usage.gemini_state(1)
    gemini_cooldown = bool(gemini_state.get("cooldown_active"))

    ai_rows = [
        _gemini_ai_line(snapshot),
        _simple_ai_line("groq", "Groq", snapshot, used_by=_used_by("Groq")),
        _simple_ai_line("cloudflare", "Cloudflare AI", snapshot),
    ]
    openrouter_line = _openrouter_ai_line(snapshot, ai)
    if openrouter_line:
        ai_rows.append(openrouter_line)

    api_rows = [
        _api_line("openweather", "OpenWeather", snapshot),
        _api_line("tavily", "Tavily", snapshot),
        _api_line("firecrawl", "Firecrawl", snapshot),
        _api_line("tmdb", "TMDB", snapshot),
        _api_line("ticketmaster", "Ticketmaster", snapshot),
        _api_line("zeroentropy", "ZeroEntropy", snapshot),
        _api_line("pexels", "Pexels", snapshot),
    ]

    n_bad = sum(1 for line in ai_rows + api_rows if line.startswith(ui.BAD))
    status_dot, status_text = (ui.WARN, "Работает с ограничениями") if (gemini_cooldown or n_bad) else (ui.OK, "Работает")
    if n_bad:
        word = "недоступен" if n_bad == 1 else "недоступно"
        sub_line = f"Основные функции доступны · {n_bad} {_plural_services(n_bad)} {word}"
    else:
        sub_line = "Все сервисы в норме"
    fallback_line = f"Резерв AI: {'включён' if gemini_cooldown else 'выключен'}"

    errors = _today_errors()
    last_failure = None
    if errors:
        last = errors[0]
        summary = _issue_summary(last.get("source", "app"), last.get("msg", ""))
        kind = last.get("kind", "")
        code = f" ({kind})" if kind else ""
        last_failure = (f"{_hhmm(last.get('ts', 0))} · {summary}{code}", _USER_FALLBACK_TEXT)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_home")],
    ])
    msg = ui.api_ai(status_dot, status_text, sub_line, fallback_line, ai_rows, api_rows,
                     last_failure, _updated_at())
    await _show(bot, cid, msg, kb, q)


async def send_notifications(bot, cid, q=None):
    stats = _notification_stats(cid)
    rows = _notification_test_rows()
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_home")])
    msg = ui.notifications(
        stats["sent_today"],
        stats["errors_today"],
        stats["active_types"],
        _updated_at(),
    )
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


async def check_notifications(bot, cid, q=None):
    await send_notifications(bot, cid, q)


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


# ================= УВЕДОМЛЕНИЯ =================

def _notification_options_by_kind():
    import settings as _s
    return {opt.key: opt.button_label for opt in _s.get_notification_options()}


def _notification_test_rows():
    import settings as _s
    return [
        [InlineKeyboardButton(opt.button_label, callback_data=f"set_admin_broadcast_test_{opt.key}")]
        for opt in _s.get_notification_options()
    ]


async def run_test(bot, cid, kind):
    """Прогоняет плановое уведомление прямо сейчас - само уведомление и есть результат теста,
    без отдельной карточки "Тест отправлен" поверх него."""
    import settings as _s
    if kind in _notification_options_by_kind():
        await _s._run_notif_test(bot, cid, kind)


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
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_system")],
    ])
    msg = ui.logs(rows, len(errors), _updated_at(), summary)
    await _show(bot, cid, msg, kb, q)
