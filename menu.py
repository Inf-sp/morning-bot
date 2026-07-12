from ui import menu as menu_ui


_WELCOME = menu_ui.welcome()
WELCOME, WELCOME_ENTITIES = _WELCOME.text, _WELCOME.entities

REPLY_KB_FLAG = "reply_kb_cleared_v4"  # версия флага в профиле - нижняя Reply-клавиатура
                                        # убрана целиком, старым профилям шлём удаление ещё раз


def main_menu_kb():
    return menu_ui.main_menu_kb()


def main_menu_screen(cid=None):
    return WELCOME, WELCOME_ENTITIES, main_menu_kb()


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
