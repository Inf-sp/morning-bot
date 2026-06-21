from datetime import datetime
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import weather
import wardrobe
from util import esc, send_long, _WEEKDAYS, _MONTHS, flag_from_cc, country_flag

TZ = config.TZ

# --- Сводка дня (Мой день) ---

def plany_extras(country, date_str, city="", weather_text="", wardrobe_text="", weekday="", is_weekend=False):
    day_kind = "выходной" if is_weekend else "будний день"
    prompt = f"""Сгенерируй блоки для ежедневной сводки. Дата: {date_str} ({weekday}, {day_kind}). Город: {city}. Страна: {country}.
Погода сегодня: {weather_text}
Гардероб (используй ТОЛЬКО эти вещи, точные названия): {wardrobe_text}

{config.USER_FOCUS_CONTEXT}

{config.MYDAY_RULES}

Строго валидный JSON, экранируй кавычки, без переносов внутри значений.
{{
 "focus": "Фокус дня - ОДНА доминанта через глагол действия, одна короткая строка (см. правила [Фокус дня])",
 "outfit": ["верх","низ","обувь","аксессуар"],
 "word_ru": "слово дня на русском (одно слово)",
 "word_nl": "перевод на нидерландский С АРТИКЛЕМ (de/het)",
 "word_en": "перевод на английский С АРТИКЛЕМ (a/the)",
 "idea": "бизнес-идея по правилам [Бизнес-идея]: Суть одним предложением + Монетизация/MVP одним предложением",
 "fact": "интересный факт по правилам [Интересный факт]: локальный, удивляющий, максимум 2 коротких предложения",
 "quote": "цитата по правилам [Цитата], строго привязанная к Фокусу дня выше",
 "quote_src": "автор"
}}
Правила для outfit: 1 верх + 1 низ + обувь (+ опц. аксессуар), сочетание по цвету, минимализм. От +24°C без дождя - ШОРТЫ + футболка; +17..+23 - лёгкие брюки + футболка/рубашка; ниже +16 или дождь/ветер - слои/ветровка, закрытая обувь. Без обращения по имени.
Цитата должна логически соответствовать Фокусу дня. НЕ повторяй одно и то же в word, idea и fact. Кратко."""
    return ai.llm_json(prompt, 1300)

_day_cache = {}  # cid -> {"date":..., "text":..., "ex":..., "outfit":...}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)

def _day_menu_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Погода на завтра", callback_data="a_w_tomorrow")],
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("🌍 Сменить город", callback_data="a_setcity")],
        [InlineKeyboardButton("⭐ Добавить в избранное", callback_data="md_fav")],
    ])

def _build_day_text(cid):
    s = store.get_settings(cid)
    data = weather.fetch_weather(s["lat"], s["lon"], 2)
    d = data["daily"]
    day_str = d["time"][0]
    code = d["weathercode"][0]
    tmax = d["temperature_2m_max"][0]
    rain = d["precipitation_probability_max"][0] or 0
    rain_mm = (d.get("precipitation_sum") or [None])[0] if d.get("precipitation_sum") else None
    wind_ms = d["windspeed_10m_max"][0] or 0
    icon = weather.weather_icon(code, tmax, rain, wind_ms, rain_mm)
    wemoji, wword = weather.wind_scale(wind_ms)
    rain_p = weather._periods(data, day_str, "precipitation_probability", weather.RAIN_PROB_MIN)
    rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
    # ветер: подробно только если сильный
    if wind_ms >= 8:
        wind_p = weather._periods(data, day_str, "windspeed_10m", 6)
        wind_when = (" (" + ", ".join(wind_p) + ")") if wind_p else ""
        wind_str = f"{wemoji} {wword}{wind_when} {wind_ms:.0f} м/с"
    else:
        wind_str = f"💨 Ветер {wind_ms:.0f} м/с"

    now = datetime.now(TZ)
    wblock = weather.weather_block(data, 0, s["city"])
    weekday_name = _WEEKDAYS[now.weekday()]
    is_weekend = now.weekday() >= 5
    ex = plany_extras(s.get("country", ""), day_str, s.get("city", ""),
                      weather_text=wblock, wardrobe_text=store.wardrobe_to_text(store.load_wardrobe()),
                      weekday=weekday_name, is_weekend=is_weekend)
    dict_words = store.get_list(config.DICT_KEY, cid)
    if dict_words:
        w = random.choice(dict_words[-20:])
        if isinstance(w, dict) and w.get("nl"):
            ex["word_ru"] = w.get("ru", ex.get("word_ru", ""))
            ex["word_nl"] = w.get("nl", ex.get("word_nl", ""))
            ex["word_en"] = w.get("en", ex.get("word_en", ""))

    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    def cap(x): return x[:1].upper() + x[1:] if x else x
    flag = flag_from_cc(s.get("cc", "")) or (country_flag(s.get("country", "")) if s.get("country") else "")
    title_flag = f" {flag}" if flag else ""
    L = [f"<b>Мой день • {esc(header)} • {esc(s.get('city',''))}{title_flag}</b>", ""]
    L += [f"<b>{icon} Погода сегодня</b>",
          f"До {tmax:+.0f}°C • {weather.rain_text(rain, rain_mm, rain_when)}{wind_str}", ""]
    outfit = " + ".join(ex.get("outfit", [])).rstrip(".")  # для «Сохранить образ дня», в сводке не показываем
    focus = (ex.get("focus") or "").strip().rstrip(".")
    if focus:
        L += ["<b>🎯 Фокус дня</b>", esc(focus), ""]
    L += ["<b>📚 Слово дня</b>",
          f"{cap(esc(ex.get('word_ru','')))} → 🇳🇱 {cap(esc(ex.get('word_nl','')))} → 🇬🇧 {cap(esc(ex.get('word_en','')))}", ""]
    L += ["<b>🚀 Бизнес-идея</b>", esc(ex.get("idea", "").strip()), ""]
    fact = ex.get("fact") or ""
    if not fact:
        facts = ex.get("facts", [])
        if isinstance(facts, list) and facts:
            fact = facts[0]
        elif isinstance(facts, str):
            fact = facts
    if fact:
        L += ["<b>🔬 Интересный факт</b>", esc(fact.strip()), ""]
    if ex.get("quote"):
        src = esc(ex.get("quote_src", "")).strip()
        line = f"«{esc(ex.get('quote',''))}»" + (f" — {src}" if src else "")
        L += ["<b>💭 Цитата</b>", line]
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
    if data == "md_worrycheck":
        await show_worry_check(bot, cid); return
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
                                   [InlineKeyboardButton("⬅️ Назад", callback_data="a_plany")]])
        await bot.send_message(chat_id=cid, text=out, reply_markup=kb)
        return
    if data == "md_fav":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Сохранить бизнес-идею", callback_data="md_save_idea")],
            [InlineKeyboardButton("⭐ Сохранить цитату", callback_data="md_save_quote")],
            [InlineKeyboardButton("⭐ Сохранить образ дня", callback_data="md_save_look")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="a_plany")],
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
    prompt = f"""Запись дневника пользователя: "{entry}"
Ответь как спокойный мини-психолог: 2-3 предложения поддержки и одна практичная мысль.
{config.LAGOM}
Без markdown."""
    return ai.llm(prompt, 400, 0.8)

