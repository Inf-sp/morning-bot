from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import re
import config
import store
import ai
from util import esc

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

Порекомендуй РОВНО 5 {what}, максимально точно под этот профиль вкуса.{avoid}
JSON: {{"items": [{{"title": "название (год)", "title_en": "оригинальное/английское название", "hook": "1 строка: на что похоже из его референсов и чем зацепит"}}]}}"""
        return ai.llm_json(prompt, 1000)

    # книги: референсы вкуса берём из "Мои книги" (настройки) + любимые из конфига
    my_books = store.get_list(config.BOOKS_KEY, cid)
    my_books_titles = [b if isinstance(b, str) else str(b) for b in my_books]
    read_seen = store.get_list(config.READLIST_KEY, cid)         # уже в закладках "почитать"
    black = store.get_list(config.BOOK_BLACKLIST_KEY, cid)       # отклонённые
    read_titles = [s if isinstance(s, str) else str(s) for s in read_seen]
    black_titles = [s if isinstance(s, str) else str(s) for s in black]
    refs = my_books_titles or [config.FAV_BOOKS]
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
 "hook": "1 строка: если понравились такие-то его книги - эта зацепит тем-то"}}]}}"""
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
            x = results[0]
            date = x.get("release_date") or x.get("first_air_date") or ""
            kind = "movie" if x.get("media_type") == "movie" else "tv"
            poster = x.get("poster_path")
            genres = ", ".join(_TMDB_GENRES.get(g, "") for g in (x.get("genre_ids") or [])[:3] if _TMDB_GENRES.get(g))
            overview = x.get("overview", "")
            # если русского описания нет - подтянем англ как запас
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
                    "genres": genres,
                    "poster": (f"https://image.tmdb.org/t/p/w500{poster}" if poster else None),
                    "url": f"https://www.themoviedb.org/{kind}/{x.get('id')}",
                    "overview": overview}
        except Exception:
            continue
    return None

def _movie_card(it, tm):
    title = (tm["name"] if tm else it.get("title", ""))
    year = f" ({tm['year']})" if tm and tm["year"] else ""
    cap = [f"🎬 <b>{esc(title)}{year}</b>"]
    en = (tm.get("name_en") if tm else "") or it.get("title_en", "")
    if en and en.lower() != title.lower():
        cap.append(f"<i>{esc(en)}</i>")
    if tm and tm.get("genres"):
        cap.append("")
        cap.append(f"🎭 {esc(tm['genres'])}")
    if tm and tm["rating"]:
        cap.append(f"⭐ {tm['rating']:.1f}/10 TMDb")
    if tm and tm.get("overview"):
        cap.append("")
        cap.append(esc(tm["overview"][:300]))
    cap.append("")
    cap.append(f"💡 {esc(it.get('hook', ''))}")
    if tm and tm.get("url"):
        cap.append("")
        cap.append(f"🔗 {tm['url']}")
    return title, "\n".join(cap)

