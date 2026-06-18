from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai

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

async def send_recos(bot, cid, kind):
    await bot.send_message(chat_id=cid, text="Подбираю под твой вкус...")
    try:
        data = content_recommend(kind, str(cid))
        items = data.get("items", [])
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    store.last_recos[str(cid)] = {"kind": kind, "items": [it.get("title", "") for it in items]}
    head = "🎬 Что посмотреть" if kind == "movie" else "📖 Что почитать"
    lines = [head, ""]
    for it in items:
        lines.append(f"• {it.get('title','')}")
        lines.append(f"  {it.get('hook','')}")
        lines.append(f"  ⭐ ~{it.get('rating','')}/10")
    label = "🍿 В список" if kind == "movie" else "📚 В список"
    rows = [[InlineKeyboardButton(f"{label}: {it.get('title','')[:28]}", callback_data=f"reco_{i}")]
            for i, it in enumerate(items)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_content")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

async def add_reco(bot, cid, i):
    rec = store.last_recos.get(str(cid))
    if rec and i < len(rec["items"]):
        title = rec["items"][i]
        key = config.WATCHLIST_KEY if rec["kind"] == "movie" else config.READLIST_KEY
        store.add_to_list(key, cid, title)
        await bot.send_message(chat_id=cid, text=f"Добавил в список: {title}")

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

async def send_artists(bot, cid):
    arts = store.get_list(config.ARTISTS_KEY, cid)
    txt = "🎤 Мои артисты:\n" + ("\n".join(f"• {a}" for a in arts) if arts else "пусто")
    await bot.send_message(chat_id=cid, text=txt)

async def find_concerts(bot, cid):
    if not config.TICKETMASTER_API_KEY:
        await bot.send_message(chat_id=cid,
            text="🔎 Поиск концертов требует бесплатный ключ Ticketmaster.\n"
                 "Заведи его на developer.ticketmaster.com и добавь на Railway переменную TICKETMASTER_API_KEY.")
        return
    artists = store.get_list(config.ARTISTS_KEY, cid)
    if not artists:
        await bot.send_message(chat_id=cid, text="Список артистов пуст. Нажми «Мои артисты» или выполни /reload_artists.")
        return
    await bot.send_message(chat_id=cid, text="Ищу концерты в Нидерландах, ~10-20 сек...")
    import requests
    found = {}
    for a in artists[:40]:
        try:
            r = requests.get("https://app.ticketmaster.com/discovery/v2/events.json",
                params={"apikey": config.TICKETMASTER_API_KEY, "keyword": a, "countryCode": "NL",
                        "classificationName": "music", "size": 2, "sort": "date,asc"}, timeout=15)
            for e in r.json().get("_embedded", {}).get("events", []):
                found[e.get("id")] = e
        except Exception:
            continue
    if not found:
        await bot.send_message(chat_id=cid, text="Сейчас концертов твоих артистов в Нидерландах не нашёл. Загляни позже.")
        return
    lines = ["🎤 Концерты в Нидерландах", ""]
    for e in list(found.values())[:15]:
        name = e.get("name", "")
        date = e.get("dates", {}).get("start", {}).get("localDate", "")
        ven = (e.get("_embedded", {}).get("venues") or [{}])[0]
        vn = ven.get("name", "")
        city = (ven.get("city") or {}).get("name", "")
        url = e.get("url", "")
        lines.append(f"🎤 {name}")
        if vn or city:
            lines.append(f"📍 {vn}{', ' + city if city else ''}")
        if date:
            lines.append(f"📅 {date}")
        pr = e.get("priceRanges")
        if pr:
            lines.append(f"💰 {pr[0].get('min')}-{pr[0].get('max')} {pr[0].get('currency','')}")
        if url:
            lines.append(f"🎟 {url}")
        lines.append("")
    from util import send_long
    await send_long(bot, cid, "\n".join(lines))

async def start_add_artist(bot, cid):
    store.pending_input[str(cid)] = "artist"
    await bot.send_message(chat_id=cid, text="Напиши имя артиста - добавлю в список.")

async def add_artist(bot, cid, text):
    store.add_to_list(config.ARTISTS_KEY, cid, text)
    await bot.send_message(chat_id=cid, text="Добавил артиста.")