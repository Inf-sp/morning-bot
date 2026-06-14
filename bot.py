import os
import json
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Keys ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
CHAT_ID = os.environ.get("CHAT_ID", "")

TZ = ZoneInfo("Europe/Amsterdam")

# --- Storage ---
plans_storage = {}
challenge_state = {}  # {chat_id: {"ru": "..."}}

# --- Wardrobe ---
WARDROBE_FILE = "wardrobe.json"

DEFAULT_WARDROBE = {
    "футболки": ["белая", "чёрная", "фиолетовая", "бежевая", "мятная"],
    "рубашки": [
        "белая", "белая в синюю полоску", "голубая почти белая",
        "светло-серая мягкая мелкий белый квадрат", "зелёная с цветочным принтом",
        "бежевая с цветочным принтом", "чёрная", "коричневая плотная Uniqlo"
    ],
    "свитшоты": ["тёмно-зелёная", "тёмно-серая", "серая"],
    "верхняя одежда": [
        "бежевая лёгкая ветровка", "синяя ветровка тканевая в лёгкую полоску",
        "чёрная лёгкая ветровка", "фиолетовый флис Uniqlo"
    ],
    "брюки": ["чёрные", "коричнево-бежевые", "оливковые хаки", "джинсы"],
    "обувь": [
        "белые низкие кеды", "чёрные тонкие кеды",
        "NB чёрно-фиолетовые беговые", "Timberland городской формат"
    ],
    "носки": ["чёрные", "белые", "коричневые"],
    "кепки": ["чёрная", "бежевая"],
    "аксессуары": [
        "Casio чёрные цифровые тонкий ремешок",
        "цепочка толстая сталь",
        "цепочка тонкая со значком сторон света",
        "кольцо змея", "кольцо перышко тонкое", "кольцо якорь массивное",
        "очки чёрные", "очки радужные"
    ]
}

STYLE_NOTES = """
- Коричневая рубашка Uniqlo - только с кремовым/белым низом и нейтральными брюками
- Чёрный с головы до ног - избегать
- NB - для активных выходов, Timberland - городской casual
- Цепочки не смешивать между собой
- Стиль: минимализм, скандинавская эстетика, базовые цвета, натуральные ткани
"""

TEMP_ZONES = """
Температурные правила (ориентир по ощущаемой температуре):
- ниже 5°C: флис + ветровка сверху
- 5-12°C: свитшот + ветровка
- 12-18°C: рубашка или свитшот, ветровку взять с собой
- 18-23°C: футболка, рубашка сверху по желанию
- выше 23°C: футболка
- дождь или вероятность >50%: ветровка обязательно
- ветер сильнее 8 м/с: добавь один слой
"""

# ---------- Helpers ----------

def gemini(prompt: str, max_tokens: int = 600, temperature: float = 0.7) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}
    }
    last_err = None
    for attempt in range(3):
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 429:
            last_err = "429 rate limit"
            time.sleep(5)
            continue
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception(f"Gemini перегружен ({last_err}). Подожди минуту.")


def load_wardrobe():
    if os.path.exists(WARDROBE_FILE):
        with open(WARDROBE_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_WARDROBE.copy()

def save_wardrobe(wardrobe):
    with open(WARDROBE_FILE, "w") as f:
        json.dump(wardrobe, f, ensure_ascii=False, indent=2)

def wardrobe_to_text(wardrobe):
    lines = []
    for category, items in wardrobe.items():
        lines.append(f"{category.capitalize()}: {', '.join(items)}")
    return "\n".join(lines)


def get_weather():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 52.63,
        "longitude": 4.74,
        "current": "temperature_2m,apparent_temperature,weathercode,windspeed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,precipitation_probability_max,weathercode,windspeed_10m_max",
        "hourly": "precipitation_probability",
        "timezone": "Europe/Amsterdam",
        "wind_speed_unit": "ms",
        "forecast_days": 1
    }
    r = requests.get(url, params=params, timeout=20)
    data = r.json()

    weather_codes = {
        0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность",
        3: "пасмурно", 45: "туман", 48: "туман с инеем",
        51: "лёгкая морось", 53: "морось", 55: "сильная морось",
        61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
        71: "небольшой снег", 73: "снег", 75: "сильный снег",
        80: "ливень", 81: "сильный ливень", 95: "гроза"
    }

    c = data["current"]
    d = data["daily"]

    now_temp = c["temperature_2m"]
    now_feels = c["apparent_temperature"]
    now_desc = weather_codes.get(c["weathercode"], "")

    tmax = d["temperature_2m_max"][0]
    tmin = d["temperature_2m_min"][0]
    feels_max = d["apparent_temperature_max"][0]
    feels_min = d["apparent_temperature_min"][0]
    rain_prob = d["precipitation_probability_max"][0]
    wind_max = d["windspeed_10m_max"][0]
    day_desc = weather_codes.get(d["weathercode"][0], "")

    rain_when = ""
    try:
        hours = data["hourly"]["time"]
        probs = data["hourly"]["precipitation_probability"]
        rainy = [h.split("T")[1][:5] for h, p in zip(hours, probs) if p and p >= 50]
        if rainy:
            rain_when = f", дождь вероятен около {rainy[0]}"
    except Exception:
        pass

    return (
        f"Сейчас: {now_desc}, {now_temp:.0f}°C (ощущается {now_feels:.0f}°C)\n"
        f"День: {day_desc}, от {tmin:.0f} до {tmax:.0f}°C "
        f"(ощущается {feels_min:.0f}...{feels_max:.0f}°C)\n"
        f"Дождь: {rain_prob:.0f}%{rain_when}\n"
        f"Ветер до {wind_max:.0f} м/с"
    )


