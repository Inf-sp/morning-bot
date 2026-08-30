import asyncio
import contextvars
import inspect
import logging
import re
import json
import time
import threading
import os
import requests
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import base64
from typing import Literal
import api_usage
import config
import provider_runtime
import store
import secure

_log = logging.getLogger(__name__)
_GEMINI_RATE_LOCK = threading.Lock()
_ACTIVE_DEADLINE = contextvars.ContextVar("ai_deadline", default=None)
STANDARD_BUDGET_SECONDS = 10.0
COMPLEX_BUDGET_SECONDS = 15.0
OPENROUTER_FALLBACK_RESERVE_SECONDS = 2.5
FREE_CHAT_BUDGET_SECONDS = 7.0
FREE_CHAT_MAX_TOKENS = 350
FREE_CHAT_ROUTE_VERSION = "free-chat-concise-v5"
FREE_CHAT_SCENARIO = "assistant/free_chat"
FREE_CHAT_TIER = "smart"
_FREE_CHAT_PROVIDER_TIMEOUTS = {
    "groq_standard": 2.5,
    "cf": 2.0,
    "openrouter": 2.5,
}
_MIN_USEFUL_PROVIDER_ATTEMPT_SECONDS = 1.0
_COMPLEX_MODULE_PREFIXES = (
    "assistant", "food", "cooking", "recipe", "wardrobe", "travel", "leisure", "learning",
)
_PUBLIC_AI_FALLBACK_MODULES = frozenset({
    "learning", "learning_game", "learning_trainer", "trainer",
    "learning_dictionary", "learning_dict_add", "learning_srs_migration", "dictionary_import",
})

GROQ_SIMPLE = "groq_simple"
GROQ_STANDARD = "groq_standard"
GROQ_COMPLEX = "groq_complex"
_ROUTE_PROVIDER_BASE = {
    GROQ_SIMPLE: "groq",
    GROQ_STANDARD: "groq",
    GROQ_COMPLEX: "groq",
}

# ---------- Cost logger ----------
_COST_MAX = 500  # максимум записей в rolling-буфере
_AI_TRAFFIC_MAX = 1000
_AI_TRAFFIC_TTL = 48 * 3600
OPENROUTER_FALLBACK_STATS_KEY = "openrouter_fallback_stats.json"
LOCAL_FALLBACK_TEXT = "Сейчас не удалось подготовить ответ. Попробуй ещё раз чуть позже."

PrivacyLevel = Literal["public", "personal", "sensitive"]
ResponseMode = Literal["plain_text", "json", "structured", "tool_call"]


@dataclass(frozen=True)
class FallbackPolicy:
    fallback_allowed: bool = False
    privacy_level: PrivacyLevel = "personal"
    response_mode: ResponseMode = "plain_text"
    allow_personal_openrouter: bool = False

    @property
    def openrouter_allowed(self) -> bool:
        if self.fallback_allowed is not True:
            return False
        if self.response_mode not in ("plain_text", "json"):
            return False
        if self.privacy_level == "public":
            return True
        if self.privacy_level == "personal" and self.allow_personal_openrouter:
            return True
        return False


class LLMProviderError(Exception):
    def __init__(self, provider: str, message: str, status_code: int | None = None,
                 temporary: bool = False, error_type: str = "provider_error",
                 retry_after: int | None = None, limit_scope: str = "",
                 cooldown_until: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.temporary = temporary
        self.error_type = error_type
        self.retry_after = retry_after
        self.limit_scope = limit_scope
        self.cooldown_until = cooldown_until


class _PartialStreamError(Exception):
    """Provider stream failed after it had already yielded visible text."""

    def __init__(self, error, partial):
        super().__init__(str(error))
        self.error = error
        self.partial = str(partial or "")


class StreamOutputInterrupted(Exception):
    """Safe user-facing outcome for an interrupted visible stream."""

    def __init__(self):
        super().__init__("⏳ Ответ оборвался. Отправь сообщение ещё раз.")


def _budget_for_module(module: str) -> float:
    module = str(module or "").casefold()
    if module.startswith(_COMPLEX_MODULE_PREFIXES):
        return COMPLEX_BUDGET_SECONDS
    return STANDARD_BUDGET_SECONDS


def _remaining_seconds() -> float | None:
    deadline = _ACTIVE_DEADLINE.get()
    if deadline is None:
        return None
    return max(0.0, float(deadline) - time.monotonic())


def _deadline_error() -> LLMProviderError:
    return LLMProviderError(
        "chain", "response deadline exceeded", temporary=True,
        error_type="deadline",
    )


def _bounded_timeout(timeout) -> float:
    remaining = _remaining_seconds()
    if remaining is None:
        return float(timeout)
    if remaining <= 0.2:
        raise _deadline_error()
    return max(0.2, min(float(timeout), remaining))


def _run_with_deadline(module, budget_seconds, call):
    if _ACTIVE_DEADLINE.get() is not None:
        remaining = _remaining_seconds()
        if remaining is not None and remaining <= 0.2:
            raise _deadline_error()
        return call()
    budget = float(budget_seconds or _budget_for_module(module))
    try:
        import tracking
        action_remaining = tracking.remaining_action_seconds()
        if action_remaining is not None:
            budget = min(budget, action_remaining)
    except Exception:
        pass
    if budget <= 0.2:
        raise _deadline_error()
    token = _ACTIVE_DEADLINE.set(time.monotonic() + budget)
    try:
        return call()
    finally:
        _ACTIVE_DEADLINE.reset(token)


def _run_provider_attempt(call, *, reserve_seconds=0.0):
    """Ограничивает одну попытку, сохраняя время следующим AI-резервам."""
    outer_deadline = _ACTIVE_DEADLINE.get()
    if outer_deadline is None or reserve_seconds <= 0:
        return call()
    now = time.monotonic()
    attempt_deadline = min(
        float(outer_deadline),
        max(now + 0.2, float(outer_deadline) - float(reserve_seconds)),
    )
    token = _ACTIVE_DEADLINE.set(attempt_deadline)
    try:
        return call()
    finally:
        _ACTIVE_DEADLINE.reset(token)


def _reserve_for_later_providers(order, index, policy):
    """Возвращает минимальное время, которое нельзя отдавать текущей модели."""
    later = tuple(order[index + 1:])
    regular_reserves = sum(
        1 for name in later if name != "openrouter"
    ) * _MIN_USEFUL_PROVIDER_ATTEMPT_SECONDS
    openrouter_reserve = (
        OPENROUTER_FALLBACK_RESERVE_SECONDS
        if policy.openrouter_allowed and "openrouter" in later and config.OPENROUTER_API_KEY
        else 0.0
    )
    return regular_reserves + openrouter_reserve


def _is_temporary_status(status_code):
    return status_code in (429, 502, 503, 504)


def _is_temporary_exception(exc):
    if isinstance(exc, LLMProviderError):
        return exc.temporary
    return isinstance(exc, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ReadTimeout,
        requests.exceptions.ConnectionError,
    ))

_TIMEOUT_CAPS = {
    "gemini": 6.0,
    "groq": 5.0,
    "cf": 4.0,
}


def _timeout_cap(name: str):
    return _TIMEOUT_CAPS.get(name)


def _log_cost(provider: str, model: str, prompt: str, result: str, module: str = "", ms: int = 0, ok: bool = True):
    """Добавить запись о LLM-вызове в rolling-буфер (хранится в store).

    ms  — latency вызова в миллисекундах (для «ср. ответ» в админке);
    ok  — успешность (для «ошибок сегодня»)."""
    try:
        tokens = (len(prompt) + len(result or "")) // 4
        entry = {
            "ts": int(time.time()),
            "provider": provider,
            "model": model or "",
            "tokens": tokens,
            "module": module or "",
            "ms": int(ms),
            "ok": bool(ok),
        }
        buf = store._load(config.COST_LOG_KEY).get("log", [])
        buf.append(entry)
        store._save(config.COST_LOG_KEY, {"log": buf[-_COST_MAX:]})
    except Exception:
        pass  # логирование не должно ломать основной поток


def _record_ai_attempt(provider: str, model: str, module: str, *, ok: bool,
                       latency_ms: int = 0, failure: str = "", cache_hit: bool = False) -> None:
    """Короткий технический след AI-попытки без текста запроса или ответа."""
    try:
        import tracking

        trace = tracking.current_action()
        origin = "Пользователь" if trace is not None else "Фон"
        actor = str(getattr(trace, "cid", "") or "") if trace is not None else ""
        section = str(getattr(trace, "section", "") or "")
        if not section:
            section = tracking._section_for(f"{module}.py")
        entry = {
            "ts": int(time.time()),
            "provider": str(provider or "")[:40],
            "model": str(model or "")[:80],
            "module": str(module or "")[:60],
            "origin": origin,
            "actor": actor,
            "section": str(section or "Система")[:40],
            "action": str(getattr(trace, "action", "") or "")[:100],
            "ok": bool(ok),
            "cache_hit": bool(cache_hit),
            "latency_ms": max(0, int(latency_ms or 0)),
            "failure": str(failure or "")[:120],
        }
        cutoff = entry["ts"] - _AI_TRAFFIC_TTL
        data = store._load(config.AI_TRAFFIC_LOG_KEY) or {}
        rows = [row for row in data.get("log", []) if int(row.get("ts") or 0) >= cutoff]
        rows.append(entry)
        store._save(config.AI_TRAFFIC_LOG_KEY, {"log": rows[-_AI_TRAFFIC_MAX:]})
    except Exception:
        pass


