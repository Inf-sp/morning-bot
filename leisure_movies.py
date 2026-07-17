from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ui.constants import COUNTRY_EMOJI, save_toggle_label, ui_label
import asyncio
import logging
import re
import random
import time
import config
import api_usage

_log = logging.getLogger(__name__)
import store
import ai
import util
import settings
import tmdb
import movie_engine
import recommendation_stoplist
import verify
from ui import leisure as leisure_ui
from ui.navigation import back_menu_keyboard
from leisure_collection import (
    _ask_collect,
    _ensure_books,
    _norm,
    collect_done,
    content_recommend,
    dedupe_lists,
    seed_movies_from_content,
)


_TMDB_GENRES = {28:"боевик",12:"приключения",16:"анимация",35:"комедия",80:"криминал",99:"документальный",
    18:"драма",10751:"семейный",14:"фэнтези",36:"история",27:"ужасы",10402:"музыка",9648:"детектив",
    10749:"мелодрама",878:"фантастика",10770:"телефильм",53:"триллер",10752:"военный",37:"вестерн",
    10759:"боевик",10762:"детское",10763:"новости",10764:"реалити",10765:"фантастика",10766:"мыло",
    10767:"ток-шоу",10768:"военное"}

def _tmdb_lookup(title, title_en=""):
    if not config.TMDB_API_KEY:
        return None
    cache_key = f"{title_en}|{title}".strip().lower()
    cached = util.ttl_get("tmdb_lookup", cache_key, 86400)
    if cached is not None:
        return cached
    import requests
    for q in [t for t in (title_en, title) if t]:
        try:
            r = requests.get("https://api.themoviedb.org/3/search/multi",
                params={"api_key": config.TMDB_API_KEY, "query": q, "include_adult": "false",
                        "language": "ru-RU"}, timeout=12)
            results = [x for x in r.json().get("results", []) if x.get("media_type") in ("movie", "tv")]
            if not results:
                continue
            def _ok(x):
                nm = (x.get("title") or x.get("name") or "").lower()
                return nm and not x.get("adult") and not any(b in nm for b in _BAD_TMDB)
            good = [x for x in results if _ok(x)]
            if not good:
                continue
            x = good[0]
            date = x.get("release_date") or x.get("first_air_date") or ""
            kind = "movie" if x.get("media_type") == "movie" else "tv"
            poster = x.get("poster_path")
            genres = ", ".join(_TMDB_GENRES.get(g, "") for g in (x.get("genre_ids") or [])[:3] if _TMDB_GENRES.get(g))
            overview = x.get("overview", "")
            if not overview:
                try:
                    rid = requests.get(f"https://api.themoviedb.org/3/{kind}/{x.get('id')}",
                        params={"api_key": config.TMDB_API_KEY, "language": "ru-RU"}, timeout=10)
                    overview = rid.json().get("overview", "")
                except Exception:
                    pass
            return util.ttl_set("tmdb_lookup", cache_key, {
                "name": x.get("title") or x.get("name") or q,
                "name_en": x.get("original_title") or x.get("original_name") or "",
                "year": date[:4] if date else "", "rating": x.get("vote_average") or 0,
                "vote_count": int(x.get("vote_count") or 0),
                "popularity": x.get("popularity") or 0,
                "genres": genres, "kind": kind,
                "poster": (f"https://image.tmdb.org/t/p/w500{poster}" if poster else None),
                "url": f"https://www.themoviedb.org/{kind}/{x.get('id')}",
                "overview": overview,
            })
        except Exception:
            continue
    return util.ttl_set("tmdb_lookup", cache_key, None)

def _display_title(it, tm):
    """Название, которое реально показано пользователю (TMDb если есть, иначе от LLM)."""
    name = (tm.get("name") if tm else "") or it.get("title", "")
    year = (tm.get("year") if tm else "") or ""
    return f"{name} ({year})" if year else name

_BAD_TMDB = ("making of", "behind the scenes", "bonus", "featurette",
             "the making", "deleted scenes", "trailer", "teaser")

def _clip(text, limit=450):
    """Аккуратно обрезает описание по концу предложения/слова, без обрыва на полуслове."""
    return leisure_ui.clip(text, limit=limit)

def _movie_card(it, tm):
    return leisure_ui.movie_card(it, tm)

