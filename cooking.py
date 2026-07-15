import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store

_log = logging.getLogger(__name__)
import rerank
import util
import verify
from ui import balance as balance_ui
from ui import food as food_ui
from ui.constants import ui_label
import menu
from response_delivery import (
    answer_keyboard as _ans_kb,
    back_keyboard as _back_kb,
    build_entity_card as _build_entity_card,
    clean_card_text as _clean_card_text,
    keyboard as _kb,
    send_response as _send,
)
from recipe_state import (
    MEAL_CHOICES,
    _leftover_recent,
    _leftover_remember,
    _persist_current_queue_recipe,
    add_to_recipe_history,
    bump_cuisine_weight,
    clear_active_meal,
    clear_recipe_queue,
    get_active_meal,
    get_cuisine_weights,
    get_recipe_history,
    get_recipe_queue,
    queue_next,
    set_active_meal,
    set_recipe_queue,
)
from recipe_generation import (
    RECIPE_BATCH_SIZE,
    RECIPE_CUISINE_EMOJI_FALLBACK,
    _gen_leftovers_recipe,
    _gen_leftovers_recipe_batch,
    _gen_recipe,
    _gen_recipe_batch,
    _season_hint,
)


def _food_card(d, label="Рецепт дня"):
    """Единый формат карточки рецепта для радара и нового рецепта."""
    return food_ui.food_card(d, label=label)

def _finish_dot(value):
    return balance_ui.finish_dot(value)

def _recipe_kb():
    """Единая клавиатура карточки рецепта для всех 4 категорий (§6.2 спеки).

    «Ещё рецепт» (as_food) и «Назад» (as_food_back) работают в рамках активной
    категории (balance.get_active_meal) — см. handle_callback. «Назад» — отдельный
    callback, а не общий m_close, чтобы не задевать другие разделы, которые тоже
    используют m_close для закрытия карточки без возврата в конкретное меню."""
    return _kb([
        [("✨ Ещё рецепт", "as_food")],
        [("❤️ Сохранить", "as_recipe_save")],
        [("⬅️ Назад", "as_food_back"), ("#️⃣ Меню", "m_menu")],
    ])

def _recipe_typed_kb():
    """Клавиатура после «рецепта дня» (send_recipe_featured, вне категорий очереди) —
    выбор типа приёма пищи; нажатие уводит в новую систему очередей через enter_meal."""
    return _kb([
        [("🥐 Завтрак", "a_recipe_breakfast"), ("🥗 Обед", "a_recipe_lunch"), ("🍲 Ужин", "a_recipe_dinner")],
        [("⬅️ Назад", "m_food"), ("#️⃣ Меню", "m_menu")],
    ])

def _fridge_recipe_kb():
    """Клавиатура после рецепта из холодильника через путь чата (send_leftovers/
    assistant.py) — не через кнопки категории «Готовка» (там используется _recipe_kb
    через enter_meal/show_next_recipe). «Заменить» переиспользует as_fridge_cook,
    который теперь тоже заводит активную категорию fridge и общую очередь."""
    return _kb([
        [("✨ Заменить", "as_fridge_cook")],
        [("⬅️ Назад", "m_food"), ("#️⃣ Меню", "m_menu")],
    ])

# ---------- Кулинарный радар ----------

def _recipe_card(d):
    return _food_card(d, label="Рецепт дня")



async def send_recipe(bot, cid, constraint="обычное блюдо", status=None):
    status = status or await util.StatusManager.start(bot, cid)
    try:
        d = await asyncio.to_thread(_gen_recipe, constraint, cid=cid)
    except Exception as e:
        await status.stop(delete=True)
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", constraint)
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card.text
    await status.replace(card.text, entities=card.entities, reply_markup=_recipe_kb())


# ---------- Готовка: единая навигация по категориям (§6 спеки) ----------
_MEAL_CONSTRAINT = {
    "breakfast": "завтрак",
    "lunch": "обед",
    "dinner": "ужин",
}

# Явный запрет на типичные блюда других приёмов пищи — чтобы летняя/сезонная
# подсказка (например "гриль") не перетягивала завтрак в сторону обеда/ужина.
_MEAL_GUARD = {
    "breakfast": (
        "Это ЗАВТРАК: лёгкие блюда, которые едят с утра (каша, омлет, тосты, сырники, "
        "йогурт с добавками, блинчики, творог). ЗАПРЕЩЕНО предлагать блюда для обеда/ужина "
        "(жаркое, гриль из мяса, стейк, наваристые супы, плов) — даже если сезонная "
        "подсказка выше советует лёгкие летние блюда, это НЕ повод предложить гриль на завтрак."
    ),
    "lunch": "Это ОБЕД: основное блюдо сытнее завтрака — суп, горячее с гарниром, боул.",
    "dinner": "Это УЖИН: сытное, но не тяжёлое перед сном блюдо.",
}


