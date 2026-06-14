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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

TZ = ZoneInfo("Europe/Amsterdam")

# --- Storage (resets on redeploy) ---
challenge_state = {}   # {chat_id: {"ru": "..."}}
chat_history = {}      # {chat_id: [ {role, content}, ... ]}

WARDROBE_FILE = "wardrobe.json"

# --- Лагом: личные принципы DM ---
LAGOM = """
Принципы (лагом) Дмитрия:
- Сейчас не вся жизнь. Сейчас один шаг.
- От чего наполняешься - то и монетизируй.
- Мне не нужно идеально. Мне нужно начать.
- Я не ленивый. Мой мозг так работает.
- Остановись. Выдохни. Потом действуй.
- Я делаю лучшее из возможного сегодня.
- Быть добрым и скромным недостаточно. Мир продвигает тех, кто умеет быть видимым.
- Не пропускай зло дальше себя.
- Мечты и риск важны, главное - двигаться вперёд.
- Любовь важна, но не единственное. Цени поддержку, создавай воспоминания.
- Фокус на хорошем и благодарность за мелочи.
- Не все споры стоят нервов.
- Уважай границы, говори открыто.
- Чужие эмоции - не моя ответственность.
- Требовать соблюдения своих прав - это здоровое поведение.
- Перемены открывают возможности.
- Окружение влияет - ищи своё, а не терпи.
- Книги - источник радости и роста.
- Путешествия важнее материального.
- Избавляйся от лишнего, чтобы освободить место новому.
- Баланс между работой, отдыхом и движением необходим.
- Скука - твой криптонит. Создавай интерес.
- Не забывай переключаться, но не убегать.
- Пауза сейчас - победа.
- Это состояние пройдёт. Мне не нужно решать всё сейчас. Я могу замедлиться.
"""

TRAVEL = """
Дмитрий любит путешествовать. Был в: Австрия, Беларусь, Бельгия, Великобритания, Венгрия,
Германия, Греция, Дания, Испания, Италия, Латвия, Литва, Мальта, Мексика, Нидерланды,
Норвегия, Польша, Португалия, Россия, Сербия, Сингапур, Словакия, Таиланд, Турция,
Финляндия, Франция, Черногория, Чехия, Швеция, Эстония, Япония, Ватикан, Люксембург.
Планы 2026: Грузия (16-26 апреля), Мадейра + Азоры (1-14 мая), Нормандия.
"""

STYLE_NOTES = """
- Коричневая рубашка Uniqlo - только с кремовым/белым низом и нейтральными брюками
- Чёрный с головы до ног - избегать
- NB - для активных выходов, Timberland - городской casual
- Цепочки не смешивать между собой
- Стиль: минимализм, скандинавская эстетика, базовые цвета, натуральные ткани
"""

TEMP_ZONES = """
Температурные правила (по ощущаемой температуре):
- ниже 5°C: флис + ветровка
- 5-12°C: свитшот + ветровка
- 12-18°C: рубашка или свитшот, ветровку взять с собой
- 18-23°C: футболка, рубашка сверху по желанию
- выше 23°C: футболка
- дождь или вероятность >50%: ветровка обязательно
- ветер сильнее 8 м/с: добавь слой
"""

# ---------- Gemini ----------

def gemini(prompt: str, max_tokens: int = 600, temperature: float = 0.7) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature}
    }
    last_err = None
    for _ in range(3):
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 429:
            last_err = "429 rate limit"
            time.sleep(5)
            continue
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception(f"Gemini перегружен ({last_err}). Подожди минуту.")

# ---------- Chat (Gemini, бесплатно) ----------

CHAT_SYSTEM = f"""Ты личный ассистент Дмитрия (DM). Отвечаешь в Telegram.

Кто он: инженер, дизайнер (UI/UX, графика, айдентика), фотограф. Живёт в Нидерландах.
Учит нидерландский (B1). У него СДВГ - давай структуру, короткие шаги.

Как общаться:
- как умный коллега, не как учитель
- прямо, без воды и канцелярита
- короткие предложения, ясная логика, конкретика
- проверяй слабые идеи на прочность, предлагай альтернативу
- короткое тире -, не длинное
- по-русски, если он не пишет на другом языке

Его принципы (учитывай по духу, не цитируй механически):
{LAGOM}
"""

def chat_reply(history: list) -> str:
    # history: [{"role": "user"/"assistant", "content": "..."}]
    contents = []
    for m in history:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "system_instruction": {"parts": [{"text": CHAT_SYSTEM}]},
        "contents": contents,
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.8}
    }
    last_err = None
    for _ in range(3):
        r = requests.post(url, json=payload, timeout=40)
        if r.status_code == 429:
            last_err = "429 rate limit"
            time.sleep(5)
            continue
        r.raise_for_status()
        data = r.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    raise Exception(f"Gemini перегружен ({last_err}). Подожди минуту.")


