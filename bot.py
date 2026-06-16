import os
import re
import json
import time
import requests
from datetime import datetime, date, timedelta
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
game_state = {}         # chat_id -> {answer, quote, quote_ru}
game_config = {}        # chat_id -> {lang, difficulty}
grammar_state = {}      # chat_id -> {correct, why, a, b}
last_recos = {}         # chat_id -> {kind, items:[title]}
suggested_countries = {}  # chat_id -> [country]
pending_input = {}      # chat_id -> "diary" | "plant" | "fav_movie" | "fav_book" | "artist"
SETTINGS_FILE = "settings.json"
NOTES_FILE = "notes.json"
LEVELS_FILE = "levels.json"       # {chat_id: {язык: уровень}}
WARDROBE_FILE = "wardrobe.json"

DEFAULT_CITY = {"lat": 52.63, "lon": 4.74, "city": "Алкмар"}

# Мой лагом - единый источник (для фраз дня и тона ассистента)
LAGOM_LINES = [
    "Сейчас не вся жизнь. Сейчас один шаг.",
    "От чего наполняешься - то и монетизируй.",
    "Мне не нужно идеально. Мне нужно начать.",
    "Я не ленивый. Мой мозг так работает.",
    "Остановись. Выдохни. Потом действуй.",
    "Я делаю лучшее из возможного сегодня.",
    "Быть добрым и скромным недостаточно. Мир замечает и продвигает тех, кто не прячется и умеет быть видимым.",
    "Если нежелательная мысль - крикни «Стража» или трижды скажи «Отмена».",
    "Не пропускай зло дальше себя.",
    "Всё просто - нужно только перестать верить тем, кто убеждает в обратном.",
    "Мечты и риск важны, главное - двигаться вперёд.",
    "Любовь важна, но не единственное; цени поддержку, создавай воспоминания.",
    "Фокус на хорошем и благодарность за мелочи.",
    "Не все споры стоят нервов.",
    "Уважай границы, говори открыто.",
    "Родители - взрослые, ты не несёшь ответственность за их чувства и здоровье.",
    "Требовать соблюдения своих прав - это не наглость, а здоровое поведение.",
    "Перемены открывают возможности.",
    "Окружение влияет - ищи своё, а не терпи.",
    "Книги - источник радости и роста.",
    "Путешествия важнее материального.",
    "Избавляйся от лишнего, чтобы освободить место новому.",
    "Баланс между работой, отдыхом и движением - необходим.",
    "Скука - твой криптонит. Создавай интерес - в задачах, в среде, в людях.",
    "Не забывай переключаться, но не убегать.",
    "Это не угроза. Это просто раздражение.",
    "Я могу ответить позже.",
    "Мне не нужно выигрывать этот момент.",
    "Чужие эмоции - не моя ответственность.",
    "Пауза сейчас - победа.",
    "Это состояние пройдёт.",
    "Мне не нужно решать всё сейчас.",
    "Я могу замедлиться.",
]

def lagom_of_day():
    idx = datetime.now(TZ).timetuple().tm_yday % len(LAGOM_LINES)
    return LAGOM_LINES[idx]

# Тон ассистента берём из тех же установок (без дублирования)
LAGOM = "Установки Дмитрия (для тона, не цитировать дословно):\n" + " ".join(LAGOM_LINES)

# Любимые книги для цитат (мотивирующие, позитивные)
FAV_BOOKS = "Цветы для Элджернона (Дэниел Киз), Марсианин (Энди Вейр), 1984 (Джордж Оруэлл), Машина времени (Г. Уэллс)"

STYLE_PROFILE = """
Стиль Дмитрия: современный минимализм с элементами скандинавского и японского casual.
Цель: выглядеть современно, собранно и расслабленно. Образ уместен в Амстердаме, Утрехте, Копенгагене, Стокгольме.
Контекст: одежда под велосипед, прогулки, путешествия и повседневную работу.
Любит: плотные футболки с плотным воротом, рубашки свободного кроя, ветровки и лёгкую верхнюю одежду, удобную обувь, минималистичные аксессуары.
Избегать: узких вещей, чрезмерно спортивных образов.
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
PLANTS_KEY = "plants.json"         # [{name, interval, next}]
DIARY_KEY = "diary.json"           # [{date, text}]
ARTISTS_KEY = "artists.json"
WATCHLIST_KEY = "watchlist.json"   # список просмотра
READLIST_KEY = "readlist.json"     # список чтения
FAVCOUNTRIES_KEY = "favcountries.json"  # [{name, flag}]
WORRIES_KEY = "worries.json"       # [{text, status}] status: pending/real/let_go

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

def grammar_data(language, level="B1"):
    prompt = f"""Грамматическое задание по языку {language} для русскоговорящего ученика уровня {level}.
Выбери одну грамматическую тему уровня {level} (каждый раз разную). Сделай предложение с ОДНИМ пропуском.

JSON:
{{
 "rule_title": "короткое название темы по-русски",
 "rule": "объяснение правила простым языком, 2-3 строки, по-русски",
 "sentence": "предложение на {language} с пропуском в виде ____",
 "a": "вариант ответа A (одно слово)",
 "b": "вариант ответа B (одно слово)",
 "correct": "a или b",
 "why": "одна строка - почему этот вариант верный, по-русски"
}}"""
    return llm_json(prompt, 800)

async def send_grammar(bot, chat_id, language, flag):
    level = get_level(chat_id, language)
    d = grammar_data(language, level)
    grammar_state[str(chat_id)] = {"correct": d.get("correct", "a"), "why": d.get("why", ""),
                                   "a": d.get("a", ""), "b": d.get("b", ""),
                                   "lang": language, "flag": flag}
    text = (f"📖 {flag} Грамматика ({level})\n\n"
            f"{d.get('rule_title','')}\n{d.get('rule','')}\n\n"
            f"Заполни пропуск:\n{d.get('sentence','')}")
    code = "nl" if language == "нидерландский" else "en"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(d.get("a", "A"), callback_data="gram_a"),
         InlineKeyboardButton(d.get("b", "B"), callback_data="gram_b")],
        [InlineKeyboardButton("➕ Ещё пример", callback_data=f"again_gram_{code}")],
    ])
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)

def generate_challenge(language, level="B1"):
    prompt = f"""Дай ОДНУ фразу на русском для перевода на {language}.
