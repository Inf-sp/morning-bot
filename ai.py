import asyncio
import inspect
import logging
import re
import json
import time
import requests
import config
import store
import secure

_log = logging.getLogger(__name__)

# ---------- Cost logger ----------
_COST_MAX = 500  # максимум записей в rolling-буфере


def _log_cost(provider: str, model: str, prompt: str, result: str, module: str = ""):
    """Добавить запись о LLM-вызове в rolling-буфер (хранится в store)."""
    try:
        tokens = (len(prompt) + len(result or "")) // 4
        entry = {
            "ts": int(time.time()),
            "provider": provider,
            "model": model or "",
            "tokens": tokens,
            "module": module or "",
        }
        buf = store._load(config.COST_LOG_KEY).get("log", [])
        buf.append(entry)
        store._save(config.COST_LOG_KEY, {"log": buf[-_COST_MAX:]})
    except Exception:
        pass  # логирование не должно ломать основной поток


def get_cost_log() -> list:
    """Вернуть список всех сохранённых записей расходов."""
    try:
        return store._load(config.COST_LOG_KEY).get("log", [])
    except Exception:
        return []

def _post(url, headers, payload, timeout, name):
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code != 200:
        # тело ошибки в логи (видно причину), но без секретов
        body = secure.redact((r.text or "")[:300])
        raise Exception(f"{name} {r.status_code}: {body}")
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
def _gen_claude(prompt, max_tokens, model=None):
    if not config.ANTHROPIC_API_KEY:
        raise Exception("no claude")
    r = _post("https://api.anthropic.com/v1/messages",
        {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        {"model": model or config.ANTHROPIC_MODEL, "max_tokens": max_tokens,
         "messages": [{"role": "user", "content": prompt}]},
        60, "claude")
    return r.json()["content"][0]["text"]

def _gen_gemini(prompt, max_tokens, temperature):
    r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={config.GEMINI_API_KEY}",
        {}, {"contents": [{"parts": [{"text": prompt}]}],
             "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature, "thinkingConfig": {"thinkingBudget": 0}}},
        30, "gemini")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

def _gen_openai(prompt, max_tokens, temperature):
    if not config.OPENAI_API_KEY:
        raise Exception("no openai")
    r = _post("https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"},
        {"model": config.OPENAI_MODEL, "messages": [{"role": "user", "content": prompt}],
         "max_tokens": max_tokens, "temperature": temperature},
        40, "openai")
    return r.json()["choices"][0]["message"]["content"]

def _gen_openrouter(prompt, max_tokens, temperature):
    if not config.OPENROUTER_API_KEY:
        raise Exception("no openrouter")
    r = _post("https://openrouter.ai/api/v1/chat/completions",
        {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "Content-Type": "application/json"},
        {"model": config.OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}],
         "max_tokens": max_tokens, "temperature": temperature},
        40, "openrouter")
    return r.json()["choices"][0]["message"]["content"]

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

def _friendly(errs):
    joined = "; ".join(errs)
    _log.warning("LLM chain failed: %s", secure.redact(joined))
    if "429" in joined or "Too Many Requests" in joined or "rate" in joined.lower():
        return "⏳ ИИ временно перегружен — подожди минуту и попробуй снова."
    return "⚠️ ИИ временно недоступен — попробуй снова через пару минут."

DEFAULT_ORDER  = ("claude", "openai", "gemini", "openrouter", "groq", "cf")
# Чат: Gemini первым — лучше поддерживает диалог, свободный и живой стиль
CHAT_ORDER     = ("gemini", "claude", "openrouter", "groq", "openai", "cf")
# Грамматика/быстрые задачи: Groq (Llama-70b) первым — скорость, structured output
GRAMMAR_ORDER  = ("groq", "gemini", "claude", "openrouter", "openai", "cf")
# Досуг/рекомендации: Gemini первым — богатое знание культуры, кино, музыки, путешествий
LEISURE_ORDER  = ("gemini", "openrouter", "claude", "openai", "groq", "cf")

# Явные пресеты: позволяют приоритизировать конкретный провайдер, не меняя код вызова по всему проекту.
PROVIDER_ORDER = {
    "claude": DEFAULT_ORDER,
    "openai": ("openai", "claude", "gemini", "openrouter", "groq", "cf"),
    "openrouter": ("openrouter", "claude", "openai", "gemini", "groq", "cf"),
    "cf": ("cf", "claude", "openai", "gemini", "openrouter", "groq"),
    "groq": ("groq", "gemini", "claude", "openrouter", "openai", "cf"),
    "gemini": ("gemini", "claude", "openrouter", "groq", "openai", "cf"),
}

# --- тиры: маршрутизация по задаче ---
# cheap  → Groq первым (грамматика, переводы, простые lookup-и; Claude Haiku если дойдёт)
# smart  → Claude первым (чат, рецепты, гардероб, мотивация — требуют рассуждений)
# leisure → Gemini первым (досуг, путешествия, рекомендации — требуют знания мира)
TIERS = {
    "cheap":   (GRAMMAR_ORDER, config.GRAMMAR_MODEL),
    "smart":   (DEFAULT_ORDER, None),
    "leisure": (LEISURE_ORDER, None),
}

