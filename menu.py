from telegram import ReplyKeyboardMarkup

from ui import menu as menu_ui
from ui.constants import ui_label


_WELCOME = menu_ui.welcome()
WELCOME, WELCOME_ENTITIES = _WELCOME.text, _WELCOME.entities


def main_kb(cid=None):
    rows = [
        [ui_label("myday", "Мой день")],
        [ui_label("wardrobe", "Гардероб"), ui_label("food", "Готовка")],
        [ui_label("learning", "Обучение"), ui_label("health", "Здоровье")],
        [ui_label("travel", "Путешествия"), ui_label("leisure", "Досуг")],
        [ui_label("settings", "Настройки")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


MAIN_LABELS = [
    ui_label("myday", "Мой день"),
    ui_label("wardrobe", "Гардероб"),
    ui_label("food", "Готовка"),
    ui_label("learning", "Обучение"),
    ui_label("health", "Здоровье"),
    ui_label("travel", "Путешествия"),
    ui_label("leisure", "Досуг"),
    ui_label("settings", "Настройки"),
]


LABEL_TO_KEY = {
    ui_label("wardrobe", "Гардероб"): "m_wardrobe",
    ui_label("food", "Готовка"): "m_food",
    ui_label("learning", "Обучение"): "m_learn",
    ui_label("health", "Здоровье"): "m_balance",
    ui_label("travel", "Путешествия"): "m_travel",
    ui_label("leisure", "Досуг"): "m_leisure",
    ui_label("settings", "Настройки"): "m_notes",
}


def _back(parent="m_close"):
    return [("⬅️ Назад", parent)]


def _learning_code(cid):
    if cid is None:
        return "nl"
    try:
        import store
        code = store.get_learning_language(cid)
        if code in ("nl", "en"):
            return code
        import settings
        return "en" if settings.study_lang(cid) == "английский" else "nl"
    except Exception:
        return "nl"


def menu_screen(key, cid=None):
    if key == "m_learn":
        msg = menu_ui.learning_menu(_learning_code(cid))
    else:
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