Уровень {level}: сложность строго под уровень. Бытовая или рабочая ситуация.
Фраза с заглавной буквы и с точкой в конце.
Выведи ТОЛЬКО русскую фразу, без кавычек."""
    return llm(prompt, 200, 1.0).strip()

def check_translation(language, ru, answer):
    prompt = f"""Ученик переводит с русского на {language}.
Русская фраза: {ru}
Перевод ученика: {answer}

JSON:
{{
 "ok": true/false (перевод по сути верный или нет),
 "error": "если есть ошибка - в чём именно, коротко по-русски (иначе пустая строка)",
 "correct": "правильный, естественный вариант на {language}",
 "simple": ["1-3 очень коротких пункта-объяснения по-русски"],
 "easier": "более простой/разговорный вариант на {language}, если уместно (иначе пустая строка)"
}}"""
    return llm_json(prompt, 800)

def generate_shopping_advice():
    w = load_wardrobe()
    prompt = f"""Ты стилист. Профиль:
{STYLE_PROFILE}

Гардероб:
{wardrobe_to_text(w)}

Предложи что докупить, чтобы открыть больше сочетаний. Тренды 2026, скандинавский/японский casual. Без брендов.
Раздели строго по разделам, коротко (длинное не читают):

🛍️ Что докупить

ВЕРХ
- вещь - одна строка почему

НИЗ
- вещь - почему

ОБУВЬ
- вещь - почему

Максимум 2 пункта на раздел. Без markdown и звёздочек."""
    return llm(prompt, 800, 0.7)

def parse_wardrobe_list(text):
    w = load_wardrobe()
    cats = ", ".join(w.keys()) or "футболки, рубашки, свитшоты, верхняя одежда, брюки, обувь, носки, кепки, аксессуары"
    prompt = f"""Разбери список одежды и распредели по категориям.
Существующие категории: {cats}. Используй их, если подходит. Можно создать новую категорию.

Список:
{text}

Верни JSON: {{"категория": ["вещь", "вещь"], ...}}. Названия короткие, в нижнем регистре."""
    return llm_json(prompt, 800)

# ---------- Модульные генераторы ----------

def wardrobe_analysis():
    w = load_wardrobe()
    prompt = f"""Ты стилист с прямым, дерзким тоном. Профиль:
{STYLE_PROFILE}

Гардероб:
{wardrobe_to_text(w)}

Разбери вещи ПО НАЗНАЧЕНИЮ. Без markdown и звёздочек. Коротко, с юмором. Формат:

🧠 Разбор по назначению

🏃 Повседневная: ...
🏠 Домашняя: ...
🏋️ Спортивная: ...
👔 Деловая: ...
🎉 Праздничная: ...

⚠️ Пора заменить/докупить:
- вещь - дерзкий комментарий (напр. "годится только для прогулки с собакой в 5 утра")

В конце строка: 👉 Что купить - смотри «Советы к покупке»."""
    return llm(prompt, 1100, 0.8)

def generate_look():
    w = load_wardrobe()
    prompt = f"""Ты стилист. Профиль:
{STYLE_PROFILE}

Гардероб:
{wardrobe_to_text(w)}

Собери 2-3 самые ИНТЕРЕСНЫЕ комбинации из этих вещей (не про погоду, а про стиль и характер).
Каждая: вещи через запятую, и в конце короткая шутка-вердикт с эмодзи, для какого случая (например: 😎 для свидания с твоим отражением; 🕺 зайдёт на вечеринку бабушек в клубе престарелых).
Без markdown и звёздочек. Заголовок: ✨ Луки дня."""
    return llm(prompt, 900, 1.0)

def travel_suggest_data():
    prompt = f"""Дмитрий был в: {VISITED}.
Любит интеллектуальную атмосферу, города с характером, природу.
Предложи 4 НОВЫХ страны/направления (где не был).
JSON: {{"items": [{{"country": "страна", "flag": "эмодзи флага", "why": "одна строка почему зайдёт"}}]}}"""
    return llm_json(prompt, 700)

def country_facts(country):
    prompt = f"""Дай 10 коротких интересных фактов про страну {country}, по-русски, нумерованным списком, без markdown и звёздочек.
Заголовок: 📍 {country} - 10 фактов."""
    return llm(prompt, 900, 0.8)

def country_flag(name):
    try:
        out = llm(f"Верни ТОЛЬКО эмодзи флага страны: {name}. Без текста.", 20, 0).strip()
        return out if out else "🏳"
    except Exception:
        return "🏳"

def parse_plant(text):
    """Из 'монстера, раз в 5 дней' -> ('монстера', 5). По умолчанию 7 дней."""
    interval = 7
    m = re.search(r"(\d+)", text)
    if m:
        try:
            interval = max(1, min(60, int(m.group(1))))
        except Exception:
            pass
    name = re.split(r"[,;]| раз | поливать | каждые ", text)[0].strip()
    return (name or text.strip(), interval)

def content_recommend(kind, favorites):
    fav = ", ".join(favorites) if favorites else "1984, Цветы для Элджернона, Марсианин, умная фантастика"
    what = "фильмов/сериалов" if kind == "movie" else "книг"
    prompt = f"""Порекомендуй 5 {what} для вкуса: {fav}. Любит научную фантастику и интеллектуальное.
JSON: {{"items": [{{"title": "название (год)", "hook": "1 строка интриги, на что похоже", "rating": "X.X"}}]}}
rating - предполагаемая оценка из 10 на основе его вкуса."""
    return llm_json(prompt, 900)

def game_data(clue_lang, difficulty):
    diff_map = {
        "easy": "очень известный персонаж/личность, подсказки простые и явные",
        "med": "известный персонаж/личность, подсказки средней сложности",
        "hard": "менее очевидный, но узнаваемый персонаж/личность, подсказки хитрые и непрямые",
    }
    diff = diff_map.get(difficulty, diff_map["med"])
    prompt = f"""Игра-детектив: загадай персонажа или личность (кино, наука, история, музыка, литература).
Сложность: {diff}. Язык подсказок: {clue_lang}.
Ответь СТРОГО в таком формате, каждое поле с новой строки, без markdown, без кавычек:

