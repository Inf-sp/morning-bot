"""Persistent API usage accounting for the admin API screen.

Only real external requests should call `record_*`. Cache hits are intentionally
not counted as API usage.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import requests

import config
import store

SERVICE_LABELS = {
    "openweather": "OpenWeather",
    "gemini": "Gemini",
    "pexels": "Pexels",
    "tavily": "Tavily",
    "firecrawl": "Firecrawl",
    "cloudflare": "Cloudflare",
    "groq": "Groq",
    "cohere": "Cohere",
    "github_models": "GitHub Models",
    "google_books": "Google Books",
    "languagetool": "LanguageTool",
    "spoonacular": "Spoonacular",
    "themealdb": "TheMealDB",
    "telegram": "Telegram",
    "tmdb": "TMDB",
    "ticketmaster": "Ticketmaster",
    "zeroentropy": "ZeroEntropy",
}

SERVICE_ICONS = SERVICE_LABELS


def _now():
    return datetime.now(config.TZ)


def _bucket(period: str, dt=None) -> str:
    dt = dt or _now()
    if period == "minute":
        return dt.strftime("%Y-%m-%dT%H:%M")
    if period == "hour":
        return dt.strftime("%Y-%m-%dT%H")
    if period == "month":
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")


def _period_start(period: str, dt=None):
    dt = dt or _now()
    if period == "minute":
        return dt.replace(second=0, microsecond=0)
    if period == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    if period == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _template():
    return {"services": {}}


def _service(data, name: str):
    svc = data.setdefault("services", {}).setdefault(name, {})
    svc.setdefault("counts", {})
    svc.setdefault("errors", [])
    return svc


def _inc_count(svc: dict, period: str, unit: str, amount: int, dt=None) -> None:
    key = f"{period}:{unit}:{_bucket(period, dt)}"
    counts = svc.setdefault("counts", {})
    counts[key] = int(counts.get(key) or 0) + int(amount or 0)


def _count(svc: dict, period: str, unit: str, dt=None) -> int:
    key = f"{period}:{unit}:{_bucket(period, dt)}"
    return int((svc.get("counts") or {}).get(key) or 0)


def _prune(svc: dict, now_ts: int) -> None:
    cutoff = now_ts - 40 * 86400
    svc["errors"] = [e for e in (svc.get("errors") or [])[-20:] if int(e.get("ts") or 0) >= cutoff]
    svc["events"] = [e for e in (svc.get("events") or [])[-100:] if int(e.get("ts") or 0) >= cutoff]


def _append_event(svc: dict, event: dict, now_ts: int) -> None:
    events = svc.setdefault("events", [])
    events.append({"ts": now_ts, **event})
    svc["events"] = events[-100:]


def record_request(service: str, ok: bool = True, *, units: dict | None = None,
                   status_code: int | None = None, error: str = "",
                   latency_ms: int | None = None, headers: dict | None = None) -> None:
    now = _now()
    now_ts = int(now.timestamp())
    units = {"requests": 1, **(units or {})}

    def mut(data):
        data = data or _template()
        svc = _service(data, service)
        for unit, amount in units.items():
            for period in ("minute", "hour", "day", "month"):
                _inc_count(svc, period, unit, int(amount or 0), now)
        svc["last_request_at"] = now_ts
        svc["last_ok"] = bool(ok)
        if ok:
            svc["last_success_at"] = now_ts
        else:
            svc["last_error_at"] = now_ts
            svc["last_error_reason"] = str(error or f"HTTP {status_code or '?'}")[:120]
            if status_code == 429 or "rate" in str(error).lower():
                svc["rate_limit_errors"] = int(svc.get("rate_limit_errors") or 0) + 1
                svc["last_rate_limit_at"] = now_ts
            svc.setdefault("errors", []).append({
                "ts": now_ts,
                "status_code": status_code,
                "reason": str(error or "")[:120],
            })
        if latency_ms is not None:
            prev_n = int(svc.get("latency_count") or 0)
            prev_avg = int(svc.get("avg_latency_ms") or 0)
            svc["latency_count"] = prev_n + 1
            svc["avg_latency_ms"] = round((prev_avg * prev_n + int(latency_ms)) / (prev_n + 1))
        if headers:
            svc["last_headers"] = {
                k: str(v)[:80] for k, v in headers.items()
                if str(k).lower().startswith(("x-ratelimit", "ratelimit", "retry-after"))
            }
        _prune(svc, now_ts)
        return data, True

    try:
        store.mutate_kv(config.API_USAGE_KEY, mut)
    except Exception:
        pass


def set_gemini_rate_limit(*, limit_scope: str = "", retry_after: int | None = None,
                          cooldown_until: int | None = None, message: str = "") -> None:
    now_ts = int(_now().timestamp())
    scope = (limit_scope or "limit").upper()
    until = int(cooldown_until or (now_ts + max(60, int(retry_after or 0))))

    def mut(data):
        data = data or _template()
        svc = _service(data, "gemini")
        svc["last_ok"] = False
        svc["last_error_at"] = now_ts
        svc["last_error_reason"] = f"лимит {scope}".strip()
        svc["last_rate_limit_at"] = now_ts
        svc["last_429_at"] = now_ts
        svc["cooldown_until"] = until
        svc["cooldown_scope"] = scope
        svc["last_retry_after"] = int(retry_after or 0)
        _append_event(svc, {
            "type": "rate_limit",
            "limit_scope": scope,
            "retry_after": int(retry_after or 0),
            "cooldown_until": until,
            "message": str(message or "")[:120],
        }, now_ts)
        _prune(svc, now_ts)
        return data, True

    try:
        store.mutate_kv(config.API_USAGE_KEY, mut)
    except Exception:
        pass


def record_gemini_fallback(*, target: str = "local", reason: str = "") -> None:
    now_ts = int(_now().timestamp())

    def mut(data):
        data = data or _template()
        svc = _service(data, "gemini")
        svc["last_fallback_at"] = now_ts
        svc["fallback_count"] = int(svc.get("fallback_count") or 0) + 1
        _append_event(svc, {
            "type": "fallback",
            "target": str(target or "local")[:40],
            "reason": str(reason or "")[:80],
        }, now_ts)
        _prune(svc, now_ts)
        return data, True

    try:
        store.mutate_kv(config.API_USAGE_KEY, mut)
    except Exception:
        pass


def should_log_gemini_limit(dedup_token: str) -> bool:
    """Персистентная дедупликация лог-записей о лимите Gemini.

    Переживает рестарт процесса: без этого каждый деплой/рестарт Railway
    сбрасывал бы in-memory дедуп и плодил дубли записей об одном и том же
    ещё не истёкшем cooldown.
    """
    result = {"log": False}

    def mut(data):
        data = data or _template()
        svc = _service(data, "gemini")
        if svc.get("last_logged_dedup_token") == dedup_token:
            return data, False
        svc["last_logged_dedup_token"] = dedup_token
        result["log"] = True
        return data, True

    try:
        store.mutate_kv(config.API_USAGE_KEY, mut)
    except Exception:
        return True
    return result["log"]


def gemini_state(period_days: int = 1) -> dict:
    try:
        data = store._load(config.API_USAGE_KEY)
        svc = ((data.get("services") or {}).get("gemini") or {}) if isinstance(data, dict) else {}
    except Exception:
        svc = {}
    cutoff = int(time.time() - period_days * 86400)
    events = [e for e in (svc.get("events") or []) if int(e.get("ts") or 0) >= cutoff]
    cooldown_until = int(svc.get("cooldown_until") or 0)
    now_ts = int(time.time())
    return {
        "last_429_at": int(svc.get("last_429_at") or svc.get("last_rate_limit_at") or 0),
        "cooldown_until": cooldown_until,
        "cooldown_active": cooldown_until > now_ts,
        "cooldown_seconds": max(0, cooldown_until - now_ts),
        "cooldown_scope": str(svc.get("cooldown_scope") or "").upper(),
        "last_retry_after": int(svc.get("last_retry_after") or 0),
        "rate_limits": sum(1 for e in events if e.get("type") == "rate_limit"),
        "fallbacks": sum(1 for e in events if e.get("type") == "fallback"),
        "events": events,
        "last_error_reason": svc.get("last_error_reason") or "",
    }


def record_cache_hit(service: str) -> None:
    now = _now()

    def mut(data):
        data = data or _template()
        svc = _service(data, service)
        _inc_count(svc, "day", "cache_hits", 1, now)
        return data, True

    try:
        store.mutate_kv(config.API_USAGE_KEY, mut)
    except Exception:
        pass


def _configured(service: str) -> bool:
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
        "google_books": bool(config.GOOGLE_BOOKS_API_KEY),
        "languagetool": bool(config.LANGUAGETOOL_API_URL),
        "spoonacular": bool(config.SPOONACULAR_API_KEY),
        "themealdb": bool(config.THEMEALDB_API_KEY),
        "telegram": bool(config.TELEGRAM_TOKEN),
        "tmdb": bool(config.TMDB_API_KEY),
        "ticketmaster": bool(config.TICKETMASTER_API_KEY),
        "zeroentropy": bool(config.ZEROENTROPY_API_KEY),
    }.get(service, False)


def _recent_rate_limit(svc: dict) -> bool:
    ts = int(svc.get("last_rate_limit_at") or 0)
    return bool(ts and ts >= int(time.time()) - 86400)


def _recent_temp_error(svc: dict) -> bool:
    ts = int(svc.get("last_error_at") or 0)
    if not ts or ts < int(time.time()) - 86400:
        return False
    reason = str(svc.get("last_error_reason") or "").lower()
    return any(x in reason for x in ("timeout", "network", "temporary", "503", "502", "504"))


def _quota_rows(service: str, svc: dict, dt=None):
    rows = []
    for quota in config.API_QUOTAS.get(service, []):
        if quota.get("enabled") is False:
            continue
        unit = quota.get("unit") or "requests"
        period = quota.get("period") or "day"
        used = _count(svc, period, unit, dt)
        rows.append({
            "mode": quota.get("mode") or "local",
            "unit": unit,
            "period": period,
            "limit": quota.get("limit"),
            "used": used,
            "warn": float(quota.get("warn_threshold", 0.8)),
            "critical": float(quota.get("critical_threshold", 0.95)),
        })
    return rows


def _status(svc: dict, quotas: list[dict]):
    if int(svc.get("cooldown_until") or 0) > int(time.time()):
        return "warn", "cooldown активен"
    if svc.get("last_ok") is False:
        return "bad", "Последний запрос завершился ошибкой"
    for q in quotas:
        limit = q.get("limit")
        if limit and q["used"] >= int(limit) * q.get("critical", 0.95):
            return "bad", "Почти исчерпан лимит"
    if _recent_rate_limit(svc):
        return "warn", "Было превышение лимита"
    if _recent_temp_error(svc):
        return "warn", "Была временная ошибка"
    for q in quotas:
        limit = q.get("limit")
        if limit and q["used"] >= int(limit) * q.get("warn", 0.8):
            return "warn", "Использование растёт"
    if not svc.get("last_request_at"):
        return "off", "Запросов сегодня не было"
    if int(time.time()) - int(svc.get("last_request_at") or 0) > 86400:
        return "stale", "Давно не было проверки"
    return "ok", "В норме"


def _is_used_today_or_configured_with_recent(svc: dict, dt=None) -> bool:
    if _count(svc, "day", "requests", dt) > 0:
        return True
    if _count(svc, "day", "messages", dt) > 0:
        return True
    if _count(svc, "day", "tokens", dt) > 0:
        return True
    last = int(svc.get("last_request_at") or 0)
    return bool(last and last >= int(_period_start("day", dt).timestamp()))


def snapshot():
    data = store._load(config.API_USAGE_KEY)
    services = data.get("services", {}) if isinstance(data, dict) else {}
    out = []
    for service in SERVICE_LABELS:
        svc = services.get(service) or {}
        if not _configured(service):
            continue
        if not _is_used_today_or_configured_with_recent(svc) and not svc.get("last_request_at"):
            continue
        quotas = _quota_rows(service, svc)
        status, status_text = _status(svc, quotas)
        out.append({
            "service": service,
            "label": SERVICE_LABELS[service],
            "icon": SERVICE_ICONS[service],
            "status": status,
            "status_text": status_text,
            "last_ok": svc.get("last_ok"),
            "quotas": quotas,
            "day_requests": _count(svc, "day", "requests"),
            "day_messages": _count(svc, "day", "messages"),
            "day_tokens": _count(svc, "day", "tokens"),
            "month_credits": _count(svc, "month", "credits"),
            "cache_hits": _count(svc, "day", "cache_hits"),
            "last_request_at": svc.get("last_request_at"),
            "last_success_at": svc.get("last_success_at"),
            "last_error_at": svc.get("last_error_at"),
            "last_error_reason": svc.get("last_error_reason") or "",
            "rate_limit_errors": int(svc.get("rate_limit_errors") or 0),
            "last_429_at": svc.get("last_429_at") or svc.get("last_rate_limit_at"),
            "cooldown_until": svc.get("cooldown_until"),
            "cooldown_scope": svc.get("cooldown_scope") or "",
            "last_fallback_at": svc.get("last_fallback_at"),
            "fallback_count": int(svc.get("fallback_count") or 0),
            "events": list(svc.get("events") or [])[-10:],
            "avg_latency_ms": int(svc.get("avg_latency_ms") or 0),
            "errors": list(svc.get("errors") or [])[-10:],
        })
    return {"updated_at": int(time.time()), "services": out}


_REMOTE_QUOTA_CACHE = {}   # service -> (ts, dict|None)
_REMOTE_QUOTA_TTL = 1800   # 30 минут - диагностический экран, свежесть не критична


def _cached_remote_quota(service: str, fetch_fn) -> dict | None:
    hit = _REMOTE_QUOTA_CACHE.get(service)
    if hit and time.time() - hit[0] < _REMOTE_QUOTA_TTL:
        return hit[1]
    try:
        data = fetch_fn()
    except Exception:
        data = None
    _REMOTE_QUOTA_CACHE[service] = (time.time(), data)
    return data


def firecrawl_credit_usage() -> dict | None:
    """Реальный остаток кредитов Firecrawl через /v2/team/credit-usage (кэш 30 мин)."""
    if not config.FIRECRAWL_API_KEY:
        return None

    def fetch():
        r = requests.get(
            "https://api.firecrawl.dev/v2/team/credit-usage",
            headers={"Authorization": f"Bearer {config.FIRECRAWL_API_KEY}"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        d = (r.json() or {}).get("data") or {}
        if d.get("remainingCredits") is None:
            return None
        return {
            "remaining": d.get("remainingCredits"),
            "limit": d.get("planCredits"),
            "reset_at": d.get("billingPeriodEnd"),
        }

    return _cached_remote_quota("firecrawl", fetch)


def openrouter_key_usage() -> dict | None:
    """Реальный остаток кредитов OpenRouter через /api/v1/key (кэш 30 мин)."""
    if not config.OPENROUTER_API_KEY:
        return None

    def fetch():
        r = requests.get(
            "https://openrouter.ai/api/v1/key",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        d = (r.json() or {}).get("data") or {}
        limit = d.get("limit")
        remaining = d.get("limit_remaining")
        used = d.get("usage")
        if remaining is None and limit is not None and used is not None:
            remaining = max(0, limit - used)
        if remaining is None:
            return None
        return {"remaining": remaining, "limit": limit}

    return _cached_remote_quota("openrouter", fetch)


def seconds_until_gemini_slot(limit: int = 4, window: int = 60) -> float:
    data = store._load(config.API_USAGE_KEY)
    svc = ((data.get("services") or {}).get("gemini") or {}) if isinstance(data, dict) else {}
    starts = []
    now = _now()
    for i in (0, 1):
        minute = now - timedelta(minutes=i)
        bucket_start = minute.replace(second=0, microsecond=0)
        used = _count(svc, "minute", "requests", bucket_start)
        starts.extend([bucket_start.timestamp()] * used)
    cutoff = time.time() - window
    starts = sorted(ts for ts in starts if ts >= cutoff)
    if len(starts) < limit:
        return 0
    return max(0, window - (time.time() - starts[0]))