def _movie_kb(i):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("😕 Не нравится", callback_data=f"movie_no_{i}")],
        [InlineKeyboardButton("⭐ Добавить в закладки", callback_data=f"reco_{i}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

MIN_TMDB_RATING = 7.0  # рекомендуем только фильмы/сериалы с оценкой выше этой

def _pick_good_movie(items, used_titles):
    """Возвращает (item, tm) для первого фильма с рейтингом >= порога и не из used_titles.
    Если таких нет - первый доступный (с tm если получилось)."""
    fallback = None
    for it in items:
        if it.get("title", "") in used_titles:
            continue
        tm = _tmdb_lookup(it.get("title", ""), it.get("title_en", "")) if config.TMDB_API_KEY else None
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
    try:
        data = content_recommend(kind, str(cid))
        items = data.get("items", [])
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    if not items:
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    it, tm = _pick_good_movie(items, set())
    if not it:
        await bot.send_message(chat_id=cid, text="Не удалось подобрать. Попробуй ещё раз."); return
    store.last_recos[str(cid)] = {"kind": kind, "items": [it.get("title", "")]}
    store.last_source[str(cid)] = "Досуг · Фильмы и сериалы"
    store.last_answer[str(cid)] = f"{it.get('title','')} - {it.get('hook','')}"
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
    it, tm = _pick_good_movie(items, set(rec["items"]))
    if not it:
        return
    rec["items"].append(it.get("title", ""))
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
        [InlineKeyboardButton("⭐ Добавить в закладки", callback_data=f"reco_{i}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
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

async def send_books_reco(bot, cid):
    await bot.send_message(chat_id=cid, text="Подбираю книги под твой вкус...")
    try:
        data = content_recommend("book", str(cid))
        items = data.get("items", [])
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}"); return
    if not items:
        await bot.send_message(chat_id=cid, text="Не удалось подобрать книгу. Попробуй ещё раз."); return
    it = items[0]
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
    if not items:
        return
    it = items[0]
    rec = store.last_recos.get(str(cid), {"kind": "book", "items": []})
    rec["items"].append(it.get("title", ""))
    store.last_recos[str(cid)] = rec
    ni = len(rec["items"]) - 1
    await _send_book_card(bot, cid, it, ni)

async def add_reco(bot, cid, i):
    from datetime import datetime
    rec = store.last_recos.get(str(cid))
    if not (rec and i < len(rec["items"])):
        return
    title = rec["items"][i]
    kind = rec["kind"]
    key = config.WATCHLIST_KEY if kind == "movie" else config.READLIST_KEY
    folder = "Фильмы и сериалы" if kind == "movie" else "Книги"
    store.add_to_list(key, cid, title)
    store.add_to_list(config.NOTES_KEY, cid,
                      {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": title, "source": folder})
    await bot.send_message(chat_id=cid, text=f"⭐ В закладках «{folder}»: {title}. Вот ещё вариант 👇")
    # сразу показываем следующую рекомендацию
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
        rec["items"].append(it.get("title", ""))
        store.last_recos[str(cid)] = rec
        ni = len(rec["items"]) - 1
        await _send_movie_card(bot, cid, it, ni, tm=tm)
    else:
        it = items[0]
        rec["items"].append(it.get("title", ""))
        store.last_recos[str(cid)] = rec
        ni = len(rec["items"]) - 1
        await _send_book_card(bot, cid, it, ni)

async def send_watchlist(bot, cid):
    lst = store.get_list(config.WATCHLIST_KEY, cid)
    await bot.send_message(chat_id=cid, text="🍿 Посмотреть:\n" + ("\n".join(f"• {x}" for x in lst) if lst else "пусто"))

async def send_readlist(bot, cid):
    lst = store.get_list(config.READLIST_KEY, cid)
    await bot.send_message(chat_id=cid, text="📚 Почитать:\n" + ("\n".join(f"• {x}" for x in lst) if lst else "пусто"))

async def send_fav(bot, cid):
    favs = store.get_list(config.FAVORITES_KEY, cid)
    store.pending_input[str(cid)] = "favorite"
    await bot.send_message(chat_id=cid,
        text="❤️ Любимое:\n" + ("\n".join(f"• {f}" for f in favs) if favs else "пусто") + "\n\nНапиши фильм/сериал/книгу - добавлю.")

async def add_fav(bot, cid, text):
    store.add_to_list(config.FAVORITES_KEY, cid, text)
    await bot.send_message(chat_id=cid, text="Добавил в любимое.")

def _listen_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("😕 Не нравится", callback_data="a_listen_no")],
        [InlineKeyboardButton("🔍 Поиск по концертам", callback_data="a_concerts_find")],
        [InlineKeyboardButton("⭐ Добавить в закладки", callback_data="listen_0")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")],
    ])

async def listen_dislike(bot, cid):
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and rec["items"]:
        store.add_to_list("music_dislike.json", cid, rec["items"][0])
    await send_listen(bot, cid)

async def send_listen(bot, cid):
    arts = _ensure_artists(cid)
    anchors = ", ".join(arts[:25]) if arts else "Charli xcx, The xx, Fever Ray, RÜFÜS DU SOL, PLACEBO"
    disliked = store.get_list("music_dislike.json", cid)
    avoid_artists = ", ".join(arts[:40])
    avoid_dis = ", ".join([str(x) for x in disliked][:40])
    await bot.send_message(chat_id=cid, text="Подбираю исполнителя под твой вкус...")
    try:
        data = ai.llm_json(
            f"Любимые исполнители пользователя (его вкус): {anchors}.\n"
            f"НЕ предлагай тех, кто уже в любимых: {avoid_artists}.\n"
            + (f"НЕ предлагай уже отклонённых: {avoid_dis}.\n" if avoid_dis else "")
            + "Предложи РОВНО ОДНОГО нового исполнителя, максимально близкого по вкусу "
            "(электроника, синтипоп, альт, дрим-поп, дарквейв, арт-поп и близкое).\n"
            'JSON: {"artist":"имя","desc":"1-2 строки образно о звучании",'
            '"why":["2 пункта: на кого из ЕГО любимых похоже и чем",'
            '"можно ссылаться на конкретных артистов из его списка"],'
            '"tracks":["3-4 лучших трека с короткой пометкой через тире"],'
            '"fact":"1 интересный факт об исполнителе"}', 1000)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}"); return
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
        store.add_to_list(config.NOTES_KEY, cid,
                          {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": title, "source": "Музыка"})
        await bot.send_message(chat_id=cid, text=f"⭐ В закладках «Музыка»: {title}")

async def send_artists(bot, cid):
    arts = store.get_list(config.ARTISTS_KEY, cid)
    txt = "🎤 Мои артисты:\n" + ("\n".join(f"• {a}" for a in arts) if arts else "пусто")
    await bot.send_message(chat_id=cid, text=txt)

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
            text="🔎 Поиск концертов требует бесплатный ключ Ticketmaster.\n"
                 "Заведи его на developer.ticketmaster.com и добавь на Railway переменную TICKETMASTER_API_KEY.")
        return
    artists = _ensure_artists(cid)
    if not artists:
        await bot.send_message(chat_id=cid, text="Не удалось загрузить артистов. Добавь их в настройках.")
        return
    import util
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

    await bot.send_message(chat_id=cid, text=f"Ищу концерты в {cname}, ~15-30 сек...")
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
    rows.append([InlineKeyboardButton("⬅️ Меню", callback_data="m_music")])
    kb = InlineKeyboardMarkup(rows)

    if not found:
        store.last_answer[str(cid)] = f"Концерты в {cname}: ничего не нашёл."
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

async def concert_pick_country(bot, cid):
    codes = [("be", "🇧🇪 Бельгия"), ("de", "🇩🇪 Германия"), ("fr", "🇫🇷 Франция"),
             ("gb", "🇬🇧 Великобр."), ("es", "🇪🇸 Испания"), ("it", "🇮🇹 Италия"),
             ("at", "🇦🇹 Австрия"), ("ch", "🇨🇭 Швейцария"), ("pl", "🇵🇱 Польша"),
             ("se", "🇸🇪 Швеция"), ("dk", "🇩🇰 Дания"), ("pt", "🇵🇹 Португалия")]
    rows = [[InlineKeyboardButton(lbl, callback_data=f"a_concerts_{cc}")] for cc, lbl in codes]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_music")])
    await bot.send_message(chat_id=cid, text="🌍 Выбери страну для поиска концертов:",
                           reply_markup=InlineKeyboardMarkup(rows))

async def start_add_artist(bot, cid):
    store.pending_input[str(cid)] = "artist"
    await bot.send_message(chat_id=cid, text="Напиши имя артиста - добавлю в список.")

async def add_artist(bot, cid, text):
    store.add_to_list(config.ARTISTS_KEY, cid, text)
    await bot.send_message(chat_id=cid, text="Добавил артиста.")