CLUES: 3-4 подсказки на языке {clue_lang}, разделённые знаком | , от непрямой к явной, без имени
ANSWER: имя
HINT: ещё одна явная подсказка на языке {clue_lang}
QUOTE: короткая дерзкая или смешная фраза в духе персонажа на языке {clue_lang}
QUOTE_RU: перевод фразы на русский"""
    raw = llm(prompt, 800, 0.9)
    out = {}
    for key, field in (("CLUES", "clues"), ("ANSWER", "answer"), ("HINT", "hint"),
                       ("QUOTE", "quote"), ("QUOTE_RU", "quote_ru")):
        m = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z_]+:|\Z)", raw, re.S)
        out[field] = m.group(1).strip() if m else ""
    out["clues"] = out.get("clues", "").replace(" | ", "\n").replace("|", "\n")
    return out

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
ВАЖНО: верни строго валидный JSON, экранируй кавычки внутри строк, без переносов строк внутри значений.
{{
 "place_country": "{country}",
 "place_text": "2-3 коротких факта про {country}, живо",
 "fact": "новый интересный научный факт с конкретикой - где/что именно, 1-2 предложения, не голословно",
 "word_ru": "русское слово дня (одно слово)",
 "word_nl": "перевод на нидерландский",
 "word_en": "перевод на английский",
 "example_nl": "пример с этим словом на нидерландском",
 "example_ru": "перевод примера на русский",
 "quote": "мотивирующая позитивная цитата (1-2 предложения) из книги: {FAV_BOOKS}",
 "quote_book": "название книги и автор"
}}"""
    return llm_json(prompt, 1000)

# ---------- Send ----------

async def send_long(bot, chat_id, text):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000])

def morning_greeting(weather_short):
    prompt = f"""Напиши КОРОТКОЕ утреннее приветствие Дмитрию (по-русски, можно с лёгкой дерзостью/юмором).
Погода сегодня: {weather_short}
Структура (2-4 строки, без markdown и звёздочек):
- приветствие + одна фраза про день/погоду с характером
- мини-настрой на день
В конце добавь ОДИН совет по духу его установок (НЕ про одежду):
{LAGOM}
Коротко. Не перечисляй список, просто живой текст."""
    return llm(prompt, 400, 0.95)

def assemble_morning(chat_id):
    s = get_settings(chat_id)
    data = fetch_weather(s["lat"], s["lon"], days=2)
    wblock = weather_block(data, 0, s["city"])
    of = build_outfit_focus(wblock, "сегодня")
    try:
        greet = morning_greeting(wblock)
    except Exception:
        greet = "Доброе утро. Один шаг за раз - этого достаточно."

    parts = [greet, "", "— — —", "", wblock, "",
             "👕 Лук дня", ", ".join(of.get("outfit", []))]
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
        await send_grammar(context.bot, CHAT_ID, "нидерландский", "🇳🇱")
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка урока: {e}")

