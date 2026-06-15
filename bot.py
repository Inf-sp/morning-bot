import os
import re
import json
import time
import requests
from datetime import datetime, date
from zoneinfo import ZoneInfo
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, BotCommand)
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# --- Keys ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

TZ = ZoneInfo("Europe/Amsterdam")

# --- Storage ---
challenge_state = {}
chat_history = {}
lesson_answers = {}   # chat_id -> ответ мини-теста
text_router_state = {}  # chat_id -> уровень меню
add_wardrobe_mode = {}  # chat_id -> True когда ждём список одежды
game_state = {}         # chat_id -> {answer, quote}
pending_input = {}      # chat_id -> "diary" | "plant" | "fav_movie" | "fav_book" | "artist"
SETTINGS_FILE = "settings.json"
NOTES_FILE = "notes.json"
LEVELS_FILE = "levels.json"       # {chat_id: {язык: уровень}}
WARDROBE_FILE = "wardrobe.json"

DEFAULT_CITY = {"lat": 52.63, "lon": 4.74, "city": "Алкмар"}

# Будущие поездки: впиши ("Название", "ГГГГ-ММ-ДД"). Прошедшие игнорируются.
TRIPS = [
    ("Грузия", "2026-04-16"),
    ("Мадейра + Азоры", "2026-05-01"),
]

# Любимые книги для цитат
BOOKS = "1984, Цветы для Элджернона, Машина времени, Остров доктора Моро, Марсианин"

# Короткие лагом-строки (реальные принципы, выбираются по дню, без выдумок)
LAGOM_LINES = [
    "Сейчас один шаг, не вся жизнь",
    "Не нужно идеально - нужно начать",
    "Я не ленивый, мозг так работает",
    "Остановись, выдохни, действуй",
    "Пауза сейчас - победа",
    "Фокус на хорошем",
    "Не все споры стоят нервов",
    "Чужие эмоции - не моя ответственность",
    "Перемены открывают возможности",
    "Скука - криптонит, создавай интерес",
    "Это состояние пройдёт",
    "Делаю лучшее из возможного сегодня",
]

def lagom_of_day():
    idx = datetime.now(TZ).timetuple().tm_yday % len(LAGOM_LINES)
    return LAGOM_LINES[idx]

LAGOM = """
Принципы Дмитрия (для тона, не цитировать дословно):
Сейчас один шаг, не вся жизнь. Не нужно идеально - нужно начать. Я не ленивый, мозг так работает.
Остановись, выдохни, действуй. Пауза - победа. Фокус на хорошем. Не все споры стоят нервов.
Чужие эмоции - не моя ответственность. Перемены открывают возможности. Книги и путешествия важнее вещей.
Скука - криптонит, создавай интерес. Это состояние пройдёт.
"""

STYLE_PROFILE = """
Стиль Дмитрия: современный минимализм с элементами скандинавского и японского casual.
Цель: выглядеть современно, собранно и расслабленно. Образ должен быть уместен в Амстердаме, Утрехте, Копенгагене, Стокгольме.
Палитра: чёрный, белый, серый, бежевый, оливковый, коричневый, тёмно-синий. Натуральные фактурные ткани. Свободный или прямой силуэт. Комфорт важнее формальности.
Контекст: одежда под велосипед, прогулки, путешествия и повседневную работу.
Любит: плотные футболки с высоким воротом, рубашки свободного кроя, ветровки и лёгкую верхнюю одежду, удобную обувь с массивным силуэтом, минималистичные аксессуары.
Избегать: узких вещей, агрессивных логотипов, кислотных цветов, офисного/делового стиля, чрезмерно спортивных образов, чёрного с головы до ног, смешивания цепочек между собой.
Опорные вещи (строй образы вокруг них): коричневая плотная рубашка Uniqlo, бежевая ветровка, оливковые брюки, белые кеды, Timberland, чёрные джинсы.
Правила: коричневая рубашка Uniqlo - только с кремовым/белым низом и нейтральными брюками. NB - активные выходы, Timberland - городской casual.
"""

TEMP_ZONES = """
ниже 5°C: флис + ветровка | 5-12: свитшот + ветровка | 12-18: рубашка/свитшот + ветровка с собой
18-23: футболка, рубашка сверху | выше 23: футболка | дождь >50%: ветровка | ветер >8 м/с: +слой
"""

# ---------- Storage: Postgres (с откатом в память) ----------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
_conn = None
_mem = {}  # откат, если базы нет

def _db():
    """Возвращает живое соединение или None если базы нет."""
    global _conn
    if not DATABASE_URL:
        return None
    try:
        if _conn is None or _conn.closed:
            import psycopg2
            _conn = psycopg2.connect(DATABASE_URL)
            _conn.autocommit = True
            with _conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value JSONB)")
        # проверка живости
        with _conn.cursor() as cur:
            cur.execute("SELECT 1")
        return _conn
    except Exception:
        try:
            import psycopg2
            _conn = psycopg2.connect(DATABASE_URL)
            _conn.autocommit = True
            with _conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value JSONB)")
            return _conn
        except Exception:
            return None

def _load(key):
    conn = _db()
    if conn is None:
        return dict(_mem.get(key, {}))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM kv WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else {}
    except Exception:
        return dict(_mem.get(key, {}))

def _save(key, data):
    conn = _db()
    if conn is None:
        _mem[key] = data
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kv (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, json.dumps(data, ensure_ascii=False))
            )
    except Exception:
        _mem[key] = data

def get_settings(chat_id):
    return _load(SETTINGS_FILE).get(str(chat_id), DEFAULT_CITY)

def set_settings(chat_id, lat, lon, city):
    d = _load(SETTINGS_FILE)
    d[str(chat_id)] = {"lat": lat, "lon": lon, "city": city}
    _save(SETTINGS_FILE, d)

def get_level(chat_id, language):
    return _load(LEVELS_FILE).get(str(chat_id), {}).get(language, "B1")

def set_level(chat_id, language, level):
    d = _load(LEVELS_FILE)
    d.setdefault(str(chat_id), {})[language] = level
    _save(LEVELS_FILE, d)

def load_wardrobe():
    w = _load(WARDROBE_FILE)
    if not w:
        # первый запуск: засеять из репозиторного wardrobe.json, если есть
        try:
            if os.path.exists(WARDROBE_FILE):
                with open(WARDROBE_FILE) as f:
                    seed = json.load(f)
                if seed:
                    _save(WARDROBE_FILE, seed)
                    return seed
        except Exception:
            pass
    return w

def save_wardrobe(w):
    _save(WARDROBE_FILE, w)

def merge_wardrobe(new_items: dict):
    """new_items: {категория: [вещи]} -> добавляет к существующему, без дублей."""
    w = load_wardrobe()
    added = 0
    for cat, items in new_items.items():
        cat = cat.lower().strip()
        w.setdefault(cat, [])
        for it in items:
            it = it.strip().lower()
            if it and it not in [x.lower() for x in w[cat]]:
                w[cat].append(it)
                added += 1
    save_wardrobe(w)
    return added

def wardrobe_to_text(w):
    return "\n".join(f"{c.capitalize()}: {', '.join(i)}" for c, i in w.items())

