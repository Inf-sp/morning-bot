import asyncio
import inspect
import logging
import re
import json
import time
import threading
import requests
from dataclasses import dataclass
from typing import Literal
import api_usage
import config
import store
import secure

_log = logging.getLogger(__name__)
_GEMINI_RATE_LOCK = threading.Lock()

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
                 retry_after: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.temporary = temporary
        self.error_type = error_type
        self.retry_after = retry_after


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

def _post(url, headers, payload, timeout, name):
    service = {"cf": "cloudflare"}.get(name, name)
    t0 = time.time()
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.Timeout as e:
        api_usage.record_request(service, ok=False, error="timeout")
        raise LLMProviderError(name, f"{name} timeout", temporary=True, error_type=type(e).__name__) from e
    except requests.exceptions.ConnectionError as e:
        api_usage.record_request(service, ok=False, error="network_error")
        raise LLMProviderError(name, f"{name} network error", temporary=True, error_type=type(e).__name__) from e
    if r.status_code != 200:
        # тело ошибки в логи (видно причину), но без секретов
        body = secure.redact((r.text or "")[:300])
        temporary = _is_temporary_status(r.status_code)
        api_usage.record_request(service, ok=False, status_code=r.status_code,
                                 error=f"HTTP {r.status_code}",
                                 latency_ms=int((time.time() - t0) * 1000),
                                 headers=r.headers)
        retry_after = None
        try:
            retry_after = int(r.headers.get("Retry-After") or 0) or None
        except Exception:
            retry_after = None
        raise LLMProviderError(name, f"{name} {r.status_code}: {body}",
                               status_code=r.status_code, temporary=temporary,
                               error_type="http_error", retry_after=retry_after)
    if service != "gemini":
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
def _gen_gemini(prompt, max_tokens, temperature):
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature,
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    last_err = None
    for attempt in range(2):
        try:
            with _GEMINI_RATE_LOCK:
                wait = api_usage.seconds_until_gemini_slot(limit=4, window=60)
                if wait > 0:
                    time.sleep(wait)
                t0 = time.time()
                r = _post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={config.GEMINI_API_KEY}",
                    {}, payload, 30, "gemini")
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
        except LLMProviderError as e:
            last_err = e
            if e.status_code == 429 and attempt == 0:
                time.sleep(min(max(int(e.retry_after or 5), 1), 60))
                continue
            raise
    raise last_err

def _looks_bad_fallback_text(text: str, response_mode: ResponseMode = "plain_text") -> bool:
    s = (text or "").strip()
    if len(s) < (2 if response_mode == "json" else 20):
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
                                    response_mode: ResponseMode = "plain_text"):
    if not config.OPENROUTER_API_KEY:
        return None
    token_cap = 5000 if response_mode == "json" else 700
    timeout = 30 if response_mode == "json" else 12
    t0 = time.time()
    status_code = None
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": config.OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": min(int(max_tokens or 400), token_cap),
                "temperature": min(float(temperature or 0.7), 0.8),
            },
            timeout=timeout,
        )
        status_code = r.status_code
        if r.status_code != 200:
            _log_openrouter_fallback(origin_provider, reason, False, status_code,
                                     int((time.time() - t0) * 1000))
            return None
        text = _as_text(r.json()["choices"][0]["message"]["content"])
        if not text or _looks_bad_fallback_text(text, response_mode=response_mode):
            _log_openrouter_fallback(origin_provider, "bad_output", False, status_code,
                                     int((time.time() - t0) * 1000))
            return None
        _log_openrouter_fallback(origin_provider, reason, True, status_code,
                                 int((time.time() - t0) * 1000))
        return text.strip()
    except Exception as e:
        err_type = type(e).__name__
        _log_openrouter_fallback(origin_provider, err_type, False, status_code,
                                 int((time.time() - t0) * 1000))
        return None

