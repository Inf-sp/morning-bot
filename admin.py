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


# ================= ДОМ =================

async def send_home(bot, cid):
    stats = _user_stats()
    usage = get_llm_usage_summary(1)
    import weather
    weather_usage = weather.get_weather_usage()
    issues = _collect_issues()
    errors = tracking.errors_today()
    if errors == 0:
        dot, txt = ui.OK, "всё работает"
    elif errors < 10:
        dot, txt = ui.WARN, "есть ошибки"
    else:
        dot, txt = ui.BAD, "много ошибок"
    next_title, next_when = _next_broadcast()
    issues_label = ("⚠️ Проблемы" if issues else "🟢 Система")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Пользователи", callback_data="set_admin_users"),
         InlineKeyboardButton("🤖 LLM", callback_data="set_admin_llm")],
        [InlineKeyboardButton("📰 Personal News", callback_data="set_admin_news")],
        [InlineKeyboardButton("🔔 Уведомления", callback_data="set_admin_broadcast"),
         InlineKeyboardButton(f"{issues_label} ({len(issues)})" if issues else issues_label,
                               callback_data="set_admin_issues")],
        [InlineKeyboardButton("🔄 Проверить доступное", callback_data="set_admin_check_all")],
    ])
    top_issue = f"{issues[0][1]} · {issues[0][2]}" if issues else None
    msg = ui.home(
        system_dot=dot, system_text=txt,
        total_users=stats["total"], active_7d=stats["active_7d"],
        llm_calls_today=usage["calls"], llm_tokens_today=usage["tokens"],
        weather_usage=weather_usage,
        next_broadcast_title=next_title, next_broadcast_when=next_when,
        issues_count=len(issues), top_issue=top_issue,
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= ПОЛЬЗОВАТЕЛИ =================

def _user_stats():
    import onboarding_status as obs
    from settings import notif_on, NOTIF_TYPES
    cids = access.get_allowed_cids()
    onboarded = 0
    all_off = 0
    for c in cids:
        if all(obs.is_settled(c, s) for s in obs.SECTIONS):
            onboarded += 1
        if not any(notif_on(c, k) for k, _ in NOTIF_TYPES):
            all_off += 1
    total = len(cids)
    return {
        "total": total,
        "new_today": tracking.new_today(),
        "active_1d": tracking.active_count(1),
        "active_7d": tracking.active_count(7),
        "onboarded": onboarded,
        "not_onboarded": total - onboarded,
        "all_off": all_off,
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


async def send_users(bot, cid):
    stats = _user_stats()
    last_user = _last_active_user()
    rows = []
    for uid in access.get_allowed_cids():
        prof = store.get_profile(uid)
        name = prof.get("name", "") or str(uid)
        dot = tracking.churn_dot(uid)
        if access.is_owner(uid):
            continue
        rows.append([InlineKeyboardButton(f"❌ {dot} {name}", callback_data=f"set_admin_revoke_{uid}")])
    rows.append([InlineKeyboardButton("🔗 Создать инвайт", callback_data="set_admin_invite")])
    rows.append(_back())
    msg = ui.users(stats, last_user)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))


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
            "Unsplash",
            config.UNSPLASH_ACCESS_KEY,
            "GET",
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}"},
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


async def clear_cache(bot, cid):
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
    await send_issues(bot, cid)


async def check_all(bot, cid):
    """Перепроверяет доступные health-check'и: БД, Weather и LLM API."""
    rows = await _api_probe_results()
    import weather
    usage = weather.get_weather_usage()
    history = weather.get_weather_usage_last_days(7)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🕘 OpenWeather 7 дней", callback_data="set_admin_weather_usage")],
        _back("set_admin_issues"),
    ])
    msg = ui.api_check(rows, weather_usage=usage, weather_history=history)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_weather_usage(bot, cid):
    import weather
    kb = InlineKeyboardMarkup([_back("set_admin_check_all")])
    msg = ui.weather_usage_history(weather.get_weather_usage_last_days(7))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= УВЕДОМЛЕНИЯ =================

def _next_broadcast():
    """Ближайшее автоматическое уведомление: (title, when). Сейчас — утренний бриф."""
    return "☀️ Утренний дайджест", "завтра, 08:00"


async def send_broadcast(bot, cid):
    next_title, next_when = _next_broadcast()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 Тест уведомления", callback_data="set_admin_broadcast_test_pick")],
        _back(),
    ])
    msg = ui.broadcast(next_title, next_when)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_broadcast_test_pick(bot, cid):
    import settings as _s
    options = _s.get_admin_notification_options()
    kb_rows = [[InlineKeyboardButton(opt.title, callback_data=f"set_admin_broadcast_test_{opt.key}")]
               for opt in options]
    kb_rows.append(_back("set_admin_broadcast"))
    msg = ui.notification_picker(options)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(kb_rows))
