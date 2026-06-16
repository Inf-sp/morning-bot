from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai

def content_recommend(kind, favorites):
    fav = ", ".join(favorites) if favorites else "1984, Цветы для Элджернона, Марсианин, умная фантастика"
    what = "фильмов/сериалов" if kind == "movie" else "книг"
    prompt = f"""Порекомендуй 5 {what} для вкуса: {fav}. Любит научную фантастику и интеллектуальное.
JSON: {{"items": [{{"title": "название (год)", "hook": "1 строка интриги, на что похоже", "rating": "X.X"}}]}}
rating - предполагаемая оценка из 10 на основе вкуса."""
    return ai.llm_json(prompt, 900)

async def send_recos(bot, cid, kind):
    await bot.send_message(chat_id=cid, text="Подбираю...")
    try:
        data = content_recommend(kind, store.get_list(config.FAVORITES_KEY, str(cid)))
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
    await bot.send_message(chat_id=cid,
        text="🎤 Артисты:\n" + ("\n".join(f"• {a}" for a in arts) if arts else "пусто") +
             "\n\nПоиск концертов требует API событий (Ticketmaster/Bandsintown).")

async def start_add_artist(bot, cid):
    store.pending_input[str(cid)] = "artist"
    await bot.send_message(chat_id=cid, text="Напиши имя артиста - добавлю в список.")

async def add_artist(bot, cid, text):
    store.add_to_list(config.ARTISTS_KEY, cid, text)
    await bot.send_message(chat_id=cid, text="Добавил артиста.")
