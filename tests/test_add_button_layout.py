import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import fridge
import dictionary_import
import wardrobe


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


class _Bot:
    message = None

    async def send_message(self, **kwargs):
        self.message = kwargs


def _assert_add_menu(rows, expected_add):
    assert rows[0] == [expected_add]
    assert rows[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert all(not label.startswith(("✏️ Добав", "✨ Добав", "✅ Добав"))
               for row in rows for label in row)


def test_wardrobe_add_action_is_above_navigation_and_separate():
    rows = _labels(wardrobe.closet_kb())
    assert rows[0] == ["📌 Предпочтения"]
    assert rows[-2] == ["🆕 Добавить вещь"]
    assert all("Провер" not in label and "Оцен" not in label for row in rows for label in row)
    assert rows[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_fridge_preferences_are_first_and_add_is_separate(monkeypatch):
    monkeypatch.setattr(fridge.store, "get_list", lambda *_args: [])
    bot = _Bot()

    asyncio.run(fridge.send_fridge(bot, "pytest-add-layout"))

    rows = _labels(bot.message["reply_markup"])
    assert rows[0] == ["📌 Предпочтения"]
    assert rows[-2] == ["🆕 Добавить продукт"]
    assert rows[-1] == ["⬅️ Назад", "#️⃣ Главная"]

def test_dictionary_batch_keeps_add_action_on_own_first_row():
    rows = _labels(dictionary_import._dict_batch_preview_kb())

    assert rows == [["🆕 Добавить всё"], ["❌ Не добавлять"]]