async def job_checkin_day(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        pending_input[str(CHAT_ID)] = "worry"
        await context.bot.send_message(chat_id=CHAT_ID,
            text="🌤 Дим, что сейчас тревожит? Напиши одним сообщением, каждую тревогу с новой строки - вечером проверим, что реально случилось.")
    except Exception:
        pass

async def job_checkin_evening(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        await _send_daycheck(context.bot, CHAT_ID)
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

async def job_water(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    pl = get_list(PLANTS_KEY, CHAT_ID)
    today = datetime.now(TZ).date()
    due = []
    for p in pl:
        if not isinstance(p, dict):
            continue
        try:
            if date.fromisoformat(p.get("next", "")) <= today:
                due.append(p["name"])
        except Exception:
            pass
    if due:
        try:
            await context.bot.send_message(chat_id=CHAT_ID,
                text="💧 Пора полить:\n" + "\n".join(f"• {n}" for n in due) +
                     "\n\nОткрой 🌱 Растения → «Полил» после полива.")
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

WELCOME = (
    "Что будем делать сегодня?\n\n"
    "💬 Напиши вопрос своими словами или выбери раздел кнопками.\n\n"
    "Попробуй спросить:\n"
    "👕 Что надеть сегодня?\n"
    "🇳🇱 Объясни разницу между die и dat\n"
    "✈️ Куда поехать на выходные из Нидерландов?\n"
    "📚 Посоветуй книгу как «Цветы для Элджернона»\n"
    "🎬 Найди сериал похожий на The Last of Us\n"
    "🌤 Какая погода на выходных?\n\n"
    "💡 Не знаешь, с чего начать? Расскажи, что сейчас в голове - задача, идея, проблема или вопрос."
)

MAIN_TEXT = "Привет! 👋 Я DM.\n\nВыбери раздел:"
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


def _ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def _back(parent):
    return [("👨🏻‍💻 Вызов ассистента", "a_assist"), ("⬅️ Назад", parent)]

# Текст + клавиатура для каждого экрана меню. m_<key> открывает экран.
def menu_screen(key):
    if key == "m_main":
        return (MAIN_TEXT, _ikb([
            [("🧭 Планы", "a_plany")],
            [("👕 Гардероб", "m_wardrobe"), ("📚 Обучение", "m_lang")],
            [("🌤 Погода", "m_weather"), ("✈️ Путешествия", "m_travel")],
            [("🧠 Мотивация", "m_lagom"), ("🎬 Развлечения", "m_content")],
            [("🌱 Растения", "m_plants"), ("⚙️ Настройки", "m_settings")],
            [("👨🏻‍💻 Вызов ассистента", "a_assist")],
        ]))
    if key == "m_wardrobe":
        return ("👕 Гардероб", _ikb([
            [("✨ Сгенерировать лук", "a_look")],
            [("📊 Список", "a_wlist"), ("🧠 Анализ", "a_wanalysis")],
            [("🛍️ Советы к покупке", "a_shop")],
            [("📤 Добавить одежду", "a_wadd")],
            _back("m_main"),
        ]))
    if key == "m_lang":
        return ("📚 Обучение + игра", _ikb([
            [("🇳🇱 Нидерландский", "m_nl"), ("🇬🇧 Английский", "m_en")],
            [("🕵️ Игра-детектив", "a_game")],
            [("⚙️ Уровень языка", "a_levels")],
            _back("m_main"),
        ]))
    if key == "m_nl":
        return ("🇳🇱 Нидерландский", _ikb([
            [("📖 Грамматика", "a_gram_nl"), ("⚡ Тренировка", "a_tr_nl")],
            [("👨🏻‍💻 Вызов ассистента", "a_assist"), ("⬅️ Назад", "m_lang")],
        ]))
    if key == "m_en":
        return ("🇬🇧 Английский", _ikb([
            [("📖 Грамматика", "a_gram_en"), ("⚡ Тренировка", "a_tr_en")],
            [("👨🏻‍💻 Вызов ассистента", "a_assist"), ("⬅️ Назад", "m_lang")],
        ]))
    if key == "m_weather":
        return ("🌤 Погода", _ikb([
            [("🌤 Сегодня", "a_w_today"), ("📅 7 дней", "a_w_week")],
            [("📍 Сменить город", "a_setcity")],
            _back("m_main"),
        ]))
    if key == "m_travel":
        return ("✈️ Путешествия", _ikb([
            [("🗺 Куда поехать", "a_trav_go"), ("🏳 Мои страны", "a_trav_my")],
            _back("m_main"),
        ]))
    if key == "m_lagom":
        return ("🧠 Мотивация", _ikb([
            [("🌙 Проверка дня", "a_daycheck"), ("📊 Дневник", "a_diary")],
            [("🌿 Фраза дня", "a_phrase")],
            _back("m_main"),
        ]))
    if key == "m_content":
        return ("🎬 Развлечения", _ikb([
            [("🎬 Что посмотреть", "a_watch"), ("📖 Что почитать", "a_read")],
            [("🍿 Список просмотра", "a_watchlist"), ("📚 Список чтения", "a_readlist")],
            [("❤️ Любимое", "a_fav"), ("🎤 Концерты", "m_concerts")],
            _back("m_main"),
        ]))
    if key == "m_concerts":
        return ("🎤 Концерты", _ikb([
            [("🎤 Мои артисты", "a_artists"), ("➕ Добавить", "a_artadd")],
            [("👨🏻‍💻 Вызов ассистента", "a_assist"), ("⬅️ Назад", "m_content")],
        ]))
    if key == "m_plants":
        return ("🌱 Растения", _ikb([
            [("💧 Мои растения", "a_plants_list"), ("🗓 Полив", "a_water")],
            [("➕ Добавить растение", "a_plantadd")],
            _back("m_main"),
        ]))
    if key == "m_settings":
        return ("⚙️ Настройки", _ikb([
            [("📍 Сменить город", "a_setcity"), ("🌐 Уровень языков", "a_levels")],
            _back("m_main"),
        ]))
    return (MAIN_TEXT, menu_screen("m_main")[1])


async def start(update, context):
    text, kb = menu_screen("m_main")
    await update.message.reply_text(text, reply_markup=kb)


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

async def _send_plany(bot, cid):
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
    wind_ms = d["windspeed_10m_max"][0] or 0
    L = [f"🧭 <b>Планы | {header}</b>", "", "<b>Погода</b>",
         f"{icon} {_esc(s['city'])}: {temp:+.0f}°C • Вероятность дождя {rain:.0f}% • 💨 {wind_ms:.0f} м/с",
         "", "<b>Лук дня</b>", _esc(", ".join(of.get("outfit", []))),
         "", "<b>Слово дня</b>", _esc(ex.get("word_ru", "")),
         f"🇳🇱 {_esc(ex.get('word_nl',''))} / 🇬🇧 {_esc(ex.get('word_en',''))}",
         f"<i>{_esc(ex.get('example_nl',''))} ({_esc(ex.get('example_ru',''))})</i>",
         "", "🔬 <b>Интересный научный факт</b>", _esc(ex.get("fact", ""))]
    if ex.get("quote"):
        L += ["", "📖 <b>Цитата дня</b>", _esc(ex.get("quote", "")), f"<i>— {_esc(ex.get('quote_book',''))}</i>"]
    if ex.get("place_text"):
        L += ["", f"🗺️ <b>Место дня: {_esc(ex.get('place_country',''))}</b>", _esc(ex.get("place_text", ""))]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")

async def plany_command(update, context):
    await update.message.reply_text("Собираю сводку дня...")
    try:
        await _send_plany(context.bot, update.effective_chat.id)
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

def wind_note(ms):
    if ms >= 11:
        return "очень сильный, некомфортно"
    if ms >= 8:
        return "сильный"
    if ms >= 5:
        return "заметный"
    return "слабый"

async def _send_weather(bot, cid, days):
    s = get_settings(cid)
    data = fetch_weather(s["lat"], s["lon"], max(days, 2))
    d = data["daily"]
    names = ["Сегодня", "Завтра"]
    out = []
    if days == 1:
        code = d["weathercode"][0]
        rain = d["precipitation_probability_max"][0] or 0
        wind_ms = d["windspeed_10m_max"][0] or 0
        icon = weather_icon(code, d["temperature_2m_max"][0], rain, wind_ms * 3.6)
        out += [f"📍 {s['city']}",
                f"{icon} {d['temperature_2m_min'][0]:.0f}-{d['temperature_2m_max'][0]:.0f}°C",
                f"Вероятность дождя {rain:.0f}%",
                f"💨 Ветер {wind_ms:.0f} м/с - {wind_note(wind_ms)}"]
        rubric = world_extreme_line()
        if rubric:
            out += ["", rubric]
        out += ["", _random.choice(CLOSERS).format(city=s["city"])]
    else:
        out.append(f"📍 {s['city']} - прогноз на {days} дн.")
        out.append("")
        for i in range(days):
            label = names[i] if i < 2 else d["time"][i]
            code = d["weathercode"][i]
            rain = d["precipitation_probability_max"][i] or 0
            wind_kmh = (d["windspeed_10m_max"][i] or 0) * 3.6
            icon = weather_icon(code, d["temperature_2m_max"][i], rain, wind_kmh)
            out.append(f"{icon} {label}: {d['temperature_2m_min'][i]:.0f}-{d['temperature_2m_max'][i]:.0f}°C, "
                       f"дождь {rain:.0f}%, ветер {wind_kmh:.0f} км/ч")
    await bot.send_message(chat_id=cid, text="\n".join(out))

async def weather_command(update, context):
    days = 1
    if context.args:
        try:
            days = max(1, min(7, int(context.args[0])))
        except Exception:
            pass
    try:
        await _send_weather(context.bot, update.effective_chat.id, days)
    except Exception as e:
        await update.message.reply_text(f"Ошибка погоды: {e}")

# Точки для рубрики мира (реальные данные через Open-Meteo)
EXTREME_POINTS = [
    ("🇰🇼", "Кувейте", 29.37, 47.98),
    ("🇦🇶", "Антарктиде", -78.46, 106.84),
    ("🇺🇸", "Долине Смерти", 36.46, -116.87),
    ("🇷🇺", "Оймяконе", 63.46, 142.79),
    ("🇮🇳", "Дели", 28.61, 77.21),
    ("🇦🇪", "Дубае", 25.20, 55.27),
]

def world_extreme_line():
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
    if t >= 45:
        return f"🔥 Аномальная жара: в {flag} {name} сейчас {t:+.0f}°C - асфальт почти плавится."
    if t >= 38:
        return f"🥵 В {flag} {name} сейчас {t:+.0f}°C - дышать тяжело."
    if t <= -40:
        return f"🥶 Экстремальный холод: в {flag} {name} {t:+.0f}°C - вдвое холоднее морозилки."
    if t <= -20:
        return f"❄️ В {flag} {name} сейчас {t:+.0f}°C."
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
        try:
            fact = llm(f"Один короткий интересный факт про город {c['name']}. Одно предложение, без вступления.", 120, 0.8).strip()
        except Exception:
            fact = ""
        msg = f"Готово. Ты находишься в городе {c['name']}."
        if fact:
            msg += f"\n\n💡 {fact}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def location_handler(update, context):
    cid = update.effective_chat.id
    loc = update.message.location
    city = "ваш город"
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": "", "count": 1, "language": "ru",
                                 "latitude": loc.latitude, "longitude": loc.longitude}, timeout=15)
        res = r.json().get("results")
        if res:
            city = res[0].get("name", city)
    except Exception:
        pass
    set_settings(cid, loc.latitude, loc.longitude, city)
    try:
        data = fetch_weather(loc.latitude, loc.longitude, 1)
        await update.message.reply_text(f"Готово. Ты находишься в городе {city}.\n\n" + weather_block(data, 0, city))
    except Exception as e:
        await update.message.reply_text(f"Локация сохранена. Ошибка погоды: {e}")

async def dutch_command(update, context):
    await update.message.reply_text("Готовлю грамматику...")
    try:
        await send_grammar(context.bot, update.effective_chat.id, "нидерландский", "🇳🇱")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def english_command(update, context):
    await update.message.reply_text("Готовлю грамматику...")
    try:
        await send_grammar(context.bot, update.effective_chat.id, "английский", "🇬🇧")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def answer_callback(update, context):
    q = update.callback_query
    await q.answer()
    cid = str(q.message.chat_id)
    data = q.data

    # --- Навигация по меню: редактируем сообщение на месте ---
    if data.startswith("m_"):
        text, kb = menu_screen(data)
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            await context.bot.send_message(chat_id=cid, text=text, reply_markup=kb)
        return

    # --- Действия: открывают результат отдельным сообщением, меню остаётся ---
    if data.startswith("a_"):
        act = data[2:]
        try:
            if act == "assist":
                await context.bot.send_message(chat_id=cid, text=WELCOME)
            elif act == "plany":
                await context.bot.send_message(chat_id=cid, text="Собираю сводку дня...")
                await _send_plany(context.bot, cid)
            # Гардероб
            elif act == "look":
                await context.bot.send_message(chat_id=cid, text="Собираю комбинации...")
                await send_long(context.bot, cid, generate_look())
            elif act == "wlist":
                w = load_wardrobe()
                await send_long(context.bot, cid, "📊 Гардероб\n\n" + (wardrobe_to_text(w) or "Пусто."))
            elif act == "wanalysis":
                await context.bot.send_message(chat_id=cid, text="Анализирую...")
                await send_long(context.bot, cid, wardrobe_analysis())
            elif act == "shop":
                await context.bot.send_message(chat_id=cid, text="Подбираю...")
                await send_long(context.bot, cid, generate_shopping_advice())
            elif act == "wadd":
                add_wardrobe_mode[cid] = True
                await context.bot.send_message(chat_id=cid,
                    text="📤 Отправь список одежды текстом или файлом (.txt). Можно несколько подряд. Когда закончишь - вернись в меню кнопкой.")
            # Языки
            elif act == "gram_nl":
                await send_grammar(context.bot, cid, "нидерландский", "🇳🇱")
            elif act == "gram_en":
                await send_grammar(context.bot, cid, "английский", "🇬🇧")
            elif act == "tr_nl":
                await _do_translate(context.bot, cid, "нидерландский")
            elif act == "tr_en":
                await _do_translate(context.bot, cid, "английский")
            elif act == "game":
                await context.bot.send_message(chat_id=cid, text="🕵️ Игра-детектив. На каком языке подсказки?",
                                               reply_markup=await _game_lang_kb())
            elif act == "levels":
                nl_lvl, en_lvl = get_level(cid, "нидерландский"), get_level(cid, "английский")
                kb_nl = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_nl_{l}") for l in LEVELS]])
                kb_en = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_en_{l}") for l in LEVELS]])
                await context.bot.send_message(chat_id=cid, text=f"🇳🇱 Уровень нидерландского (сейчас {nl_lvl}):", reply_markup=kb_nl)
                await context.bot.send_message(chat_id=cid, text=f"🇬🇧 Уровень английского (сейчас {en_lvl}):", reply_markup=kb_en)
            # Погода
            elif act == "w_today":
                await _send_weather(context.bot, cid, 1)
            elif act == "w_week":
                await _send_weather(context.bot, cid, 7)
            elif act == "setcity":
                await context.bot.send_message(chat_id=cid, text="Отправь геолокацию или команду: /setcity Амстердам")
            # Путешествия
            elif act == "trav_go":
                await context.bot.send_message(chat_id=cid, text="Подбираю направления...")
                await _send_travel_go(context.bot, cid)
            elif act == "trav_my":
                await _send_travel_my(context.bot, cid)
            # Мотивация
            elif act == "daycheck":
                await _send_daycheck(context.bot, cid)
            elif act == "diary":
                entries = get_list(DIARY_KEY, cid)
                if not entries:
                    await context.bot.send_message(chat_id=cid, text="Дневник пуст. Записи появятся после проверки дня.")
                else:
                    last = entries[-7:]
                    await send_long(context.bot, cid, "📊 Последние записи\n\n" + "\n\n".join(f"{e['date']}: {e['text']}" for e in last))
            elif act == "phrase":
                await context.bot.send_message(chat_id=cid, text="🌿 " + lagom_of_day())
            # Развлечения
            elif act == "watch":
                await context.bot.send_message(chat_id=cid, text="Подбираю...")
                await _send_recos(context.bot, cid, "movie")
            elif act == "read":
                await context.bot.send_message(chat_id=cid, text="Подбираю...")
                await _send_recos(context.bot, cid, "book")
            elif act == "watchlist":
                lst = get_list(WATCHLIST_KEY, cid)
                await context.bot.send_message(chat_id=cid, text="🍿 Посмотреть:\n" + ("\n".join(f"• {x}" for x in lst) if lst else "пусто"))
            elif act == "readlist":
                lst = get_list(READLIST_KEY, cid)
                await context.bot.send_message(chat_id=cid, text="📚 Почитать:\n" + ("\n".join(f"• {x}" for x in lst) if lst else "пусто"))
            elif act == "fav":
                favs = get_list(FAVORITES_KEY, cid)
                pending_input[cid] = "favorite"
                await context.bot.send_message(chat_id=cid,
                    text="❤️ Любимое:\n" + ("\n".join(f"• {f}" for f in favs) if favs else "пусто") + "\n\nНапиши фильм/сериал/книгу - добавлю.")
            elif act == "artists":
                arts = get_list(ARTISTS_KEY, cid)
                await context.bot.send_message(chat_id=cid,
                    text="🎤 Артисты:\n" + ("\n".join(f"• {a}" for a in arts) if arts else "пусто") +
                         "\n\nПоиск концертов требует API событий (Ticketmaster/Bandsintown).")
            elif act == "artadd":
                pending_input[cid] = "artist"
                await context.bot.send_message(chat_id=cid, text="Напиши имя артиста - добавлю в список.")
            # Растения
            elif act == "plants_list":
                pl = get_list(PLANTS_KEY, cid)
                if not pl:
                    await context.bot.send_message(chat_id=cid, text="🌱 Растений нет.")
                else:
                    lines = ["🌱 Растения"]
                    rows = []
                    for i, p in enumerate(pl):
                        nm = p.get("name", "?") if isinstance(p, dict) else str(p)
                        iv = p.get("interval", 7) if isinstance(p, dict) else 7
                        nxt = p.get("next", "") if isinstance(p, dict) else ""
                        lines.append(f"• {nm} - раз в {iv} дн." + (f", след. {nxt}" if nxt else ""))
                        rows.append([InlineKeyboardButton(f"💧 Полил: {nm}", callback_data=f"water_{i}")])
                    await context.bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
            elif act == "water":
                pl = get_list(PLANTS_KEY, cid)
                if not pl:
                    await context.bot.send_message(chat_id=cid, text="Сначала добавь растения с интервалом полива.")
                else:
                    today = datetime.now(TZ).date()
                    due, ok = [], []
                    for p in pl:
                        if not isinstance(p, dict):
                            continue
                        try:
                            nd = date.fromisoformat(p.get("next", ""))
                        except Exception:
                            nd = today
                        (due if nd <= today else ok).append((p["name"], nd))
                    out = ["🗓 Полив"]
                    if due:
                        out += ["", "💧 Пора:"] + [f"• {n}" for n, _ in due]
                    if ok:
                        out += ["", "✅ Рано:"] + [f"• {n} (до {dt.isoformat()})" for n, dt in ok]
                    await context.bot.send_message(chat_id=cid, text="\n".join(out))
            elif act == "plantadd":
                pending_input[cid] = "plant"
                await context.bot.send_message(chat_id=cid, text="Напиши растение и интервал, напр.: монстера, раз в 5 дней")
        except Exception as e:
            await context.bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return

    if data.startswith("lvl_"):
        _, code, level = data.split("_")
        language = "нидерландский" if code == "nl" else "английский"
        set_level(cid, language, level)
        await q.message.reply_text(f"Уровень {language} установлен: {level}")
        return
    if data in ("gram_a", "gram_b"):
        st = grammar_state.get(cid)
        if not st:
            await q.message.reply_text("Задание устарело, запроси новое."); return
        chosen = "a" if data == "gram_a" else "b"
        if chosen == st["correct"]:
            await q.message.reply_text(f"✅ Верно! {st['why']}")
        else:
            right = st["a"] if st["correct"] == "a" else st["b"]
            await q.message.reply_text(f"❌ Нет. Правильно: {right}\n{st['why']}")
        return
    if data.startswith("gamelang_"):
        lang = {"ru": "русский", "en": "английский", "nl": "нидерландский"}[data.split("_")[1]]
        game_config[cid] = {"lang": lang, "difficulty": "med"}
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Лёгкая", callback_data="gamediff_easy"),
            InlineKeyboardButton("Средняя", callback_data="gamediff_med"),
            InlineKeyboardButton("Тяжёлая", callback_data="gamediff_hard"),
        ]])
        await q.message.reply_text(f"Язык: {lang}. Выбери сложность:", reply_markup=kb)
        return
    if data.startswith("gamediff_"):
        diff = data.split("_")[1]
        cfg = game_config.get(cid, {"lang": "нидерландский"})
        cfg["difficulty"] = diff
        game_config[cid] = cfg
        await _send_game(context.bot, cid)
        return
    if data.startswith("water_"):
        i = int(data.split("_")[1])
        pl = get_list(PLANTS_KEY, cid)
        if i < len(pl) and isinstance(pl[i], dict):
            iv = pl[i].get("interval", 7)
            pl[i]["next"] = (datetime.now(TZ).date() + timedelta(days=iv)).isoformat()
            set_list(PLANTS_KEY, cid, pl)
            await q.message.reply_text(f"💧 {pl[i]['name']} полит. Следующий полив {pl[i]['next']}.")
        return
    if data == "game_again":
        await _send_game(context.bot, cid)
        return
    if data == "game_hint":
        st = game_state.get(cid)
        if st and st.get("hint"):
            await q.message.reply_text(f"💡 {st['hint']}\n\nНапиши ответ или нажми «Показать ответ».")
        else:
            await q.message.reply_text("Подсказок больше нет.")
        return
    if data == "game_reveal":
        st = game_state.pop(cid, None)
        if st:
            L = [f"👁 Это {st.get('answer','')}", "", f"💬 {st.get('quote','')}"]
            if st.get("quote_ru"):
                L.append(f"<i>{_esc(st['quote_ru'])}</i>")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕵️ Загадать ещё", callback_data="game_again")]])
            await context.bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
        return
    if data == "game_change":
        await q.message.reply_text("На каком языке подсказки?", reply_markup=await _game_lang_kb())
        return
    if data.startswith("again_"):
        what = data[len("again_"):]
        if what == "tr_nl":
            context.args = []
            await _do_translate(context.bot, cid, "нидерландский")
        elif what == "tr_en":
            context.args = ["en"]
            await _do_translate(context.bot, cid, "английский")
        elif what == "gram_nl":
            await send_grammar(context.bot, cid, "нидерландский", "🇳🇱")
        elif what == "gram_en":
            await send_grammar(context.bot, cid, "английский", "🇬🇧")
        return
    if data.startswith("reco_"):
        i = int(data.split("_")[1])
        rec = last_recos.get(cid)
        if rec and i < len(rec["items"]):
            title = rec["items"][i]
            key = WATCHLIST_KEY if rec["kind"] == "movie" else READLIST_KEY
            add_to_list(key, cid, title)
            await q.message.reply_text(f"Добавил в список: {title}")
        return
    if data.startswith("facts_"):
        i = int(data.split("_")[1])
        countries = suggested_countries.get(cid, [])
        if i < len(countries):
            await q.message.reply_text("Собираю факты...")
            try:
                await send_long(context.bot, cid, country_facts(countries[i]))
            except Exception as e:
                await q.message.reply_text(f"Ошибка: {e}")
        return
    if data.startswith("delcountry_"):
        i = int(data.split("_")[1])
        favs = get_list(FAVCOUNTRIES_KEY, cid)
        if i < len(favs):
            removed = favs.pop(i)
            set_list(FAVCOUNTRIES_KEY, cid, favs)
            await q.message.reply_text(f"Удалил: {removed.get('name','')}")
        return
    if data.startswith("worry_"):
        _, action, idx = data.split("_")
        i = int(idx)
        worries = get_list(WORRIES_KEY, cid)
        if i < len(worries):
            worries[i]["status"] = "real" if action == "real" else "let_go"
            set_list(WORRIES_KEY, cid, worries)
            # записать в дневник итог, если всё отмечено
            if all(w.get("status") != "pending" for w in worries):
                real = [w["text"] for w in worries if w["status"] == "real"]
                summary = f"Тревог: {len(worries)}, реально: {len(real)}, отпущено: {len(worries)-len(real)}"
                entries = get_list(DIARY_KEY, cid)
                entries.append({"date": datetime.now(TZ).strftime("%d.%m"), "text": summary})
                set_list(DIARY_KEY, cid, entries)
            await _show_worry_check(context.bot, cid)
        return

