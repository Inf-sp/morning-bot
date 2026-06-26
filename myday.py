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
    """Список Лагом-принципов пользователя — делегирует в memory.get_lagom."""
    return memory.get_lagom(cid)

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

def city_fact(city, country, cid, cc=""):
    """Факт о городе: Wikidata (структура) → Wikipedia+LLM → Gemini Search. Anti-repeat по cid."""
    cid = str(cid)
    city_key = (city or "").lower().replace(" ", "_")
    seen_all = store._load(config.CITY_FACTS_KEY)
    seen_types = set(seen_all.get(cid, {}).get(city_key + "_types", []))
    seen_texts = set(seen_all.get(cid, {}).get(city_key, []))

    def _save(text, fact_type=None):
        if fact_type:
            seen_types.add(fact_type)
            seen_all.setdefault(cid, {})[city_key + "_types"] = list(seen_types)
        seen_texts.add(text)
        seen_all.setdefault(cid, {})[city_key] = list(seen_texts)
        store._save(config.CITY_FACTS_KEY, seen_all)

    # --- Уровень 1: Wikidata — структурированные факты, без LLM, без галлюцинаций ---
    wd_facts = research.wikidata_city_facts(city)
    unseen_wd = {t: s for t, s in wd_facts.items() if t not in seen_types}
    if not unseen_wd:
        unseen_wd = wd_facts  # всё видели — начинаем сначала
    if unseen_wd:
        fact_type, fact_text = random.choice(list(unseen_wd.items()))
        _save(fact_text, fact_type)
        return fact_text

    # --- Уровень 2: Wikipedia + LLM-перефраз (строго по источнику) ---
    pool = list(research.wiki_sentences(city))
    unseen_wiki = [s for s in pool if s not in seen_texts]
    if not unseen_wiki:
        unseen_wiki = pool
    if unseen_wiki:
        fact_raw = random.choice(unseen_wiki)
        lang_hint = "на русском" if re.search(r"[А-Яа-яЁё]", fact_raw) else "переведи на русский и"
        place = f"{city}, {country}" if country else city
        prompt = (
            f"Источник ({place}): «{fact_raw}»\n\n"
            f"Перепиши это {lang_hint}. "
            "Правила: используй ТОЛЬКО слова и числа из источника — "
            "никаких новых дат, имён, цифр которых нет в тексте выше. "
            "Если источник беден — сократи, но не дополняй. "
            "Максимум 2 предложения. Без заголовка, без кавычек."
        )
        result = ai.llm(prompt, 180, tier="cheap") or fact_raw
        _save(result)
        return result

    # --- Уровень 3: Gemini + Google Search (последний резерв) ---
    gsf = research.gemini_search_fact(city, country, cc=cc, avoid=list(seen_texts))
    if gsf:
        _save(gsf)
        return gsf

    return ""


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



_QUOTE_RESET_AFTER = 15  # сбрасываем anti-repeat после N авторов


def _build_quote_context(cid):
    """Собирает контекст пользователя для персонализации цитаты."""
    movies = store.get_list(config.WATCHLIST_KEY, cid)[:6]
    books = store.get_list(config.BOOKS_KEY, cid)[:6]
    artists = store.get_list(config.ARTISTS_KEY, cid)[:6]
    focus = memory.fresh_focus(cid)
    seen_authors = store.get_list(config.QUOTE_AUTHORS_KEY, cid)
    if len(seen_authors) >= _QUOTE_RESET_AFTER:
        store.set_list(config.QUOTE_AUTHORS_KEY, cid, [])
        seen_authors = []
    return {
        "movies": [str(m) for m in movies if m],
        "books": [str(b) for b in books if b],
        "artists": [str(a) for a in artists if a],
        "focus": focus,
        "seen_authors": seen_authors,
    }


