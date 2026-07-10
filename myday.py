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

# --- Недельные AI-пулы (факты о городе, база знаний) ---
# Общий движок: раз в неделю AI генерирует пачку 14-21 элемент, каждый день выдаётся
# следующий непоказанный (shown_at), без повтора, пока пул не исчерпан - тогда генерируем
# новый пул досрочно. Экономит AI-вызовы (§14 CLAUDE.md: 0-1 в день для "Мой день").

_POOL_MIN_ITEMS = 7
_POOL_TARGET_ITEMS = 18

_CONTENT_BLACKLIST = (
    "футбол", "спорт", "voetbal", "match", "wedstrijd", "club", "клуб", "score", "счёт",
    "гол", "матч", "чемпионат", "лига", "politics", "политик", "выбор", "партия",
    "crime", "преступ", "убий", "moord", "oorlog", "война", "теракт", "суд",
)


def _content_blocked(text: str) -> bool:
    low = (text or "").lower()
    return any(word in low for word in _CONTENT_BLACKLIST)


def _iso_week_key(dt=None) -> str:
    dt = dt or datetime.now(TZ)
    year, week, _ = dt.isocalendar()
    return f"{year}-{week:02d}"


def _pool_get(store_key: str, cid: str, pool_id: str) -> dict:
    data = store._load(store_key) or {}
    return (data.get(str(cid)) or {}).get(pool_id) or {}


def _pool_next_unshown(store_key: str, cid: str, pool_id: str) -> dict | None:
    """Помечает первый непоказанный item как shown и возвращает его (атомарно)."""
    cid = str(cid)
    result = {"item": None}

    def mut(data):
        bucket = data.setdefault(cid, {}).setdefault(pool_id, {})
        items = bucket.get("items") or []
        for item in items:
            if not item.get("shown_at"):
                item["shown_at"] = int(datetime.now(TZ).timestamp())
                result["item"] = dict(item)
                break
        return data, True

    store.mutate_kv(store_key, mut)
    return result["item"]


def _pool_save(store_key: str, cid: str, pool_id: str, items: list) -> None:
    cid = str(cid)

    def mut(data):
        data.setdefault(cid, {})[pool_id] = {
            "week": _iso_week_key(),
            "generated_at": int(datetime.now(TZ).timestamp()),
            "items": items,
        }
        return data, True

    store.mutate_kv(store_key, mut)


def _pool_ensure_fresh(store_key: str, cid: str, pool_id: str, generate_fn) -> None:
    """Если пула нет, он не за эту неделю, или все элементы показаны - генерирует новый."""
    bucket = _pool_get(store_key, cid, pool_id)
    items = bucket.get("items") or []
    stale_week = bucket.get("week") != _iso_week_key()
    exhausted = bool(items) and all(i.get("shown_at") for i in items)
    if items and not stale_week and not exhausted:
        return
    raw_items = generate_fn()
    filtered = [
        {"id": idx, "text": text, **extra, "shown_at": None}
        for idx, (text, extra) in enumerate(raw_items)
        if text and not _content_blocked(text)
    ]
    if len(filtered) < _POOL_MIN_ITEMS and items and not exhausted:
        # генерация дала слишком мало валидных элементов - лучше донашивать старый пул,
        # чем показывать пользователю пустоту или урезанный набор
        return
    if filtered:
        _pool_save(store_key, cid, pool_id, filtered)


# --- Факты о городе (недельный AI-пул + curated JSON только как fallback) ---

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


_FACT_HISTORY_DAYS = 365


def _fact_history_get(cid, city_key):
    """Тексты фактов, показанных за последние 12 месяцев, для anti-repeat в промпте."""
    cid = str(cid)
    data = store._load(config.CITY_FACT_HISTORY_KEY) or {}
    entries = (data.get(cid) or {}).get(city_key) or []
    cutoff = int(datetime.now(TZ).timestamp()) - _FACT_HISTORY_DAYS * 86400
    return [e["text"] for e in entries if isinstance(e, dict) and e.get("shown_at", 0) >= cutoff and e.get("text")]