async def send_daycheck(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)   # фикс: ответ не уйдёт в Обратный перевод
    store.game_state.pop(cid, None)
    worries = store.get_list(config.WORRIES_KEY, cid)
    lines = ["😌 <b>Дневник тревоги</b>", "",
             "Сюда выгружай всё, что крутится в голове. Не анализируй - просто запиши.",
             "Каждую тревогу с новой строки. Вечером проверим, что было фактами, а что шумом.", ""]
    if worries:
        lines.append("<b>Тревоги за сегодня:</b>")
        for w in worries:
            mark = {"real": "📌", "let_go": "🧹"}.get(w.get("status"), "•")
            lines.append(f"{mark} {esc(w['text'])}")
        lines.append("")
        lines.append("Напиши новые мысли сообщением или разбери текущие 👇")
    else:
        lines.append("Пока пусто. Напиши тревоги одним сообщением.")
    store.pending_input[cid] = "worry"
    rows = [[InlineKeyboardButton("🧠 Разобрать тревоги", callback_data="md_worrycheck")]] if worries else []
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_close")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_evening_review(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)
    store.game_state.pop(cid, None)
    worries = store.get_list(config.WORRIES_KEY, cid)
    if not worries:
        await bot.send_message(chat_id=cid, parse_mode="HTML",
            text="🥸 <b>Вечерний разбор</b>\n\nСегодня тревог не записано. Если что-то крутится - выгрузи сейчас, каждую с новой строки.")
        store.pending_input[cid] = "worry"
        return
    wlist = "\n".join(f"- {w['text']}" for w in worries)
    try:
        analysis = ai.llm(
            "Ты спокойный психолог. Разбери список тревог человека с СДВГ кратко и по-доброму. "
            "Для каждой - одна строка: что из этого факт, а что тревожный шум, и мягкий вывод. Без воды, тепло.\n\n"
            f"Тревоги:\n{wlist}", 700, 0.6)
    except Exception:
        analysis = ""
    L = ["🥸 <b>Вечерний разбор</b>", "",
         "Сейчас не анализируй - просто посмотри, что крутилось за день.", "",
         "<b>Что меня сегодня напрягло:</b>"]
    rows = []
    for i, w in enumerate(worries):
        L.append(f"• {esc(w['text'])}")
        rows.append([InlineKeyboardButton(f"🗑 {w['text'][:24]}", callback_data=f"worry_del_{i}")])
    if analysis:
        L += ["", "👓 <b>Разбор:</b>", esc(analysis)]
    rows.append([InlineKeyboardButton("🧹 Очистить все тревоги", callback_data="worry_clearall")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_close")])
    from telegram import InlineKeyboardMarkup
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def worry_delete(bot, cid, i):
    cid = str(cid)
    worries = store.get_list(config.WORRIES_KEY, cid)
    if i < len(worries):
        worries.pop(i)
        store.set_list(config.WORRIES_KEY, cid, worries)
    await send_evening_review(bot, cid)

async def worry_clear_all(bot, cid):
    store.set_list(config.WORRIES_KEY, str(cid), [])
    await bot.send_message(chat_id=cid, text="🧹 Дневник тревог очищен. Лёгкой ночи.")

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
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_close")])
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
    cid = str(cid)
    new = [{"text": w.strip(), "status": "pending"} for w in text.split("\n") if w.strip()]
    existing = store.get_list(config.WORRIES_KEY, cid)
    store.set_list(config.WORRIES_KEY, cid, existing + new)
    await bot.send_message(chat_id=cid, text=f"📝 Записал в дневник тревоги: +{len(new)}. Вечером проверим, что реально случилось.")

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