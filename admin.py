"""Логика админ-панели (§ docs/admin.md).

Собирает данные для каждого экрана из access/store/tracking/ai и отдаёт готовый
MessageSpec из ui.admin. Роутинг (settings.dispatch) делегирует сюда через send_*.

Все функции — async send_*(bot, cid); гард на владельца — в settings._admin_guard.
"""
import logging
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

_PROV_ORDER = [
    ("gemini", "Gemini", lambda: True),
    ("groq", "Groq", lambda: bool(config.GROQ_API_KEY)),
    ("cf", "Cloudflare", lambda: bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID)),
]


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


def _short_status(service: str, label: str, snapshot=None):
    svc = _snapshot_service(snapshot or api_usage.snapshot(), service)
    if not _configured_service(service):
        return f"{ui.WARN} {label} · нет ключа"
    if service == "gemini":
        state = api_usage.gemini_state(1)
        if state.get("cooldown_active"):
            return f"{ui.WARN} {label} · cooldown до {_hhmm(state.get('cooldown_until'))}"
        if state.get("last_429_at") and state.get("last_429_at") >= time.time() - DAY and svc.get("last_ok") is False:
            scope = f" {state.get('cooldown_scope')}" if state.get("cooldown_scope") else ""
            return f"{ui.BAD} {label} · лимит{scope}".strip()
    if svc:
        status = svc.get("status")
        if status == "bad":
            reason = svc.get("last_error_reason") or svc.get("status_text") or "есть ошибка"
            return f"{ui.BAD} {label} · {str(reason)[:42]}"
        if status == "warn":
            return f"{ui.WARN} {label} · {svc.get('status_text') or 'есть предупреждение'}"
    return f"{ui.OK} {label} · работает"


def _openweather_line(snapshot=None):
    import weather
    usage = weather.get_weather_usage()
    total = int(usage.get("requests_total") or 0)
    limit = int(config.WEATHER_FREE_DAILY_LIMIT)
    last_error = usage.get("last_error_reason") or ""
    if total >= int(config.WEATHER_HARD_DAILY_LIMIT):
        return f"{ui.BAD} OpenWeather · лимит {total}/{limit}"
    if last_error and usage.get("last_error_at"):
        return f"{ui.BAD} OpenWeather · {str(last_error)[:42]}"
    if total >= int(config.WEATHER_WARNING_LIMIT):
        return f"{ui.WARN} OpenWeather · {total}/{limit} сегодня"
    return f"{ui.OK} OpenWeather · {total}/{limit} сегодня"


def _llm_line():
    usage = get_llm_usage_summary(1)
    errors = len(_today_errors("llm"))
    dot = ui.OK if errors == 0 else ui.BAD
    return f"{dot} LLM · {usage['calls']} запросов · {errors} ошибок"


def _data_line():
    try:
        store._load("__health__")
    except Exception as e:
        return f"{ui.BAD} Данные · {str(e)[:42]}"
    return f"{ui.OK} Данные · работает"


def _probe_overrides(probe_results):
    overrides = {}
    if not probe_results:
        return overrides
    label_map = {
        "Weather": "OpenWeather",
        "Cloudflare": "Cloudflare",
        "Gemini": "Gemini",
        "Groq": "Groq",
        "Telegram": "Telegram",
        "TMDB": "TMDB",
        "Pexels": "Pexels",
        "Tavily": "Tavily",
    }
    for result in probe_results:
        label, ok, detail = result[:3]
        target = label_map.get(label, label)
        overrides[target] = f"{ui.OK} {target} · работает" if ok else f"{ui.BAD} {target} · {str(detail)[:42]}"
    return overrides


def _apply_probe_overrides(rows, probe_results):
    overrides = _probe_overrides(probe_results)
    if not overrides:
        return rows
    out = []
    for row in rows:
        replaced = False
        for label, line in overrides.items():
            if f" {label} ·" in row:
                out.append(line)
                replaced = True
                break
        if not replaced:
            out.append(row)
    return out


def _system_rows(probe_results=None):
    snapshot = api_usage.snapshot()
    rows = [
        _llm_line(),
        _openweather_line(snapshot),
        _short_status("telegram", "Telegram", snapshot),
        _short_status("tmdb", "TMDB", snapshot),
        _short_status("pexels", "Pexels", snapshot),
        _short_status("cloudflare", "Cloudflare", snapshot),
        _short_status("groq", "Groq", snapshot),
        _short_status("gemini", "Gemini", snapshot),
        _short_status("tavily", "Tavily", snapshot),
        _short_status("firecrawl", "Firecrawl", snapshot),
        _short_status("pexels", "Фото рецептов", snapshot),
        _data_line(),
        f"{ui.OK} Планировщик · работает",
    ]
    return _apply_probe_overrides(rows, probe_results)