def _movie_kb(i, category=None, saved=False):
    """Клавиатура карточки кино — всегда 4 кнопки действия + Назад, без строки
    «По жанру/По настроению» (выбор происходит на приветственном экране раздела,
    см. send_movie_home).

    category используется только для сохранения контекста подбора; кнопка возврата
    на карточке всегда ведёт в общее меню Досуга.
    """
    rows = [
        [InlineKeyboardButton("✨ Заменить", callback_data=f"movie_no_{i}")],
        [InlineKeyboardButton("❤️ В любимые", callback_data=f"movie_love_{i}"),
         InlineKeyboardButton(save_toggle_label(saved), callback_data=f"reco_{i}")],
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


# Полный набор жанров — для экрана предпочтений (чекбоксы).
_GENRE_ALL = [
    ("Комедия", 35), ("Ужасы", 27),
    ("Фантастика", 878), ("Триллер", 53),
    ("Романтика", 10749), ("Драма", 18),
    ("Боевик", 28), ("Детектив", 9648),
    ("Криминал", 80), ("Фэнтези", 14),
    ("Приключения", 12), ("Вестерн", 37),
    ("Документальный", 99), ("Семейный", 10751),
]

# Шесть популярных жанров для быстрого меню «По жанру» (2 столбца, помещается на экран).
# Остальные жанры остаются в предпочтениях и в алгоритме ранжирования.
_GENRE_MENU = [
    ("😂 Комедия", 35), ("👻 Ужасы", 27),
    ("🚀 Фантастика", 878), ("🔪 Триллер", 53),
    ("💕 Романтика", 10749), ("🎭 Драма", 18),
]

# Настроения (8 вариантов, 2 столбца): ключ → подпись.
# Удалённые настроения свёрнуты внутрь оставшихся — см. _MOOD_GENRES/_mood_to_genres.
_MOOD_MENU = [
    ("light", "😌 Лёгкое"), ("scary", "😱 Страшное"),
    ("think", "🤔 Подумать"), ("thrill", "😲 Захватывающее"),
    ("romance", "💘 Романтика"), ("atmo", "🌫️ Атмосферное"),
    ("puzzle", "🧩 Запутанное"), ("action", "💥 Экшен"),
]

# Настроение → жанры-подсказки (детерминированный фолбэк, если LLM недоступен).
# Свёрнутые настроения усиливают соответствующие: «медленное и красивое» и «спокойный
# вечер» → атмосферное; «масштабное»/«без остановки» → экшен; «необычное» → подумать;
# «неожиданная концовка» → запутанное (плюс ключевые слова в _mood_keywords).
_MOOD_GENRES = {
    "light": [35, 10751, 12],
    "scary": [27, 53],
    "think": [878, 18, 9648, 14],   # + «необычное» (нестандартные проекты)
    "thrill": [28, 53, 9648],
    "romance": [10749, 35],
    "atmo": [878, 14, 18],          # + «медленное и красивое», «спокойный вечер»
    "puzzle": [9648, 53, 878],      # + «неожиданная концовка»
    "action": [28, 12, 878, 36],    # + «без остановки», «масштабное» (эпик)
}

# Ключевые слова TMDb для тонкой настройки настроения (id ключевых слов TMDb).
# «Запутанное»/«неожиданная концовка» — plot twist (id 9673).
_MOOD_KEYWORDS = {
    "puzzle": [9673],
}


def _movie_genre_menu_kb():
    rows = []
    buttons = [InlineKeyboardButton(label, callback_data=f"movie_g_{gid}")
               for label, gid in _GENRE_MENU]
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


def _movie_mood_menu_kb():
    rows = []
    buttons = [InlineKeyboardButton(label, callback_data=f"movie_mood_{key}")
               for key, label in _MOOD_MENU]
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)

MIN_TMDB_RATING = 7.0

_MOVIE_FALLBACKS = [
    {"title": "Решение уйти", "title_en": "Decision to Leave", "hook": "изящный детектив с холодной романтикой и сильной режиссурой"},
    {"title": "Пылающий", "title_en": "Burning", "hook": "медленный корейский триллер с тревожной пустотой и недосказанностью"},
    {"title": "Разделение", "title_en": "Severance", "hook": "сериал про офисный абсурд, контроль и очень цепкую загадку"},
    {"title": "Медведь", "title_en": "The Bear", "hook": "нервный сериал про работу, семью и попытку собрать жизнь заново"},
    {"title": "Патерсон", "title_en": "Paterson", "hook": "тихое кино про ритм дней, наблюдательность и внутреннюю опору"},
]

