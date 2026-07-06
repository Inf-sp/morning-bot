import asyncio

import pytest

import balance


def _fake_recipes(n=10, cuisine="italian", prefix="Рецепт"):
    return [
        {
            "name": f"{prefix} {i}",
            "cuisine": cuisine,
            "cuisine_emoji": "🇮🇹",
            "photo_query": "pasta italian food",
            "main_ingredients_en": "pasta, cheese",
            "dish_type_en": "pasta",
            "meal_type_en": "dinner",
            "ingredients": "паста, сыр",
            "chef_tip": "совет",
            "steps": [{"text": "Шаг", "minutes": 5}],
            "time": "10 мин",
            "servings": "1",
        }
        for i in range(n)
    ]


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kw):
        self.sent.append(("message", kw))

    async def send_photo(self, **kw):
        self.sent.append(("photo", kw))


def _run(coro):
    return asyncio.run(coro)


# ---------- active_meal / очередь: базовая FSM (§4.1, §4.2, §6.1) ----------

@pytest.mark.unit
def test_active_meal_roundtrip_and_clear():
    cid = "food-active-meal-1"
    assert balance.get_active_meal(cid) is None
    balance.set_active_meal(cid, "breakfast")
    assert balance.get_active_meal(cid) == "breakfast"
    balance.clear_active_meal(cid)
    assert balance.get_active_meal(cid) is None


@pytest.mark.unit
def test_set_active_meal_rejects_unknown_value():
    with pytest.raises(ValueError):
        balance.set_active_meal("food-active-meal-2", "brunch")


@pytest.mark.unit
def test_queue_next_returns_items_in_order_then_none():
    cid = "food-queue-1"
    balance.set_recipe_queue(cid, "lunch", [{"name": "A"}, {"name": "B"}], pos=0)
    assert balance.queue_next(cid) == {"name": "A"}
    assert balance.queue_next(cid) == {"name": "B"}
    assert balance.queue_next(cid) is None


@pytest.mark.unit
def test_enter_meal_generates_queue_and_shows_first_recipe(monkeypatch):
    cid = "food-enter-1"
    bot = FakeBot()
    batch = _fake_recipes()
    monkeypatch.setattr(balance, "_gen_recipe_batch", lambda *a, **kw: batch)
    monkeypatch.setattr(balance.photo_provider, "get_dish_photo", lambda *a, **kw: None)

    _run(balance.enter_meal(bot, cid, "breakfast"))

    assert balance.get_active_meal(cid) == "breakfast"
    q = balance.get_recipe_queue(cid)
    assert q["meal"] == "breakfast"
    assert q["pos"] == 1  # первый рецепт уже показан
    assert len(bot.sent) == 1


@pytest.mark.unit
def test_show_next_recipe_stays_within_active_category(monkeypatch):
    """Регрессия на баг из ТЗ п.1: «Ещё рецепт» после «Завтрака» не должен уехать в другую категорию."""
    cid = "food-stay-1"
    bot = FakeBot()
    batch = _fake_recipes()
    monkeypatch.setattr(balance, "_gen_recipe_batch", lambda *a, **kw: batch)
    monkeypatch.setattr(balance.photo_provider, "get_dish_photo", lambda *a, **kw: None)

    _run(balance.enter_meal(bot, cid, "breakfast"))
    for _ in range(3):
        _run(balance.show_next_recipe(bot, cid))

    assert balance.get_active_meal(cid) == "breakfast"
    assert balance.get_recipe_queue(cid)["meal"] == "breakfast"


@pytest.mark.unit
def test_show_next_recipe_regenerates_queue_when_exhausted(monkeypatch):
    cid = "food-exhaust-1"
    bot = FakeBot()
    calls = {"n": 0}

    def gen(*a, **kw):
        calls["n"] += 1
        return _fake_recipes(prefix=f"batch{calls['n']}")

    monkeypatch.setattr(balance, "_gen_recipe_batch", gen)
    monkeypatch.setattr(balance.photo_provider, "get_dish_photo", lambda *a, **kw: None)

    _run(balance.enter_meal(bot, cid, "dinner"))
    assert calls["n"] == 1
    for _ in range(9):  # исчерпываем оставшиеся 9 из батча в 10
        _run(balance.show_next_recipe(bot, cid))
    assert calls["n"] == 1
    _run(balance.show_next_recipe(bot, cid))  # 10-й next -> новая генерация
    assert calls["n"] == 2


