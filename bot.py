import os
import asyncio
import anthropic
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Keys ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
WEATHER_API_KEY = os.environ["WEATHER_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CITY = "Alkmaar"
CHAT_ID = os.environ.get("CHAT_ID", "")

# --- Storage for plans ---
plans_storage = {}

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def get_weather():
    url = f"https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": CITY,
        "appid": WEATHER_API_KEY,
        "units": "metric",
        "lang": "ru"
    }
    r = requests.get(url, params=params)
    data = r.json()
    temp = data["main"]["temp"]
    feels = data["main"]["feels_like"]
    desc = data["weather"][0]["description"]
    wind = data["wind"]["speed"]
    return f"{desc}, {temp:.0f}°C (ощущается {feels:.0f}°C), ветер {wind:.0f} м/с"


def generate_morning_brief(weather: str, plans: str):
    prompt = f"""Ты личный стилист и утренний помощник. 
    
Погода сегодня в Алкмаре: {weather}

Планы на день: {plans if plans else "не указаны"}

Параметры владельца: рост 179 см, вес ~65 кг, обувь EU 42.5, джинсы W31 L31.
Стиль: минимализм, базовые цвета, натуральные ткани, плотные футболки, массивная удобная обувь.

Напиши короткое утреннее сообщение:
1. Погода одной строкой
2. Лук на день — конкретно (3-4 предмета), учитывая погоду и планы
3. Одна практичная подсказка на день

Тон: дружелюбный, лаконичный. Без лишних слов."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


async def send_morning_brief(context: ContextTypes.DEFAULT_TYPE):
    chat_id = CHAT_ID
    if not chat_id:
        return

    weather = get_weather()
    plans = plans_storage.get(chat_id, "")

    text = generate_morning_brief(weather, plans)

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"☀️ *Доброе утро!*\n\n{text}",
        parse_mode="Markdown"
    )

    # Reset plans after morning send
    plans_storage[chat_id] = ""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text(
        f"Привет! Я твой утренний бот.\n\n"
        f"Твой Chat ID: `{chat_id}`\n\n"
        f"Команды:\n"
        f"/plans — посмотреть планы на завтра\n"
        f"/test — получить утреннюю сводку прямо сейчас\n\n"
        f"Просто напиши мне планы на завтра, и утром получишь сводку с луком.",
        parse_mode="Markdown"
    )


async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    plans = plans_storage.get(chat_id, "")
    if plans:
        await update.message.reply_text(f"📋 Планы на завтра:\n{plans}")
    else:
        await update.message.reply_text("Планов нет. Напиши что планируешь — запомню.")


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text("Генерирую сводку...")

    try:
        weather = get_weather()
    except Exception as e:
        await update.message.reply_text(f"Ошибка погоды: {e}")
        return

    try:
        plans = plans_storage.get(chat_id, "")
        text = generate_morning_brief(weather, plans)
    except Exception as e:
        await update.message.reply_text(f"Ошибка Claude: {e}")
        return

    await update.message.reply_text(
        f"☀️ *Тестовая сводка*\n\n{text}",
        parse_mode="Markdown"
    )


async def save_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    text = update.message.text
    plans_storage[chat_id] = text
    await update.message.reply_text("✅ Записал! Утром напомню с луком на день.")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plans", plans_command))
    app.add_handler(CommandHandler("test", test_command))

    # Any text message = save as plans
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_plans))

    # Schedule morning brief at 7:30
    job_queue = app.job_queue
    job_queue.run_daily(
        send_morning_brief,
        time=datetime.strptime("07:30", "%H:%M").time(),
        days=(0, 1, 2, 3, 4, 5, 6)
    )

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()