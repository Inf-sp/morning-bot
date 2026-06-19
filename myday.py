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
ROTATING = [
    ("🐱", "Факт о кошках"),
    ("🌈", "Факт о ЛГБТК+ истории"),
    ("🚲", "Велосипедный совет"),
    ("📷", "Совет по фотографии"),
    ("📚", "Книжная рекомендация"),
    ("🧠", "Факт о СДВГ"),
    ("✈️", "Страна для путешествия"),
    ("🎨", "Дизайн-фишка"),
    ("💡", "Бытовой лайфхак"),
]

def _wind_word(ms):
    if ms >= 14:
        return "штормовой"
    if ms >= 8:
        return "сильный"
    if ms >= 5:
        return "умеренный"
    return "слабый"

def plany_extras(country, date_str, city=""):
    prompt = f"""Сгенерируй блоки для ежедневной сводки. Дата: {date_str}. Город: {city}. Страна: {country}.
Строго валидный JSON, экранируй кавычки, без переносов внутри значений.
{{
 "event": "Реальное событие, привязанное к дате {date_str}. Приоритет: сначала событие/праздник в городе {city}, затем в стране {country}, затем в мире. Если на саму дату события нет - возьми то, что идёт именно на ЭТОЙ неделе (не раньше и не позже даты). Гос-праздники и крупные сезонные/культурные события (сельдь, тюльпаны, фестивали, Прайд, Неделя музеев). НЕ политика. 1-2 предложения по-русски.",
 "word_ru": "слово дня на русском (одно слово)",
 "word_nl": "перевод на нидерландский С АРТИКЛЕМ (de/het)",
 "word_en": "перевод на английский С АРТИКЛЕМ (a/the)",
 "idea": "1 бизнес-идея, 1-2 предложения, ВСЕГДА новая, придумай название в стиле научной фантастики",
 "facts": ["3 разных интересных факта (наука/технологии/природа), каждый НЕ больше 1 предложения, всегда новые"],
 "quote": "вдохновляющая цитата",
 "quote_src": "автор, год"
}}
Правила: НЕ повторяй одно и то же в word, idea и facts. Кратко."""
    return ai.llm_json(prompt, 1200)

_day_cache = {}  # cid -> {"date":..., "text":..., "ex":..., "outfit":...}

def _day_menu_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Полный прогноз на сегодня", callback_data="a_w_full")],
        [InlineKeyboardButton("🗓️ Погода на завтра", callback_data="a_w_tomorrow")],
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("🚀 Новая идея", callback_data="md_idea")],
        [InlineKeyboardButton("🌍 Сменить город", callback_data="a_setcity")],
        [InlineKeyboardButton("⭐ Добавить в избранное", callback_data="md_fav")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_close")],
    ])

