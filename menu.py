import config
import store
from ui import menu as menu_ui

REPLY_KB_REMOVED_FLAG = "reply_kb_removed_v7"  # разово снимаем нижнюю Reply-клавиатуру
                                                # «Ассистент» у профилей, где она уже была


def welcome_for(cid):
    """Приветствие с именем пользователя из профиля, если оно уже собрано онбордингом."""
    name = store.get_profile(cid).get("name", "") if cid is not None else ""
    return menu_ui.welcome(name)


def main_menu_kb():
    return menu_ui.main_menu_kb()


_MAIN_MENU_CALLBACKS = {
    "m_myday", "m_wardrobe", "m_food", "m_learn", "m_balance",
    "m_travel", "m_movie", "m_books", "m_music", "m_notes",
}


def is_main_menu_markup(markup):
    """Распознаёт главное меню, включая сообщения из версии до персистентного id."""
    callbacks = {
        button.callback_data
        for row in getattr(markup, "inline_keyboard", [])
        for button in row
        if getattr(button, "callback_data", None)
    }
    return _MAIN_MENU_CALLBACKS.issubset(callbacks)


def main_menu_screen(cid=None):
    msg = welcome_for(cid)
    return msg.text, msg.entities, main_menu_kb()


def inactivity_reminder():
    return menu_ui.inactivity_reminder()


def _back(parent="m_close"):
    return [("⬅️ Назад", parent), ("#️⃣ Главная", "m_menu")]


def menu_screen(key, cid=None):
    if key == "m_learn":
        import learning
        home = learning.build_learning_home(cid) if cid is not None else {"has_material": False, "lang_code": "nl"}
        msg = menu_ui.learning_menu(home)
    elif key == "m_balance":
        import balance
        msg = menu_ui.health_menu(balance.health_focus(cid))
    else:
        msg = menu_ui.menu_screen(key)
    return msg.text, msg.entities, msg.reply_markup


def has_available_fridge(cid) -> bool:
    from fridge_model import _fridge_available
    return bool(_fridge_available(store.get_list(config.FRIDGE_KEY, str(cid))))


async def send_food_menu(bot, cid, status=None, refresh=False, q=None):
    import asyncio
    import recipe_generation
    import util
    import verify

    if not has_available_fridge(cid):
        msg = menu_ui.food_empty_menu()
        if status is not None:
            await status.replace(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
        elif q is not None:
            try:
                await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
            except Exception:
                await bot.send_message(
                    chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup,
                )
        else:
            await bot.send_message(
                chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup,
            )
        return

    if not refresh:
        cached = recipe_generation.get_cached_cooking_home_idea(cid)
        if cached is not None:
            msg = menu_ui.food_menu(cached)
            if status is not None:
                await status.replace(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
            elif q is not None:
                try:
                    await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
                except Exception:
                    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                                           reply_markup=msg.reply_markup)
            else:
                await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                                       reply_markup=msg.reply_markup)
            return

    owns_status = status is None
    if status is None:
        if q is not None:
            status = await util.StatusManager.start_inline(
                q,
                bot=bot,
                cid=cid,
                stages=util.StatusManager.TOPIC_STAGES["food"],
                preserve_message=True,
            )
        else:
            status = await util.StatusManager.start(
                bot, cid, stages=util.StatusManager.TOPIC_STAGES["food"])
    try:
        idea = await asyncio.to_thread(
            recipe_generation.get_cooking_home_idea, cid, None, refresh)
        msg = menu_ui.food_menu(idea)
        await status.replace(
            msg.text,
            entities=msg.entities,
            reply_markup=msg.reply_markup,
        )
    except Exception as error:
        await verify.safe_error(bot, cid, error, back="m_menu")
    finally:
        if owns_status:
            await status.stop(delete=True)
