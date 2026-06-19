from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import learning

SETTINGS_KEY = "user_settings.json"
NOTIF_TYPES = [
    ("morning", "🌅 Утренняя сводка (08:30)"),
    ("grammar", "📚 Грамматика/слово дня (11:00)"),
    ("checkin_day", "🫣 Дневная разгрузка (14:00)"),
    ("checkin_eve", "🥸 Вечерний разбор (20:00)"),
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
        [InlineKeyboardButton("🔔 Уведомления", callback_data="set_notif")],
        [InlineKeyboardButton("🗣 Язык для грамматики", callback_data="set_lang")],
        [InlineKeyboardButton("🎚 Уровень языков", callback_data="set_levels")],
        [InlineKeyboardButton("🌍 Сменить город", callback_data="set_city")],
        [InlineKeyboardButton("👕 Параметры шкафа", callback_data="set_body")],
    ])

async def send_home(bot, cid):
    await bot.send_message(chat_id=cid,
        text="⚙️ <b>Настройки</b>\n\nЗдесь можно настроить уведомления, язык обучения, "
             "уровень, город и параметры для подбора лука.\n\nВыбери 👇",
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