# Простые списки в одном kv-ключе: {chat_id: [...]}
def get_list(key, chat_id):
    return _load(key).get(str(chat_id), [])

def add_to_list(key, chat_id, item):
    d = _load(key)
    d.setdefault(str(chat_id), []).append(item)
    _save(key, d)

def set_list(key, chat_id, items):
    d = _load(key)
    d[str(chat_id)] = items
    _save(key, d)

FAVORITES_KEY = "favorites.json"   # любимые фильмы/книги
PLANTS_KEY = "plants.json"
DIARY_KEY = "diary.json"           # [{date, text}]
ARTISTS_KEY = "artists.json"

VISITED = ("Австрия, Беларусь, Бельгия, Великобритания, Венгрия, Германия, Греция, Дания, "
           "Испания, Италия, Латвия, Литва, Мальта, Мексика, Нидерланды, Норвегия, Польша, "
           "Португалия, Россия, Сербия, Сингапур, Словакия, Таиланд, Турция, Финляндия, Франция, "
           "Черногория, Чехия, Швеция, Эстония, Япония, Ватикан, Люксембург")

# ---------- LLM chain ----------

def _gen_claude(prompt, max_tokens):
    if not ANTHROPIC_API_KEY:
        raise Exception("no claude")
    r = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
        timeout=60)
    r.raise_for_status()
    return r.json()["content"][0]["text"]

def _gen_gemini(prompt, max_tokens, temperature):
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature, "thinkingConfig": {"thinkingBudget": 0}}},
        timeout=30)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

def _gen_groq(prompt, max_tokens, temperature):
    if not GROQ_API_KEY:
        raise Exception("no groq")
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}],
              "max_tokens": max_tokens, "temperature": temperature}, timeout=40)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _gen_cf(prompt, max_tokens):
    if not (CF_API_TOKEN and CF_ACCOUNT_ID):
        raise Exception("no cf")
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct",
        headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
        json={"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}, timeout=40)
    r.raise_for_status()
    return r.json()["result"]["response"]

def llm(prompt, max_tokens=1200, temperature=0.7):
    errs = []
    for name, call in (
        ("claude", lambda: _gen_claude(prompt, max_tokens)),
        ("gemini", lambda: _gen_gemini(prompt, max_tokens, temperature)),
        ("groq", lambda: _gen_groq(prompt, max_tokens, temperature)),
        ("cf", lambda: _gen_cf(prompt, max_tokens)),
    ):
        try:
            out = call()
            if out and out.strip():
                return out
        except Exception as e:
            errs.append(f"{name}:{e}")
    raise Exception("API недоступны: " + "; ".join(errs))

def llm_json(prompt, max_tokens=1200):
    raw = llm(prompt + "\n\nВерни ТОЛЬКО валидный JSON, без markdown и пояснений.", max_tokens, 0.7)
    raw = re.sub(r"```(json)?", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        raw = m.group(0)
    return json.loads(raw)

# ---------- Chat (free text) ----------

CHAT_SYSTEM = f"""Ты личный ассистент Дмитрия (DM) в Telegram.
Он инженер, дизайнер (UI/UX, графика), фотограф. Живёт в Нидерландах. Учит нидерландский (B1) и английский. У него СДВГ - давай структуру, короткие шаги.
Общайся как умный коллега: прямо, без воды и канцелярита, короткими предложениями, конкретно. Проверяй слабые идеи, предлагай альтернативу. Короткое тире -, не длинное. По-русски, если он не пишет иначе.
{LAGOM}"""

def _chat(provider, history):
    if provider == "claude":
        if not ANTHROPIC_API_KEY:
            raise Exception("no claude")
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 2048, "system": CHAT_SYSTEM, "messages": history}, timeout=60)
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    if provider == "gemini":
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in history]
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
            json={"system_instruction": {"parts": [{"text": CHAT_SYSTEM}]}, "contents": contents,
                  "generationConfig": {"maxOutputTokens": 3000, "temperature": 0.8, "thinkingConfig": {"thinkingBudget": 0}}}, timeout=40)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    if provider == "groq":
        if not GROQ_API_KEY:
            raise Exception("no groq")
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "system", "content": CHAT_SYSTEM}] + history,
                  "max_tokens": 2048, "temperature": 0.8}, timeout=40)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    if provider == "cf":
        if not (CF_API_TOKEN and CF_ACCOUNT_ID):
            raise Exception("no cf")
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.1-8b-instruct",
            headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
            json={"messages": [{"role": "system", "content": CHAT_SYSTEM}] + history, "max_tokens": 2048}, timeout=40)
        r.raise_for_status()
        return r.json()["result"]["response"]

def chat_chain(history):
    errs = []
    for p in ("claude", "gemini", "groq", "cf"):
        try:
            out = _chat(p, history)
            if out and out.strip():
                return out
        except Exception as e:
            errs.append(f"{p}:{e}")
    raise Exception("чат недоступен: " + "; ".join(errs))

# ---------- Weather ----------

EMOJI = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️", 45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌧️", 61: "🌦️", 63: "🌧️", 65: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "❄️", 80: "🌧️", 81: "🌧️", 95: "⛈️"
}
DESC = {
    0: "ясно", 1: "малооблачно", 2: "переменно", 3: "пасмурно", 45: "туман", 48: "туман",
    51: "морось", 53: "морось", 55: "морось", 61: "дождь", 63: "дождь", 65: "сильный дождь",
    71: "снег", 73: "снег", 75: "сильный снег", 80: "ливень", 81: "ливень", 95: "гроза"
}

def fetch_weather(lat, lon, days=2):
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weathercode",
        "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_probability_max,weathercode,windspeed_10m_max",
        "timezone": "Europe/Amsterdam", "wind_speed_unit": "ms", "forecast_days": days
    }, timeout=20)
    r.raise_for_status()
    return r.json()

def weather_block(data, day, city):
    d = data["daily"]
    code = d["weathercode"][day]
    emoji = EMOJI.get(code, "🌡️")
    desc = DESC.get(code, "")
    tmin, tmax = d["temperature_2m_min"][day], d["temperature_2m_max"][day]
    wind = d["windspeed_10m_max"][day]
    rain = d["precipitation_probability_max"][day]
    lines = [f"📍 {city}", f"{emoji} {desc}, {tmin:.0f}-{tmax:.0f}°C", f"💨 ветер до {wind:.0f} м/с"]
    if rain and rain >= 30:
        lines.append(f"🌧️ дождь {rain:.0f}%")
    return "\n".join(lines)

def trip_countdown():
    today = datetime.now(TZ).date()
    upcoming = []
    for name, ds in TRIPS:
        try:
            d = date.fromisoformat(ds)
            if d >= today:
                upcoming.append((d, name))
        except Exception:
            pass
    if not upcoming:
        return None
    upcoming.sort()
    d, name = upcoming[0]
    days = (d - today).days
    return f"✈️ До поездки ({name}): {days} дн."

# ---------- Generators ----------

