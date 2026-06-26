from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import learning

SETTINGS_KEY = "user_settings.json"
NOTIF_TYPES = [
    ("grammar", "📚 Слова дня (11:00)"),
    ("checkin_day", "🫣 Дневная разгрузка (14:00)"),
    ("checkin_eve", "🥸 Вечерний разбор (22:00)"),
]
STYLES = ["минимализм", "скандинавская эстетика", "натуральные ткани"]

def _all():
    return store._load(SETTINGS_KEY)

def get(cid, key, default=None):
    return _all().get(str(cid), {}).get(key, default)

def set_(cid, key, value):
    d = _all()
    d.setdefault(str(cid), {})[key] = value
    store._save(SETTINGS_KEY, d)

def notif_on(cid, kind):
    return get(cid, f"notif_{kind}", True)

def study_lang(cid):
    return get(cid, "study_lang", "нидерландский")


def home_kb(cid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗄 Шкаф", callback_data="set_wardrobe")],
        [InlineKeyboardButton("🍃 Лагом", callback_data="set_lagom")],
        [InlineKeyboardButton("❤️ Любимые", callback_data="set_love")],
        [InlineKeyboardButton("🗂️ Словарь", callback_data="set_dict")],
        [InlineKeyboardButton("🧊 Холодильник", callback_data="set_fridge")],
        [InlineKeyboardButton("🔔 Уведомления", callback_data="set_notif")],
        [InlineKeyboardButton("🗣 Язык для грамматики", callback_data="set_lang")],
        [InlineKeyboardButton("🎚 Уровень языков", callback_data="set_levels")],
        [InlineKeyboardButton("🌍 Сменить город", callback_data="set_city")],
    ])

async def send_home(bot, cid):
    await bot.send_message(chat_id=cid,
        text="⚙️ <b>Настройки</b>\n\nЯзык, уведомления, город и параметры стиля.\n\nВыбери раздел 👇",
        parse_mode="HTML", reply_markup=home_kb(cid))

async def send_notif(bot, cid):
    rows = []
    for kind, label in NOTIF_TYPES:
        on = notif_on(cid, kind)
        mark = "🟢" if on else "⚪"
        rows.append([InlineKeyboardButton(f"{mark} {label}", callback_data=f"set_notiftgl_{kind}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    await bot.send_message(chat_id=cid,
        text="🔔 <b>Уведомления</b>\n\nНажми, чтобы включить/выключить. 🟢 - включено.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def toggle_notif(bot, cid, kind):
    set_(cid, f"notif_{kind}", not notif_on(cid, kind))
    await send_notif(bot, cid)

async def send_lang(bot, cid):
    cur = study_lang(cid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(("✅ " if cur == "нидерландский" else "") + "🇳🇱 Нидерландский", callback_data="set_lang_nl")],
        [InlineKeyboardButton(("✅ " if cur == "английский" else "") + "🇬🇧 Английский", callback_data="set_lang_en")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home")],
    ])
    await bot.send_message(chat_id=cid, text="🗣 <b>Язык для утренней грамматики/слова дня</b>",
                           parse_mode="HTML", reply_markup=kb)

async def set_lang(bot, cid, lang):
    set_(cid, "study_lang", lang)
    await bot.send_message(chat_id=cid, text=f"Готово. Язык уведомлений по обучению: {lang}.")
    await send_home(bot, cid)

async def send_body(bot, cid):
    cur = get(cid, "style", "минимализм")
    rows = [[InlineKeyboardButton(("✅ " if cur == s else "") + s, callback_data=f"set_style_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("✏️ Ввести параметры тела", callback_data="set_bodyinput")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    body = get(cid, "body", "рост 179 см, вес ~65 кг, обувь EU 42.5, брюки W31 L31, размер M")
    await bot.send_message(chat_id=cid,
        text=f"👕 <b>Параметры шкафа</b>\n\nСейчас: {body}\nСтиль: {cur}\n\nВыбери стиль или введи параметры:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def set_style(bot, cid, i):
    if 0 <= i < len(STYLES):
        set_(cid, "style", STYLES[i])
    await send_body(bot, cid)


# ===== Списки в настройках: страны, артисты, книги, шкаф =====
def _list_kb(items, del_prefix, add_cb, back="set_home"):
    rows = []
    for i, it in enumerate(items[-40:]):
        label = it if isinstance(it, str) else (it.get("name") or it.get("word") or str(it))
        rows.append([InlineKeyboardButton(f"❌ {str(label)[:28]}", callback_data=f"{del_prefix}{i}")])
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data=add_cb)])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back)])
    return InlineKeyboardMarkup(rows)

