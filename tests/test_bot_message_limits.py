import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot
import util


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


def test_formatted_long_message_is_split_without_broken_entities():
    chunks = util.telegram_text_chunks("**" + ("важно " * 900) + "**", 4000)

    assert len(chunks) >= 2
    for text, entities in chunks:
        text_length = len(text.encode("utf-16-le")) // 2
        assert text_length <= 4000
        assert all(entity.offset + entity.length <= text_length for entity in entities)
