from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import asyncio
import logging
import re
import random
import time
import config

_log = logging.getLogger(__name__)
import store
import ai
import util
import research
import settings
from util import country_flag, esc
import verify
from ui import leisure as leisure_ui

# ===== КОНТЕНТ (content.py) =====

# --- Инлайн-сбор предпочтений при пустом профиле ---
_COLLECT_HINTS = {
    "artists": (
        "🎵 <b>Ещё нет любимых исполнителей</b>\n\n"
        "Чтобы подбирать музыку под твой вкус, мне нужно знать, кого ты слушаешь.\n\n"
        "Пришли список прямо сюда — по одному или через запятую:\n"
        "<i>Например: The xx, Massive Attack, Portishead</i>"
    ),
    "movies": (
        "🎬 <b>Ещё нет любимых фильмов</b>\n\n"
        "Пришли список фильмов или сериалов, которые тебе понравились, — "
        "подберу похожее.\n\n"
        "<i>Например: Паразиты, Эйфория, Настоящий детектив</i>"
    ),
    "books": (
        "📚 <b>Ещё нет любимых книг</b>\n\n"
        "Пришли список книг, которые ты читал и которые тебе понравились, — "
        "подберу похожее.\n\n"
        "<i>Например: Дюна, Атлант расправил плечи, Идиот</i>"
    ),
}

async def _ask_collect(bot, cid, kind: str):
    """Показывает экран сбора предпочтений и ставит pending_input."""
    import secure as _sec
    store.pending_input[str(cid)] = f"collect_{kind}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Пропустить", callback_data="m_leisure")]])
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
            config.READLIST_KEY, config.FAVORITES_KEY, config.COUNTRIES_KEY]
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
        priority = settings.priority_context(cid)
        priority_block = f"\n{priority}\n" if priority else ""
        web_block = ""
        web = research.tavily_snippet(
            f"лучшие фильмы сериалы 2024 2025 драма артхаус триллер похожие {anchors[:80]}",
            max_chars=700,
        )
        if web:
            web_block = f"\nАктуальные новинки и рекомендации из сети (используй как источник реальных названий):\n{web}\n"
        prompt = f"""Ты опытный кинокритик. Порекомендуй фильмы и сериалы под вкус пользователя.
Его любимые работы (референсы вкуса): {anchors}
{priority_block}
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
    priority = settings.priority_context(cid)
    priority_block = f"\n{priority}\n" if priority else ""
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
{priority_block}
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

def _tmdb_upcoming(cc):
    if not config.TMDB_API_KEY:
        return []
    key = (cc or "").upper()
    cached = util.ttl_get("tmdb_upcoming", key, 86400)
    if cached is not None:
        return cached
    import requests
    try:
        r = requests.get("https://api.themoviedb.org/3/movie/upcoming",
            params={"api_key": config.TMDB_API_KEY, "language": "ru-RU",
                    "region": key, "page": 1}, timeout=15)
        return util.ttl_set("tmdb_upcoming", key, r.json().get("results", []))
    except Exception:
        return util.ttl_set("tmdb_upcoming", key, [])

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

def _movie_kb(i):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐️ Сохранить", callback_data=f"reco_{i}")],
        [InlineKeyboardButton("✨ Заменить", callback_data=f"movie_no_{i}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")],
    ])

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

async def _send_movie_card(bot, cid, it, i, tm="__lookup__"):
    it = it if isinstance(it, dict) else {"title": str(it)}
    if tm == "__lookup__":
        tm = _tmdb_lookup(it.get("title", ""), it.get("title_en", "")) if config.TMDB_API_KEY else None
    title, msg = _movie_card(it, tm)
    kb = _movie_kb(i)
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
    items = []
    for _ in range(2):
        try:
            data = await asyncio.to_thread(content_recommend, kind, str(cid))
            items = _normalize_movie_items(data.get("items", []) if isinstance(data, dict) else [])
        except Exception:
            items = []
        if items:
            break
    if not items:
        items = _fallback_movie_items(cid)
    if not items:
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    it, tm = await asyncio.to_thread(_pick_good_movie, items, _movie_used(cid))
    if not it:
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    disp = _display_title(it, tm)
    store.last_recos[str(cid)] = {"kind": kind, "items": [disp]}
    store.last_source[str(cid)] = "Досуг · Кино"
    store.last_answer[str(cid)] = f"{disp} - {it.get('hook','')}"
    await _send_movie_card(bot, cid, it, 0, tm=tm)

async def movie_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        store.add_to_list(config.MOVIE_BLACKLIST_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"Понял, больше не буду рекомендовать «{title}». Вот другой вариант 👇")
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
        [InlineKeyboardButton("⭐️ Сохранить", callback_data=f"reco_{i}"),
         InlineKeyboardButton("✨ Заменить", callback_data=f"book_no_{i}")],
        [InlineKeyboardButton("🎚️ Настройки книг", callback_data="set_books")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")],
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
        store.add_to_list(config.BOOK_BLACKLIST_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"Понял, «{title}» исключил. Вот другая книга 👇")
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
    """Загрузить следующую рекомендацию кино и показать карточку."""
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
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    used = _movie_used(cid) | {str(x).lower() for x in rec["items"]}
    it, tm = await asyncio.to_thread(_pick_good_movie, items, used)
    if not it:
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    rec["items"].append(_display_title(it, tm))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_movie_card(bot, cid, it, ni, tm=tm)

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

async def movie_love(bot, cid, i):
    """Фильм/сериал — в любимые (watchlist), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.WATCHLIST_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"❤️ «{title}» — в любимые (Кино). Вот ещё вариант 👇")
    await _advance_movie(bot, cid)

