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
import store
import tracking
from ui import admin as ui

_log = logging.getLogger(__name__)

DAY = 86400

# человекочитаемые имена модулей LLM в терминах пользовательских разделов
_MOD_NAMES = {
    "wardrobe": "👕 Гардероб", "balance": "🚑 Здоровье", "food": "🥣 Готовка",
    "weather": "☀️ Мой день", "learning": "📚 Обучение", "leisure": "🍿 Досуг",
    "myday": "☀️ Мой день", "travel": "🧳 Поездки", "assistant": "💬 Чат",
    "content": "🍿 Досуг", "notes": "🎚️ Настройки",
}

_PROV_ORDER = [
    ("gemini", "Gemini", lambda: True),
    ("groq", "Groq", lambda: bool(config.GROQ_API_KEY)),
    ("cf", "Cloudflare", lambda: bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID)),
]


def _back(target="set_admin"):
    return [InlineKeyboardButton("◀️ Назад", callback_data=target)]


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


def _news_line(snapshot=None):
    try:
        import personal_news
        snap = personal_news.budget_snapshot()
        errors = int(snap.get("errors") or 0)
        if errors:
            return f"{ui.WARN} Новости · ошибок {errors}"
    except Exception as e:
        return f"{ui.BAD} Новости · {str(e)[:42]}"
    return f"{ui.OK} Новости · работает"


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
        _news_line(snapshot),
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
        [InlineKeyboardButton("🔄 Проверить всё" if not system_bad else "🔄 Проверить снова",
                              callback_data="adm_check_all")],
        [InlineKeyboardButton("📊 Система", callback_data="adm_system"),
         InlineKeyboardButton("👥 Пользователи", callback_data="adm_users")],
        [InlineKeyboardButton("🔔 Уведомления", callback_data="adm_notif"),
         InlineKeyboardButton("🧪 Тесты", callback_data="adm_tests")],
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
    import onboarding_status as obs
    from settings import notif_on, NOTIF_TYPES
    cids = access.get_allowed_cids()
    onboarded = 0
    all_off = 0
    with_notifications = 0
    for c in cids:
        if all(obs.is_settled(c, s) for s in obs.SECTIONS):
            onboarded += 1
        any_notif = any(notif_on(c, k) for k, _ in NOTIF_TYPES)
        if any_notif:
            with_notifications += 1
        else:
            all_off += 1
    cutoff = time.time() - 7 * DAY
    activities = tracking._all()
    new_7d = sum(1 for r in activities.values() if r.get("first_ts", 0) >= cutoff)
    total = len(cids)
    return {
        "total": total,
        "new_today": tracking.new_today(),
        "new_7d": new_7d,
        "active_1d": tracking.active_count(1),
        "active_7d": tracking.active_count(7),
        "onboarded": onboarded,
        "not_onboarded": total - onboarded,
        "all_off": all_off,
        "with_notifications": with_notifications,
        "admins": sum(1 for c in cids if access.is_owner(c)),
        "active_invites": len(access.pending_invites()),
        "used_invites": 0,
        "avg_msgs": tracking.avg_messages(),
    }


def _last_active_user():
    """Самый недавно активный пользователь -> (dot, name, city, action, when) | None."""
    best_cid, best_ts = None, 0
    for c in access.get_allowed_cids():
        ts = tracking.get_activity(c).get("last_ts", 0)
        if ts > best_ts:
            best_cid, best_ts = c, ts
    if not best_cid:
        return None
    prof = store.get_profile(best_cid)
    settings = store.get_settings(best_cid)
    name = prof.get("name") or f"ID {str(best_cid)[:4]}…"
    city = settings.get("city", "")
    action = store.last_source.get(str(best_cid), "")
    return (tracking.churn_dot(best_cid), name, city, action, tracking.human_last_seen(best_cid))


