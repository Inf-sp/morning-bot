import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import learning_game
import travel_photos
from ui import learning as learning_ui


class FakeBot:
    def __init__(self):
        self.photos = []
        self.messages = []

    async def send_photo(self, **kwargs):
        self.photos.append(kwargs)

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


def test_dutch_detective_result_has_no_russian_labels():
    message = learning_ui.game_found(
        learning_game.GAME_UI["нидерландский"], "De kat", "Een kat is 's nachts vaak actief.",
    )

    assert message.text.startswith("✅ Zaak opgelost!")
    assert "Waarom:" in message.text
    assert "Дело раскрыто" not in message.text
    assert "Почему:" not in message.text


def test_detective_rejects_portrait_photo(monkeypatch):
    monkeypatch.setattr(
        travel_photos,
        "find_illustration",
        lambda _query: {"url": "https://example.test/portrait.jpg", "width": 800, "height": 1200},
    )
    monkeypatch.setattr(learning_game.store, "game_config", {"42": {"lang": "нидерландский"}})
    bot = FakeBot()
    state = {"answer": "De kat", "explain": "Een kat is een huisdier."}

    asyncio.run(learning_game._send_game_result(bot, "42", state, learning_game.GAME_UI["нидерландский"], None))

    assert bot.photos == []
    assert len(bot.messages) == 1
