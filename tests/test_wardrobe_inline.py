import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_restore_home_kb_puts_inline_buttons_back():
    class Message:
        reply_markup = None

        async def edit_reply_markup(self, reply_markup=None):
            self.reply_markup = reply_markup

    class Query:
        message = Message()

    q = Query()

    asyncio.run(wardrobe._restore_home_kb(q))

    assert _labels(q.message.reply_markup) == [
        ["✨ Образ на сегодня"],
        ["✂️ Разбор гардероба"],
        ["🔍 Проверка покупки"],
        ["🎚️ Настройки гардероба"],
    ]


def test_send_home_includes_inline_keyboard():
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()

    asyncio.run(wardrobe.send_home(bot, "pytest-wardrobe-inline"))

    assert bot.message["reply_markup"] is not None
    assert _labels(bot.message["reply_markup"]) == [
        ["✨ Образ на сегодня"],
        ["✂️ Разбор гардероба"],
        ["🔍 Проверка покупки"],
        ["🎚️ Настройки гардероба"],
    ]
