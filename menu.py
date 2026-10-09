import logging

import category_news
import store
from ui import menu as menu_ui

_log = logging.getLogger(__name__)

REPLY_KB_REMOVED_FLAG = "reply_kb_removed_v7"  # разово снимаем нижнюю Reply-клавиатуру
                                                # «Ассистент» у профилей, где она уже была


def welcome_for(cid):
    """Приветствие с именем пользователя из профиля, если оно уже собрано онбордингом."""
    name = store.get_profile(cid).get("name", "") if cid is not None else ""
    return menu_ui.welcome(name)


def main_menu_kb():
    return menu_ui.main_menu_kb()


# Общие кнопки текущего и старого (с Кино/Музыкой/Книгами/Играми) главного меню:
# старые меню в истории чата тоже распознаются.
_MAIN_MENU_CALLBACKS = {"m_myday", "m_wardrobe", "m_food", "m_learn", "m_settings"}


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


def menu_screen(key, cid=None):
    if key == "m_learn":
        import learning
        home = learning.build_learning_home(cid) if cid is not None else {"has_material": False, "lang_code": "nl"}
        msg = menu_ui.learning_menu(home)
    else:
        msg = menu_ui.menu_screen(key)
    return msg.text, msg.entities, msg.reply_markup


async def _deliver(bot, cid, msg, status=None, q=None, **extra):
    """Статус -> правка карточки -> новое сообщение."""
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=msg.reply_markup, **extra)
        return
    if q is not None:
        try:
            await q.message.edit_text(
                msg.text, entities=msg.entities, reply_markup=msg.reply_markup, **extra)
            return
        except Exception:
            _log.debug("_deliver: ignored error", exc_info=True)
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup, **extra)


def _day_menu_message(menu, news=None):
    import day_menu
    return menu_ui.day_menu(
        menu, cuisine_label=day_menu.cuisine_label(menu.get("cuisine")),
        intro=day_menu.CUISINE_INTRO.get(menu.get("cuisine"), ""), news=news,
    )


async def _with_food_status(bot, cid, status, q, build):
    """Собирает экран Готовки с индикатором ожидания; ошибки — дружелюбным экраном."""
    import util
    import verify

    owns_status = status is None
    if status is None:
        if q is not None:
            status = await util.StatusManager.start_inline(
                q, bot=bot, cid=cid, stages=util.StatusManager.TOPIC_STAGES["food"],
                preserve_message=True,
            )
        else:
            status = await util.StatusManager.start(
                bot, cid, stages=util.StatusManager.TOPIC_STAGES["food"])
    try:
        await build(status)
    except Exception as error:
        await verify.safe_error(bot, cid, error, back="m_food")
    finally:
        if owns_status:
            await status.stop(delete=True)


async def send_food_menu(bot, cid, status=None, refresh=False, q=None, meal=None, cuisine=None):
    """Главный экран Готовки — меню на день; meal — полный рецепт блюда этого приёма пищи.

    Меню дня берётся из кэша мгновенно (ночной прогрев); без кэша или по
    «✨ Другое меню» (refresh, cuisine) собирается один раз с индикатором.
    """
    import asyncio
    import day_menu

    if meal in day_menu.MEALS:
        async def build_recipe(status):
            idea = await asyncio.to_thread(day_menu.dish_recipe, cid, meal)
            msg = menu_ui.food_menu(idea, meal=meal)
            await status.replace(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)

        await _with_food_status(bot, cid, status, q, build_recipe)
        return

    news = category_news.cached_line("food")
    if not refresh:
        ready = day_menu.get_cached_day_menu(cid)
        if ready is not None:
            await _deliver(bot, cid, _day_menu_message(ready, news), status, q)
            return

    async def build_menu(status):
        menu = await asyncio.to_thread(day_menu.get_day_menu, cid, None, refresh, cuisine)
        msg = _day_menu_message(menu, news)
        await status.replace(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)

    await _with_food_status(bot, cid, status, q, build_menu)
