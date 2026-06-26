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
STYLES = [
    "минимализм",
    "скандинавский стиль",
    "smart casual",
    "casual / повседневный",
    "классика",
    "streetwear / городской",
    "натуральный / бохо",
    "спортивный",
]

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
        [InlineKeyboardButton("🌍 Сменить город", callback_data="set_city")],
        [InlineKeyboardButton("🔔 Уведомления", callback_data="set_notif")],
        [InlineKeyboardButton("🎚 Уровень языков", callback_data="set_levels")],
        [InlineKeyboardButton("👕 Шкаф", callback_data="set_wardrobe")],
        [InlineKeyboardButton("🧊 Холодильник", callback_data="set_fridge")],
        [InlineKeyboardButton("🎯 Лагом", callback_data="set_lagom")],
        [InlineKeyboardButton("🗂️ Словарь", callback_data="set_dict")],
        [InlineKeyboardButton("❤️ Любимые", callback_data="set_love")],
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
    body = get(cid, "body", "рост 179 см, вес ~65 кг, обувь EU 42.5, брюки W31 L31, размер M")
    style = get(cid, "style", "минимализм")
    txt = f"📐 <b>Параметры шкафа</b>\n\n<b>Параметры:</b> {body}\n<b>Стиль:</b> {style}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Параметры тела", callback_data="set_bodyinput")],
        [InlineKeyboardButton("🎨 Стиль", callback_data="set_stylepick")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_wardrobe")],
    ])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)

async def send_style_pick(bot, cid):
    cur = get(cid, "style", "минимализм")
    rows = [[InlineKeyboardButton(("✅ " if cur == s else "") + s, callback_data=f"set_style_{i}")]
            for i, s in enumerate(STYLES)]
    rows.append([InlineKeyboardButton("✏️ Описать своими словами", callback_data="set_stylecustom")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_body")])
    await bot.send_message(chat_id=cid,
        text="🎨 <b>Стиль одежды</b>\n\nВыбери из предложенных или опиши своими словами — бот учтёт при подборе образа:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def set_style(bot, cid, i):
    if 0 <= i < len(STYLES):
        set_(cid, "style", STYLES[i])
    await send_body(bot, cid)


# ===== Списки в настройках: страны, артисты, книги, шкаф =====
def _item_label(it):
    return it if isinstance(it, str) else (it.get("name") or it.get("word") or str(it))

def _list_kb(items, del_prefix, add_cb, back="set_home"):
    rows = [[InlineKeyboardButton(f"❌ {_item_label(it)[:35]}", callback_data=f"{del_prefix}{i}")]
            for i, it in enumerate(items[-40:])]
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data=add_cb)])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back)])
    return InlineKeyboardMarkup(rows)

async def _send_list(bot, cid, title, items, del_prefix, add_cb, back="set_home"):
    """Лагом и аналогичные экраны с intro-текстом: элементы только в кнопках."""
    txt = title if items else f"{title}\n\nПока пусто — добавь первый элемент 👇"
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=_list_kb(items, del_prefix, add_cb, back))

# --- Шкаф ---
async def send_wardrobe(bot, cid):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Добавить", callback_data="set_ward_add")],
        [InlineKeyboardButton("❌ Убрать", callback_data="set_ward_del")],
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
    from util import country_flag
    items = _preload_countries(cid)
    rows = [[InlineKeyboardButton(f"❌ {country_flag(it)} {_item_label(it)[:33]}", callback_data=f"setdel_country_{i}")]
            for i, it in enumerate(items[-40:])]
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data="setadd_country")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    await bot.send_message(chat_id=cid, text="🧳 <b>Мои страны</b>", parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

# --- Артисты ---
async def send_artists(bot, cid):
    items = store.get_list(config.ARTISTS_KEY, cid)
    await bot.send_message(chat_id=cid, text="🎤 <b>Мои артисты</b>", parse_mode="HTML",
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
    "☕️ <b>Лагом — твои установки и ценности</b>\n\n"
    "Лагом (швед. <i>lagom</i> — «в самый раз») — твой личный свод принципов: "
    "что важно, как хочешь жить, что даёт энергию, а что забирает.\n\n"
    "Бот использует их в 🎯 Личная мотивация — "
    "чтобы советы звучали именно про тебя, а не общими словами.\n\n"
    "<b>Примеры:</b> «Меньше, но лучше» · «Физическая активность каждый день» · "
    "«Не сравниваю себя с другими»\n\n"
)

async def send_lagom(bot, cid):
    import memory
    items = memory.get_lagom(cid)
    txt = _LAGOM_INTRO.rstrip() if items else f"{_LAGOM_INTRO.rstrip()}\n\nПока пусто — добавь первый принцип 👇"
    rows = []
    rows.append([InlineKeyboardButton("📝 Добавить", callback_data="setadd_lagom")])
    if items:
        rows.append([InlineKeyboardButton("❌ Убрать", callback_data="set_lagom_clean")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="set_home")])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_books(bot, cid):
    items = _preload_books(cid)
    await bot.send_message(chat_id=cid, text="📚 <b>Мои книги</b>", parse_mode="HTML",
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
    from util import esc
    keymap = {"country": config.COUNTRIES_KEY, "artist": config.ARTISTS_KEY, "book": config.BOOKS_KEY}
    icons = {"country": "🧳", "artist": "🎤", "book": "📚"}
    item = text.strip()
    store.add_to_list(keymap[kind], cid, item)
    await bot.send_message(chat_id=cid,
        text=f"✅ {icons.get(kind, '')} «{esc(item)}» добавлено.", parse_mode="HTML")
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
    elif data == "set_ward_add":
        store.pending_input[cid] = "wardrobe_add_set"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="set_wardrobe")]])
        await bot.send_message(chat_id=cid,
            text="🏷 Напиши вещь: тип + цвет + детали/бренд.\n"
                 "<i>Напр.: «Футболка белая Uniqlo» или «Шорты серые тонкие». Можно списком.</i>",
            parse_mode="HTML", reply_markup=kb)
    elif data == "set_ward_del":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "kast_s")
    elif data == "set_lagom":
        await send_lagom(bot, cid)
    elif data == "setadd_lagom":
        store.pending_input[cid] = "setadd_lagom"
        await bot.send_message(chat_id=cid,
            text="☕️ Напиши установку или принцип — добавлю в Лагом.\n\n"
                 "<i>Например: «Меньше экрана, больше природы»</i>",
            parse_mode="HTML")
    elif data.startswith("setdel_lagom_"):
        import memory
        memory.del_lagom(cid, int(data.split("_")[-1]))
        await send_lagom(bot, cid)
    elif data == "set_lagom_clean":
        from cleanup import open_cleanup
        await open_cleanup(bot, cid, "lagom")
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
    elif data == "set_stylepick":
        await send_style_pick(bot, cid)
    elif data == "set_stylecustom":
        store.pending_input[cid] = "styleinput"
        await bot.send_message(chat_id=cid,
            text="🎨 Опиши свой стиль — как хочешь выглядеть, что нравится, что нет.\n\n"
                 "<i>Например: «Люблю тёмные оттенки, оверсайз-силуэты, минимум принтов. "
                 "Стараюсь избегать костюмов.»</i>",
            parse_mode="HTML")
    elif data.startswith("set_style_"):
        await set_style(bot, cid, int(data.split("_")[-1]))
    elif data == "set_bodyinput":
        store.pending_input[cid] = "bodyinput"
        await bot.send_message(chat_id=cid, text="✏️ Напиши параметры: рост, вес, обувь, размер брюк и одежды.")