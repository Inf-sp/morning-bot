from datetime import datetime
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import weather
import wardrobe
from util import esc, send_long, _WEEKDAYS, _MONTHS

TZ = config.TZ

# --- Сводка дня (Мой день) ---
def plany_extras():
    country = random.choice([c.strip() for c in config.VISITED.split(",")])
    prompt = f"""Сгенерируй блоки для ежедневной сводки (учит нидерландский и английский).
ВАЖНО: строго валидный JSON, экранируй кавычки, без переносов внутри значений.
{{
 "place_country": "{country}",
 "place_text": "2-3 коротких факта про {country}",
 "fact": "новый научный факт с конкретикой (где/что именно), 1-2 предложения",
 "word_ru": "русское слово дня (одно слово)",
 "word_nl": "перевод на нидерландский",
 "word_en": "перевод на английский",
 "example_nl": "пример на нидерландском",
 "example_ru": "перевод примера",
 "quote": "мотивирующая позитивная цитата (1-2 предложения) из книги: {config.FAV_BOOKS}",
 "quote_book": "название книги и автор"
}}"""
    return ai.llm_json(prompt, 1000)

async def send_plany(bot, cid):
    s = store.get_settings(cid)
    data = weather.fetch_weather(s["lat"], s["lon"], 2)
    cur = data["current"]
    d = data["daily"]
    temp = cur["temperature_2m"]
    code = cur["weathercode"]
    rain = d["precipitation_probability_max"][0] or 0
    wind_kmh = (d["windspeed_10m_max"][0] or 0) * 3.6
    wind_ms = d["windspeed_10m_max"][0] or 0
    icon = weather.weather_icon(code, temp, rain, wind_kmh)
    of = wardrobe.build_outfit_focus(weather.weather_block(data, 0, s["city"]), "сегодня")
    ex = plany_extras()
    now = datetime.now(TZ)
    header = f"{_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS[now.month-1]}"
    L = [f"🧭 <b>Мой день | {header}</b>", "", "<b>Погода</b>",
         f"{icon} {esc(s['city'])}: {temp:+.0f}°C • Вероятность дождя {rain:.0f}% • 💨 {wind_ms:.0f} м/с",
         "", "<b>Лук дня</b>", esc(", ".join(of.get("outfit", []))),
         "", "<b>Слово дня</b>", esc(ex.get("word_ru", "")),
         f"🇳🇱 {esc(ex.get('word_nl',''))} / 🇬🇧 {esc(ex.get('word_en',''))}",
         f"<i>{esc(ex.get('example_nl',''))} ({esc(ex.get('example_ru',''))})</i>",
         "", "🔬 <b>Интересный научный факт</b>", esc(ex.get("fact", ""))]
    if ex.get("quote"):
        L += ["", "📖 <b>Цитата дня</b>", esc(ex.get("quote", "")), f"<i>— {esc(ex.get('quote_book',''))}</i>"]
    if ex.get("place_text"):
        L += ["", f"🗺️ <b>Место дня: {esc(ex.get('place_country',''))}</b>", esc(ex.get("place_text", ""))]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")

# --- Утро ---
def morning_greeting(weather_short):
    prompt = f"""Короткое утреннее приветствие Дмитрию (по-русски, можно с лёгкой дерзостью).
Погода сегодня: {weather_short}
2-4 строки: приветствие с характером + мини-настрой. В конце ОДИН совет по духу его установок (НЕ про одежду):
{config.LAGOM}
Без markdown и звёздочек."""
    return ai.llm(prompt, 400, 0.95)

def assemble_morning(chat_id):
    s = store.get_settings(chat_id)
    data = weather.fetch_weather(s["lat"], s["lon"], days=2)
    wblock = weather.weather_block(data, 0, s["city"])
    of = wardrobe.build_outfit_focus(wblock, "сегодня")
    try:
        greet = morning_greeting(wblock)
    except Exception:
        greet = "Доброе утро. Один шаг за раз - этого достаточно."
    parts = [greet, "", "— — —", "", wblock, "", "👕 Лук дня", ", ".join(of.get("outfit", []))]
    return "\n".join(parts)