async def _do_translate(bot, cid, lang):
    level = get_level(cid, lang)
    try:
        ru = generate_challenge(lang, level)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    challenge_state[str(cid)] = {"ru": ru, "lang": lang}
    flag = "🇳🇱" if lang == "нидерландский" else "🇬🇧"
    await bot.send_message(chat_id=cid,
        text=f"{flag} Тренировка ({level})\n\nПереведи на {lang}:\n«{ru}»\n\nНапиши перевод на {lang} следующим сообщением.")

async def translate_command(update, context):
    cid = str(update.effective_chat.id)
    lang = "нидерландский"
    if context.args and context.args[0].lower() in ("en", "eng", "англ"):
        lang = "английский"
    await update.message.reply_text("Придумываю фразу...")
    await _do_translate(context.bot, cid, lang)

# --- Шкаф ---

async def generate_look_command(update, context):
    cid = update.effective_chat.id
    await update.message.reply_text("Собираю интересные комбинации...")
    try:
        await send_long(context.bot, cid, generate_look())
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def _send_recos(bot, cid, kind):
    try:
        data = content_recommend(kind, get_list(FAVORITES_KEY, str(cid)))
        items = data.get("items", [])
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}"); return
    last_recos[str(cid)] = {"kind": kind, "items": [it.get("title", "") for it in items]}
    head = "🎬 Что посмотреть" if kind == "movie" else "📖 Что почитать"
    lines = [head, ""]
    for it in items:
        lines.append(f"• {it.get('title','')}")
        lines.append(f"  {it.get('hook','')}")
        lines.append(f"  ⭐ ~{it.get('rating','')}/10")
    label = "🍿 В список" if kind == "movie" else "📚 В список"
    rows = [[InlineKeyboardButton(f"{label}: {it.get('title','')[:28]}", callback_data=f"reco_{i}")]
            for i, it in enumerate(items)]
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