def _gen_groq(prompt, max_tokens, temperature):
    if not config.GROQ_API_KEY:
        raise Exception("no groq")
    r = _post("https://api.groq.com/openai/v1/chat/completions",
        {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"},
        {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
         "max_tokens": max_tokens, "temperature": temperature},
        40, "groq")
    return r.json()["choices"][0]["message"]["content"]

def _gen_cf(prompt, max_tokens):
    if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
        raise Exception("no cf")
    r = _post(f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct",
        {"Authorization": f"Bearer {config.CF_API_TOKEN}", "Content-Type": "application/json"},
        {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
        40, "cf")
    return _as_text(r.json().get("result", {}).get("response"))

# ---------- circuit breaker для 429 ----------
_COOLDOWN_SEC = 300  # 5 минут - на столько провайдер уходит в конец очереди после 429
_cooldowns = {}  # provider -> ts до которого он считается недоступным

def _mark_cooldown(name, err):
    if "429" in str(err) or "Too Many Requests" in str(err):
        _cooldowns[name] = time.time() + _COOLDOWN_SEC

def _is_cooling(name):
    return _cooldowns.get(name, 0) > time.time()

def _reorder_for_cooldown(order):
    """Провайдеров на cooldown (недавний 429) отодвигаем в конец, чтобы не терять
    время на заведомо неудачный запрос перед рабочим fallback-ом."""
    if not any(_is_cooling(n) for n in order):
        return order
    return tuple(sorted(order, key=lambda n: _is_cooling(n)))

def _friendly(errs):
    joined = "; ".join(errs)
    _log.warning("LLM chain failed: %s", secure.redact(joined))
    if "429" in joined or "Too Many Requests" in joined or "rate" in joined.lower():
        return "⏳ ИИ временно перегружен — подожди минуту и попробуй снова."
    return "⚠️ ИИ временно недоступен — попробуй снова через пару минут."

DEFAULT_ORDER  = ("gemini", "groq", "cf")
# Чат: Gemini первым — лучше поддерживает диалог, свободный и живой стиль
CHAT_ORDER     = ("gemini", "groq", "cf")
# Грамматика/быстрые задачи: Groq (Llama-70b) первым — скорость, structured output
GRAMMAR_ORDER  = ("groq", "gemini", "cf")
# Досуг/рекомендации: Gemini первым — богатое знание культуры, кино, музыки, путешествий
LEISURE_ORDER  = ("gemini", "groq", "cf")

# Явные пресеты: позволяют приоритизировать конкретный провайдер, не меняя код вызова по всему проекту.
PROVIDER_ORDER = {
    "cf": ("cf", "gemini", "groq"),
    "groq": ("groq", "gemini", "cf"),
    "gemini": ("gemini", "groq", "cf"),
}

# --- тиры: маршрутизация по задаче ---
# cheap  → Groq первым (грамматика, переводы, простые lookup-и)
# smart  → Gemini первым (чат, рецепты, гардероб, мотивация — требуют рассуждений)
# leisure → Gemini первым (досуг, путешествия, рекомендации — требуют знания мира)
TIERS = {
    "cheap":   (GRAMMAR_ORDER, None),
    "smart":   (DEFAULT_ORDER, None),
    "leisure": (LEISURE_ORDER, None),
}

def _resolve(tier, order, route=None):
    """Явный order имеет приоритет; иначе берём порядок из тира.
    route позволяет принудительно поставить конкретного провайдера первым."""
    if order is not None:
        return tuple(n for n in order if n in PROVIDER_ORDER or n in DEFAULT_ORDER)
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

def llm(prompt, max_tokens=1200, temperature=0.7, order=None, tier=None, module="", route=None,
        fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
        response_mode: ResponseMode = "plain_text", fallback_policy=None,
        allow_personal_openrouter=False):
    if not module:
        module = _caller_module()
    policy = _coerce_policy(fallback_allowed, privacy_level, response_mode, fallback_policy,
                            allow_personal_openrouter)
    order = _resolve(tier, order, route=route)
    order = _reorder_for_cooldown(order)
    calls = {
        "gemini": lambda: _gen_gemini(prompt, max_tokens, temperature),
        "groq": lambda: _gen_groq(prompt, max_tokens, temperature),
        "cf": lambda: _gen_cf(prompt, max_tokens),
    }
    errs = []
    temporary_errs = []
    for name in order:
        if name == "openrouter":
            continue
        t0 = time.time()
        try:
            out = _as_text(calls[name]())
            if out and out.strip():
                ms = int((time.time() - t0) * 1000)
                _log_cost(name, name, prompt, out, module, ms=ms, ok=True)
                return out
        except Exception as e:
            _mark_cooldown(name, e)
            errs.append(f"{name}:{e}")
            if _is_temporary_exception(e):
                temporary_errs.append((name, e))
    if policy.openrouter_allowed and temporary_errs:
        origin, err = temporary_errs[0]
        reason = getattr(err, "error_type", type(err).__name__)
        _log.warning("LLM temporary failure; trying OpenRouter fallback: provider=%s reason=%s", origin, reason)
        out = _openrouter_plain_text_fallback(prompt, max_tokens, temperature, origin, reason,
                                              response_mode=policy.response_mode)
        if out:
            _log_cost("openrouter_fallback", config.OPENROUTER_MODEL, "", out, module, ok=True)
            return out
        raise Exception(LOCAL_FALLBACK_TEXT)
    _friendly_msg = _friendly(errs)
    try:
        import tracking
        tracking.log_error("llm", "; ".join(errs)[:200] or _friendly_msg, kind="all-providers-failed")
    except Exception:
        pass
    raise Exception(_friendly_msg)

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
    raw = re.sub(r"```(json)?", "", raw or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    # пытаемся распарсить в несколько шагов, от мягкого к агрессивному
    for attempt in (
        lambda s: json.loads(s, strict=False),
        lambda s: json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s), strict=False),
        lambda s: json.loads(_repair_inner_quotes(s), strict=False),
        lambda s: json.JSONDecoder(strict=False).raw_decode(s)[0],
        lambda s: json.JSONDecoder(strict=False).raw_decode(_repair_inner_quotes(s))[0],
    ):
        try:
            parsed = attempt(raw)
        except Exception:
            continue
        # Вызывающие ждут JSON-объект (dict). Модель иногда отдаёт строку/массив/число -
        # не отдаём такое наружу, иначе p["..."] / p.get(...) падают вне try у вызывающего.
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0]
    raise ValueError("json_parse_failed")


