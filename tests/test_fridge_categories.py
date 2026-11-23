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


def test_fridge_uses_eight_cooking_categories_and_detects_examples():
    assert _CAT_ORDER == [
        "мясо и птица",
        "рыба и морепродукты",
        "колбасы и полуфабрикаты",
        "овощи и зелень",
        "фрукты и ягоды",
        "молочное и яйца",
        "крупы, макароны и хлеб",
        "соусы и специи",
    ]  # «Сладкое и напитки» — скрытая: из этого не готовят
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


def test_fridge_home_is_the_shared_list_without_add_or_edit(monkeypatch):
    from fridge_model import GROUPS
    _memory_store(monkeypatch, [
        {"name": "курица", "cat": "мясо и птица", "on": True, "group": "chicken"},
        {"name": "индейка", "cat": "мясо и птица", "on": False, "group": "turkey"},
        {"name": "яблоки и груши", "cat": "фрукты и ягоды", "on": True, "group": "apples"},
        {"name": "утка", "cat": "мясо и птица", "on": True},  # запись прежних версий — не показывается
    ])
    bot = _Bot()

    asyncio.run(fridge.send_fridge(bot, "fridge-home"))

    message = bot.messages[-1]
    assert message["text"] == ("🎚️ Мои продукты · 2\n\nОтмечай в категориях, что любишь и обычно покупаешь, — "
                               "рецепт «Из моих продуктов» соберу из них.")
    labels = _labels(message["reply_markup"])
    assert labels[:2] == [["Мясо и птица · 1"], ["Рыба и морепродукты · 0"]]
    assert len(labels) == len(GROUPS) + 2 and labels[-2:] == [["📝 Выбрать предпочтения"], ["⬅️ Назад", "#️⃣ Главная"]]


def test_category_has_five_groups_with_examples_and_toggles(monkeypatch):
    from fridge_model import GROUPS
    state = _memory_store(monkeypatch, [{"name": "курица", "cat": "мясо и птица", "on": True, "group": "chicken"}])
    bot = _Bot()

    asyncio.run(fridge.send_fridge_cat(bot, "fridge-category", 1, 0))  # рыба
    assert bot.messages[-1]["text"] == "Рыба и морепродукты · 0 из 5"
    assert _labels(bot.messages[-1]["reply_markup"])[:-1] == [
        ["□ Лосось"], ["□ Тунец"], ["□ Белая рыба (треска, судак)"], ["□ Скумбрия и сельдь"],
        ["□ Морепродукты (креветки, кальмары, мидии)"]]
    assert all(len(groups) == 5 for groups in GROUPS.values())

    asyncio.run(fridge.fridge_fav(bot, "fridge-category", 1, 2))  # □ → ✅ белая рыба
    asyncio.run(fridge.fridge_fav(bot, "fridge-category", 0, 2))  # ✅ → □ курица
    assert fridge._fridge_available(state) == ["Белая рыба (треска, судак)"]
    assert ["✅ Курица"] not in _labels(bot.messages[-1]["reply_markup"])


def test_chat_add_marks_groups_and_rejects_others(monkeypatch):
    state = _memory_store(monkeypatch, [])
    bot = _Bot()

    asyncio.run(fridge.fridge_add_done(bot, "fridge-chat", "курица"))  # первая — сразу рецепт дня
    asyncio.run(fridge.fridge_add_done(bot, "fridge-chat", "куриные сердечки, сыр гауда, кинза, дуриан, чипсы, курица"))

    assert fridge._fridge_available(state) == [
        "Курица", "Фарш и субпродукты (печень, сердечки, желудки)", "Зелень и салатные листья",
        "Сыр (твёрдый, моцарелла, фета)"]
    text = next(m["text"] for m in bot.messages if m["text"].startswith("Мои продукты обновлены"))
    assert "Отметил любимыми:" in text and "Уже любимые:\nКурица" in text
    assert "• дуриан — нет в списке продуктов" in text and "• чипсы — нет в списке продуктов" in text


def test_switch_to_groups_maps_previous_marks(monkeypatch):
    profile = {"fridge_base_v": 4}
    state = _memory_store(monkeypatch, [
        {"name": "куриные ножки", "cat": "мясо и птица", "on": True},
        {"name": "филе лосося", "cat": "рыба и морепродукты", "on": True},
        {"name": "сельдь", "cat": "рыба и морепродукты", "on": True},
        {"name": "грибы", "cat": "овощи и зелень", "on": True},
        {"name": "батончики", "cat": "сладкое и напитки", "on": True},
    ], profile=profile)
    assert fridge._fridge_available(state) == ["Курица", "Лосось", "Скумбрия и сельдь"]  # ещё до переноса

    asyncio.run(fridge.send_fridge(_Bot(), "fridge-switch"))

    assert fridge._fridge_available(state) == ["Курица", "Лосось", "Скумбрия и сельдь"]
    assert {"куриные ножки", "грибы", "батончики"} <= {item["name"] for item in state}  # данные не удалены
    assert profile["fridge_base_v"] == fridge.BASE_VERSION
