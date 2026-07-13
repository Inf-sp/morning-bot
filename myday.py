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
import learning
import research
import memory
from util import esc, _WEEKDAY_SHORT, _MONTHS, flag_from_cc, country_flag
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
    nl_snippet = research.firecrawl_snippet("жизнь в Нидерландах советы быт бюрократия велосипед", 900)
    nl_ground_block = (
        f"Для категории 'жизнь в Нидерландах' используй как источник этот реальный веб-контент "
        f"(не противоречь ему, не выдумывай факты про NL, если он есть):\n{nl_snippet}\n"
        if nl_snippet else ""
    )
    prompt = (
        f"Составь {_POOL_TARGET_ITEMS} практичных, не банальных советов для персональной "
        f"'Базы знаний' в утреннем уведомлении Telegram-бота.\n"
        f"Категории (используй только их): {cats_str}.\n"
        f"{interest_block}"
        f"{nl_ground_block}"
        "Каждый совет должен быть конкретным и применимым сразу, без общих фраз вроде "
        "'пейте больше воды' или 'высыпайтесь'.\n"
        "Для категории 'кухня': только практичные лайфхаки — что помогает готовить быстрее, "
        "улучшает вкус, исправляет частую ошибку или продлевает хранение продукта. Не используй "
        "фильмы, книги, знаменитостей и абстрактные идеи. Не предлагай целое блюдо вместо лайфхака. "
        "Один пункт — одно конкретное действие с понятным результатом "
        "(например: 'Чтобы омлет получился пышнее, добавьте к яйцам ложку воды и готовьте под "
        "крышкой на слабом огне').\n"
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


def kitchen_lifehacks(cid, n=3):
    """N кухонных лайфхаков из того же недельного пула, что и «Мой день» (категория
    «кухня») — без отдельного AI-вызова на каждый заход в «Готовку». Помечает выданные
    как показанные, чтобы при следующем входе на этой неделе не повторяться."""
    cid = str(cid)
    _pool_ensure_fresh(config.LIFEHACK_POOL_KEY, cid, "default", lambda: _generate_lifehack_pool(cid))
    bucket = _pool_get(config.LIFEHACK_POOL_KEY, cid, "default")
    items = bucket.get("items") or []
    unshown_kitchen = [i for i in items if i.get("category") == "кухня" and not i.get("shown_at")]
    if len(unshown_kitchen) < n:
        # даже показанные ранее кухонные лучше, чем пустой экран - лучше повторить, чем показать ничего
        any_kitchen = [i for i in items if i.get("category") == "кухня"]
        unshown_kitchen = any_kitchen if len(any_kitchen) >= n else unshown_kitchen
    chosen = unshown_kitchen[:n]
    if chosen:
        ids = {c["id"] for c in chosen}

        def mut(data):
            b = data.setdefault(cid, {}).setdefault("default", {})
            for it in b.get("items") or []:
                if it.get("id") in ids and not it.get("shown_at"):
                    it["shown_at"] = int(datetime.now(TZ).timestamp())
            return data, True

        store.mutate_kv(config.LIFEHACK_POOL_KEY, mut)
        return [c["text"] for c in chosen]
    fallback = []
    for _ in range(n):
        _label, text = _lifehack_fallback(cid)
        if text and text not in fallback:
            fallback.append(text)
    return fallback



_QUOTE_RESET_AFTER = 15  # сбрасываем anti-repeat после N авторов


def _item_text(item):
    """Текст элемента списка: элемент может быть строкой или {"id":..., "value": строка}
    (после захода в удаление, см. store.ensure_list_ids_via)."""
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


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
        "movies": [_item_text(m) for m in movies if _item_text(m)],
        "books": [_item_text(b) for b in books if _item_text(b)],
        "artists": [_item_text(a) for a in artists if _item_text(a)],
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

    # tier="cheap" ставит groq первым (GRAMMAR_ORDER) - он хуже держит требование
    # "только кириллица" и стабильно ронял цитату через _quote_valid. Gemini
    # (smart) справляется надёжнее с этим требованием к языку.
    d = ai.llm_json(prompt, 200, tier="smart", module="myday")
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
    """Запись дня для карточки 'Мой день' — тот же материал, что показывает
    экран 'Обучение' (см. learning.select_daily_material): выбор и его
    побочные эффекты (last_shown_at) живут в learning.py, здесь только формат."""
    entry = learning.select_daily_material(cid)
    lang = learning._active_language_code(cid)
    if not entry:
        return "", lang
    term = learning._entry_term(entry)
    ru = learning._entry_translation(entry).replace(";", ",")
    return f"{_cap(term)} → {_cap(ru)}.", lang

_day_cache = {}  # cid -> {"date":..., "text":..., "entities":..., "ts": float}

def reset_day_cache(cid):
    _day_cache.pop(str(cid), None)

def _day_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_menu"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
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
        weather_icon = icon
        rain_part = weather.rain_text(rain, rain_mm, rain_when)
        weather_line = f"до {tmax:+.0f}°C" + (f" · {rain_part}" if rain_part else "") + f" · {wind_part}"
        hum_title, hum_line = weather.humidity_phrase(data, day_str, tmax, s.get("cc", ""))
    else:
        rain = 0
        rain_mm = None
        tmax = None
        response = getattr(weather_error, "response", None)
        status = getattr(response, "status_code", None)
        weather_icon = "☁️"
        if isinstance(weather_error, weather.WeatherDailyLimitExceeded) or status == 429:
            weather_line = f"Погодный лимит исчерпан. {weather.WEATHER_LIMIT_FALLBACK}"
        else:
            weather_line = "Сейчас недоступна — остальная сводка всё равно готова."
        hum_title, hum_line = "", ""

    now = datetime.now(TZ)
    weekday_name = _WEEKDAY_SHORT[now.weekday()]
    is_weekend = now.weekday() >= 5
    word_line, word_lang = _word_of_day(cid)

    header = f"{weekday_name}, {now.day} {_MONTHS[now.month-1]}"
    flag = flag_from_cc(s.get("cc", "")) or (country_flag(s.get("country", "")) if s.get("country") else "")
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
        weather_icon=weather_icon,
        weather_line=weather_line,
        humidity_line=f"{hum_title} · {hum_line}" if hum_title else "",
        word_line=word_line,
        word_lang=word_lang,
        lifehack=hack_text,
        quote_text=quote_text,
        quote_author=quote_author,
    )
    text = msg.text
    # weather-грейдер: предупреждение в логи, если в сводке упомянут зонт без дождя
    _, _uw = verify.grade_umbrella(text, weather._rain_real(rain, rain_mm))
    for w in _uw:
        _log.warning("[verify] weather: %s", w)
    return text, msg.entities

async def _maybe_prompt_dict_seed(bot, cid):
    """Если словарь на активном языке пуст, а seed ещё не предлагали - предложить
    один раз наполнить словарь (§28 CLAUDE.md: стартовые слова по language/level)."""
    try:
        lang = learning._active_language_code(cid)
        words = learning._ensure_dict(cid)
        has_words = any(
            learning._entry_term(w) and learning._dict_lang(w) == lang
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
    stale = not cache or cache.get("date") != today or force
    if stale:
        try:
            await bot.send_chat_action(chat_id=cid, action="typing")
        except Exception:
            pass
        try:
            text, entities = await asyncio.to_thread(_build_day_text, cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        _day_cache[str(cid)] = {
            "date": today, "text": text, "entities": entities,
            "ts": _time.time(),
        }
    cached = _day_cache[str(cid)]
    await bot.send_message(
        chat_id=cid, text=cached["text"], entities=cached.get("entities"),
        reply_markup=_day_menu_kb(),
    )