async def movie_seen(bot, cid, i):
    """Фильм — уже знакомо: запомнить и не повторять, вкус не меняется."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.MOVIE_SEEN_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"✅ «{title}» — уже знакомо, не буду повторять. Вот ещё вариант 👇")
    await _advance_movie(bot, cid)

async def book_love(bot, cid, i):
    """Книга — в любимые (Мои книги), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.BOOKS_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"❤️ «{title}» — в любимые (Мои книги). Вот ещё вариант 👇")
    await _advance_book(bot, cid)

async def book_seen(bot, cid, i):
    """Книга — уже знакомо: запомнить и не повторять, вкус не меняется."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.BOOK_SEEN_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"✅ «{title}» — уже знакомо, не буду повторять. Вот ещё вариант 👇")
    await _advance_book(bot, cid)

async def listen_love(bot, cid):
    """Артист - в любимые (Мои музыканты), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        artist = rec["items"][0]
        _add_unique(config.ARTISTS_KEY, cid, artist)
        await bot.send_message(chat_id=cid, text=f"❤️ «{artist}» — в любимые (Мои музыканты). Вот ещё вариант 👇")
    await send_listen(bot, cid)

async def listen_seen(bot, cid):
    """Артист — уже знакомо: запомнить и не повторять, вкус не меняется."""
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        artist = rec["items"][0]
        _add_unique(config.MUSIC_SEEN_KEY, cid, artist)
        await bot.send_message(chat_id=cid, text=f"✅ «{artist}» — уже знакомо, не буду повторять. Вот ещё вариант 👇")
    await send_listen(bot, cid)