def build_outfit_focus(weather_text, day_label):
    w = load_wardrobe()
    prompt = f"""Ты персональный стилист Дмитрия, не генератор случайных комплектов.

{STYLE_PROFILE}

Погода ({day_label}):
{weather_text}

Параметры: 179 см, ~65 кг, обувь 42.5, джинсы W31 L31.
Гардероб (используй ТОЛЬКО эти вещи, ничего не выдумывай):
{wardrobe_to_text(w)}

Температурные зоны:{TEMP_ZONES}

Учитывай при подборе: погоду и ветер (для Нидерландов критично), что образ для велосипеда и прогулок, цветовые сочетания, актуальные тренды 2026 (свободный/прямой силуэт), разнообразие. Собери законченный образ из 3-4 вещей, по возможности вокруг опорных предметов.

JSON:
{{
 "outfit": ["вещь 1","вещь 2","вещь 3","вещь 4"],
 "why": "1-2 предложения: почему образ работает - палитра, силуэт, комфорт для погоды и ветра",
 "focus": "один короткий конкретный совет на день с учётом СДВГ, без банальностей"
}}"""
    return llm_json(prompt, 800)

def build_morning_extras(day_label):
    prompt = f"""Сгенерируй для русскоговорящего пользователя (учит нидерландский B1):
JSON:
{{
 "dutch": "одно полезное нидерландское слово B1 с переводом, формат: woord — перевод",
 "quote": "короткая цитата или мысль (максимум 12 слов) из научной фантастики ({BOOKS}), с указанием книги в скобках"
}}"""
    return llm_json(prompt, 500)

def lesson_data(language, level="B1"):
    prompt = f"""Урок языка для русскоговорящего ученика уровня {level}. Язык: {language}.
Слово и фраза должны строго соответствовать уровню {level} (для A1-A2 простые, для B2-C2 сложнее), каждый раз разные.

JSON:
{{
 "word": "слово на {language}",
 "pron": "[транскрипция IPA в квадратных скобках, или пустая строка]",
 "meaning": "значение по-русски",
 "example": "предложение с этим словом на {language}",
 "example_tr": "перевод примера на русский",
 "phrase": "полезная разговорная фраза дня на {language}",
 "phrase_tr": "перевод фразы на русский",
 "wrong": "частая ошибочная версия этой фразы (как говорят НЕ надо)",
 "test_ru": "короткая русская фраза для мини-теста",
 "test_answer": "правильный перевод test_ru на {language}"
}}"""
    return llm_json(prompt, 900)

def format_lesson(language, flag, data, level="B1"):
    L = [f"{flag} {language.capitalize()} на сегодня ({level})", "", f"📝 {data.get('word','')}"]
    if data.get("pron"):
        L.append(data["pron"])
    L += [f"Значение: {data.get('meaning','')}", "",
          f"🎬 {data.get('example','')}", data.get("example_tr", ""), "",
          "🧠 Попробуй использовать это слово сегодня хотя бы раз.", "",
          "— — —", "", "💬 Фраза дня", data.get("phrase", ""), data.get("phrase_tr", "")]
    if data.get("wrong"):
        L += ["", "Не говори:", f"❌ {data['wrong']}", "Говори:", f"✅ {data.get('phrase','')}"]
    L += ["", "— — —", "", "⚡ Мини-тест", "Как сказать:", f"«{data.get('test_ru','')}»"]
    return "\n".join(L)

async def send_lesson(bot, chat_id, language, flag):
    level = get_level(chat_id, language)
    data = lesson_data(language, level)
    text = format_lesson(language, flag, data, level)
    lesson_answers[str(chat_id)] = data.get("test_answer", "")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Показать ответ", callback_data="lesson_answer")]])
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)

def generate_challenge(language, level="B1"):
    prompt = f"""Дай ОДНУ фразу на русском для перевода на {language}.
Уровень {level}: сложность строго под уровень. Бытовая или рабочая ситуация.
Выведи ТОЛЬКО русскую фразу, без кавычек."""
    return llm(prompt, 200, 1.0).strip()

def check_translation(language, ru, answer):
    prompt = f"""Ученик переводит с русского на {language}.
Русская фраза: {ru}
Перевод ученика: {answer}

Проверь коротко, без markdown:
1. Верно или ошибки
2. Правильный вариант и в чём ошибка
3. Более естественный вариант, если есть
Тон коллеги, по делу. Не обрывай."""
    return llm(prompt, 800, 0.4)

def generate_shopping_advice():
    w = load_wardrobe()
    prompt = f"""Ты стилист. Профиль клиента:
{STYLE_PROFILE}

Текущий гардероб:
{wardrobe_to_text(w)}

Проанализируй и предложи 4-5 вещей, которые ДОКУПИТЬ, чтобы поднять уровень гардероба и открыть больше сочетаний.
Учитывай тренды 2026, скандинавский/японский casual, что образы для велосипеда, прогулок и работы.
Для каждой вещи: что именно, и одна строка - почему она усилит гардероб.
Без markdown и звёздочек, просто текст. Заголовок: 🛍️ Что докупить."""
    return llm(prompt, 1000, 0.7)

def parse_wardrobe_list(text):
    w = load_wardrobe()
    cats = ", ".join(w.keys()) or "футболки, рубашки, свитшоты, верхняя одежда, брюки, обувь, носки, кепки, аксессуары"
    prompt = f"""Разбери список одежды и распредели по категориям.
Существующие категории: {cats}. Используй их, если подходит. Можно создать новую категорию при необходимости.

Список от пользователя:
{text}

Верни JSON: {{"категория": ["вещь", "вещь"], ...}}. Названия вещей короткие, в нижнем регистре."""
    return llm_json(prompt, 800)

# ---------- Модульные генераторы ----------

def wardrobe_analysis():
    w = load_wardrobe()
    prompt = f"""Ты стилист с прямым, иногда дерзким тоном. Профиль:
{STYLE_PROFILE}

Гардероб:
{wardrobe_to_text(w)}

Разбери коротко, без markdown и звёздочек:
🧠 Анализ гардероба
- Что дублируется
- Что устарело или выбивается из стиля
- Чего не хватает для большего числа сочетаний
Будь конкретным. Без воды."""
    return llm(prompt, 1000, 0.7)

def travel_suggest():
    prompt = f"""Дмитрий уже был в этих странах: {VISITED}.
Любит: путешествия важнее вещей, интеллектуальная атмосфера, города с характером, природа.
Предложи 4-5 НОВЫХ направлений (где он не был), коротко - почему именно ему зайдёт.
Без markdown и звёздочек. Заголовок: 🗺 Куда поехать."""
    return llm(prompt, 900, 0.8)

def content_recommend(kind, favorites):
    fav = ", ".join(favorites) if favorites else "1984, Цветы для Элджернона, Марсианин, умная фантастика"
    what = "фильмов или сериалов" if kind == "movie" else "книг"
    prompt = f"""Порекомендуй 4-5 {what} для человека с таким вкусом: {fav}.
Любит научную фантастику и интеллектуальное. Для каждого: название, год, одна строка - почему зайдёт.
Без markdown и звёздочек. Заголовок: {'🎬 Что посмотреть' if kind=='movie' else '📖 Что почитать'}."""
    return llm(prompt, 900, 0.8)

