import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot


def test_bot_splits_plain_long_messages_and_keeps_keyboard_on_last_part():
    calls = []

    class FakeBot:
        async def _send_message_once(self, chat_id, *args, **kwargs):
            calls.append((chat_id, args, kwargs))
            return {"part": len(calls)}

    result = asyncio.run(bot._MenuCleanupBot.send_message(
        FakeBot(), "42", text="я" * 4_100, reply_markup="keyboard", transient=True,
    ))

    assert len(calls) == 2
    assert all(len(call[2]["text"].encode("utf-16-le")) // 2 <= 4_000 for call in calls)
    assert "reply_markup" not in calls[0][2]
    assert calls[1][2]["reply_markup"] == "keyboard"
    assert result == {"part": 2}