# --- Мотивация / проверка дня ---
def diary_reflect(entry):
    prompt = f"""Запись дневника Дмитрия: "{entry}"
Ответь как спокойный мини-психолог: 2-3 предложения поддержки и одна практичная мысль.
{config.LAGOM}
Без markdown."""
    return ai.llm(prompt, 400, 0.8)

async def send_daycheck(bot, cid):
    cid = str(cid)
    worries = store.get_list(config.WORRIES_KEY, cid)
    pending = [w for w in worries if w.get("status") == "pending"]
    if not pending:
        store.pending_input[cid] = "worry"
        await bot.send_message(chat_id=cid,
            text="🌙 Дим, как вечер?\n\nЧто сегодня шумело в голове? Напиши тревоги одним сообщением, каждую с новой строки - проверим, что реально случилось.")
        return
    await show_worry_check(bot, cid)

async def show_worry_check(bot, cid):
    cid = str(cid)
    worries = store.get_list(config.WORRIES_KEY, cid)
    total = len(worries)
    resolved = sum(1 for w in worries if w.get("status") in ("real", "let_go"))
    let_go = sum(1 for w in worries if w.get("status") == "let_go")
    pct = int(100 * let_go / total) if total else 0
    bar = "▓" * (pct // 10) + "░" * (10 - pct // 10)
    lines = [f"🧠 Проверка дня", f"🧹 Ментальная разгрузка: {pct}%", bar, ""]
    rows = []
    for i, w in enumerate(worries):
        mark = {"real": "📌", "let_go": "🧹"}.get(w.get("status"), "•")
        lines.append(f"{mark} {w['text']}")
        if w.get("status") == "pending":
            rows.append([InlineKeyboardButton(f"📌 Случилось", callback_data=f"worry_real_{i}"),
                         InlineKeyboardButton(f"🧹 Отпустить", callback_data=f"worry_let_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_myday")])
    if resolved == total and total:
        lines += ["", "Готово. Чем больше отпускаешь шума - тем чище голова."]
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

async def worry_mark(bot, cid, i, status):
    cid = str(cid)
    worries = store.get_list(config.WORRIES_KEY, cid)
    if i < len(worries):
        worries[i]["status"] = status
        store.set_list(config.WORRIES_KEY, cid, worries)
        if all(w.get("status") != "pending" for w in worries):
            real = [w["text"] for w in worries if w["status"] == "real"]
            summary = f"Тревог: {len(worries)}, реально: {len(real)}, отпущено: {len(worries)-len(real)}"
            store.add_to_list(config.DIARY_KEY, cid, {"date": datetime.now(TZ).strftime("%d.%m"), "text": summary})
        await show_worry_check(bot, cid)

async def save_worries(bot, cid, text):
    items = [{"text": w.strip(), "status": "pending"} for w in text.split("\n") if w.strip()]
    store.set_list(config.WORRIES_KEY, cid, items)
    await bot.send_message(chat_id=cid, text=f"Записал тревог: {len(items)}. Вечером проверим, что реально случилось.")

async def save_diary(bot, cid, text):
    store.add_to_list(config.DIARY_KEY, cid, {"date": datetime.now(TZ).strftime("%d.%m"), "text": text})
    try:
        await send_long(bot, cid, diary_reflect(text))
    except Exception:
        await bot.send_message(chat_id=cid, text="Записал в дневник.")

async def send_diary(bot, cid):
    entries = store.get_list(config.DIARY_KEY, cid)
    if not entries:
        await bot.send_message(chat_id=cid, text="Дневник пуст. Записи появятся после проверки дня.")
    else:
        last = entries[-7:]
        await send_long(bot, cid, "📊 Последние записи\n\n" + "\n\n".join(f"{e['date']}: {e['text']}" for e in last))

async def send_phrase(bot, cid):
    await bot.send_message(chat_id=cid, text="🌿 " + config.lagom_of_day())
