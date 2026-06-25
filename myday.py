import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config

_HERE = Path(__file__).parent
_log = logging.getLogger(__name__)
import store
import ai
import weather
import balance
import learning
import research
import memory
from util import esc, _WEEKDAYS, _MONTHS, flag_from_cc, country_flag
import verify

TZ = config.TZ

def ensure_lagom(cid):
    """Список Лагом-фраз пользователя; авто-загрузка из lagom.json если пусто."""
    items = store.get_list(config.LAGOM_KEY, cid)
    if items:
        return items
    try:
        import json
        with open(_HERE / "lagom.json", encoding="utf-8") as f:
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

def city_fact(city, country, cid):
    """Факт о городе: Wikipedia (RU+EN) + Wikidata как fallback. Anti-repeat по cid."""
    pool = list(research.wiki_sentences(city))
    if len(pool) < 3:
        wd = research.wikidata_city_sentence(city)
        if wd and wd not in pool:
            pool.append(wd)
    if not pool:
        return ""
    cid = str(cid)
    city_key = (city or "").lower().replace(" ", "_")
    seen_all = store._load(config.CITY_FACTS_KEY)
    seen = set(seen_all.get(cid, {}).get(city_key, []))
    unseen = [s for s in pool if s not in seen]
    if not unseen:
        seen = set()
        unseen = pool
    fact_raw = random.choice(unseen)
    seen.add(fact_raw)
    seen_all.setdefault(cid, {})[city_key] = list(seen)
    store._save(config.CITY_FACTS_KEY, seen_all)
    lang_hint = "на русском" if re.search(r"[А-Яа-яЁё]", fact_raw) else "переведи на русский и"
    place = f"{city}, {country}" if country else city
    prompt = (
        f"Источник ({place}): «{fact_raw}»\n\n"
        f"Перепиши это {lang_hint} как факт для утреннего бота о {place}. "
        "Критерии:\n"
        "1. Локальность — связан с историей, законами, менталитетом, архитектурой или инфраструктурой региона.\n"
        "2. Эффект «Вау» — даже местный житель должен узнать что-то новое.\n"
        "3. Краткость — максимум 2 коротких предложения, без воды.\n"
        "Используй ТОЛЬКО информацию из источника, не придумывай деталей. "
        "Не начинай с названия города. Без заголовка и без кавычек."
    )
    return ai.llm(prompt, 200, tier="cheap") or fact_raw


def daily_lifehack(cid, rain=False, hot=False, is_weekend=False):
    """Случайный совет из lifehacks.json с anti-repeat и контекстной фильтрацией."""
    try:
        import json
        with open(_HERE / "lifehacks.json", encoding="utf-8") as f:
            cats = json.load(f)
    except Exception:
        return "", ""
    all_tips = [
        (cat["emoji"], cat["cat"], f"{ci}:{ti}", tip["text"], tip.get("tags", []))
        for ci, cat in enumerate(cats)
        for ti, tip in enumerate(cat["tips"])
    ]
    if not all_tips:
        return "", ""
    cid = str(cid)
    seen = set(store.get_list(config.LIFEHACK_KEY, cid))
    ctx_tags = (["rain"] if rain else []) + (["hot"] if hot else []) + ([] if is_weekend else ["work"])
    contextual = [t for t in all_tips if t[4] and any(g in t[4] for g in ctx_tags) and t[2] not in seen]
    unseen = [t for t in all_tips if t[2] not in seen]
    pool = contextual or unseen
    if not pool:
        store.set_list(config.LIFEHACK_KEY, cid, [])
        pool = all_tips
    tip = random.choice(pool)
    new_seen = list(seen | {tip[2]})
    store.set_list(config.LIFEHACK_KEY, cid, new_seen)
    return f"{tip[0]} {tip[1]}", tip[3]

def plany_extras(country, date_str, city="", weather_text="", wardrobe_text="", weekday="", is_weekend=False, cc=""):
    day_kind = "выходной" if is_weekend else "будний день"
    place = f"{city}, {country}" if country else city
    prompt = f"""Сгенерируй блоки для ежедневной сводки. Дата: {date_str} ({weekday}, {day_kind}). Локация: {place}.
Погода сегодня: {weather_text}
Гардероб (используй ТОЛЬКО эти вещи, точные названия): {wardrobe_text}

{config.myday_rules(city, country, cc)}

Строго валидный JSON, экранируй кавычки, без переносов внутри значений.
{{
 "outfit": ["верх","низ","обувь","аксессуар"],
 "quote": "короткая цитата от мыслителя/учёного/предпринимателя (Сенека, Марк Аврелий, Навал, Джобс), без банальностей. ТОЛЬКО на русском языке, без иностранных слов.",
 "quote_src": "автор"
}}
Правила для outfit: 1 верх + 1 низ + обувь (+ опц. аксессуар), сочетание по цвету, минимализм. От +24°C без дождя - ШОРТЫ + футболка; +17..+23 - лёгкие брюки + футболка/рубашка; ниже +16 или дождь/ветер - слои/ветровка, закрытая обувь. Без обращения по имени."""
    return ai.llm_json(prompt, 600, tier="cheap")

def _cap(s):
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s

def _quote_valid(q):
    """Пропускает цитату если LLM вставил латинское слово в кириллический текст."""
    return not re.search(r'[а-яА-ЯЁё][a-zA-Z]|[a-zA-Z][а-яА-ЯЁё]', q or "")

