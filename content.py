from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import re
import config
import store
import ai
from util import esc

def content_recommend(kind, cid):
    if kind == "movie":
        seen = store.get_list(config.WATCHLIST_KEY, cid)
        what = "фильмов или сериалов"
    else:
        seen = store.get_list(config.READLIST_KEY, cid)
        what = "книг"
    seen_titles = [s if isinstance(s, str) else str(s) for s in seen]
    avoid = ("\nНЕ рекомендуй то, что уже есть в его списке: " + ", ".join(seen_titles[:60])) if seen_titles else ""
    anchors = ", ".join(seen_titles[:25]) if seen_titles else "Breaking Bad, Euphoria, Parasite, Call Me by Your Name"
    prompt = f"""{config.CONTENT_TASTE}

Его уже отмеченные работы (референсы вкуса): {anchors}

Порекомендуй 5 {what}, максимально точно под этот профиль вкуса.{avoid}
JSON: {{"items": [{{"title": "название (год)", "hook": "1 строка: на что похоже из его референсов и чем зацепит", "rating": "X.X"}}]}}
rating - предполагаемая оценка из 10 именно под его вкус."""
    return ai.llm_json(prompt, 1000)

def _tmdb_lookup(title):
    if not config.TMDB_API_KEY:
        return None
    import requests
    try:
        r = requests.get("https://api.themoviedb.org/3/search/multi",
            params={"api_key": config.TMDB_API_KEY, "query": title, "language": "ru-RU",
                    "include_adult": "false"}, timeout=12)
        results = [x for x in r.json().get("results", []) if x.get("media_type") in ("movie", "tv")]
        if not results:
            return None
        x = results[0]
        name = x.get("title") or x.get("name") or title
        date = x.get("release_date") or x.get("first_air_date") or ""
        year = date[:4] if date else ""
        rating = x.get("vote_average") or 0
        poster = x.get("poster_path")
        kind = "movie" if x.get("media_type") == "movie" else "tv"
        return {"name": name, "year": year, "rating": rating,
                "poster": (f"https://image.tmdb.org/t/p/w500{poster}" if poster else None),
                "url": f"https://www.themoviedb.org/{kind}/{x.get('id')}",
                "overview": x.get("overview", "")}
    except Exception:
        return None