async def _send_daycheck(bot, cid):
    cid = str(cid)
    worries = get_list(WORRIES_KEY, cid)
    pending = [w for w in worries if w.get("status") == "pending"]
    if not pending:
        pending_input[cid] = "worry"
        await bot.send_message(chat_id=cid,
            text="🌙 Дим, как вечер?\n\nЧто сегодня шумело в голове? Напиши тревоги одним сообщением, каждую с новой строки - вечером проверим, что реально случилось.")
        return
    await _show_worry_check(bot, cid)

async def start_day_check(update, context):
    await _send_daycheck(context.bot, update.effective_chat.id)

async def _send_travel_go(bot, cid):
    cid = str(cid)
    data = travel_suggest_data()
    items = data.get("items", [])
    suggested_countries[cid] = [it.get("country", "") for it in items]
    lines = ["🗺 Куда поехать", ""]
    for it in items:
        lines.append(f"{it.get('flag','')} {it.get('country','')} - {it.get('why','')}")
    rows = [[InlineKeyboardButton(f"📍 10 фактов: {it.get('country','')}", callback_data=f"facts_{i}")]
            for i, it in enumerate(items)]
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

async def _send_travel_my(bot, cid):
    cid = str(cid)
    favs = get_list(FAVCOUNTRIES_KEY, cid)
    out = ["🏳 Любимые страны:"]
    if favs:
        rows = []
        for i, c in enumerate(favs):
            out.append(f"{c.get('flag','🏳')} {c.get('name','')}")
            rows.append([InlineKeyboardButton(f"❌ {c.get('name','')}", callback_data=f"delcountry_{i}")])
        await bot.send_message(chat_id=cid, text="\n".join(out), reply_markup=InlineKeyboardMarkup(rows))
    else:
        await bot.send_message(chat_id=cid, text="🏳 Список пуст.")
    pending_input[cid] = "favcountry"
    await bot.send_message(chat_id=cid, text="➕ Добавить любимую страну - напиши её название.")