async def send_users(bot, cid, q=None):
    stats = _user_stats()
    rows = [
        [InlineKeyboardButton("➕ Инвайт", callback_data="adm_invite_create")],
        [InlineKeyboardButton("✉️ Приветствие", callback_data="adm_welcome")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="adm_users")],
        [InlineKeyboardButton("⬅️ Админ", callback_data="adm_home")],
    ]
    msg = ui.users(stats, _updated_at())
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


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
        [InlineKeyboardButton("🔄 Проверить систему", callback_data="adm_system_check")],
        [InlineKeyboardButton("🔧 Диагностика", callback_data="adm_diag"),
         InlineKeyboardButton("📜 Логи", callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Админ", callback_data="adm_home")],
    ])
    msg = ui.system(_system_rows(), _updated_at())
    await _show(bot, cid, msg, kb, q)


async def check_system(bot, cid, q=None):
    """Активная проверка использует существующие probe-функции и возвращает экран системы."""
    results = None
    try:
        results = await _api_probe_results()
    except Exception as e:
        tracking.log_error("service", str(e), kind="system_probe")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Проверить систему", callback_data="adm_system_check")],
        [InlineKeyboardButton("🔧 Диагностика", callback_data="adm_diag"),
         InlineKeyboardButton("📜 Логи", callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Админ", callback_data="adm_home")],
    ])
    msg = ui.system(_system_rows(results), _updated_at())
    await _show(bot, cid, msg, kb, q)


async def send_diagnostics(bot, cid, q=None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("☁️ API", callback_data="adm_diag_api"),
         InlineKeyboardButton("🤖 LLM", callback_data="adm_diag_llm")],
        [InlineKeyboardButton("🧠 Новости", callback_data="adm_diag_news"),
         InlineKeyboardButton("📜 Логи", callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Система", callback_data="adm_system")],
    ])
    msg = ui.diagnostics()
    await _show(bot, cid, msg, kb, q)


def _api_diagnostic_rows(snapshot):
    rows = []
    for service in ("openweather", "groq", "gemini", "pexels", "tavily", "telegram", "tmdb", "cloudflare"):
        svc = _snapshot_service(snapshot, service)
        if not svc and not _configured_service(service):
            continue
        label = api_usage.SERVICE_LABELS.get(service, service)
        lines = []
        if service == "openweather":
            import weather
            usage = weather.get_weather_usage()
            total = int(usage.get("requests_total") or 0)
            lines.append(f"{total}/{config.WEATHER_FREE_DAILY_LIMIT} сегодня")
            if usage.get("last_error_reason"):
                lines.append(f"ошибка: {usage.get('last_error_reason')}")
        elif service == "tavily":
            quota = next((q for q in svc.get("quotas", []) if q.get("unit") == "credits"), None)
            if quota:
                lines.append(f"{quota.get('used', 0)}/{quota.get('limit', 1000)} месяц")
            lines.append(f"ошибок сегодня {len([e for e in svc.get('errors', []) if e.get('ts', 0) >= time.time() - DAY])}")
        else:
            if svc.get("day_requests"):
                lines.append(f"запросов сегодня {svc.get('day_requests')}")
            if svc.get("day_tokens"):
                lines.append(f"токенов сегодня {_num(svc.get('day_tokens'))}")
            lines.append(f"ошибок сегодня {len([e for e in svc.get('errors', []) if e.get('ts', 0) >= time.time() - DAY])}")
        if svc.get("last_error_reason"):
            lines.append(f"последняя ошибка: {svc.get('last_error_reason')}")
        rows.append((label, lines or ["работает"]))
    return rows


async def send_diag_api(bot, cid, q=None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="adm_diag_api")],
        [InlineKeyboardButton("⬅️ Диагностика", callback_data="adm_diag")],
    ])
    snapshot = api_usage.snapshot()
    msg = ui.api_diagnostics_compact(_api_diagnostic_rows(snapshot), _updated_at())
    await _show(bot, cid, msg, kb, q)


