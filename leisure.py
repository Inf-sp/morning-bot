from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ui.constants import COUNTRY_EMOJI, ui_label
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
import research
import settings
import tmdb
import movie_engine
import verify
from ui import leisure as leisure_ui

# ===== КОНТЕНТ (content.py) =====

# --- Инлайн-сбор предпочтений при пустом профиле ---
_COLLECT_HINTS = {
    "artists": (
        f"{ui_label('music', '')} <b>Ещё нет любимых исполнителей</b>\n\n"
        "Чтобы подбирать музыку под твой вкус, мне нужно знать, кого ты слушаешь.\n\n"
        "Пришли список прямо сюда — по одному или через запятую:\n"
        "<i>Например: The xx, Massive Attack, Portishead</i>"
    ),
    "movies": (
        f"{ui_label('cinema', '')} <b>Ещё нет любимых фильмов</b>\n\n"
        "Пришли список фильмов или сериалов, которые тебе понравились, — "
        "подберу похожее.\n\n"
        "<i>Например: Паразиты, Эйфория, Настоящий детектив</i>"
    ),
    "books": (
        f"{ui_label('books', '')} <b>Ещё нет любимых книг</b>\n\n"
        "Пришли список книг, которые ты читал и которые тебе понравились, — "
        "подберу похожее.\n\n"
        "<i>Например: Дюна, Атлант расправил плечи, Идиот</i>"
    ),
}

async def _ask_collect(bot, cid, kind: str):
    """Показывает экран сбора предпочтений и ставит pending_input."""
    store.pending_input[str(cid)] = f"collect_{kind}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data="m_leisure")]])
    await bot.send_message(chat_id=cid, text=_COLLECT_HINTS[kind], parse_mode="HTML", reply_markup=kb)

async def collect_done(bot, cid, kind: str, text: str):
    """Парсит и сохраняет введённый список; повторно открывает раздел."""
    import secure as _sec
    raw = _sec.clamp(text)
    # Разбиваем по запятым, переносам, точкам с запятой
    items = [x.strip() for x in re.split(r"[,;\n]+", raw) if x.strip()]
    if not items:
        await bot.send_message(chat_id=cid, text="Не смог разобрать список — попробуй ещё раз.")
        return
    key_map = {"artists": config.ARTISTS_KEY, "movies": config.WATCHLIST_KEY, "books": config.BOOKS_KEY}
    key = key_map.get(kind)
    if key:
        existing = {_norm(x) for x in store.get_list(key, cid)}
        added = [it for it in items if _norm(it) not in existing]
        for it in added:
            store.add_to_list(key, cid, it)
        n = len(added)
        label = {"artists": "артист(ов)", "movies": "фильм(ов)", "books": "книг(и)"}[kind]
        await bot.send_message(chat_id=cid,
            text=f"✅ Сохранено {n} {label}.", parse_mode="HTML")
    # Повторно открываем нужный раздел
    if kind == "artists":
        await send_listen(bot, cid)
    elif kind == "movies":
        await send_recos(bot, cid, "movie")
    elif kind == "books":
        await send_books_reco(bot, cid)

def _ensure_books(cid):
    """Возвращает список книг пользователя (без авто-сида)."""
    return store.get_list(config.BOOKS_KEY, cid)

def _norm(x):
    """Нормализованное имя элемента (строка или {name}) для сравнения без учёта регистра."""
    s = x.get("name", "") if isinstance(x, dict) else str(x)
    return s.strip().lower()

def _add_unique(key, cid, value):
    """Добавляет в список, только если такого ещё нет (без учёта регистра). True - если добавлено."""
    existing = {_norm(x) for x in store.get_list(key, cid)}
    if _norm(value) in existing:
        return False
    store.add_to_list(key, cid, value)
    return True

def _note_fav_exists(cid, text):
    """Есть ли уже такая закладка (bucket=fav) с тем же текстом."""
    t = (text or "").strip().lower()
    for n in store.get_list(config.NOTES_KEY, cid):
        if isinstance(n, dict) and n.get("bucket", "fav") == "fav" and n.get("text", "").strip().lower() == t:
            return True
    return False

def dedupe_lists():
    """Разовая чистка: убирает повторы (без учёта регистра) в списках любимого/закладок."""
    keys = [config.BOOKS_KEY, config.ARTISTS_KEY, config.WATCHLIST_KEY,
            config.READLIST_KEY, config.COUNTRIES_KEY]
    changed_any = False
    for key in keys:
        data = store._load(key)
        changed = False
        for cid, items in (data or {}).items():
            if not isinstance(items, list):
                continue
            seen, out = set(), []
            for it in items:
                n = _norm(it)
                if n and n in seen:
                    continue
                seen.add(n)
                out.append(it)
            if len(out) != len(items):
                data[cid] = out
                changed = True
        if changed:
            store._save(key, data)
            changed_any = True
    return changed_any

