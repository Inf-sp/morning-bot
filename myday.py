from datetime import datetime
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import weather
import balance
from util import esc, _WEEKDAYS, _MONTHS, flag_from_cc, country_flag

TZ = config.TZ

def ensure_lagom(cid):
    """Список Лагом-фраз пользователя; авто-загрузка из lagom.json если пусто."""
    items = store.get_list(config.LAGOM_KEY, cid)
    if items:
        return items
    try:
        import json
        with open("lagom.json", encoding="utf-8") as f:
            seed = json.load(f)
        if seed:
            store.set_list(config.LAGOM_KEY, cid, seed)
            return seed
    except Exception:
        pass
    return items

def _strip_quotes(s):
    """Убирает внешние кавычки (« » \" \" \" ') с краёв, чтобы не задваивать обёртку."""
    s = (s or "").strip()
    pairs = ('«»', '""', '""', "''", '„“', '‚‘')
    changed = True
    while changed and len(s) >= 2:
        changed = False
        for p in pairs:
            if s[0] == p[0] and s[-1] == p[1]:
                s = s[1:-1].strip()
                changed = True
        # одинаковые прямые кавычки с обеих сторон
        if len(s) >= 2 and s[0] in '"\'' and s[-1] == s[0]:
            s = s[1:-1].strip()
            changed = True
    return s

# --- Сводка дня (Мой день) ---

def plany_extras(country, date_str, city="", weather_text="", wardrobe_text="", weekday="", is_weekend=False, cc=""):
    day_kind = "выходной" if is_weekend else "будний день"
    place = f"{city}, {country}" if country else city
    prompt = f"""Сгенерируй блоки для ежедневной сводки. Дата: {date_str} ({weekday}, {day_kind}). Локация: {place}.
Погода сегодня: {weather_text}
Гардероб (используй ТОЛЬКО эти вещи, точные названия): {wardrobe_text}

{config.myday_rules(city, country, cc)}

Интересный факт должен быть про текущую локацию ({place}), РЕАЛЬНЫЙ и проверяемый, без выдумок и без выводов-домыслов.

Строго валидный JSON, экранируй кавычки, без переносов внутри значений.
{{
 "outfit": ["верх","низ","обувь","аксессуар"],
 "fact": "интересный факт по правилам [Интересный факт] про {place}: локальный, РЕАЛЬНЫЙ, удивляющий, максимум 2 коротких предложения, без домыслов",
 "quote": "короткая цитата от мыслителя/учёного/предпринимателя (Сенека, Марк Аврелий, Навал, Джобс), без банальностей",
 "quote_src": "автор"
}}
Правила для outfit: 1 верх + 1 низ + обувь (+ опц. аксессуар), сочетание по цвету, минимализм. От +24°C без дождя - ШОРТЫ + футболка; +17..+23 - лёгкие брюки + футболка/рубашка; ниже +16 или дождь/ветер - слои/ветровка, закрытая обувь. Без обращения по имени.
Факт - только реальные проверяемые сведения. Кратко."""
    return ai.llm_json(prompt, 1100)

_day_cache = {}  # cid -> {"date":..., "text":..., "ex":..., "outfit":...}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)

def _day_menu_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Погода на завтра", callback_data="a_w_tomorrow")],
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("🌍 Сменить город", callback_data="a_setcity")],
        [InlineKeyboardButton("⭐ В закладки", callback_data="md_fav")],
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
                      weekday=weekday_name, is_weekend=is_weekend, cc=s.get("cc", ""))
    dict_words = store.get_list(config.DICT_KEY, cid)
    word_line = ""
    if dict_words:
        w = random.choice(dict_words)
        word = w.get("word", "") if isinstance(w, dict) else str(w)
        ru = w.get("ru", "") if isinstance(w, dict) else ""
        word_line = f"{word} → {ru}"

    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    def cap(x): return x[:1].upper() + x[1:] if x else x
    flag = flag_from_cc(s.get("cc", "")) or (country_flag(s.get("country", "")) if s.get("country") else "")
    title_flag = f" {flag}" if flag else ""
    L = [f"<b>Мой день • {esc(header)} • {esc(s.get('city',''))}{title_flag}</b>", ""]
    L += [f"<b>{icon} Погода сегодня</b>",
          f"До {tmax:+.0f}°C • {weather.rain_text(rain, rain_mm, rain_when)}{wind_str}", ""]
    outfit = " + ".join(ex.get("outfit", [])).rstrip(".")  # для «Сохранить образ дня», в сводке не показываем
    if word_line:
        L += ["<b>📚 Слово дня</b>", esc(word_line), ""]
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
        line = f"«{esc(_strip_quotes(ex.get('quote','')))}»" + (f" — {src}" if src else "")
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
        await balance.show_worry_check(bot, cid); return
    if data == "md_fav":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔬 Сохранить интересный факт", callback_data="md_save_fact")],
            [InlineKeyboardButton("💭 Сохранить цитату", callback_data="md_save_quote")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="a_plany")],
        ])
        try:
            await q.message.edit_reply_markup(reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text="Что сохранить?", reply_markup=kb)
        return
    if data.startswith("md_save_"):
        what = data[len("md_save_"):]
        fact_txt = ex.get("fact") or ""
        if not fact_txt:
            facts = ex.get("facts", [])
            fact_txt = facts[0] if isinstance(facts, list) and facts else (facts if isinstance(facts, str) else "")
        mapping = {
            "fact": ("Факты", fact_txt),
            "quote": ("Цитаты", (f"«{_strip_quotes(ex.get('quote',''))}» — {ex.get('quote_src','')}") if ex.get("quote") else ""),
        }
        cat, txt = mapping.get(what, ("Прочее", ""))
        if not txt:
            await bot.send_message(chat_id=cid, text="Нечего сохранять - открой «Мой день» заново."); return
        store.add_to_list(config.NOTES_KEY, cid, {"date": datetime.now(TZ).strftime("%d.%m"),
                                                  "text": txt.strip(), "source": cat, "bucket": "fav"})
        await bot.send_message(chat_id=cid, text=f"⭐ Сохранено в «{cat}».")
        return