async def send_diag_llm(bot, cid, q=None):
    import ai
    usage = get_llm_usage_summary(1)
    fallback_stats = ai.get_openrouter_fallback_stats(1)
    errors = _today_errors("llm")
    providers = " · ".join(label for _key, label, cfg in _PROV_ORDER if cfg())
    fallback_errors = int(fallback_stats.get("errors", 0) or 0)
    fallback_text = "работает" if fallback_errors == 0 else f"{fallback_errors} проблем"
    problem = None
    if errors:
        last = errors[0]
        problem = _issue_summary("llm", last.get("msg", ""))
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="adm_diag_llm")],
        [InlineKeyboardButton("⬅️ Диагностика", callback_data="adm_diag")],
    ])
    msg = ui.llm_diagnostics(
        usage["calls"], usage["tokens"], len(errors), providers, fallback_text, problem, _updated_at()
    )
    await _show(bot, cid, msg, kb, q)


async def send_diag_news(bot, cid, q=None):
    import personal_news
    snap = personal_news.budget_snapshot()
    last = datetime.fromtimestamp(snap["last_build_ts"], config.TZ).strftime("%H:%M") if snap["last_build_ts"] else "—"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="adm_diag_news")],
        [InlineKeyboardButton("⬅️ Диагностика", callback_data="adm_diag")],
    ])
    msg = ui.news_diagnostics(
        snap["today_credits"], personal_news.NEWS_DAILY_CREDIT_BUDGET,
        snap["month_credits"], personal_news.TAVILY_MONTHLY_CREDIT_LIMIT,
        snap["cache_hits"], last, snap["errors"], _updated_at()
    )
    await _show(bot, cid, msg, kb, q)


async def send_notifications(bot, cid, q=None):
    stats = _notification_stats(cid)
    ok = stats["errors_today"] == 0
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 Тесты", callback_data="adm_tests")],
        [InlineKeyboardButton("🔄 Проверить", callback_data="adm_notif_check")],
        [InlineKeyboardButton("⬅️ Админ", callback_data="adm_home")],
    ])
    msg = ui.notifications(
        ui.OK if ok else ui.BAD,
        "Работают" if ok else "Есть ошибки",
        stats["sent_today"],
        stats["errors_today"],
        stats["active_types"],
        _updated_at(),
    )
    await _show(bot, cid, msg, kb, q)


async def check_notifications(bot, cid, q=None):
    await send_notifications(bot, cid, q)


# ================= LLM =================

def _cost_recent(days):
    import ai
    cutoff = time.time() - days * DAY
    return [e for e in ai.get_cost_log() if e.get("ts", 0) >= cutoff]

def _avg_ms(recent):
    vals = [e.get("ms", 0) for e in recent if e.get("ms")]
    return round(sum(vals) / len(vals)) if vals else 0

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

def _llm_today_count():
    return get_llm_usage_summary(1)["calls"]


async def send_llm(bot, cid):
    import ai
    log = ai.get_cost_log()
    last = log[-1] if log else {}
    usage = get_llm_usage_summary(1)
    fallback_stats = ai.get_openrouter_fallback_stats(1)
    errs = tracking.get_errors(source="llm", limit=200)
    errs_today = sum(1 for e in errs if e.get("ts", 0) >= time.time() - DAY)
    status_dot, status_txt = (ui.OK, "работает") if not errs_today else (ui.WARN, "есть ошибки")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Проверить", callback_data="set_admin_llmcheck"),
         InlineKeyboardButton("🕘 История", callback_data="set_admin_llmhistory")],
        _back(),
    ])
    msg = ui.llm(status_dot, status_txt, _when(last.get("ts", 0)), _avg_ms(_cost_recent(1)),
                 errs_today, usage["calls"], usage["tokens"], usage["providers"], fallback_stats)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_personal_news(bot, cid):
    import personal_news
    kb = InlineKeyboardMarkup([_back()])
    await bot.send_message(chat_id=cid, text=personal_news.admin_stats_text(), reply_markup=kb)