def claude_reply(history: list) -> str:
    """Claude Sonnet через HTTP. Бросает исключение при ошибке - вызывающий откатится на Gemini."""
    if not ANTHROPIC_API_KEY:
        raise Exception("no anthropic key")
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "system": CHAT_SYSTEM,
        "messages": history
    }
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data["content"][0]["text"]

# ---------- Wardrobe ----------

def load_wardrobe():
    if os.path.exists(WARDROBE_FILE):
        with open(WARDROBE_FILE, "r") as f:
            return json.load(f)
    return {}

def wardrobe_to_text(wardrobe):
    return "\n".join(f"{cat.capitalize()}: {', '.join(items)}" for cat, items in wardrobe.items())

# ---------- Weather ----------

def get_weather():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 52.63, "longitude": 4.74,
        "current": "temperature_2m,apparent_temperature,weathercode,windspeed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,precipitation_probability_max,weathercode,windspeed_10m_max",
        "hourly": "precipitation_probability",
        "timezone": "Europe/Amsterdam", "wind_speed_unit": "ms", "forecast_days": 1
    }
    r = requests.get(url, params=params, timeout=20)
    data = r.json()
    codes = {
        0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
        45: "туман", 48: "туман с инеем", 51: "лёгкая морось", 53: "морось", 55: "сильная морось",
        61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 71: "небольшой снег",
        73: "снег", 75: "сильный снег", 80: "ливень", 81: "сильный ливень", 95: "гроза"
    }
    c, d = data["current"], data["daily"]
    rain_when = ""
    try:
        hrs, pr = data["hourly"]["time"], data["hourly"]["precipitation_probability"]
        rainy = [h.split("T")[1][:5] for h, p in zip(hrs, pr) if p and p >= 50]
        if rainy:
            rain_when = f", дождь вероятен около {rainy[0]}"
    except Exception:
        pass
    return (
        f"Сейчас: {codes.get(c['weathercode'],'')}, {c['temperature_2m']:.0f}°C (ощущается {c['apparent_temperature']:.0f}°C)\n"
        f"День: {codes.get(d['weathercode'][0],'')}, от {d['temperature_2m_min'][0]:.0f} до {d['temperature_2m_max'][0]:.0f}°C "
        f"(ощущается {d['apparent_temperature_min'][0]:.0f}...{d['apparent_temperature_max'][0]:.0f}°C)\n"
        f"Дождь: {d['precipitation_probability_max'][0]:.0f}%{rain_when}\n"
        f"Ветер до {d['windspeed_10m_max'][0]:.0f} м/с"
    )

# ---------- Generators ----------

def generate_outfit(weather: str, plans: str = ""):
    wardrobe = load_wardrobe()
    prompt = f"""Ты личный стилист. Коротко и конкретно.

Погода в Алкмаре сегодня:
{weather}

Планы: {plans if plans else "обычный день"}

Параметры: рост 179 см, вес ~65 кг, обувь EU 42.5, джинсы W31 L31

Гардероб:
{wardrobe_to_text(wardrobe)}

Заметки стилиста:
{STYLE_NOTES}
{TEMP_ZONES}

Учитывай весь день. Если днём теплеет - предложи слои которые можно снять. Если дождь позже - напомни про ветровку.

Напиши:
1. Погода - кратко, главное на день
2. Лук - конкретно 3-4 предмета из гардероба выше, по правилам
3. Один совет на день

Без маркдауна и звёздочек, просто текст."""
    return gemini(prompt, max_tokens=900)


def generate_lagom(mode: str = "morning"):
    if mode == "morning":
        task = ("Напиши короткое утреннее обращение к Дмитрию. Начни со слов \"Доброе утро\". "
                "2-3 предложения: тёплое пожелание на день, опираясь по духу на 1-2 его принципа. "
                "Иногда можно лёгкая отсылка к путешествиям. Его голос: спокойный, прямой, без пафоса.")
    else:
        task = ("Напиши одну короткую мысль-настройку в стиле Дмитрия, опираясь на его принципы. "
                "Не утреннее приветствие, а отдельная фраза дня - другой ракурс. 1-2 предложения.")
    prompt = f"""{LAGOM}
{TRAVEL}

{task}
Без маркдауна и звёздочек. По-русски."""
    return gemini(prompt, max_tokens=300, temperature=0.95)


def generate_dutch_lesson():
    prompt = """Ты преподаватель нидерландского для русскоговорящего ученика уровня B1.
Лексика и грамматика уровня B1 - не примитивные. Бери менее очевидные слова, фразовые глаголы, разговорные обороты, нюансы порядка слов.
Каждый день тема разная.

Формат строго, без маркдауна и звёздочек:

СЛОВО ДНЯ
[слово] - [перевод]
Пример: [предложение на нидерландском]
Перевод: [перевод примера]

ФРАЗА ДНЯ (реальная ситуация: магазин, кафе, работа, улица)
[фраза] - [перевод]
Когда использовать: [короткое пояснение]

ГРАММАТИКА ДНЯ
[одна тема коротко: правило в 2-3 строки + пример с переводом]

Компактно, без воды."""
    return gemini(prompt, max_tokens=1200, temperature=0.9)


