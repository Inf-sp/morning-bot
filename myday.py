import asyncio
import json
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

# --- Факты о городе (lazy build) ---

_FACTS_MIN = 5   # минимум фактов для показа; 30 было слишком — Railway стирал файлы

_FACT_ASPECTS = [
    "history, founding date, and historical records",
    "architecture, infrastructure, and city planning",
    "unusual laws, regulations, and local quirks",
    "culture, traditions, and notable people born here",
]

_build_attempted: set = set()  # city slugs, уже пробованные в этой сессии


def _city_slug(city: str) -> str:
    return re.sub(r"[^\w]", "_", (city or "").strip().lower())


def _load_city_facts(city: str) -> list:
    """Загружает факты из store (Postgres/in-memory) — переживает рестарты."""
    db = store._load(config.CITY_FACTS_DB_KEY)
    return list(db.get(_city_slug(city), []))


def _save_city_facts(city: str, facts: list) -> None:
    """Сохраняет факты в store (Postgres/in-memory) — переживает рестарты."""
    db = store._load(config.CITY_FACTS_DB_KEY)
    db[_city_slug(city)] = facts
    store._save(config.CITY_FACTS_DB_KEY, db)


def _is_russian(text: str) -> bool:
    """True если ≥30% символов кириллица — перевод не нужен."""
    if not text:
        return True
    cyr = sum(1 for c in text if 'а' <= c.lower() <= 'я' or c in 'ёЁ')
    return cyr / len(text) >= 0.3


def _translate_to_ru(text: str) -> str:
    """Переводит факт на русский (tier=cheap). При ошибке — оригинал."""
    try:
        return ai.llm(
            f"Переведи точно на русский. Сохрани все факты, числа и имена собственные. "
            f"Только перевод, без пояснений:\n{text}",
            300, tier="cheap"
        ).strip()
    except Exception as e:
        _log.warning("myday: _translate_to_ru failed: %s", e)
        return text


def _score_facts(candidates: list, city: str, country: str) -> list:
    """Оценивает кандидатов LLM → [{text, score}]. Пропускает score < 3."""
    if not candidates:
        return []
    place = f"{city}, {country}" if country else city
    items = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(candidates))
    prompt = (
        f"Оцени интересность каждого факта о {place} по шкале 1-5.\n"
        f"ОБЯЗАТЕЛЬНО: факт должен упоминать название '{city}'. Если не упоминает — score 1.\n"
        "АВТОМАТИЧЕСКИ score 1: классификации, рейтинги, членства в организациях, сетях, "
        "UNESCO tier/category, global city, GAWC, Gamma, муниципальный статус — неинтересно.\n"
        "5 = рекорд / «первый в мире» / Гиннес-уровень\n"
        "4 = неожиданность, контринтуитивно, удивит местного жителя\n"
        "3 = конкретика с цифрой/датой/именем\n"
        "2 = курьёз, но слабый\n"
        "1 = общая фраза / классификация / членство / без конкретики\n\n"
        f"{items}\n\n"
        'JSON: {"scores":[{"idx":1,"score":4},...]}'
    )
    try:
        d = ai.llm_json(prompt, 600, tier="cheap")
        raw_scores = d.get("scores", []) if isinstance(d, dict) else []
        result = []
        for item in raw_scores:
            try:
                idx = int(item.get("idx", 0))
                score = int(item.get("score", 1))
            except (TypeError, ValueError):
                continue
            if 1 <= idx <= len(candidates) and score >= 3:
                result.append({"text": candidates[idx - 1], "score": score})
        return result
    except Exception as e:
        _log.warning("myday: _score_facts failed: %s", e)
        return [{"text": t, "score": 3} for t in candidates]


def _llm_facts_fallback(city: str, country: str) -> list:
    """LLM-fallback когда research вернул пустой результат."""
    if not city:
        return []
    place = f"{city}, {country}" if country else city
    prompt = (
        f"Напиши 5 интересных малоизвестных фактов о городе {place}. "
        "Конкретика: числа, даты, имена. Каждый факт 1–2 предложения на русском. "
        "Только сам факт, без вводных слов. "
        'JSON-массив строк: ["факт1","факт2",...]'
    )
    try:
        raw = ai.llm(prompt, 600, tier="cheap")
        m = re.search(r'\[.*\]', raw, re.S)
        if m:
            arr = json.loads(m.group(0))
            return [f for f in arr if isinstance(f, str) and len(f.strip()) > 20]
    except Exception as e:
        _log.warning("myday: _llm_facts_fallback(%s) failed: %s", city, e)
    return []


