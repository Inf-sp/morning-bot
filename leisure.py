from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import re
import random
import config
import store
import ai
import util
import research
from util import country_flag, esc
import verify

# ===== КОНТЕНТ (content.py) =====

def _ensure_books(cid):
    """Возвращает 'Мои книги'; если пусто - подгружает из content.json (секция books)."""
    books = store.get_list(config.BOOKS_KEY, cid)
    if books:
        return books
    try:
        import json
        with open("content.json", encoding="utf-8") as f:
            data = json.load(f)
        seed = list(data.get("books", []))
        if seed:
            store.set_list(config.BOOKS_KEY, cid, seed)
            return seed
    except Exception:
        pass
    return books

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

def content_recommend(kind, cid):
    if kind == "movie":
        seen = store.get_list(config.WATCHLIST_KEY, cid)
        black = store.get_list(config.MOVIE_BLACKLIST_KEY, cid)
        what = "фильмов или сериалов"
        seen_titles = [s if isinstance(s, str) else str(s) for s in seen]
        black_titles = [s if isinstance(s, str) else str(s) for s in black]
        skip = seen_titles + black_titles
        avoid = ("\nНЕ рекомендуй то, что уже отмечено или не понравилось: " + ", ".join(skip[:80])) if skip else ""
        anchors = ", ".join(seen_titles[:25]) if seen_titles else "Breaking Bad, Euphoria, Parasite, Call Me by Your Name"
        prompt = f"""{config.CONTENT_TASTE}

Его уже отмеченные работы (референсы вкуса): {anchors}

Порекомендуй РОВНО 5 {what}, максимально точно под этот профиль вкуса.
Обязательно дай СМЕСЬ: и фильмы, и сериалы - минимум 2 сериала из 5.{avoid}
JSON: {{"items": [{{"title": "название (год)", "title_en": "оригинальное/английское название", "hook": "1 строка: на что похоже из его референсов и чем зацепит"}}]}}"""
        return ai.llm_json(prompt, 1000)

    # книги: референсы вкуса берём из "Мои книги" (настройки/БД, авто-загрузка из content.json)
    my_books = _ensure_books(cid)
    my_books_titles = [b if isinstance(b, str) else str(b) for b in my_books]
    read_seen = store.get_list(config.READLIST_KEY, cid)
    black = store.get_list(config.BOOK_BLACKLIST_KEY, cid)
    read_titles = [s if isinstance(s, str) else str(s) for s in read_seen]
    black_titles = [s if isinstance(s, str) else str(s) for s in black]
    refs = my_books_titles
    anchors = ", ".join(refs[:25])
    skip = my_books_titles + read_titles + black_titles
    avoid = ("\nНЕ рекомендуй уже прочитанное/в закладках/отклонённое: " + ", ".join(skip[:80])) if skip else ""
    prompt = f"""{config.CONTENT_TASTE}

Любимые книги пользователя (референсы книжного вкуса, из его настроек): {anchors}

Ты опытный книжный критик. Порекомендуй РОВНО 5 действительно сильных КНИГ под этот вкус (без проходных).
Сравнивай ТОЛЬКО с книгами из его списка выше, не с фильмами/сериалами.{avoid}
JSON: {{"items": [{{"title": "название", "title_en": "оригинальное название", "year": "год",
 "author": "автор", "desc": "1-2 строки общего описания/жанра",
 "why": ["2 пункта почему зайдёт, со ссылкой на конкретные книги из его списка"],
 "plot": "коротко о сюжете, 2-3 предложения без жёстких спойлеров финала",
 "quote": "короткая цитата из книги",
 "hook": "1 строка: если понравились такие-то его книги - эта зайдёт тем-то"}}]}}"""
    return ai.llm_json(prompt, 1300)

_TMDB_GENRES = {28:"боевик",12:"приключения",16:"анимация",35:"комедия",80:"криминал",99:"документальный",
    18:"драма",10751:"семейный",14:"фэнтези",36:"история",27:"ужасы",10402:"музыка",9648:"детектив",
    10749:"мелодрама",878:"фантастика",10770:"телефильм",53:"триллер",10752:"военный",37:"вестерн",
    10759:"боевик",10762:"детское",10763:"новости",10764:"реалити",10765:"фантастика",10766:"мыло",
    10767:"ток-шоу",10768:"военное"}