def _has_bad_system_row(rows):
    return any(row.startswith(ui.BAD) for row in rows)


def _line_summary_for_home(rows):
    bad = next((row for row in rows if row.startswith(ui.BAD)), None)
    if bad:
        return bad.split(" ", 1)[1]
    warn = next((row for row in rows if row.startswith(ui.WARN)), None)
    if warn:
        return warn.split(" ", 1)[1]
    return "OK · лимиты в норме"


# ================= ДОМ =================

async def send_home(bot, cid, q=None):
    stats = _user_stats()
    system_rows = _system_rows()
    notif = _notification_stats(cid)
    system_bad = _has_bad_system_row(system_rows)
    notif_bad = notif["errors_today"] > 0
    dot, txt = (ui.BAD, "Есть проблема") if (system_bad or notif_bad) else (ui.OK, "Всё работает")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui_label("system", "Система"), callback_data="adm_system")],
        [InlineKeyboardButton(ui_label("notifications", "Уведомления"), callback_data="adm_notif")],
        [InlineKeyboardButton(ui_label("users", "Пользователи"), callback_data="adm_users")],
    ])
    msg = ui.home(
        system_dot=dot,
        system_text=txt,
        system_line=_line_summary_for_home(system_rows),
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


async def send_system(bot, cid, q=None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔌 API и AI", callback_data="adm_api_ai")],
        [InlineKeyboardButton(ui_label("logs", "Логи"), callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_home")],
    ])
    msg = ui.system(_system_rows(), _updated_at())
    await _show(bot, cid, msg, kb, q)


# ================= API И AI (единый экран, § docs/admin.md) =================

_FEATURE_ROWS = [
    ("Мой день", "OpenWeather + Wiki + Gemini", ("gemini",)),
    ("Погода", "OpenWeather", ()),
    ("Гардероб", "OpenWeather + Gemini", ("gemini",)),
    ("Готовка", "Gemini → Groq", ("gemini",)),
    ("Здоровье", "Gemini + ZeroEntropy", ("gemini",)),
    ("Обучение", "Gemini → Groq", ("gemini",)),
    ("Путешествия", "Tavily + Gemini", ("gemini",)),
    ("Досуг", "TMDB/Tavily/Ticketmaster + Gemini", ("gemini",)),
    ("Ассистент", "intent-router + Gemini", ("gemini",)),
]


def _gemini_ai_line(snapshot):
    label = "Gemini"
    if not _configured_service("gemini"):
        return f"{ui.OFF} {label} · нет ключа"
    state = api_usage.gemini_state(1)
    quota = next((q for q in config.API_QUOTAS.get("gemini", []) if q.get("unit") == "requests"), None)
    limit_txt = f"{quota.get('limit')} / мин" if quota else "лимит OK"
    if state.get("cooldown_active"):
        return f"{ui.WARN} {label} · пауза до {_hhmm(state.get('cooldown_until'))} · {limit_txt}"
    return f"{ui.OK} {label} · работает · {limit_txt}"


def _simple_ai_line(service, label, snapshot):
    if not _configured_service(service):
        return f"{ui.OFF} {label} · резерв · нет ключа"
    svc = _snapshot_service(snapshot, service)
    if svc.get("status") == "bad":
        reason = svc.get("last_error_reason") or "ошибка"
        return f"{ui.BAD} {label} · резерв · {str(reason)[:40]}"
    return f"{ui.OK} {label} · резерв · лимит OK"


def _api_line(service, label, snapshot):
    if service == "openweather":
        import weather
        usage = weather.get_weather_usage()
        total = int(usage.get("requests_total") or 0)
        limit = int(config.WEATHER_FREE_DAILY_LIMIT)
        if usage.get("last_error_reason") and usage.get("last_error_at"):
            return f"{ui.BAD} {label} · ошибка · {str(usage.get('last_error_reason'))[:40]}"
        return f"{ui.OK} {label} · работает · осталось {max(0, limit - total)} / {limit}"
    if not _configured_service(service):
        return f"{ui.OFF} {label} · нет ключа"
    svc = _snapshot_service(snapshot, service)
    if service == "tavily":
        quota = next((q for q in svc.get("quotas", []) if q.get("unit") == "credits"), None)
        if quota:
            limit = int(quota.get("limit") or 1000)
            used = int(quota.get("used") or 0)
            return f"{ui.OK} {label} · работает · осталось {max(0, limit - used)} / {limit}"
        return f"{ui.OK} {label} · работает · лимит OK"
    if service == "ticketmaster":
        return f"{ui.OK} {label} · работает · кэш 7 дней"
    if svc.get("status") == "bad":
        reason = svc.get("last_error_reason") or "ошибка"
        return f"{ui.BAD} {label} · ошибка · {str(reason)[:40]}"
    return f"{ui.OK} {label} · работает · лимит OK"


def _feature_status(snapshot, gemini_cooldown):
    rows = []
    for name, providers, deps in _FEATURE_ROWS:
        is_fallback = gemini_cooldown and "gemini" in deps
        dot = ui.WARN if is_fallback else ui.OK
        status = "fallback" if is_fallback else "работает"
        rows.append(f"{dot} {name} · {providers} · {status}")
    return rows


async def send_api_ai(bot, cid, q=None):
    import ai
    snapshot = api_usage.snapshot()
    gemini_state = api_usage.gemini_state(1)
    gemini_cooldown = bool(gemini_state.get("cooldown_active"))

    ai_rows = [
        _gemini_ai_line(snapshot),
        _simple_ai_line("groq", "Groq", snapshot),
        _simple_ai_line("cloudflare", "Cloudflare AI", snapshot),
    ]
    if config.OPENROUTER_API_KEY:
        fallback_stats = ai.get_openrouter_fallback_stats(1)
        errors = int(fallback_stats.get("errors") or 0)
        ai_rows.append(
            "OpenRouter · резерв · лимит OK" if not errors else f"OpenRouter · резерв · {errors} ошибок"
        )

    api_rows = [
        _api_line("openweather", "OpenWeather", snapshot),
        _api_line("tavily", "Tavily", snapshot),
        _api_line("firecrawl", "Firecrawl", snapshot),
        _api_line("tmdb", "TMDB", snapshot),
        _api_line("ticketmaster", "Ticketmaster", snapshot),
        _api_line("zeroentropy", "ZeroEntropy", snapshot),
        _api_line("pexels", "Pexels", snapshot),
    ]

    feature_rows = _feature_status(snapshot, gemini_cooldown)

    problem_line = None
    status_ok = True
    if gemini_cooldown:
        scope = gemini_state.get("cooldown_scope") or ""
        problem_line = f"Gemini на паузе до {_hhmm(gemini_state.get('cooldown_until'))}" + (f" - {scope}" if scope else "")
        status_ok = False
    bad_service = next((s for s in snapshot.get("services", []) if s.get("status") == "bad"), None)
    if bad_service and not problem_line:
        problem_line = f"{bad_service.get('label')} · {bad_service.get('last_error_reason') or 'ошибка'}"
        status_ok = False

    errors = _today_errors()
    last_error_line = None
    if errors:
        last = errors[0]
        last_error_line = f"{_hhmm(last.get('ts', 0))} · {_issue_summary(last.get('source', 'app'), last.get('msg', ''))}"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui_label("logs", "Логи"), callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_system")],
    ])
    msg = ui.api_ai(status_ok, gemini_cooldown, problem_line, ai_rows, api_rows, feature_rows,
                     last_error_line, _updated_at())
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