async def _send_queue_card(bot, cid, meal, d, status=None):
    """Отправляет карточку ОДНОГО показываемого рецепта без фото."""
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe_queue", meal)
    store.last_source[str(cid)] = "Питание · Рецепт"
    label = food_ui.MEAL_LABEL.get(meal, "Рецепт")
    card = food_ui.food_card(d, label=label, meal=meal, cuisine_emoji_fallback=RECIPE_CUISINE_EMOJI_FALLBACK)
    store.last_answer[str(cid)] = card.text
    kb = _recipe_kb()
    _persist_current_queue_recipe(cid, d)
    _log.info("_send_queue_card: meal=%s cid=%s status_mode=%s text_len=%s",
              meal, cid, getattr(status, "mode", None), len(card.text or ""))
    if status is not None:
        await status.stop(delete=False)
    try:
        msg = await bot.send_message(chat_id=cid, text=card.text, entities=card.entities, reply_markup=kb)
    except Exception as e:
        _log.error("_send_queue_card: send_message FAILED cid=%s meal=%s: %r\ncard.text=%r",
                   cid, meal, e, card.text, exc_info=True)
        raise
    _log.info("_send_queue_card: sent message_id=%s cid=%s", msg.message_id, cid)


async def _generate_and_store_queue(cid, meal, ingredients=None):
    """Генерирует новую очередь ~10 рецептов для категории meal и сохраняет её (§4.2/§5).

    ТОЛЬКО текстовые поля рецепта, без единого сетевого вызова к Pexels."""
    cuisine_weights = get_cuisine_weights(cid)
    recent_history = get_recipe_history(cid)
    season_hint = _season_hint()
    if meal == "fridge":
        items = await asyncio.to_thread(
            _gen_leftovers_recipe_batch, ingredients or "", cid,
            cuisine_weights, recent_history, season_hint)
    else:
        constraint = _MEAL_CONSTRAINT.get(meal, "обычное блюдо")
        meal_guard = _MEAL_GUARD.get(meal, "")
        items = await asyncio.to_thread(
            _gen_recipe_batch, constraint, cid,
            cuisine_weights, recent_history, season_hint, RECIPE_BATCH_SIZE, meal_guard)
    if items:
        set_recipe_queue(cid, meal, items, pos=0)
        add_to_recipe_history(cid, [it.get("name", "") for it in items if it.get("name")])
    return items


async def enter_meal(bot, cid, meal, ingredients=None, status=None):
    """Явный вход в категорию из меню «Готовка» (§6.1): фиксирует active_meal,
    генерирует очередь при необходимости и показывает первый рецепт."""
    set_active_meal(cid, meal)
    q = get_recipe_queue(cid)
    if q.get("meal") != meal or not q.get("items"):
        if status is None:
            status = await util.StatusManager.start(bot, cid)
        try:
            items = await _generate_and_store_queue(cid, meal, ingredients)
        except Exception as e:
            await status.stop(delete=True)
            await verify.safe_error(bot, cid, e); return
        if not items:
            await status.replace("Не получилось придумать рецепты, попробуй ещё раз.")
            return
    d = queue_next(cid)
    if d is None:
        if status is not None:
            await status.replace("Не получилось придумать рецепты, попробуй ещё раз.")
        else:
            await bot.send_message(chat_id=cid, text="Не получилось придумать рецепты, попробуй ещё раз.")
        return
    await _send_queue_card(bot, cid, meal, d, status=status)


async def show_next_recipe(bot, cid, status=None):
    """«Ещё рецепт» (as_food): показывает следующий рецепт активной категории (§6.1).

    Категория берётся из active_meal — не из текста кнопки, поэтому «Ещё рецепт»
    физически не может перепрыгнуть в другую категорию (фикс бага из ТЗ п.1)."""
    meal = get_active_meal(cid)
    if not meal:
        # активная категория не выбрана (например, состояние потеряно) — просим
        # выбрать категорию явно, вместо того чтобы угадывать одну из четырёх.
        await menu.send_food_menu(bot, cid)
        return
    ingredients = None
    if meal == "fridge":
        raw = store.get_list(config.FRIDGE_KEY, str(cid))
        available = _fridge_available(raw)
        if not available:
            msg = food_ui.fridge_empty_for_recipe()
            await bot.send_message(chat_id=cid, text=msg.text)
            return
        ingredients = ", ".join(available)
    prev = store.last_recipe.get(str(cid)) or {}
    prev_cuisine = prev.get("cuisine")
    if prev_cuisine:
        bump_cuisine_weight(cid, prev_cuisine, -1)
    d = queue_next(cid)
    if d is None:
        if status is None:
            status = await util.StatusManager.start(bot, cid)
        try:
            items = await _generate_and_store_queue(cid, meal, ingredients)
        except Exception as e:
            await status.stop(delete=True)
            await verify.safe_error(bot, cid, e); return
        if not items:
            await status.replace("Не получилось придумать рецепты, попробуй ещё раз.")
            return
        d = queue_next(cid)
        if d is None:
            await status.replace("Не получилось придумать рецепты, попробуй ещё раз.")
            return
    await _send_queue_card(bot, cid, meal, d, status=status)


async def back_to_food_menu(bot, cid):
    """«Назад» из карточки рецепта (§2 спеки): возврат в меню «Готовка» вместо «Готово.»,
    со сбросом активной категории и очереди — новый явный выбор категории обязателен."""
    _log.info("back_to_food_menu: cid=%s", cid)
    clear_active_meal(cid)
    clear_recipe_queue(cid)
    await menu.send_food_menu(bot, cid)

