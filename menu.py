from telegram import ReplyKeyboardMarkup

from ui import menu as menu_ui


_WELCOME = menu_ui.welcome()
WELCOME, WELCOME_ENTITIES = _WELCOME.text, _WELCOME.entities


def main_kb(cid=None):
    rows = [
        ["☀️ Мой день"],
        ["👕 Гардероб", "🚑 Здоровье"],
        ["📚 Обучение", "🍿 Досуг"],
        ["🥣 Готовка", "🎚️ Настройки"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


MAIN_LABELS = [
    "☀️ Мой день",
    "👕 Гардероб",
    "🚑 Здоровье",
    "📚 Обучение",
    "🍿 Досуг",
    "🥣 Готовка",
    "🎚️ Настройки",
]


LABEL_TO_KEY = {
    "👕 Гардероб": "m_wardrobe",
    "🚑 Здоровье": "m_balance",
    "📚 Обучение": "m_learn",
    "🍿 Досуг": "m_leisure",
    "🥣 Готовка": "m_food",
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
    msg = menu_ui.food_menu()
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=msg.reply_markup,
    )