async def send_llm_check(bot, cid):
    results = await _llm_probe_results()
    kb = InlineKeyboardMarkup([_back("set_admin_llm")])
    msg = ui.llm_check(results)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def _llm_probe_results():
    import ai
    probes = [("Cloudflare", "cf"), ("Gemini", "gemini"), ("Groq", "groq")]
    results = []
    for label, route in probes:
        configured, missing = _provider_configured(route)
        if not configured:
            results.append((label, False, f"нет ключа: {missing}"))
            continue
        try:
            await ai.allm("Ответь одним словом: ok", 10, 0.0, order=(route,), module="admin")
            results.append((label, True, ""))
        except Exception as e:
            results.append((label, False, _issue_summary("llm", f"{route}:{e}")[:60]))
    return results


async def _api_probe_results():
    import asyncio
    llm_results = await _llm_probe_results()
    external_results = await asyncio.to_thread(_external_api_probe_results)
    return llm_results + external_results


_WEATHER_ONECALL_ENDPOINT = "https://api.openweathermap.org/data/4.0/onecall/current"
_TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"


def _weather_probe():
    """Health-check One Call API 4.0. Не логирует ключ и query-параметры."""
    if not config.WEATHER_API_KEY:
        return ("Weather", False, "нет ключа")
    try:
        import weather
        weather._onecall_get("current", 52.37, 4.89, timeout=12)
    except Exception as e:
        response = getattr(e, "response", None)
        if response is not None and getattr(response, "status_code", None) == 401:
            body = ""
            try:
                body = (response.text or "")[:200]
            except Exception:
                pass
            if "subscri" in body.lower():
                reason = "Нужна активация One Call API 4.0 в OpenWeather"
            else:
                reason = _http_error(response)
        elif response is not None:
            reason = _http_error(response)
            if reason.startswith("HTTP 429: LLM:"):
                reason = reason.replace("HTTP 429: LLM:", "HTTP 429:", 1)
        else:
            reason = _probe_exception(e)
        _log.warning("weather probe failed: endpoint=%s reason=%s", _WEATHER_ONECALL_ENDPOINT, reason)
        return ("Weather", False, reason)
    return ("Weather", True, "")


def _external_api_probe_results():
    import requests

    def missing(name):
        return (name, False, "нет ключа")

    def http_probe(label, key, method, url, **kwargs):
        if not key:
            return missing(label)
        try:
            timeout = kwargs.pop("timeout", 12)
            r = requests.request(method, url, timeout=timeout, **kwargs)
            if 200 <= r.status_code < 300:
                return (label, True, "")
            return (label, False, _http_error(r))
        except Exception as e:
            return (label, False, _probe_exception(e))

    def tavily_probe():
        configured = bool(config.TAVILY_API_KEY)
        if not configured:
            return ("Tavily", False, "ключ отсутствует, неверный или отозван")
        try:
            r = requests.post(
                _TAVILY_SEARCH_ENDPOINT,
                json={
                    "api_key": config.TAVILY_API_KEY,
                    "query": "Amsterdam",
                    "max_results": 1,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                },
                timeout=12,
            )
            if 200 <= r.status_code < 300:
                return ("Tavily", True, "")
            if r.status_code == 401:
                return ("Tavily", False, "ключ отсутствует, неверный или отозван")
            if r.status_code == 429:
                return ("Tavily", False, "лимит запросов исчерпан", "🟠")
            reason = (getattr(r, "reason", "") or "HTTP error")[:80]
            return ("Tavily", False,
                    f"HTTP {r.status_code}; HTTPError: {reason}; "
                    f"endpoint: {_TAVILY_SEARCH_ENDPOINT}; configured: yes")
        except requests.exceptions.Timeout:
            return ("Tavily", False, "сервис временно не ответил", "🟠")
        except Exception as e:
            exc_type = type(e).__name__
            msg = str(e).replace(config.TAVILY_API_KEY, "[redacted]")[:80]
            return ("Tavily", False,
                    f"HTTP n/a; {exc_type}: {msg}; "
                    f"endpoint: {_TAVILY_SEARCH_ENDPOINT}; configured: {'yes' if configured else 'no'}")

    results = [
        http_probe(
            "Telegram",
            config.TELEGRAM_TOKEN,
            "GET",
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getMe",
        ),
        http_probe(
            "TMDB",
            config.TMDB_API_KEY,
            "GET",
            "https://api.themoviedb.org/3/configuration",
            params={"api_key": config.TMDB_API_KEY},
        ),
        http_probe(
            "Ticketmaster",
            config.TICKETMASTER_API_KEY,
            "GET",
            "https://app.ticketmaster.com/discovery/v2/events.json",
            params={"apikey": config.TICKETMASTER_API_KEY, "countryCode": "NL", "size": 1},
        ),
        tavily_probe(),
        _weather_probe(),
        http_probe(
            "Pexels",
            config.PEXELS_API_KEY,
            "GET",
            "https://api.pexels.com/v1/search",
            headers={"Authorization": config.PEXELS_API_KEY},
            params={"query": "breakfast", "per_page": 1},
        ),
        http_probe(
            "ZeroEntropy",
            config.ZEROENTROPY_API_KEY,
            "POST",
            "https://api.zeroentropy.dev/v1/models/rerank",
            headers={"Authorization": f"Bearer {config.ZEROENTROPY_API_KEY}", "Content-Type": "application/json"},
            json={"model": "zerank-2", "query": "test", "documents": ["test document"], "top_n": 1, "latency": "fast"},
        ),
    ]
    return results


