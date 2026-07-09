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
import settings
from util import esc, _WEEKDAYS, _MONTHS, flag_from_cc, country_flag
import verify
from ui import myday as myday_ui

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

# --- Факты о городе (curated JSON + research fallback) ---

_CURATED_FACTS: dict = {}   # кеш city_facts.json на время сессии


def _load_curated_facts(city: str) -> list:
    """Загружает факты из city_facts.json (кеш в памяти). Поиск по имени города."""
    global _CURATED_FACTS
    if not _CURATED_FACTS:
        try:
            _CURATED_FACTS = json.loads((_HERE / config.CITY_FACTS_FILE).read_text(encoding="utf-8"))
        except Exception as e:
            _log.warning("myday: city_facts.json not loaded: %s", e)
            return []
    city_lower = city.strip().lower()
    for key, facts in _CURATED_FACTS.items():
        if key.strip().lower() == city_lower:
            return list(facts)
    return []


def city_fact(city, country, cid, cc=""):
    """Grounded факт о городе: curated JSON с anti-repeat → research.wiki_fact → ''."""
    if not city:
        return ""
    cid = str(cid)
    today_mmdd = datetime.now(config.TZ).strftime("%m-%d")
    facts = _load_curated_facts(city)

    if facts:
        # Приоритет: факт с датой = сегодня
        dated = [f for f in facts if f.get("date") == today_mmdd]
        if dated:
            return dated[0]["text"]

        # Anti-repeat по индексам (как Лагом)
        all_idx = list(range(len(facts)))
        seen_data = store._load(config.CITY_FACT_IDX_KEY) or {}
        city_key = city.strip().lower()
        seen_set = set(seen_data.get(cid, {}).get(city_key, []))
        unseen = [i for i in all_idx if i not in seen_set]
        if not unseen:
            seen_set = set()
            unseen = all_idx
        chosen_idx = random.choice(unseen)
        seen_set.add(chosen_idx)
        seen_data.setdefault(cid, {})[city_key] = list(seen_set)
        store._save(config.CITY_FACT_IDX_KEY, seen_data)
        return facts[chosen_idx]["text"]

    # Fallback: Wikipedia — только если текст конкретный (>60 символов)
    try:
        wiki = research.wiki_fact(city)
        if wiki and len(wiki.strip()) > 60:
            return wiki.strip()
    except Exception as e:
        _log.warning("myday: wiki_fact(%s) failed: %s", city, e)
    return ""


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
    seen_authors = store.get_list(config.QUOTE_AUTHORS_KEY, cid)
    if len(seen_authors) >= _QUOTE_RESET_AFTER:
        store.set_list(config.QUOTE_AUTHORS_KEY, cid, [])
        seen_authors = []
    return {
        "movies": [str(m) for m in movies if m],
        "books": [str(b) for b in books if b],
        "artists": [str(a) for a in artists if a],
        "seen_authors": seen_authors,
    }


def _fetch_quote(cid=None):
    """Персонализированная цитата дня с anti-repeat по авторам."""
    ctx = _build_quote_context(cid) if cid else {
        "movies": [], "books": [], "artists": [], "focus": "", "seen_authors": []
    }

    parts = []
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
            f"Русское слово «{ru}» переводится на {known} как «{word}». "
            f"Сначала определи, в каком именно значении «{ru}» и «{word}» связаны здесь "
            "(русское слово может быть многозначным - не переводи его дословно/в другом значении). "
            "Дай перевод именно в этом значении на недостающий язык. "
            "СТРОГО: nl - только на нидерландском (с артиклем de/het), "
            "en - только на английском. Одним словом/словосочетанием, без пояснений, без других языков.\n"
            'JSON: {"nl":"нидерландский перевод","en":"английский перевод"}',
            200, ai.GRAMMAR_ORDER)
        nl = nl or (d.get("nl") or "").strip()
        en = en or (d.get("en") or "").strip()
    except Exception:
        pass
    return nl, en

def _word_of_day(cid):
    """Слово дня: ОБЯЗАТЕЛЬНО и только из словаря СЛОВ (не фраз). Формат: 🇳🇱 → 🇬🇧 → Русский."""
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
    parts = []
    if nl:
        parts.append(f"{_cap(nl)} 🇳🇱")
    if en:
        parts.append(f"{_cap(en)} 🇬🇧")
    parts.append(_cap(ru))
    return " → ".join(parts)

_day_cache = {}  # cid -> {"date":..., "text":..., "entities":..., "has_fact": bool, "ts": float}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)

def _day_menu_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("🌍 Сменить город", callback_data="a_setcity")],
    ])

