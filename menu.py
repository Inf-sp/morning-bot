import asyncio
import logging

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


# Общие кнопки текущего и старого (с Кино/Музыкой/Книгами/Играми — их больше нет) главного меню:
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


def _day_menu_message(menu, cid=None):
    import config
    import day_menu
    import vacation
    from fridge_model import _fridge_available
    import settings
    stats = None
    if cid is not None and not vacation.active(cid):  # в отпуске холодильник остался дома
        stats = {"products": len(_fridge_available(store.get_list(config.FRIDGE_KEY, str(cid)))),
                 "meals": len(settings.food_meals(cid))}
    trip = cid is not None and vacation.active(cid)
    return menu_ui.day_menu(
        menu, cuisine_label=menu.get("cuisine_label") or day_menu.cuisine_label(menu.get("cuisine")),
        # Описание кухни: дома — из списка кухонь; кухня страны отпуска — из гида поездки.
        intro=day_menu.CUISINE_INTRO.get(menu.get("cuisine"), "")
        or (vacation.local_cuisine_intro(cid) if cid is not None
            and str(menu.get("cuisine") or "").startswith(day_menu.LOCAL_PREFIX) else ""),
        flag=day_menu.cuisine_flag(menu.get("cuisine")),
        show_mine=cid is None or not vacation.active(cid), stats=stats, trip=trip,
        # В отпуске — одно блюдо, которое стоит попробовать, внизу курсивом — статистика поездки.
        trip_dish=day_menu.trip_dish(menu) if trip else None,
        trip_stats=_trip_food_stats(cid, menu) if trip else None,
    )


def _trip_food_stats(cid, menu) -> dict:
    """День поездки и сколько разных блюд кухни страны уже подобрано: {day, days, dishes}."""
    import datetime as _dt
    import config
    import vacation
    current = vacation.state(cid) or {}
    today = _dt.datetime.now(config.TZ).date()
    try:
        since = _dt.date.fromisoformat(current.get("since") or "")
    except ValueError:
        since = None
    try:
        until = _dt.date.fromisoformat(current.get("until") or "")
    except ValueError:
        until = None
    history = ((store.get_profile(cid) or {}).get("cooking_day_menu_history") or {}).get("by_cuisine") or {}
    shown = {str(name).casefold() for name in history.get(menu.get("cuisine"), []) if name}
    return {"day": (today - since).days + 1 if since else 0,
            "days": (until - since).days + 1 if since and until else 0,
            "dishes": len(shown)}


async def send_trip_dish(bot, cid, status=None, q=None):
    """«Другое блюдо» в отпуске: следующее блюдо набора, набор кончился — новый."""
    import asyncio
    import day_menu
    menu = await asyncio.to_thread(day_menu.next_trip_dish, cid)
    await _deliver(bot, cid, _day_menu_message(menu, cid), status, q)


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


async def send_mine_recipe(bot, cid, status=None, refresh=False, cuisine=None):
    """«Из моих продуктов»: рецепт только из отмеченных продуктов, приём пищи — по времени суток.
    cuisine — кухня из «Другого рецепта»; без неё — кухня меню дня."""
    import recipe_generation
    from datetime import datetime
    from fridge_model import _fridge_available
    import config
    import settings

    if not _fridge_available(store.get_list(config.FRIDGE_KEY, str(cid))):
        msg = menu_ui.fridge_empty_for_mine()
        await _deliver(bot, cid, msg, status)
        return

    async def build(status):
        import day_menu
        meal = recipe_generation._home_meal_for_hour(datetime.now(config.TZ).hour)
        # Кухня — как у меню дня на главном экране Готовки (кухня страны отпуска — без строгого выбора).
        known = cuisine in dict(settings.CUISINE_OPTIONS)  # кухня из callback — только из списка
        menu_cuisine = (cuisine if known else None) or (day_menu.get_cached_day_menu(cid) or {}).get("cuisine") or \
            day_menu.day_cuisine(cid, day_menu._today())
        picked = None if str(menu_cuisine).startswith(day_menu.LOCAL_PREFIX) else menu_cuisine
        idea = await asyncio.to_thread(recipe_generation.get_cooking_home_idea, cid, None, refresh, picked)
        msg = menu_ui.food_menu(idea, meal=meal, from_fridge=True)
        await status.replace(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)

    await _with_food_status(bot, cid, status, None, build)


async def send_food_menu(bot, cid, status=None, refresh=False, q=None, meal=None, cuisine=None):
    """Главный экран Готовки — меню на день; meal — полный рецепт блюда этого приёма пищи.

    Меню дня берётся из кэша мгновенно (ночной прогрев); без кэша или по
    «✨ Другое меню на сегодня» (refresh, cuisine) собирается один раз с индикатором.
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

    if not refresh:
        ready = day_menu.get_cached_day_menu(cid)
        if ready is not None:
            await _deliver(bot, cid, _day_menu_message(ready, cid), status, q)
            return

    async def build_menu(status):
        menu = await asyncio.to_thread(day_menu.get_day_menu, cid, None, refresh, cuisine)
        msg = _day_menu_message(menu, cid)
        await status.replace(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)

    await _with_food_status(bot, cid, status, q, build_menu)