# ================= LLM =================

def _cost_recent(days):
    import ai
    cutoff = time.time() - days * DAY
    return [e for e in ai.get_cost_log() if e.get("ts", 0) >= cutoff]

def get_llm_usage_summary(period_days=1):
    """Расходы/нагрузка LLM за период — данные, без Telegram-разметки."""
    recent = _cost_recent(period_days)
    total = sum(e.get("tokens", 0) for e in recent)
    by_prov = {}
    for e in recent:
        prov = e.get("provider") or "?"
        by_prov[prov] = by_prov.get(prov, 0) + e.get("tokens", 0)
    providers = []
    if total:
        for key, label, _cfg in _PROV_ORDER:
            tok = by_prov.get(key, 0)
            if tok:
                providers.append((label, round(tok / total * 100)))
        providers.sort(key=lambda x: -x[1])
    return {
        "calls": len(recent),
        "tokens": total,
        "avg_tokens": round(total / len(recent)) if recent else 0,
        "providers": providers,
    }


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
    import settings as _s
    options = _notification_options_by_kind()
    if kind not in options:
        ok = False
        detail = "неизвестный тест"
        label = kind
    else:
        ok = await _s._run_notif_test(bot, cid, kind)
        detail = "OK" if ok else (_issue_summary("app", kind) or "ошибка")
        label = options[kind]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_notif")],
    ])
    msg = ui.test_result(ok, _updated_at(), label, detail)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


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
