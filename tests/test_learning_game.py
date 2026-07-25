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


def test_dutch_hint_message_is_translated_but_button_is_russian():
    message = learning_ui.game_hint(learning_game.GAME_UI["нидерландский"], "Hij miauwt.")

    assert message.text.startswith("💡 Hint")
    assert message.reply_markup.inline_keyboard[0][0].text == "😞 Сдаюсь"


def test_detective_starts_easy_riddle_without_difficulty_prompt(monkeypatch):
    calls = []

    async def send_game(bot, cid, status=None):
        calls.append((bot, cid, status))

    monkeypatch.setattr(learning_game.store, "game_config", {})
    monkeypatch.setattr(learning_game, "_active_language_code", lambda _cid: "nl")
    monkeypatch.setattr(learning_game, "send_game", send_game)

    asyncio.run(learning_game.start(object(), "42"))

    assert calls == [(calls[0][0], "42", None)]
    assert learning_game.store.game_config["42"] == {"lang": "нидерландский"}


def test_detective_buttons_stay_in_russian_while_clue_message_is_dutch(monkeypatch):
    class Bot:
        messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    monkeypatch.setattr(learning_game.store, "game_config", {"42": {"lang": "нидерландский"}})
    monkeypatch.setattr(learning_game, "_game_recent", lambda _cid: [])
    monkeypatch.setattr(learning_game, "_remember_game_answer", lambda *_args: None)
    monkeypatch.setattr(learning_game, "game_data", lambda *_args, **_kwargs: {
        "clues": "Een korte aanwijzing.", "answer": "De kat", "aliases": [],
        "hint": "Hij miauwt.", "hint2": "Hij woont vaak in een huis.",
        "explain": "Dit is een kat.",
    })

    bot = Bot()
    asyncio.run(learning_game.send_game(bot, "42"))

    labels = [button.text for row in bot.messages[0]["reply_markup"].inline_keyboard for button in row]
    assert labels == ["💡 Подсказка", "😞 Сдаюсь", "⬅️ Назад", "#️⃣ Главная"]


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