async def _show_worry_check(bot, cid):
    worries = get_list(WORRIES_KEY, str(cid))
    total = len(worries)
    resolved = sum(1 for w in worries if w.get("status") in ("real", "let_go"))
    pct = int(resolved / total * 100) if total else 0
    bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
    lines = ["🧠 Проверка дня", f"🧹 Ментальная разгрузка: {pct}%", bar, "",
             "Отметь по каждой тревоге - реально случилось или можно отпустить:"]
    rows = []
    for i, w in enumerate(worries):
        mark = {"real": "✅", "let_go": "🧹", "pending": "❓"}.get(w.get("status", "pending"), "❓")
        lines.append(f"{mark} {w['text']}")
        if w.get("status") == "pending":
            rows.append([
                InlineKeyboardButton("📌 Случилось", callback_data=f"worry_real_{i}"),
                InlineKeyboardButton("🧹 Отпустить", callback_data=f"worry_let_{i}"),
            ])
    await bot.send_message(chat_id=cid, text="\n".join(lines),
                           reply_markup=InlineKeyboardMarkup(rows) if rows else None)

async def _send_game(bot, cid):
    cfg = game_config.get(str(cid), {"lang": "нидерландский", "difficulty": "med"})
    try:
        d = game_data(cfg["lang"], cfg["difficulty"])
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    game_state[str(cid)] = {"answer": d.get("answer", ""), "quote": d.get("quote", ""),
                            "quote_ru": d.get("quote_ru", ""), "hint": d.get("hint", ""), "tries": 0}
    diff_ru = {"easy": "лёгкая", "med": "средняя", "hard": "тяжёлая"}.get(cfg["difficulty"], "средняя")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Подсказка", callback_data="game_hint"),
         InlineKeyboardButton("👁 Показать ответ", callback_data="game_reveal")],
        [InlineKeyboardButton("🔁 Сменить язык/сложность", callback_data="game_change")],
    ])
    await bot.send_message(chat_id=cid,
        text=f"🕵️ Детектив ({cfg['lang']}, {diff_ru})\n\n{d.get('clues','')}\n\nНапиши имя. Ответ можно на любом языке, опечатка ок.",
        reply_markup=kb)