def llm_json(prompt, max_tokens=1200, order=None, tier=None, module="", route=None,
             fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
             allow_personal_openrouter=False, fallback_policy=None):
    if not module:
        module = _caller_module()
    raw = llm(prompt + "\n\nВерни ТОЛЬКО валидный JSON, без markdown. "
                       "Внутри строковых значений НЕ используй двойные кавычки - "
                       "вместо них используй « » или одинарные.", max_tokens, 0.7, order, tier, module, route,
              fallback_allowed=fallback_allowed, privacy_level=privacy_level, response_mode="json",
              fallback_policy=fallback_policy, allow_personal_openrouter=allow_personal_openrouter)
    try:
        return _parse_json_response(raw)
    except ValueError:
        pass

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
    except Exception:
        try:
            import tracking
            tracking.log_error("llm", "Не удалось разобрать JSON", kind="json-parse")
        except Exception:
            pass
    raise Exception("Не удалось разобрать ответ ИИ (JSON). Попробуй ещё раз.")

CHAT_SYSTEM = f"""Ты помощник.

ОТВЕЧАЙ КОРОТКО: до 8 строк.
ФОРМАТ БЕЗ HTML, MARKDOWN И ЭМОДЗИ:
1. Первая строка - короткий заголовок без эмодзи.
2. Пустая строка.
3. Одно короткое предложение-пояснение.
4. Если нужно выделить важный фрагмент, дай его отдельным абзацем с префиксом ">".
5. Перед списком поставь короткий логичный заголовок с двоеточием: "Что важно:", "Что сделать:", "Почему:" или другой подходящий по смыслу.
6. Маркированный список с дефисами.
7. Перед последней строкой всегда должна быть пустая строка.
8. Последняя строка - короткий итог без курсива, с точкой и без вводных ярлыков вроде "Последний совет:", "Итог:", "Важно:".

СТИЛЬ:
- Сначала суть, потом детали.
- Пиши по-русски, если не просят другой язык.
- Не задавай лишних вопросов и не растягивай ответ."""

def _chat_system(cid=None):
    """Системный промпт чата с лагом-принципами текущего пользователя (per-user)."""
    if cid is None:
        return CHAT_SYSTEM
    import memory
    items = memory.get_lagom(cid)
    if not items:
        return CHAT_SYSTEM
    block = "Лагом-установки пользователя (ориентир тона и ценностей):\n" + \
            "\n".join(f"• {it}" for it in items[:12])
    return CHAT_SYSTEM + "\n\n" + block

def _chat(provider, history, system):
    if provider == "gemini":
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in history]
        r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={config.GEMINI_API_KEY}",
            {}, {"system_instruction": {"parts": [{"text": system}]}, "contents": contents,
                 "generationConfig": {"maxOutputTokens": 700, "temperature": 0.8, "thinkingConfig": {"thinkingBudget": 0}}}, 40, "gemini")
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    if provider == "groq":
        if not config.GROQ_API_KEY:
            raise Exception("no groq")
        r = _post("https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"},
            {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": system}] + history,
             "max_tokens": 700, "temperature": 0.8}, 40, "groq")
        return r.json()["choices"][0]["message"]["content"]
    if provider == "cf":
        if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
            raise Exception("no cf")
        r = _post(f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct",
            {"Authorization": f"Bearer {config.CF_API_TOKEN}", "Content-Type": "application/json"},
            {"messages": [{"role": "system", "content": system}] + history, "max_tokens": 700}, 40, "cf")
        return _as_text(r.json().get("result", {}).get("response"))

def chat_chain(history, cid=None):
    system = _chat_system(cid)
    errs = []
    prompt_len = sum(len(m.get("content", "")) for m in history)
    for p in _reorder_for_cooldown(CHAT_ORDER):
        try:
            out = _as_text(_chat(p, history, system))
            if out and out.strip():
                _log_cost(p, p, "c" * prompt_len, out, "assistant")
                return out
        except Exception as e:
            _mark_cooldown(p, e)
            errs.append(f"{p}:{e}")
    raise Exception(_friendly(errs))


# --- async-обёртки для вызова из async-обработчиков без блокировки event loop ---
async def allm(prompt, max_tokens=1200, temperature=0.7, order=None, tier=None, route=None, module="",
               fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
               response_mode: ResponseMode = "plain_text", fallback_policy=None):
    return await asyncio.to_thread(
        llm, prompt, max_tokens, temperature, order, tier, module, route,
        fallback_allowed, privacy_level, response_mode, fallback_policy,
    )

async def allm_json(prompt, max_tokens=1200, order=None, tier=None, route=None, module="",
                    fallback_allowed=False, privacy_level: PrivacyLevel = "personal",
                    allow_personal_openrouter=False, fallback_policy=None):
    return await asyncio.to_thread(
        llm_json, prompt, max_tokens, order, tier, module, route,
        fallback_allowed, privacy_level, allow_personal_openrouter, fallback_policy,
    )

async def achat_chain(history, cid=None):
    return await asyncio.to_thread(chat_chain, history, cid)
