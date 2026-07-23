import asyncio
import contextvars
import inspect
import logging
import re
import json
import time
import threading
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
_COMPLEX_MODULE_PREFIXES = (
    "assistant", "food", "cooking", "recipe", "wardrobe", "travel", "leisure", "learning",
)

# ---------- Cost logger ----------
_COST_MAX = 500  # максимум записей в rolling-буфере
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
    "cohere": 5.0,
    "github_models": 5.0,
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
        data = store._load(config.AI_RESPONSE_CACHE_KEY)
        items = data.setdefault("items", {})
        items[key] = {"ts": int(time.time()), "value": value}
        if len(items) > _AI_CACHE_MAX:
            oldest = sorted(items.items(), key=lambda kv: int((kv[1] or {}).get("ts") or 0))
            for k, _v in oldest[:len(items) - _AI_CACHE_MAX]:
                items.pop(k, None)
        store._save(config.AI_RESPONSE_CACHE_KEY, data)
    except Exception:
        pass


def _cache_delete(key: str):
    try:
        data = store._load(config.AI_RESPONSE_CACHE_KEY)
        items = data.get("items") or {}
        if key in items:
            items.pop(key, None)
            store._save(config.AI_RESPONSE_CACHE_KEY, data)
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
    if provider == "gemini":
        return config.GEMINI_MODEL
    if provider == "cohere":
        return config.COHERE_MODEL
    if provider == "github_models":
        return config.GITHUB_MODELS_MODEL
    if provider == "groq":
        return "llama-3.3-70b-versatile"
    if provider == "cf":
        return "cloudflare-cf-model"
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


def _cohere_limit_error():
    if api_usage.cohere_requests()["allowed"]:
        return None
    return LLMProviderError(
        "cohere", "cohere monthly limit exhausted", error_type="quota",
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
        tracking.log_error(
            "llm", f"{first}\n{second}", kind=kind or "gemini_rate_limit",
            section="Разные категории", action="сработал лимит провайдера",
            service="Gemini", fallback="автоматический резерв" if fallback else "",
        )
    except Exception:
        pass

def _post(url, headers, payload, timeout, name, timeout_cap=None):
    service = {"cf": "cloudflare"}.get(name, name)
    cohere_request = service == "cohere"
    gemini_request = service == "gemini"
    if cohere_request:
        limit_error = _cohere_limit_error()
        if limit_error is not None:
            raise limit_error
    if timeout_cap is None:
        timeout_cap = _timeout_cap(name)
    if timeout_cap is not None:
        timeout = min(float(timeout), float(timeout_cap))
    t0 = time.time()
    timeout = _bounded_timeout(timeout)
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.Timeout as e:
        if cohere_request:
            provider_runtime.record_result(service, False, error="timeout")
        else:
            api_usage.record_request(service, ok=False, error="timeout")
        raise LLMProviderError(name, f"{name} timeout", temporary=True, error_type=type(e).__name__) from e
    except requests.exceptions.ConnectionError as e:
        if cohere_request:
            provider_runtime.record_result(service, False, error="network_error")
        else:
            api_usage.record_request(service, ok=False, error="network_error")
        raise LLMProviderError(name, f"{name} network error", temporary=True, error_type=type(e).__name__) from e
    finally:
        if cohere_request:
            api_usage.cohere_requests(consume=True)
        elif gemini_request:
            api_usage.gemini_requests(consume=True)
    if r.status_code != 200:
        # тело ошибки в логи (видно причину), но без секретов
        body = secure.redact((r.text or "")[:300])
        temporary = _is_temporary_status(r.status_code)
        limit_scope = ""
        cooldown_until = None
        if cohere_request:
            provider_runtime.record_result(
                service, False, status_code=r.status_code,
                error=f"HTTP {r.status_code}", headers=r.headers,
            )
        else:
            api_usage.record_request(service, ok=False, status_code=r.status_code,
                                     error=f"HTTP {r.status_code}",
                                     latency_ms=int((time.time() - t0) * 1000),
                                     headers=r.headers)
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
    if cohere_request:
        provider_runtime.record_result(service, True, headers=r.headers)
    elif service != "gemini":
        api_usage.record_request(service, ok=True, latency_ms=int((time.time() - t0) * 1000),
                                 headers=r.headers)
    return r

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
def _gen_gemini(prompt, max_tokens, temperature, response_mode: ResponseMode = "plain_text"):
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
            time.sleep(wait)
        t0 = time.time()
        r = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}",
            {}, payload, 30, "gemini", timeout_cap=6)
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