def _fact_history_add(cid, city_key, text):
    """Записывает показанный факт в историю и вычищает записи старше 12 месяцев."""
    if not text:
        return
    cid = str(cid)
    cutoff = int(datetime.now(TZ).timestamp()) - _FACT_HISTORY_DAYS * 86400

    def mut(data):
        entries = data.setdefault(cid, {}).setdefault(city_key, [])
        entries[:] = [e for e in entries if isinstance(e, dict) and e.get("shown_at", 0) >= cutoff]
        entries.append({"text": text, "shown_at": int(datetime.now(TZ).timestamp())})
        return data, True

    store.mutate_kv(config.CITY_FACT_HISTORY_KEY, mut)


def _curated_fact_fallback(city, cid):
    """Аварийный путь, если AI недоступен при генерации недельного пула:
    curated JSON с anti-repeat → research.tavily_fact → ''."""
    cid = str(cid)
    facts = _load_curated_facts(city)
    if facts:
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
        text = facts[chosen_idx]["text"]
        if not _content_blocked(text):
            return text
    try:
        fact = research.tavily_fact(city)
        if fact and len(fact.strip()) > 60 and not _content_blocked(fact):
            return fact.strip()
    except Exception as e:
        _log.warning("myday: tavily_fact(%s) failed: %s", city, e)
    return ""


def _generate_fact_pool(city, country, recent_facts=None):
    recent_facts = [str(f).strip() for f in (recent_facts or []) if str(f).strip()]
    avoid_block = ""
    if recent_facts:
        avoid_block = (
            "Не повторяй эти факты и не пересказывай их другими словами "
            "(за последние 12 месяцев уже использовались):\n"
            + "\n".join(f"- {f}" for f in recent_facts[-60:]) + "\n"
        )
    prompt = (
        f"Составь {_POOL_TARGET_ITEMS} коротких интересных фактов о городе {city}"
        f"{f', {country}' if country else ''} для утреннего уведомления Telegram-бота.\n"
        "Тема каждого факта — город, регион, Нидерланды, наука, технологии, культура, "
        "архитектура или повседневная жизнь.\n"
        "Факт должен удивлять, а не просто сообщать справочную информацию: ищи "
        "неожиданный масштаб, контраст, неочевидную связь или малоизвестную деталь, и "
        "объясняй прямо в тексте, почему это интересно.\n"
        "Пиши в стиле короткой журнальной заметки, максимум 3 коротких предложения на факт.\n"
        "Без списков внутри факта, без голых дат без контекста, без канцелярского языка.\n"
        "Не начинай факт с фраз «Знаете ли вы», «Мало кто знает», «Интересный факт» или "
        "похожих вводных клише.\n"
        "Не преувеличивай и не придумывай эффект неожиданности — только то, что "
        "подтверждается надёжными источниками.\n"
        f"{avoid_block}"
        "СТРОГО ЗАПРЕЩЕНО: футбол, спорт любого вида, результаты матчей, клубы, спортивные "
        "даты, политика, выборы, партии, криминал, преступления, войны, теракты, суды.\n"
        'Верни JSON: {"facts": ["факт 1", "факт 2", ...]}'
    )
    try:
        d = ai.llm_json(prompt, 1800, tier="cheap", module="myday")
    except Exception as e:
        _log.warning("myday: fact pool generation failed: %s", e)
        return []
    facts = d.get("facts") if isinstance(d, dict) else []
    return [(str(f).strip(), {}) for f in (facts or []) if str(f).strip()]


def city_fact(city, country, cid, cc=""):
    """Факт о городе из недельного AI-пула (§46 CLAUDE.md: без спорта/политики/криминала).
    Если AI недоступен при первой генерации пула за неделю - curated JSON/Tavily.
    Факты, показанные за последние 12 месяцев, не повторяются (передаются в промпт при
    генерации нового пула) и записываются в историю при каждом реальном показе."""
    if not city:
        return ""
    cid = str(cid)
    pool_id = city.strip().lower()
    recent = _fact_history_get(cid, pool_id)
    _pool_ensure_fresh(config.FACT_POOL_KEY, cid, pool_id, lambda: _generate_fact_pool(city, country, recent))
    item = _pool_next_unshown(config.FACT_POOL_KEY, cid, pool_id)
    text = item["text"] if item else _curated_fact_fallback(city, cid)
    if text:
        _fact_history_add(cid, pool_id, text)
    return text


# --- Сводка дня (Мой день) ---


_LIFEHACK_CATEGORIES = (
    "дом", "кухня", "гардероб", "продуктивность", "технологии",
    "фотография", "жизнь в Нидерландах", "растения", "домашние животные",
)