def game_data(language, level):
    prompt = f"""Игра "угадай персонажа/личность" на языке: {language}, уровень {level}.
Загадай известного персонажа или реального человека (кино, наука, история, музыка).
JSON:
{{
 "clues": "3-4 подсказки на {language}, каждая с новой строки, от сложной к лёгкой, без имени",
 "answer": "имя",
 "quote": "короткая цитата или интересный факт о нём"
}}"""
    return llm_json(prompt, 700)

def lagom_checkin(kind):
    if kind == "day":
        task = "Дневной чек-ин (14:00). Один короткий вопрос-настройка: как идёт день, что в фокусе. Тепло, прямо, без пафоса."
    else:
        task = "Вечерний разбор (20:00). Мягко предложи отделить 'что тревожило' от 'что реально произошло' и отпустить ненужное. 2-3 строки + один вопрос."
    prompt = f"{LAGOM}\n\n{task}\nБез markdown и звёздочек. По-русски."
    return llm(prompt, 400, 0.9)

def diary_reflect(entry):
    prompt = f"""Запись из эмоционального дневника Дмитрия: "{entry}"
Ответь как спокойный мини-психолог: 2-3 предложения поддержки и одна практичная мысль. Опирайся на его лагом по духу.
{LAGOM}
Без markdown и звёздочек."""
    return llm(prompt, 400, 0.8)

import random as _random

def fetch_current_temp(lat, lon):
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": lat, "longitude": lon, "current": "temperature_2m"}, timeout=15)
        return r.json()["current"]["temperature_2m"]
    except Exception:
        return None

def plany_extras():
    country = _random.choice([c.strip() for c in VISITED.split(",")])
    prompt = f"""Сгенерируй блоки для ежедневной сводки русскоязычного пользователя (учит нидерландский и английский).
JSON:
{{
 "place_country": "{country}",
 "place_text": "3-5 коротких интересных фактов про страну {country}, живо и нескучно",
 "fact": "новый интересный научный факт, одна строка",
 "word_ru": "русское слово дня (одно слово)",
 "word_nl": "перевод на нидерландский",
 "word_en": "перевод на английский",
 "example_nl": "пример с этим словом на нидерландском",
 "example_ru": "перевод примера на русский",
 "quote": "короткая цитата (до 14 слов) из книги {BOOKS}",
 "quote_book": "название книги, откуда цитата"
}}"""
    return llm_json(prompt, 1000)

# ---------- Send ----------

async def send_long(bot, chat_id, text):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000])

def assemble_morning(chat_id):
    s = get_settings(chat_id)
    data = fetch_weather(s["lat"], s["lon"], days=2)
    wblock = weather_block(data, 0, s["city"])
    of = build_outfit_focus(wblock, "сегодня")
    extras = build_morning_extras("сегодня")

    parts = ["☀️ Доброе утро, Дмитрий!", "", wblock, ""]
    parts.append("👕 Лук дня")
    parts.append(" + ".join(of.get("outfit", [])) + ".")
    parts += ["", "Почему работает:", of.get("why", ""), "",
              f"🧠 {of.get('focus','')}", "",
              f"🇳🇱 {extras.get('dutch','')}", "",
              f"📖 {extras.get('quote','')}", "",
              f"🌿 {lagom_of_day()}"]
    tc = trip_countdown()
    if tc:
        parts += ["", tc]
    return "\n".join(parts)

# ---------- Scheduled ----------

