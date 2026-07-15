import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe
from ui.wardrobe import purchase_check_card


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_send_home_includes_inline_keyboard():
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()

    asyncio.run(wardrobe.send_home(bot, "pytest-wardrobe-inline"))

    assert bot.message["reply_markup"] is not None
    assert _labels(bot.message["reply_markup"]) == [
        ["🆕 Добавить вещь"],
        ["👕 Мой шкаф", "🎨 Мой стиль"],
        ["⬅️ Назад", "#️⃣ Меню"],
    ]


def test_cached_home_edits_once_without_loading_message(monkeypatch):
    cached = {
        "date": wardrobe._day_key(),
        "text": "cached",
        "look_data": {
            "items": [{"name": "Белая футболка"}, {"name": "Синие брюки"}, {"name": "Белые кеды"}],
            "reasons": ["Светлый верх поддерживает обувь"],
            "style_tip": "Заправь футболку только спереди",
            "final_text": "ничего добавлять не нужно",
        },
    }
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: cached)

    class Message:
        edits = []

        async def edit_text(self, *args, **kwargs):
            self.edits.append((args, kwargs))

    class Query:
        message = Message()

    class Bot:
        sends = []

        async def send_message(self, **kwargs):
            self.sends.append(kwargs)

    q = Query()
    bot = Bot()
    asyncio.run(wardrobe.send_home(bot, "cached-fast", q=q))

    assert len(q.message.edits) == 1
    assert bot.sends == []


def test_purchase_assessment_card_uses_thinking_emoji():
    message = purchase_check_card({"item": "Куртка", "verdict": "Подходит"})

    assert message.text.startswith("🧐 Оценка")
