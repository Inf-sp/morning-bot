import os
import json
import time
import requests
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Keys ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
CHAT_ID = os.environ.get("CHAT_ID", "")

# --- Storage ---
plans_storage = {}

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
        "wind_speed_unit": "ms"
    }
    r = requests.get(url, params=params)
    data = r.json()
    c = data["current"]
    temp = c["temperature_2m"]
    feels = c["apparent_temperature"]
    wind = c["windspeed_10m"]
    code = c["weathercode"]

    weather_codes = {
        0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность",
        3: "пасмурно", 45: "туман", 48: "туман с инеем",
        51: "лёгкая морось", 53: "морось", 55: "сильная морось",
        61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
        71: "небольшой снег", 73: "снег", 75: "сильный снег",
        80: "ливень", 81: "сильный ливень", 95: "гроза"
    }
    desc = weather_codes.get(code, f"код {code}")
    return f"{desc}, {temp:.0f}°C (ощущается {feels:.0f}°C), ветер {wind:.0f} м/с"


def generate_morning_brief(weather: str, plans: str, wardrobe: dict):
    wardrobe_text = wardrobe_to_text(wardrobe)

    prompt = f"""Ты личный стилист. Отвечай коротко и конкретно.

Погода в Алкмаре: {weather}
Планы: {plans if plans else "обычный день"}

Параметры: рост 179 см, вес ~65 кг, обувь EU 42.5, джинсы W31 L31

Гардероб:
{wardrobe_text}

Заметки стилиста:
{STYLE_NOTES}

Напиши:
1. Погода - одна строка
2. Лук - конкретно 3-4 предмета из гардероба выше, учитывай погоду и планы
3. Один совет на день

Формат: без маркдауна, без звёздочек, просто текст."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 500, "temperature": 0.7}
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text(
        f"Привет! Твой Chat ID: {chat_id}\n\n"
        f"Команды:\n"
        f"/test - сводка прямо сейчас\n"
        f"/plans - планы на завтра\n"
        f"/wardrobe - посмотреть гардероб\n"
        f"/add [категория] [вещь] - добавить вещь\n"
        f"/remove [категория] [вещь] - удалить вещь\n\n"
        f"Любой текст = планы на завтра."
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
    text = wardrobe_to_text(wardrobe)
    await update.message.reply_text(f"Гардероб:\n\n{text}")


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


async def save_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    plans_storage[chat_id] = update.message.text
    await update.message.reply_text("Записал. Утром напомню.")


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_plans))

    job_queue = app.job_queue
    job_queue.run_daily(
        send_morning_brief,
        time=datetime.strptime("07:30", "%H:%M").replace(tzinfo=timezone.utc).timetz(),
        days=(0, 1, 2, 3, 4, 5, 6)
    )

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()