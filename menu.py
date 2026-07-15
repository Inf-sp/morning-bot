from ui import menu as menu_ui

REPLY_KB_REMOVED_FLAG = "reply_kb_removed_v7"  # разово снимаем нижнюю Reply-клавиатуру
                                                # «Ассистент» у профилей, где она уже была


def welcome_for(cid):
    """Приветствие с именем пользователя из профиля, если оно уже собрано онбордингом."""
    import store
    name = store.get_profile(cid).get("name", "") if cid is not None else ""
    return menu_ui.welcome(name)


def main_menu_kb():
    return menu_ui.main_menu_kb()


def main_menu_screen(cid=None):
    msg = welcome_for(cid)
    return msg.text, msg.entities, main_menu_kb()


def _back(parent="m_close"):
    return [("⬅️ Назад", parent), ("#️⃣ Меню", "m_menu")]


def menu_screen(key, cid=None):
    if key == "m_learn":
        import learning
        home = learning.build_learning_home(cid) if cid is not None else {"has_material": False, "lang_code": "nl"}
        msg = menu_ui.learning_menu(home)
    else:
        msg = menu_ui.menu_screen(key)
    return msg.text, msg.entities, msg.reply_markup


async def send_food_menu(bot, cid):
    import myday
    lifehacks = myday.kitchen_lifehacks(cid, 3)
    msg = menu_ui.food_menu(lifehacks)
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=msg.reply_markup,
    )