def _http_error(response):
    try:
        body = response.text or ""
    except Exception:
        body = ""
    summary = _issue_summary("llm", body)
    if summary.endswith("сбой генерации"):
        summary = body[:80] or response.reason or "ошибка"
    return f"HTTP {response.status_code}: {summary[:80]}"


def _probe_exception(exc):
    text = str(exc)
    if "NameResolutionError" in text or "Failed to resolve" in text:
        return "нет сетевого доступа/DNS"
    if "timeout" in text.lower():
        return "timeout"
    return text[:80]


def _provider_configured(route):
    if route == "openrouter":
        return bool(config.OPENROUTER_API_KEY), "OPENROUTER_API_KEY"
    if route == "gemini":
        return bool(config.GEMINI_API_KEY), "GEMINI_API_KEY"
    if route == "groq":
        return bool(config.GROQ_API_KEY), "GROQ_API_KEY"
    if route == "cf":
        ok = bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID)
        return ok, "CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID"
    return False, route


async def send_llm_history(bot, cid):
    import ai
    rows = []
    for e in reversed(ai.get_cost_log()[-12:]):
        rows.append((_hhmm(e.get("ts", 0)), (e.get("provider") or "?").capitalize(),
                     _MOD_NAMES.get(e.get("module", ""), e.get("module", "")), e.get("ok", True)))
    kb = InlineKeyboardMarkup([_back("set_admin_llm")])
    msg = ui.llm_history(rows)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= ПРОБЛЕМЫ =================

_ISSUES_CACHE = {}


def _issue_key(ts, source, kind) -> str:
    """Стабильный короткий ключ проблемы (не зависит от позиции в списке).
    Хешируем: source/kind могут быть длинными, а callback_data ограничен 64 байтами."""
    import hashlib
    raw = f"{ts}:{source}:{kind}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _issue_summary(source, msg):
    msg = str(msg or "")
    low = msg.lower()
    if source == "llm":
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


def _collect_issues():
    """Реальные проблемы из error-лога за сегодня (без внешних пингов) — дёшево, для дома/списка.

    rows: [(key, dot, name, detail)]."""
    errs = tracking.get_errors(limit=50)
    cutoff = time.time() - DAY
    rows = []
    for e in errs:
        ts = e.get("ts", 0)
        if ts < cutoff:
            continue
        source = e.get("source", "?")
        kind = e.get("kind", "")
        dot = ui.BAD if source in ("llm", "service") else ui.WARN
        rows.append((_issue_key(ts, source, kind), dot, source, f"{_issue_summary(source, e.get('msg', ''))} · {_when(ts)}"))
    return rows