def _build_city_facts(city: str, country: str, cc: str) -> list:
    """One-time build: собирает факты из Wikipedia/Gemini/Tavily/LLM, сохраняет в store."""
    existing = _load_city_facts(city)
    existing_texts: set = {f["text"] for f in existing}

    raw: list = []
    seen: set = set(existing_texts)

    def _add(text: str) -> None:
        t = (text or "").strip()
        if t and t not in seen:
            raw.append(t)
            seen.add(t)

    # Wikidata: год основания
    wd = research.wikidata_city_facts(city)
    if wd.get("founded"):
        _add(wd["founded"])
    for s in research.wiki_sentences(city):
        _add(s)

    # Tavily: актуальные факты из сети
    place = f"{city}, {country}" if country else city
    for r in research.tavily_search(f"interesting facts about {place} history culture", max_results=5):
        for sent in (r.get("content") or "").split(". "):
            if len(sent.strip()) > 30:
                _add(sent.strip())

    avoid = list(seen)
    for aspect in _FACT_ASPECTS:
        for fact in research.gemini_search_facts_multi(city, country, cc, aspect, avoid):
            _add(fact)
        avoid = list(seen)

    if not raw:
        _log.warning("myday: research returned nothing for %s — trying LLM fallback", city)
        raw = _llm_facts_fallback(city, country)
    if not raw:
        return existing

    _BATCH = 15
    scored: list = []
    for i in range(0, len(raw), _BATCH):
        scored.extend(_score_facts(raw[i:i + _BATCH], city, country))

    for f in scored:
        if not _is_russian(f["text"]):
            f["text"] = _translate_to_ru(f["text"])

    all_facts = existing + scored
    _save_city_facts(city, all_facts)
    _log.info("myday: built %d facts for %s (was %d)", len(all_facts), city, len(existing))
    return all_facts


def city_fact(city, country, cid, cc=""):
    """Факт о городе: lazy-build из store, anti-repeat по cid."""
    cid = str(cid)
    if not city:
        _log.warning("myday: city_fact called with empty city for cid=%s", cid)
        return ""
    slug = _city_slug(city)

    facts = _load_city_facts(city)
    if len(facts) < _FACTS_MIN and slug not in _build_attempted:
        _build_attempted.add(slug)
        facts = _build_city_facts(city, country, cc)
        if not facts:
            # build провалился — разрешаем повтор в следующей сессии
            _build_attempted.discard(slug)

    if not facts:
        return ""

    seen_all = store._load(config.CITY_FACTS_KEY)
    seen_texts = set(seen_all.get(cid, {}).get(slug, []))

    # Сначала score 4-5, потом 3+
    high = [f for f in facts if f.get("score", 3) >= 4 and f["text"] not in seen_texts]
    mid = [f for f in facts if f.get("score", 3) >= 3 and f["text"] not in seen_texts]
    pool = high or mid

    if not pool:
        # Все показали — сброс, начинаем с высокоскоринговых
        seen_texts = set()
        high = [f for f in facts if f.get("score", 3) >= 4]
        pool = high or facts

    city_lower = city.lower()
    city_pool = [f for f in pool if city_lower in f["text"].lower()]
    chosen = random.choice(city_pool) if city_pool else random.choice(pool)

    # lazy-перевод для фактов, сохранённых до введения перевода
    if not _is_russian(chosen["text"]):
        translated = _translate_to_ru(chosen["text"])
        if translated != chosen["text"]:
            chosen["text"] = translated
            _save_city_facts(city, facts)

    seen_texts.add(chosen["text"])
    seen_all.setdefault(cid, {})[slug] = list(seen_texts)
    store._save(config.CITY_FACTS_KEY, seen_all)

    return chosen["text"]


# --- Сводка дня (Мой день) ---


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

_day_cache = {}  # cid -> {"date":..., "text":..., "has_fact": bool, "ts": float}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)