def seed_movies_from_content():
    """Разово: вливает films+series из content.json в watchlist владельца (CHAT_ID).
    Маркер в store не даёт повторить — удалённые фильмы не возвращаются при рестарте."""
    if not config.CHAT_ID:
        return False
    marker = f"movies_{config.CHAT_ID}"
    flags = store._load("_seed_flags") or {}
    if flags.get(marker):
        return False
    try:
        from pathlib import Path
        import json
        raw = json.loads((Path(__file__).parent / "content.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    titles = [t for t in raw.get("films", []) + raw.get("series", []) if isinstance(t, str) and t.strip()]
    for title in titles:
        _add_unique(config.WATCHLIST_KEY, config.CHAT_ID, title.strip())
    flags[marker] = True
    store._save("_seed_flags", flags)
    return True

def content_recommend(kind, cid):
    if kind == "movie":
        loved = store.get_list(config.WATCHLIST_KEY, cid)
        movie_seen = store.get_list(config.MOVIE_SEEN_KEY, cid)
        black = store.get_list(config.MOVIE_BLACKLIST_KEY, cid)
        notes_all = store.get_list(config.NOTES_KEY, cid)
        noted_movies = [n.get("text", "") for n in notes_all
                        if isinstance(n, dict) and "кино" in str(n.get("source", "")).lower()]
        what = "фильмов или сериалов"
        loved_titles = [s if isinstance(s, str) else str(s) for s in loved]
        seen_titles = [s if isinstance(s, str) else str(s) for s in movie_seen]
        black_titles = [s if isinstance(s, str) else str(s) for s in black]
        skip = loved_titles + seen_titles + black_titles + noted_movies
        avoid = ("\nНЕ рекомендуй то, что уже отмечено или не понравилось: " + ", ".join(skip[:80])) if skip else ""
        anchors = ", ".join(loved_titles[:25])
        web_block = ""
        web = research.tavily_snippet(
            f"лучшие фильмы сериалы 2024 2025 драма артхаус триллер похожие {anchors[:80]}",
            max_chars=700,
        )
        if web:
            web_block = f"\nАктуальные новинки и рекомендации из сети (используй как источник реальных названий):\n{web}\n"
        prompt = f"""Ты опытный кинокритик. Порекомендуй фильмы и сериалы под вкус пользователя.
Его любимые работы (референсы вкуса): {anchors}
{web_block}
Порекомендуй РОВНО 5 {what}, максимально точно под этот вкус.
Обязательно дай СМЕСЬ: и фильмы, и сериалы — минимум 2 сериала из 5.{avoid}
JSON: {{"items": [{{"title": "название (год)", "title_en": "оригинальное/английское название", "hook": "1 строка: на что похоже из его референсов и чем зацепит"}}]}}"""
        return ai.llm_json(prompt, 1000, tier="leisure")

    # книги: референсы вкуса берём из "Мои книги" (настройки/БД, авто-загрузка из content.json)
    my_books = _ensure_books(cid)
    my_books_titles = [b if isinstance(b, str) else str(b) for b in my_books]
    read_seen = store.get_list(config.READLIST_KEY, cid)
    book_seen = store.get_list(config.BOOK_SEEN_KEY, cid)
    black = store.get_list(config.BOOK_BLACKLIST_KEY, cid)
    read_titles = [s if isinstance(s, str) else str(s) for s in read_seen]
    book_seen_titles = [s if isinstance(s, str) else str(s) for s in book_seen]
    black_titles = [s if isinstance(s, str) else str(s) for s in black]
    refs = my_books_titles
    anchors = ", ".join(refs[:25])
    skip = my_books_titles + read_titles + book_seen_titles + black_titles
    avoid = ("\nНЕ рекомендуй уже прочитанное/в закладках/отклонённое: " + ", ".join(skip[:80])) if skip else ""
    web_block = ""
    web = research.tavily_snippet(
        f"лучшие книги 2023 2024 2025 литература {anchors[:80]}",
        max_chars=700,
    )
    if web:
        web_block = f"\nАктуальные книжные новинки и рейтинги из сети (используй как источник реальных названий):\n{web}\n"
    prompt = f"""Ты профессиональный редактор и логический критик. Порекомендуй книги под вкус пользователя.
Пиши прямо, жестко и емко. Убирай воду и вводные слова: никаких "однако", "более того", "стоит отметить".
Используй короткие предложения, но чередуй длину для естественного ритма. Не используй точки с запятой.
Если сюжет дублирует описание мира - объединяй. Двусмысленные фразы заменяй точными.
Любимые книги пользователя (референсы вкуса): {anchors if anchors else "список пуст, предложи разнообразные жанры"}
{web_block}
Порекомендуй РОВНО 5 действительно сильных КНИГ под этот вкус (без проходных).
Сравнивай ТОЛЬКО с книгами из его списка выше, не с фильмами/сериалами.{avoid}
JSON: {{"items": [{{"title": "название", "title_en": "оригинальное название", "year": "год",
 "author": "автор", "desc": "вводный абзац: 1-2 емких предложения о мире/конфликте/жанре, без воды",
 "why": ["раздел 1: сильный тезис почему читать, с точным сравнением с книгами пользователя", "раздел 1: второй сильный тезис"],
 "plot": "раздел 2: сюжет и главный конфликт, 2-3 точных предложения; если мир уже описан, не дублируй",
 "quote": "короткая цитата из книги",
 "hook": "1 короткий редакторский итог без общих слов"}}]}}"""
    return ai.llm_json(prompt, 1300, tier="leisure")

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
                return nm and not any(b in nm for b in _BAD_TMDB)
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

def _movie_kb(i, category=None):
    """Клавиатура карточки кино — всегда 4 кнопки действия + Назад, без строки
    «По жанру/По настроению» (выбор происходит на приветственном экране раздела,
    см. send_movie_home).

    category используется только для сохранения контекста подбора; кнопка возврата
    на карточке всегда ведёт в общее меню Досуга.
    """
    rows = [
        [InlineKeyboardButton("✨ Заменить", callback_data=f"movie_no_{i}")],
        [InlineKeyboardButton("❤️ В любимые", callback_data=f"movie_love_{i}"),
         InlineKeyboardButton(ui_label("save", "Сохранить"), callback_data=f"reco_{i}")],
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
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
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
    return InlineKeyboardMarkup(rows)


def _movie_mood_menu_kb():
    rows = []
    buttons = [InlineKeyboardButton(label, callback_data=f"movie_mood_{key}")
               for key, label in _MOOD_MENU]
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
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
    ms = store.get_list(config.MOVIE_SEEN_KEY, cid)
    bl = store.get_list(config.MOVIE_BLACKLIST_KEY, cid)
    notes_all = store.get_list(config.NOTES_KEY, cid)
    noted = [n.get("text", "") for n in notes_all
             if isinstance(n, dict) and "кино" in str(n.get("source", "")).lower()]
    used = set()
    for x in list(wl) + list(ms) + list(bl) + noted:
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
    Если подходящих нет - первый доступный."""
    used = {str(u).lower() for u in used_titles}
    fallback = None
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("title", "").lower() in used:
            continue
        tm = _tmdb_lookup(it.get("title", ""), it.get("title_en", "")) if config.TMDB_API_KEY else None
        disp = _display_title(it, tm).lower()
        if disp in used:
            continue
        if fallback is None:
            fallback = (it, tm)
        rating = (tm or {}).get("rating") or 0
        if not config.TMDB_API_KEY or rating >= MIN_TMDB_RATING:
            return it, tm
    return fallback if fallback else (items[0] if items else None, None)

async def _send_movie_card(bot, cid, it, i, tm="__lookup__", category=None):
    it = it if isinstance(it, dict) else {"title": str(it)}
    if tm == "__lookup__":
        tm = _tmdb_lookup(it.get("title", ""), it.get("title_en", "")) if config.TMDB_API_KEY else None
    title, msg = _movie_card(it, tm)
    kb = _movie_kb(i, category=category)
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
        await send_books_reco(bot, cid)
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
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    disp = _display_title(it, tm)
    movie_engine.mark_shown(cid, disp)
    store.last_recos[str(cid)] = {"kind": kind, "items": [disp]}
    store.last_source[str(cid)] = "Досуг · Кино"
    store.last_answer[str(cid)] = f"{disp} - {it.get('hook','')}"
    await _send_movie_card(bot, cid, it, 0, tm=tm)


def _movie_home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Подобрать кино", callback_data="movie_reco")],
        [InlineKeyboardButton("По жанру", callback_data="movie_genre_menu")],
        [InlineKeyboardButton("По настроению", callback_data="movie_mood_menu")],
        [InlineKeyboardButton("🎚️ Предпочтения", callback_data="movie_prefs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
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
    return "ru-RU"


async def send_movie_home(bot, cid, q=None):
    """Приветственный экран раздела «Кино» (тот же паттерн, что у Гардероба):
    сколько уже в любимых + какие жанры выбраны в предпочтениях + что сейчас в прокате,
    снизу — вход в обычную рекомендацию по любимым, по жанру или по настроению."""
    loved_count = len(store.get_list(config.WATCHLIST_KEY, cid))
    selected = {int(x) for x in (settings.get(cid, "movie_genres", []) or []) if str(x).isdigit()}
    genre_labels = [label for label, gid in _GENRE_ALL if gid in selected]

    s = store.get_settings(cid)
    cc = (s.get("cc") or config.DEFAULT_CITY.get("cc", "")).upper()
    country_label = _movie_country_label(s.get("country"), cc)
    now_playing = []
    if config.TMDB_API_KEY:
        try:
            now_playing = await asyncio.to_thread(tmdb.get_now_playing, cc, _movie_service_language(cid))
        except Exception:
            now_playing = []
    now_playing = [m for m in now_playing if (m.rating or 0) > 7]

    msg = leisure_ui.movie_home_screen(loved_count, genre_labels, country_label, now_playing[:10])
    kb = _movie_home_kb()
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


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
        verb = "понравился" if tm.get("via") == "recommendations" else "похоже на"
        return f"Потому что вам {verb} «{because}»" if verb == "понравился" else f"Похоже на «{because}»"
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
    return await asyncio.to_thread(_pick_good_movie, items, used)

async def movie_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.MOVIE_BLACKLIST_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"Понял, больше не буду рекомендовать «{title}». Вот другой вариант.")
    await _advance_movie(bot, cid)

def _book_cover(title, title_en=""):
    import requests
    for q in [t for t in (title_en, title) if t]:
        try:
            r = requests.get("https://openlibrary.org/search.json",
                             params={"title": q, "limit": 1}, timeout=10)
            docs = r.json().get("docs", [])
            if docs and docs[0].get("cover_i"):
                return f"https://covers.openlibrary.org/b/id/{docs[0]['cover_i']}-L.jpg"
        except Exception:
            continue
    return None

def _book_text(it):
    return leisure_ui.book_text(it)

def _book_kb(i):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Заменить", callback_data=f"book_no_{i}")],
        [InlineKeyboardButton("❤️ В любимые", callback_data=f"book_love_{i}"),
         InlineKeyboardButton(ui_label("save", "Сохранить"), callback_data=f"reco_{i}")],
        [InlineKeyboardButton("🎚️ Настройки книг", callback_data="as_love_books")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

async def _send_book_card(bot, cid, it, i):
    msg = _book_text(it)
    kb = _book_kb(i)
    cover = _book_cover(it.get("title", ""), it.get("title_en", ""))
    if cover:
        try:
            await bot.send_photo(chat_id=cid, photo=cover, caption=msg.text, caption_entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)

_FALLBACK_BOOKS = [
    {"title": "Мастер и Маргарита", "title_en": "The Master and Margarita", "year": "1967",
     "author": "Михаил Булгаков", "desc": "Сатира, мистика и история любви в одном романе.",
     "why": ["Многослойность: дьявол в Москве, Понтий Пилат и вечная любовь сразу",
             "Из тех книг, что перечитывают всю жизнь и каждый раз видят новое"],
     "plot": "Воланд со свитой устраивает хаос в советской Москве, а параллельно разворачивается роман Мастера о Пилате и история его любви к Маргарите.",
     "quote": "Рукописи не горят.",
     "hook": "Абсолютная классика, которую стоит прочесть хотя бы раз."},
    {"title": "1984", "title_en": "1984", "year": "1949",
     "author": "Джордж Оруэлл", "desc": "Главная антиутопия XX века о тотальной слежке.",
     "why": ["Предсказала мир, в котором мы во многом живём",
             "Меняет взгляд на свободу, правду и язык"],
     "plot": "Уинстон Смит живёт в государстве, где Большой Брат следит за каждым, и пытается сохранить способность думать самостоятельно.",
     "quote": "Война - это мир. Свобода - это рабство. Незнание - сила.",
     "hook": "Если не читал - это пробел, который точно стоит закрыть."},
    {"title": "Маленький принц", "title_en": "Le Petit Prince", "year": "1943",
     "author": "Антуан де Сент-Экзюпери", "desc": "Мудрая сказка для взрослых о главном.",
     "why": ["Читается за вечер, остаётся с тобой на годы",
             "Простыми словами о любви, дружбе и смысле"],
     "plot": "Лётчик в пустыне встречает мальчика с другой планеты, и через его рассказы открываются простые истины о том, что по-настоящему важно.",
     "quote": "Мы в ответе за тех, кого приручили.",
     "hook": "Тёплая книга, которую стоит прочитать всем."},
    {"title": "Убить пересмешника", "title_en": "To Kill a Mockingbird", "year": "1960",
     "author": "Харпер Ли", "desc": "Роман о справедливости и взрослении на юге США.",
     "why": ["Учит эмпатии без морализаторства",
             "Один из главных романов о совести и предрассудках"],
     "plot": "Девочка Скаут растёт в маленьком городке, где её отец-адвокат защищает несправедливо обвинённого, и взрослеет, сталкиваясь с миром взрослых.",
     "hook": "Книга из всех списков «обязательного к прочтению»."},
    {"title": "Сто лет одиночества", "title_en": "Cien años de soledad", "year": "1967",
     "author": "Габриэль Гарсиа Маркес", "desc": "Эталон магического реализма.",
     "why": ["Завораживающий язык и целый придуманный мир",
             "Семейная сага, которую считают одной из лучших книг века"],
     "plot": "История нескольких поколений семьи Буэндиа в вымышленном городке Макондо, где обыденное и волшебное переплетены.",
     "hook": "Если хочешь большую сильную книгу - начни с неё."},
    {"title": "Преступление и наказание", "title_en": "Crime and Punishment", "year": "1866",
     "author": "Фёдор Достоевский", "desc": "Психологический роман о вине и искуплении.",
     "why": ["Заглядывает в самые тёмные уголки разума",
             "Классика, которая держит как триллер"],
     "plot": "Студент Раскольников убивает старуху-процентщицу, проверяя свою теорию, и оказывается раздавлен муками совести.",
     "hook": "Достоевский, с которого стоит начать знакомство."},
]

def _book_used(cid):
    """Названия книг, которые нельзя повторять: любимые, знакомые, закладки, отклонённые."""
    used = set()
    for key in (config.BOOKS_KEY, config.BOOK_SEEN_KEY, config.READLIST_KEY, config.BOOK_BLACKLIST_KEY):
        for x in store.get_list(key, cid):
            used.add((x if isinstance(x, str) else str(x)).strip().lower())
    return used

def _fallback_book(cid, extra_skip=()):
    """Гарантированная рекомендация: популярная must-read книга, ещё не виденная пользователем."""
    used = _book_used(cid) | {str(x).strip().lower() for x in extra_skip}
    pool = [b for b in _FALLBACK_BOOKS if b["title"].lower() not in used] or _FALLBACK_BOOKS
    return random.choice(pool)

def _pick_good_book(items, cid, extra_skip=()):
    """Первая книга из items, которой ещё нет в списках/показанных; иначе - гарантированный фолбэк."""
    used = _book_used(cid) | {str(x).strip().lower() for x in extra_skip}
    for it in items or []:
        t = (it.get("title", "") or "").strip().lower()
        if t and t not in used:
            return it
    return _fallback_book(cid, extra_skip=extra_skip)

async def send_books_reco(bot, cid):
    if not _ensure_books(cid):
        await _ask_collect(bot, cid, "books")
        return
    items = []
    for _ in range(2):
        try:
            data = await asyncio.to_thread(content_recommend, "book", str(cid))
            items = data.get("items", []) if isinstance(data, dict) else []
        except Exception:
            items = []
        if items:
            break
    it = _pick_good_book(items, cid)
    store.last_recos[str(cid)] = {"kind": "book", "items": [it.get("title", "")]}
    store.last_source[str(cid)] = "Досуг · Книги"
    store.last_answer[str(cid)] = it.get("title", "")
    await _send_book_card(bot, cid, it, 0)

async def book_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.BOOK_BLACKLIST_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"Понял, «{title}» исключил. Вот другая книга.")
    try:
        data = await asyncio.to_thread(content_recommend, "book", str(cid))
        items = data.get("items", [])
    except Exception:
        items = []
    rec = store.last_recos.get(str(cid), {"kind": "book", "items": []})
    it = _pick_good_book(items, cid, extra_skip=rec.get("items", []))
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_book_card(bot, cid, it, ni)

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
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
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

async def _advance_book(bot, cid):
    """Загрузить следующую рекомендацию книги и показать карточку."""
    try:
        data = await asyncio.to_thread(content_recommend, "book", str(cid))
        items = data.get("items", [])
    except Exception:
        items = []
    rec = store.last_recos.get(str(cid), {"kind": "book", "items": []})
    it = _pick_good_book(items, cid, extra_skip=rec.get("items", []))
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_book_card(bot, cid, it, ni)

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
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_mydata_leisure")])
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
        await verify.safe_error(bot, cid, e)
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
        await verify.safe_error(bot, cid, e)
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
        await bot.send_message(chat_id=cid, text=f"❤️ «{title}» — в любимые (Кино). Вот ещё вариант.")
    await _advance_movie(bot, cid)

async def book_love(bot, cid, i):
    """Книга — в любимые (Мои книги), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.BOOKS_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"❤️ «{title}» — в любимые (Мои книги). Вот ещё вариант.")
    await _advance_book(bot, cid)

async def listen_love(bot, cid):
    """Артист - в любимые (Мои музыканты), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        artist = rec["items"][0]
        _add_unique(config.ARTISTS_KEY, cid, artist)
        await bot.send_message(chat_id=cid, text=f"❤️ «{artist}» — в любимые (Мои музыканты). Вот ещё вариант.")
    await send_listen(bot, cid)

async def add_reco(bot, cid, i):
    from datetime import datetime
    rec = store.last_recos.get(str(cid))
    if not (rec and i < len(rec["items"])):
        return
    title = rec["items"][i]
    kind = rec["kind"]
    folder = "Кино" if kind == "movie" else "Книги"
    if kind != "movie":
        _add_unique(config.READLIST_KEY, cid, title)
    if not _note_fav_exists(cid, title):
        store.add_to_list(config.NOTES_KEY, cid,
                          {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": title, "source": folder, "bucket": "fav"})
    await bot.send_message(chat_id=cid, text=f"{ui_label('save', 'Сохранено')} «{folder}»: {title}. Вот ещё вариант")
    if kind == "movie":
        # Следующая карточка — через TMDb-движок (LLM-фолбэк внутри).
        await _advance_movie(bot, cid)
        return
    try:
        data = await asyncio.to_thread(content_recommend, kind, str(cid))
        items = data.get("items", []) if isinstance(data, dict) else []
    except Exception:
        items = []
    if not items:
        return
    it = _pick_good_book(items, cid, extra_skip=rec["items"])
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_book_card(bot, cid, it, ni)

def _listen_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Заменить", callback_data="a_listen_no")],
        [InlineKeyboardButton("❤️ В любимые", callback_data="listen_love"),
         InlineKeyboardButton(ui_label("save", "Сохранить"), callback_data="listen_0")],
        [InlineKeyboardButton("🎚️ Настройка музыкантов", callback_data="as_love_artists")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

async def listen_dislike(bot, cid):
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        _add_unique(config.MUSIC_DISLIKE_KEY, cid, rec["items"][0])
    await send_listen(bot, cid)

async def send_listen(bot, cid):
    arts = _ensure_artists(cid)
    if not arts:
        await _ask_collect(bot, cid, "artists")
        return
    anchors = ", ".join(arts[:25])
    disliked = store.get_list(config.MUSIC_DISLIKE_KEY, cid)
    music_seen = store.get_list(config.MUSIC_SEEN_KEY, cid)
    notes = store.get_list(config.NOTES_KEY, cid)
    booked = [n.get("text", "") for n in notes
              if isinstance(n, dict) and "музык" in str(n.get("source", "")).lower()]
    known = (set(a.lower() for a in arts) | set(b.lower() for b in booked)
             | set(str(d).lower() for d in disliked) | set(str(s).lower() for s in music_seen))
    avoid_all = ", ".join(list(arts) + booked + [str(d) for d in disliked] + [str(s) for s in music_seen])[:600]
    web_block = ""
    web = await asyncio.to_thread(
        research.tavily_snippet,
        f"new music similar to {anchors[:60]} indie alternative recommendations 2024 2025",
        500,
    )
    if web:
        web_block = (
            f"\nАктуальные данные из сети (используй для реальных названий треков и альбомов):\n{web}\n"
        )
    data = None
    for _ in range(3):
        try:
            cand = await ai.allm_json(
                "Ты — музыкальный эксперт-минималист. Пиши коротко, емко, без воды и лишних вводных слов "
                '(никаких "стоит отметить", "однако"). Используй контрастную структуру.\n'
                "Правила подбора ориентиров:\n"
                "1. Сравнивай только с релевантными группами из вкуса пользователя.\n"
                "2. Не смешивай полярные жанры: никакого симфо-метала, чистого клубного хауса "
                "и других дальних жанров в сравнениях, если их нет во вкусе пользователя.\n\n"
                f"Любимые исполнители пользователя (его вкус): {anchors}.\n"
                f"НЕ предлагай никого из этого списка (уже в закладках/любимых/отклонены): {avoid_all}.\n"
                f"{web_block}"
                "Предложи РОВНО ОДНОГО НОВОГО исполнителя, максимально близкого по вкусу "
                "(электроника, синтипоп, альт, дрим-поп, дарквейв, арт-поп и близкое).\n"
                "Треки указывай ТОЛЬКО реально существующие — без выдуманных названий.\n"
                "В why дай 2 коротких контрастных пункта: сначала точное сходство, затем отличие/зацепку.\n"
                "Верни строго такой JSON:\n"
                '{"artist": "имя исполнителя", '
                '"desc": "1-2 строки образно о звучании", '
                '"why": ["пункт 1 - на кого из его любимых похоже и чем", "пункт 2"], '
                '"tracks": ["трек 1 - короткая пометка", "трек 2", "трек 3"], '
                '"fact": "1 интересный факт об исполнителе"}',
                1000, tier="leisure", route="gemini", module="leisure")
        except Exception:
            cand = None
        if cand and cand.get("artist") and cand["artist"].strip().lower() not in known:
            data = cand
            break
        data = cand
    if not data or not data.get("artist"):
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    artist = data.get("artist", "")
    store.last_recos[str(cid)] = {"kind": "listen", "items": [artist]}
    store.last_source[str(cid)] = "Досуг · Музыка"
    msg = leisure_ui.artist_card(data)
    store.last_answer[str(cid)] = leisure_ui.plain_from_html(msg.text)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_listen_kb())

async def add_listen(bot, cid, i):
    from datetime import datetime
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        title = rec["items"][0]
        if not _note_fav_exists(cid, title):
            store.add_to_list(config.NOTES_KEY, cid,
                              {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": title, "source": "Музыка", "bucket": "fav"})
        await bot.send_message(chat_id=cid, text=f"⭐️ В закладках «Музыка»: {title}. Вот ещё вариант.")
    await send_listen(bot, cid)

def _ensure_artists(cid):
    """Возвращает список артистов пользователя (без авто-сида)."""
    return store.get_list(config.ARTISTS_KEY, cid)

_TRIBUTE_MARKERS = ("tribute", "cover", "covers", "candlelight", "songs of", "the music of",
                    "performed by", "celebrating", "by candle", "symphonic", "reimagined",
                    "someone like", "a tribute", "in the style of", "plays the music", "experience:")

# Ticketmaster на бесплатном тарифе держит ~5 запросов/сек — без ограничения параллелизма
# и retry список из 30+ артистов заваливает API 429-ми, которые тихо трактуются как "нет концертов".
_TICKETMASTER_CONCURRENCY = asyncio.Semaphore(5)
_TICKETMASTER_RETRY_DELAYS = (0.5, 1.5, 3.0)

def _ticketmaster_get(url, params, timeout=15):
    """GET с retry только на 429/5xx (экспоненциальный backoff) — сетевые ошибки,
    таймауты и прочие сбои не ретраим, чтобы не блокировать поток попытками, которые не помогут."""
    import requests
    delays = (0,) + _TICKETMASTER_RETRY_DELAYS
    for i, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except Exception as e:
            api_usage.record_request("ticketmaster", ok=False, error=type(e).__name__)
            raise
        status = getattr(r, "status_code", None)
        api_usage.record_request("ticketmaster", ok=200 <= int(status or 0) < 300,
                                 status_code=status,
                                 error="" if 200 <= int(status or 0) < 300 else f"HTTP {status}",
                                 headers=r.headers)
        if status == 429 or (isinstance(status, int) and status >= 500):
            if i == len(delays) - 1:
                r.raise_for_status()
            continue
        r.raise_for_status()
        return r

def _ticketmaster_events_for_artist(artist, cc, start_dt="", end_dt="", size=3):
    if not config.TICKETMASTER_API_KEY:
        return []
    cache_key = f"{artist}|{cc}|{start_dt}|{end_dt}|{size}".lower()
    cached = util.ttl_get("ticketmaster", cache_key, 86400)
    if cached is not None:
        return cached
    params = {
        "apikey": config.TICKETMASTER_API_KEY,
        "keyword": artist,
        "countryCode": cc,
        "classificationName": "music",
        "size": size,
        "sort": "date,asc",
    }
    if start_dt:
        params["startDateTime"] = start_dt
    if end_dt:
        params["endDateTime"] = end_dt
    try:
        r = _ticketmaster_get("https://app.ticketmaster.com/discovery/v2/events.json", params)
    except Exception as e:
        _log.warning("ticketmaster events failed for artist=%s: %s", artist, e)
        return []
    events = []
    al = artist.lower()
    for e in r.json().get("_embedded", {}).get("events", []):
        name_l = e.get("name", "").lower()
        attractions = [att.get("name", "").lower()
                       for att in (e.get("_embedded", {}).get("attractions") or [])]
        attr_match = any(al in nm or nm in al for nm in attractions)
        if any(k in name_l for k in _TRIBUTE_MARKERS):
            continue
        if not (al in name_l or attr_match):
            continue
        e["_artist"] = artist
        events.append(e)
    return util.ttl_set("ticketmaster", cache_key, events)


async def _ticketmaster_fetch_throttled(fn, *args):
    """Ограничивает параллелизм запросов к Ticketmaster (_TICKETMASTER_CONCURRENCY),
    чтобы большие списки артистов не заваливали бесплатный тариф API 429-ми."""
    async with _TICKETMASTER_CONCURRENCY:
        return await asyncio.to_thread(fn, *args)

async def _ticketmaster_events_many(artists, cc, start_dt="", end_dt="", size=3, limit=40):
    tasks = [
        _ticketmaster_fetch_throttled(_ticketmaster_events_for_artist, artist, cc, start_dt, end_dt, size)
        for artist in artists[:limit]
    ]
    batches = await asyncio.gather(*tasks, return_exceptions=True)
    found, seen_pairs = {}, set()
    for batch in batches:
        if isinstance(batch, Exception):
            continue
        for e in batch:
            artist = e.get("_artist", "")
            date = e.get("dates", {}).get("start", {}).get("localDate", "")
            city = ((e.get("_embedded", {}).get("venues") or [{}])[0].get("city") or {}).get("name", "")
            pair = (artist.lower(), date, city.lower())
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            found[e.get("id") or f"{artist}:{date}:{e.get('name', '')}"] = e
    return sorted(found.values(), key=lambda e: e.get("dates", {}).get("start", {}).get("localDate") or "9999-99-99")

_GENRE_TRANSLATIONS = {
    "rock": "Рок", "pop": "Поп", "hip-hop/rap": "Хип-хоп", "hip hop": "Хип-хоп",
    "electronic": "Электроника", "dance/electronic": "Электроника", "jazz": "Джаз",
    "classical": "Классика", "r&b": "R&B", "country": "Кантри", "metal": "Метал",
    "reggae": "Регги", "blues": "Блюз", "folk": "Фолк", "world": "Мировая музыка",
    "alternative": "Альтернатива", "indie": "Инди", "punk": "Панк", "other": "",
    "undefined": "",
}

def _concert_genre(e):
    """Жанр из Ticketmaster classifications (genre/subGenre); '' если не найден или не музыка."""
    for c in (e.get("classifications") or []):
        genre = (c.get("genre") or {}).get("name", "")
        sub = (c.get("subGenre") or {}).get("name", "")
        label = sub if sub and sub.lower() not in ("other", "undefined") else genre
        if not label or label.lower() in ("other", "undefined"):
            continue
        return _GENRE_TRANSLATIONS.get(label.lower(), label)
    return ""

def _concert_min_price(e):
    """Минимальная цена из Ticketmaster priceRanges, отформатированная как '25 EUR'; '' если нет данных."""
    ranges = e.get("priceRanges") or []
    mins = [r.get("min") for r in ranges if isinstance(r.get("min"), (int, float))]
    if not mins:
        return ""
    best = min(mins)
    currency = (ranges[0].get("currency") or "").upper()
    amount = int(best) if best == int(best) else round(best, 2)
    return f"от {amount} {currency}".strip()

def _concert_place_name(name, cc=""):
    cc = (cc or "").upper()
    by_cc = {
        "NL": "Нидерландах",
        "BE": "Бельгии",
        "DE": "Германии",
        "FR": "Франции",
        "GB": "Великобритании",
        "ES": "Испании",
        "IT": "Италии",
        "AT": "Австрии",
        "CH": "Швейцарии",
        "PL": "Польше",
        "SE": "Швеции",
        "DK": "Дании",
        "PT": "Португалии",
    }
    if cc in by_cc:
        return by_cc[cc]
    low = str(name or "").strip().lower()
    if low in ("нидерланды", "netherlands", "nl"):
        return "Нидерландах"
    return str(name or "твоей стране").strip()

_CONCERTS_CACHE_TTL = 7 * 86400  # неделя — кэш обновляется job'ом по воскресеньям перед уведомлением


def _concerts_cache_get(cid, cc):
    """Кэшированный список концертов пользователя за неделю; None если нет/устарел/не тот cc."""
    entry = store._load(config.CONCERTS_CACHE_KEY).get(str(cid))
    if not entry or entry.get("cc") != cc:
        return None
    import time
    if time.time() - entry.get("ts", 0) > _CONCERTS_CACHE_TTL:
        return None
    return entry.get("events", [])


def _concerts_cache_set(cid, cc, events):
    import time
    d = store._load(config.CONCERTS_CACHE_KEY)
    d[str(cid)] = {"ts": time.time(), "cc": cc, "events": events}
    store._save(config.CONCERTS_CACHE_KEY, d)


async def _fetch_concerts(artists, cc, cname):
    """Живой запрос к Ticketmaster без кэша — общая часть для
    find_concerts/send_weekly_events и для job'а прогрева кэша по воскресеньям."""
    from datetime import datetime, timedelta
    now = datetime.now(config.TZ)
    date_from = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (now + timedelta(days=182)).strftime("%Y-%m-%dT%H:%M:%SZ")  # ~6 месяцев

    return await _ticketmaster_events_many(artists, cc, start_dt=date_from, end_dt=date_to, size=10, limit=40)


async def refresh_concerts_cache(cid):
    """Прогревает недельный кэш концертов пользователя — вызывается job'ом по воскресеньям
    перед уведомлением «Афиша недели», чтобы само уведомление и последующие «Концерты» не ждали API."""
    artists = _ensure_artists(cid)
    if not artists or not config.TICKETMASTER_API_KEY:
        return
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    cname = s.get("country") or "твоя страна"
    events = await _fetch_concerts(artists, cc, cname)
    _concerts_cache_set(cid, cc, events)


_SEEN_CONCERTS_LIMIT = 300  # ограничение размера истории «виденных» concert ID на пользователя


def _concert_event_id(e):
    """Стабильный ID концерта для сравнения «уже видел / новый»: нативный id источника,
    иначе (артист, дата, город) — тот же ключ, которым события дедуплицируются в _ticketmaster_events_many."""
    if e.get("id"):
        return str(e["id"])
    artist = e.get("_artist", "")
    date = e.get("dates", {}).get("start", {}).get("localDate", "")
    city = ((e.get("_embedded", {}).get("venues") or [{}])[0].get("city") or {}).get("name", "")
    return f"{artist.lower()}:{date}:{city.lower()}"


def _seen_concerts_has_history(cid):
    return str(cid) in store._load(config.SEEN_CONCERTS_KEY)


def _seen_concerts_get(cid):
    return set(store._load(config.SEEN_CONCERTS_KEY).get(str(cid), []))


def _seen_concerts_add(cid, ids):
    d = store._load(config.SEEN_CONCERTS_KEY)
    merged = list(dict.fromkeys([*d.get(str(cid), []), *ids]))
    d[str(cid)] = merged[-_SEEN_CONCERTS_LIMIT:]
    store._save(config.SEEN_CONCERTS_KEY, d)


async def _fetch_favorite_events(cid):
    """Концерты избранных артистов пользователя в его стране: сперва недельный кэш (его прогревает
    job_refresh_concerts_cache по вс перед этой же проверкой), иначе живой запрос. [] если артистов/ключа нет."""
    artists = _ensure_artists(cid)
    if not artists or not config.TICKETMASTER_API_KEY:
        return []
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    cname = s.get("country") or "твоя страна"
    cached = _concerts_cache_get(cid, cc)
    events = cached if cached is not None else await _fetch_concerts(artists, cc, cname)

    from datetime import datetime
    today_str = datetime.now(config.TZ).date().isoformat()
    return [e for e in events
            if e.get("dates", {}).get("start", {}).get("localDate", "9999") >= today_str]


async def find_new_favorite_concerts(cid):
    """Сравнивает свежие концерты избранных артистов с уже виденными и возвращает только новые
    (без побочных эффектов — запись в seen делает вызывающий код после успешной отправки)."""
    events = await _fetch_favorite_events(cid)
    seen = _seen_concerts_get(cid)
    return [e for e in events if _concert_event_id(e) not in seen]


async def _build_new_concerts_msg(cid):
    """Новые концерты любимых артистов -> MessageSpec, либо None если показывать нечего.
    Молчит, если ничего нового не появилось с прошлой проверки. При первом включении
    (нет истории seen) тихо запоминает текущие концерты, ничего не шлёт — иначе первый
    запуск продублировал бы всю афишу как «новое»."""
    if not _seen_concerts_has_history(cid):
        events = await _fetch_favorite_events(cid)
        _seen_concerts_add(cid, [_concert_event_id(e) for e in events])
        return None

    new_events = await find_new_favorite_concerts(cid)
    if not new_events:
        return None
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    flag = util.flag_from_cc(cc)

    from util import _MONTHS

    def _fmt_date(ds):
        try:
            y, m, dd = ds.split("-")
            return f"{int(dd)} {_MONTHS[int(m)-1]} {y}"
        except Exception:
            return ds

    rows_data = []
    for e in new_events:
        date = e.get("dates", {}).get("start", {}).get("localDate", "")
        city = ((e.get("_embedded", {}).get("venues") or [{}])[0].get("city") or {}).get("name", "")
        rows_data.append({
            "artist": e.get("_artist", ""),
            "flag": flag,
            "place": city,
            "genre": _concert_genre(e),
            "price": _concert_min_price(e),
            "date": _fmt_date(date) if date else "",
            "url": e.get("url", ""),
        })

    msg = leisure_ui.concerts_list("Новые концерты твоих артистов", rows_data)
    _seen_concerts_add(cid, [_concert_event_id(e) for e in new_events])
    return msg


async def find_concerts(bot, cid, mode="home"):
    if not config.TICKETMASTER_API_KEY:
        await bot.send_message(chat_id=cid,
            text="Поиск мероприятий требует бесплатный ключ Ticketmaster.\n"
                 "Заведи его на developer.ticketmaster.com и добавь на Railway переменную TICKETMASTER_API_KEY.")
        return
    artists = _ensure_artists(cid)
    if not artists:
        await bot.send_message(chat_id=cid, text="Не удалось загрузить артистов. Добавь их в настройках.")
        return
    s = store.get_settings(cid)
    home_cc = (s.get("cc") or "NL").upper()
    home_flag = util.flag_from_cc(home_cc)
    home_name = s.get("country") or "твоя страна"
    CC_MAP = {
        "nl": ("NL", COUNTRY_EMOJI["nl"], "Нидерланды"),
        "be": ("BE", COUNTRY_EMOJI["be"], "Бельгия"),
        "de": ("DE", COUNTRY_EMOJI["de"], "Германия"),
        "fr": ("FR", COUNTRY_EMOJI["fr"], "Франция"),
        "gb": ("GB", COUNTRY_EMOJI["gb"], "Великобритания"),
        "es": ("ES", COUNTRY_EMOJI["es"], "Испания"),
        "it": ("IT", COUNTRY_EMOJI["it"], "Италия"),
        "at": ("AT", COUNTRY_EMOJI["at"], "Австрия"),
        "ch": ("CH", COUNTRY_EMOJI["ch"], "Швейцария"),
        "pl": ("PL", COUNTRY_EMOJI["pl"], "Польша"),
        "se": ("SE", COUNTRY_EMOJI["se"], "Швеция"),
        "dk": ("DK", COUNTRY_EMOJI["dk"], "Дания"),
        "pt": ("PT", COUNTRY_EMOJI["pt"], "Португалия"),
    }
    if mode in CC_MAP:
        cc, flag, cname = CC_MAP[mode]
    else:
        cc, flag, cname = home_cc, home_flag, home_name
    cname_place = _concert_place_name(cname, cc)

    from util import _MONTHS
    from datetime import datetime

    events = _concerts_cache_get(cid, cc)
    if events is None:
        events = await _fetch_concerts(artists, cc, cname)
        _concerts_cache_set(cid, cc, events)

    rows = [
        [InlineKeyboardButton("🌍 Сменить страну", callback_data="a_concerts_pick")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ]
    kb = InlineKeyboardMarkup(rows)

    def _fmt_date(ds):
        try:
            y, m, dd = ds.split("-")
            return f"{int(dd)} {_MONTHS[int(m)-1]} {y}"
        except Exception:
            return ds

    place_label = f"Концерты в {cname_place} — ближайшие 6 месяцев"
    today_str = datetime.now(config.TZ).date().isoformat()
    seen_artist_events = set()
    rows_data = []
    for e in events:
        artist = e.get("_artist", "")
        date = e.get("dates", {}).get("start", {}).get("localDate", "")
        if date and date < today_str:
            continue
        city = ((e.get("_embedded", {}).get("venues") or [{}])[0].get("city") or {}).get("name", "")
        dedup_key = (artist.lower(), date, city.lower())
        if dedup_key in seen_artist_events:
            continue
        seen_artist_events.add(dedup_key)

        place = city
        rows_data.append({
            "artist": artist,
            "flag": flag,
            "place": place,
            "genre": _concert_genre(e),
            "price": _concert_min_price(e),
            "date": _fmt_date(date) if date else "",
            "url": e.get("url", ""),
        })

    msg = leisure_ui.concerts_list(place_label, rows_data)
    store.last_source[str(cid)] = "Досуг · Концерты"
    store.last_answer[str(cid)] = msg.text
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
                           disable_web_page_preview=True)




async def _build_weekly_events_msg(cid):
    """Афиша недели: концерты артистов пользователя + кинопремьеры ближайших дней -> MessageSpec."""
    from datetime import datetime, timedelta

    s = store.get_settings(cid)
    cc = (s.get("cc") or config.DEFAULT_CITY.get("cc", "")).upper()
    cname = _concert_place_name(s.get("country"), cc)
    now = datetime.now(config.TZ)
    period_start = now.date()
    period_end = (now + timedelta(days=7)).date()
    today_str = period_start.isoformat()
    date_to_str = period_end.isoformat()

    # --- Концерты ---
    # Читаем недельный кэш (обновлён job'ом refresh_concerts_cache перед этим уведомлением),
    # чтобы не делать живой запрос к Ticketmaster по всем артистам прямо в момент отправки.
    concert_items = []
    if config.TICKETMASTER_API_KEY:
        artists = _ensure_artists(cid)
        if artists:
            cached = _concerts_cache_get(cid, cc)
            events = cached if cached is not None else await _fetch_concerts(artists, cc, cname)
            if cached is None:
                _concerts_cache_set(cid, cc, events)
            events = [e for e in events
                      if today_str <= e.get("dates", {}).get("start", {}).get("localDate", "9999") <= date_to_str]
            for e in events[:5]:
                artist = e.get("_artist", "")
                date_str = e.get("dates", {}).get("start", {}).get("localDate", "")
                ven = (e.get("_embedded", {}).get("venues") or [{}])[0]
                vn = ven.get("name", "")
                city = (ven.get("city") or {}).get("name", "")
                venue_str = ", ".join(x for x in [vn, city] if x)
                concert_items.append({
                    "title": artist,
                    "place": venue_str,
                    "date": date_str,
                })

    # --- Кинопремьеры ---
    movie_items = []
    if config.TMDB_API_KEY:
        try:
            movie_items = await asyncio.to_thread(
                tmdb.get_upcoming_theatrical_releases,
                cc,
                period_start,
                period_end,
                _movie_service_language(cid),
            )
        except Exception:
            movie_items = []

    return leisure_ui.weekly_events_card(period_start, period_end, concert_items, movie_items[:5])


async def send_weekend_events(bot, cid):
    """Пятница 10:00 — «Куда сходить»: афиша недели (концерты + кино) и новые концерты
    любимых артистов одним сообщением."""
    from ui.builder import MessageBuilder
    weekly_msg = await _build_weekly_events_msg(cid)
    new_concerts_msg = await _build_new_concerts_msg(cid)
    combined = MessageBuilder()
    combined.embed(weekly_msg)
    if new_concerts_msg is not None:
        combined.embed(new_concerts_msg)
    msg = combined.build_stripped()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, disable_web_page_preview=True)


async def concert_pick_country(bot, cid):
    countries = [
        ("at", "Австрия", f"{COUNTRY_EMOJI['at']} Австрия"),
        ("be", "Бельгия", f"{COUNTRY_EMOJI['be']} Бельгия"),
        ("gb", "Великобритания", f"{COUNTRY_EMOJI['gb']} Великобр."),
        ("de", "Германия", f"{COUNTRY_EMOJI['de']} Германия"),
        ("dk", "Дания", f"{COUNTRY_EMOJI['dk']} Дания"),
        ("es", "Испания", f"{COUNTRY_EMOJI['es']} Испания"),
        ("it", "Италия", f"{COUNTRY_EMOJI['it']} Италия"),
        ("nl", "Нидерланды", f"{COUNTRY_EMOJI['nl']} Нидерланды"),
        ("pl", "Польша", f"{COUNTRY_EMOJI['pl']} Польша"),
        ("pt", "Португалия", f"{COUNTRY_EMOJI['pt']} Португалия"),
        ("fr", "Франция", f"{COUNTRY_EMOJI['fr']} Франция"),
        ("ch", "Швейцария", f"{COUNTRY_EMOJI['ch']} Швейцария"),
        ("se", "Швеция", f"{COUNTRY_EMOJI['se']} Швеция"),
    ]
    buttons = [
        InlineKeyboardButton(label, callback_data=f"a_concerts_{cc}")
        for cc, _name, label in sorted(countries, key=lambda x: x[1])
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
    await bot.send_message(chat_id=cid, text="🌍 Выбери страну для поиска концертов:",
                           reply_markup=InlineKeyboardMarkup(rows))