def ai_traffic_summary(period_seconds=24 * 3600, limit=5) -> dict:
    """Сводка попыток для админки: кто и какой раздел создаёт нагрузку."""
    try:
        cutoff = time.time() - max(60, int(period_seconds or 0))
        rows = [
            row for row in (store._load(config.AI_TRAFFIC_LOG_KEY) or {}).get("log", [])
            if int(row.get("ts") or 0) >= cutoff
        ]
    except Exception:
        rows = []
    grouped = {}
    providers = {}
    peaks = {}
    for row in rows:
        key = (str(row.get("origin") or "Фон"), str(row.get("section") or "Система"),
               str(row.get("actor") or ""))
        item = grouped.setdefault(key, {
            "origin": key[0], "section": key[1], "actor": key[2],
            "attempts": 0, "failed": 0, "cache_hits": 0,
        })
        item["attempts"] += 1
        item["failed"] += 0 if row.get("ok") else 1
        item["cache_hits"] += 1 if row.get("cache_hit") else 0
        provider = str(row.get("provider") or "")
        if provider and provider != "cache":
            providers[provider] = providers.get(provider, 0) + 1
        bucket = int(row.get("ts") or 0) // 300 * 300
        peak = peaks.setdefault(bucket, {"ts": bucket, "attempts": 0, "failed": 0})
        peak["attempts"] += 1
        peak["failed"] += 0 if row.get("ok") else 1
    sources = sorted(grouped.values(), key=lambda item: (-item["attempts"], -item["failed"]))
    peak = max(peaks.values(), key=lambda item: (item["attempts"], item["failed"]), default=None)
    return {
        "total": len(rows),
        "failed": sum(1 for row in rows if not row.get("ok")),
        "cache_hits": sum(1 for row in rows if row.get("cache_hit")),
        "providers": dict(sorted(providers.items(), key=lambda item: -item[1])),
        "sources": sources[:max(1, int(limit or 1))],
        "peak": peak,
    }


def _log_openrouter_fallback(origin_provider: str, reason: str, ok: bool,
                             status_code: int | None = None, latency_ms: int = 0,
                             fallback_used: bool = True):
    """Telemetry без prompt/response/API key."""
    try:
        entry = {
            "ts": int(time.time()),
            "provider": "openrouter",
            "model": config.OPENROUTER_MODEL,
            "origin_provider": origin_provider or "",
            "reason": reason or "",
            "status_code": status_code,
            "latency_ms": int(latency_ms or 0),
            "fallback_used": bool(fallback_used),
            "ok": bool(ok),
        }
        data = store._load(OPENROUTER_FALLBACK_STATS_KEY)
        log = data.get("log", [])
        log.append(entry)
        data["log"] = log[-_COST_MAX:]
        store._save(OPENROUTER_FALLBACK_STATS_KEY, data)
    except Exception:
        pass


def get_openrouter_fallback_stats(period_days=1) -> dict:
    try:
        cutoff = time.time() - period_days * 86400
        rows = [e for e in store._load(OPENROUTER_FALLBACK_STATS_KEY).get("log", [])
                if e.get("ts", 0) >= cutoff]
    except Exception:
        rows = []
    return {
        "attempts": len(rows),
        "success": sum(1 for e in rows if e.get("ok")),
        "errors": sum(1 for e in rows if not e.get("ok")),
    }


def get_cost_log() -> list:
    """Вернуть список всех сохранённых записей расходов."""
    try:
        return store._load(config.COST_LOG_KEY).get("log", [])
    except Exception:
        return []


_AI_CACHE_MAX = 300
_AI_CACHE_TTLS = {
    "food": 24 * 3600,
    "leisure": 18 * 3600,
    "travel": 18 * 3600,
    "wardrobe": 18 * 3600,
    "learning_explain": 14 * 86400,
    "learning_dict_add": 30 * 86400,
    "deploy": 10 * 365 * 86400,
}


def _cache_ttl(module: str, response_mode: ResponseMode) -> int:
    module = module or ""
    if module == "learning":
        return 0
    if module in _AI_CACHE_TTLS:
        return _AI_CACHE_TTLS[module]
    return 0