def _gen_cohere(prompt, max_tokens, temperature, response_mode: ResponseMode = "plain_text"):
    """Cohere Chat API V2 для языковых, классификационных и JSON-задач."""
    if not config.COHERE_API_KEY:
        raise LLMProviderError("cohere", "no cohere key", error_type="credentials")
    cohere_temperature = 0.3 if temperature is None else float(temperature)
    payload = {
        "model": config.COHERE_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(max_tokens or 1200),
        "temperature": min(max(cohere_temperature, 0.0), 1.0),
        # Command A+ currently rejects disabled reasoning, while the default
        # reasoning can consume the whole short-answer budget. Keep a small,
        # bounded thinking slice and leave the rest for the actual response.
        "thinking": {
            "type": "enabled",
            "token_budget": min(64, max(20, int(max_tokens or 1200) // 5)),
        },
    }
    if response_mode == "json":
        payload["response_format"] = {"type": "json_object"}
    r = _post(
        "https://api.cohere.com/v2/chat",
        {
            "Authorization": f"Bearer {config.COHERE_API_KEY}",
            "Content-Type": "application/json",
            "X-Client-Name": "morning-bot",
        },
        payload,
        30,
        "cohere",
        timeout_cap=5,
    )
    data = r.json()
    content = (data.get("message") or {}).get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").strip():
            return str(block["text"]).strip()
    raise LLMProviderError("cohere", "empty cohere response", error_type="empty_response")


def _gen_github_models(prompt, max_tokens, temperature,
                       response_mode: ResponseMode = "plain_text"):
    """GitHub Models Chat Completions как универсальный резервный провайдер."""
    if not config.GITHUB_MODELS_TOKEN:
        raise LLMProviderError(
            "github_models", "no GitHub Models token", error_type="credentials",
        )
    github_temperature = 0.3 if temperature is None else float(temperature)
    payload = {
        "model": config.GITHUB_MODELS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(max_tokens or 1200),
        "temperature": min(max(github_temperature, 0.0), 1.0),
    }
    if response_mode == "json":
        payload["response_format"] = {"type": "json_object"}
    r = _post(
        "https://models.github.ai/inference/chat/completions",
        {
            "Authorization": f"Bearer {config.GITHUB_MODELS_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2026-03-10",
        },
        payload,
        30,
        "github_models",
        timeout_cap=5,
    )
    data = r.json()
    choices = data.get("choices") or []
    if choices:
        content = ((choices[0].get("message") or {}).get("content") or "").strip()
        if content:
            return content
    raise LLMProviderError(
        "github_models", "empty GitHub Models response", error_type="empty_response",
    )


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

def _gen_groq(prompt, max_tokens, temperature, response_mode: ResponseMode = "plain_text"):
    if not config.GROQ_API_KEY:
        raise Exception("no groq")
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_mode == "json":
        payload["response_format"] = {"type": "json_object"}
    r = _post("https://api.groq.com/openai/v1/chat/completions",
        {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"},
        payload,
        40, "groq", timeout_cap=5)
    return r.json()["choices"][0]["message"]["content"]

def _gen_cf(prompt, max_tokens):
    if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
        raise Exception("no cf")
    r = _post(f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct",
        {"Authorization": f"Bearer {config.CF_API_TOKEN}", "Content-Type": "application/json"},
        {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
        40, "cf", timeout_cap=4)
    return _as_text(r.json().get("result", {}).get("response"))

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
    _cooldowns[name] = max(_cooldowns.get(name, 0), time.time() + seconds)

def _is_cooling(name):
    return _cooldowns.get(name, 0) > time.time()

def _reorder_for_cooldown(order):
    """Провайдеров на cooldown (недавний временный сбой) отодвигаем в конец, чтобы не терять
    время на заведомо неудачный запрос перед рабочим fallback-ом."""
    if not any(_is_cooling(n) for n in order):
        return order
    return tuple(sorted(order, key=lambda n: _is_cooling(n)))


def _monitor_name(provider):
    return "cloudflare" if provider == "cf" else provider


def _provider_name(service):
    return "cf" if service == "cloudflare" else service


def _reorder_for_monitor(order):
    """Put a genuinely selected reserve first; keep the primary in the chain so
    a later successful call can automatically restore it."""
    result = list(order)
    if not result:
        return tuple(result)
    selected = _provider_name(provider_runtime.selected_provider(_monitor_name(result[0])))
    if selected in result and selected != result[0]:
        result.remove(selected)
        result.insert(0, selected)
    return tuple(result)


def _reorder_for_cohere_limit(order, *, cohere_primary=False):
    """При месячном лимите ставит GitHub Models сразу после пропущенного Cohere."""
    if "cohere" not in order or api_usage.cohere_requests()["allowed"]:
        return order
    result = [name for name in order if name != "github_models"]
    if cohere_primary:
        result.remove("cohere")
        result.insert(0, "cohere")
    result.insert(result.index("cohere") + 1, "github_models")
    return tuple(result)


def _provider_is_unavailable(name):
    if name == "cohere":
        limit_error = _cohere_limit_error()
        if limit_error is not None:
            return limit_error
    if name == "gemini":
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

# Разбор, классификация и короткий structured output не расходуют Gemini.
UTILITY_ORDER = ("groq", "github_models", "cohere", "openrouter")
# Gemini остаётся только для одной финальной пользовательской карточки.
PREMIUM_ORDER = ("gemini", "github_models", "groq", "openrouter")
DEFAULT_ORDER = UTILITY_ORDER
CHAT_ORDER = ("groq", "github_models", "cohere", "cf")
GRAMMAR_ORDER = UTILITY_ORDER
LEISURE_ORDER = PREMIUM_ORDER
FOOD_ORDER = PREMIUM_ORDER

# Явные пресеты: позволяют приоритизировать конкретный провайдер, не меняя код вызова по всему проекту.
PROVIDER_ORDER = {
    "cf": ("cf", "groq", "github_models", "cohere"),
    "groq": ("groq", "github_models", "cohere", "openrouter"),
    "github_models": ("github_models", "groq", "cohere", "openrouter"),
    "cohere": ("cohere", "groq", "github_models", "openrouter"),
    "gemini": PREMIUM_ORDER,
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
    # Cohere первым: языки, строгий JSON, классификация и короткий анализ.
    "learning": GRAMMAR_ORDER,
    "learning_dict_add": GRAMMAR_ORDER,
    "learning_trainer": GRAMMAR_ORDER,
    "learning_srs_migration": GRAMMAR_ORDER,
    "learning_game": GRAMMAR_ORDER,
    "learning_dictionary": GRAMMAR_ORDER,
    "dictionary_import": GRAMMAR_ORDER,
    "trainer": GRAMMAR_ORDER,
    "health": GRAMMAR_ORDER,
    "balance": GRAMMAR_ORDER,
    "thoughts": GRAMMAR_ORDER,
    # Gemini первым: творческая генерация, рекомендации и свободный диалог.
    "food": FOOD_ORDER,
    "cooking": FOOD_ORDER,
    "recipe_generation": FOOD_ORDER,
    "wardrobe": LEISURE_ORDER,
    "wardrobe_copy": LEISURE_ORDER,
    "wardrobe_migration": LEISURE_ORDER,
    "travel": LEISURE_ORDER,
    "travel_facts10": LEISURE_ORDER,
    "leisure": LEISURE_ORDER,
    "leisure_movies": LEISURE_ORDER,
    "leisure_music": LEISURE_ORDER,
    "leisure_concerts": LEISURE_ORDER,
    "leisure_collection": LEISURE_ORDER,
    "myday": LEISURE_ORDER,
    "firstvisit": UTILITY_ORDER,
    "weather": LEISURE_ORDER,
}


def _resolve(tier, order, route=None, module=""):
    """Явный order имеет наивысший приоритет (единственный способ обойти module-policy).
    Иначе — policy известного раздела; иначе route/tier, как раньше."""
    if order is not None:
        return tuple(
            n for n in order
            if n == "openrouter" or n in PROVIDER_ORDER or n in DEFAULT_ORDER
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
              allow_personal_openrouter=False, cache_context=None):
    if not module:
        module = _caller_module()
    policy = _coerce_policy(fallback_allowed, privacy_level, response_mode, fallback_policy,
                            allow_personal_openrouter)
    order = _resolve(tier, order, route=route, module=module)
    try:
        import tracking
        tracking.annotate_ai_route(
            requested_tier="premium" if order and order[0] == "gemini" else "utility",
            primary=order[0] if order else "",
        )
    except Exception:
        pass
    cohere_primary = bool(order and order[0] == "cohere")
    cache_ttl = _cache_ttl(module, response_mode)
    cache_key = _cache_key(
        order, prompt, max_tokens, temperature, module, response_mode,
        cache_context=cache_context,
    )
    cached = _cache_get(cache_key, cache_ttl)
    if cached:
        if _is_cacheable_response(cached, response_mode):
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
    pre_gemini_unavailable = _gemini_cooldown_error() if "gemini" in order else None
    order = _reorder_for_cooldown(_reorder_for_monitor(order))
    order = _reorder_for_cohere_limit(order, cohere_primary=cohere_primary)
    calls = {
        "gemini": lambda: _gen_gemini(prompt, max_tokens, temperature, response_mode),
        "cohere": lambda: _gen_cohere(prompt, max_tokens, temperature, response_mode),
        "github_models": lambda: _gen_github_models(
            prompt, max_tokens, temperature, response_mode,
        ),
        "groq": lambda: _gen_groq(prompt, max_tokens, temperature, response_mode),
        "cf": lambda: _gen_cf(prompt, max_tokens),
    }
    errs = []
    temporary_errs = []
    gemini_rate_limit_err = pre_gemini_unavailable
    rate_limit_logged = False
    failed_providers = []
    for name in order:
        remaining = _remaining_seconds()
        if remaining is not None and remaining <= 0:
            errs.append("chain:deadline")
            break
        if name == "openrouter":
            continue
        if name == "gemini":
            if not _reserve_gemini_for_action():
                errs.append("gemini: action budget exhausted")
                continue
        unavailable = _provider_is_unavailable(name)
        if unavailable is not None:
            try:
                import tracking
                tracking.record_ai_failure(
                    name, str(getattr(unavailable, "status_code", "") or getattr(unavailable, "error_type", "")),
                )
            except Exception:
                pass
            local_cohere_limit = (
                name == "cohere" and getattr(unavailable, "error_type", "") == "quota"
            )
            if not local_cohere_limit:
                failed_providers.append(name)
            errs.append(f"{name}:{unavailable}")
            if _is_temporary_exception(unavailable):
                temporary_errs.append((name, unavailable))
            if name == "gemini" and getattr(unavailable, "error_type", "") == "rate_limit":
                gemini_rate_limit_err = unavailable
            continue
        t0 = time.time()
        try:
            out = _as_text(calls[name]())
            if out and out.strip():
                for failed in failed_providers:
                    provider_runtime.activate_fallback(
                        _monitor_name(failed), _monitor_name(name), reason="request",
                    )
                ms = int((time.time() - t0) * 1000)
                if name != "gemini" and gemini_rate_limit_err is not None:
                    api_usage.record_gemini_fallback(target=name, reason="cooldown")
                    _log_gemini_limit("gemini_rate_limit", gemini_rate_limit_err, fallback=True)
                    rate_limit_logged = True
                _log_cost(name, name, prompt, out, module, ms=ms, ok=True)
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
        except Exception as e:
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
            if name == "gemini" and getattr(e, "error_type", "") == "rate_limit":
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
        out = _openrouter_plain_text_fallback(prompt, max_tokens, temperature, origin, reason,
                                              response_mode=policy.response_mode)
        if out:
            if origin in calls:
                provider_runtime.activate_fallback(
                    _monitor_name(origin), "openrouter", reason=reason,
                )
            if origin == "gemini" and getattr(err, "error_type", "") == "rate_limit":
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
        if origin == "gemini" and getattr(err, "error_type", "") == "rate_limit":
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
    try:
        import tracking
        if gemini_rate_limit_err is None:
            tracking.log_error(
                "llm", "; ".join(errs)[:1000] or _friendly_msg,
                kind="all-providers-failed", action="не сформирован ответ",
                service="несколько AI-сервисов", fallback="шаблон без AI",
            )
    except Exception:
        pass
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
                   allow_personal_openrouter=False, fallback_policy=None, cache_context=None):
    if not module:
        module = _caller_module()
    expected_format = "JSON object"
    raw = llm(prompt + "\n\nВерни ТОЛЬКО валидный JSON, без markdown. "
                       "Внутри строковых значений НЕ используй двойные кавычки - "
                       "вместо них используй « » или одинарные.", max_tokens, 0.7, order, tier, module, route,
              fallback_allowed=fallback_allowed, privacy_level=privacy_level, response_mode="json",
              fallback_policy=fallback_policy, allow_personal_openrouter=allow_personal_openrouter,
              cache_context=cache_context)
    try:
        return _parse_json_response(raw)
    except ValueError as exc:
        parse_error = exc

    repair_prompt = (
        "Преобразуй ответ ИИ ниже в один валидный JSON-объект без markdown и пояснений. "
        "Сохрани существующие данные, не добавляй новые факты. Если данных недостаточно, верни {}.\n\n"
        "Ожидаемая задача:\n"
        f"{secure.wrap_untrusted(prompt[:3000], 'исходный промпт')}\n\n"
        "Ответ ИИ для исправления:\n"
        f"{secure.wrap_untrusted(str(raw)[:6000], 'сырой ответ ИИ')}"
    )
    try:
        repaired = llm(repair_prompt, max_tokens, 0.1, order, tier, module, route,
                       fallback_allowed=fallback_allowed, privacy_level=privacy_level,
                       response_mode="json", fallback_policy=fallback_policy,
                       allow_personal_openrouter=allow_personal_openrouter)
        return _parse_json_response(repaired)
    except Exception as exc:
        try:
            provider = (tuple(order or ()) or ("llm",))[0]
            import tracking
            tracking.log_error(
                "llm",
                (
                    f"Не удалось разобрать JSON: {type(parse_error).__name__ if 'parse_error' in locals() else type(exc).__name__}; "
                    f"provider={provider}; model={_provider_model_name(provider)}; expected={expected_format}; "
                    f"response={_json_preview(raw)}"
                ),
                kind="json-parse",
                action="не обработан ответ сервиса",
                service=provider.title().replace("_", " "),
                fallback="безопасный шаблон",
                exc=exc,
            )
        except Exception:
            pass
    raise Exception("Не удалось разобрать ответ ИИ (JSON). Попробуй ещё раз.")


def llm_json(prompt, max_tokens=1200, order=None, tier=None, module="", route=None,
             fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
             allow_personal_openrouter=False, fallback_policy=None, budget_seconds=None,
             cache_context=None):
    resolved_module = module or _caller_module()
    return _run_with_deadline(
        resolved_module,
        budget_seconds,
        lambda: _llm_json_impl(
            prompt, max_tokens, order, tier, resolved_module, route,
            fallback_allowed, privacy_level, allow_personal_openrouter,
            fallback_policy, cache_context,
        ),
    )

CHAT_SYSTEM = """Ты помощник. Отвечай как обычный собеседник в чате, а не как документ.

ДЛИНА: короткое сообщение пользователя (реплика, вопрос из пары слов) заслуживает
ответ в 1 предложение. Длинный или сложный вопрос — до 8 строк, не длиннее.
Списки, заголовки и структура ("Что важно:", "Что сделать:") — только если вопрос
реально сложный и многосоставный; для обычного разговора не нужны.
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

def _chat(provider, history, system):
    if provider == "gemini":
        if not _reserve_gemini_for_action():
            raise LLMProviderError("gemini", "gemini action budget exhausted", error_type="action_budget")
        cooling = _gemini_cooldown_error()
        if cooling is not None:
            raise cooling
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in history]
        r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}",
            {}, {"system_instruction": {"parts": [{"text": system}]}, "contents": contents,
                 "generationConfig": {"maxOutputTokens": 700, "temperature": 0.8, "thinkingConfig": {"thinkingBudget": 0}}}, 40, "gemini", timeout_cap=6)
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    if provider == "cohere":
        if not config.COHERE_API_KEY:
            raise LLMProviderError("cohere", "no cohere key", error_type="credentials")
        r = _post(
            "https://api.cohere.com/v2/chat",
            {
                "Authorization": f"Bearer {config.COHERE_API_KEY}",
                "Content-Type": "application/json",
                "X-Client-Name": "morning-bot",
            },
            {
                "model": config.COHERE_MODEL,
                "messages": [{"role": "system", "content": system}] + history,
                "max_tokens": 700,
                "temperature": 0.8,
            },
            30,
            "cohere",
            timeout_cap=5,
        )
        return r.json()["message"]["content"][0]["text"]
    if provider == "github_models":
        if not config.GITHUB_MODELS_TOKEN:
            raise LLMProviderError(
                "github_models", "no GitHub Models token", error_type="credentials",
            )
        r = _post(
            "https://models.github.ai/inference/chat/completions",
            {
                "Authorization": f"Bearer {config.GITHUB_MODELS_TOKEN}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            {
                "model": config.GITHUB_MODELS_MODEL,
                "messages": [{"role": "system", "content": system}] + history,
                "max_tokens": 700,
                "temperature": 0.8,
            },
            30,
            "github_models",
            timeout_cap=5,
        )
        return r.json()["choices"][0]["message"]["content"]
    if provider == "groq":
        if not config.GROQ_API_KEY:
            raise Exception("no groq")
        r = _post("https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"},
            {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": system}] + history,
             "max_tokens": 700, "temperature": 0.8}, 40, "groq", timeout_cap=5)
        return r.json()["choices"][0]["message"]["content"]
    if provider == "cf":
        if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
            raise Exception("no cf")
        r = _post(f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct",
            {"Authorization": f"Bearer {config.CF_API_TOKEN}", "Content-Type": "application/json"},
            {"messages": [{"role": "system", "content": system}] + history, "max_tokens": 700}, 40, "cf", timeout_cap=4)
        return _as_text(r.json().get("result", {}).get("response"))

def _chat_chain_impl(history, cid=None):
    system = _chat_system(cid)
    errs = []
    prompt_len = sum(len(m.get("content", "")) for m in history)
    gemini_rate_limit_err = _gemini_cooldown_error() if "gemini" in CHAT_ORDER else None
    failed_providers = []
    for p in _reorder_for_cooldown(_reorder_for_monitor(CHAT_ORDER)):
        remaining = _remaining_seconds()
        if remaining is not None and remaining <= 0:
            errs.append("chain:deadline")
            break
        unavailable = _provider_is_unavailable(p)
        if unavailable is not None:
            failed_providers.append(p)
            errs.append(f"{p}:{unavailable}")
            if p == "gemini" and getattr(unavailable, "error_type", "") == "rate_limit":
                gemini_rate_limit_err = unavailable
            continue
        try:
            out = _as_text(_chat(p, history, system))
            if out and out.strip():
                for failed in failed_providers:
                    provider_runtime.activate_fallback(
                        _monitor_name(failed), _monitor_name(p), reason="request",
                    )
                if p != "gemini" and gemini_rate_limit_err is not None:
                    api_usage.record_gemini_fallback(target=p, reason="cooldown")
                    _log_gemini_limit("gemini_rate_limit", gemini_rate_limit_err, fallback=True)
                _log_cost(p, p, "c" * prompt_len, out, "assistant")
                try:
                    import tracking
                    tracking.annotate_action(
                        provider=p,
                        fallback="provider" if failed_providers else "",
                    )
                except Exception:
                    pass
                return out
        except Exception as e:
            failed_providers.append(p)
            _mark_cooldown(p, e)
            errs.append(f"{p}:{e}")
            if p == "gemini" and getattr(e, "error_type", "") == "rate_limit":
                gemini_rate_limit_err = e
    if gemini_rate_limit_err is not None:
        api_usage.record_gemini_fallback(target="local", reason="chat_failed")
        _log_gemini_limit("gemini_rate_limit", gemini_rate_limit_err, fallback=True)
    try:
        import tracking
        tracking.annotate_action(fallback="local")
    except Exception:
        pass
    raise Exception(_friendly(errs))


def chat_chain(history, cid=None, budget_seconds=None):
    return _run_with_deadline(
        "assistant",
        budget_seconds,
        lambda: _chat_chain_impl(history, cid),
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
                    budget_seconds=None, cache_context=None):
    return await asyncio.to_thread(
        llm_json, prompt, max_tokens, order, tier, module, route,
        fallback_allowed, privacy_level, allow_personal_openrouter, fallback_policy,
        budget_seconds, cache_context,
    )

async def achat_chain(history, cid=None, budget_seconds=COMPLEX_BUDGET_SECONDS):
    return await asyncio.to_thread(chat_chain, history, cid, budget_seconds)
