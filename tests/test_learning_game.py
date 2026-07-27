import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import learning_game
import learning_router
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


def test_dutch_detective_result_shows_explanation_and_words_without_russian_labels():
    message = learning_ui.game_found(
        learning_game.GAME_UI["нидерландский"], "De kat",
        "Een kat heeft snorharen en jaagt soms op muizen.",
        [{"word": "snorharen", "translation": "усы"}],
    )

    assert message.text.startswith("✅ Zaak opgelost!")
    assert "Een kat heeft snorharen" in message.text
    assert "📚 Onthoud:" in message.text
    assert "• snorharen → усы" in message.text
    assert "Дело раскрыто" not in message.text
    assert "Почему:" not in message.text
    assert "Waarom:" not in message.text


def test_dutch_hint_message_is_translated_but_button_is_russian():
    message = learning_ui.game_hint(learning_game.GAME_UI["нидерландский"], "Hij miauwt.")

    assert message.text.startswith("💡 Hint")
    labels = [button.text for row in message.reply_markup.inline_keyboard for button in row]
    assert labels == ["😞 Сдаюсь", "⬅️ Назад", "#️⃣ Главная"]


def test_hint_is_one_time_and_disappears_from_original_card(monkeypatch):
    class Message:
        def __init__(self):
            self.edited_markup = None
            self.replies = []

        async def edit_reply_markup(self, **kwargs):
            self.edited_markup = kwargs["reply_markup"]

        async def reply_text(self, *_args, **kwargs):
            self.replies.append(kwargs)

    message = Message()
    monkeypatch.setattr(
        learning_game.store, "game_state",
        {"42": {"answer": "De kat", "hint": "Ik heb snorharen.", "hint_used": False}},
    )
    monkeypatch.setattr(
        learning_game.store, "game_config",
        {"42": {"lang": "нидерландский"}},
    )

    asyncio.run(learning_game.game_hint(object(), "42", type("Q", (), {"message": message})()))

    original_labels = [
        button.text
        for row in message.edited_markup.inline_keyboard
        for button in row
    ]
    reply_labels = [
        button.text
        for row in message.replies[0]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert learning_game.store.game_state["42"]["hint_used"] is True
    assert original_labels == ["😞 Сдаюсь", "⬅️ Назад", "#️⃣ Главная"]
    assert reply_labels == ["😞 Сдаюсь", "⬅️ Назад", "#️⃣ Главная"]


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


def test_detective_start_reuses_inline_status(monkeypatch):
    calls = []

    async def send_game(bot, cid, status=None):
        calls.append((bot, cid, status))

    marker = object()
    monkeypatch.setattr(learning_game.store, "game_config", {})
    monkeypatch.setattr(learning_game, "_active_language_code", lambda _cid: "nl")
    monkeypatch.setattr(learning_game, "send_game", send_game)

    asyncio.run(learning_game.start(object(), "42", status=marker))

    assert calls[0][2] is marker


def test_detective_route_uses_preserving_inline_status(monkeypatch):
    calls = []

    async def run_with_status(callback, **kwargs):
        calls.append(kwargs)
        await callback(object())

    async def start(bot, cid, status=None):
        calls.append((bot, cid, status))

    monkeypatch.setattr(learning_router.game, "start", start)

    asyncio.run(learning_router.handle_action(object(), "42", object(), "game", run_with_status))

    assert calls[0] == {"preserve_message": True}
    assert calls[1][1:] == ("42", calls[1][2])


def test_trainer_start_uses_preserving_inline_status(monkeypatch):
    calls = []

    async def run_with_status(callback, **kwargs):
        calls.append(kwargs)
        await callback(object())

    async def start(bot, cid, language):
        calls.append((bot, cid, language))

    monkeypatch.setattr(learning_router.trainer, "start", start)

    asyncio.run(learning_router.handle_action(
        object(), "42", object(), "train_nl", run_with_status,
    ))

    assert calls[0] == {"preserve_message": True}


def test_trainer_next_task_uses_preserving_inline_status(monkeypatch):
    calls = []

    async def run_with_status(callback, **kwargs):
        calls.append(kwargs)
        await callback(object())

    async def next_exercise(bot, cid, task_id=""):
        calls.append((bot, cid, task_id))

    monkeypatch.setattr(learning_router.trainer, "next_exercise", next_exercise)

    asyncio.run(learning_router.handle_callback(
        object(), "42", "ex_next_task-1", run_with_status,
    ))

    assert calls[0] == {"preserve_message": True}


def test_detective_buttons_stay_in_russian_while_clue_message_is_dutch(monkeypatch):
    class Bot:
        messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    monkeypatch.setattr(learning_game.store, "game_config", {"42": {"lang": "нидерландский"}})
    monkeypatch.setattr(learning_game, "_game_recent", lambda _cid: [])
    monkeypatch.setattr(learning_game, "_remember_game_answer", lambda *_args: None)
    monkeypatch.setattr(learning_game, "game_data", lambda *_args, **_kwargs: {
        "description": "Ik woon vaak bij mensen. Overdag slaap ik graag op warme plekken. Ik kan heel stil lopen en soms jaag ik op kleine dieren.",
        "answer": "De kat", "answer_en": "Cat", "aliases": [],
        "hint": "Ik heb snorharen en ik zeg miauw.",
        "explain": "Een kat woont vaak bij mensen en jaagt soms op muizen.",
        "words": [{"word": "snorharen", "translation": "усы"}],
    })

    bot = Bot()
    asyncio.run(learning_game.send_game(bot, "42"))

    assert "Detective · Nederlands" in bot.messages[0]["text"]
    assert "Verdachte:" not in bot.messages[0]["text"]
    assert "• " not in bot.messages[0]["text"]
    labels = [button.text for row in bot.messages[0]["reply_markup"].inline_keyboard for button in row]
    assert labels == ["💡 Подсказка", "😞 Сдаюсь", "⬅️ Назад", "#️⃣ Главная"]


def test_detective_rejects_vague_clues_without_a_signature():
    assert not learning_game._description_is_guessable({
        "description": "Het is 's nachts actief. Het heeft grote ogen. Het houdt van vis.",
        "answer": "de kat", "aliases": ["kat"],
        "hint": "Het is een huisdier.",
    })


def test_detective_accepts_connected_description_with_a_unique_hint():
    assert learning_game._description_is_guessable({
        "description": "Ik woon vaak bij mensen. Overdag slaap ik graag op warme plekken. Ik kan heel stil lopen en soms jaag ik op kleine dieren.",
        "answer": "de kat", "aliases": ["kat", "кошка", "cat"],
        "hint": "Ik heb snorharen en ik zeg miauw.",
        "explain": "Een kat woont vaak bij mensen en jaagt soms op muizen.",
        "lang": "нидерландский",
    })


def test_game_data_uses_description_and_parses_learning_words(monkeypatch):
    captured = {}

    def fake_llm(*_args, **kwargs):
        captured.update(kwargs)
        return (
            "DESCRIPTION: Ik woon vaak bij mensen. Ik slaap graag op warme plekken. "
            "Soms jaag ik op kleine dieren.\n"
            "ANSWER: de kat\n"
            "ALIASES: кошка|cat|kat\n"
            "ENGLISH: cat\n"
            "HINT: Ik heb snorharen en ik zeg miauw.\n"
            "EXPLAIN: Een kat woont vaak bij mensen en jaagt soms op muizen.\n"
            "WORDS: snorharen|усы; jagen|охотиться; ik|я"
        )

    monkeypatch.setattr(learning_game.ai, "llm", fake_llm)

    data = learning_game.game_data("нидерландский", [])

    assert data["description"].startswith("Ik woon vaak bij mensen.")
    assert captured["fallback_allowed"] is True
    assert captured["privacy_level"] == "public"
    assert "clues" not in data
    assert "hint2" not in data
    assert data["words"] == [
        {"word": "snorharen", "translation": "усы"},
        {"word": "jagen", "translation": "охотиться"},
    ]


def test_game_data_uses_public_openrouter_fallback_and_local_card(monkeypatch):
    captured = {}

    def unavailable(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("all providers unavailable")

    monkeypatch.setattr(learning_game.ai, "llm", unavailable)

    data = learning_game.game_data("английский", [])

    assert captured["fallback_allowed"] is True
    assert captured["privacy_level"] == "public"
    assert data["answer"] == "the cat"
    assert learning_game._description_is_guessable(data, "английский")


def test_detective_rejects_answer_inside_description():
    assert not learning_game._description_is_guessable({
        "description": "Ik ben een kat. Ik woon bij mensen. Ik maak soms een zacht geluid.",
        "answer": "de kat", "aliases": ["kat"],
        "hint": "Ik heb snorharen.",
        "explain": "Dit past bij een kat.",
        "lang": "нидерландский",
    })


def test_second_wrong_answer_uses_common_round_completion(monkeypatch):
    calls = []

    async def finish(bot, cid, state, ui):
        calls.append((bot, cid, state, ui))

    monkeypatch.setattr(learning_game, "_finish_game_round", finish)
    monkeypatch.setattr(
        learning_game.store, "game_state",
        {"42": {
            "answer": "De kat", "aliases": ["cat"], "tries": 1,
            "hint_used": False,
        }},
    )
    monkeypatch.setattr(
        learning_game.store, "game_config",
        {"42": {"lang": "нидерландский"}},
    )

    asyncio.run(learning_game.game_answer(object(), "42", "wronganswer"))

    assert len(calls) == 1
    assert calls[0][2]["tries"] == 2


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


def test_detective_searches_by_english_answer(monkeypatch):
    queries = []
    monkeypatch.setattr(
        travel_photos,
        "find_illustration",
        lambda query: queries.append(query) or {
            "url": "https://example.test/cat.jpg", "width": 1600, "height": 900,
        },
    )
    bot = FakeBot()
    state = {
        "answer": "De kat",
        "aliases": ["Кошка", "Cat", "Kat"],
        "answer_en": "Cat",
        "explain": "Een kat is een huisdier.",
    }

    asyncio.run(learning_game._send_game_result(
        bot, "42", state, learning_game.GAME_UI["нидерландский"], None))

    assert queries == ["Cat"]
    assert len(bot.photos) == 1


def test_detective_photo_search_returns_first_horizontal_result(monkeypatch):
    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"results": [
                {
                    "id": "first", "width": 1200, "height": 700,
                    "alt_description": "first result",
                    "urls": {"regular": "https://example.test/first.jpg"},
                    "user": {}, "links": {},
                },
                {
                    "id": "second", "width": 2400, "height": 1300,
                    "alt_description": "second result",
                    "urls": {"regular": "https://example.test/second.jpg"},
                    "user": {}, "links": {},
                },
            ]}

    monkeypatch.setattr(travel_photos.config, "UNSPLASH_ACCESS_KEY", "test-key")
    monkeypatch.setattr(travel_photos.requests, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(travel_photos.api_usage, "record_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(travel_photos.provider_runtime, "record_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(travel_photos.provider_runtime, "activate_fallback", lambda *_args, **_kwargs: None)

    photo = travel_photos.find_illustration("Кошка")

    assert photo["id"] == "first"