def _movie_used(cid):
    """Множество названий, которые нельзя повторять: любимые, знакомые, чёрный список, закладки."""
    wl = store.get_list(config.WATCHLIST_KEY, cid)
    blocked = recommendation_stoplist.values(cid, "movie")
    notes_all = store.get_list(config.NOTES_KEY, cid)
    noted = [n.get("text", "") for n in notes_all
             if isinstance(n, dict) and "кино" in str(n.get("source", "")).lower()]
    used = set()
    for x in list(wl) + blocked + noted:
        used.add((x if isinstance(x, str) else str(x)).lower())
    return used

def _fallback_movie_items(cid):
    used = _movie_used(cid)
    return [
        dict(x) for x in _MOVIE_FALLBACKS
        if x["title"].lower() not in used and x["title_en"].lower() not in used
    ]

def _normalize_movie_items(items):
    """LLM иногда возвращает строки или неполные объекты вместо ожидаемых dict."""
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, str):
            title = it.strip()
            if title:
                out.append({"title": title, "title_en": "", "hook": ""})
            continue
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or it.get("name") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "title_en": str(it.get("title_en") or it.get("original_title") or it.get("name_en") or "").strip(),
            "hook": str(it.get("hook") or it.get("why") or it.get("desc") or "").strip(),
        })
    return out

def _pick_good_movie(items, used_titles):
    """Возвращает (item, tm) для первого фильма с рейтингом >= порога и не из used_titles.
    Фильмы без достаточного числа голосов не используются как запасной вариант."""
    used = {str(u).lower() for u in used_titles}
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("title", "").lower() in used:
            continue
        tm = _tmdb_lookup(it.get("title", ""), it.get("title_en", "")) if config.TMDB_API_KEY else None
        disp = _display_title(it, tm).lower()
        if disp in used:
            continue
        if not config.TMDB_API_KEY:
            return it, tm
        rating = (tm or {}).get("rating") or 0
        vote_count = int((tm or {}).get("vote_count") or 0)
        if rating >= MIN_TMDB_RATING and vote_count >= movie_engine.MIN_VOTE_COUNT:
            return it, tm
    return None, None

async def _send_movie_card(bot, cid, it, i, tm="__lookup__", category=None):
    import saved_items
    it = it if isinstance(it, dict) else {"title": str(it)}
    if tm == "__lookup__":
        tm = _tmdb_lookup(it.get("title", ""), it.get("title_en", "")) if config.TMDB_API_KEY else None
    title, msg = _movie_card(it, tm)
    kb = _movie_kb(
        i, category=category,
        saved=saved_items.is_note_saved(cid, it.get("title", "")),
    )
    if tm and tm.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=tm["poster"], caption=msg.text, caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    try:
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
    except Exception:
        await bot.send_message(chat_id=cid, text=msg.text, reply_markup=kb)

async def send_recos(bot, cid, kind):
    if kind == "book":
        import leisure_books
        await leisure_books.send_books_reco(bot, cid)
        return
    # Пустой список фильмов — предлагаем собрать
    seen = store.get_list(config.WATCHLIST_KEY, cid)
    if not seen:
        await _ask_collect(bot, cid, "movies")
        return
    # Основной путь — TMDb-движок (Recommendations + Similar по любимым).
    it, tm = await _tmdb_engine_pick(cid)
    if it is None:
        # Фолбэк — LLM-подбор (старый путь).
        it, tm = await _llm_movie_pick(cid, _movie_used(cid))
    if not it:
        await bot.send_message(
            chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз.",
            reply_markup=back_menu_keyboard("m_leisure")); return
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    store.last_recos[str(cid)] = {"kind": kind, "items": [disp]}
    store.last_source[str(cid)] = "Досуг · Кино"
    store.last_answer[str(cid)] = f"{disp} - {it.get('hook','')}"
    await _send_movie_card(bot, cid, it, 0, tm=tm)