def _resolve(tier, order, claude_model, route=None):
    """Явные order/claude_model имеют приоритет; иначе берём орден/модель из тира.
    route позволяет принудительно поставить конкретного провайдера первым."""
    if order is not None or claude_model is not None:
        return order or DEFAULT_ORDER, claude_model
    if route:
        return PROVIDER_ORDER.get(route, DEFAULT_ORDER), None
    o, m = TIERS.get(tier or "smart", (DEFAULT_ORDER, None))
    return o, m

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

def llm(prompt, max_tokens=1200, temperature=0.7, order=None, claude_model=None, tier=None, module="", route=None):
    if not module:
        module = _caller_module()
    order, claude_model = _resolve(tier, order, claude_model, route=route)
    calls = {
        "claude": lambda: _gen_claude(prompt, max_tokens, claude_model),
        "openai": lambda: _gen_openai(prompt, max_tokens, temperature),
        "gemini": lambda: _gen_gemini(prompt, max_tokens, temperature),
        "openrouter": lambda: _gen_openrouter(prompt, max_tokens, temperature),
        "groq": lambda: _gen_groq(prompt, max_tokens, temperature),
        "cf": lambda: _gen_cf(prompt, max_tokens),
    }
    errs = []
    for name in order:
        try:
            out = _as_text(calls[name]())
            if out and out.strip():
                _log_cost(name, claude_model if name == "claude" else name, prompt, out, module)
                return out
        except Exception as e:
            errs.append(f"{name}:{e}")
    raise Exception(_friendly(errs))

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

def llm_json(prompt, max_tokens=1200, order=None, claude_model=None, tier=None, module="", route=None):
    if not module:
        module = _caller_module()
    raw = llm(prompt + "\n\nВерни ТОЛЬКО валидный JSON, без markdown. "
                       "Внутри строковых значений НЕ используй двойные кавычки - "
                       "вместо них используй « » или одинарные.", max_tokens, 0.7, order, claude_model, tier, module, route)
    raw = re.sub(r"```(json)?", "", raw).strip()
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
            return attempt(raw)
        except Exception:
            continue
    # последний шанс - пустой dict, чтобы вызывающий показал понятную ошибку
    raise Exception("Не удалось разобрать ответ ИИ (JSON). Попробуй ещё раз.")

CHAT_SYSTEM = f"""Ты помощник.

ОТВЕЧАЙ КОРОТКО: 3-6 строк максимум.
ФОРМАТ:
1. Первая строка - одно эмодзи + <b>жирный заголовок</b>.
2. Дальше - список или 2-3 коротких пункта.
3. Только HTML: <b>, <i>, <code>. Markdown не нужен.
4. Никаких вводных, никакого «конечно», «разумеется».

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
    if provider == "claude":
        if not config.ANTHROPIC_API_KEY:
            raise Exception("no claude")
        r = _post("https://api.anthropic.com/v1/messages",
            {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": "claude-sonnet-4-6", "max_tokens": 700, "system": system, "messages": history}, 60, "claude")
        return r.json()["content"][0]["text"]
    if provider == "gemini":
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in history]
        r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={config.GEMINI_API_KEY}",
            {}, {"system_instruction": {"parts": [{"text": system}]}, "contents": contents,
                 "generationConfig": {"maxOutputTokens": 700, "temperature": 0.8, "thinkingConfig": {"thinkingBudget": 0}}}, 40, "gemini")
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise Exception("no openai")
        r = _post("https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"},
            {"model": config.OPENAI_MODEL, "messages": [{"role": "system", "content": system}] + history,
             "max_tokens": 700, "temperature": 0.8}, 40, "openai")
        return r.json()["choices"][0]["message"]["content"]
    if provider == "openrouter":
        if not config.OPENROUTER_API_KEY:
            raise Exception("no openrouter")
        r = _post("https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            {"model": config.OPENROUTER_MODEL, "messages": [{"role": "system", "content": system}] + history,
             "max_tokens": 700, "temperature": 0.8}, 40, "openrouter")
        return r.json()["choices"][0]["message"]["content"]
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
    for p in CHAT_ORDER:
        try:
            out = _as_text(_chat(p, history, system))
            if out and out.strip():
                _log_cost(p, p, "c" * prompt_len, out, "assistant")
                return out
        except Exception as e:
            errs.append(f"{p}:{e}")
    raise Exception(_friendly(errs))


# --- async-обёртки для вызова из async-обработчиков без блокировки event loop ---
async def allm(prompt, max_tokens=1200, temperature=0.7, order=None, claude_model=None, tier=None, route=None, module=""):
    return await asyncio.to_thread(llm, prompt, max_tokens, temperature, order, claude_model, tier, module, route)

async def allm_json(prompt, max_tokens=1200, order=None, claude_model=None, tier=None, route=None, module=""):
    return await asyncio.to_thread(llm_json, prompt, max_tokens, order, claude_model, tier, module, route)

async def achat_chain(history, cid=None):
    return await asyncio.to_thread(chat_chain, history, cid)
