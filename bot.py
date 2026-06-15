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
    "Сейчас один шаг, не вся жизнь.",
    "Не нужно идеально. Нужно начать.",
    "Я не ленивый. Мозг так работает.",
    "Остановись, выдохни, действуй.",
    "Пауза сейчас - победа.",
    "Фокус на хорошем.",
    "Не все споры стоят нервов.",
    "Чужие эмоции - не моя ответственность.",
    "Перемены открывают возможности.",
    "Скука - криптонит. Создавай интерес.",
    "Это состояние пройдёт.",
    "Делаю лучшее из возможного сегодня.",
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

# ---------- Files ----------

def _load(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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
    return _load(WARDROBE_FILE)

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

# ---------- Commands ----------

MENU = (
    "📋 План - одежда и погода (сегодня / завтра / 3 дня)\n"
    "👔 Шкаф - сгенерировать лук, советы к покупке, добавить одежду\n"
    "🌍 Изучение языков - нидерландский и английский (уроки + перевод + уровень)\n\n"
    "Геолокация или /setcity Город - сменить город."
)

# --- Многоуровневое меню ---
MAIN_KB = ReplyKeyboardMarkup([["📋 План"], ["👔 Шкаф"], ["🌍 Изучение языков"]], resize_keyboard=True)
PLAN_KB = ReplyKeyboardMarkup([["📅 Сегодня", "📅 Завтра"], ["🗓️ На 3 дня"], ["⬅️ Назад"]], resize_keyboard=True)
WARDROBE_KB = ReplyKeyboardMarkup([["✨ Сгенерировать лук"], ["🛍️ Советы к покупке"], ["📤 Добавить одежду"], ["⬅️ Назад"]], resize_keyboard=True)
LANG_KB = ReplyKeyboardMarkup([["🇳🇱 Нидерландский"], ["🇬🇧 Английский"], ["⚙️ Уровень языка"], ["⬅️ Назад"]], resize_keyboard=True)
NL_KB = ReplyKeyboardMarkup([["📖 Урок NL", "⚡ Перевод NL"], ["⬅️ Назад"]], resize_keyboard=True)
EN_KB = ReplyKeyboardMarkup([["📖 Урок EN", "⚡ Перевод EN"], ["⬅️ Назад"]], resize_keyboard=True)
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


async def start(update, context):
    await update.message.reply_text(f"Привет! Твой ассистент DM.\n\n{MENU}", reply_markup=MAIN_KB)


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
        data = fetch_weather(s["lat"], s["lon"], max(days, 1))
        if days == 1:
            await update.message.reply_text(weather_block(data, 0, s["city"]))
            return
        d = data["daily"]
        out = [f"📍 {s['city']} - прогноз на {days} дн.", ""]
        names = ["Сегодня", "Завтра"]
        for i in range(days):
            label = names[i] if i < 2 else d["time"][i]
            code = d["weathercode"][i]
            out.append(f"{EMOJI.get(code,'🌡️')} {label}: {d['temperature_2m_min'][i]:.0f}-{d['temperature_2m_max'][i]:.0f}°C, "
                       f"{DESC.get(code,'')}, ветер {d['windspeed_10m_max'][i]:.0f} м/с, дождь {d['precipitation_probability_max'][i] or 0:.0f}%")
        await update.message.reply_text("\n".join(out))
    except Exception as e:
        await update.message.reply_text(f"Ошибка погоды: {e}")

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

    # --- Навигация по меню ---
    if text == "📋 План":
        await update.message.reply_text("Выбери период:", reply_markup=PLAN_KB)
        return
    if text == "👔 Шкаф":
        text_router_state[cid] = "wardrobe"
        await update.message.reply_text("Шкаф:", reply_markup=WARDROBE_KB)
        return
    if text == "🌍 Изучение языков":
        text_router_state[cid] = "lang"
        await update.message.reply_text("Выбери язык:", reply_markup=LANG_KB)
        return
    if text == "🇳🇱 Нидерландский":
        text_router_state[cid] = "nl"
        await update.message.reply_text("Нидерландский:", reply_markup=NL_KB)
        return
    if text == "🇬🇧 Английский":
        text_router_state[cid] = "en"
        await update.message.reply_text("Английский:", reply_markup=EN_KB)
        return
    if text == "⚙️ Уровень языка":
        nl_lvl, en_lvl = get_level(cid, "нидерландский"), get_level(cid, "английский")
        kb_nl = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_nl_{l}") for l in LEVELS]])
        kb_en = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_en_{l}") for l in LEVELS]])
        await update.message.reply_text(f"🇳🇱 Уровень нидерландского (сейчас {nl_lvl}):", reply_markup=kb_nl)
        await update.message.reply_text(f"🇬🇧 Уровень английского (сейчас {en_lvl}):", reply_markup=kb_en)
        return
    if text == "⬅️ Назад":
        add_wardrobe_mode.pop(cid, None)
        if text_router_state.get(cid) in ("nl", "en"):
            text_router_state[cid] = "lang"
            await update.message.reply_text("Выбери язык:", reply_markup=LANG_KB)
        else:
            text_router_state[cid] = "main"
            await update.message.reply_text("Главное меню:", reply_markup=MAIN_KB)
        return

    # --- Действия: План ---
    if text == "📅 Сегодня":
        await plan_command(update, context); return
    if text == "📅 Завтра":
        await tomorrow_command(update, context); return
    if text == "🗓️ На 3 дня":
        await plan3_command(update, context); return

    # --- Действия: Шкаф ---
    if text == "✨ Сгенерировать лук":
        await generate_look_command(update, context); return
    if text == "🛍️ Советы к покупке":
        await shopping_command(update, context); return
    if text == "📤 Добавить одежду":
        await add_clothes_start(update, context); return

    # --- Действия: языки ---
    if text == "📖 Урок NL":
        text_router_state[cid] = "nl"; await dutch_command(update, context); return
    if text == "📖 Урок EN":
        text_router_state[cid] = "en"; await english_command(update, context); return
    if text == "⚡ Перевод NL":
        text_router_state[cid] = "nl"; context.args = []; await translate_command(update, context); return
    if text == "⚡ Перевод EN":
        text_router_state[cid] = "en"; context.args = ["en"]; await translate_command(update, context); return

    # --- Режим добавления одежды: текст = список вещей ---
    if add_wardrobe_mode.get(cid):
        await _ingest_wardrobe(update, text)
        return

    if cid in challenge_state:
        st = challenge_state.pop(cid)
        await update.message.reply_text("Проверяю...")
        try:
            fb = check_translation(st["lang"], st["ru"], text)
        except Exception as e:
            await update.message.reply_text(f"Ошибка проверки: {e}")
            return
        await send_long(context.bot, cid, fb + "\n\n/translate - ещё фраза")
        return

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
        BotCommand("plan3", "погода на 3 дня"),
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
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("tomorrow", tomorrow_command))
    app.add_handler(CommandHandler("plan3", plan3_command))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("setcity", setcity_command))
    app.add_handler(CommandHandler("dutch", dutch_command))
    app.add_handler(CommandHandler("english", english_command))
    app.add_handler(CommandHandler("translate", translate_command))
    app.add_handler(CallbackQueryHandler(answer_callback, pattern="^(lesson_answer|lvl_)"))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    jq.run_daily(job_morning, time=datetime.strptime("08:30", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(job_dutch, time=datetime.strptime("11:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()