def _build_day_text(cid):
    s = store.get_settings(cid)
    data = weather.fetch_weather(s["lat"], s["lon"], 2)
    d = data["daily"]
    day_str = d["time"][0]
    code = d["weathercode"][0]
    tmax = d["temperature_2m_max"][0]
    rain = d["precipitation_probability_max"][0] or 0
    wind_ms = d["windspeed_10m_max"][0] or 0
    icon = weather.weather_icon(code, tmax, rain, wind_ms)
    wemoji, wword = weather.wind_scale(wind_ms)
    rain_p = weather._periods(data, day_str, "precipitation_probability", 40)
    rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
    # ветер: подробно только если сильный
    if wind_ms >= 8:
        wind_p = weather._periods(data, day_str, "windspeed_10m", 6)
        wind_when = (" (" + ", ".join(wind_p) + ")") if wind_p else ""
        wind_str = f"{wemoji} {wword}{wind_when} {wind_ms:.0f} м/с"
    else:
        wind_str = f"💨 Ветер {wind_ms:.0f} м/с"

    of = wardrobe.build_outfit_focus(weather.weather_block(data, 0, s["city"]), "сегодня")
    ex = plany_extras(s.get("country", ""), day_str, s.get("city", ""))
    dict_words = store.get_list(config.DICT_KEY, cid)
    if dict_words:
        w = random.choice(dict_words[-20:])
        if isinstance(w, dict) and w.get("nl"):
            ex["word_ru"] = w.get("ru", ex.get("word_ru", ""))
            ex["word_nl"] = w.get("nl", ex.get("word_nl", ""))
            ex["word_en"] = w.get("en", ex.get("word_en", ""))

    now = datetime.now(TZ)
    header = f"{_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS[now.month-1]}"
    def cap(x): return x[:1].upper() + x[1:] if x else x
    L = [f"<b>Мой день • {esc(header)} • {esc(s.get('city',''))}</b>", ""]
    L += [f"<b>{icon} Погода сегодня</b>",
          f"До {tmax:+.0f}°C • Дождь{rain_when} {rain:.0f}% • {wind_str}", ""]
    if ex.get("event"):
        L += ["<b>🗓️ Важное событие</b>", esc(ex.get("event", "")), ""]
    outfit = " + ".join(of.get("outfit", [])).rstrip(".")
    L += ["<b>👕 Что надеть сегодня</b>", esc(outfit), ""]
    L += ["<b>📚 Слово дня</b>",
          f"{cap(esc(ex.get('word_ru','')))} → 🇳🇱 {cap(esc(ex.get('word_nl','')))} → 🇬🇧 {cap(esc(ex.get('word_en','')))}", ""]
    L += ["<b>🚀 Бизнес-идея</b>", esc(ex.get("idea", "").split(". ")[0].rstrip(".")), ""]
    L += ["<b>🔬 Интересные факты</b>"]
    facts = ex.get("facts", [])
    if isinstance(facts, str): facts = [facts]
    for f in facts[:3]:
        if f: L.append(f"• {esc(f.rstrip('.'))}")
    L.append("")
    if ex.get("quote"):
        L += ["<b>💭 Цитата</b>", f"«{esc(ex.get('quote',''))}» - ({esc(ex.get('quote_src',''))})"]
    text = "\n".join(L).strip()
    return text, ex, outfit, day_str

async def send_plany(bot, cid):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cache = _day_cache.get(str(cid))
    if not cache or cache.get("date") != today:
        await bot.send_message(chat_id=cid, text="Собираю сводку дня...")
        try:
            text, ex, outfit, _ = _build_day_text(cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=f"Ошибка: {e}"); return
        _day_cache[str(cid)] = {"date": today, "text": text, "ex": ex, "outfit": outfit}
    text = _day_cache[str(cid)]["text"]
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=_day_menu_kb())

async def handle_callback(bot, cid, q, data):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    cache = _day_cache.get(str(cid), {})
    ex = cache.get("ex", {})
    if data == "md_idea":
        import assistant
        await bot.send_message(chat_id=cid, text="Думаю...")
        try:
            out = assistant._gen_idea(cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        store.last_source[str(cid)] = "Идеи"
        store.last_answer[str(cid)] = out
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 Новая идея", callback_data="md_idea")],
                                   [InlineKeyboardButton("⭐ Добавить в избранное", callback_data="as_fav")],
                                   [InlineKeyboardButton("⬅️ Назад", callback_data="m_close")]])
        await bot.send_message(chat_id=cid, text=out, reply_markup=kb)
        return
    if data == "md_fav":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Сохранить бизнес-идею", callback_data="md_save_idea")],
            [InlineKeyboardButton("⭐ Сохранить цитату", callback_data="md_save_quote")],
            [InlineKeyboardButton("⭐ Сохранить событие", callback_data="md_save_event")],
            [InlineKeyboardButton("⭐ Сохранить образ дня", callback_data="md_save_look")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="m_close")],
        ])
        try:
            await q.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text="Что сохранить?", reply_markup=kb)
        return
    if data.startswith("md_save_"):
        what = data[len("md_save_"):]
        mapping = {
            "idea": ("Идеи", ex.get("idea", "")),
            "quote": ("Цитаты", (f"«{ex.get('quote','')}» - ({ex.get('quote_src','')})") if ex.get("quote") else ""),
            "event": ("События", ex.get("event", "")),
            "look": ("Образы", cache.get("outfit", "")),
        }
        cat, txt = mapping.get(what, ("Прочее", ""))
        if not txt:
            await bot.send_message(chat_id=cid, text="Нечего сохранять - открой «Мой день» заново."); return
        store.add_to_list(config.NOTES_KEY, cid, {"date": datetime.now(TZ).strftime("%d.%m"),
                                                  "text": txt.strip(), "source": cat})
        await bot.send_message(chat_id=cid, text=f"⭐ Сохранено в «{cat}».")
        return

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
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_home")])
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