@pytest.mark.unit
def test_show_next_recipe_without_active_meal_returns_to_menu(monkeypatch):
    cid = "food-no-active-1"
    bot = FakeBot()
    sent_to_menu = {}

    async def fake_send_food_menu(bot, cid):
        sent_to_menu["called"] = True

    monkeypatch.setattr(balance.menu, "send_food_menu", fake_send_food_menu)

    _run(balance.show_next_recipe(bot, cid))

    assert sent_to_menu.get("called") is True


@pytest.mark.unit
def test_back_to_food_menu_clears_state_and_shows_menu(monkeypatch):
    cid = "food-back-1"
    bot = FakeBot()
    balance.set_active_meal(cid, "lunch")
    balance.set_recipe_queue(cid, "lunch", [{"name": "A"}])
    called = {}

    async def fake_send_food_menu(bot, cid):
        called["yes"] = True

    monkeypatch.setattr(balance.menu, "send_food_menu", fake_send_food_menu)

    _run(balance.back_to_food_menu(bot, cid))

    assert balance.get_active_meal(cid) is None
    assert balance.get_recipe_queue(cid) == {}
    assert called.get("yes") is True


# ---------- история рекомендаций: анти-повтор (§4.3) ----------

@pytest.mark.unit
def test_recipe_history_dedupes_case_insensitively():
    cid = "food-history-1"
    balance.add_to_recipe_history(cid, ["Шакшука", "Омлет"])
    balance.add_to_recipe_history(cid, ["шакшука", "Тамаго"])
    history = balance.get_recipe_history(cid)
    assert history.count("Шакшука") == 1
    assert "шакшука" not in [h for h in history if h != "Шакшука"]
    assert history == ["Шакшука", "Омлет", "Тамаго"]


@pytest.mark.unit
def test_recipe_history_caps_at_limit():
    cid = "food-history-2"
    names = [f"Блюдо {i}" for i in range(balance.RECIPE_HISTORY_LIMIT + 20)]
    balance.add_to_recipe_history(cid, names)
    history = balance.get_recipe_history(cid)
    assert len(history) == balance.RECIPE_HISTORY_LIMIT
    assert history[-1] == names[-1]


@pytest.mark.unit
def test_enter_meal_appends_batch_names_to_history(monkeypatch):
    cid = "food-history-3"
    bot = FakeBot()
    batch = _fake_recipes(n=5)
    monkeypatch.setattr(balance, "_gen_recipe_batch", lambda *a, **kw: batch)
    monkeypatch.setattr(balance.photo_provider, "get_dish_photo", lambda *a, **kw: None)

    _run(balance.enter_meal(bot, cid, "breakfast"))

    history = balance.get_recipe_history(cid)
    assert {r["name"] for r in batch} <= set(history)


# ---------- веса кухонь: обучение на действиях (§4.4, §12) ----------

@pytest.mark.unit
def test_bump_cuisine_weight_clamps_to_bounds():
    cid = "food-weights-1"
    for _ in range(30):
        balance.bump_cuisine_weight(cid, "italian", 1)
    assert balance.get_cuisine_weights(cid)["italian"] == balance.CUISINE_WEIGHT_MAX
    for _ in range(30):
        balance.bump_cuisine_weight(cid, "italian", -1)
    assert balance.get_cuisine_weights(cid)["italian"] == balance.CUISINE_WEIGHT_MIN


@pytest.mark.unit
def test_save_my_recipe_increases_cuisine_weight(monkeypatch):
    cid = "food-weights-2"
    bot = FakeBot()
    import store
    store.last_recipe[cid] = {"name": "Паста Карбонара", "cuisine": "italian"}

    _run(balance.save_my_recipe(bot, cid))

    assert balance.get_cuisine_weights(cid).get("italian") == 1


@pytest.mark.unit
def test_show_next_recipe_decreases_previous_cuisine_weight_on_replace(monkeypatch):
    cid = "food-weights-3"
    bot = FakeBot()
    batch = _fake_recipes(cuisine="japanese")
    monkeypatch.setattr(balance, "_gen_recipe_batch", lambda *a, **kw: batch)
    monkeypatch.setattr(balance.photo_provider, "get_dish_photo", lambda *a, **kw: None)

    _run(balance.enter_meal(bot, cid, "dinner"))
    assert balance.get_cuisine_weights(cid).get("japanese", 0) == 0

    _run(balance.show_next_recipe(bot, cid))
    assert balance.get_cuisine_weights(cid).get("japanese") == -1