def _fetch_quote(cid=None):
    """Персонализированная цитата дня с anti-repeat по авторам."""
    ctx = _build_quote_context(cid) if cid else {
        "movies": [], "books": [], "artists": [], "focus": "", "seen_authors": []
    }

    parts = []
    if ctx["focus"]:
        parts.append(f"Фокус дня человека: «{ctx['focus']}»")
    if ctx["movies"]:
        parts.append(f"Любимые фильмы/сериалы: {', '.join(ctx['movies'])}")
    if ctx["books"]:
        parts.append(f"Любимые книги: {', '.join(ctx['books'])}")
    if ctx["artists"]:
        parts.append(f"Любимые исполнители: {', '.join(ctx['artists'])}")

    context_block = ("\n".join(parts) + "\n\n") if parts else ""

    avoid_block = ""
    if ctx["seen_authors"]:
        avoid_block = f"Этих авторов уже показывали — не повторяй: {', '.join(ctx['seen_authors'])}.\n\n"

    if parts:
        author_hint = (
            "Выбери автора, чьё мировоззрение или творчество перекликается с интересами человека выше. "
            "Это может быть режиссёр, писатель, музыкант, философ, предприниматель или учёный — "
            "главное, чтобы цитата резонировала с его вкусами или фокусом дня."
        )
    else:
        author_hint = (
            "Выбери мыслителя или предпринимателя (Сенека, Марк Аврелий, Навал Равикант, "
            "Монтень, Шопенгауэр, Эпиктет, Чарли Мунгер — без банальностей)."
        )

    prompt = (
        f"{context_block}"
        f"{avoid_block}"
        f"Дай одну нестандартную цитату (1-2 предложения). {author_hint} "
        "Цитата должна быть реальной — не выдумывай. "
        'Строго JSON: {"quote": "текст на русском", "src": "Автор"}. '
        "Только кириллица, никаких латинских букв в тексте цитаты."
    )

    d = ai.llm_json(prompt, 200, tier="cheap")
    if not isinstance(d, dict):
        return {}

    src = (d.get("src") or "").strip()
    if src and cid:
        seen = store.get_list(config.QUOTE_AUTHORS_KEY, cid)
        if src not in seen:
            store.set_list(config.QUOTE_AUTHORS_KEY, cid, seen + [src])

    return d

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

_day_cache = {}  # cid -> {"date":..., "text":...}

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
    weekday_name = _WEEKDAYS[now.weekday()]
    is_weekend = now.weekday() >= 5
    word_line = _word_of_day(cid)

    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    flag = flag_from_cc(s.get("cc", "")) or (country_flag(s.get("country", "")) if s.get("country") else "")
    title_flag = f" {flag}" if flag else ""
    L = [f"<b>Мой день • {esc(header)} • {esc(s.get('city',''))}{title_flag}</b>", ""]
    L += [f"<b>{icon} Погода сегодня</b>",
          f"До {tmax:+.0f}°C • {weather.rain_text(rain, rain_mm, rain_when)}{wind_str}", ""]
    focus = memory.fresh_focus(cid)
    if focus:
        L += ["<b>🎯 Фокус на сегодня</b>", esc(focus), ""]
    if word_line:
        L += ["<b>📚 Слово дня</b>", esc(word_line), ""]
    fact = city_fact(s.get("city", ""), s.get("country", ""), cid, cc=s.get("cc", ""))
    if fact:
        L += ["<b>🔬 Интересный факт</b>", esc(fact.strip()), ""]
    hack_cat, hack_text = daily_lifehack(
        cid, rain=rain >= 40, hot=tmax >= 24, is_weekend=is_weekend)
    if hack_text:
        L += [f"<b>💡 База знаний</b>", esc(hack_text)]
    q_data = _fetch_quote(cid)
    raw_quote = _strip_quotes(q_data.get("quote", ""))
    if raw_quote and _quote_valid(raw_quote):
        src = esc(q_data.get("src", "")).strip()
        line = f"«{esc(raw_quote)}»" + (f" — {src}" if src else "")
        L += ["", "<b>💭 Цитата</b>", line]
    text = "\n".join(L).strip()
    # weather-грейдер: предупреждение в логи, если в сводке упомянут зонт без дождя
    _, _uw = verify.grade_umbrella(text, weather._rain_real(rain, rain_mm))
    for w in _uw:
        _log.warning("[verify] weather: %s", w)
    return text

async def send_plany(bot, cid):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cache = _day_cache.get(str(cid))
    if not cache or cache.get("date") != today:
        await bot.send_message(chat_id=cid, text="Собираю сводку дня...")
        try:
            text = await asyncio.to_thread(_build_day_text, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        _day_cache[str(cid)] = {"date": today, "text": text}
    text = _day_cache[str(cid)]["text"]
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=_day_menu_kb())

async def handle_callback(bot, cid, q, data):
    if data == "md_worrycheck":
        await balance.send_evening_review(bot, cid); return
