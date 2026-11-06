import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import fridge
from fridge_model import _CAT_ORDER, _fridge_detect_cat, _fridge_migrate


class _Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _memory_store(monkeypatch, initial, profile=None):
    """profile=None — базовый набор уже добавлен (как у существующих пользователей); {} — первое открытие."""
    async def no_cooking_home(*_args, **_kwargs):
        return None

    saved_profile = {"fridge_base_v": fridge.BASE_VERSION} if profile is None else profile
    monkeypatch.setattr(fridge.store, "get_profile", lambda _cid: dict(saved_profile))
    monkeypatch.setattr(fridge.store, "mutate_profile",
                        lambda _cid, change: saved_profile.update(change(dict(saved_profile))[0]))

    # Переход в «Готовку» собирает настоящий рецепт — здесь он не нужен.
    monkeypatch.setattr(fridge, "_send_cooking_home", no_cooking_home)
    state = list(initial)
    monkeypatch.setattr(fridge.store, "get_list", lambda *_args: list(state))

    def set_list(_key, _cid, value):
        state[:] = value

    monkeypatch.setattr(fridge.store, "set_list", set_list)
    return state


def test_fridge_uses_six_categories_and_detects_required_examples():
    assert _CAT_ORDER == [
        "мясо и рыба",
        "овощи и фрукты",
        "молочное и напитки",
        "бакалея",
        "специи и соусы",
        "заморозка",
    ]
    assert _fridge_detect_cat("куриная грудка") == "мясо и рыба"
    assert _fridge_detect_cat("шампиньоны") == "овощи и фрукты"
    assert _fridge_detect_cat("апельсиновый сок") == "молочное и напитки"
    assert _fridge_detect_cat("подсолнечное масло") == "бакалея"
    assert _fridge_detect_cat("сливочное масло") == "бакалея"
    assert _fridge_detect_cat("паста том ям") == "специи и соусы"
    assert _fridge_detect_cat("замороженная рыба") == "заморозка"
    assert _fridge_migrate(["мороженая рыба"])[0]["cat"] == "заморозка"
    assert _fridge_detect_cat("дуриан") is None


def test_fridge_migration_removes_other_and_sorts_database_records():
    migrated = _fridge_migrate([
        {"name": "томатная паста", "cat": "крупы и макароны", "on": True},
        {"name": "яблоки", "cat": "фрукты", "on": True},
        {"name": "лосось", "cat": "прочее", "on": False},
        {"name": "молоко", "cat": "молочное и яйца", "on": True},
        {"name": "замороженная рыба", "cat": "рыба", "on": True},
    ])

    assert [(item["name"], item["cat"]) for item in migrated] == [
        ("лосось", "мясо и рыба"),
        ("яблоки", "овощи и фрукты"),
        ("молоко", "молочное и напитки"),
        ("томатная паста", "специи и соусы"),
        ("замороженная рыба", "заморозка"),
    ]
    assert all(item["cat"] != "прочее" for item in migrated)


def test_fridge_home_has_available_counts_and_delete_before_navigation(monkeypatch):
    _memory_store(monkeypatch, [
        {"name": "курица", "cat": "мясо и рыба", "on": True},
        {"name": "лосось", "cat": "мясо и рыба", "on": False},
        {"name": "яблоки", "cat": "овощи и фрукты", "on": True},
    ])
    bot = _Bot()

    asyncio.run(fridge.send_fridge(bot, "fridge-home"))

    message = bot.messages[-1]
    assert message["text"] == ("🎚️ Любимые продукты · 2\n\nОтмечай в категориях, что любишь и обычно покупаешь, — "
                               "рецепт «Из любимых продуктов» соберу из них.")
    assert _labels(message["reply_markup"]) == [
        ["✅ Добавить продукт"],
        ["Мясо и рыба · 1"],
        ["Овощи и фрукты · 1"],  # пустые категории не показываются
        ["📝 Выбрать предпочтения"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_fridge_category_uses_status_dots_without_delete(monkeypatch):
    _memory_store(monkeypatch, [
        {"name": "курица", "cat": "мясо и рыба", "on": True},
        {"name": "лосось", "cat": "мясо и рыба", "on": False},
    ])
    bot = _Bot()

    asyncio.run(fridge.send_fridge_cat(bot, "fridge-category", 0, 0))

    message = bot.messages[-1]
    assert message["text"] == "Мясо и рыба · 1 из 2"  # любимых из всех в категории
    rows = _labels(message["reply_markup"])
    assert rows[:2] == [["✅ курица"], ["□ лосось"]]
    assert rows[-2] == ["✏️ Изменить"]
    assert rows[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_unknown_product_requires_one_of_six_categories(monkeypatch):
    state = _memory_store(monkeypatch, [])
    fridge._pending_category_choices.clear()
    bot = _Bot()

    asyncio.run(fridge.fridge_add_done(bot, "fridge-unknown", "дуриан"))

    assert state == []
    assert _labels(bot.messages[-1]["reply_markup"])[:-1] == [
        ["Мясо и рыба"],
        ["Овощи и фрукты"],
        ["Молочное и напитки"],
        ["Бакалея"],
        ["Специи и соусы"],
        ["Заморозка"],
    ]

    asyncio.run(fridge.fridge_assign_category(bot, "fridge-unknown", 1))

    assert state == [{
        "name": "дуриан",
        "cat": "овощи и фрукты",
        "cat_manual": True,
        "on": True,
    }]


def test_base_products_are_added_once_and_favorites_go_first(monkeypatch):
    from fridge_model import BASE_PRODUCTS
    profile = {}
    state = _memory_store(monkeypatch, [{"name": "креветки", "cat": "мясо и рыба", "on": True}], profile=profile)
    bot = _Bot()

    asyncio.run(fridge.send_fridge(bot, "fridge-base"))  # первое открытие — базовый набор невыбранным
    total = sum(len(names) for names in BASE_PRODUCTS.values())
    assert len(state) == total and profile["fridge_base_v"] == fridge.BASE_VERSION  # креветки уже были в наборе
    assert sum(item["on"] for item in state) == 1
    assert bot.messages[-1]["text"].startswith("🎚️ Любимые продукты · 1\n")
    assert _labels(bot.messages[-1]["reply_markup"])[1] == ["Мясо и рыба · 1"]

    asyncio.run(fridge.send_fridge_cat(bot, "fridge-base", 0, 0))
    assert bot.messages[-1]["text"] == f"Мясо и рыба · 1 из {len(BASE_PRODUCTS['мясо и рыба'])}"
    assert _labels(bot.messages[-1]["reply_markup"])[:2] == [["✅ креветки"], ["□ бекон"]]  # любимые — первыми

    state[:] = [item for item in state if item["name"] != "бекон"]  # удалённое из набора не возвращается
    asyncio.run(fridge.send_fridge(bot, "fridge-base"))
    assert "бекон" not in {item["name"] for item in state}

    asyncio.run(fridge.fridge_add_done(bot, "fridge-base", "курица, дуриан"))  # «добавь» — отметить любимым
    assert next(item for item in state if item["name"] == "курица")["on"] is True