def _tmdb_lookup(title, title_en=""):
    if not config.TMDB_API_KEY:
        return None
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
            return {"name": x.get("title") or x.get("name") or q,
                    "name_en": x.get("original_title") or x.get("original_name") or "",
                    "year": date[:4] if date else "", "rating": x.get("vote_average") or 0,
                    "genres": genres, "kind": kind,
                    "poster": (f"https://image.tmdb.org/t/p/w500{poster}" if poster else None),
                    "url": f"https://www.themoviedb.org/{kind}/{x.get('id')}",
                    "overview": overview}
        except Exception:
            continue
    return None

def _display_title(it, tm):
    """Название, которое реально показано пользователю (TMDb если есть, иначе от LLM)."""
    name = (tm.get("name") if tm else "") or it.get("title", "")
    year = (tm.get("year") if tm else "") or ""
    return f"{name} ({year})" if year else name

_BAD_TMDB = ("making of", "behind the scenes", "bonus", "featurette",
             "the making", "deleted scenes", "trailer", "teaser")

def _clip(text, limit=450):
    """Аккуратно обрезает описание по концу предложения/слова, без обрыва на полуслове."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= int(limit * 0.5):
        return cut[:end + 1].strip()
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip(" ,.;:—-") + "…"

def _movie_card(it, tm):
    title = (tm["name"] if tm else it.get("title", ""))
    year = f" ({tm['year']})" if tm and tm["year"] else ""
    kind = (tm.get("kind") if tm else "") or ""
    icon = "📺" if kind == "tv" else "🎬"
    type_label = "Сериал" if kind == "tv" else ("Фильм" if kind == "movie" else "")
    cap = [f"{icon} <b>{esc(title)}{year}</b>"]
    en = (tm.get("name_en") if tm else "") or it.get("title_en", "")
    if en and en.lower() != title.lower():
        cap.append(f"<i>{esc(en)}</i>")
    genre_bits = " · ".join(x for x in [type_label, (tm.get("genres") if tm else "")] if x)
    if genre_bits:
        cap.append("")
        cap.append(f"🎭 {esc(genre_bits)}")
    if tm and tm["rating"]:
        cap.append(f"⭐ {tm['rating']:.1f}/10 TMDb")
    if tm and tm.get("overview"):
        cap.append("")
        cap.append(esc(_clip(tm["overview"])))
    cap.append("")
    cap.append(f"💡 {esc(it.get('hook', ''))}")
    if tm and tm.get("url"):
        cap.append("")
        cap.append(f"🔗 {tm['url']}")
    return title, "\n".join(cap)

def _movie_kb(i):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("😕 Не нравится", callback_data=f"movie_no_{i}")],
        [InlineKeyboardButton("❤️ В любимые", callback_data=f"movie_love_{i}"),
         InlineKeyboardButton("⭐ В закладки", callback_data=f"reco_{i}"),
         InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

MIN_TMDB_RATING = 7.0

def _movie_used(cid):
    """Множество названий, которые нельзя повторять: закладки+любимые (watchlist) и чёрный список."""
    wl = store.get_list(config.WATCHLIST_KEY, cid)
    bl = store.get_list(config.MOVIE_BLACKLIST_KEY, cid)
    used = set()
    for x in list(wl) + list(bl):
        used.add((x if isinstance(x, str) else str(x)).lower())
    return used

def _pick_good_movie(items, used_titles):
    """Возвращает (item, tm) для первого фильма с рейтингом >= порога и не из used_titles.
    Если подходящих нет - первый доступный."""
    used = {str(u).lower() for u in used_titles}
    fallback = None
    for it in items:
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
    if tm == "__lookup__":
        tm = _tmdb_lookup(it.get("title", ""), it.get("title_en", "")) if config.TMDB_API_KEY else None
    title, text = _movie_card(it, tm)
    kb = _movie_kb(i)
    if tm and tm.get("poster"):
        try:
            await bot.send_photo(chat_id=cid, photo=tm["poster"], caption=text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)

async def send_recos(bot, cid, kind):
    if kind == "book":
        await send_books_reco(bot, cid)
        return
    await bot.send_message(chat_id=cid, text="Подбираю под твой вкус...")
    items = []
    for _ in range(2):
        try:
            data = content_recommend(kind, str(cid))
            items = data.get("items", []) if isinstance(data, dict) else []
        except Exception:
            items = []
        if items:
            break
    if not items:
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    it, tm = _pick_good_movie(items, _movie_used(cid))
    if not it:
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    disp = _display_title(it, tm)
    store.last_recos[str(cid)] = {"kind": kind, "items": [disp]}
    store.last_source[str(cid)] = "Досуг · Фильмы и сериалы"
    store.last_answer[str(cid)] = f"{disp} - {it.get('hook','')}"
    await _send_movie_card(bot, cid, it, 0, tm=tm)

async def movie_dislike(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        store.add_to_list(config.MOVIE_BLACKLIST_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"Понял, больше не буду рекомендовать «{title}». Вот другой вариант 👇")
    try:
        data = content_recommend("movie", str(cid))
        items = data.get("items", [])
    except Exception:
        items = []
    if not items:
        return
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    used = _movie_used(cid) | {str(x).lower() for x in rec["items"]}
    it, tm = _pick_good_movie(items, used)
    if not it:
        return
    rec["items"].append(_display_title(it, tm))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_movie_card(bot, cid, it, ni, tm=tm)

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
    author = esc(it.get("author", ""))
    title = esc(it.get("title", ""))
    en = esc(it.get("title_en", ""))
    year = esc(str(it.get("year", "")))
    head_meta = ", ".join(x for x in [en, year] if x)
    head = f"{author} • «{title}»" if author else f"«{title}»"
    if head_meta:
        head += f" <i>({head_meta})</i>"
    L = [head]
    if it.get("desc"):
        L += ["", esc(it["desc"])]
    why = it.get("why") or []
    if isinstance(why, list) and why:
        L += ["", "🎯 <b>Почему она тебе точно зайдёт:</b>"] + [f"• {esc(str(w))}" for w in why]
    if it.get("plot"):
        L += ["", f"✍🏻 <b>Коротко о сюжете:</b> {esc(it['plot'])}"]
    if it.get("quote"):
        L += ["", f"💬 <b>Цитата:</b> «{esc(it['quote'])}»"]
    if it.get("hook"):
        L += ["", f"💡 {esc(it['hook'])}"]
    return "\n".join(L)

def _book_kb(i):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("😕 Не нравится", callback_data=f"book_no_{i}")],
        [InlineKeyboardButton("❤️ В любимые", callback_data=f"book_love_{i}"),
         InlineKeyboardButton("⭐ В закладки", callback_data=f"reco_{i}"),
         InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

async def _send_book_card(bot, cid, it, i):
    text = _book_text(it)
    kb = _book_kb(i)
    cover = _book_cover(it.get("title", ""), it.get("title_en", ""))
    if cover:
        try:
            await bot.send_photo(chat_id=cid, photo=cover, caption=text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)

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
    """Названия книг, которые нельзя повторять: любимые (Мои книги) + закладки + отклонённые."""
    used = set()
    for key in (config.BOOKS_KEY, config.READLIST_KEY, config.BOOK_BLACKLIST_KEY):
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
    await bot.send_message(chat_id=cid, text="Подбираю книги под твой вкус...")
    items = []
    for _ in range(2):
        try:
            data = content_recommend("book", str(cid))
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
        data = content_recommend("book", str(cid))
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
    """Фильм/сериал - в любимые (watchlist), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.WATCHLIST_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"❤️ «{title}» - в любимые (Фильмы и сериалы). Вот ещё вариант 👇")
    try:
        data = content_recommend("movie", str(cid))
        items = data.get("items", [])
    except Exception:
        items = []
    if not items:
        return
    rec = store.last_recos.get(str(cid), {"kind": "movie", "items": []})
    used = _movie_used(cid) | {str(x).lower() for x in rec["items"]}
    it, tm = _pick_good_movie(items, used)
    if not it:
        return
    rec["items"].append(_display_title(it, tm))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_movie_card(bot, cid, it, ni, tm=tm)