async def _game_lang_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇷🇺 Русский", callback_data="gamelang_ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="gamelang_en"),
        InlineKeyboardButton("🇳🇱 Nederlands", callback_data="gamelang_nl"),
    ]])

async def game_start(update, context):
    await update.message.reply_text("🕵️ Игра-детектив. На каком языке подсказки?", reply_markup=await _game_lang_kb())

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

    # Меню теперь инлайн (в answer_callback). Здесь - режимы ввода, игра, перевод, чат.

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
            name, interval = parse_plant(text)
            pl = get_list(PLANTS_KEY, cid)
            today = datetime.now(TZ).date()
            pl.append({"name": name, "interval": interval,
                       "next": (today + timedelta(days=interval)).isoformat()})
            set_list(PLANTS_KEY, cid, pl)
            await update.message.reply_text(f"Добавил: {name}, полив раз в {interval} дн.")
            return
        if kind == "favcountry":
            flag = country_flag(text.strip())
            favs = get_list(FAVCOUNTRIES_KEY, cid)
            favs.append({"name": text.strip(), "flag": flag})
            set_list(FAVCOUNTRIES_KEY, cid, favs)
            await update.message.reply_text(f"Добавил: {flag} {text.strip()}")
            return
        if kind == "worry":
            items = [{"text": w.strip(), "status": "pending"} for w in text.split("\n") if w.strip()]
            set_list(WORRIES_KEY, cid, items)
            await update.message.reply_text(f"Записал тревог: {len(items)}. Вечером проверим, что реально случилось.")
            return

    # ===== Игра: ответ-догадка =====
    if cid in game_state:
        st = game_state[cid]
        ans = st["answer"].lower().strip()
        guess = text.lower().strip()
        def close(a, b):
            if a in b or b in a:
                return True
            if abs(len(a) - len(b)) <= 1:
                diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
                return diff <= 1
            return False
        correct = any(close(guess, part) for part in [ans] + ans.split())
        if correct:
            game_state.pop(cid, None)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕵️ Загадать ещё", callback_data="game_again")]])
            L = ["✅ Верно!", "", f"💬 {st.get('quote','')}"]
            if st.get("quote_ru"):
                L.append(f"<i>{_esc(st['quote_ru'])}</i>")
            await context.bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
            return
        # не угадал
        st["tries"] = st.get("tries", 0) + 1
        if st["tries"] >= 2:
            game_state.pop(cid, None)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕵️ Загадать ещё", callback_data="game_again")]])
            await context.bot.send_message(chat_id=cid, text=f"❌ Не угадал. Это {st['answer']}.", reply_markup=kb)
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("💡 Подсказка", callback_data="game_hint"),
                InlineKeyboardButton("👁 Показать ответ", callback_data="game_reveal")],
                [InlineKeyboardButton("🔁 Сменить язык/сложность", callback_data="game_change")]])
            await update.message.reply_text("❌ Не то. Ещё попытка - напиши имя или возьми подсказку.", reply_markup=kb)
        return

    # ===== Перевод-челлендж =====
    if cid in challenge_state:
        st = challenge_state.pop(cid)
        await update.message.reply_text("Проверяю...")
        try:
            r = check_translation(st["lang"], st["ru"], text)
        except Exception as e:
            await update.message.reply_text(f"Ошибка проверки: {e}")
            return
        flag = "🇳🇱" if st["lang"] == "нидерландский" else "🇬🇧"
        L = [f"{flag} Перевод"]
        if r.get("ok"):
            L += ["", "✅ Верно!"]
            if r.get("correct"):
                L += ["", "💡 Естественнее", r["correct"]]
        else:
            if r.get("error"):
                L += ["", "❌ Ошибка", r["error"]]
            if r.get("correct"):
                L += ["", "💡 Правильно", r["correct"]]
        simple = r.get("simple") or []
        if simple:
            L += ["", "🧠 Просто"] + [f"• {x}" for x in simple]
        if r.get("easier"):
            L += ["", "✔️ Можно проще", r["easier"]]
        arg = "tr_en" if st["lang"] == "английский" else "tr_nl"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚡ Ещё фраза", callback_data=f"again_{arg}")]])
        await context.bot.send_message(chat_id=cid, text="\n".join(L), reply_markup=kb)
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
    await app.bot.set_my_commands([BotCommand("start", "меню")])


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
    app.add_handler(CallbackQueryHandler(answer_callback))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    jq.run_daily(job_morning, time=datetime.strptime("08:30", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_dutch, time=datetime.strptime("11:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_checkin_day, time=datetime.strptime("14:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_checkin_evening, time=datetime.strptime("20:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_weekly, time=datetime.strptime("19:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=(6,))  # воскресенье
    jq.run_daily(job_water, time=datetime.strptime("10:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()