async def send_recipe_featured(bot, cid, status=None):
    """Новый рецепт из меню — под результатом кнопки завтрак/обед/ужин."""
    status = status or await util.StatusManager.start(bot, cid)
    try:
        d = await asyncio.to_thread(_gen_recipe, "любое блюдо под вкус пользователя", cid=cid)
    except Exception as e:
        await status.stop(delete=True)
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", "featured")
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card.text
    await status.replace(card.text, entities=card.entities, reply_markup=_recipe_typed_kb())



async def send_leftovers(bot, cid, ingredients, status=None):
    status = status or await util.StatusManager.start(bot, cid)
    try:
        d = await asyncio.to_thread(_gen_leftovers_recipe, ingredients, cid)
    except Exception as e:
        await status.stop(delete=True)
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("leftovers", ingredients)
    _leftover_remember(cid, d.get("name", ""))
    card = _food_card(d, label="Рецепт из холодильника")
    store.last_source[str(cid)] = "Питание · Остатки"
    store.last_answer[str(cid)] = card.text
    await status.replace(card.text, entities=card.entities, reply_markup=_fridge_recipe_kb())


async def handle_callback(bot, cid, q, data):
    """Обрабатывает кулинарные callback-и. Возвращает True при совпадении."""
    import fridge as fridge_flow
    import saved_recipes
    if data == "as_food":
        status = await util.StatusManager.start_inline(
            q, bot=bot, cid=cid, stages=util.StatusManager.TOPIC_STAGES["food"])
        try:
            await show_next_recipe(bot, cid, status=status)
        except Exception as error:
            await verify.safe_error(bot, cid, error)
        finally:
            await status.stop(delete=False)
        return True
    if data == "as_food_back":
        await back_to_food_menu(bot, cid)
        return True
    if data in ("as_fridge", "as_fridge_home"):
        await fridge_flow.send_fridge(bot, cid, q)
        return True
    if data.startswith("as_fridge_cat_"):
        parts = data.split("_")
        try:
            await fridge_flow.send_fridge_cat(bot, cid, int(parts[3]), int(parts[4]), q)
        except (ValueError, IndexError):
            await fridge_flow.send_fridge(bot, cid, q)
        return True
    if data == "as_fridge_add":
        store.pending_input[str(cid)] = "fridge_add_-1"
        await bot.send_message(
            chat_id=cid,
            text="✏️ Напиши продукты через запятую или с новой строки — добавлю в список.",
            reply_markup=_back_kb(),
        )
        return True
    if data.startswith("as_fridge_add_"):
        try:
            category = int(data.split("_")[-1])
        except (ValueError, IndexError):
            category = -1
        store.pending_input[str(cid)] = f"fridge_add_{category}"
        await bot.send_message(
            chat_id=cid,
            text="✏️ Напиши продукты через запятую или с новой строки — добавлю в список.",
            reply_markup=_back_kb(),
        )
        return True
    if data == "as_fridge_cook":
        status = await util.StatusManager.start_inline(
            q, bot=bot, cid=cid, stages=util.StatusManager.TOPIC_STAGES["food"])
        try:
            available = _fridge_available(store.get_list(config.FRIDGE_KEY, str(cid)))
            if not available:
                message = food_ui.fridge_empty_for_recipe()
                await bot.send_message(chat_id=cid, text=message.text)
            else:
                await enter_meal(bot, cid, "fridge", ", ".join(available), status=status)
        except Exception as error:
            await verify.safe_error(bot, cid, error)
        finally:
            await status.stop(delete=False)
        return True
    if data == "as_fridge_clean":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "fridge")
        return True
    if data.startswith(("as_fridge_tgl_", "as_fridge_del_")):
        parts = data.split("_")
        try:
            function = fridge_flow.fridge_toggle if data.startswith("as_fridge_tgl_") else fridge_flow.fridge_del
            await function(bot, cid, int(parts[3]), int(parts[4]), int(parts[5]), q)
        except (ValueError, IndexError):
            await fridge_flow.send_fridge(bot, cid, q)
        return True
    if data == "as_recipe_save":
        await saved_recipes.save_my_recipe(bot, cid)
        return True
    if data == "as_recipe_clean":
        import cleanup
        await cleanup.open_cleanup(bot, cid, "recipes")
        return True
    if data == "as_my_recipes":
        await saved_recipes.send_my_recipes(bot, cid)
        return True
    if data.startswith("as_my_recipe_del_"):
        try:
            await saved_recipes.my_recipe_del(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return True
    if data.startswith("as_my_recipe_"):
        try:
            await saved_recipes.send_my_recipe_full(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return True
    return False


async def retry_last_action(bot, cid, status=None):
    action = store.last_action.get(str(cid))
    if action and action[0] == "recipe":
        await send_recipe(bot, cid, action[1], status=status)
        return True
    if action and action[0] == "leftovers":
        await send_leftovers(bot, cid, action[1], status=status)
        return True
    return False