_LIFEHACK_CATEGORY_EMOJI = {
    "дом": "🏠", "кухня": "🍳", "гардероб": "👕", "продуктивность": "⚡",
    "технологии": "💻", "фотография": "📷", "жизнь в нидерландах": "🇳🇱",
    "растения": "🌿", "домашние животные": "🐾",
}


def _lifehack_fallback(cid, rain=False, hot=False, is_weekend=False):
    """Аварийный путь, если AI недоступен при генерации недельного пула: lifehacks.json."""
    try:
        with open(_HERE / "lifehacks.json", encoding="utf-8") as f:
            cats = json.load(f)
    except Exception:
        return "", ""
    all_tips = [
        (cat["emoji"], cat["cat"], f"{ci}:{ti}", tip["text"], tip.get("tags", []))
        for ci, cat in enumerate(cats)
        for ti, tip in enumerate(cat["tips"])
        if not _content_blocked(tip["text"])
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


def _generate_lifehack_pool(cid):
    interests = []
    movies = store.get_list(config.WATCHLIST_KEY, cid)[:4]
    books = store.get_list(config.BOOKS_KEY, cid)[:4]
    if movies:
        interests.append(f"любит фильмы/сериалы: {', '.join(str(m) for m in movies if m)}")
    if books:
        interests.append(f"любит книги: {', '.join(str(b) for b in books if b)}")
    interest_block = ("Интересы пользователя: " + "; ".join(interests) + ".\n") if interests else ""
    cats_str = ", ".join(_LIFEHACK_CATEGORIES)
    prompt = (
        f"Составь {_POOL_TARGET_ITEMS} практичных, не банальных советов для персональной "
        f"'Базы знаний' в утреннем уведомлении Telegram-бота.\n"
        f"Категории (используй только их): {cats_str}.\n"
        f"{interest_block}"
        "Каждый совет должен быть конкретным и применимым сразу, без общих фраз вроде "
        "'пейте больше воды' или 'высыпайтесь'.\n"
        'Верни JSON: {"tips": [{"category": "одна из категорий выше", "text": "совет"}]}'
    )
    try:
        d = ai.llm_json(prompt, 1800, tier="cheap", module="myday")
    except Exception as e:
        _log.warning("myday: lifehack pool generation failed: %s", e)
        return []
    tips = d.get("tips") if isinstance(d, dict) else []
    out = []
    for t in tips or []:
        text = str((t or {}).get("text") or "").strip()
        cat = str((t or {}).get("category") or "").strip().lower()
        if cat not in [c.lower() for c in _LIFEHACK_CATEGORIES]:
            cat = ""
        if text:
            out.append((text, {"category": cat}))
    return out


def daily_lifehack(cid, rain=False, hot=False, is_weekend=False):
    """Совет из недельного AI-пула по 9 персональным категориям (§ CLAUDE.md).
    Если AI недоступен при первой генерации пула за неделю - lifehacks.json."""
    cid = str(cid)
    _pool_ensure_fresh(config.LIFEHACK_POOL_KEY, cid, "default", lambda: _generate_lifehack_pool(cid))
    bucket = _pool_get(config.LIFEHACK_POOL_KEY, cid, "default")
    items = bucket.get("items") or []
    if items:
        # контекстный приоритет среди непоказанных: дождь/жара -> гардероб, иначе любой
        ctx_cat = "гардероб" if (rain or hot) else ""
        unshown = [i for i in items if not i.get("shown_at")]
        preferred = [i for i in unshown if ctx_cat and i.get("category") == ctx_cat]
        candidates = preferred or unshown
        if candidates:
            target_id = candidates[0]["id"]

            def mut(data):
                b = data.setdefault(cid, {}).setdefault("default", {})
                for it in b.get("items") or []:
                    if it.get("id") == target_id:
                        it["shown_at"] = int(datetime.now(TZ).timestamp())
                        break
                return data, True

            store.mutate_kv(config.LIFEHACK_POOL_KEY, mut)
            chosen = next(i for i in items if i["id"] == target_id)
            cat = chosen.get("category") or ""
            emoji = _LIFEHACK_CATEGORY_EMOJI.get(cat, "💡")
            label = cat.capitalize() if cat else "Совет"
            return f"{emoji} {label}", chosen["text"]
    return _lifehack_fallback(cid, rain=rain, hot=hot, is_weekend=is_weekend)



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


_QUOTE_MAX_CHARS = 220  # ограничивает цитату 2-3 строками в Telegram-карточке


def _clip_quote(text):
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= _QUOTE_MAX_CHARS:
        return text
    return text[:_QUOTE_MAX_CHARS - 1].rstrip(" ,.;:") + "…"

def _word_of_day(cid):
    """Запись дня: из единого словаря на активном изучаемом языке, с приоритетом
    давно не показанных/невыученных записей (last_shown_at, status). Обновляет
    last_shown_at при показе."""
    lang = learning._active_language_code(cid)
    words = learning._ensure_dict(cid)
    pool = [w for w in words
            if learning._entry_term(w) and learning._entry_translation(w)
            and learning._dict_lang(w) == lang]
    if not pool:
        return "", lang

    def _priority_key(w):
        shown = w.get("last_shown_at")
        never_shown = 0 if not shown else 1
        not_known = 0 if w.get("status") != "known" else 1
        return (never_shown, not_known, shown or "")

    pool.sort(key=_priority_key)
    top_n = pool[:max(1, len(pool) // 3)] or pool
    w = random.choice(top_n)
    term = learning._entry_term(w)
    ru = learning._entry_translation(w)

    w["last_shown_at"] = datetime.now(TZ).isoformat()
    try:
        idx = words.index(w)
        words[idx] = w
        store.set_list(config.DICT_KEY, cid, words)
    except Exception:
        pass

    return f"{_cap(term)} → {_cap(ru)}", lang

_day_cache = {}  # cid -> {"date":..., "text":..., "entities":..., "has_fact": bool, "ts": float}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)

def _day_menu_kb():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
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
    word_line, word_lang = _word_of_day(cid)
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
    raw_quote = _clip_quote(_strip_quotes(q_data.get("quote", "")))
    quote_text, quote_author = "", ""
    if raw_quote and _quote_valid(raw_quote):
        quote_text = esc(raw_quote)
        quote_author = esc(q_data.get("src", "")).strip()
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
        word_lang=word_lang,
        fact=fact,
        lifehack=hack_text,
        quote_text=quote_text,
        quote_author=quote_author,
    )
    text = msg.text
    # weather-грейдер: предупреждение в логи, если в сводке упомянут зонт без дождя
    _, _uw = verify.grade_umbrella(text, weather._rain_real(rain, rain_mm))
    for w in _uw:
        _log.warning("[verify] weather: %s", w)
    # помечаем, есть ли факт — чтобы кешировать короче если нет
    _build_day_text._has_fact = bool(fact)
    return text, msg.entities

async def _maybe_prompt_dict_seed(bot, cid):
    """Если словарь на активном языке пуст, а seed ещё не предлагали - предложить
    один раз наполнить словарь (§28 CLAUDE.md: стартовые слова по language/level)."""
    try:
        lang = learning._active_language_code(cid)
        words = learning._ensure_dict(cid)
        has_words = any(
            _is_word_entry(w) and (w.get("lang") or "nl") == lang
            for w in words
        )
        if has_words:
            return
        prof = store.get_profile(cid)
        if prof.get("_myday_seed_prompted"):
            return
        prof["_myday_seed_prompted"] = True
        store.set_profile(cid, prof)
        await learning.send_seed_intro(bot, cid, lang)
    except Exception as e:
        _log.warning("myday: _maybe_prompt_dict_seed failed: %s", e)


async def send_plany(bot, cid, force=False, show_loading=True):
    """Собирает и отправляет сводку «Мой день» без промежуточного «Собираю...» —
    пользователь сразу получает готовый результат одним сообщением. show_loading
    сохранён в сигнатуре для обратной совместимости вызовов, но больше не шлёт
    отдельное сообщение — при холодном кэше показывается только typing-индикатор."""
    import time as _time
    await _maybe_prompt_dict_seed(bot, cid)
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
        try:
            await bot.send_chat_action(chat_id=cid, action="typing")
        except Exception:
            pass
        _build_day_text._has_fact = False
        try:
            text, entities = await asyncio.to_thread(_build_day_text, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        _day_cache[str(cid)] = {
            "date": today, "text": text, "entities": entities,
            "has_fact": getattr(_build_day_text, "_has_fact", False),
            "ts": _time.time(),
        }
    cached = _day_cache[str(cid)]
    await bot.send_message(
        chat_id=cid, text=cached["text"], entities=cached.get("entities"),
        reply_markup=_day_menu_kb(),
    )
