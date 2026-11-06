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


def test_fridge_uses_nine_product_type_categories_and_detects_examples():
    assert _CAT_ORDER == [
        "мясо и птица",
        "рыба и морепродукты",
        "колбасы и полуфабрикаты",
        "овощи и зелень",
        "фрукты и ягоды",
        "молочное и яйца",
        "крупы, макароны и хлеб",
        "соусы и специи",
        "сладкое и напитки",
    ]
    assert _fridge_detect_cat("куриная грудка") == "мясо и птица"
    assert _fridge_detect_cat("крабовые палочки") == "рыба и морепродукты"
    assert _fridge_detect_cat("куриные сосиски") == "колбасы и полуфабрикаты"  # не мясо
    assert _fridge_detect_cat("копчёная курица") == "колбасы и полуфабрикаты"
    assert _fridge_detect_cat("шампиньоны") == "овощи и зелень"
    assert _fridge_detect_cat("клубника") == "фрукты и ягоды"
    assert _fridge_detect_cat("апельсиновый сок") == "сладкое и напитки"
    assert _fridge_detect_cat("конфеты") == "сладкое и напитки"  # не «фета»
    assert _fridge_detect_cat("подсолнечное масло") == "соусы и специи"
    assert _fridge_detect_cat("сливочное масло") == "молочное и яйца"
    assert _fridge_detect_cat("паста том ям") == "соусы и специи"
    # Категория — по типу продукта, а не «заморозка»; замороженное без типа — полуфабрикаты.
    assert _fridge_detect_cat("замороженная рыба") == "рыба и морепродукты"
    assert _fridge_migrate(["мороженая рыба"])[0]["cat"] == "рыба и морепродукты"
    assert _fridge_detect_cat("замороженные овощи") == "овощи и зелень"
    assert _fridge_detect_cat("пельмени") == "колбасы и полуфабрикаты"
    assert _fridge_detect_cat("мороженое") == "сладкое и напитки"
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
        ("замороженная рыба", "рыба и морепродукты"),
        ("лосось", "рыба и морепродукты"),
        ("яблоки", "фрукты и ягоды"),
        ("молоко", "молочное и яйца"),
        ("томатная паста", "соусы и специи"),
    ]
    # Прежние шесть категорий: продукт — по типу, свой без типа — в ближайшую новую.
    old = _fridge_migrate([
        {"name": "креветки", "cat": "мясо и рыба", "on": True},
        {"name": "пельмени", "cat": "заморозка", "on": True},
        {"name": "дуриан", "cat": "овощи и фрукты", "on": True, "cat_manual": True},
        {"name": "бекон", "cat": "мясо и рыба", "on": False, "cat_manual": True, "base": True},
    ])
    assert {item["name"]: item["cat"] for item in old} == {
        "креветки": "рыба и морепродукты", "пельмени": "колбасы и полуфабрикаты", "дуриан": "овощи и зелень",
        "бекон": "колбасы и полуфабрикаты"}
    assert all(item["cat"] != "прочее" for item in migrated)


def test_fridge_home_has_available_counts_and_delete_before_navigation(monkeypatch):
    _memory_store(monkeypatch, [
        {"name": "курица", "cat": "мясо и птица", "on": True},
        {"name": "индейка", "cat": "мясо и птица", "on": False},
        {"name": "яблоки", "cat": "фрукты и ягоды", "on": True},
    ])
    bot = _Bot()

    asyncio.run(fridge.send_fridge(bot, "fridge-home"))

    message = bot.messages[-1]
    assert message["text"] == ("🎚️ Любимые продукты · 2\n\nОтмечай в категориях, что любишь и обычно покупаешь, — "
                               "рецепт «Из любимых продуктов» соберу из них.")
    assert _labels(message["reply_markup"]) == [
        ["✅ Добавить продукт"],
        ["Мясо и птица · 1"],
        ["Фрукты и ягоды · 1"],  # пустые категории не показываются
        ["📝 Выбрать предпочтения"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_fridge_category_uses_status_dots_without_delete(monkeypatch):
    _memory_store(monkeypatch, [
        {"name": "курица", "cat": "мясо и птица", "on": True},
        {"name": "индейка", "cat": "мясо и птица", "on": False},
    ])
    bot = _Bot()

    asyncio.run(fridge.send_fridge_cat(bot, "fridge-category", 0, 0))

    message = bot.messages[-1]
    assert message["text"] == "Мясо и птица · 1 из 2"  # любимых из всех в категории
    rows = _labels(message["reply_markup"])
    assert rows[:2] == [["✅ курица"], ["□ индейка"]]
    assert rows[-2] == ["✏️ Изменить"]
    assert rows[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_unknown_product_requires_one_of_nine_categories(monkeypatch):
    state = _memory_store(monkeypatch, [])
    fridge._pending_category_choices.clear()
    bot = _Bot()

    asyncio.run(fridge.fridge_add_done(bot, "fridge-unknown", "дуриан"))

    assert state == []
    assert _labels(bot.messages[-1]["reply_markup"])[:-1] == [
        ["Мясо и птица"],
        ["Рыба и морепродукты"],
        ["Колбасы и полуфабрикаты"],
        ["Овощи и зелень"],
        ["Фрукты и ягоды"],
        ["Молочное и яйца"],
        ["Крупы, макароны и хлеб"],
        ["Соусы и специи"],
        ["Сладкое и напитки"],
    ]

    asyncio.run(fridge.fridge_assign_category(bot, "fridge-unknown", 4))

    assert state == [{
        "name": "дуриан",
        "cat": "фрукты и ягоды",
        "cat_manual": True,
        "on": True,
    }]


def test_base_products_are_added_once_and_favorites_go_first(monkeypatch):
    from fridge_model import BASE_PRODUCTS
    profile = {}
    state = _memory_store(monkeypatch, [{"name": "креветки", "cat": "мясо и рыба", "on": True}], profile=profile)
    meat = len(BASE_PRODUCTS["мясо и птица"])
    bot = _Bot()

    asyncio.run(fridge.send_fridge(bot, "fridge-base"))  # первое открытие — базовый набор невыбранным
    total = sum(len(names) for names in BASE_PRODUCTS.values())
    assert len(state) == total and profile["fridge_base_v"] == fridge.BASE_VERSION  # креветки уже были в наборе
    assert sum(item["on"] for item in state) == 1
    assert bot.messages[-1]["text"].startswith("🎚️ Любимые продукты · 1\n")
    assert _labels(bot.messages[-1]["reply_markup"])[1:3] == [["Мясо и птица · 0"], ["Рыба и морепродукты · 1"]]

    asyncio.run(fridge.send_fridge_cat(bot, "fridge-base", 1, 0))
    fish = len(BASE_PRODUCTS["рыба и морепродукты"])
    assert bot.messages[-1]["text"] == f"Рыба и морепродукты · 1 из {fish}"
    assert _labels(bot.messages[-1]["reply_markup"])[:2] == [["✅ креветки"], ["□ кальмары"]]  # любимые — первыми
    asyncio.run(fridge.send_fridge_cat(bot, "fridge-base", 0, 0))
    assert bot.messages[-1]["text"] == f"Мясо и птица · 0 из {meat}"

    state[:] = [item for item in state if item["name"] != "кальмары"]  # удалённое из набора не возвращается
    asyncio.run(fridge.send_fridge(bot, "fridge-base"))
    assert "кальмары" not in {item["name"] for item in state}

    asyncio.run(fridge.fridge_add_done(bot, "fridge-base", "курица, дуриан"))  # «добавь» — отметить любимым
    assert next(item for item in state if item["name"] == "курица")["on"] is True