def _normalise_cache_context(value):
    """Make structured cache input stable without storing its raw values."""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        return {
            str(key): _normalise_cache_context(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalise_cache_context(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_normalise_cache_context(item) for item in value),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return re.sub(r"\s+", " ", str(value)).strip()


def _cache_key(provider_order, prompt, max_tokens, temperature, module, response_mode,
               cache_context=None):
    """Cache a semantic answer, independently from the current reserve chain.

    Personal recommendation flows pass a structured context.  It deliberately
    replaces the rendered prompt, so copy edits and provider order do not create
    a second expensive request for the same scenario.
    """
    raw = json.dumps({
        "context": _normalise_cache_context(cache_context) if cache_context is not None else None,
        "prompt": "" if cache_context is not None else re.sub(r"\s+", " ", str(prompt or "")).strip(),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "module": module or "",
        "mode": response_mode,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key: str, ttl: int):
    if ttl <= 0:
        return None
    try:
        data = store._load(config.AI_RESPONSE_CACHE_KEY)
        entry = (data.get("items") or {}).get(key)
        if not entry:
            return None
        if time.time() - int(entry.get("ts") or 0) > ttl:
            return None
        api_usage.record_cache_hit("gemini")
        return entry.get("value")
    except Exception:
        return None


def _is_cacheable_response(out: str, response_mode: str) -> bool:
    """Не кэшируем ответ в json-режиме, если он не парсится как JSON — иначе
    один невалидный ответ модели (например, лишняя кавычка внутри строки)
    навсегда застревает в кэше на TTL модуля (до 30 дней), и повторные попытки
    пользователя получают тот же сломанный ответ вместо новой генерации."""
    if response_mode != "json":
        return True
    try:
        _parse_json_response(out)
        return True
    except ValueError:
        return False


def _cache_set(key: str, value):
    if value is None:
        return
    try:
        def change(data):
            items = data.setdefault("items", {})
            items[key] = {"ts": int(time.time()), "value": value}
            if len(items) > _AI_CACHE_MAX:
                oldest = sorted(
                    items.items(), key=lambda kv: int((kv[1] or {}).get("ts") or 0)
                )
                for old_key, _entry in oldest[:len(items) - _AI_CACHE_MAX]:
                    items.pop(old_key, None)
            return data, None

        store.mutate_kv(config.AI_RESPONSE_CACHE_KEY, change)
    except Exception:
        pass


def _cache_delete(key: str):
    try:
        def change(data):
            items = data.get("items") or {}
            items.pop(key, None)
            return data, None

        store.mutate_kv(config.AI_RESPONSE_CACHE_KEY, change)
    except Exception:
        pass


def _parse_retry_seconds(headers=None, body="") -> int | None:
    try:
        val = int((headers or {}).get("Retry-After") or 0)
        if val > 0:
            return val
    except Exception:
        pass
    text = body or ""
    try:
        data = json.loads(text)
        for detail in ((data.get("error") or {}).get("details") or []):
            delay = detail.get("retryDelay") or detail.get("retry_delay")
            if isinstance(delay, str):
                m = re.match(r"(\d+)s$", delay.strip())
                if m:
                    return int(m.group(1))
    except Exception:
        pass
    m = re.search(r"retry(?: after|Delay)?[^\d]{0,20}(\d+)\s*s", text, re.I)
    return int(m.group(1)) if m else None


def _provider_model_name(provider: str) -> str:
    provider = (provider or "").strip()
    if provider == GROQ_SIMPLE:
        return config.GROQ_SIMPLE_MODEL
    if provider == GROQ_STANDARD:
        return config.GROQ_STANDARD_MODEL
    if provider == GROQ_COMPLEX:
        return config.GROQ_COMPLEX_MODEL
    if provider == "gemini":
        return config.GEMINI_MODEL
    if provider == "groq":
        return config.GROQ_STANDARD_MODEL
    if provider == "cf":
        return config.CF_MODEL
    return ""


def _json_preview(raw: str, limit: int = 320) -> str:
    text = secure.redact(str(raw or "")).strip()
    return text[:limit]


def _extract_json_text(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return text
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
    if fence:
        return fence.group(1).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    return text


def _next_local_day_seconds() -> int:
    now = datetime.now(config.TZ)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return max(3600, int((tomorrow - now).total_seconds()))


def _classify_gemini_limit(body="", headers=None) -> tuple[str, int | None, int]:
    text = (body or "")
    low = text.lower()
    retry_after = _parse_retry_seconds(headers, text)
    compact = re.sub(r"[^a-z0-9]+", "", low)
    if any(x in compact for x in ("requestsperday", "perday", "rpd", "daily")):
        return "RPD", retry_after, _next_local_day_seconds()
    if any(x in compact for x in ("tokensperminute", "tpm")):
        return "TPM", retry_after, max(60, min(int(retry_after or 60), 300))
    if any(x in compact for x in ("requestsperminute", "perminute", "rpm")):
        return "RPM", retry_after, max(60, min(int(retry_after or 60), 300))
    if "resource_exhausted" in low or "too many requests" in low or "quota" in low:
        return "limit", retry_after, max(60, min(int(retry_after or 60), 300))
    return "", retry_after, max(60, min(int(retry_after or 60), 300))


def _gemini_cooldown_error():
    state = api_usage.gemini_state(1)
    if not state.get("cooldown_active"):
        return None
    retry_after = int(state.get("cooldown_seconds") or 0)
    scope = state.get("cooldown_scope") or "limit"
    return LLMProviderError(
        "gemini",
        f"gemini cooldown {scope}: retry after {retry_after}s",
        status_code=429,
        temporary=True,
        error_type="rate_limit",
        retry_after=retry_after,
        limit_scope=scope,
        cooldown_until=int(state.get("cooldown_until") or 0),
    )


def get_gemini_rate_limit_stats(period_days=1) -> dict:
    return api_usage.gemini_state(period_days)


def _cooldown_phrase(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 90:
        return f"{max(1, seconds)} сек"
    minutes = max(1, round(seconds / 60))
    return f"{minutes} мин"


def _log_gemini_limit(kind: str, err: Exception | None = None, fallback: bool = False):
    try:
        import tracking
        state = api_usage.gemini_state(1)
        scope = (getattr(err, "limit_scope", "") or state.get("cooldown_scope") or "").upper()
        cooldown_until = int(getattr(err, "cooldown_until", None) or state.get("cooldown_until") or 0)
        if scope == "RPD":
            seconds = state.get("cooldown_seconds") or max(
                0, cooldown_until - int(time.time()),
            )
        else:
            seconds = getattr(err, "retry_after", None) or state.get("cooldown_seconds") or 0
        dedup_token = f"{kind or 'gemini_rate_limit'}:{scope or 'limit'}:{cooldown_until}:{bool(fallback)}"
        if not api_usage.should_log_gemini_limit(dedup_token):
            return
        first = f"Gemini · лимит {scope}".strip()
        second = "Fallback включён" if fallback else "Fallback будет использован"
        if seconds:
            second += f" · повтор после {_cooldown_phrase(int(seconds))}"
        else:
            second += " · повтор после cooldown"
        action_trace = tracking.current_action()
        section = action_trace.section if action_trace and action_trace.section else "Система"
        tracking.log_error(
            "llm", f"{first}\n{second}", kind=kind or "gemini_rate_limit",
            section=section, action="сработал лимит провайдера",
            service="Gemini", fallback="автоматический резерв" if fallback else "",
        )
    except Exception:
        pass

def _is_json_validation_error(status_code, body="") -> bool:
    if int(status_code or 0) != 400:
        return False
    text = str(body or "").casefold()
    return "failed to validate json" in text or "failed_generation" in text


def _post(url, headers, payload, timeout, name, timeout_cap=None, usage_service=None,
          suppress_json_validation_failure=False):
    service_aliases = {"cf": "cloudflare", **_ROUTE_PROVIDER_BASE}
    service = service_aliases.get(name, name)
    meter_service = usage_service or service
    gemini_request = service == "gemini"
    if timeout_cap is None:
        timeout_cap = _timeout_cap(name)
    if timeout_cap is not None:
        timeout = min(float(timeout), float(timeout_cap))
    t0 = time.time()
    timeout = _bounded_timeout(timeout)

    def record_usage(ok, *, monitor_result=True, **kwargs):
        api_usage.record_request(
            meter_service, ok=ok, monitor_result=monitor_result, **kwargs,
        )
        if meter_service != service and monitor_result:
            try:
                provider_runtime.record_result(service, ok, **kwargs)
            except Exception:
                pass

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.Timeout as e:
        record_usage(False, error="timeout")
        raise LLMProviderError(name, f"{name} timeout", temporary=True, error_type=type(e).__name__) from e
    except requests.exceptions.ConnectionError as e:
        record_usage(False, error="network_error")
        raise LLMProviderError(name, f"{name} network error", temporary=True, error_type=type(e).__name__) from e
    finally:
        if gemini_request:
            api_usage.gemini_requests(consume=True)
    if r.status_code != 200:
        # тело ошибки в логи (видно причину), но без секретов
        body = secure.redact((r.text or "")[:300])
        temporary = _is_temporary_status(r.status_code)
        limit_scope = ""
        cooldown_until = None
        json_validation_failure = (
            suppress_json_validation_failure
            and _is_json_validation_error(r.status_code, body)
        )
        record_usage(
            False, status_code=r.status_code, error=f"HTTP {r.status_code}",
            latency_ms=int((time.time() - t0) * 1000), headers=r.headers,
            monitor_result=not json_validation_failure,
        )
        retry_after = None
        try:
            retry_after = int(r.headers.get("Retry-After") or 0) or None
        except Exception:
            retry_after = None
        if service == "gemini" and (r.status_code == 429 or "RESOURCE_EXHAUSTED" in (r.text or "")):
            limit_scope, parsed_retry, cooldown_seconds = _classify_gemini_limit(r.text or "", r.headers)
            limit_scope = limit_scope or "limit"
            retry_after = retry_after or parsed_retry
            cooldown_until = int(time.time()) + int(cooldown_seconds)
            api_usage.set_gemini_rate_limit(
                limit_scope=limit_scope,
                retry_after=retry_after,
                cooldown_until=cooldown_until,
                message=body,
            )
        raise LLMProviderError(name, f"{name} {r.status_code}: {body}",
                               status_code=r.status_code, temporary=temporary,
                               error_type="rate_limit" if limit_scope else "http_error",
                               retry_after=retry_after, limit_scope=limit_scope,
                               cooldown_until=cooldown_until)
    if service != "gemini":
        record_usage(True, latency_ms=int((time.time() - t0) * 1000), headers=r.headers)
    return r


def _stream_post(url, headers, payload, timeout, name, timeout_cap=None, usage_service=None):
    """Open one SSE request and account for it exactly once when it finishes.

    ``_post`` records a successful HTTP 200 before the body is read, which is
    correct for JSON responses but wrong for a stream that can later break.
    Free-chat streaming therefore has its own small transport helper.
    """
    service_aliases = {"cf": "cloudflare", **_ROUTE_PROVIDER_BASE}
    service = service_aliases.get(name, name)
    meter_service = usage_service or service
    if timeout_cap is None:
        timeout_cap = _timeout_cap(name)
    if timeout_cap is not None:
        timeout = min(float(timeout), float(timeout_cap))
    timeout = _bounded_timeout(timeout)
    started = time.time()
    accounted = False

    def record(ok, *, error="", status_code=None, headers_=None):
        nonlocal accounted
        if accounted:
            return
        accounted = True
        details = {
            "latency_ms": int((time.time() - started) * 1000),
            "headers": headers_ or {},
        }
        if not ok:
            details["error"] = error or "stream_error"
            if status_code is not None:
                details["status_code"] = status_code
        api_usage.record_request(meter_service, ok=ok, **details)
        if meter_service != service:
            try:
                provider_runtime.record_result(service, ok, **details)
            except Exception:
                pass

    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=timeout, stream=True,
        )
    except requests.exceptions.Timeout as error:
        record(False, error="timeout")
        raise LLMProviderError(
            name, f"{name} timeout", temporary=True,
            error_type=type(error).__name__,
        ) from error
    except requests.exceptions.ConnectionError as error:
        record(False, error="network_error")
        raise LLMProviderError(
            name, f"{name} network error", temporary=True,
            error_type=type(error).__name__,
        ) from error

    if response.status_code == 200:
        return response, record

    body = secure.redact((response.text or "")[:300])
    status_code = response.status_code
    record(
        False,
        error=f"HTTP {status_code}",
        status_code=status_code,
        headers_=response.headers,
    )
    try:
        response.close()
    except Exception:
        pass
    retry_after = None
    try:
        retry_after = int(response.headers.get("Retry-After") or 0) or None
    except Exception:
        pass
    raise LLMProviderError(
        name, f"{name} {status_code}: {body}", status_code=status_code,
        temporary=_is_temporary_status(status_code), error_type="http_error",
        retry_after=retry_after,
    )


def _stream_content(value):
    """Normalise OpenAI-compatible delta content without accepting tool calls."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                chunks.append(str(item.get("text") or item.get("content") or ""))
        return "".join(chunks)
    return ""


def _iter_sse_deltas(response, provider):
    """Yield content deltas and reject incomplete/error SSE responses."""
    saw_done = False
    saw_finish = False
    try:
        # Requests defaults a ``text/event-stream`` response without a charset
        # to ISO-8859-1.  SSE payloads are UTF-8, so letting Requests decode
        # them corrupts non-ASCII assistant text before JSON sees it.
        for raw_line in response.iter_lines(decode_unicode=False):
            remaining = _remaining_seconds()
            if remaining is not None and remaining <= 0.2:
                raise _deadline_error()
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="replace").strip()
            else:
                line = str(raw_line or "").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                saw_done = True
                break
            try:
                event = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise LLMProviderError(
                    provider, "malformed stream event", temporary=True,
                    error_type="stream_protocol",
                ) from error
            error_payload = event.get("error") if isinstance(event, dict) else None
            if error_payload:
                if isinstance(error_payload, dict):
                    detail = error_payload.get("message") or error_payload.get("code") or "stream error"
                else:
                    detail = str(error_payload)
                raise LLMProviderError(
                    provider, str(detail), temporary=True, error_type="stream_error",
                )
            choices = event.get("choices") if isinstance(event, dict) else None
            for choice in choices or []:
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta") or {}
                content = _stream_content(delta.get("content") if isinstance(delta, dict) else "")
                if content:
                    yield content
                if choice.get("finish_reason"):
                    saw_finish = True
        if not (saw_done or saw_finish):
            raise LLMProviderError(
                provider, "stream ended before completion", temporary=True,
                error_type="stream_incomplete",
            )
    except requests.exceptions.Timeout as error:
        raise LLMProviderError(
            provider, f"{provider} timeout", temporary=True,
            error_type=type(error).__name__,
        ) from error
    except requests.exceptions.ConnectionError as error:
        raise LLMProviderError(
            provider, f"{provider} network error", temporary=True,
            error_type=type(error).__name__,
        ) from error


def _stream_openai_chat(url, headers, payload, timeout, provider, emit, *, usage_service=None):
    """Read an OpenAI-compatible SSE completion and return its complete text."""
    response, record = _stream_post(
        url, headers, payload, timeout, provider, timeout_cap=timeout,
        usage_service=usage_service,
    )
    pieces = []
    try:
        for delta in _iter_sse_deltas(response, provider):
            pieces.append(delta)
            emit(delta)
        output = "".join(pieces).strip()
        if not output:
            raise LLMProviderError(provider, "empty stream response", error_type="empty_response")
        record(True, headers_=response.headers)
        return output
    except Exception as error:
        record(False, error=type(error).__name__, headers_=response.headers)
        if pieces:
            raise _PartialStreamError(error, "".join(pieces)) from error
        raise
    finally:
        try:
            response.close()
        except Exception:
            pass

def _as_text(x):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        for k in ("response", "text", "content", "output"):
            v = x.get(k)
            if isinstance(v, str):
                return v
    return None

# ---------- одиночная генерация ----------
def _gen_gemini(prompt, max_tokens, temperature, response_mode: ResponseMode = "plain_text",
                model=None, provider="gemini"):
    cooling = _gemini_cooldown_error()
    if cooling is not None:
        raise cooling
    generation_config = {
        "maxOutputTokens": max_tokens,
        "temperature": temperature,
        "thinkingConfig": {"thinkingBudget": 0},
    }
    if response_mode == "json":
        generation_config["responseMimeType"] = "application/json"
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": generation_config}
    with _GEMINI_RATE_LOCK:
        wait = api_usage.seconds_until_gemini_slot(limit=4, window=60)
        if wait > 0:
            remaining = _remaining_seconds()
            if remaining is not None and wait >= max(0.0, remaining - 0.2):
                raise _deadline_error()
            time.sleep(wait)
        t0 = time.time()
        r = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model or _provider_model_name(provider)}:generateContent?key={config.GEMINI_API_KEY}",
            {}, payload, 30, provider, timeout_cap=5)
    data = r.json()
    usage = data.get("usageMetadata") or data.get("usage_metadata") or {}
    input_tokens = int(usage.get("promptTokenCount") or usage.get("prompt_token_count") or 0)
    output_tokens = int(usage.get("candidatesTokenCount") or usage.get("candidates_token_count") or 0)
    api_usage.record_request(
        "gemini",
        ok=True,
        units={
            "tokens": input_tokens + output_tokens,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        latency_ms=int((time.time() - t0) * 1000),
        headers=r.headers,
    )
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _gemini_image_json(image_bytes, mime_type, prompt, max_tokens=1000):
    """Один приватный vision-запрос в Gemini для распознавания изображения.

    Изображение не попадает в кэш, логи или fallback-провайдеры.
    """
    if not config.GEMINI_API_KEY:
        raise LLMProviderError("gemini", "no gemini key", error_type="credentials")
    if not _reserve_gemini_for_action():
        raise LLMProviderError(
            "gemini", "gemini action budget exhausted", error_type="action_budget",
        )
    payload = {
        "contents": [{"parts": [
            {"inlineData": {
                "mimeType": mime_type or "image/jpeg",
                "data": base64.b64encode(bytes(image_bytes)).decode("ascii"),
            }},
            {"text": prompt},
        ]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    r = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}",
        {}, payload, 40, "gemini", timeout_cap=40,
    )
    data = r.json()
    usage = data.get("usageMetadata") or {}
    input_tokens = int(usage.get("promptTokenCount") or 0)
    output_tokens = int(usage.get("candidatesTokenCount") or 0)
    api_usage.record_request(
        "gemini", ok=True,
        units={"tokens": input_tokens + output_tokens,
               "input_tokens": input_tokens, "output_tokens": output_tokens},
        headers=r.headers,
    )
    raw = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_json_response(raw)


async def allm_image_json(image_bytes, mime_type, prompt, max_tokens=1000):
    return await asyncio.to_thread(
        lambda: _run_with_deadline(
            "wardrobe",
            COMPLEX_BUDGET_SECONDS,
            lambda: _gemini_image_json(image_bytes, mime_type, prompt, max_tokens),
        )
    )

def _looks_bad_fallback_text(text: str, response_mode: ResponseMode = "plain_text") -> bool:
    s = (text or "").strip()
    if len(s) < 2:
        return True
    low = s.lower()
    if response_mode != "json" and low.startswith(("{", "[", "```")):
        return True
    if "|---" in s or re.search(r"^\s*\|.+\|\s*$", s, re.M):
        return True
    if any(x in low for x in ("as an ai language model", "system prompt", "developer message", "api key")):
        return True
    return False


def _openrouter_plain_text_fallback(prompt, max_tokens, temperature, origin_provider, reason,
                                    response_mode: ResponseMode = "plain_text", _retry=False):
    if not config.OPENROUTER_API_KEY:
        return None
    token_cap = 5000 if response_mode == "json" else 700
    try:
        timeout = _bounded_timeout(30 if response_mode == "json" else 12)
    except LLMProviderError:
        return None
    t0 = time.time()
    status_code = None
    try:
        payload = {
            "model": config.OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": min(int(max_tokens or 400), token_cap),
            "temperature": min(float(temperature or 0.7), 0.8),
        }
        if response_mode == "json":
            payload["response_format"] = {"type": "json_object"}
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        status_code = r.status_code
        if r.status_code != 200:
            api_usage.record_request(
                "openrouter", ok=False, status_code=r.status_code,
                error=f"HTTP {r.status_code}", headers=r.headers,
            )
            if not _retry and (r.status_code == 429 or r.status_code >= 500):
                time.sleep(0.3)
                return _openrouter_plain_text_fallback(
                    prompt, max_tokens, temperature, origin_provider, reason,
                    response_mode=response_mode, _retry=True,
                )
            _log_openrouter_fallback(origin_provider, reason, False, status_code,
                                     int((time.time() - t0) * 1000))
            return None
        text = _as_text(r.json()["choices"][0]["message"]["content"])
        if not text or _looks_bad_fallback_text(text, response_mode=response_mode):
            api_usage.record_request("openrouter", ok=False, error="invalid response")
            if not _retry:
                time.sleep(0.3)
                return _openrouter_plain_text_fallback(
                    prompt, max_tokens, temperature, origin_provider, reason,
                    response_mode=response_mode, _retry=True,
                )
            _log_openrouter_fallback(origin_provider, "bad_output", False, status_code,
                                     int((time.time() - t0) * 1000))
            return None
        api_usage.record_request("openrouter", ok=True, headers=r.headers)
        _log_openrouter_fallback(origin_provider, reason, True, status_code,
                                 int((time.time() - t0) * 1000))
        return text.strip()
    except Exception as e:
        err_type = type(e).__name__
        api_usage.record_request("openrouter", ok=False, error=err_type)
        if not _retry and isinstance(e, (
            requests.exceptions.Timeout, requests.exceptions.ConnectionError,
        )):
            time.sleep(0.3)
            return _openrouter_plain_text_fallback(
                prompt, max_tokens, temperature, origin_provider, reason,
                response_mode=response_mode, _retry=True,
            )
        _log_openrouter_fallback(origin_provider, err_type, False, status_code,
                                 int((time.time() - t0) * 1000))
        return None

def _gen_groq(prompt, max_tokens, temperature, response_mode: ResponseMode = "plain_text",
              model=None, provider="groq"):
    if not config.GROQ_API_KEY:
        raise Exception("no groq")
    payload = {
        "model": model or _provider_model_name(provider),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_mode == "json":
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"}
    usage_service = api_usage.groq_model_service(model or _provider_model_name(provider))
    try:
        r = _post(
            "https://api.groq.com/openai/v1/chat/completions", headers, payload,
            40, provider, timeout_cap=5, usage_service=usage_service,
            suppress_json_validation_failure=response_mode == "json",
        )
    except LLMProviderError as exc:
        if response_mode != "json" or not _is_json_validation_error(exc.status_code, str(exc)):
            raise
        # Некоторые модели Groq иногда отклоняют собственный JSON-режим. Prompt
        # уже требует валидный JSON, а локальный парсер проверит ответ, поэтому
        # повторяем тот же запрос без server-side ограничения.
        retry_payload = dict(payload)
        retry_payload.pop("response_format", None)
        r = _post(
            "https://api.groq.com/openai/v1/chat/completions", headers, retry_payload,
            40, provider, timeout_cap=5, usage_service=usage_service,
        )
    return r.json()["choices"][0]["message"]["content"]

def _gen_cf(prompt, max_tokens):
    if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
        raise Exception("no cf")
    r = _post(f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run/{config.CF_MODEL}",
        {"Authorization": f"Bearer {config.CF_API_TOKEN}", "Content-Type": "application/json"},
        {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
        40, "cf", timeout_cap=4)
    output = _as_text(r.json().get("result", {}).get("response"))
    api_usage.record_request(
        "cloudflare", units={"neurons": api_usage.estimate_cloudflare_neurons(prompt, output)},
        include_request=False,
    )
    return output

# ---------- circuit breaker для временных сбоев ----------
_RATE_LIMIT_COOLDOWN_SEC = 300
_OUTAGE_COOLDOWN_SEC = 90
_cooldowns = {}  # provider -> ts до которого он считается недоступным

def _mark_cooldown(name, err):
    """Временно убирает нестабильного провайдера из начала цепочки.

    429 требует более длинной паузы, а 5xx/timeout/network — короткой. При этом
    провайдер не исключается навсегда: после паузы он автоматически проверяется
    следующим обычным запросом.
    """
    if (not _is_temporary_exception(err)
            or getattr(err, "error_type", "") == "deadline"):
        return
    status = getattr(err, "status_code", None)
    seconds = _RATE_LIMIT_COOLDOWN_SEC if status == 429 else _OUTAGE_COOLDOWN_SEC
    retry_after = getattr(err, "retry_after", None)
    if retry_after:
        seconds = max(seconds, min(int(retry_after), 3600))
    key = _ROUTE_PROVIDER_BASE.get(name, name)
    _cooldowns[key] = max(_cooldowns.get(key, 0), time.time() + seconds)

def _is_cooling(name):
    return _cooldowns.get(_ROUTE_PROVIDER_BASE.get(name, name), 0) > time.time()

def _reorder_for_cooldown(order):
    """Провайдеров на cooldown (недавний временный сбой) отодвигаем в конец, чтобы не терять
    время на заведомо неудачный запрос перед рабочим fallback-ом."""
    if not any(_is_cooling(n) for n in order):
        return order
    return tuple(sorted(order, key=lambda n: _is_cooling(n)))


def _monitor_name(provider):
    provider = _ROUTE_PROVIDER_BASE.get(provider, provider)
    return "cloudflare" if provider == "cf" else provider


def _provider_name(service):
    return "cf" if service == "cloudflare" else service


def _route_name_for_provider(order, provider):
    for name in order:
        if _monitor_name(name) == provider:
            return name
    return provider


def _reorder_for_monitor(order):
    """Put a genuinely selected reserve first; keep the primary in the chain so
    a later successful call can automatically restore it."""
    result = list(order)
    if not result:
        return tuple(result)
    selected = _route_name_for_provider(
        result, _provider_name(provider_runtime.selected_provider(_monitor_name(result[0])))
    )
    if selected in result and selected != result[0]:
        result.remove(selected)
        result.insert(0, selected)
    return tuple(result)


def _provider_is_unavailable(name):
    if _monitor_name(name) == "gemini":
        rate_limit = _gemini_cooldown_error()
        if rate_limit is not None:
            return rate_limit
    if _is_cooling(name):
        return LLMProviderError(name, f"{name} cooldown", temporary=True, error_type="cooldown")
    return None

def _friendly(errs):
    joined = "; ".join(errs)
    _log.warning("LLM chain failed: %s", secure.redact(joined))
    if "deadline" in joined.lower():
        return "⏳ Не успел подготовить ответ вовремя. Попробуй ещё раз."
    if "429" in joined or "Too Many Requests" in joined or "rate" in joined.lower():
        return "⏳ ИИ временно перегружен — подожди минуту и попробуй снова."
    return "⚠️ ИИ временно недоступен — попробуй снова через пару минут."


def _reserve_gemini_for_action() -> bool:
    """Gemini may produce at most one response for one user action."""
    try:
        import tracking
        return tracking.consume_provider_budget("gemini", limit=1)
    except Exception:
        return True

# Три понятных маршрута: простой, обычный и сложный. OpenRouter вызывается
# только последним резервом через общую политику fallback.
SIMPLE_ORDER = (GROQ_SIMPLE, "cf", "openrouter")
STANDARD_ORDER = (GROQ_STANDARD, "cf", "openrouter")
COMPLEX_ORDER = ("gemini", GROQ_COMPLEX, "openrouter")
UTILITY_ORDER = SIMPLE_ORDER
DEFAULT_ORDER = STANDARD_ORDER
CHAT_ORDER = STANDARD_ORDER
GRAMMAR_ORDER = STANDARD_ORDER
LEISURE_ORDER = COMPLEX_ORDER
FOOD_ORDER = COMPLEX_ORDER

# Явные пресеты: позволяют приоритизировать конкретный провайдер, не меняя код вызова по всему проекту.
PROVIDER_ORDER = {
    "cf": ("cf", "openrouter"),
    "groq": STANDARD_ORDER,
    "gemini": COMPLEX_ORDER,
}

# --- тиры: маршрутизация по задаче ---
# cheap  → utility-маршрут для грамматики, переводов и строгого JSON
# smart  → utility-маршрут по умолчанию
# leisure → premium-маршрут для финальных рекомендаций
TIERS = {
    "cheap":   (GRAMMAR_ORDER, None),
    "smart":   (DEFAULT_ORDER, None),
    "leisure": (LEISURE_ORDER, None),
}

# --- единый AI-router: политика провайдеров по разделу бота (module) ---
# Переопределяет tier/route для известных разделов, чтобы порядок провайдеров и
# запрет конкретного provider не зависели от того, что явно передал вызов внутри
# раздела. Единственный способ обойти policy — явный order=(...) в вызове.
MODULE_POLICY = {
    # Быстрый utility-маршрут: языки, строгий JSON, классификация и короткий анализ.
    "wardrobe_utility": SIMPLE_ORDER,
    "learning": GRAMMAR_ORDER,
    "learning_dict_add": GRAMMAR_ORDER,
    "learning_trainer": GRAMMAR_ORDER,
    "learning_srs_migration": GRAMMAR_ORDER,
    "learning_game": GRAMMAR_ORDER,
    "learning_dictionary": GRAMMAR_ORDER,
    "dictionary_import": GRAMMAR_ORDER,
    "trainer": GRAMMAR_ORDER,
    # Сложные карточки, рекомендации и свободный диалог используют premium-маршрут.
    "food": FOOD_ORDER,
    "cooking": FOOD_ORDER,
    "recipe_generation": FOOD_ORDER,
    "wardrobe": LEISURE_ORDER,
    "wardrobe_migration": LEISURE_ORDER,
    "travel": LEISURE_ORDER,
    "travel_facts10": LEISURE_ORDER,
    "leisure": LEISURE_ORDER,
    "leisure_movies": LEISURE_ORDER,
    "leisure_music": LEISURE_ORDER,
    "leisure_games": LEISURE_ORDER,
    "leisure_concerts": LEISURE_ORDER,
    "leisure_collection": LEISURE_ORDER,
    "myday": LEISURE_ORDER,
    "weather": LEISURE_ORDER,
}


def _resolve(tier, order, route=None, module=""):
    """Явный order имеет наивысший приоритет (единственный способ обойти module-policy).
    Иначе — policy известного раздела; иначе route/tier, как раньше."""
    if order is not None:
        return tuple(
            n for n in order
            if n == "openrouter" or n in PROVIDER_ORDER or n in DEFAULT_ORDER
            or n in {GROQ_SIMPLE, GROQ_STANDARD, GROQ_COMPLEX,
                     "groq", "gemini", "cf"}
        )
    if module and module in MODULE_POLICY:
        return MODULE_POLICY[module]
    if route:
        return PROVIDER_ORDER.get(route, DEFAULT_ORDER)
    o, _ = TIERS.get(tier or "smart", (DEFAULT_ORDER, None))
    return o


def _coerce_policy(fallback_allowed=False, privacy_level="personal", response_mode="plain_text",
                   fallback_policy=None, allow_personal_openrouter=False):
    if isinstance(fallback_policy, FallbackPolicy):
        return fallback_policy
    return FallbackPolicy(
        fallback_allowed=bool(fallback_allowed),
        privacy_level=privacy_level,
        response_mode=response_mode,
        allow_personal_openrouter=bool(allow_personal_openrouter),
    )

_SKIP_MODULES = frozenset({"ai", "bot", "asyncio", "threading", "concurrent", "<string>", "run_code"})

def _caller_module() -> str:
    """Автоопределение модуля-источника вызова из стека (пропускаем ai.py и служебные)."""
    for frame in inspect.stack()[2:6]:
        fname = (frame.filename or "").rsplit("/", 1)[-1]
        if fname.endswith(".py"):
            m = fname[:-3]
            if m not in _SKIP_MODULES:
                return m
    return ""

def _llm_impl(prompt, max_tokens=1200, temperature=0.7, order=None, tier=None, module="", route=None,
              fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
              response_mode: ResponseMode = "plain_text", fallback_policy=None,
              allow_personal_openrouter=False, cache_context=None, response_validator=None):
    if not module:
        module = _caller_module()
    if (
        fallback_policy is None
        and not fallback_allowed
        and module in _PUBLIC_AI_FALLBACK_MODULES
    ):
        # Учебные тексты не содержат профильных данных: для них последний
        # OpenRouter-резерв включён централизованно, чтобы новый вызов не
        # зависел от того, не забыл ли автор передать три флага вручную.
        fallback_allowed = True
        privacy_level = "public"
    policy = _coerce_policy(fallback_allowed, privacy_level, response_mode, fallback_policy,
                            allow_personal_openrouter)
    order = _resolve(tier, order, route=route, module=module)
    try:
        import tracking
        primary = order[0] if order else ""
        requested_tier = (
            "complex" if primary in ("gemini", GROQ_COMPLEX)
            else "simple" if primary in (GROQ_SIMPLE, "cf")
            else "standard"
        )
        tracking.annotate_ai_route(
            requested_tier=requested_tier,
            primary=primary,
        )
    except Exception:
        pass
    cache_ttl = _cache_ttl(module, response_mode)
    cache_key = _cache_key(
        order, prompt, max_tokens, temperature, module, response_mode,
        cache_context=cache_context,
    )
    cached = _cache_get(cache_key, cache_ttl)
    if cached:
        if _is_cacheable_response(cached, response_mode):
            _record_ai_attempt("cache", "", module, ok=True, cache_hit=True)
            try:
                import tracking
                tracking.annotate_action(provider="cache", cache_hit=True)
            except Exception:
                pass
            return cached
        # Ранее закэширован ответ, который не парсится как JSON (баг, уже
        # исправлен на записи) - не отдаём его снова на TTL модуля (до 30 дней),
        # чистим и генерируем заново.
        _cache_delete(cache_key)
    pre_gemini_unavailable = _gemini_cooldown_error() if any(
        _monitor_name(name) == "gemini" for name in order
    ) else None
    order = _reorder_for_cooldown(_reorder_for_monitor(order))
    calls = {
        "gemini": lambda: _gen_gemini(prompt, max_tokens, temperature, response_mode),
        GROQ_SIMPLE: lambda: _gen_groq(
            prompt, max_tokens, temperature, response_mode,
            model=config.GROQ_SIMPLE_MODEL, provider=GROQ_SIMPLE,
        ),
        GROQ_STANDARD: lambda: _gen_groq(
            prompt, max_tokens, temperature, response_mode,
            model=config.GROQ_STANDARD_MODEL, provider=GROQ_STANDARD,
        ),
        GROQ_COMPLEX: lambda: _gen_groq(
            prompt, max_tokens, temperature, response_mode,
            model=config.GROQ_COMPLEX_MODEL, provider=GROQ_COMPLEX,
        ),
        "groq": lambda: _gen_groq(prompt, max_tokens, temperature, response_mode),
        "cf": lambda: _gen_cf(prompt, max_tokens),
    }
    errs = []
    temporary_errs = []
    gemini_rate_limit_err = pre_gemini_unavailable
    rate_limit_logged = False
    failed_providers = []
    for provider_index, name in enumerate(order):
        remaining = _remaining_seconds()
        if remaining is not None and remaining <= 0:
            errs.append("chain:deadline")
            break
        if name == "openrouter":
            continue
        if (
            policy.openrouter_allowed
            and "openrouter" in order
            and config.OPENROUTER_API_KEY
            and remaining is not None
            and remaining <= OPENROUTER_FALLBACK_RESERVE_SECONDS
        ):
            # Не тратим последние секунды на очередной провайдер: они
            # зарезервированы для последнего общего AI-fallback.
            errs.append("chain:openrouter-reserved")
            break
        if _monitor_name(name) == "gemini":
            if not _reserve_gemini_for_action():
                errs.append("gemini: action budget exhausted")
                continue
        unavailable = _provider_is_unavailable(name)
        if unavailable is not None:
            _record_ai_attempt(
                name, _provider_model_name(name), module, ok=False,
                failure=str(unavailable),
            )
            try:
                import tracking
                tracking.record_ai_failure(
                    name, str(getattr(unavailable, "status_code", "") or getattr(unavailable, "error_type", "")),
                )
            except Exception:
                pass
            failed_providers.append(name)
            errs.append(f"{name}:{unavailable}")
            if _is_temporary_exception(unavailable):
                temporary_errs.append((name, unavailable))
            if _monitor_name(name) == "gemini" and getattr(unavailable, "error_type", "") == "rate_limit":
                gemini_rate_limit_err = unavailable
            continue
        t0 = time.time()
        try:
            out = _as_text(_run_provider_attempt(
                calls[name],
                reserve_seconds=_reserve_for_later_providers(
                    order, provider_index, policy,
                ),
            ))
            if out and out.strip():
                if response_validator is not None:
                    try:
                        response_validator(out)
                    except Exception:
                        ms = int((time.time() - t0) * 1000)
                        _record_ai_attempt(
                            name, _provider_model_name(name), module, ok=False,
                            latency_ms=ms, failure="invalid structured response",
                        )
                        try:
                            import tracking
                            tracking.record_ai_failure(name, "invalid_response")
                        except Exception:
                            pass
                        failed_providers.append(name)
                        errs.append(f"{name}:invalid structured response")
                        temporary_errs.append((name, LLMProviderError(
                            name, "invalid structured response", temporary=True,
                            error_type="invalid_response",
                        )))
                        continue
                for failed in failed_providers:
                    provider_runtime.activate_fallback(
                        _monitor_name(failed), _monitor_name(name), reason="request",
                    )
                ms = int((time.time() - t0) * 1000)
                _record_ai_attempt(name, _provider_model_name(name), module, ok=True, latency_ms=ms)
                if _monitor_name(name) != "gemini" and gemini_rate_limit_err is not None:
                    api_usage.record_gemini_fallback(target=name, reason="cooldown")
                    _log_gemini_limit("gemini_rate_limit", gemini_rate_limit_err, fallback=True)
                    rate_limit_logged = True
                _log_cost(name, _provider_model_name(name), prompt, out, module, ms=ms, ok=True)
                if _is_cacheable_response(out, response_mode):
                    _cache_set(cache_key, out)
                try:
                    import tracking
                    tracking.annotate_action(
                        provider=name,
                        fallback="provider" if failed_providers else "",
                    )
                except Exception:
                    pass
                return out
            _record_ai_attempt(
                name, _provider_model_name(name), module, ok=False,
                latency_ms=int((time.time() - t0) * 1000), failure="empty response",
            )
        except Exception as e:
            ms = int((time.time() - t0) * 1000)
            _record_ai_attempt(
                name, _provider_model_name(name), module, ok=False,
                latency_ms=ms, failure=str(e) or type(e).__name__,
            )
            try:
                import tracking
                tracking.record_ai_failure(
                    name, str(getattr(e, "status_code", "") or getattr(e, "error_type", "") or type(e).__name__),
                )
            except Exception:
                pass
            failed_providers.append(name)
            _mark_cooldown(name, e)
            errs.append(f"{name}:{e}")
            if _is_temporary_exception(e):
                temporary_errs.append((name, e))
            if _monitor_name(name) == "gemini" and getattr(e, "error_type", "") == "rate_limit":
                gemini_rate_limit_err = e
    remaining = _remaining_seconds()
    if (policy.openrouter_allowed and (remaining is None or remaining > 0.2)
            and (temporary_errs or "openrouter" in order)):
        if temporary_errs:
            origin, err = temporary_errs[0]
            reason = getattr(err, "error_type", type(err).__name__)
        else:
            origin, err = "provider_chain", None
            reason = "all_providers_failed"
        _log.warning("LLM chain failed; trying OpenRouter fallback: provider=%s reason=%s", origin, reason)
        fallback_started = time.time()
        out = _openrouter_plain_text_fallback(prompt, max_tokens, temperature, origin, reason,
                                              response_mode=policy.response_mode)
        fallback_ms = int((time.time() - fallback_started) * 1000)
        if out:
            _record_ai_attempt("openrouter", config.OPENROUTER_MODEL, module, ok=True,
                               latency_ms=fallback_ms)
            if origin in calls:
                provider_runtime.activate_fallback(
                    _monitor_name(origin), "openrouter", reason=reason,
                )
            if _monitor_name(origin) == "gemini" and getattr(err, "error_type", "") == "rate_limit":
                api_usage.record_gemini_fallback(target="openrouter", reason=reason)
                _log_gemini_limit("gemini_rate_limit", err, fallback=True)
                rate_limit_logged = True
            _log_cost("openrouter_fallback", config.OPENROUTER_MODEL, "", out, module, ok=True)
            if _is_cacheable_response(out, response_mode):
                _cache_set(cache_key, out)
            try:
                import tracking
                tracking.annotate_action(provider="openrouter", fallback="provider")
            except Exception:
                pass
            return out
        _record_ai_attempt("openrouter", config.OPENROUTER_MODEL, module, ok=False,
                           latency_ms=fallback_ms, failure="fallback failed")
        if _monitor_name(origin) == "gemini" and getattr(err, "error_type", "") == "rate_limit":
            api_usage.record_gemini_fallback(target="local", reason="openrouter_failed")
            _log_gemini_limit("gemini_rate_limit", err, fallback=True)
            rate_limit_logged = True
        raise Exception(LOCAL_FALLBACK_TEXT)
    if gemini_rate_limit_err is not None and not rate_limit_logged:
        api_usage.record_gemini_fallback(target="local", reason="all_providers_failed")
        _log_gemini_limit("gemini_rate_limit", gemini_rate_limit_err, fallback=True)
    _friendly_msg = _friendly(errs)
    try:
        import tracking
        tracking.annotate_action(fallback="local")
    except Exception:
        pass
    # Сбои конкретных провайдеров уже записаны provider_runtime как единые
    # системные инциденты. Не создаём вторую ошибку раздела с тем же сбоем.
    raise Exception(_friendly_msg)


def llm(prompt, max_tokens=1200, temperature=0.7, order=None, tier=None, module="", route=None,
        fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
        response_mode: ResponseMode = "plain_text", fallback_policy=None,
        allow_personal_openrouter=False, budget_seconds=None, cache_context=None):
    resolved_module = module or _caller_module()
    return _run_with_deadline(
        resolved_module,
        budget_seconds,
        lambda: _llm_impl(
            prompt, max_tokens, temperature, order, tier, resolved_module, route,
            fallback_allowed, privacy_level, response_mode, fallback_policy,
            allow_personal_openrouter, cache_context,
        ),
    )

def _repair_inner_quotes(raw):
    """Чинит неэкранированные двойные кавычки внутри строковых значений JSON.
    Идём по символам, отслеживаем, находимся ли внутри строки-значения."""
    out = []
    in_str = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if not in_str:
            out.append(ch)
            if ch == '"':
                in_str = True
            i += 1
            continue
        # внутри строки
        if ch == '\\':
            out.append(ch)
            if i + 1 < n:
                out.append(raw[i + 1])
                i += 2
                continue
            i += 1
            continue
        if ch == '"':
            # смотрим вперёд: если дальше структурный символ - это конец строки
            j = i + 1
            while j < n and raw[j] in ' \t\r\n':
                j += 1
            if j < n and raw[j] in ',:}]':
                out.append('"')
                in_str = False
            elif j >= n:
                out.append('"')
                in_str = False
            else:
                # кавычка внутри текста - экранируем
                out.append('\\"')
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_json_response(raw):
    raw = _extract_json_text(raw)
    if not raw:
        raise ValueError("json_parse_failed:empty")
    attempts = (
        lambda s: json.loads(s, strict=False),
        lambda s: json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s), strict=False),
        lambda s: json.loads(_repair_inner_quotes(s), strict=False),
        lambda s: json.JSONDecoder(strict=False).raw_decode(s)[0],
        lambda s: json.JSONDecoder(strict=False).raw_decode(_repair_inner_quotes(s))[0],
    )
    errors = []
    for attempt in attempts:
        try:
            parsed = attempt(raw)
        except Exception as exc:
            errors.append(type(exc).__name__)
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0]
        errors.append(type(parsed).__name__)
    raise ValueError(f"json_parse_failed:{errors[-1] if errors else 'unknown'}")


def _llm_json_impl(prompt, max_tokens=1200, order=None, tier=None, module="", route=None,
                   fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
                   allow_personal_openrouter=False, fallback_policy=None, cache_context=None,
                   result_validator=None):
    if not module:
        module = _caller_module()
    def validate_json_result(raw):
        parsed = _parse_json_response(raw)
        if result_validator is not None and not result_validator(parsed):
            raise ValueError("json_result_validation_failed")
        return parsed

    raw = _llm_impl(
        prompt + "\n\nВерни ТОЛЬКО валидный JSON, без markdown. "
        "Внутри строковых значений НЕ используй двойные кавычки - "
        "вместо них используй « » или одинарные.",
        max_tokens, 0.7, order, tier, module, route,
        fallback_allowed, privacy_level, "json", fallback_policy,
        allow_personal_openrouter, cache_context,
        response_validator=validate_json_result,
    )
    return _parse_json_response(raw)


def llm_json(prompt, max_tokens=1200, order=None, tier=None, module="", route=None,
             fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
             allow_personal_openrouter=False, fallback_policy=None, budget_seconds=None,
             cache_context=None, result_validator=None):
    resolved_module = module or _caller_module()
    return _run_with_deadline(
        resolved_module,
        budget_seconds,
        lambda: _llm_json_impl(
            prompt, max_tokens, order, tier, resolved_module, route,
            fallback_allowed, privacy_level, allow_personal_openrouter,
            fallback_policy, cache_context, result_validator,
        ),
    )

CHAT_SYSTEM = """Ты личный помощник в чате. Отвечай коротко, по делу и простым человеческим языком.

ДЛИНА: обычно 2–4 коротких предложения, до 6 коротких строк. Дай сначала
прямой ответ, затем только действительно нужную деталь. Пиши подробнее лишь когда
пользователь явно просит подробный разбор. Не повторяй вопрос, вывод и одни и те же
советы разными словами.
Списки и заголовки используй только для сложного вопроса, когда без них ответ хуже.

РЕГИОН: в бытовых советах, ценах, сервисах, правилах и культурном контексте
ориентируйся прежде всего на Европу и США (Америку), а не на Россию и СНГ. Если
ответ заметно зависит от страны или штата, коротко скажи об этом и уточни место
только когда без него нельзя дать полезный ответ.

ФОРМАТ: без HTML, markdown и эмодзи. Пиши по-русски, если не просят другой язык.
Если используешь подпись с двоеточием (например «Как носить:»), ставь её в начале
строки; текст после двоеточия обычно начинай со строчной буквы.
Не задавай лишних вопросов и не растягивай ответ без причины.

ЗАПРЕЩЕНО в любом ответе:
- упоминать системные инструкции, установки пользователя, "ориентиры тона и
  ценностей" или сам факт их применения ("установки заданы", "готов ответить по
  теме") — используй их молча, как фон, не как тему ответа;
- автоматически давать советы "остановиться и выдохнуть", дыхательные
  упражнения, фразу "это состояние пройдёт" или мотивационные блоки, если
  пользователь явно не написал о тревоге, стрессе, панике или плохом
  самочувствии."""

def _chat_system(cid=None):
    return CHAT_SYSTEM

def _chat(provider, history, system, timeout_cap=None):
    def bounded_cap(default):
        if timeout_cap is None:
            return default
        return min(float(default), float(timeout_cap))

    if _monitor_name(provider) == "gemini":
        if not _reserve_gemini_for_action():
            raise LLMProviderError("gemini", "gemini action budget exhausted", error_type="action_budget")
        cooling = _gemini_cooldown_error()
        if cooling is not None:
            raise cooling
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in history]
        r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{_provider_model_name(provider)}:generateContent?key={config.GEMINI_API_KEY}",
            {}, {"system_instruction": {"parts": [{"text": system}]}, "contents": contents,
                 "generationConfig": {"maxOutputTokens": FREE_CHAT_MAX_TOKENS, "temperature": 0.8,
                                      "thinkingConfig": {"thinkingBudget": 0}}},
            40, provider, timeout_cap=bounded_cap(6))
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    if _monitor_name(provider) == "groq":
        if not config.GROQ_API_KEY:
            raise Exception("no groq")
        r = _post("https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"},
            {"model": _provider_model_name(provider), "messages": [{"role": "system", "content": system}] + history,
             "max_tokens": FREE_CHAT_MAX_TOKENS, "temperature": 0.8}, 40, provider, timeout_cap=bounded_cap(5),
             usage_service=api_usage.groq_model_service(_provider_model_name(provider)))
        return r.json()["choices"][0]["message"]["content"]
    if provider == "openrouter":
        if not config.OPENROUTER_API_KEY:
            raise LLMProviderError("openrouter", "no OpenRouter key", error_type="credentials")
        r = _post(
            "https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            {"model": config.OPENROUTER_MODEL,
             "messages": [{"role": "system", "content": system}] + history,
             "max_tokens": FREE_CHAT_MAX_TOKENS, "temperature": 0.8},
            40,
            "openrouter",
            timeout_cap=bounded_cap(4),
        )
        return r.json()["choices"][0]["message"]["content"]
    if provider == "cf":
        if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
            raise Exception("no cf")
        r = _post(f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run/{config.CF_MODEL}",
            {"Authorization": f"Bearer {config.CF_API_TOKEN}", "Content-Type": "application/json"},
            {"messages": [{"role": "system", "content": system}] + history,
             "max_tokens": FREE_CHAT_MAX_TOKENS}, 40, "cf", timeout_cap=bounded_cap(4))
        output = _as_text(r.json().get("result", {}).get("response"))
        api_usage.record_request(
            "cloudflare",
            units={"neurons": api_usage.estimate_cloudflare_neurons(
                system + "\n" + "\n".join(str(item.get("content") or "") for item in history),
                output,
            )},
            include_request=False,
        )
        return output


def _chat_stream(provider, history, system, emit, timeout_cap=None):
    """Stream free-chat text where a provider has an SSE completion endpoint.

    This intentionally covers only the free assistant route. Structured feature
    calls must validate a completed JSON payload before users see it.
    """
    def bounded_cap(default):
        if timeout_cap is None:
            return default
        return min(float(default), float(timeout_cap))

    messages = [{"role": "system", "content": system}] + history
    payload = {
        "messages": messages,
        "max_tokens": FREE_CHAT_MAX_TOKENS,
        "temperature": 0.8,
        "stream": True,
    }
    if _monitor_name(provider) == "groq":
        if not config.GROQ_API_KEY:
            raise LLMProviderError(provider, "no groq", error_type="credentials")
        return _stream_openai_chat(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                "Authorization": f"Bearer {config.GROQ_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            {**payload, "model": _provider_model_name(provider)},
            bounded_cap(5), provider, emit,
            usage_service=api_usage.groq_model_service(_provider_model_name(provider)),
        )
    if provider == "openrouter":
        if not config.OPENROUTER_API_KEY:
            raise LLMProviderError(provider, "no OpenRouter key", error_type="credentials")
        return _stream_openai_chat(
            "https://openrouter.ai/api/v1/chat/completions",
            {
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            {**payload, "model": config.OPENROUTER_MODEL},
            bounded_cap(4), provider, emit,
        )

    # Cloudflare's current chat path is the reliable non-streaming reserve.
    # It remains inside the same chain and publishes its finished text once.
    output = _as_text(_chat(provider, history, system, timeout_cap=timeout_cap))
    if output:
        emit(output)
    return output


def _log_free_chat_route(*, served_by="", outcome=""):
    _log.info(
        "AI route scenario=%s tier=%s provider_chain=%s served_by=%s version=%s deployment=%s replica=%s pid=%s route_version=%s outcome=%s",
        FREE_CHAT_SCENARIO, FREE_CHAT_TIER, ",".join(CHAT_ORDER), served_by or "-",
        config.APP_VERSION or "-", config.RAILWAY_DEPLOYMENT_ID or "local",
        getattr(config, "RAILWAY_REPLICA_ID", "") or "local", os.getpid(),
        FREE_CHAT_ROUTE_VERSION, outcome or "-",
    )


def _chat_chain_impl(history, cid=None):
    system = _chat_system(cid)
    errs = []
    prompt_len = sum(len(m.get("content", "")) for m in history)
    failed_providers = []
    try:
        import tracking
        tracking.annotate_ai_route(requested_tier=FREE_CHAT_TIER, primary=CHAT_ORDER[0])
    except Exception:
        pass
    for p in CHAT_ORDER:
        remaining = _remaining_seconds()
        if remaining is not None and remaining < _MIN_USEFUL_PROVIDER_ATTEMPT_SECONDS:
            errs.append("chain:deadline")
            break
        unavailable = _provider_is_unavailable(p)
        if unavailable is not None:
            _record_ai_attempt(p, _provider_model_name(p), "assistant", ok=False,
                               failure=str(unavailable))
            failed_providers.append(p)
            errs.append(f"{p}:{unavailable}")
            continue
        try:
            attempt_started = time.time()
            attempt_timeout = min(
                _FREE_CHAT_PROVIDER_TIMEOUTS[p],
                remaining if remaining is not None else _FREE_CHAT_PROVIDER_TIMEOUTS[p],
            )
            out = _as_text(_chat(p, history, system, timeout_cap=attempt_timeout))
            if out and out.strip():
                _record_ai_attempt(
                    p, _provider_model_name(p), "assistant", ok=True,
                    latency_ms=int((time.time() - attempt_started) * 1000),
                )
                for failed in failed_providers:
                    provider_runtime.activate_fallback(
                        _monitor_name(failed), _monitor_name(p), reason="request",
                    )
                _log_cost(p, _provider_model_name(p), "c" * prompt_len, out, "assistant")
                try:
                    import tracking
                    tracking.annotate_action(
                        provider=p,
                        fallback="provider" if failed_providers else "",
                    )
                except Exception:
                    pass
                _log_free_chat_route(served_by=p, outcome="success")
                return out
            _record_ai_attempt(
                p, _provider_model_name(p), "assistant", ok=False,
                latency_ms=int((time.time() - attempt_started) * 1000), failure="empty response",
            )
        except Exception as e:
            _record_ai_attempt(p, _provider_model_name(p), "assistant", ok=False,
                               latency_ms=int((time.time() - attempt_started) * 1000),
                               failure=str(e) or type(e).__name__)
            failed_providers.append(p)
            _mark_cooldown(p, e)
            errs.append(f"{p}:{e}")
    try:
        import tracking
        tracking.annotate_action(fallback="local")
    except Exception:
        pass
    _log_free_chat_route(outcome="failed")
    raise Exception(_friendly(errs))


def _chat_chain_stream_impl(history, cid=None, emit=None):
    """Free-chat route with SSE before the first visible provider output.

    A provider may be replaced only before it has yielded text. Once the user
    has seen a delta, swapping models would make two unrelated answers appear
    as one; that case ends with a short retry prompt instead.
    """
    system = _chat_system(cid)
    emit = emit or (lambda _delta: None)
    errs = []
    prompt_len = sum(len(m.get("content", "")) for m in history)
    failed_providers = []
    try:
        import tracking
        tracking.annotate_ai_route(requested_tier=FREE_CHAT_TIER, primary=CHAT_ORDER[0])
    except Exception:
        pass

    for p in CHAT_ORDER:
        remaining = _remaining_seconds()
        if remaining is not None and remaining < _MIN_USEFUL_PROVIDER_ATTEMPT_SECONDS:
            errs.append("chain:deadline")
            break
        unavailable = _provider_is_unavailable(p)
        if unavailable is not None:
            _record_ai_attempt(
                p, _provider_model_name(p), "assistant", ok=False, failure=str(unavailable),
            )
            failed_providers.append(p)
            errs.append(f"{p}:{unavailable}")
            continue

        emitted = False

        def send_delta(delta):
            nonlocal emitted
            delta = str(delta or "")
            if delta:
                emitted = True
                emit(delta)

        try:
            attempt_started = time.time()
            attempt_timeout = min(
                _FREE_CHAT_PROVIDER_TIMEOUTS[p],
                remaining if remaining is not None else _FREE_CHAT_PROVIDER_TIMEOUTS[p],
            )
            out = _as_text(_chat_stream(p, history, system, send_delta, timeout_cap=attempt_timeout))
            if out and out.strip():
                _record_ai_attempt(
                    p, _provider_model_name(p), "assistant", ok=True,
                    latency_ms=int((time.time() - attempt_started) * 1000),
                )
                for failed in failed_providers:
                    provider_runtime.activate_fallback(
                        _monitor_name(failed), _monitor_name(p), reason="request",
                    )
                _log_cost(p, _provider_model_name(p), "c" * prompt_len, out, "assistant")
                try:
                    import tracking
                    tracking.annotate_action(
                        provider=p, fallback="provider" if failed_providers else "",
                    )
                except Exception:
                    pass
                _log_free_chat_route(served_by=p, outcome="success")
                return out
            _record_ai_attempt(
                p, _provider_model_name(p), "assistant", ok=False,
                latency_ms=int((time.time() - attempt_started) * 1000), failure="empty response",
            )
        except _PartialStreamError as stream_error:
            error = stream_error.error
            _record_ai_attempt(
                p, _provider_model_name(p), "assistant", ok=False,
                latency_ms=int((time.time() - attempt_started) * 1000),
                failure=str(error) or type(error).__name__,
            )
            _mark_cooldown(p, error)
            _log_free_chat_route(served_by=p, outcome="stream_interrupted")
            raise StreamOutputInterrupted() from error
        except Exception as error:
            _record_ai_attempt(
                p, _provider_model_name(p), "assistant", ok=False,
                latency_ms=int((time.time() - attempt_started) * 1000),
                failure=str(error) or type(error).__name__,
            )
            # ``emitted`` is normally only true for _PartialStreamError. Keep
            # the guard for alternate provider implementations as well.
            if emitted:
                _mark_cooldown(p, error)
                _log_free_chat_route(served_by=p, outcome="stream_interrupted")
                raise StreamOutputInterrupted() from error
            failed_providers.append(p)
            _mark_cooldown(p, error)
            errs.append(f"{p}:{error}")
    try:
        import tracking
        tracking.annotate_action(fallback="local")
    except Exception:
        pass
    _log_free_chat_route(outcome="failed")
    raise Exception(_friendly(errs))


def chat_chain(history, cid=None, budget_seconds=None):
    return _run_with_deadline(
        "assistant",
        budget_seconds or FREE_CHAT_BUDGET_SECONDS,
        lambda: _chat_chain_impl(history, cid),
    )


def chat_chain_stream(history, cid=None, emit=None, budget_seconds=None):
    return _run_with_deadline(
        "assistant",
        budget_seconds or FREE_CHAT_BUDGET_SECONDS,
        lambda: _chat_chain_stream_impl(history, cid, emit),
    )


# --- async-обёртки для вызова из async-обработчиков без блокировки event loop ---
async def allm(prompt, max_tokens=1200, temperature=0.7, order=None, tier=None, route=None, module="",
               fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
               response_mode: ResponseMode = "plain_text", fallback_policy=None,
               allow_personal_openrouter=False, budget_seconds=None, cache_context=None):
    return await asyncio.to_thread(
        llm, prompt, max_tokens, temperature, order, tier, module, route,
        fallback_allowed, privacy_level, response_mode, fallback_policy,
        allow_personal_openrouter, budget_seconds, cache_context,
    )

async def allm_json(prompt, max_tokens=1200, order=None, tier=None, route=None, module="",
                    fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
                    allow_personal_openrouter=False, fallback_policy=None,
                    budget_seconds=None, cache_context=None, result_validator=None):
    return await asyncio.to_thread(
        llm_json, prompt, max_tokens, order, tier, module, route,
        fallback_allowed, privacy_level, allow_personal_openrouter, fallback_policy,
        budget_seconds, cache_context, result_validator,
    )

async def achat_chain(history, cid=None, budget_seconds=FREE_CHAT_BUDGET_SECONDS):
    return await asyncio.to_thread(chat_chain, history, cid, budget_seconds)


async def achat_chain_stream(history, cid=None, on_delta=None,
                             budget_seconds=FREE_CHAT_BUDGET_SECONDS):
    """Bridge synchronous provider SSE to Telegram's async draft updates."""
    if on_delta is None:
        return await achat_chain(history, cid, budget_seconds)

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def publish(kind, value):
        loop.call_soon_threadsafe(queue.put_nowait, (kind, value))

    def worker():
        try:
            result = chat_chain_stream(
                history, cid, emit=lambda delta: publish("delta", delta),
                budget_seconds=budget_seconds,
            )
        except Exception as error:
            publish("error", error)
        else:
            publish("result", result)

    worker_task = asyncio.create_task(asyncio.to_thread(worker))
    try:
        while True:
            kind, value = await queue.get()
            if kind == "delta":
                try:
                    await on_delta(value)
                except Exception:
                    # A temporary draft-render failure must not discard a
                    # fully generated answer that can still be persisted.
                    _log.debug("rich draft update failed", exc_info=True)
                continue
            await worker_task
            if kind == "error":
                raise value
            return value
    finally:
        if not worker_task.done():
            # ``requests`` cannot be force-cancelled safely from another
            # thread. It is bounded by the free-chat deadline and its queued
            # output is no longer consumed by a cancelled Telegram update.
            worker_task.cancel()