# --- Шкаф ---
async def send_wardrobe(bot, cid):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Показать всё", callback_data="w_show")],
        [InlineKeyboardButton("🏷 Добавить вещь", callback_data="w_add")],
        [InlineKeyboardButton("🧹 Удалить вещь", callback_data="w_del")],
        [InlineKeyboardButton("📐 Параметры шкафа", callback_data="set_body")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home")],
    ])
    await bot.send_message(chat_id=cid, text="👕 <b>Мой шкаф</b>\n\nБаза вещей и параметры для подбора лука.",
                           parse_mode="HTML", reply_markup=kb)

# --- Страны ---
def _preload_countries(cid):
    cur = store.get_list(config.COUNTRIES_KEY, cid)
    if not cur:
        seed = [c.strip() for c in config.VISITED.replace("Страны:", "").split(",") if c.strip()]
        store.set_list(config.COUNTRIES_KEY, cid, seed)
        return seed
    return cur

async def send_countries(bot, cid):
    items = _preload_countries(cid)
    txt = "🧳 <b>Мои страны</b>\n\n" + (", ".join(items) if items else "пусто")
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=_list_kb(items, "setdel_country_", "setadd_country"))

# --- Артисты ---
async def send_artists(bot, cid):
    items = store.get_list(config.ARTISTS_KEY, cid)
    txt = "🎤 <b>Мои артисты</b>\n\n" + (", ".join(items) if items else "пусто. Добавь или выполни /reload_artists")
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=_list_kb(items, "setdel_artist_", "setadd_artist"))

# --- Книги ---
def _preload_books(cid):
    cur = store.get_list(config.BOOKS_KEY, cid)
    if cur:
        return cur
    try:
        import json
        with open("content.json", encoding="utf-8") as f:
            seed = list(json.load(f).get("books", []))
        if seed:
            store.set_list(config.BOOKS_KEY, cid, seed)
            return seed
    except Exception:
        pass
    return cur

# --- Лагом ---
_LAGOM_INTRO = (
    "🍃 <b>Лагом — твои установки и ценности</b>\n\n"
    "Лагом (швед. <i>lagom</i> — «в самый раз») — это твой личный свод принципов: "
    "что важно, как ты хочешь жить, что даёт энергию, а что забирает.\n\n"
    "Бот использует эти установки в разделе 🎯 Личная мотивация — "
    "чтобы советы и поддержка звучали именно про тебя, а не общими словами.\n\n"
    "<b>Примеры:</b> «Меньше, но лучше», «Физическая активность каждый день», "
    "«Не сравниваю себя с другими», «Ем осознанно».\n\n"
)

async def send_lagom(bot, cid):
    import myday
    items = list(myday.ensure_lagom(cid))
    body = ("\n".join(f"• {it}" for it in items)) if items else "Пока пусто — добавь первую установку 👇"
    txt = _LAGOM_INTRO + body
    rows = [[InlineKeyboardButton(f"❌ {str(it)[:30]}", callback_data=f"setdel_lagom_{i}")]
            for i, it in enumerate(items)]
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data="setadd_lagom")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_books(bot, cid):
    items = _preload_books(cid)
    txt = "📚 <b>Мои книги</b>\n\n" + ("\n".join(f"• {b}" for b in items) if items else "пусто")
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=_list_kb(items, "setdel_book_", "setadd_book"))