def generate_morning_brief(weather: str, plans: str, wardrobe: dict):
    wardrobe_text = wardrobe_to_text(wardrobe)
    prompt = f"""Ты личный стилист. Отвечай коротко и конкретно.

Погода в Алкмаре сегодня:
{weather}

Планы: {plans if plans else "обычный день"}

Параметры: рост 179 см, вес ~65 кг, обувь EU 42.5, джинсы W31 L31

Гардероб:
{wardrobe_text}

Заметки стилиста:
{STYLE_NOTES}

{TEMP_ZONES}

Учитывай весь день, а не только утро. Если днём теплеет - предложи слои которые можно снять. Если дождь позже - напомни взять ветровку.

Напиши:
1. Погода - кратко, главное на день
2. Лук - конкретно 3-4 предмета из гардероба выше, по температурным правилам
3. Один совет на день

Формат: без маркдауна, без звёздочек, просто текст."""
    return gemini(prompt, max_tokens=600)


def generate_dutch_lesson():
    prompt = """Ты преподаватель нидерландского для русскоговорящего ученика уровня A2-B1.
Составь короткий дневной урок. Каждый день тема должна быть разной.

Формат строго такой, без маркдауна и звёздочек:

СЛОВО ДНЯ
[нидерландское слово] - [перевод]
Пример: [предложение на нидерландском]
Перевод: [перевод примера]

ФРАЗА ДНЯ (реальная ситуация: магазин, кафе, работа, улица)
[фраза на нидерландском] - [перевод]
Когда использовать: [короткое пояснение]

ГРАММАТИКА ДНЯ
[одна тема, очень коротко: правило в 2-3 строки + один пример с переводом]

Держи всё компактно. Без лишней воды."""
    return gemini(prompt, max_tokens=800, temperature=0.9)


def generate_translation_challenge():
    prompt = """Дай ОДНУ фразу на русском для перевода на нидерландский.
Уровень A2-B1, бытовая ситуация (магазин, кафе, работа, дорога, быт).
Выведи ТОЛЬКО саму русскую фразу, без кавычек, без пояснений, без перевода."""
    return gemini(prompt, max_tokens=100, temperature=1.0).strip()


def check_translation(ru_phrase: str, user_answer: str):
    prompt = f"""Ученик переводит с русского на нидерландский.

Русская фраза: {ru_phrase}
Перевод ученика: {user_answer}

Проверь перевод. Ответь коротко, без маркдауна и звёздочек:
1. Верно или есть ошибки
2. Если есть ошибки - правильный вариант и в чём ошибка (грамматика, порядок слов, слово)
3. Если есть более естественный вариант - покажи его

Будь конкретным и кратким. Тон - как коллега, не как учитель."""
    return gemini(prompt, max_tokens=400, temperature=0.4)


# ---------- Scheduled ----------