async def book_love(bot, cid, i):
    """Книга - в любимые (Мои книги), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        _add_unique(config.BOOKS_KEY, cid, title)
        await bot.send_message(chat_id=cid, text=f"❤️ «{title}» - в любимые (Мои книги). Вот ещё вариант 👇")
    try:
        data = content_recommend("book", str(cid))
        items = data.get("items", [])
    except Exception:
        items = []
    rec = store.last_recos.get(str(cid), {"kind": "book", "items": []})
    it = _pick_good_book(items, cid, extra_skip=rec.get("items", []))
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_book_card(bot, cid, it, ni)

async def listen_love(bot, cid):
    """Артист - в любимые (Мои артисты), затем следующая рекомендация."""
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        artist = rec["items"][0]
        _add_unique(config.ARTISTS_KEY, cid, artist)
        await bot.send_message(chat_id=cid, text=f"❤️ «{artist}» - в любимые (Мои артисты). Вот ещё вариант 👇")
    await send_listen(bot, cid)

async def add_reco(bot, cid, i):
    from datetime import datetime
    rec = store.last_recos.get(str(cid))
    if not (rec and i < len(rec["items"])):
        return
    title = rec["items"][i]
    kind = rec["kind"]
    key = config.WATCHLIST_KEY if kind == "movie" else config.READLIST_KEY
    folder = "Фильмы и сериалы" if kind == "movie" else "Книги"
    _add_unique(key, cid, title)
    if not _note_fav_exists(cid, title):
        store.add_to_list(config.NOTES_KEY, cid,
                          {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": title, "source": folder, "bucket": "fav"})
    await bot.send_message(chat_id=cid, text=f"⭐ В закладках «{folder}»: {title}. Вот ещё вариант 👇")
    try:
        data = content_recommend(kind, str(cid))
        items = data.get("items", [])
    except Exception:
        items = []
    if not items:
        return
    if kind == "movie":
        it, tm = _pick_good_movie(items, set(rec["items"]))
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
        rows.append([InlineKeyboardButton("🧹 Чистка списка", callback_data="a_watchclean")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
    await bot.send_message(chat_id=cid,
        text="🍿 Посмотреть:\n" + ("\n".join(f"• {_list_text(x)}" for x in lst) if lst else "пусто"),
        reply_markup=InlineKeyboardMarkup(rows))

async def send_readlist(bot, cid):
    lst = store.get_list(config.READLIST_KEY, cid)
    rows = []
    if lst:
        rows.append([InlineKeyboardButton("🧹 Чистка списка", callback_data="a_readclean")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
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
        [InlineKeyboardButton("😕 Не нравится", callback_data="a_listen_no")],
        [InlineKeyboardButton("❤️ В любимые", callback_data="listen_love"),
         InlineKeyboardButton("⭐ В закладки", callback_data="listen_0"),
         InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

async def listen_dislike(bot, cid):
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        store.add_to_list(config.MUSIC_DISLIKE_KEY, cid, rec["items"][0])
    await send_listen(bot, cid)

async def send_listen(bot, cid):
    arts = _ensure_artists(cid)
    anchors = ", ".join(arts[:25]) if arts else "Charli xcx, The xx, Fever Ray, RÜFÜS DU SOL, PLACEBO"
    disliked = store.get_list(config.MUSIC_DISLIKE_KEY, cid)
    notes = store.get_list(config.NOTES_KEY, cid)
    booked = [n.get("text", "") for n in notes
              if isinstance(n, dict) and "музык" in str(n.get("source", "")).lower()]
    known = set(a.lower() for a in arts) | set(b.lower() for b in booked) | set(str(d).lower() for d in disliked)
    avoid_all = ", ".join(list(arts) + booked + [str(d) for d in disliked])[:600]
    await bot.send_message(chat_id=cid, text="Подбираю исполнителя под твой вкус...")
    data = None
    for _ in range(3):
        try:
            cand = ai.llm_json(
                f"Любимые исполнители пользователя (его вкус): {anchors}.\n"
                f"НЕ предлагай никого из этого списка (уже в закладках/любимых/отклонены): {avoid_all}.\n"
                "Предложи РОВНО ОДНОГО НОВОГО исполнителя, максимально близкого по вкусу "
                "(электроника, синтипоп, альт, дрим-поп, дарквейв, арт-поп и близкое).\n"
                "Верни строго такой JSON:\n"
                '{"artist": "имя исполнителя", '
                '"desc": "1-2 строки образно о звучании", '
                '"why": ["пункт 1 - на кого из его любимых похоже и чем", "пункт 2"], '
                '"tracks": ["трек 1 - короткая пометка", "трек 2", "трек 3"], '
                '"fact": "1 интересный факт об исполнителе"}',
                1000)
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
    L = [f"🎸 <b>{esc(artist)}</b>"]
    if data.get("desc"):
        L += ["", esc(data["desc"])]
    why = data.get("why") or []
    if isinstance(why, list) and why:
        L += ["", "🎯 <b>Почему тебе зайдёт:</b>"] + [f"• {esc(str(w))}" for w in why]
    tracks = data.get("tracks") or []
    if isinstance(tracks, list) and tracks:
        L += ["", "🎧 <b>С чего начать:</b>"] + [f"• {esc(str(t))}" for t in tracks]
    if data.get("fact"):
        L += ["", "💡 <b>Факт:</b>", esc(data["fact"])]
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", "\n".join(L))
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=_listen_kb())

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
    """Возвращает список артистов; если пуст - подгружает дефолтный из artists.json."""
    arts = store.get_list(config.ARTISTS_KEY, cid)
    if arts:
        return arts
    try:
        import json
        with open("artists.json", encoding="utf-8") as f:
            seed = json.load(f)
        if seed:
            store.set_list(config.ARTISTS_KEY, cid, seed)
            return seed
    except Exception:
        pass
    return arts

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
    CC_MAP = {"be": ("BE", "🇧🇪", "Бельгия"), "de": ("DE", "🇩🇪", "Германия"),
              "fr": ("FR", "🇫🇷", "Франция"), "gb": ("GB", "🇬🇧", "Великобритания"),
              "es": ("ES", "🇪🇸", "Испания"), "it": ("IT", "🇮🇹", "Италия"),
              "at": ("AT", "🇦🇹", "Австрия"), "ch": ("CH", "🇨🇭", "Швейцария"),
              "pl": ("PL", "🇵🇱", "Польша"), "se": ("SE", "🇸🇪", "Швеция"),
              "dk": ("DK", "🇩🇰", "Дания"), "pt": ("PT", "🇵🇹", "Португалия")}
    if mode in CC_MAP:
        cc, flag, cname = CC_MAP[mode]
    else:
        cc, flag, cname = home_cc, home_flag, home_name

    await bot.send_message(chat_id=cid, text=f"Ищу мероприятия в {cname}, ~15-30 сек...")
    import requests
    from util import _MONTHS
    found = {}
    seen_pairs = set()
    TRIBUTE = ("tribute", "cover", "covers", "candlelight", "songs of", "the music of",
               "performed by", "celebrating", "by candle", "symphonic", "reimagined",
               "someone like", "a tribute", "in the style of", "plays the music", "experience:")
    for a in artists[:40]:
        try:
            r = requests.get("https://app.ticketmaster.com/discovery/v2/events.json",
                params={"apikey": config.TICKETMASTER_API_KEY, "keyword": a, "countryCode": cc,
                        "classificationName": "music", "size": 3, "sort": "date,asc"}, timeout=15)
            for e in r.json().get("_embedded", {}).get("events", []):
                al = a.lower()
                name_l = e.get("name", "").lower()
                attractions = [att.get("name", "").lower()
                               for att in (e.get("_embedded", {}).get("attractions") or [])]
                attr_match = any(al in nm or nm in al for nm in attractions)
                if any(k in name_l for k in TRIBUTE):
                    continue
                if not (al in name_l or attr_match):
                    continue
                date = e.get("dates", {}).get("start", {}).get("localDate", "")
                pair = (al, date)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                e["_artist"] = a
                found[e.get("id")] = e
        except Exception:
            continue

    rows = [[InlineKeyboardButton("🌍 Сменить страну", callback_data="a_concerts_pick")]]
    kb = InlineKeyboardMarkup(rows)

    if not found:
        store.last_answer[str(cid)] = f"Мероприятия в {cname}: ничего не нашёл."
        await bot.send_message(chat_id=cid,
            text=f"🎤 <b>Концерты в {esc(cname)}</b>\n\nСейчас ничего не нашёл. Попробуй другую страну 🌍",
            parse_mode="HTML", reply_markup=kb)
        return

    def _fmt_date(ds):
        try:
            y, m, dd = ds.split("-")
            return f"{int(dd)} {_MONTHS[int(m)-1]} {y}"
        except Exception:
            return ds

    events = sorted(found.values(), key=lambda e: e.get("dates", {}).get("start", {}).get("localDate", "9999"))
    lines = [f"🎤 <b>Концерты в {esc(cname)}</b>", ""]
    for e in events[:20]:
        artist = e.get("_artist", "")
        name = e.get("name", "")
        date = e.get("dates", {}).get("start", {}).get("localDate", "")
        ven = (e.get("_embedded", {}).get("venues") or [{}])[0]
        vn = ven.get("name", "")
        city = (ven.get("city") or {}).get("name", "")
        url = e.get("url", "")
        lines.append(f"<b>{esc(artist)}</b>")
        if name and name.lower() != artist.lower():
            lines.append(esc(name))
        if vn or city:
            lines.append(f"📍 {flag} {esc(vn)}{', ' + esc(city) if city else ''}")
        if date:
            lines.append(f"📅 {_fmt_date(date)}")
        if url:
            lines.append(f"🎟 {url}")
        lines.append("")
    txt = "\n".join(lines)
    store.last_source[str(cid)] = "Досуг · Концерты"
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", txt)
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)

async def send_weekly_events(bot, cid):
    """Пн 09:00 — Досуг на неделю: фильм + музыка."""
    await bot.send_message(chat_id=cid, text="🎬 <b>Досуг на эту неделю</b>", parse_mode="HTML")
    await send_recos(bot, cid, "movie")
    await send_listen(bot, cid)


async def concert_pick_country(bot, cid):
    codes = [("be", "🇧🇪 Бельгия"), ("de", "🇩🇪 Германия"), ("fr", "🇫🇷 Франция"),
             ("gb", "🇬🇧 Великобр."), ("es", "🇪🇸 Испания"), ("it", "🇮🇹 Италия"),
             ("at", "🇦🇹 Австрия"), ("ch", "🇨🇭 Швейцария"), ("pl", "🇵🇱 Польша"),
             ("se", "🇸🇪 Швеция"), ("dk", "🇩🇰 Дания"), ("pt", "🇵🇹 Португалия")]
    rows = [[InlineKeyboardButton(lbl, callback_data=f"a_concerts_{cc}")] for cc, lbl in codes]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
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
    if not visited:
        visited = [c.strip() for c in config.VISITED.split(",") if c.strip()]
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
    return ai.llm_json(prompt, 700, tier="cheap")

def _country_card(d):
    L = [f"{d.get('flag','')} <b>{esc(d.get('country',''))}</b>", ""]
    if d.get("about"):
        L += [esc(d["about"]), ""]
    if d.get("for_what"):
        L += [f"🎯 <b>Ради чего ехать:</b> {esc(d['for_what'])}", ""]
    if d.get("langs"):
        L += [f"🗣️ <b>Язык:</b> {esc(d['langs'])}", ""]
    if d.get("note"):
        L += [f"⚠️ <b>Главный нюанс:</b> {esc(d['note'])}"]
    if d.get("fact"):
        L += ["", f"🔎 <b>Факт:</b> {esc(d['fact'])}"]
    return "\n".join(L).strip()

def _travel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧳 Собрать план поездки", callback_data="a_trav_plan")],
        [InlineKeyboardButton("😕 Не нравится", callback_data="a_trav_no")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

async def send_go(bot, cid):
    await bot.send_message(chat_id=cid, text="Подбираю страну...")
    visited = store.get_list(config.COUNTRIES_KEY, cid)
    if not visited:
        visited = [c.strip() for c in config.VISITED.split(",") if c.strip()]
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
    facts = research.country_facts(d.get("country", ""))
    if facts.get("cc"):
        d["flag"] = util.flag_from_cc(facts["cc"]) or d.get("flag", "")
    if facts.get("languages"):
        d["langs"] = ", ".join(facts["languages"][:3])
    rfact = research.wiki_fact(d.get("country", ""))
    if rfact:
        d["fact"] = rfact
    if not research.grounded(facts):
        print("[research] travel: no grounding for", d.get("country", ""))
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", _country_card(d))
    store.last_source[str(cid)] = "Путешествия"
    store.suggested_countries[str(cid)] = d.get("country", "")
    store.last_recipe[str(cid)] = d
    await bot.send_message(chat_id=cid, text=_country_card(d), parse_mode="HTML", reply_markup=_travel_kb())

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
    await bot.send_message(chat_id=cid, text="Собираю план поездки...")
    facts = research.country_facts(country)
    fblock = research.facts_block(facts)
    rfact = research.wiki_fact(country)
    if not research.grounded(facts):
        print("[research] travel-plan: no grounding for", country)
    ground_line = (f"Проверенные факты (ИСТОЧНИК ИСТИНЫ для столицы/языка/региона/валюты, "
                   f"не противоречь им): {fblock}.\n" if fblock else "")
    prompt = f"""Подробный план поездки в страну/направление: {country}. Вылет из: {home}.
{ground_line}Профиль: ценит атмосферу, природу, города с характером; путешествия важнее вещей.
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
        p = ai.llm_json(prompt, 1100)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    if facts.get("cc"):
        p["flag"] = util.flag_from_cc(facts["cc"]) or p.get("flag", "")
    if rfact:
        p["fact"] = rfact
    L = [f"{p.get('flag','')} <b>{esc(p.get('title', country))}</b>"]
    if p.get("about"):
        L += ["", esc(p["about"])]
    if p.get("why"):
        L += ["", "🎯 <b>Почему тебе подойдёт</b>"] + [f"• {esc(str(w))}" for w in p["why"]]
    if p.get("best_time"):
        L += ["", "📅 <b>Лучшее время</b>", esc(p["best_time"])]
    if p.get("budget"):
        L += ["", "💰 <b>Бюджет</b>"] + [f"• {esc(str(b))}" for b in p["budget"]]
    if p.get("spots"):
        L += ["", "📸 <b>Не пропусти</b>"] + [f"• {esc(str(sp))}" for sp in p["spots"]]
    if p.get("lgbt"):
        L += ["", "🏳️‍🌈 <b>LGBTQ+</b>", esc(p["lgbt"])]
    if p.get("fact"):
        L += ["", "🍲 <b>Интересный факт</b>", esc(p["fact"])]
    plan_text = "\n".join(L)
    store.last_answer[str(cid)] = re.sub(r"<[^>]+>", "", plan_text)
    store.last_source[str(cid)] = "Путешествия · План"
    store.last_recipe[str(cid)] = {**(store.last_recipe.get(str(cid)) or {}), "plan_text": plan_text}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("😕 Не нравится", callback_data="a_trav_no")],
        [InlineKeyboardButton("💾 Сохранить план поездки", callback_data="a_trav_save")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])
    await bot.send_message(chat_id=cid, text=plan_text, parse_mode="HTML", reply_markup=kb)

async def save_plan(bot, cid):
    from datetime import datetime
    d = store.last_recipe.get(str(cid)) or {}
    plan = d.get("plan_text", "")
    country = d.get("country") or store.suggested_countries.get(str(cid), "план")
    if not plan:
        await bot.send_message(chat_id=cid, text="Сначала собери план поездки."); return
    store.add_to_list(config.NOTES_KEY, cid, {
        "date": datetime.now(config.TZ).strftime("%d.%m"),
        "text": plan, "source": "План поездки", "bucket": "plan", "full": True,
        "country": country,
    })
    await bot.send_message(chat_id=cid, text=f"💾 План поездки ({country}) сохранён в «Мои сохранения» → «Планы».")
    await send_go(bot, cid)