async def job_morning(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        await send_long(context.bot, CHAT_ID, assemble_morning(CHAT_ID))
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка сводки: {e}")

async def job_dutch(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        await send_lesson(context.bot, CHAT_ID, "нидерландский", "🇳🇱")
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка урока: {e}")

async def job_checkin_day(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        await context.bot.send_message(chat_id=CHAT_ID,
            text="🌤 Дим, что сейчас немного тревожит? Запиши - вечером проверим, что из этого реально случилось")
        pending_input[str(CHAT_ID)] = "diary"
    except Exception:
        pass

async def job_checkin_evening(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        msg = ("🌙 Дим, как вечер? Смотри: что тревожило и что реально было - часто две разные истории. "
               "Давай отпустим то, что уже неважно. Как сегодня с этим, заметил разницу?\n\nОтветь - запишу в дневник.")
        pending_input[str(CHAT_ID)] = "diary"
        await context.bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception:
        pass

async def job_weekly(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    entries = get_list(DIARY_KEY, CHAT_ID)
    diary = "; ".join(e["text"] for e in entries[-7:]) if entries else "нет записей"
    try:
        prompt = (f"Сделай тёплый короткий итог недели для Дмитрия. Записи дневника за неделю: {diary}. "
                  f"Формат: 📊 Итоги недели. 3-4 строки: инсайт недели, что получилось, мягкая настройка на следующую. "
                  f"{LAGOM} Без markdown.")
        await send_long(context.bot, CHAT_ID, llm(prompt, 500, 0.8))
    except Exception:
        pass

# ---------- Commands ----------

MENU = (
    "👕 Гардероб - лук, список, анализ, советы, добавить одежду\n"
    "📚 Обучение + игра - грамматика, перевод, игра-детектив, уровень\n"
    "🌤 Погода - сегодня и 7 дней + рубрика мира\n"
    "✈️ Путешествия - куда поехать, мои страны\n"
    "🧠 Мотивация - проверка дня, дневник\n"
    "🎬 Развлечения - фильмы, книги, концерты\n"
    "🌱 Растения - список и полив\n"
    "⚙️ Настройки - город и уровень языков"
)

ASSIST = "👨🏻‍💻 Вызов ассистента"

WELCOME = (
    "👨🏻‍💻 Вызов ассистента\n\n"
    "Что будем делать сегодня?\n"
    "Помогу с повседневными задачами, языками, гардеробом, путешествиями и поиском информации.\n\n"
    "💬 Напиши вопрос своими словами или выбери направление в меню ниже.\n\n"
    "Попробуй спросить:\n"
    "👕 Что надеть сегодня?\n"
    "🇳🇱 Объясни разницу между die и dat\n"
    "✈️ Куда поехать на выходные из Нидерландов?\n"
    "📚 Посоветуй книгу как «Цветы для Элджернона»\n"
    "🎬 Найди сериал похожий на The Last of Us\n"
    "🛒 Что докупить в гардероб?\n"
    "🌤 Какая погода на выходных?\n\n"
    "💡 Не знаешь, с чего начать? Расскажи, что сейчас в голове - задача, идея, проблема или вопрос. Помогу разобраться."
)

FIRST_RUN = (
    "👋 Привет! Я твой персональный помощник.\n\n"
    "Чем больше пользуешься, тем точнее становятся рекомендации по одежде, фильмам, путешествиям, языкам и решениям дня.\n\n"
    "Что хочешь попробовать первым? 🚀"
)

# --- Меню-дашборд ---
MAIN_KB = ReplyKeyboardMarkup([
    [ASSIST],
    ["🧭 Планы", "👕 Гардероб"],
    ["📚 Обучение + игра", "🌤 Погода"],
    ["✈️ Путешествия", "🧠 Мотивация"],
    ["🎬 Развлечения", "🌱 Растения"],
    ["⚙️ Настройки"],
], resize_keyboard=True)

WARDROBE_KB = ReplyKeyboardMarkup([
    [ASSIST],
    ["✨ Сгенерировать лук"],
    ["📊 Скучный список", "🧠 Анализ"],
    ["🛍️ Советы к покупке", "📤 Добавить одежду"],
    ["⬅️ Назад"],
], resize_keyboard=True)

LANG_KB = ReplyKeyboardMarkup([
    [ASSIST],
    ["🇳🇱 Нидерландский", "🇬🇧 Английский"],
    ["🕵️ Игра-детектив"],
    ["⚙️ Уровень языка"],
    ["⬅️ Назад"],
], resize_keyboard=True)
NL_KB = ReplyKeyboardMarkup([[ASSIST], ["📖 Грамматика NL", "⚡ Тренировка NL"], ["⬅️ Назад"]], resize_keyboard=True)
EN_KB = ReplyKeyboardMarkup([[ASSIST], ["📖 Грамматика EN", "⚡ Тренировка EN"], ["⬅️ Назад"]], resize_keyboard=True)

WEATHER_KB = ReplyKeyboardMarkup([[ASSIST], ["🌤 Сегодня", "📅 7 дней"], ["⬅️ Назад"]], resize_keyboard=True)
TRAVEL_KB = ReplyKeyboardMarkup([[ASSIST], ["🗺 Куда поехать", "🏳 Мои страны"], ["⬅️ Назад"]], resize_keyboard=True)
LAGOM_KB = ReplyKeyboardMarkup([
    [ASSIST],
    ["🌙 Проверка дня", "📊 Дневник"],
    ["🌿 Фраза дня"],
    ["⬅️ Назад"],
], resize_keyboard=True)
CONTENT_KB = ReplyKeyboardMarkup([
    [ASSIST],
    ["🎬 Что посмотреть", "📖 Что почитать"],
    ["❤️ Любимое", "🎤 Концерты"],
    ["⬅️ Назад"],
], resize_keyboard=True)
PLANTS_KB = ReplyKeyboardMarkup([[ASSIST], ["💧 Мои растения", "🗓 Полив"], ["➕ Добавить растение"], ["⬅️ Назад"]], resize_keyboard=True)
CONCERTS_KB = ReplyKeyboardMarkup([[ASSIST], ["🎤 Мои артисты", "➕ Добавить артиста"], ["⬅️ Назад"]], resize_keyboard=True)
SETTINGS_KB = ReplyKeyboardMarkup([[ASSIST], ["📍 Сменить город", "🌐 Уровень языков"], ["⬅️ Назад"]], resize_keyboard=True)
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


async def start(update, context):
    await update.message.reply_text(f"Привет! Твой ассистент DM.\n\n{MENU}", reply_markup=MAIN_KB)


_WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]

def _esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def weather_icon(code, temp, rain, wind_kmh):
    if code in (95, 96, 99):
        return "🌩️"
    if code in (71, 73, 75, 77, 85, 86):
        return "❄️"
    if temp is not None and temp > 30 and rain >= 30:
        return "☀️🌧️"
    if rain >= 30:
        return "🌧️"
    if wind_kmh >= 40:
        return "💨"
    if code in (0, 1):
        return "☀️"
    return "☁️"

async def plany_command(update, context):
    cid = update.effective_chat.id
    await update.message.reply_text("Собираю сводку дня...")
    try:
        s = get_settings(cid)
        data = fetch_weather(s["lat"], s["lon"], 2)
        cur = data["current"]
        d = data["daily"]
        temp = cur["temperature_2m"]
        code = cur["weathercode"]
        rain = d["precipitation_probability_max"][0] or 0
        wind_kmh = (d["windspeed_10m_max"][0] or 0) * 3.6
        icon = weather_icon(code, temp, rain, wind_kmh)

        of = build_outfit_focus(weather_block(data, 0, s["city"]), "сегодня")
        ex = plany_extras()

        now = datetime.now(TZ)
        header = f"{_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS[now.month-1]}"

        L = [f"🧭 <b>Планы | {header}</b>", ""]
        # Погода
        L.append("<b>Погода</b>")
        L.append(f"{icon} {_esc(s['city'])}: {temp:+.0f}°C • 🌧 {rain:.0f}% • 💨 {wind_kmh:.0f} км/ч")
        # Место дня
        L += ["", f"🗺️ <b>Место дня: {_esc(ex.get('place_country',''))}</b>", _esc(ex.get("place_text", ""))]
        # Лук дня - через запятую, без пояснений
        L += ["", "<b>Лук дня</b>", _esc(", ".join(of.get("outfit", [])))]
        # Научный факт
        L += ["", "<b>Интересный научный факт</b>", _esc(ex.get("fact", ""))]
        # Слово дня - русское впереди, потом переводы
        L += ["", "<b>Слово дня</b>",
              _esc(ex.get("word_ru", "")),
              f"🇳🇱 {_esc(ex.get('word_nl',''))}",
              f"🇬🇧 {_esc(ex.get('word_en',''))}",
              f"<i>{_esc(ex.get('example_nl',''))}</i>",
              f"<i>{_esc(ex.get('example_ru',''))}</i>"]
        # Цитата дня с источником
        if ex.get("quote"):
            L += ["", "📖 <b>Цитата дня</b>", _esc(ex.get("quote", "")),
                  f"<i>— {_esc(ex.get('quote_book',''))}</i>"]

        await context.bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Ошибка сводки: {e}")


async def plan_command(update, context):
    cid = update.effective_chat.id
    await update.message.reply_text("Подготавливаю план на сегодня...")
    try:
        s = get_settings(cid)
        data = fetch_weather(s["lat"], s["lon"], 2)
        wblock = weather_block(data, 0, s["city"])
        of = build_outfit_focus(wblock, "сегодня")
        out = [wblock, "", "👕 Лук дня", " + ".join(of.get("outfit", [])) + ".",
               "", "Почему работает:", of.get("why", ""), "", f"🧠 {of.get('focus','')}"]
        await send_long(context.bot, cid, "\n".join(out))
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def tomorrow_command(update, context):
    cid = update.effective_chat.id
    await update.message.reply_text("Подготавливаю план на завтра...")
    try:
        s = get_settings(cid)
        data = fetch_weather(s["lat"], s["lon"], 2)
        wblock = weather_block(data, 1, s["city"])
        of = build_outfit_focus(wblock, "завтра")
        out = ["Завтра:", "", wblock, "", "👕 Лук", " + ".join(of.get("outfit", [])) + ".",
               "", "Почему работает:", of.get("why", ""), "", f"🧠 {of.get('focus','')}"]
        await send_long(context.bot, cid, "\n".join(out))
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def plan3_command(update, context):
    cid = update.effective_chat.id
    await update.message.reply_text("Готовлю саммари на 3 дня...")
    try:
        s = get_settings(cid)
        data = fetch_weather(s["lat"], s["lon"], 3)
        d = data["daily"]
        names = ["Сегодня", "Завтра", d["time"][2] if len(d["time"]) > 2 else "Послезавтра"]
        out = [f"🗓️ {s['city']} - 3 дня", ""]
        for i in range(3):
            code = d["weathercode"][i]
            out.append(f"{EMOJI.get(code,'🌡️')} {names[i]}: {d['temperature_2m_min'][i]:.0f}-{d['temperature_2m_max'][i]:.0f}°C, "
                       f"{DESC.get(code,'')}, ветер {d['windspeed_10m_max'][i]:.0f} м/с, дождь {d['precipitation_probability_max'][i] or 0:.0f}%")
        # короткий общий совет по гардеробу на 3 дня
        advice = build_outfit_focus("\n".join(out[2:]), "ближайшие 3 дня")
        out += ["", "👕 На период", " + ".join(advice.get("outfit", [])) + ".",
                "", "Почему работает:", advice.get("why", ""), "", f"🧠 {advice.get('focus','')}"]
        await send_long(context.bot, cid, "\n".join(out))
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def weather_command(update, context):
    cid = update.effective_chat.id
    days = 1
    if context.args:
        try:
            days = max(1, min(7, int(context.args[0])))
        except Exception:
            pass
    try:
        s = get_settings(cid)
        data = fetch_weather(s["lat"], s["lon"], max(days, 2))
        d = data["daily"]
        names = ["Сегодня", "Завтра"]
        out = []
        if days == 1:
            code = d["weathercode"][0]
            rain = d["precipitation_probability_max"][0] or 0
            wind_kmh = (d["windspeed_10m_max"][0] or 0) * 3.6
            icon = weather_icon(code, d["temperature_2m_max"][0], rain, wind_kmh)
            out += [f"📍 {s['city']}",
                    f"{icon} {d['temperature_2m_min'][0]:.0f}-{d['temperature_2m_max'][0]:.0f}°C • 🌧 {rain:.0f}% • 💨 {wind_kmh:.0f} км/ч"]
        else:
            out.append(f"📍 {s['city']} - прогноз на {days} дн.")
            for i in range(days):
                label = names[i] if i < 2 else d["time"][i]
                code = d["weathercode"][i]
                rain = d["precipitation_probability_max"][i] or 0
                wind_kmh = (d["windspeed_10m_max"][i] or 0) * 3.6
                icon = weather_icon(code, d["temperature_2m_max"][i], rain, wind_kmh)
                out.append(f"{icon} {label}: {d['temperature_2m_min'][i]:.0f}-{d['temperature_2m_max'][i]:.0f}°C, дождь {rain:.0f}%, ветер {wind_kmh:.0f} км/ч")

        # Рубрика мира: реальные текущие экстремумы (ротация точек по дню)
        rubric = world_extreme_line()
        if rubric:
            out += ["", rubric]
        # Дерзкая концовка
        out += ["", _random.choice(CLOSERS).format(city=s["city"])]
        await update.message.reply_text("\n".join(out))
    except Exception as e:
        await update.message.reply_text(f"Ошибка погоды: {e}")

# Точки для рубрики мира (реальные данные через Open-Meteo)
EXTREME_POINTS = [
    ("🇰🇼", "Кувейт", 29.37, 47.98),
    ("🇦🇶", "Антарктида", -78.46, 106.84),
    ("🇺🇸", "Долина Смерти", 36.46, -116.87),
    ("🇷🇺", "Оймякон", 63.46, 142.79),
    ("🇮🇳", "Дели", 28.61, 77.21),
    ("🇮🇸", "Рейкьявик", 64.15, -21.94),
    ("🇦🇪", "Дубай", 25.20, 55.27),
]

def world_extreme_line():
    # выбираем 3 точки по дню, тянем реальную температуру, берём самую крайнюю
    idx = datetime.now(TZ).timetuple().tm_yday
    pts = [EXTREME_POINTS[(idx + k) % len(EXTREME_POINTS)] for k in range(3)]
    readings = []
    for flag, name, lat, lon in pts:
        t = fetch_current_temp(lat, lon)
        if t is not None:
            readings.append((t, flag, name))
    if not readings:
        return None
    hot = max(readings, key=lambda x: x[0])
    cold = min(readings, key=lambda x: x[0])
    pick = hot if abs(hot[0]) >= abs(cold[0]) else cold
    t, flag, name = pick
    if t >= 40:
        return f"🔥 Сейчас в {flag} {name} {t:+.0f}°C - асфальт можно намазывать на хлеб."
    if t <= -20:
        return f"❄️ В {flag} {name} сейчас {t:+.0f}°C - холоднее, чем в твоей морозилке."
    return f"🌍 В {flag} {name} сейчас {t:+.0f}°C."

CLOSERS = [
    "Сегодня {city} явно выиграл погодную лотерею.",
    "Хорошая новость: зонтик сегодня может отдохнуть.",
    "Погода для прогулки. Отмазка «плохая погода» не принимается.",
    "На улице комфортно. Даже велосипед не возражает.",
    "Сегодня погода дружелюбнее некоторых людей.",
]


async def setcity_command(update, context):
    cid = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Формат: /setcity Амстердам")
        return
    name = " ".join(context.args)
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 1, "language": "ru"}, timeout=20)
        res = r.json().get("results")
        if not res:
            await update.message.reply_text(f"Не нашёл город: {name}")
            return
        c = res[0]
        set_settings(cid, c["latitude"], c["longitude"], c["name"])
        await update.message.reply_text(f"Город обновлён: {c['name']}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def location_handler(update, context):
    cid = update.effective_chat.id
    loc = update.message.location
    set_settings(cid, loc.latitude, loc.longitude, "ваша геолокация")
    try:
        data = fetch_weather(loc.latitude, loc.longitude, 1)
        await update.message.reply_text("Локация сохранена.\n\n" + weather_block(data, 0, "ваша геолокация"))
    except Exception as e:
        await update.message.reply_text(f"Локация сохранена. Ошибка погоды: {e}")

async def dutch_command(update, context):
    await update.message.reply_text("Готовлю урок...")
    try:
        await send_lesson(context.bot, update.effective_chat.id, "нидерландский", "🇳🇱")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def english_command(update, context):
    await update.message.reply_text("Готовлю урок...")
    try:
        await send_lesson(context.bot, update.effective_chat.id, "английский", "🇬🇧")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def answer_callback(update, context):
    q = update.callback_query
    await q.answer()
    cid = str(q.message.chat_id)
    data = q.data
    if data == "lesson_answer":
        ans = lesson_answers.get(cid)
        await q.message.reply_text(f"✅ {ans}" if ans else "Ответ не найден - запроси урок заново.")
        return
    if data.startswith("lvl_"):
        _, code, level = data.split("_")
        language = "нидерландский" if code == "nl" else "английский"
        set_level(cid, language, level)
        await q.message.reply_text(f"Уровень {language} установлен: {level}")
        return
    if data == "game_again":
        await _send_game(context.bot, cid)
        return

async def translate_command(update, context):
    cid = str(update.effective_chat.id)
    lang = "нидерландский"
    if context.args and context.args[0].lower() in ("en", "eng", "англ"):
        lang = "английский"
    level = get_level(cid, lang)
    await update.message.reply_text("Придумываю фразу...")
    try:
        ru = generate_challenge(lang, level)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return
    challenge_state[cid] = {"ru": ru, "lang": lang}
    await update.message.reply_text(f"Переведи на {lang} ({level}):\n\n{ru}\n\nНапиши перевод следующим сообщением.")

# --- Шкаф ---

async def generate_look_command(update, context):
    await plan_command(update, context)

async def _send_game(bot, cid):
    language = "английский" if text_router_state.get(str(cid)) == "en" else "нидерландский"
    level = get_level(cid, language)
    try:
        d = game_data(language, level)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    game_state[str(cid)] = {"answer": d.get("answer", ""), "quote": d.get("quote", "")}
    await bot.send_message(chat_id=cid,
        text=f"🕵️ Детектив ({language}, {level}):\n\n{d.get('clues','')}\n\nНапиши имя (можно на любом языке, опечатка ок).")

async def game_start(update, context):
    cid = str(update.effective_chat.id)
    await update.message.reply_text("Загадываю персонажа...")
    await _send_game(context.bot, cid)

async def shopping_command(update, context):
    cid = update.effective_chat.id
    await update.message.reply_text("Анализирую гардероб...")
    try:
        await send_long(context.bot, cid, generate_shopping_advice())
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def add_clothes_start(update, context):
    cid = str(update.effective_chat.id)
    add_wardrobe_mode[cid] = True
    await update.message.reply_text(
        "📤 Отправь список одежды в гардероб.\n"
        "Текстом или файлом (.txt). Можно несколько подряд.\n\n"
        "Когда закончишь - нажми ⬅️ Назад."
    )

async def document_handler(update, context):
    cid = str(update.effective_chat.id)
    if not add_wardrobe_mode.get(cid):
        return
    doc = update.message.document
    try:
        f = await context.bot.get_file(doc.file_id)
        content = await f.download_as_bytearray()
        text = content.decode("utf-8", errors="ignore")
    except Exception as e:
        await update.message.reply_text(f"Не смог прочитать файл: {e}")
        return
    await _ingest_wardrobe(update, text)

async def _ingest_wardrobe(update, text):
    await update.message.reply_text("Разбираю список...")
    try:
        parsed = parse_wardrobe_list(text)
        added = merge_wardrobe(parsed)
    except Exception as e:
        await update.message.reply_text(f"Ошибка разбора: {e}")
        return
    await update.message.reply_text(f"Добавил вещей: {added}. Можешь отправить ещё или нажми ⬅️ Назад.")

# ---------- Text router ----------

async def text_router(update, context):
    cid = str(update.effective_chat.id)
    text = update.message.text

    # ===== Навигация: главные разделы =====
    if text == ASSIST:
        text_router_state[cid] = "main"
        await update.message.reply_text(WELCOME, reply_markup=MAIN_KB); return
    if text == "🧭 Планы":
        await plany_command(update, context); return
    if text == "👕 Гардероб":
        text_router_state[cid] = "wardrobe"
        await update.message.reply_text("Гардероб:", reply_markup=WARDROBE_KB); return
    if text == "📚 Обучение + игра":
        text_router_state[cid] = "lang"
        await update.message.reply_text("Обучение и игра:", reply_markup=LANG_KB); return
    if text == "🌤 Погода":
        text_router_state[cid] = "weather"
        await update.message.reply_text("Погода:", reply_markup=WEATHER_KB); return
    if text == "✈️ Путешествия":
        text_router_state[cid] = "travel"
        await update.message.reply_text("Путешествия:", reply_markup=TRAVEL_KB); return
    if text == "🧠 Мотивация":
        text_router_state[cid] = "lagom"
        await update.message.reply_text("Мотивация:", reply_markup=LAGOM_KB); return
    if text == "🎬 Развлечения":
        text_router_state[cid] = "content"
        await update.message.reply_text("Развлечения:", reply_markup=CONTENT_KB); return
    if text == "🎤 Концерты":
        text_router_state[cid] = "concerts"
        await update.message.reply_text("Концерты:", reply_markup=CONCERTS_KB); return
    if text == "🌱 Растения":
        text_router_state[cid] = "plants"
        await update.message.reply_text("Растения:", reply_markup=PLANTS_KB); return
    if text == "⚙️ Настройки":
        text_router_state[cid] = "settings"
        await update.message.reply_text("Настройки:", reply_markup=SETTINGS_KB); return

    # языковые подменю
    if text == "🇳🇱 Нидерландский":
        text_router_state[cid] = "nl"
        await update.message.reply_text("Нидерландский:", reply_markup=NL_KB); return
    if text == "🇬🇧 Английский":
        text_router_state[cid] = "en"
        await update.message.reply_text("Английский:", reply_markup=EN_KB); return

    if text == "⬅️ Назад":
        add_wardrobe_mode.pop(cid, None)
        pending_input.pop(cid, None)
        if text_router_state.get(cid) in ("nl", "en"):
            text_router_state[cid] = "lang"
            await update.message.reply_text("Языки и игра:", reply_markup=LANG_KB)
        else:
            text_router_state[cid] = "main"
            await update.message.reply_text("Главное меню:", reply_markup=MAIN_KB)
        return

    # ===== Гардероб =====
    if text == "✨ Сгенерировать лук":
        await generate_look_command(update, context); return
    if text == "📊 Скучный список":
        w = load_wardrobe()
        await send_long(context.bot, cid, "📊 Гардероб\n\n" + (wardrobe_to_text(w) or "Пусто.")); return
    if text == "🧠 Анализ":
        await update.message.reply_text("Анализирую...")
        try:
            await send_long(context.bot, cid, wardrobe_analysis())
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
        return
    if text == "🛍️ Советы к покупке":
        await shopping_command(update, context); return
    if text == "📤 Добавить одежду":
        await add_clothes_start(update, context); return

    # ===== Обучение и игра =====
    if text == "📖 Грамматика NL":
        text_router_state[cid] = "nl"; await dutch_command(update, context); return
    if text == "📖 Грамматика EN":
        text_router_state[cid] = "en"; await english_command(update, context); return
    if text == "⚡ Тренировка NL":
        text_router_state[cid] = "nl"; context.args = []; await translate_command(update, context); return
    if text == "⚡ Тренировка EN":
        text_router_state[cid] = "en"; context.args = ["en"]; await translate_command(update, context); return
    if text == "🕵️ Игра-детектив":
        await game_start(update, context); return
    if text == "⚙️ Уровень языка" or text == "🌐 Уровень языков":
        nl_lvl, en_lvl = get_level(cid, "нидерландский"), get_level(cid, "английский")
        kb_nl = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_nl_{l}") for l in LEVELS]])
        kb_en = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_en_{l}") for l in LEVELS]])
        await update.message.reply_text(f"🇳🇱 Уровень нидерландского (сейчас {nl_lvl}):", reply_markup=kb_nl)
        await update.message.reply_text(f"🇬🇧 Уровень английского (сейчас {en_lvl}):", reply_markup=kb_en)
        return

    # ===== Погода =====
    if text == "🌤 Сегодня":
        context.args = []; await weather_command(update, context); return
    if text == "📅 7 дней":
        context.args = ["7"]; await weather_command(update, context); return

    # ===== Путешествия =====
    if text == "🗺 Куда поехать":
        await update.message.reply_text("Подбираю направления...")
        try:
            await send_long(context.bot, cid, travel_suggest())
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
        return
    if text == "🏳 Мои страны":
        await send_long(context.bot, cid, "🏳 Посещённые страны:\n\n" + VISITED); return

    # ===== Мотивация =====
    if text == "🌿 Фраза дня":
        await update.message.reply_text("🌿 " + lagom_of_day()); return
    if text == "🌙 Проверка дня":
        msg = ("🌙 Дим, как вечер? Смотри: что тревожило и что реально было - часто две разные истории. "
               "Давай отпустим то, что уже неважно. Как сегодня с этим, заметил разницу?\n\n"
               "Ответь - запишу в дневник.")
        pending_input[cid] = "diary"
        await update.message.reply_text(msg)
        return
    if text == "📊 Дневник":
        entries = get_list(DIARY_KEY, cid)
        if not entries:
            await update.message.reply_text("Дневник пуст. Записи появятся после вечерней проверки дня.")
        else:
            last = entries[-7:]
            await send_long(context.bot, cid, "📊 Последние записи\n\n" + "\n\n".join(f"{e['date']}: {e['text']}" for e in last))
        return

    # ===== Развлечения =====
    if text == "🎬 Что посмотреть":
        await update.message.reply_text("Подбираю...")
        try:
            await send_long(context.bot, cid, content_recommend("movie", get_list(FAVORITES_KEY, cid)))
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
        return
    if text == "📖 Что почитать":
        await update.message.reply_text("Подбираю...")
        try:
            await send_long(context.bot, cid, content_recommend("book", get_list(FAVORITES_KEY, cid)))
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
        return
    if text == "❤️ Любимое":
        favs = get_list(FAVORITES_KEY, cid)
        msg = "❤️ Любимое:\n" + ("\n".join(f"• {f}" for f in favs) if favs else "пусто")
        pending_input[cid] = "favorite"
        await update.message.reply_text(msg + "\n\nНапиши фильм/сериал/книгу - добавлю."); return
    if text == "🎤 Концерты":
        text_router_state[cid] = "concerts"
        await update.message.reply_text("Концерты:", reply_markup=CONCERTS_KB); return

    # ===== Концерты =====
    if text == "🎤 Мои артисты":
        arts = get_list(ARTISTS_KEY, cid)
        await update.message.reply_text("🎤 Артисты:\n" + ("\n".join(f"• {a}" for a in arts) if arts else "пусто") +
                                        "\n\nПоиск концертов требует API событий (Ticketmaster/Bandsintown). Дай ключ - подключу мониторинг.")
        return
    if text == "➕ Добавить артиста":
        pending_input[cid] = "artist"
        await update.message.reply_text("Напиши имя артиста - добавлю в список отслеживания."); return

    # ===== Растения =====
    if text == "💧 Мои растения":
        pl = get_list(PLANTS_KEY, cid)
        await update.message.reply_text("🌱 Растения:\n" + ("\n".join(f"• {p}" for p in pl) if pl else "пусто")); return
    if text == "🗓 Полив":
        pl = get_list(PLANTS_KEY, cid)
        if not pl:
            await update.message.reply_text("Сначала добавь растения (➕ Добавить растение) с интервалом полива.")
        else:
            await update.message.reply_text("🗓 Расписание полива:\n" + "\n".join(f"• {p}" for p in pl) +
                                             "\n\n(Авто-напоминания добавлю в следующем батче.)")
        return
    if text == "➕ Добавить растение":
        pending_input[cid] = "plant"
        await update.message.reply_text("Напиши растение и интервал полива, напр.: монстера, раз в 5 дней"); return

    # ===== Настройки =====
    if text == "📍 Сменить город":
        await update.message.reply_text("Отправь геолокацию или команду: /setcity Амстердам"); return

    # ===== Режим добавления одежды =====
    if add_wardrobe_mode.get(cid):
        await _ingest_wardrobe(update, text)
        return

    # ===== Pending-ввод (дневник, растение, артист, любимое) =====
    if cid in pending_input:
        kind = pending_input.pop(cid)
        if kind == "diary":
            entries = get_list(DIARY_KEY, cid)
            entries.append({"date": datetime.now(TZ).strftime("%d.%m"), "text": text})
            set_list(DIARY_KEY, cid, entries)
            try:
                await send_long(context.bot, cid, diary_reflect(text))
            except Exception:
                await update.message.reply_text("Записал в дневник.")
            return
        if kind == "favorite":
            add_to_list(FAVORITES_KEY, cid, text)
            await update.message.reply_text("Добавил в любимое."); return
        if kind == "artist":
            add_to_list(ARTISTS_KEY, cid, text)
            await update.message.reply_text("Добавил артиста."); return
        if kind == "plant":
            add_to_list(PLANTS_KEY, cid, text)
            await update.message.reply_text("Добавил растение."); return

    # ===== Игра: ответ-догадка =====
    if cid in game_state:
        st = game_state.pop(cid)
        ans = st["answer"].lower().strip()
        guess = text.lower().strip()
        # совпадение: точное, вхождение, или близкое по буквам (одна опечатка)
        def close(a, b):
            if a in b or b in a:
                return True
            if abs(len(a) - len(b)) <= 1:
                diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
                return diff <= 1
            return False
        correct = any(close(guess, part) for part in [ans] + ans.split())
        verdict = "✅ Верно!" if correct else f"❌ Почти. Это {st['answer']}."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕵️ Загадать ещё", callback_data="game_again")]])
        await update.message.reply_text(f"{verdict}\n\n💬 {st.get('quote','')}", reply_markup=kb)
        return

    # ===== Перевод-челлендж =====
    if cid in challenge_state:
        st = challenge_state.pop(cid)
        await update.message.reply_text("Проверяю...")
        try:
            fb = check_translation(st["lang"], st["ru"], text)
        except Exception as e:
            await update.message.reply_text(f"Ошибка проверки: {e}")
            return
        await send_long(context.bot, cid, fb)
        return

    # ===== Свободный чат =====
    await context.bot.send_chat_action(chat_id=cid, action="typing")
    hist = chat_history.get(cid, [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = chat_chain(hist)
    except Exception as e:
        await update.message.reply_text(f"Ошибка чата: {e}")
        return
    hist.append({"role": "assistant", "content": answer})
    chat_history[cid] = hist[-10:]
    await send_long(context.bot, cid, answer)


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("plan", "план на сегодня"),
        BotCommand("tomorrow", "план на завтра"),
        BotCommand("dutch", "урок нидерландского"),
        BotCommand("english", "урок английского"),
        BotCommand("translate", "перевод-челлендж"),
        BotCommand("weather", "погода"),
        BotCommand("setcity", "сменить город"),
        BotCommand("start", "меню"),
    ])


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plany", plany_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("tomorrow", tomorrow_command))
    app.add_handler(CommandHandler("plan3", plan3_command))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("setcity", setcity_command))
    app.add_handler(CommandHandler("dutch", dutch_command))
    app.add_handler(CommandHandler("english", english_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CallbackQueryHandler(answer_callback, pattern="^(lesson_answer|lvl_|game_again)"))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    jq.run_daily(job_morning, time=datetime.strptime("08:30", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_dutch, time=datetime.strptime("11:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_checkin_day, time=datetime.strptime("14:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_checkin_evening, time=datetime.strptime("20:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_weekly, time=datetime.strptime("19:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=(6,))  # воскресенье

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()