async def list_delete(bot, cid, kind, i):
    keymap = {"country": config.COUNTRIES_KEY, "artist": config.ARTISTS_KEY, "book": config.BOOKS_KEY}
    key = keymap.get(kind)
    items = store.get_list(key, cid)
    if i < len(items):
        items.pop(i)
        store.set_list(key, cid, items)
    if kind == "country":
        await send_countries(bot, cid)
    elif kind == "artist":
        await send_artists(bot, cid)
    else:
        await send_books(bot, cid)

async def list_add_done(bot, cid, kind, text):
    keymap = {"country": config.COUNTRIES_KEY, "artist": config.ARTISTS_KEY, "book": config.BOOKS_KEY}
    store.add_to_list(keymap[kind], cid, text.strip())
    await bot.send_message(chat_id=cid, text="Добавлено.")
    if kind == "country":
        await send_countries(bot, cid)
    elif kind == "artist":
        await send_artists(bot, cid)
    else:
        await send_books(bot, cid)


async def handle_callback(bot, cid, data):
    if data == "set_home":
        await send_home(bot, cid)
    elif data == "set_love":
        import notes
        await notes.send_love_home(bot, cid)
    elif data == "set_dict":
        import learning
        await learning.send_dict(bot, cid)
    elif data == "set_fridge":
        import balance
        await balance.send_fridge(bot, cid)
    elif data == "set_notif":
        await send_notif(bot, cid)
    elif data.startswith("set_notiftgl_"):
        await toggle_notif(bot, cid, data[len("set_notiftgl_"):])
    elif data == "set_lang":
        await send_lang(bot, cid)
    elif data == "set_lang_nl":
        await set_lang(bot, cid, "нидерландский")
    elif data == "set_lang_en":
        await set_lang(bot, cid, "английский")
    elif data == "set_levels":
        await learning.send_levels(bot, cid)
    elif data == "set_city":
        store.pending_input[cid] = "setcity"
        await bot.send_message(chat_id=cid, text="🌍 Напиши город - переключу.")
    elif data == "set_body":
        await send_body(bot, cid)
    elif data == "set_wardrobe":
        await send_wardrobe(bot, cid)
    elif data == "set_lagom":
        await send_lagom(bot, cid)
    elif data == "setadd_lagom":
        store.pending_input[cid] = "setadd_lagom"
        await bot.send_message(chat_id=cid,
            text="🍃 Напиши установку или принцип — добавлю в Лагом.\n\n"
                 "<i>Например: «Меньше экрана, больше природы»</i>",
            parse_mode="HTML")
    elif data.startswith("setdel_lagom_"):
        import myday
        i = int(data.split("_")[-1])
        items = list(myday.ensure_lagom(cid))
        if i < len(items):
            items.pop(i)
            store.set_list(config.LAGOM_KEY, cid, items)
        await send_lagom(bot, cid)
    elif data == "set_countries":
        await send_countries(bot, cid)
    elif data == "set_artists":
        await send_artists(bot, cid)
    elif data == "set_books":
        await send_books(bot, cid)
    elif data == "setadd_country":
        store.pending_input[cid] = "setadd_country"
        await bot.send_message(chat_id=cid, text="🧳 Напиши страну - добавлю в список.")
    elif data == "setadd_artist":
        store.pending_input[cid] = "setadd_artist"
        await bot.send_message(chat_id=cid, text="🎤 Напиши имя артиста - добавлю в список.")
    elif data == "setadd_book":
        store.pending_input[cid] = "setadd_book"
        await bot.send_message(chat_id=cid, text="📚 Напиши название книги - добавлю в список.")
    elif data.startswith("setdel_country_"):
        await list_delete(bot, cid, "country", int(data.split("_")[-1]))
    elif data.startswith("setdel_artist_"):
        await list_delete(bot, cid, "artist", int(data.split("_")[-1]))
    elif data.startswith("setdel_book_"):
        await list_delete(bot, cid, "book", int(data.split("_")[-1]))
    elif data.startswith("set_style_"):
        await set_style(bot, cid, int(data.split("_")[-1]))
    elif data == "set_bodyinput":
        store.pending_input[cid] = "bodyinput"
        await bot.send_message(chat_id=cid, text="✏️ Напиши параметры: рост, вес, обувь, размер брюк и одежды.")