"""Персистентное состояние рецептов: очередь, история и предпочтения кухонь."""

import config
import store

_LEFTOVER_RECENT_LIMIT = 12

def _leftover_recent(cid):
    """Последние названия блюд из остатков — для anti-repeat в промпте."""
    return store.get_list(config.LEFTOVER_RECIPES_SEEN_KEY, cid)

def _leftover_remember(cid, name):
    """Добавляет название в историю anti-repeat, храня не больше _LEFTOVER_RECENT_LIMIT штук."""
    if not name:
        return
    recent = _leftover_recent(cid)
    recent = [n for n in recent if n.lower() != name.lower()] + [name]
    store.set_list(config.LEFTOVER_RECIPES_SEEN_KEY, cid, recent[-_LEFTOVER_RECENT_LIMIT:])


# ---------- Готовка: активная категория, очередь, история, веса кухонь (§4, §6.1 спеки) ----------
MEAL_CHOICES = ("breakfast", "lunch", "dinner", "fridge")

RECIPE_HISTORY_LIMIT = 100
CUISINE_WEIGHT_MIN = -5
CUISINE_WEIGHT_MAX = 10


def get_active_meal(cid):
    """Текущая активная категория «Готовки» или None, если не выбрана."""
    return store._load(config.ACTIVE_MEAL_KEY).get(str(cid))


def set_active_meal(cid, meal):
    """Записывает активную категорию. meal — один из MEAL_CHOICES."""
    if meal not in MEAL_CHOICES:
        raise ValueError(f"unknown meal: {meal!r}, expected one of {MEAL_CHOICES}")
    def change(data):
        data[str(cid)] = meal
        return data, None

    store.mutate_kv(config.ACTIVE_MEAL_KEY, change)


def clear_active_meal(cid):
    """Удаляет активную категорию (возврат в меню «Готовка» через «Назад»)."""
    def change(data):
        data.pop(str(cid), None)
        return data, None

    store.mutate_kv(config.ACTIVE_MEAL_KEY, change)


def get_recipe_queue(cid):
    """Очередь рецептов текущего пользователя: {"meal":..., "items":[...], "pos": int} или {}."""
    return store._load(config.RECIPE_QUEUE_KEY).get(str(cid), {})


def set_recipe_queue(cid, meal, items, pos=0):
    """Записывает очередь целиком (обычно после генерации батча ~10 рецептов)."""
    def change(data):
        data[str(cid)] = {"meal": meal, "items": list(items), "pos": pos}
        return data, None

    store.mutate_kv(config.RECIPE_QUEUE_KEY, change)


def clear_recipe_queue(cid):
    """Удаляет очередь текущего пользователя (например, вместе с active_meal при «Назад»)."""
    def change(data):
        data.pop(str(cid), None)
        return data, None

    store.mutate_kv(config.RECIPE_QUEUE_KEY, change)


def queue_next(cid):
    """Возвращает следующий рецепт активной очереди, инкрементируя pos.

    None, если очередь пуста или уже пройдена целиком — вызывающий код должен
    сгенерировать новую очередь для той же категории (генерация вне этого модуля).
    """
    def change(data):
        queue = data.get(str(cid)) or {}
        items = queue.get("items") or []
        pos = queue.get("pos", 0)
        if not items or pos >= len(items):
            return data, None
        item = items[pos]
        queue["pos"] = pos + 1
        data[str(cid)] = queue
        return data, item

    return store.mutate_kv(config.RECIPE_QUEUE_KEY, change)


def _persist_current_queue_recipe(cid, recipe):
    """Сохраняет изменения показанного рецепта (например photo_* поля) в очередь."""
    def change(data):
        queue = data.get(str(cid)) or {}
        items = queue.get("items") or []
        idx = int(queue.get("pos", 0)) - 1
        if 0 <= idx < len(items):
            items[idx] = recipe
            data[str(cid)] = queue
        return data, None

    store.mutate_kv(config.RECIPE_QUEUE_KEY, change)


def get_recipe_history(cid):
    """Последние показанные названия рецептов (общая история, не по категориям)."""
    return store.get_list(config.RECIPE_HISTORY_KEY, cid)


def add_to_recipe_history(cid, names: list):
    """Добавляет названия в общую историю рекомендаций, FIFO максимум RECIPE_HISTORY_LIMIT."""
    if not names:
        return
    history = get_recipe_history(cid)
    seen_lower = {n.lower() for n in history}
    for name in names:
        if not name or name.lower() in seen_lower:
            continue
        history.append(name)
        seen_lower.add(name.lower())
    store.set_list(config.RECIPE_HISTORY_KEY, cid, history[-RECIPE_HISTORY_LIMIT:])


def get_cuisine_weights(cid):
    """Веса кухонь пользователя: {"italian": 3, "japanese": -1, ...}."""
    return store._load(config.CUISINE_WEIGHTS_KEY).get(str(cid), {})


def bump_cuisine_weight(cid, cuisine, delta):
    """Изменяет вес кухни на delta, с clamp в [CUISINE_WEIGHT_MIN, CUISINE_WEIGHT_MAX]."""
    if not cuisine:
        return
    def change(data):
        weights = data.setdefault(str(cid), {})
        new_value = weights.get(cuisine, 0) + delta
        weights[cuisine] = max(CUISINE_WEIGHT_MIN, min(CUISINE_WEIGHT_MAX, new_value))
        return data, None

    store.mutate_kv(config.CUISINE_WEIGHTS_KEY, change)

