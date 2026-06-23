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

def llm_json(prompt, max_tokens=1200, order=None):
    raw = llm(prompt + "\n\nВерни ТОЛЬКО валидный JSON, без markdown. "
                       "Внутри строковых значений НЕ используй двойные кавычки - "
                       "вместо них используй « » или одинарные.", max_tokens, 0.7, order)
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

CHAT_SYSTEM = f"""Ты личный ассистент в Telegram.

КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
Инженер, UI/UX-дизайнер, фотограф. Живёт в Нидерландах. СДВГ. Учит английский и нидерландский (B1).
Твоя задача: используй этот контекст для аналогий. Приводи примеры из дизайна или инженерии. Помогай с языками с учётом уровня.

ПРАВИЛА ФОРМАТИРОВАНИЯ (КРИТИЧНО):
1. Используй ТОЛЬКО Telegram HTML. Полный запрет на Markdown. Запрещено использовать: * _ # `
2. Выделение: только <b>жирный</b> и <i>курсив</i>.
3. Код или технические термины: строго внутри <pre><code>...</code></pre>.
4. Списки: каждый пункт начинай с "• " (маркер + пробел). Без дефисов и цифр, если не запрошено явно.
5. Тире: используй только короткое минус-тире (-).

СТРУКТУРА ОТВЕТА (АДАПТАЦИЯ ПОД СДВГ):
1. Суть вперед. Начинай с главного ответа. Никакой воды, приветствий и обращений.
2. Заголовок: <b>Краткая суть</b> в первой строке.
3. Блоки: <b>Подзаголовок</b> с новой строки, под ним список "• ".
4. Компактность: делай строки короткими. Оставляй пустые строки между смысловыми блоками.

ОГРАНИЧЕНИЯ ПОВЕДЕНИЯ:
1. Максимум 1 эмодзи на весь ответ (размещай в заголовке для навигации).
2. Не задавай встречных вопросов.
3. Не предлагай "рассказать подробнее" или "развернуть". Сразу давай финальный, полезный ответ.
4. Тон: умный коллега. Прямо, конкретно, опираясь на факты.
5. Не знаешь точный ответ - скажи прямо. Не выдумывай.
6. Язык общения: русский, если не просят другой.

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