async def add_reco(bot, cid, i):
    from datetime import datetime
    rec = store.last_recos.get(str(cid))
    if not (rec and i < len(rec["items"])):
        return
    title = rec["items"][i]
    kind = rec["kind"]
    folder = "Кино" if kind == "movie" else "Книги"
    # для книг — в список «прочту», для фильмов — только в закладки (не в любимые/anchor)
    if kind != "movie":
        _add_unique(config.READLIST_KEY, cid, title)
    if not _note_fav_exists(cid, title):
        store.add_to_list(config.NOTES_KEY, cid,
                          {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": title, "source": folder, "bucket": "fav"})
    await bot.send_message(chat_id=cid, text=f"⭐️ Сохранено «{folder}»: {title}. Вот ещё вариант 👇")
    try:
        data = await asyncio.to_thread(content_recommend, kind, str(cid))
        items = data.get("items", []) if isinstance(data, dict) else []
    except Exception:
        items = []
    if not items:
        return
    if kind == "movie":
        items = _normalize_movie_items(items)
        it, tm = await asyncio.to_thread(_pick_good_movie, items, set(rec["items"]))
        if not it:
            return
        rec["items"].append(_display_title(it, tm))
        store.last_recos[str(cid)] = rec
        ni = len(rec["items"]) - 1
        await _send_movie_card(bot, cid, it, ni, tm=tm)
    else:
        it = _pick_good_book(items, cid, extra_skip=rec["items"])
        rec["items"].append(it.get("title", ""))
        store.last_recos[str(cid)] = rec
        ni = len(rec["items"]) - 1
        await _send_book_card(bot, cid, it, ni)

def _list_text(it):
    return it.get("name", "") if isinstance(it, dict) else str(it)

async def send_watchlist(bot, cid):
    lst = store.get_list(config.WATCHLIST_KEY, cid)
    rows = []
    if lst:
        rows.append([InlineKeyboardButton("❌ Очистить список", callback_data="a_watchclean")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")])
    await bot.send_message(chat_id=cid,
        text="🍿 Посмотреть:\n" + ("\n".join(f"• {_list_text(x)}" for x in lst) if lst else "пусто"),
        reply_markup=InlineKeyboardMarkup(rows))

async def send_readlist(bot, cid):
    lst = store.get_list(config.READLIST_KEY, cid)
    rows = []
    if lst:
        rows.append([InlineKeyboardButton("❌ Очистить список", callback_data="a_readclean")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")])
    await bot.send_message(chat_id=cid,
        text="📚 Почитать:\n" + ("\n".join(f"• {_list_text(x)}" for x in lst) if lst else "пусто"),
        reply_markup=InlineKeyboardMarkup(rows))

async def send_fav(bot, cid):
    favs = store.get_list(config.FAVORITES_KEY, cid)
    store.pending_input[str(cid)] = "favorite"
    await bot.send_message(chat_id=cid,
        text="❤️ Любимое:\n" + ("\n".join(f"• {f}" for f in favs) if favs else "пусто") + "\n\nНапиши фильм/сериал/книгу - добавлю.")

async def add_fav(bot, cid, text):
    added = _add_unique(config.FAVORITES_KEY, cid, text)
    await bot.send_message(chat_id=cid, text="Добавил в любимое." if added else "Уже в любимом.")

def _listen_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐️ Сохранить", callback_data="listen_0"),
         InlineKeyboardButton("✨ Заменить", callback_data="a_listen_no")],
        [InlineKeyboardButton("🎚️ Настройка музыкантов", callback_data="set_artists")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")],
    ])

async def listen_dislike(bot, cid):
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        store.add_to_list(config.MUSIC_DISLIKE_KEY, cid, rec["items"][0])
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
        await bot.send_message(chat_id=cid, text=f"⭐ В закладках «Музыка»: {title}. Вот ещё вариант 👇")
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
        r = requests.get(url, params=params, timeout=timeout)
        status = getattr(r, "status_code", None)
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


def _concert_country_search_name(name, cc=""):
    by_cc = {
        "NL": "Netherlands", "BE": "Belgium", "DE": "Germany", "FR": "France",
        "GB": "United Kingdom", "ES": "Spain", "IT": "Italy", "AT": "Austria",
        "CH": "Switzerland", "PL": "Poland", "SE": "Sweden", "DK": "Denmark",
        "PT": "Portugal",
    }
    return by_cc.get((cc or "").upper(), str(name or "").strip() or "Netherlands")

def _eventbrite_events(query, country_name, size=10, category_id="103"):
    if not config.EVENTBRITE_API_KEY:
        return []
    cache_key = f"eventbrite|{query}|{country_name}|{size}|{category_id}".lower()
    cached = util.ttl_get("eventbrite", cache_key, 21600)
    if cached is not None:
        return cached
    import requests
    try:
        r = requests.get(
            "https://www.eventbriteapi.com/v3/events/search/",
            headers={"Authorization": f"Bearer {config.EVENTBRITE_API_KEY}"},
            params={
                "q": query,
                "location.address": country_name,
                "categories": category_id,
                "sort_by": "date",
                "expand": "venue",
                "page_size": size,
            },
            timeout=15,
        )
        r.raise_for_status()
    except Exception:
        return []
    events = []
    for e in r.json().get("events", []):
        name = (e.get("name") or {}).get("text") or ""
        url = e.get("url") or ""
        start = (e.get("start") or {}).get("local") or ""
        venue = e.get("venue") or {}
        city = ((venue.get("address") or {}).get("city") or "").strip()
        venue_name = (venue.get("name") or "").strip()
        if not name or any(k in name.lower() for k in _TRIBUTE_MARKERS):
            continue
        events.append({
            "id": e.get("id") or url or name,
            "name": name,
            "url": url,
            "_artist": query,
            "_source": "Eventbrite",
            "dates": {"start": {"localDate": start[:10]}},
            "_embedded": {"venues": [{"name": venue_name, "city": {"name": city}}]},
        })
    return util.ttl_set("eventbrite", cache_key, events)

async def _eventbrite_events_many(artists, country_name, size=3, limit=40):
    tasks = [
        asyncio.to_thread(_eventbrite_events, artist, country_name, size)
        for artist in artists[:limit]
    ]
    batches = await asyncio.gather(*tasks, return_exceptions=True)
    found = {}
    for batch in batches:
        if isinstance(batch, Exception):
            continue
        for e in batch:
            found[e.get("id") or e.get("url") or e.get("name", "")] = e
    return sorted(found.values(), key=lambda e: e.get("dates", {}).get("start", {}).get("localDate") or "9999-99-99")

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

def _web_concert_links_for_artists(artists, country_name, limit_artists=8, per_artist=2):
    """Fallback через веб-поиск: Songkick/Bandsintown/официальные страницы, если Ticketmaster пуст."""
    rows, seen = [], set()
    domains = ("songkick.com", "bandsintown.com", "eventbrite.", "ticketmaster.", "eventim.", "livenation.")
    for artist in artists[:limit_artists]:
        query = f'{artist} concerts {country_name} Songkick Bandsintown official tour'
        for result in research.web_search(query, max_results=6):
            url = (result.get("url") or "").strip()
            title = (result.get("title") or "").strip()
            if not url or url in seen:
                continue
            low = url.lower()
            if not any(domain in low for domain in domains):
                continue
            seen.add(url)
            rows.append({"artist": str(artist), "title": title or str(artist), "url": url})
            if sum(1 for r in rows if r["artist"] == str(artist)) >= per_artist:
                break
    return rows

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

_CONCERTS_CACHE_TTL = 7 * 86400  # неделя — кэш обновляется job'ом по воскресеньям перед рассылкой


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
    """Живой запрос к Ticketmaster (+ Eventbrite фолбэк) без кэша — общая часть для
    find_concerts/send_weekly_events и для job'а прогрева кэша по воскресеньям."""
    from datetime import datetime, timedelta
    now = datetime.now(config.TZ)
    date_from = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (now + timedelta(days=182)).strftime("%Y-%m-%dT%H:%M:%SZ")  # ~6 месяцев

    events = await _ticketmaster_events_many(artists, cc, start_dt=date_from, end_dt=date_to, size=10, limit=40)
    if not events:
        eventbrite_country = _concert_country_search_name(cname, cc)
        events = await _eventbrite_events_many(artists, eventbrite_country, size=10, limit=40)
    return events


async def refresh_concerts_cache(cid):
    """Прогревает недельный кэш концертов пользователя — вызывается job'ом по воскресеньям
    перед рассылкой «Афиша недели», чтобы сама рассылка и последующие «Концерты» не ждали API."""
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
    иначе (артист, дата, город) — тот же ключ, которым события дедуплицируются в _ticketmaster_events_many/_eventbrite_events_many."""
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
    if cached is not None:
        return cached
    return await _fetch_concerts(artists, cc, cname)


async def find_new_favorite_concerts(cid):
    """Сравнивает свежие концерты избранных артистов с уже виденными и возвращает только новые
    (без побочных эффектов — запись в seen делает вызывающий код после успешной отправки)."""
    events = await _fetch_favorite_events(cid)
    seen = _seen_concerts_get(cid)
    return [e for e in events if _concert_event_id(e) not in seen]


async def send_new_concerts_notif(bot, cid):
    """⭐ Новые концерты любимых артистов — событийное уведомление: молчит, если ничего
    нового не появилось с прошлой проверки (в отличие от еженедельной «Афиши»).
    При первом включении (нет истории seen) тихо запоминает текущие концерты, ничего не шлёт —
    иначе первый запуск продублировал бы всю афишу как «новое»."""
    if not _seen_concerts_has_history(cid):
        events = await _fetch_favorite_events(cid)
        _seen_concerts_add(cid, [_concert_event_id(e) for e in events])
        return

    new_events = await find_new_favorite_concerts(cid)
    if not new_events:
        return
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    flag = util.flag_from_cc(cc) or "🏳"

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
    store.last_source[str(cid)] = "Досуг · Концерты"
    store.last_answer[str(cid)] = msg.text
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, disable_web_page_preview=True)
    _seen_concerts_add(cid, [_concert_event_id(e) for e in new_events])


async def find_concerts(bot, cid, mode="home"):
    if not config.TICKETMASTER_API_KEY:
        await bot.send_message(chat_id=cid,
            text="🎫 Поиск мероприятий требует бесплатный ключ Ticketmaster.\n"
                 "Заведи его на developer.ticketmaster.com и добавь на Railway переменную TICKETMASTER_API_KEY.")
        return
    artists = _ensure_artists(cid)
    if not artists:
        await bot.send_message(chat_id=cid, text="Не удалось загрузить артистов. Добавь их в настройках.")
        return
    s = store.get_settings(cid)
    home_cc = (s.get("cc") or "NL").upper()
    home_flag = util.flag_from_cc(home_cc) or "🏳"
    home_name = s.get("country") or "твоя страна"
    CC_MAP = {"nl": ("NL", "🇳🇱", "Нидерланды"),
              "be": ("BE", "🇧🇪", "Бельгия"), "de": ("DE", "🇩🇪", "Германия"),
              "fr": ("FR", "🇫🇷", "Франция"), "gb": ("GB", "🇬🇧", "Великобритания"),
              "es": ("ES", "🇪🇸", "Испания"), "it": ("IT", "🇮🇹", "Италия"),
              "at": ("AT", "🇦🇹", "Австрия"), "ch": ("CH", "🇨🇭", "Швейцария"),
              "pl": ("PL", "🇵🇱", "Польша"), "se": ("SE", "🇸🇪", "Швеция"),
              "dk": ("DK", "🇩🇰", "Дания"), "pt": ("PT", "🇵🇹", "Португалия")}
    if mode in CC_MAP:
        cc, flag, cname = CC_MAP[mode]
    else:
        cc, flag, cname = home_cc, home_flag, home_name
    cname_place = _concert_place_name(cname, cc)

    from util import _MONTHS

    events = _concerts_cache_get(cid, cc)
    if events is None:
        events = await _fetch_concerts(artists, cc, cname)
        _concerts_cache_set(cid, cc, events)

    rows = [[InlineKeyboardButton("🌍 Сменить страну", callback_data="a_concerts_pick")]]
    kb = InlineKeyboardMarkup(rows)

    def _fmt_date(ds):
        try:
            y, m, dd = ds.split("-")
            return f"{int(dd)} {_MONTHS[int(m)-1]} {y}"
        except Exception:
            return ds

    place_label = f"Концерты в {cname_place} — ближайшие 6 месяцев"
    seen_artist_events = set()
    rows_data = []
    for e in events:
        artist = e.get("_artist", "")
        date = e.get("dates", {}).get("start", {}).get("localDate", "")
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




async def _rank_concerts_by_taste(events, artists):
    """LLM выбирает до 3 самых интересных концертов из уже найденных, близких вкусу пользователя."""
    names = [e.get("_artist", "") for e in events if e.get("_artist")]
    if not names:
        return events[:3]
    anchors = ", ".join(artists[:25])
    try:
        picked = await ai.allm_json(
            "Ты — музыкальный куратор. Дан список артистов из афиши города и вкус пользователя.\n"
            f"Вкус пользователя (любимые артисты): {anchors}.\n"
            f"Афиша (артисты в городе): {', '.join(names[:30])}.\n"
            "Выбери до 3 самых интересных пользователю имён из афиши (ближе по жанру/стилю к его вкусу).\n"
            'JSON: {"picks": ["имя 1", "имя 2", "имя 3"]}',
            300, tier="cheap")
    except Exception:
        return events[:3]
    picks = [str(p).lower() for p in (picked or {}).get("picks", [])] if isinstance(picked, dict) else []
    if not picks:
        return events[:3]
    by_name = {}
    for e in events:
        by_name.setdefault(e.get("_artist", "").lower(), e)
    ranked = [by_name[p] for p in picks if p in by_name]
    return ranked[:3] if ranked else events[:3]


async def send_weekly_events(bot, cid):
    """Вс 10:00 — концерты артистов пользователя + кинопремьеры ближайших дней."""
    import requests
    from datetime import datetime, timedelta
    from util import _MONTHS

    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    flag = util.flag_from_cc(cc) or "🏳"
    def _country_place():
        country = (s.get("country") or "").strip()
        low = country.lower()
        if cc == "NL" or low in ("нидерланды", "netherlands", "the netherlands"):
            return "Нидерландах"
        return country or {"BE": "Бельгии", "DE": "Германии", "FR": "Франции",
                           "GB": "Великобритании", "US": "США"}.get(cc, "твоей стране")

    cname = _country_place()
    now = datetime.now(config.TZ)
    today_str = now.strftime("%Y-%m-%d")
    date_to_str = (now + timedelta(days=7)).strftime("%Y-%m-%d")

    def _fmt_date(ds):
        try:
            y, m, dd = ds.split("-")
            return f"{int(dd)} {_MONTHS[int(m)-1]} {y}"
        except Exception:
            return ds

    def _movie_title_ok(title):
        if not title:
            return False
        # TMDB can return local titles in non-RU/EN scripts for region premieres.
        if not re.search(r"[A-Za-zА-Яа-яЁё]", title):
            return False
        return not re.search(r"[^A-Za-zА-Яа-яЁё0-9\s.,:;!?()«»\"'–—-]", title)

    def _movie_genre(m):
        gids = m.get("genre_ids") or []
        if 16 in gids:
            return "Мультфильм"
        for gid in gids:
            name = _TMDB_GENRES.get(gid)
            if name:
                return name.capitalize()
        return "Премьера"

    lines = [
        "🎵 <b>События следующей недели</b>",
        "",
        f"Вот что я нашёл для тебя на ближайшие 7 дней в <b>{esc(cname)}</b>:",
        "",
    ]

    # --- Концерты ---
    # Читаем недельный кэш (обновлён job'ом refresh_concerts_cache перед этой рассылкой),
    # чтобы не делать живой запрос к Ticketmaster по всем артистам прямо в момент отправки.
    concert_lines = []
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
                details = []
                if venue_str:
                    details.append(esc(venue_str))
                if date_str:
                    details.append(_fmt_date(date_str))
                suffix = f" ({', '.join(details)})" if details else ""
                concert_lines.append(f"• {esc(artist)}{suffix}")

    if concert_lines:
        lines += ["<b>Концерты твоих исполнителей:</b>"]
        lines += concert_lines
        lines.append("")

    # --- Кинопремьеры ---
    movie_lines = []
    if config.TMDB_API_KEY:
        try:
            results = await asyncio.to_thread(_tmdb_upcoming, cc)
            upcoming = [m for m in results
                        if today_str <= m.get("release_date", "") <= date_to_str
                        and _movie_title_ok(m.get("title", ""))]
            upcoming.sort(key=lambda m: m.get("release_date", ""))
            for m in upcoming[:5]:
                title = m.get("title", "")
                genre = _movie_genre(m)
                date = _fmt_date(m.get("release_date", ""))
                details = ", ".join(x for x in [genre, date] if x)
                movie_lines.append(f"• {esc(title)} ({esc(details)})")
        except Exception:
            pass

    if movie_lines:
        lines += ["<b>Новые премьеры в кино:</b>"]
        lines += movie_lines
        lines.append("")

    lines.append("Хорошей недели 😉")

    if not concert_lines and not movie_lines:
        lines = ["🎵 <b>События следующей недели</b>", "",
                 f"Для {esc(cname)} ничего не нашёл на ближайшие дни."]

    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def concert_pick_country(bot, cid):
    countries = [
        ("at", "Австрия", "🇦🇹 Австрия"),
        ("be", "Бельгия", "🇧🇪 Бельгия"),
        ("gb", "Великобритания", "🇬🇧 Великобр."),
        ("de", "Германия", "🇩🇪 Германия"),
        ("dk", "Дания", "🇩🇰 Дания"),
        ("es", "Испания", "🇪🇸 Испания"),
        ("it", "Италия", "🇮🇹 Италия"),
        ("nl", "Нидерланды", "🇳🇱 Нидерланды"),
        ("pl", "Польша", "🇵🇱 Польша"),
        ("pt", "Португалия", "🇵🇹 Португалия"),
        ("fr", "Франция", "🇫🇷 Франция"),
        ("ch", "Швейцария", "🇨🇭 Швейцария"),
        ("se", "Швеция", "🇸🇪 Швеция"),
    ]
    buttons = [
        InlineKeyboardButton(label, callback_data=f"a_concerts_{cc}")
        for cc, _name, label in sorted(countries, key=lambda x: x[1])
    ]
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")])
    await bot.send_message(chat_id=cid, text="🌍 Выбери страну для поиска концертов:",
                           reply_markup=InlineKeyboardMarkup(rows))


# ===== ПУТЕШЕСТВИЯ (travel.py) =====

def _plan_countries(cid):
    """Страны из уже сохранённых планов поездок (вкладка «Планы»)."""
    notes = store.get_list(config.NOTES_KEY, cid)
    return [n.get("country", "") for n in notes
            if isinstance(n, dict) and n.get("bucket") == "plan" and n.get("country")]

def travel_suggest_one(cid):
    visited = store.get_list(config.COUNTRIES_KEY, cid)
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    fav_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in favs]
    disliked = store.get_list(config.TRAVEL_DISLIKE_KEY, cid)
    plans = _plan_countries(cid)
    skip = ", ".join([str(x) for x in visited] + fav_names + [str(x) for x in disliked] + plans)
    prompt = f"""Уже был / в закладках / не интересно (СТРОГО НЕ предлагай ничего из этого списка): {skip}.
Профиль: любит интеллектуальную атмосферу, города с характером, природу; путешествия важнее вещей.
Предложи РОВНО 1 НОВУЮ страну, которой ТОЧНО НЕТ в списке выше. Перепроверь, что её нет в списке. Компактно. Верни JSON:
{{"flag":"эмодзи флага","country":"страна",
 "about":"1-2 строки образно о стране",
 "for_what":"ради чего ехать, 1 строка",
 "langs":"язык(и) + говорят ли на английском",
 "note":"главный нюанс/предупреждение, 1 строка"}}"""
    return ai.llm_json(prompt, 700, tier="leisure")

def _country_card(d):
    return leisure_ui.country_card(d)

def _travel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧳 Собрать план поездки", callback_data="a_trav_plan")],
        [InlineKeyboardButton("❌ Пропустить", callback_data="a_trav_no")],
        [InlineKeyboardButton("🎚️ Настройки стран", callback_data="set_countries")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")],
    ])

async def send_go(bot, cid):
    visited = store.get_list(config.COUNTRIES_KEY, cid)
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    fav_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in favs]
    disliked = store.get_list(config.TRAVEL_DISLIKE_KEY, cid)
    plans = _plan_countries(cid)
    skip_set = {str(x).strip().lower() for x in (list(visited) + fav_names + list(disliked) + plans) if str(x).strip()}
    d = None
    try:
        for _ in range(3):
            cand = travel_suggest_one(cid)
            cname = (cand.get("country") or "").strip().lower()
            if cname and cname not in skip_set:
                d = cand
                break
        if d is None:
            d = cand
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    facts = await asyncio.to_thread(research.country_facts, d.get("country", ""))
    if facts.get("cc"):
        d["flag"] = util.flag_from_cc(facts["cc"]) or d.get("flag", "")
    if facts.get("languages"):
        d["langs"] = ", ".join(facts["languages"][:3])
    rfact = await asyncio.to_thread(research.wiki_fact, d.get("country", ""))
    if rfact:
        d["fact"] = rfact
    if not research.grounded(facts):
        print("[research] travel: no grounding for", d.get("country", ""))
    country_msg = _country_card(d)
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", country_msg.text)
    store.last_source[str(cid)] = "Путешествия"
    store.suggested_countries[str(cid)] = d.get("country", "")
    store.last_recipe[str(cid)] = d
    await bot.send_message(chat_id=cid, text=country_msg.text, entities=country_msg.entities, reply_markup=_travel_kb())

async def travel_dislike(bot, cid):
    c = store.suggested_countries.get(str(cid))
    if c:
        store.add_to_list(config.TRAVEL_DISLIKE_KEY, cid, c)
    await send_go(bot, cid)

async def travel_fav(bot, cid):
    """Сохранить предложенную страну в закладки и сразу показать следующую."""
    c = store.suggested_countries.get(str(cid))
    if c:
        d = store.last_recipe.get(str(cid)) or {}
        flag = d.get("flag") or country_flag(c)
        favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
        favs.append({"name": c, "flag": flag})
        store.set_list(config.FAVCOUNTRIES_KEY, cid, favs)
        await bot.send_message(chat_id=cid, text=f"❤️ В любимых (Мои страны): {flag} {c}")
    await send_go(bot, cid)

async def send_plan(bot, cid):
    """Подробный план поездки по текущей предложенной стране."""
    d = store.last_recipe.get(str(cid)) or {}
    country = d.get("country") or store.suggested_countries.get(str(cid), "")
    if not country:
        await bot.send_message(chat_id=cid, text="Сначала выбери страну в Путешествиях."); return
    s = store.get_settings(cid)
    home = s.get("city", "дом")
    visited = store.get_list(config.COUNTRIES_KEY, cid)
    favs = store.get_list(config.FAVCOUNTRIES_KEY, cid)
    fav_names = [f.get("name", "") if isinstance(f, dict) else str(f) for f in favs]
    disliked = store.get_list(config.TRAVEL_DISLIKE_KEY, cid)
    skip = ", ".join([str(x) for x in visited] + fav_names + [str(x) for x in disliked] + [country])
    facts = await asyncio.to_thread(research.country_facts, country)
    fblock = research.facts_block(facts)
    rfact = await asyncio.to_thread(research.wiki_fact, country)
    if not research.grounded(facts):
        _log.warning("[research] travel-plan: no grounding for %s", country)
    ground_line = (f"Проверенные факты (ИСТОЧНИК ИСТИНЫ для столицы/языка/региона/валюты, "
                   f"не противоречь им): {fblock}.\n" if fblock else "")
    web_data = await asyncio.to_thread(
        research.tavily_snippet,
        f"{country} туризм путешествие достопримечательности 2025",
        900,
    )
    web_line = (f"Актуальная туристическая информация из сети (используй как дополнение к фактам):\n{web_data}\n"
                if web_data else "")
    prompt = f"""Подробный план поездки в страну/направление: {country}. Вылет из: {home}.
{ground_line}{web_line}Профиль: ценит атмосферу, природу, города с характером; путешествия важнее вещей.
Бюджет и сроки — это ОРИЕНТИР/оценка (так и помечай), фактические данные бери из проверенных фактов, не выдумывай.
Дай JSON (компактно, по делу, на русском):
{{"flag":"эмодзи","title":"страна/регион","about":"1-2 строки",
 "why":["3 пункта почему подойдёт"],
 "best_time":"лучшее время + темп. диапазон (ориентир), 1-2 строки",
 "budget":["перелёт туда-обратно ориентир из {home}","эконом в день","комфорт в день"],
 "spots":["3 места не пропустить с короткой пометкой"],
 "lgbt":"1 строка про дружелюбность/безопасность",
 "fact":"1 интересный местный факт"}}"""
    try:
        p = await ai.allm_json(prompt, 1100, tier="leisure", module="travel")
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    if facts.get("cc"):
        p["flag"] = util.flag_from_cc(facts["cc"]) or p.get("flag", "")
    if rfact:
        p["fact"] = rfact
    msg = leisure_ui.travel_plan(p, country)
    store.last_answer[str(cid)] = msg.text
    store.last_source[str(cid)] = "Путешествия · План"
    store.last_recipe[str(cid)] = {
        **(store.last_recipe.get(str(cid)) or {}),
        "plan_text": msg.text, "plan_entities": util.entities_to_json(msg.entities),
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Пропустить", callback_data="a_trav_no")],
        [InlineKeyboardButton("💾 Сохранить план поездки", callback_data="a_trav_save")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_leisure")],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)

async def save_plan(bot, cid):
    from datetime import datetime
    d = store.last_recipe.get(str(cid)) or {}
    plan = d.get("plan_text", "")
    country = d.get("country") or store.suggested_countries.get(str(cid), "план")
    if not plan:
        await bot.send_message(chat_id=cid, text="Сначала собери план поездки."); return
    store.add_to_list(config.NOTES_KEY, cid, {
        "date": datetime.now(config.TZ).strftime("%d.%m"),
        "text": plan, "entities": d.get("plan_entities", []),
        "source": "План поездки", "bucket": "plan", "country": country,
    })
    await bot.send_message(chat_id=cid, text=f"💾 План поездки ({country}) сохранён в «Мои сохранения» → «Планы».")
    await send_go(bot, cid)