async def _collect_issues_with_probes(cid):
    """То же самое + активный health-check БД/Weather/LLM API."""
    rows = _collect_issues()
    now = int(time.time())
    try:
        store._load("__health__")
    except Exception as e:
        rows.append((_issue_key(now, "service", "db"), ui.BAD, "База данных", str(e)[:40]))
    try:
        import asyncio
        import weather
        s = store.get_settings(cid)
        await asyncio.to_thread(weather.fetch_weather, s["lat"], s["lon"], 1)
    except Exception:
        rows.append((_issue_key(now, "service", "weather"), ui.BAD, "Weather", f"недоступна · {_when(now)}"))
    try:
        for result in await _api_probe_results():
            label, ok, detail = result[:3]
            if not ok:
                source = "llm" if label in {"OpenRouter", "Cloudflare", "Gemini", "Groq"} else "service"
                rows.append((_issue_key(now, source, label), ui.BAD, label, f"{detail} · {_when(now)}"))
    except Exception as e:
        rows.append((_issue_key(now, "llm", "probe"), ui.BAD, "LLM", f"{_issue_summary('llm', str(e))} · {_when(now)}"))
    return rows


async def send_issues(bot, cid, with_probes=False):
    rows = await _collect_issues_with_probes(cid) if with_probes else _collect_issues()
    kb_rows = []
    for key, dot, name, detail in rows:
        kb_rows.append([InlineKeyboardButton(f"{dot} {name}", callback_data=f"set_admin_issue_{key}")])
    kb_rows.append([InlineKeyboardButton("🔄 Проверить API", callback_data="set_admin_check_all"),
                     InlineKeyboardButton("🧹 Очистить ошибки", callback_data="set_admin_cache_clear")])
    kb_rows.append(_back())
    _ISSUES_CACHE[cid] = {key: (dot, name, detail) for key, dot, name, detail in rows}
    msg = ui.issues([(dot, name, detail) for _, dot, name, detail in rows], _when(time.time()))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(kb_rows))


async def send_issue_detail(bot, cid, key):
    entry = _ISSUES_CACHE.get(cid, {}).get(key)
    if entry is None:
        await bot.send_message(chat_id=cid, text="Проблема уже не доступна. Открываю актуальный список.")
        await send_issues(bot, cid)
        return
    dot, name, detail = entry
    kb = InlineKeyboardMarkup([_back("set_admin_issues")])
    msg = ui.issue_detail(_when(time.time()), name, dot, detail)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


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


async def check_all(bot, cid):
    """Показывает сохранённую статистику API без новых внешних probe-запросов."""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="set_admin_check_all"),
         InlineKeyboardButton("📋 Диагностика", callback_data="set_admin_api_diagnostics")],
        _back("set_admin"),
    ])
    msg = ui.api_check(api_usage.snapshot())
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_api_diagnostics(bot, cid):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="set_admin_api_diagnostics")],
        _back("set_admin_check_all"),
    ])
    msg = ui.api_diagnostics(api_usage.snapshot())
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= УВЕДОМЛЕНИЯ =================

def _next_broadcast():
    """Ближайшее автоматическое уведомление: (title, when). Сейчас — утренний бриф."""
    return "☀️ Утренний дайджест", "завтра, 08:00"


_TEST_HISTORY = []
_LAST_TEST_KIND = {}

_TEST_LABELS = {
    "morning": "☀️ Утро",
    "weather": "🌦 Погода",
    "word_nl": "📚 NL",
    "word_en": "🇬🇧 EN",
    "recipe": "🍽 Еда",
    "leisure": "🎬 Досуг",
    "news": "🧠 News",
}

_TEST_KIND_ALIASES = {
    "morning": "morning_brief",
    "weather": "weather_warn",
    "word_nl": "daily_words_nl",
    "word_en": "daily_words_en",
    "recipe": "recipe_daily",
    "leisure": "weekly_events",
    "news": "personal_news",
    "morning_brief": "morning_brief",
    "weather_warn": "weather_warn",
    "daily_words_nl": "daily_words_nl",
    "daily_words_en": "daily_words_en",
    "recipe_daily": "recipe_daily",
    "weekly_events": "weekly_events",
    "personal_news": "personal_news",
}