def generate_translation_challenge():
    prompt = """Дай ОДНУ фразу на русском для перевода на нидерландский.
Уровень B1: с придаточным, модальным глаголом, прошедшим временем или непростым порядком слов. Бытовая или рабочая ситуация.
Выведи ТОЛЬКО русскую фразу, без кавычек и пояснений."""
    return gemini(prompt, max_tokens=100, temperature=1.0).strip()


def check_translation(ru: str, answer: str):
    prompt = f"""Ученик (B1) переводит с русского на нидерландский.

Русская фраза: {ru}
Перевод ученика: {answer}

Проверь. Коротко, без маркдауна и звёздочек:
1. Верно или есть ошибки
2. Если ошибки - правильный вариант и в чём именно ошибка (грамматика, порядок слов, слово)
3. Если есть более естественный вариант - покажи

Конкретно и кратко. Тон коллеги, не учителя."""
    return gemini(prompt, max_tokens=400, temperature=0.4)

# ---------- Send helper ----------

async def send_long(bot, chat_id, text):
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000])

# ---------- Scheduled ----------

async def send_morning(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        intro = generate_lagom("morning")
        weather = get_weather()
        outfit = generate_outfit(weather)
        await send_long(context.bot, CHAT_ID, f"{intro}\n\n— — —\n\n{outfit}")
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка утренней сводки: {e}")


async def send_dutch(context: ContextTypes.DEFAULT_TYPE):
    if not CHAT_ID:
        return
    try:
        await send_long(context.bot, CHAT_ID, f"🇳🇱 Нидерландский на сегодня\n\n{generate_dutch_lesson()}")
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"Ошибка урока: {e}")

# ---------- Commands ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        intro = generate_lagom("morning")
    except Exception:
        intro = "Доброе утро. Один шаг за раз - этого достаточно."
    menu = (
        "/plan - что надеть сегодня (погода + лук)\n"
        "/weather - погода\n"
        "/lagom - фраза дня\n"
        "/dutch - урок нидерландского\n"
        "/vertaal - перевод-челлендж\n\n"
        "Расписание: 07:30 сводка, 12:00 урок NL\n\n"
        "Без команды - пишешь мне, отвечает Claude (при лимите - Gemini).\n"
        "Во время /vertaal твой текст - это перевод."
    )
    await update.message.reply_text(f"Привет! Твой ассистент DM.\n\n{intro}\n\n— — —\n\n{menu}")


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Смотрю погоду...")
    try:
        weather = get_weather()
    except Exception as e:
        await update.message.reply_text(f"Ошибка погоды: {e}")
        return
    try:
        await send_long(context.bot, update.effective_chat.id, generate_outfit(weather))
    except Exception as e:
        await update.message.reply_text(f"Ошибка Gemini: {e}")


async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(get_weather())
    except Exception as e:
        await update.message.reply_text(f"Ошибка погоды: {e}")


async def lagom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(generate_lagom("phrase"))
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def dutch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Готовлю урок...")
    try:
        await send_long(context.bot, update.effective_chat.id, f"🇳🇱 Урок дня\n\n{generate_dutch_lesson()}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


async def vertaal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text("Придумываю фразу...")
    try:
        ru = generate_translation_challenge()
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return
    challenge_state[chat_id] = {"ru": ru}
    await update.message.reply_text(f"Переведи на нидерландский:\n\n{ru}\n\nНапиши перевод следующим сообщением.")

# ---------- Text router ----------

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    text = update.message.text

    # Идёт челлендж - это перевод
    if chat_id in challenge_state:
        ru = challenge_state.pop(chat_id)["ru"]
        await update.message.reply_text("Проверяю...")
        try:
            fb = check_translation(ru, text)
        except Exception as e:
            await update.message.reply_text(f"Ошибка проверки: {e}")
            return
        await update.message.reply_text(fb + "\n\n/vertaal - ещё фраза")
        return

    # Иначе - свободный чат: Claude Sonnet, при ошибке откат на Gemini
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    hist = chat_history.get(chat_id, [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = claude_reply(hist)
    except Exception:
        try:
            answer = chat_reply(hist)
        except Exception as e:
            await update.message.reply_text(f"Ошибка чата: {e}")
            return
    hist.append({"role": "assistant", "content": answer})
    chat_history[chat_id] = hist[-10:]
    await send_long(context.bot, chat_id, answer)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("weather", weather_command))
    app.add_handler(CommandHandler("lagom", lagom_command))
    app.add_handler(CommandHandler("dutch", dutch_command))
    app.add_handler(CommandHandler("vertaal", vertaal_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    jq = app.job_queue
    jq.run_daily(send_morning, time=datetime.strptime("07:30", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))
    jq.run_daily(send_dutch, time=datetime.strptime("12:00", "%H:%M").replace(tzinfo=TZ).timetz(), days=tuple(range(7)))

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()