def _day_menu_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Погода на завтра", callback_data="a_w_tomorrow")],
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("🔄 Обновить сводку", callback_data="md_refresh")],
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
    wind_dir_deg = (d.get("winddirection_10m_dominant") or [None])[0]
    _avg = weather._daytime_avg_wind(data, day_str)
    wind_avg = _avg if _avg is not None else wind_ms
    icon = weather.weather_icon(code, tmax, rain, wind_ms, rain_mm)
    wemoji, wword = weather.wind_scale(wind_avg)
    rain_p = weather._periods(data, day_str, "precipitation_probability", weather.RAIN_PROB_MIN)
    rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
    dir_text = weather.wind_direction_text(wind_dir_deg)
    dir_part = f", {dir_text}" if dir_text else ""
    # ветер: подробно только если сильный
    if wind_avg >= 8:
        wind_p = weather._periods(data, day_str, "windspeed_10m", 6)
        wind_when = (" (" + ", ".join(wind_p) + ")") if wind_p else ""
        wind_str = f"{wemoji} {wword}{wind_when} {wind_avg:.0f} м/с{dir_part}"
    else:
        wind_str = f"💨 Ветер {wind_avg:.0f} м/с{dir_part}"

    now = datetime.now(TZ)
    weekday_name = _WEEKDAYS[now.weekday()]
    is_weekend = now.weekday() >= 5
    word_line = _word_of_day(cid)

    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    flag = flag_from_cc(s.get("cc", "")) or (country_flag(s.get("country", "")) if s.get("country") else "")
    title_flag = f" {flag}" if flag else ""
    L = [f"<b>Мой день • {esc(header)} • {esc(s.get('city',''))}{title_flag}</b>", ""]
    L.append(f"<b>{icon} Погода сегодня</b>")
    L.append(f"До {tmax:+.0f}°C • {weather.rain_text(rain, rain_mm, rain_when)}{wind_str}")
    rain_char = weather.rain_character(code, rain_mm, rain, data, day_str)
    if rain_char:
        L.append(f"🌩 {rain_char}")
    hum = weather.humidity_phrase(data, day_str, tmax, s.get("cc", ""))
    if hum:
        L.append(f"💧 {hum}")
    L.append("")
    focus = memory.fresh_focus(cid)
    if focus:
        L += ["<b>🎯 Фокус на сегодня</b>", esc(focus), ""]
    if word_line:
        L += ["<b>📚 Слово дня</b>", esc(word_line), ""]
    try:
        fact = city_fact(s.get("city", ""), s.get("country", ""), cid, cc=s.get("cc", ""))
    except Exception as e:
        _log.warning("myday: city_fact failed: %s", e)
        fact = ""
    if fact:
        L += ["<b>🔬 Интересный факт</b>", esc(fact.strip()), ""]
    hack_cat, hack_text = daily_lifehack(
        cid, rain=rain >= 40, hot=tmax >= 24, is_weekend=is_weekend)
    if hack_text:
        L += [f"<b>💡 База знаний</b>", esc(hack_text)]
    try:
        q_data = _fetch_quote(cid)
    except Exception as e:
        _log.warning("myday: _fetch_quote failed: %s", e)
        q_data = {}
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
    # помечаем, есть ли факт — чтобы кешировать короче если нет
    _build_day_text._has_fact = bool(fact)
    return text

async def send_plany(bot, cid, force=False):
    import time as _time
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    cache = _day_cache.get(str(cid))
    # Кеш устарел если: другой день, принудительное обновление,
    # или факт не был получен и прошло >30 минут (чтобы дать build ещё шанс)
    stale = (
        not cache
        or cache.get("date") != today
        or force
        or (not cache.get("has_fact") and _time.time() - cache.get("ts", 0) > 1800)
    )
    if stale:
        await bot.send_message(chat_id=cid, text="Собираю сводку дня...")
        _build_day_text._has_fact = False
        try:
            text = await asyncio.to_thread(_build_day_text, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        _day_cache[str(cid)] = {
            "date": today, "text": text,
            "has_fact": getattr(_build_day_text, "_has_fact", False),
            "ts": _time.time(),
        }
    text = _day_cache[str(cid)]["text"]
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=_day_menu_kb())

async def handle_callback(bot, cid, q, data):
    if data == "md_refresh":
        reset_day_cache(cid)
        _build_attempted.discard(_city_slug(store.get_settings(cid).get("city", "")))
        await send_plany(bot, cid, force=True); return
    if data == "md_worrycheck":
        await balance.send_evening_review(bot, cid); return