def _movie_home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать кино", callback_data="movie_reco")],
        [InlineKeyboardButton("👻 По жанру", callback_data="movie_genre_menu"),
         InlineKeyboardButton("🫥 По настроению", callback_data="movie_mood_menu")],
        [InlineKeyboardButton("🎚️ Предпочтения", callback_data="movie_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


def _movie_country_label(name, cc=""):
    name = str(name or "").strip()
    if name:
        return name
    cc = (cc or "").upper()
    by_cc = {
        "NL": "Нидерланды",
        "BE": "Бельгия",
        "DE": "Германия",
        "FR": "Франция",
        "GB": "Великобритания",
        "ES": "Испания",
        "IT": "Италия",
        "AT": "Австрия",
        "CH": "Швейцария",
        "PL": "Польша",
        "SE": "Швеция",
        "DK": "Дания",
        "PT": "Португалия",
        "US": "США",
    }
    return by_cc.get(cc, config.DEFAULT_CITY.get("country", "Нидерланды"))


def _movie_service_language(_cid=None):
    # Официальное локальное название проката; русская машинная локализация
    # нередко создаёт несуществующие названия фильмов.
    return "nl-NL"


async def send_movie_home(bot, cid, q=None):
    """Приветственный экран раздела «Кино» (тот же паттерн, что у Гардероба):
    сколько уже в любимых + какие жанры выбраны в предпочтениях + что сейчас в прокате,
    снизу — вход в обычную рекомендацию по любимым, по жанру или по настроению."""
    selected = {int(x) for x in (settings.get(cid, "movie_genres", []) or []) if str(x).isdigit()}
    genre_labels = [label for label, gid in _GENRE_ALL if gid in selected]

    s = store.get_settings(cid)
    cc = (s.get("cc") or config.DEFAULT_CITY.get("cc", "")).upper()
    country_label = _movie_country_label(s.get("country"), cc)
    now_playing = []
    if config.TMDB_API_KEY:
        try:
            now_playing = await asyncio.to_thread(
                tmdb.get_now_playing, cc, _movie_service_language(cid), 8)
        except Exception:
            now_playing = []
    msg = leisure_ui.movie_home_screen(genre_labels, country_label, now_playing)
    kb = _movie_home_kb()
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def warm_movie_home_cache(cid):
    """Прогревает данные текущего проката, не отправляя экран в Telegram."""
    if not config.TMDB_API_KEY:
        return False
    s = store.get_settings(cid)
    cc = (s.get("cc") or config.DEFAULT_CITY.get("cc", "")).upper()
    await asyncio.to_thread(tmdb.get_now_playing, cc, _movie_service_language(cid), 8)
    return True


def _movie_prefs(cid):
    """Предпочтения кино из настроек → dict для движка (приоритеты, не запреты)."""
    g = settings.get(cid, "movie_genres", []) or []
    countries = settings.get(cid, "movie_countries", []) or []
    return {
        "genres": [int(x) for x in g if str(x).isdigit()],
        "type_pref": settings.get(cid, "movie_type_pref", "") or None,
        "series_status": settings.get(cid, "movie_series_status", "") or None,
        "recency": settings.get(cid, "movie_recency", "") or None,
        "min_rating": _as_float(settings.get(cid, "movie_min_rating", None)),
        "countries": countries,
    }


def _as_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _tmdb_engine_pick(cid, prefs=None):
    """Возвращает (it, tm) из TMDb-движка или (None, None), если данных мало.

    tm — нормализованный TMDb-dict кандидата (совместим с карточкой), дополненный
    деталями и полем because. it — лёгкий dict с title/hook для совместимости.
    """
    if prefs is None:
        prefs = _movie_prefs(cid)
    try:
        cands, taste = await asyncio.to_thread(movie_engine.recommend, cid, prefs)
    except Exception:
        return None, None
    if not cands:
        return None, None
    c = cands[0]
    return _candidate_to_card(cid, c)


def _candidate_to_card(cid, c, reason=None):
    """Обогащает кандидата деталями и строит (it, tm) для карточки.

    reason — явный источник рекомендации, если не «обычная» (Recommendations/Similar
    по любимому): {"kind": "genre"|"mood", "label": "Комедия"|"Хочу подумать"}.
    Если reason не передан, источник — anchor-поля кандидата (because/via/anchors).

    ВАЖНО: tmdb.detail() отдаёт объект из общего TTL-кэша (по ссылке, не копию) —
    его нельзя мутировать напрямую, иначе персональное поле «because» одного
    пользователя утечёт в карточку другого пользователя/другого запроса для того же
    тайтла (баг: «Потому что понравился Элита» у сериала, никак не связанного с Элитой).
    Поэтому здесь всегда делаем dict(det) перед добавлением полей.
    """
    tm = dict(c)
    try:
        det = tmdb.detail(c.get("id"), c.get("kind"))
        if det:
            det = dict(det)  # копия — не мутируем общий кэш tmdb.detail
            tm = det
    except Exception:
        pass
    if reason is not None:
        tm["reason"] = reason
    else:
        tm["because"] = c.get("because")
        tm["via"] = c.get("via")
        tm["shared_genres"] = c.get("shared_genres") or []
        tm["anchors"] = c.get("anchors")
    it = {"title": tm.get("name", ""), "title_en": tm.get("name_en", ""),
          "hook": _reason_text(tm)}
    return it, tm


def _reason_text(tm):
    """Причина рекомендации — плоский текст (для it["hook"], фолбэков без карточки-TMDb)."""
    reason = tm.get("reason")
    if reason:
        return _reason_label(reason)
    because = tm.get("because")
    if because:
        if tm.get("via") == "similar":
            genres = ", ".join(tm.get("shared_genres") or [])
            return f"Подходит по жанрам: {genres}" if genres else ""
        return f"Потому что вам понравился «{because}»"
    return ""


def _reason_label(reason):
    kind = reason.get("kind")
    label = reason.get("label", "")
    if kind == "genre":
        return f"Подборка в жанре «{label}»"
    if kind == "mood":
        return f"Подборка для настроения «{label}»"
    return ""


async def _llm_movie_pick(cid, used):
    """Старый LLM-путь как фолбэк движка."""
    items = []
    for _ in range(2):
        try:
            data = await asyncio.to_thread(content_recommend, "movie", str(cid))
            items = _normalize_movie_items(data.get("items", []) if isinstance(data, dict) else [])
        except Exception:
            items = []
        if items:
            break
    if not items:
        items = _fallback_movie_items(cid)
    if not items:
        return None, None
    picked = await asyncio.to_thread(_pick_good_movie, items, used)
    if picked[0] is not None:
        return picked
    fallbacks = _fallback_movie_items(cid)
    if fallbacks != items:
        return await asyncio.to_thread(_pick_good_movie, fallbacks, used)
    return None, None

async def movie_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        recommendation_stoplist.add(cid, "movie", title, "hidden")
    await _advance_movie(bot, cid)

async def _advance_movie(bot, cid):
    """Загрузить следующую рекомендацию кино и показать карточку.

    Если текущая сессия рекомендаций привязана к жанру/настроению (last_recos["category"],
    проставлено в _show_discovered), следующая карточка ОБЯЗАНА остаться в той же категории —
    «Заменить»/«В любимые»/«Уже видел»/«Сохранить» внутри «Комедии» не должны сбрасывать
    подбор на общий алгоритм. Без category — обычный путь Recommendations/Similar по любимым.
    """
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    category = rec.get("category")
    if category:
        it, tm = await _advance_in_category(cid, category)
        if not it:
            label = category["reason"]["label"]
            text = (f"В этом жанре «{label}» пока не нашёл нового. Попробуй другой."
                    if category["kind"] == "genre" else
                    f"Под настроение «{label}» пока не нашёл нового. Попробуй другое.")
            kb = _movie_genre_menu_kb() if category["kind"] == "genre" else _movie_mood_menu_kb()
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
            return
    else:
        it, tm = await _tmdb_engine_pick(cid)
        if it is None:
            used = _movie_used(cid) | {str(x).lower() for x in rec["items"]}
            it, tm = await _llm_movie_pick(cid, used)
    if not it:
        await bot.send_message(
            chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз.",
            reply_markup=back_menu_keyboard("m_leisure")); return
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    rec["items"].append(disp)
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_movie_card(bot, cid, it, ni, tm=tm, category=category)


async def _advance_in_category(cid, category):
    """Следующий кандидат внутри той же категории (жанр/настроение), с тем же обязательным
    гейтом (require_genre_ids/require_any_genre_ids) — см. send_movie_by_genre/_by_mood."""
    reason = category["reason"]
    if category["kind"] == "genre":
        genre_id = category["value"]
        return await asyncio.to_thread(
            _discover_pick, cid, [genre_id], _movie_prefs(cid),
            require_genre_ids=[genre_id], reason=reason)
    mood_key = category["value"]
    genre_ids = await asyncio.to_thread(_mood_to_genres, mood_key)
    keywords = _MOOD_KEYWORDS.get(mood_key)
    return await asyncio.to_thread(
        _discover_pick, cid, genre_ids, _movie_prefs(cid), keywords=keywords,
        require_any_genre_ids=genre_ids, reason=reason)

async def send_movie_genre_menu(bot, cid, q=None):
    text = "Выбери жанр — подберу фильм или сериал под твой вкус внутри него."
    await _show_menu_over_card(bot, cid, text, _movie_genre_menu_kb(), q)


async def send_movie_mood_menu(bot, cid, q=None):
    text = "Какое настроение? Подберу фильм или сериал специально под него."
    await _show_menu_over_card(bot, cid, text, _movie_mood_menu_kb(), q)


async def _show_menu_over_card(bot, cid, text, kb, q):
    """Показывает текстовое меню поверх текущего сообщения.

    Если сообщение текстовое — редактирует его. Если это карточка с постером
    (media), edit_text невозможен: снимаем кнопки у старой карточки (чтобы по ней
    нельзя было случайно нажать) и отправляем меню новым сообщением.
    """
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


# ---------- экран «Предпочтения кино» ----------
_PREF_GENRES = [(label, gid) for label, gid in _GENRE_ALL]
_PREF_COUNTRIES = [("США", "US"), ("Британия", "GB"), ("Корея", "KR"),
                   ("Япония", "JP"), ("Франция", "FR"), ("Германия", "DE")]
_PREF_TYPE = [(ui_label("cinema", "Фильмы"), "movie"), ("Сериалы", "tv"), ("Без разницы", "")]
_PREF_STATUS = [("✅ Завершённые", "completed"), ("Любые", "")]
_PREF_RECENCY = [("Новинки", "new"), ("Любые годы", "")]
_PREF_RATING = [("6.5", "6.5"), ("7.0", "7.0"), ("7.5", "7.5"), ("8.0", "8.0")]


def _movie_prefs_kb(cid):
    gsel = {int(x) for x in (settings.get(cid, "movie_genres", []) or []) if str(x).isdigit()}
    csel = set(settings.get(cid, "movie_countries", []) or [])
    tpref = settings.get(cid, "movie_type_pref", "") or ""
    spref = settings.get(cid, "movie_series_status", "") or ""
    rpref = settings.get(cid, "movie_recency", "") or ""
    rating = str(settings.get(cid, "movie_min_rating", "") or "")
    rows = []
    rows.append([InlineKeyboardButton("— Любимые жанры —", callback_data="noop")])
    gbtns = [InlineKeyboardButton(("✅ " if gid in gsel else "⬜ ") + label,
                                  callback_data=f"mpref_g_{gid}") for label, gid in _PREF_GENRES]
    for i in range(0, len(gbtns), 2):
        rows.append(gbtns[i:i + 2])
    rows.append([InlineKeyboardButton("— Тип —", callback_data="noop")])
    rows.append([InlineKeyboardButton(("✅ " if tpref == v else "") + label,
                                      callback_data=f"mpref_type_{v or 'any'}") for label, v in _PREF_TYPE])
    rows.append([InlineKeyboardButton("— Сериалы —", callback_data="noop")])
    rows.append([InlineKeyboardButton(("✅ " if spref == v else "") + label,
                                      callback_data=f"mpref_status_{v or 'any'}") for label, v in _PREF_STATUS])
    rows.append([InlineKeyboardButton("— Новинки —", callback_data="noop")])
    rows.append([InlineKeyboardButton(("✅ " if rpref == v else "") + label,
                                      callback_data=f"mpref_recency_{v or 'any'}") for label, v in _PREF_RECENCY])
    rows.append([InlineKeyboardButton("— Мин. рейтинг —", callback_data="noop")])
    rows.append([InlineKeyboardButton(("✅ " if rating == v else "") + f"⭐️ {label}",
                                      callback_data=f"mpref_rating_{v}") for label, v in _PREF_RATING])
    rows.append([InlineKeyboardButton("— Страны —", callback_data="noop")])
    cbtns = [InlineKeyboardButton(("✅ " if v in csel else "⬜ ") + label,
                                  callback_data=f"mpref_c_{v}") for label, v in _PREF_COUNTRIES]
    for i in range(0, len(cbtns), 2):
        rows.append(cbtns[i:i + 2])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_mydata_leisure"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


async def send_movie_prefs(bot, cid, q=None):
    text = ("🎬 Предпочтения кино\n\n"
            "Это приоритеты, а не жёсткие фильтры — я учитываю их при подборе, "
            "но всё равно могу предложить что-то за их пределами.")
    kb = _movie_prefs_kb(cid)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb); return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def toggle_movie_pref(bot, cid, data, q=None):
    """Обработка mpref_* переключателей."""
    if data.startswith("mpref_g_"):
        gid = data[len("mpref_g_"):]
        cur = [str(x) for x in (settings.get(cid, "movie_genres", []) or [])]
        cur = [x for x in cur if x != gid] if gid in cur else cur + [gid]
        settings.set_(cid, "movie_genres", cur)
    elif data.startswith("mpref_c_"):
        cc = data[len("mpref_c_"):]
        cur = list(settings.get(cid, "movie_countries", []) or [])
        cur = [x for x in cur if x != cc] if cc in cur else cur + [cc]
        settings.set_(cid, "movie_countries", cur)
    elif data.startswith("mpref_type_"):
        v = data[len("mpref_type_"):]
        settings.set_(cid, "movie_type_pref", "" if v == "any" else v)
    elif data.startswith("mpref_status_"):
        v = data[len("mpref_status_"):]
        settings.set_(cid, "movie_series_status", "" if v == "any" else v)
    elif data.startswith("mpref_recency_"):
        v = data[len("mpref_recency_"):]
        settings.set_(cid, "movie_recency", "" if v == "any" else v)
    elif data.startswith("mpref_rating_"):
        settings.set_(cid, "movie_min_rating", data[len("mpref_rating_"):])
    await send_movie_prefs(bot, cid, q)


def _genre_label(genre_id):
    raw_label = dict((gid, lbl) for lbl, gid in _GENRE_MENU).get(genre_id) or tmdb.GENRES.get(genre_id, "")
    return re.sub(r"^\S+\s+", "", raw_label) if raw_label else raw_label  # без ведущего эмодзи кнопки


def _mood_label(mood_key):
    raw_label = dict(_MOOD_MENU).get(mood_key, mood_key)
    return re.sub(r"^\S+\s+", "", raw_label) if raw_label else raw_label  # без ведущего эмодзи кнопки


async def send_movie_by_genre(bot, cid, genre_id):
    """Рекомендация внутри жанра: TMDb discover + учёт вкуса пользователя.

    Жанр — обязательный фильтр (не подсказка): показанный тайтл ОБЯЗАН иметь этот
    genre_id в TMDb genre_ids, иначе его нельзя показывать (см. _discover_pick require_genre_ids).
    """
    genre_id = int(genre_id)
    label = _genre_label(genre_id)
    reason = {"kind": "genre", "label": label}
    category = {"kind": "genre", "value": genre_id, "reason": reason}
    try:
        it, tm = await asyncio.to_thread(
            _discover_pick, cid, [genre_id], _movie_prefs(cid),
            require_genre_ids=[genre_id], reason=reason)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_leisure")
        return
    if not it:
        await bot.send_message(chat_id=cid, text="В этом жанре пока не нашёл нового. Попробуй другой.",
                               reply_markup=_movie_genre_menu_kb())
        return
    await _show_discovered(bot, cid, it, tm, category=category)


async def send_movie_by_mood(bot, cid, mood_key):
    """Рекомендация по настроению: LLM-классификатор настроения → жанры → TMDb discover.

    Настроение — обязательный критерий: показанный тайтл ОБЯЗАН иметь хотя бы один
    жанр из набора настроения (или подходящее ключевое слово), иначе его нельзя показывать.
    """
    label = _mood_label(mood_key)
    reason = {"kind": "mood", "label": label}
    category = {"kind": "mood", "value": mood_key, "reason": reason}
    try:
        genre_ids = await asyncio.to_thread(_mood_to_genres, mood_key)
        keywords = _MOOD_KEYWORDS.get(mood_key)
        it, tm = await asyncio.to_thread(
            _discover_pick, cid, genre_ids, _movie_prefs(cid), keywords=keywords,
            require_any_genre_ids=genre_ids, reason=reason)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_leisure")
        return
    if not it:
        await bot.send_message(chat_id=cid, text="Под это настроение пока не нашёл нового. Попробуй другое.",
                               reply_markup=_movie_mood_menu_kb())
        return
    await _show_discovered(bot, cid, it, tm, category=category)


async def _show_discovered(bot, cid, it, tm, category=None):
    """category — контекст жанра/настроения, из которого пришла карточка: сохраняем его
    в last_recos, чтобы «Заменить»/«Сохранить»/«В любимые»/«Уже видел» (через _advance_movie)
    брали СЛЕДУЮЩУЮ рекомендацию из той же категории, а не сбрасывались на общий подбор,
    и чтобы клавиатура карточки вела «Назад» в меню жанров/настроений, а не в общее меню Досуга."""
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    rec["items"].append(disp)
    rec["category"] = category
    store.last_recos[str(cid)] = rec
    store.last_source[str(cid)] = "Досуг · Кино"
    await _send_movie_card(bot, cid, it, len(rec["items"]) - 1, tm=tm, category=category)


def _mood_to_genres(mood_key):
    """LLM классифицирует настроение в набор TMDb genre_id. Фолбэк — статичная карта."""
    fallback = _MOOD_GENRES.get(mood_key, [18])
    label = _mood_label(mood_key)
    valid = ", ".join(f"{gid}={name}" for gid, name in tmdb.GENRES.items() if gid < 10000)
    try:
        data = ai.llm_json(
            f"Пользователь хочет кино под настроение: «{label}».\n"
            f"Доступные жанры TMDb (id=имя): {valid}\n"
            'Верни JSON {"genre_ids":[id,...]} — 1-3 самых подходящих жанра под это настроение.',
            200, tier="cheap")
        ids = [int(g) for g in (data or {}).get("genre_ids", []) if int(g) in tmdb.GENRES]
        return ids or fallback
    except Exception:
        return fallback


def _passes_genre_gate(c, require_genre_ids=None, require_any_genre_ids=None):
    """Обязательная пост-проверка жанра/настроения перед показом карточки (§Проверка перед
    отправкой карточки). TMDb discover с with_genres обычно уже фильтрует верно, но это
    защита от края случаев (устаревший кэш, неполные genre_ids в ответе API) — жанр/настроение
    не должны быть просто «подсказкой», это обязательное условие показа.

    require_genre_ids   — жанр обязателен (ВСЕ id должны быть в genre_ids кандидата, AND).
    require_any_genre_ids — настроение: достаточно ХОТЯ БЫ ОДНОГО совпадения (OR).
    """
    genre_ids = set(c.get("genre_ids") or [])
    if require_genre_ids and not set(require_genre_ids).issubset(genre_ids):
        return False
    if require_any_genre_ids and not genre_ids.intersection(require_any_genre_ids):
        return False
    return True


def _discover_pick(cid, genre_ids, prefs, keywords=None,
                    require_genre_ids=None, require_any_genre_ids=None, reason=None):
    """Берёт кандидатов из discover (movie+tv), фильтрует по вкусу/исключениям, ранжирует.

    keywords — id ключевых слов TMDb для тонкой настройки настроения (напр. plot twist).
    require_genre_ids / require_any_genre_ids — обязательный пост-фильтр (см. _passes_genre_gate):
    жанр/настроение не имеют права быть просто приоритетом, показанный тайтл обязан ему
    соответствовать. Перебираем ранжированный список, а не берём слепо топ-1, — если лидер
    не проходит гейт (пограничный случай неполных данных TMDb), пробуем следующего.
    reason — источник рекомендации для карточки (genre/mood), а не anchor-«понравился».
    """
    min_rating = (prefs or {}).get("min_rating") or movie_engine.RATING_STEPS[0]
    taste = movie_engine.taste_profile(cid, resolve_details=False)
    excluded = movie_engine._excluded_norms(cid)
    steps = [r for r in movie_engine.RATING_STEPS if r <= min_rating] or [movie_engine.RATING_STEPS[-1]]
    # Сначала пробуем с ключевыми словами (тонкая настройка настроения), затем без них —
    # keywords должны быть приоритетом, а не жёстким фильтром.
    for kw in ([keywords, None] if keywords else [None]):
        for mr in steps:
            pool = {}
            for kind in ("movie", "tv"):
                for c in tmdb.discover(kind, genre_ids=genre_ids, min_rating=mr, keywords=kw):
                    if not c.get("id") or movie_engine._norm(c.get("name")) in excluded:
                        continue
                    if not _passes_genre_gate(c, require_genre_ids, require_any_genre_ids):
                        continue
                    pool[f"{c['kind']}:{c['id']}"] = c
            if pool:
                ranked = movie_engine.rank(list(pool.values()), taste, prefs)
                return _candidate_to_card(cid, ranked[0], reason=reason)
    return None, None


async def movie_love(bot, cid, i):
    """Фильм/сериал — в любимые (watchlist), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.WATCHLIST_KEY, cid, title)
    await _advance_movie(bot, cid)

async def add_reco(bot, cid, i, q=None):
    """Переключает сохранение текущей рекомендации кино или книги."""
    import saved_items
    rec = store.last_recos.get(str(cid))
    if not (rec and i < len(rec["items"])):
        return
    title = rec["items"][i]
    kind = rec["kind"]
    folder = "Кино" if kind == "movie" else "Книги"
    saved = saved_items.toggle_note(cid, title, source=folder)
    if kind != "movie":
        items = store.get_list(config.READLIST_KEY, cid)
        target = str(title).strip().casefold()
        if saved:
            existing = {
                str(item.get("value") if isinstance(item, dict) else item).strip().casefold()
                for item in items
            }
            if target not in existing:
                store.set_list(config.READLIST_KEY, cid, [*items, title])
        else:
            items = [
                item for item in items
                if str(item.get("value") if isinstance(item, dict) else item).strip().casefold() != target
            ]
            store.set_list(config.READLIST_KEY, cid, items)
    await saved_items.update_save_button(q, f"reco_{i}", saved)
    if not saved:
        return
    if kind == "movie":
        await _advance_movie(bot, cid)
    else:
        import leisure_books
        await leisure_books._advance_book(bot, cid)
