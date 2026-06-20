import re
import json
import requests
import config

def _post(url, headers, payload, timeout, name):
    r = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code != 200:
        # покажем тело ошибки в логах - так видно причину (особенно 400)
        body = (r.text or "")[:300]
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
def _gen_claude(prompt, max_tokens):
    if not config.ANTHROPIC_API_KEY:
        raise Exception("no claude")
    r = _post("https://api.anthropic.com/v1/messages",
        {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        {"model": "claude-sonnet-4-6", "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
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
    print("LLM chain failed:", joined)  # в логи Railway
    if "429" in joined or "Too Many Requests" in joined or "rate" in joined.lower():
        return "⏳ Сейчас слишком много запросов к ИИ - бесплатные лимиты исчерпаны. Подожди минуту и попробуй снова."
    return "⚠️ ИИ временно недоступен. Попробуй ещё раз через пару минут."

DEFAULT_ORDER = ("claude", "openai", "gemini", "openrouter", "groq", "cf")
LEARN_ORDER = ("openai", "claude", "gemini", "openrouter", "groq", "cf")

def llm(prompt, max_tokens=1200, temperature=0.7, order=None):
    order = order or DEFAULT_ORDER
    calls = {
        "claude": lambda: _gen_claude(prompt, max_tokens),
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
                return out
        except Exception as e:
            errs.append(f"{name}:{e}")
    raise Exception(_friendly(errs))

def llm_json(prompt, max_tokens=1200, order=None):
    raw = llm(prompt + "\n\nВерни ТОЛЬКО валидный JSON, без markdown.", max_tokens, 0.7, order)
    raw = re.sub(r"```(json)?", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError:
        try:
            fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
            return json.loads(fixed, strict=False)
        except json.JSONDecodeError:
            return json.JSONDecoder(strict=False).raw_decode(raw)[0]

CHAT_SYSTEM = f"""Ты личный ассистент в Telegram.
Собеседник - инженер, дизайнер (UI/UX, графика), фотограф. Живёт в Нидерландах. Учит нидерландский (B1) и английский. У него СДВГ.

Формат ответа (строго, под ADHD):
- Красиво и аккуратно оформлено, легко читается. Короткие строки, чёткая структура.
- Сначала главное, потом детали. Без воды и длинных вступлений.
- Списки (-, •) если уместно; для сравнения/выбора можно таблицу.
- Максимум ОДИН эмодзи на весь ответ (как навигация, не для украшения).
- НЕ предлагай «развернуть» и не задавай лишних встречных вопросов - просто дай хороший ответ.
- Тон умного коллеги: прямо, конкретно. Короткое тире -, не длинное. По-русски, если не пишут иначе.
- Не знаешь - честно скажи, не выдумывай. Без обращения по имени.

{config.LAGOM}"""

def _chat(provider, history):
    if provider == "claude":
        if not config.ANTHROPIC_API_KEY:
            raise Exception("no claude")
        r = _post("https://api.anthropic.com/v1/messages",
            {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {"model": "claude-sonnet-4-6", "max_tokens": 700, "system": CHAT_SYSTEM, "messages": history}, 60, "claude")
        return r.json()["content"][0]["text"]
    if provider == "gemini":
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in history]
        r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={config.GEMINI_API_KEY}",
            {}, {"system_instruction": {"parts": [{"text": CHAT_SYSTEM}]}, "contents": contents,
                 "generationConfig": {"maxOutputTokens": 700, "temperature": 0.8, "thinkingConfig": {"thinkingBudget": 0}}}, 40, "gemini")
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise Exception("no openai")
        r = _post("https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"},
            {"model": config.OPENAI_MODEL, "messages": [{"role": "system", "content": CHAT_SYSTEM}] + history,
             "max_tokens": 700, "temperature": 0.8}, 40, "openai")
        return r.json()["choices"][0]["message"]["content"]
    if provider == "openrouter":
        if not config.OPENROUTER_API_KEY:
            raise Exception("no openrouter")
        r = _post("https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            {"model": config.OPENROUTER_MODEL, "messages": [{"role": "system", "content": CHAT_SYSTEM}] + history,
             "max_tokens": 700, "temperature": 0.8}, 40, "openrouter")
        return r.json()["choices"][0]["message"]["content"]
    if provider == "groq":
        if not config.GROQ_API_KEY:
            raise Exception("no groq")
        r = _post("https://api.groq.com/openai/v1/chat/completions",
            {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"},
            {"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": CHAT_SYSTEM}] + history,
             "max_tokens": 700, "temperature": 0.8}, 40, "groq")
        return r.json()["choices"][0]["message"]["content"]
    if provider == "cf":
        if not (config.CF_API_TOKEN and config.CF_ACCOUNT_ID):
            raise Exception("no cf")
        r = _post(f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct",
            {"Authorization": f"Bearer {config.CF_API_TOKEN}", "Content-Type": "application/json"},
            {"messages": [{"role": "system", "content": CHAT_SYSTEM}] + history, "max_tokens": 700}, 40, "cf")
        return _as_text(r.json().get("result", {}).get("response"))

def chat_chain(history):
    errs = []
    for p in ("claude", "openai", "gemini", "openrouter", "groq", "cf"):
        try:
            out = _as_text(_chat(p, history))
            if out and out.strip():
                return out
        except Exception as e:
            errs.append(f"{p}:{e}")
    raise Exception(_friendly(errs))