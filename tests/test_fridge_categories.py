import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import fridge
import data_refresh
import config
from fridge_model import _CAT_ORDER, _fridge_detect_cat, _fridge_migrate


class _Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _memory_store(monkeypatch, initial):
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
    assert message["text"].startswith("🧊 Мой холодильник · 2 продукта в наличии")
    assert _labels(message["reply_markup"]) == [
        ["🆕 Добавить продукт"],
        ["Мясо и рыба · 1"],
        ["Овощи и фрукты · 1"],
        ["Молочное и напитки · 0"],
        ["Бакалея · 0"],
        ["Специи и соусы · 0"],
        ["Заморозка · 0"],
        ["❌ Удалить продукты"],
        ["⬅️ Назад", "#️⃣ Меню"],
    ]


def test_fridge_category_uses_status_dots_without_delete(monkeypatch):
    _memory_store(monkeypatch, [
        {"name": "курица", "cat": "мясо и рыба", "on": True},
        {"name": "лосось", "cat": "мясо и рыба", "on": False},
    ])
    bot = _Bot()

    asyncio.run(fridge.send_fridge_cat(bot, "fridge-category", 0, 0))

    message = bot.messages[-1]
    assert message["text"].startswith("Мясо и рыба · 2 продукта · 1 в наличии")
    assert "🟢 — есть в наличии  🔴 — закончилось" in message["text"]
    rows = _labels(message["reply_markup"])
    assert rows[:2] == [["🟢 курица"], ["🔴 лосось"]]
    assert all("Удалить" not in label for row in rows for label in row)
    assert rows[-1] == ["⬅️ Назад", "#️⃣ Меню"]


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


def test_database_refresh_persists_fridge_in_category_order(monkeypatch):
    state = {
        config.FRIDGE_KEY: [
            {"name": "яблоки", "cat": "фрукты", "on": True},
            {"name": "лосось", "cat": "прочее", "on": True},
            {"name": "томатная паста", "cat": "крупы и макароны", "on": True},
        ],
        config.MOVIE_SEEN_KEY: ["Патерсон"],
    }
    monkeypatch.setattr(data_refresh, "_collection_keys", lambda: [])
    monkeypatch.setattr(
        data_refresh.store, "get_list", lambda key, _cid: list(state.get(key, [])))
    monkeypatch.setattr(data_refresh.store, "load_wardrobe", lambda *_args: {})
    monkeypatch.setattr(data_refresh, "migration_count", lambda *_args: 0)

    def set_list(key, _cid, value):
        state[key] = list(value)

    async def migrate(_cid, wardrobe):
        return wardrobe

    async def concerts(_cid):
        return {"status": "no_artists", "artists": 0, "events": 0}

    monkeypatch.setattr(data_refresh.store, "set_list", set_list)
    monkeypatch.setattr(data_refresh, "migrate_item_attrs", migrate)
    monkeypatch.setattr("leisure_concerts.refresh_concerts_cache", concerts)

    result = asyncio.run(data_refresh.refresh_user_database("fridge-refresh"))

    assert [item["cat"] for item in state[config.FRIDGE_KEY]] == [
        "мясо и рыба",
        "овощи и фрукты",
        "специи и соусы",
    ]
    assert result["fridge_items"] == 3
    assert result["stoplist_items"] == 1
    assert state[config.MOVIE_SEEN_KEY] == []
    assert state[config.RECOMMENDATION_STOPLIST_KEY][0]["category"] == "Не рекомендовать"