def _build_day_text(cid):
    s = store.get_settings(cid)
    try:
        data = weather.fetch_weather(s["lat"], s["lon"], 2)
        weather_error = None
    except Exception as e:
        _log.warning("myday: fetch_weather failed: %s", e)
        data = None
        weather_error = e

    if data:
        d = data["daily"]
        day_str = d["time"][0]
        code = d["weathercode"][0]
        tmax = d["temperature_2m_max"][0]
        rain = d["precipitation_probability_max"][0] or 0
        rain_mm = (d.get("precipitation_sum") or [None])[0] if d.get("precipitation_sum") else None
        wind_ms = d["windspeed_10m_max"][0] or 0
        icon = weather.weather_icon(code, tmax, rain, wind_ms, rain_mm)
        rain_p = weather._periods(data, day_str, "precipitation_probability", weather.RAIN_PROB_MIN)
        rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
        # ветер: показываем всегда, в одной строке с температурой и дождём, без эмодзи
        _, wword = weather.wind_scale(wind_ms)
        wind_p = weather._periods(data, day_str, "windspeed_10m", 6)
        wind_when = (" (" + ", ".join(wind_p) + ")") if wind_p else ""
        wind_part = f"{wword} до {wind_ms:.0f} м/с{wind_when}"
        weather_title = f"{icon} Погода сегодня"
        rain_part = weather.rain_text(rain, rain_mm, rain_when)
        weather_line = f"До {tmax:+.0f}°C" + (f" • {rain_part}" if rain_part else "") + f" • {wind_part}"
        hum_title, hum_line = weather.humidity_phrase(data, day_str, tmax, s.get("cc", ""))
    else:
        rain = 0
        rain_mm = None
        tmax = None
        response = getattr(weather_error, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(weather_error, weather.WeatherDailyLimitExceeded) or status == 429:
            weather_title = "☁️ Погодный лимит исчерпан"
            weather_line = weather.WEATHER_LIMIT_FALLBACK
        else:
            weather_title = "☁️ Погода сейчас недоступна"
            weather_line = "Не удалось получить прогноз — остальная сводка всё равно готова."
        hum_title, hum_line = "", ""

    now = datetime.now(TZ)
    weekday_name = _WEEKDAYS[now.weekday()]
    is_weekend = now.weekday() >= 5
    word_line = _word_of_day(cid)
    pr_labels = settings.priority_labels(cid)

    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    flag = flag_from_cc(s.get("cc", "")) or (country_flag(s.get("country", "")) if s.get("country") else "")
    try:
        fact = city_fact(s.get("city", ""), s.get("country", ""), cid, cc=s.get("cc", ""))
    except Exception as e:
        _log.warning("myday: city_fact failed: %s", e)
        fact = ""
    pr = set(settings.priorities(cid))
    hack_cat, hack_text = ("", "")
    if "quiet" not in pr:
        hack_cat, hack_text = daily_lifehack(
            cid, rain=rain >= 40, hot=(tmax is not None and tmax >= 24), is_weekend=is_weekend)
    try:
        q_data = _fetch_quote(cid)
    except Exception as e:
        _log.warning("myday: _fetch_quote failed: %s", e)
        q_data = {}
    raw_quote = _strip_quotes(q_data.get("quote", ""))
    quote_line = ""
    if raw_quote and _quote_valid(raw_quote):
        src = esc(q_data.get("src", "")).strip()
        quote_line = f"«{esc(raw_quote)}»" + (f" — {src}" if src else "")
    msg = myday_ui.day_summary(
        header,
        s.get("city", ""),
        flag=flag,
        priorities=pr_labels,
        weather_title=weather_title,
        weather_line=weather_line,
        humidity_title=hum_title,
        humidity_line=hum_line,
        word_line=word_line,
        fact=fact,
        lifehack=hack_text,
        quote_line=quote_line,
    )
    text = msg.text
    # weather-грейдер: предупреждение в логи, если в сводке упомянут зонт без дождя
    _, _uw = verify.grade_umbrella(text, weather._rain_real(rain, rain_mm))
    for w in _uw:
        _log.warning("[verify] weather: %s", w)
    # помечаем, есть ли факт — чтобы кешировать короче если нет
    _build_day_text._has_fact = bool(fact)
    return text, msg.entities

async def _replace_or_send(bot, cid, loading_message, text, entities, reply_markup):
    if loading_message is not None:
        try:
            await loading_message.edit_text(text=text, entities=entities, reply_markup=reply_markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=reply_markup)


async def send_plany(bot, cid, force=False, show_loading=True):
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
    loading_message = None
    if stale:
        if show_loading:
            try:
                loading_message = await bot.send_message(chat_id=cid, text="⏳ Собираю «Мой день»...")
            except Exception:
                loading_message = None
        _build_day_text._has_fact = False
        try:
            text, entities = await asyncio.to_thread(_build_day_text, cid)
        except Exception as e:
            if loading_message is not None:
                try:
                    await loading_message.delete()
                except Exception:
                    pass
            await verify.safe_error(bot, cid, e); return
        _day_cache[str(cid)] = {
            "date": today, "text": text, "entities": entities,
            "has_fact": getattr(_build_day_text, "_has_fact", False),
            "ts": _time.time(),
        }
    cached = _day_cache[str(cid)]
    await _replace_or_send(
        bot, cid, loading_message,
        cached["text"], cached.get("entities"), _day_menu_kb()
    )
