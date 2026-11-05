"""Ассистент: любой вопрос в чате — ответ ИИ; разделы открываются только кнопками, из чата — команды добавления."""
import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import assistant
import assistant_context
import fridge

QUESTIONS = [
    "Концерты рядом",  # раньше — список концертов вместо ответа
    "Когда концерт Tove Lo?",
    "Посоветуй фильм",
    "Что надеть?",
    "Какая погода завтра",
    "Что приготовить на ужин?",
    "Что сегодня?",
    "Слово дня",
    "Учить английский",
]


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_question_gets_an_ai_answer(monkeypatch, question):
    calls, sent = [], []
    monkeypatch.setattr(assistant.store, "chat_history", {})
    monkeypatch.setattr(assistant.research, "requires_explicit_web_search", lambda _text: False)
    monkeypatch.setattr(assistant_context, "build", lambda _cid, _text: "")

    async def no_draft(*_a, **_k):
        return None

    class Status:
        async def stop(self, delete=True):
            return None

        async def replace(self, text, **_kwargs):
            sent.append(text)
            return True

    async def start(*_a, **_k):
        return Status()

    async def chain(history, _cid):
        calls.append(history[-1]["content"])
        return "Ответ по делу"

    class Bot:
        async def send_chat_action(self, **_k):
            return None

        async def send_message(self, **kwargs):
            raise AssertionError(f"раздел вместо ответа: {kwargs}")

    monkeypatch.setattr(assistant.rich_delivery, "start_draft", no_draft)
    monkeypatch.setattr(assistant.util.StatusManager, "start", start)
    monkeypatch.setattr(assistant.ai, "achat_chain", chain)
    asyncio.run(assistant.chat_reply(Bot(), "42", question))
    assert calls == [question] and len(sent) == 1 and sent[0].startswith("Ответ по делу")


def test_wardrobe_and_fridge_add_commands_are_recognised():
    detect = assistant.detect_wardrobe_add
    assert detect("Добавь в шкаф чёрную футболку Uniqlo") == "чёрную футболку Uniqlo"
    assert detect("добавь мне в гардероб: белые кеды, синие джинсы") == "белые кеды, синие джинсы"
    assert detect("Добавь бежевую ветровку в шкаф") == "бежевую ветровку"
    assert detect("добавь вещь синие джинсы") == "синие джинсы"
    assert detect("добавь в шкаф") == ""  # без вещи — спросить, что добавить
    assert detect("что у меня в шкафу?") is None and detect("добавь в словарь bespreken") is None
    assert fridge._fridge_payload_from_chat("добавь молоко и яйца в холодильник") == "молоко и яйца"
    assert fridge._fridge_payload_from_chat("добавь в холодильник: сыр") == "сыр"


@pytest.mark.parametrize("text, expected", [
    ("Добавь в шкаф чёрную футболку", "wardrobe"),
    ("добавь молоко в холодильник", "fridge"),
])
def test_add_commands_are_not_taken_by_dictionary(monkeypatch, text, expected):
    import bot_text
    handled = []

    async def wardrobe_add(_bot, _cid, payload):
        handled.append(("wardrobe", payload))

    async def fridge_add(_bot, _cid, payload, *_a):
        handled.append(("fridge", payload))

    async def dictionary(*_args):
        raise AssertionError("dictionary must not see wardrobe or fridge commands")

    async def nothing(*_args):
        return None

    monkeypatch.setattr(bot_text.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_text.tracking, "touch", lambda _cid: None)
    import wardrobe
    monkeypatch.setattr(wardrobe, "add_item", wardrobe_add)
    monkeypatch.setattr(fridge, "fridge_add_done", fridge_add)
    monkeypatch.setattr(bot_text.dictionary_import, "try_add_dict_from_chat", dictionary)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id="42"), message=SimpleNamespace(text=text))
    asyncio.run(bot_text.handle(update, SimpleNamespace(bot=object()), nothing))
    assert [kind for kind, _payload in handled] == [expected]


def test_concert_question_gets_upcoming_concerts_of_my_artists(monkeypatch):
    import leisure_concerts
    monkeypatch.setattr(leisure_concerts, "cached_favorite_concerts", lambda _cid, _limit: [
        {"artist": "Tove Lo", "date": "2026-11-12", "time": "20:00", "venue": "AFAS Live", "city": "Amsterdam"}])
    assert "concerts" in assistant_context.topics("Когда концерт Tove Lo?")
    block = assistant_context._concerts("42")
    assert "Tove Lo — 2026-11-12, 20:00, AFAS Live, Amsterdam" in block