_TEST_ORDER = [
    "morning",
    "weather",
    "word_nl",
    "word_en",
    "recipe",
    "leisure",
    "news",
]


def _test_label(kind):
    reverse = {v: k for k, v in _TEST_KIND_ALIASES.items() if k in _TEST_LABELS}
    key = reverse.get(kind, kind)
    return _TEST_LABELS.get(key, dict(__import__("settings").NOTIF_TYPES).get(kind, kind))


def _remember_test(kind, ok, detail):
    label = _test_label(kind)
    row = f"{_updated_at()} · {label.replace('☀️ ', '').replace('🌦 ', '').replace('📚 ', '').replace('🇬🇧 ', '').replace('🍽 ', '').replace('🎬 ', '').replace('🧠 ', '')} · {detail}"
    _TEST_HISTORY.insert(0, row)
    del _TEST_HISTORY[5:]


async def send_tests(bot, cid, q=None):
    rows = [
        [InlineKeyboardButton(_test_label("morning"), callback_data="adm_test_morning"),
         InlineKeyboardButton(_test_label("weather"), callback_data="adm_test_weather")],
        [InlineKeyboardButton(_test_label("word_nl"), callback_data="adm_test_word_nl"),
         InlineKeyboardButton(_test_label("word_en"), callback_data="adm_test_word_en")],
        [InlineKeyboardButton(_test_label("recipe"), callback_data="adm_test_recipe"),
         InlineKeyboardButton(_test_label("leisure"), callback_data="adm_test_leisure")],
        [InlineKeyboardButton(_test_label("news"), callback_data="adm_test_news"),
         InlineKeyboardButton("✅ Все", callback_data="adm_test_all")],
        [InlineKeyboardButton("⬅️ Админ", callback_data="adm_home")],
    ]
    msg = ui.tests(_TEST_HISTORY)
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


async def run_test(bot, cid, kind):
    import settings as _s
    if kind == "repeat":
        kind = _LAST_TEST_KIND.get(str(cid), "all")
    kinds = list(_TEST_ORDER) if kind == "all" else [kind]
    ok = True
    failed_detail = ""
    for item in kinds:
        notif_kind = _TEST_KIND_ALIASES.get(item, item)
        if notif_kind not in dict(_s.NOTIF_TYPES):
            ok = False
            failed_detail = "неизвестный тест"
            break
        if not await _s._run_notif_test(bot, cid, notif_kind):
            ok = False
            failed_detail = _issue_summary("app", notif_kind) or "ошибка"
            break
    label = "✅ Все" if kind == "all" else _test_label(kind)
    detail = "OK" if ok else failed_detail
    _LAST_TEST_KIND[str(cid)] = kind
    _remember_test(kind, ok, detail)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Повторить", callback_data=f"adm_test_{kind}") if kind != "all"
         else InlineKeyboardButton("🔁 Повторить", callback_data="adm_test_repeat"),
         InlineKeyboardButton("🧪 Тесты", callback_data="adm_tests")],
        [InlineKeyboardButton("⬅️ Админ", callback_data="adm_home")],
    ])
    msg = ui.test_result(ok, _updated_at(), label, detail)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_broadcast(bot, cid, q=None):
    await send_notifications(bot, cid, q)


async def send_broadcast_test_pick(bot, cid, q=None):
    await send_tests(bot, cid, q)


async def send_logs(bot, cid, q=None):
    cutoff = time.time() - DAY
    errors = [e for e in tracking.get_errors(limit=200) if e.get("ts", 0) >= cutoff]
    rows = []
    for e in errors[:5]:
        source = str(e.get("source") or "app")
        rows.append(f"{_hhmm(e.get('ts', 0))} · {source} · {_issue_summary(source, e.get('msg', ''))}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Система", callback_data="adm_system")],
    ])
    msg = ui.logs(rows, len(errors), _updated_at())
    await _show(bot, cid, msg, kb, q)