def _is_word_entry(w):
    """Запись словаря - именно СЛОВО, а не фраза."""
    if not isinstance(w, dict):
        return False
    if w.get("kind"):
        return w["kind"] == "word"
    return " " not in (w.get("word") or "").strip()

def _fill_translations(ru, word, lang):
    """Возвращает (nl, en): известный язык как есть, недостающий - переводим (и кэшируем у вызывающего)."""
    nl = word if lang == "nl" else ""
    en = word if lang == "en" else ""
    if nl and en:
        return nl, en
    known = "нидерландском" if lang == "nl" else "английском"
    try:
        d = ai.llm_json(
            f"Слово на русском: «{ru}». Уже известно на {known}: «{word}». "
            "Дай недостающие переводы. СТРОГО: nl - только на нидерландском (с артиклем de/het), "
            "en - только на английском. Одним словом/словосочетанием, без пояснений, без других языков.\n"
            'JSON: {"nl":"нидерландский перевод","en":"английский перевод"}',
            200, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)
        nl = nl or (d.get("nl") or "").strip()
        en = en or (d.get("en") or "").strip()
    except Exception:
        pass
    return nl, en

def _word_of_day(cid):
    """Слово дня: ОБЯЗАТЕЛЬНО и только из словаря СЛОВ (не фраз). Формат: Русский → 🇳🇱 → 🇬🇧."""
    words = learning._ensure_dict(cid)
    pool = [w for w in words if _is_word_entry(w)
            and (w.get("ru") or "").strip() and (w.get("word") or "").strip()]
    if not pool:
        return ""
    w = random.choice(pool)
    ru = (w.get("ru") or "").strip()
    lang = "en" if w.get("lang") == "en" else "nl"
    nl = (w.get("nl") or (w.get("word") if lang == "nl" else "") or "").strip()
    en = (w.get("en") or (w.get("word") if lang == "en" else "") or "").strip()
    if not nl or not en:
        nl2, en2 = _fill_translations(ru, w.get("word", ""), lang)
        nl, en = nl or nl2, en or en2
        # кэшируем перевод в записи, чтобы не переводить повторно
        if nl:
            w["nl"] = nl
        if en:
            w["en"] = en
        try:
            store.set_list(config.DICT_KEY, cid, words)
        except Exception:
            pass
    parts = [_cap(ru)]
    if nl:
        parts.append(f"🇳🇱 {_cap(nl)}")
    if en:
        parts.append(f"🇬🇧 {_cap(en)}")
    return " → ".join(parts)

_day_cache = {}  # cid -> {"date":..., "text":..., "ex":..., "outfit":...}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)

def _day_menu_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Погода на завтра", callback_data="a_w_tomorrow")],
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("🌍 Сменить город", callback_data="a_setcity")],
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
    word_line = _word_of_day(cid)

    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    def cap(x): return x[:1].upper() + x[1:] if x else x
    flag = flag_from_cc(s.get("cc", "")) or (country_flag(s.get("country", "")) if s.get("country") else "")
    title_flag = f" {flag}" if flag else ""
    L = [f"<b>Мой день • {esc(header)} • {esc(s.get('city',''))}{title_flag}</b>", ""]
    L += [f"<b>{icon} Погода сегодня</b>",
          f"До {tmax:+.0f}°C • {weather.rain_text(rain, rain_mm, rain_when)}{wind_str}", ""]
    focus = memory.fresh_focus(cid)        # перенесён с вечернего разбора
    if focus:
        L += ["<b>🎯 Фокус на сегодня</b>", esc(focus), ""]
    outfit = " + ".join(ex.get("outfit", [])).rstrip(".")  # для «Сохранить образ дня», в сводке не показываем
    if word_line:
        L += ["<b>📚 Слово дня</b>", esc(word_line), ""]
    fact = city_fact(s.get("city", ""), s.get("country", ""), cid)
    if fact:
        L += ["<b>🔬 Интересный факт</b>", esc(fact.strip()), ""]
    hack_cat, hack_text = daily_lifehack(
        cid, rain=rain >= 40, hot=tmax >= 24, is_weekend=is_weekend)
    if hack_text:
        L += [f"<b>💡 База знаний</b>", esc(hack_text)]
    raw_quote = _strip_quotes(ex.get("quote", ""))
    if raw_quote and _quote_valid(raw_quote):
        src = esc(ex.get("quote_src", "")).strip()
        line = f"«{esc(raw_quote)}»" + (f" — {src}" if src else "")
        L += ["", "<b>💭 Цитата</b>", line]
    text = "\n".join(L).strip()
    # weather-грейдер: предупреждение в логи, если в сводке упомянут зонт без дождя
    _, _uw = verify.grade_umbrella(text, weather._rain_real(rain, rain_mm))
    for w in _uw:
        _log.warning("[verify] weather: %s", w)
    return text, ex, outfit, day_str

async def send_plany(bot, cid):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cache = _day_cache.get(str(cid))
    if not cache or cache.get("date") != today:
        await bot.send_message(chat_id=cid, text="Собираю сводку дня...")
        try:
            text, ex, outfit, _ = await asyncio.to_thread(_build_day_text, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        _day_cache[str(cid)] = {"date": today, "text": text, "ex": ex, "outfit": outfit}
    text = _day_cache[str(cid)]["text"]
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=_day_menu_kb())

async def handle_callback(bot, cid, q, data):
    if data == "md_worrycheck":
        await balance.send_evening_review(bot, cid); return