async def send_morning_brief(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        weather = get_weather()
        plans = plans_storage.get(CHAT_ID, "")
        wardrobe = load_wardrobe()
        text = generate_morning_brief(weather, plans, wardrobe)
        await context.bot.send_message(chat_id=CHAT_ID, text=f"☀️ Доброе утро!\n\n{text}")
        plans_storage[CHAT_ID] = ""
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка: {e}")


async def send_dutch_lesson(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        text = generate_dutch_lesson()
        await context.bot.send_message(chat_id=CHAT_ID, text=f"🇳🇱 Нидерландский на сегодня\n\n{text}")
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка урока: {e}")


async def send_evening_checkin(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    await context.bot.send_message(
        chat_id=CHAT_ID,
        text="🌙 Какие планы на завтра?\n\nНапиши одним сообщением - утром учту в подборе одежды."
    )


# ---------- Commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text(
        f"Привет! Твой Chat ID: {chat_id}\n\n"
        f"Одежда и погода:\n"
        f"/test - сводка с луком сейчас\n"
        f"/plans - планы на завтра\n"
        f"/wardrobe - гардероб\n"
        f"/add [категория] [вещь]\n"
        f"/remove [категория] [вещь]\n\n"
        f"Нидерландский:\n"
        f"/dutch - урок дня (слово, фраза, грамматика)\n"
        f"/vertaal - челлендж: перевести фразу\n\n"
        f"Расписание:\n"
        f"07:30 сводка, 12:00 урок NL, 21:00 спрошу планы\n\n"
        f"Любой текст = планы на завтра.\n"
        f"Во время челленджа любой текст = твой перевод."
    )


async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    plans = plans_storage.get(chat_id, "")
    if plans:
        await update.message.reply_text(f"Планы на завтра:\n{plans}")
    else:
        await update.message.reply_text("Планов нет. Напиши что планируешь.")


async def wardrobe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wardrobe = load_wardrobe()
    await update.message.reply_text(f"Гардероб:\n\n{wardrobe_to_text(wardrobe)}")


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Формат: /add [категория] [вещь]\n\n"
            "Пример: /add рубашки красная льняная\n\n"
            "Категории: футболки, рубашки, свитшоты, верхняя одежда, брюки, обувь, носки, кепки, аксессуары"
        )
        return
    category = context.args[0].lower()
    item = " ".join(context.args[1:]).lower()
    wardrobe = load_wardrobe()
    if category not in wardrobe:
        wardrobe[category] = []
    if item in wardrobe[category]:
        await update.message.reply_text(f"'{item}' уже есть в {category}.")
        return
    wardrobe[category].append(item)
    save_wardrobe(wardrobe)
    await update.message.reply_text(f"Добавил: {item} → {category}")


async def remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Формат: /remove [категория] [вещь]\n\n"
            "Пример: /remove рубашки коричневая плотная uniqlo"
        )
        return
    category = context.args[0].lower()
    item = " ".join(context.args[1:]).lower()
    wardrobe = load_wardrobe()
    if category not in wardrobe:
        await update.message.reply_text(f"Категория '{category}' не найдена.")
        return
    matches = [i for i in wardrobe[category] if item in i.lower()]
    if not matches:
        await update.message.reply_text(f"'{item}' не найдено в {category}.")
        return
    wardrobe[category] = [i for i in wardrobe[category] if item not in i.lower()]
    save_wardrobe(wardrobe)
    await update.message.reply_text(f"Удалил из {category}: {', '.join(matches)}")


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Генерирую...")
    try:
        weather = get_weather()
    except Exception as e:
        await update.message.reply_text(f"Ошибка погоды: {e}")
        return
    try:
        chat_id = str(update.effective_chat.id)
        plans = plans_storage.get(chat_id, "")
        wardrobe = load_wardrobe()
        text = generate_morning_brief(weather, plans, wardrobe)
    except Exception as e:
        await update.message.reply_text(f"Ошибка Gemini: {e}")
        return
    await update.message.reply_text(f"Тест:\n\n{text}")


async def dutch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Готовлю урок...")
    try:
        text = generate_dutch_lesson()
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return
    await update.message.reply_text(f"🇳🇱 Урок дня\n\n{text}")


async def vertaal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text("Придумываю фразу...")
    try:
        ru = generate_translation_challenge()
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return
    challenge_state[chat_id] = {"ru": ru}
    await update.message.reply_text(
        f"Переведи на нидерландский:\n\n{ru}\n\nНапиши перевод следующим сообщением."
    )


# ---------- Text router ----------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    text = update.message.text

    # Если идёт челлендж - это ответ-перевод
    if chat_id in challenge_state:
        ru = challenge_state[chat_id]["ru"]
        del challenge_state[chat_id]
        await update.message.reply_text("Проверяю...")
        try:
            feedback = check_translation(ru, text)
        except Exception as e:
            await update.message.reply_text(f"Ошибка проверки: {e}")
            return
        await update.message.reply_text(feedback + "\n\n/vertaal - ещё одна фраза")
        return

    # Иначе - планы на завтра
    plans_storage[chat_id] = text
    await update.message.reply_text("Записал. Утром учту.")


def main():
    if not os.path.exists(WARDROBE_FILE):
        save_wardrobe(DEFAULT_WARDROBE)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(CommandHandler("plans", plans_command))
    app.add_handler(CommandHandler("wardrobe", wardrobe_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("remove", remove_command))
    app.add_handler(CommandHandler("dutch", dutch_command))
    app.add_handler(CommandHandler("vertaal", vertaal_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    jq.run_daily(send_morning_brief,
                 time=datetime.strptime("07:30", "%H:%M").replace(tzinfo=TZ).timetz(),
                 days=tuple(range(7)))
    jq.run_daily(send_dutch_lesson,
                 time=datetime.strptime("12:00", "%H:%M").replace(tzinfo=TZ).timetz(),
                 days=tuple(range(7)))
    jq.run_daily(send_evening_checkin,
                 time=datetime.strptime("21:00", "%H:%M").replace(tzinfo=TZ).timetz(),
                 days=tuple(range(7)))

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()