async def send_recos(bot, cid, kind):
    await bot.send_message(chat_id=cid, text="Подбираю под твой вкус...")
    try:
        data = content_recommend(kind, str(cid))
        items = data.get("items", [])
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    store.last_recos[str(cid)] = {"kind": kind, "items": [it.get("title", "") for it in items]}
    store.last_source[str(cid)] = "Досуг · " + ("Что посмотреть" if kind == "movie" else "Что почитать")
    store.last_answer[str(cid)] = "\n".join(f"{it.get('title','')} - {it.get('hook','')}" for it in items)
    label = "🍿 В закладки" if kind == "movie" else "📚 В закладки"

    if kind == "movie" and config.TMDB_API_KEY:
        await bot.send_message(chat_id=cid, text="🎬 <b>Что посмотреть</b>", parse_mode="HTML")
        for i, it in enumerate(items):
            tm = _tmdb_lookup(it.get("title", ""))
            title = (tm["name"] if tm else it.get("title", ""))
            year = f" ({tm['year']})" if tm and tm["year"] else ""
            cap = [f"🎬 <b>{esc(title)}{year}</b>"]
            if tm and tm["rating"]:
                cap.append(f"⭐ {tm['rating']:.1f}/10 TMDb")
            cap.append(esc(it.get("hook", "")))
            if tm and tm.get("url"):
                cap.append(tm["url"])
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"{label}: {title[:26]}", callback_data=f"reco_{i}")]])
            text = "\n".join(cap)
            if tm and tm.get("poster"):
                try:
                    await bot.send_photo(chat_id=cid, photo=tm["poster"], caption=text, parse_mode="HTML", reply_markup=kb)
                    continue
                except Exception:
                    pass
            await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)
        await bot.send_message(chat_id=cid, text="Ещё 👇",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")]]))
        return

    head = "🎬 Что посмотреть" if kind == "movie" else "📖 Что почитать"
    lines = [head, ""]
    for it in items:
        lines.append(f"• {it.get('title','')}")
        lines.append(f"  {it.get('hook','')}")
        lines.append(f"  ⭐ ~{it.get('rating','')}/10")
    rows = [[InlineKeyboardButton(f"{label}: {it.get('title','')[:28]}", callback_data=f"reco_{i}")]
            for i, it in enumerate(items)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

async def add_reco(bot, cid, i):
    from datetime import datetime
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        key = config.WATCHLIST_KEY if rec["kind"] == "movie" else config.READLIST_KEY
        folder = "Что посмотреть" if rec["kind"] == "movie" else "Что почитать"
        store.add_to_list(key, cid, title)
        store.add_to_list(config.NOTES_KEY, cid,
                          {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": title, "source": folder})
        await bot.send_message(chat_id=cid, text=f"⭐ В закладках «{folder}»: {title}")

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

async def send_listen(bot, cid):
    arts = store.get_list(config.ARTISTS_KEY, cid)
    anchors = ", ".join(arts[:25]) if arts else "Charli xcx, The xx, Fever Ray, RÜFÜS DU SOL, PLACEBO"
    await bot.send_message(chat_id=cid, text="Подбираю под твой вкус...")
    try:
        data = ai.llm_json(
            f"Любимые исполнители: {anchors}. Порекомендуй 5 новых артистов/треков в этом вкусе "
            "(электроника, синтипоп, альт, дрим-поп, дарквейв и близкое). НЕ повторяй уже любимых.\n"
            'JSON: {"items": [{"title": "Артист - Трек/Альбом", "hook": "1 строка чем похоже"}]}', 900)
        items = data.get("items", [])
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}"); return
    store.last_recos[str(cid)] = {"kind": "listen", "items": [it.get("title", "") for it in items]}
    lines = ["🎵 <b>Что послушать</b>", ""]
    for it in items:
        lines.append(f"• {esc(it.get('title',''))}")
        lines.append(f"  {esc(it.get('hook',''))}")
    rows = [[InlineKeyboardButton(f"⭐ В закладки: {it.get('title','')[:26]}", callback_data=f"listen_{i}")]
            for i, it in enumerate(items)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def add_listen(bot, cid, i):
    from datetime import datetime
    rec = store.last_recos.get(str(cid))
    if rec and rec.get("kind") == "listen" and i < len(rec["items"]):
        title = rec["items"][i]
        store.add_to_list(config.NOTES_KEY, cid,
                          {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": title, "source": "Что послушать"})
        await bot.send_message(chat_id=cid, text=f"⭐ В закладках «Что послушать»: {title}")

async def send_artists(bot, cid):
    arts = store.get_list(config.ARTISTS_KEY, cid)
    txt = "🎤 Мои артисты:\n" + ("\n".join(f"• {a}" for a in arts) if arts else "пусто")
    await bot.send_message(chat_id=cid, text=txt)

async def find_concerts(bot, cid, mode="home"):
    if not config.TICKETMASTER_API_KEY:
        await bot.send_message(chat_id=cid,
            text="🔎 Поиск концертов требует бесплатный ключ Ticketmaster.\n"
                 "Заведи его на developer.ticketmaster.com и добавь на Railway переменную TICKETMASTER_API_KEY.")
        return
    artists = store.get_list(config.ARTISTS_KEY, cid)
    if not artists:
        await bot.send_message(chat_id=cid, text="Список артистов пуст. Нажми «Мои артисты» или выполни /reload_artists.")
        return
    import util
    s = store.get_settings(cid)
    home_cc = (s.get("cc") or "NL").upper()
    home_flag = util.flag_from_cc(home_cc) or "🏳"
    home_name = s.get("country") or "твоя страна"
    if mode == "be":
        cc, flag, cname = "BE", "🇧🇪", "Бельгия"
    elif mode == "de":
        cc, flag, cname = "DE", "🇩🇪", "Германия"
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

    rows = []
    if mode == "home":
        rows.append([InlineKeyboardButton("🇧🇪 Бельгия", callback_data="a_concerts_be")])
        rows.append([InlineKeyboardButton("🇩🇪 Германия", callback_data="a_concerts_de")])
    else:
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="a_concerts_find")])
    rows.append([InlineKeyboardButton("⬅️ Меню", callback_data="m_leisure")])
    kb = InlineKeyboardMarkup(rows)

    if not found:
        store.last_answer[str(cid)] = f"Концерты в {cname}: ничего не нашёл."
        await bot.send_message(chat_id=cid,
            text=f"🎤 <b>Концерты в {esc(cname)}</b>\n\nСейчас ничего не нашёл. Загляни позже"
                 + (" или проверь Бельгию/Германию ниже." if mode == "home" else "."),
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

async def start_add_artist(bot, cid):
    store.pending_input[str(cid)] = "artist"
    await bot.send_message(chat_id=cid, text="Напиши имя артиста - добавлю в список.")

async def add_artist(bot, cid, text):
    store.add_to_list(config.ARTISTS_KEY, cid, text)
    await bot.send_message(chat_id=cid, text="Добавил артиста.")