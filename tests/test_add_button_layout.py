import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import fridge
import dictionary_import
import saved_items
import settings
import wardrobe


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


class _Bot:
    message = None

    async def send_message(self, **kwargs):
        self.message = kwargs


def _assert_add_menu(rows, expected_add):
    assert rows[0] == [expected_add]
    assert rows[-1] == ["⬅️ Назад", "#️⃣ Меню"]
    assert all(not label.startswith(("✏️ Добав", "✨ Добав", "✅ Добав"))
               for row in rows for label in row)


def test_wardrobe_add_action_is_first_and_separate():
    rows = _labels(wardrobe.closet_kb())
    assert rows[0] == ["🆕 Добавить вещь", "🔍 Найти"]
    assert rows[-1] == ["⬅️ Назад", "#️⃣ Меню"]


def test_fridge_add_action_is_first_and_separate(monkeypatch):
    monkeypatch.setattr(fridge.store, "get_list", lambda *_args: [])
    bot = _Bot()

    asyncio.run(fridge.send_fridge(bot, "pytest-add-layout"))

    _assert_add_menu(_labels(bot.message["reply_markup"]), "🆕 Добавить продукт")


def test_favorites_add_action_names_object(monkeypatch):
    monkeypatch.setattr(saved_items, "_love_items", lambda *_args: ["Книга"])
    bot = _Bot()

    asyncio.run(saved_items.send_love_section(bot, "pytest-add-layout", "books"))

    _assert_add_menu(_labels(bot.message["reply_markup"]), "🆕 Добавить книгу")


def test_lagom_add_action_is_first_and_separate(monkeypatch):
    import memory

    monkeypatch.setattr(memory, "get_lagom", lambda *_args: ["Больше ходить"])
    bot = _Bot()

    asyncio.run(settings.send_lagom(bot, "pytest-add-layout"))

    _assert_add_menu(_labels(bot.message["reply_markup"]), "🆕 Добавить принцип")


def test_dictionary_batch_keeps_add_action_on_own_first_row():
    rows = _labels(dictionary_import._dict_batch_preview_kb())

    assert rows == [["🆕 Добавить всё"], ["❌ Не добавлять"]]
