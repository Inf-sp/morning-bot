from telegram import ReplyKeyboardMarkup

from ui import menu as menu_ui


_WELCOME = menu_ui.welcome()
WELCOME, WELCOME_ENTITIES = _WELCOME.text, _WELCOME.entities


def main_kb(cid=None):
    rows = [
        ["☀️ Мой день"],
        ["👕 Гардероб", "🥣 Готовка"],
        ["📚 Обучение", "🚑 Здоровье"],
        ["🍿 Досуг", "🎚️ Настройки"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


MAIN_LABELS = [
    "☀️ Мой день",
    "👕 Гардероб",
    "🥣 Готовка",
    "📚 Обучение",
    "🚑 Здоровье",
    "🍿 Досуг",
    "🎚️ Настройки",
]


LABEL_TO_KEY = {
    "👕 Гардероб": "m_wardrobe",
    "🥣 Готовка": "m_food",
    "📚 Обучение": "m_learn",
    "🚑 Здоровье": "m_balance",
    "🍿 Досуг": "m_leisure",
    "🎚️ Настройки": "m_notes",
}


def _ikb(rows):
    return menu_ui.ikb(rows)


def _back(parent="m_close"):
    return [("◀️ Назад", parent)]


def menu_screen(key):
    msg = menu_ui.menu_screen(key)
    return msg.text, msg.entities, msg.reply_markup


async def send_food_menu(bot, cid):
    import settings as _s
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    msg = menu_ui.food_menu()
    kb = msg.reply_markup
    extra = _s.setup_again_rows(cid, "cooking")
    if extra and kb is not None:
        kb = InlineKeyboardMarkup(list(kb.inline_keyboard) + extra)
    await _s.send_setup_again_banner(bot, cid, "cooking")
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=kb,
    )
