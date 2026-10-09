import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import assistant
import assistant_context
import settings


def test_topics_follow_the_question():
    assert assistant_context.topics("Какие носки мне купить?") == ["wardrobe", "weather"]
    assert assistant_context.topics("Что приготовить без мяса из того что есть?") == ["food"]
    assert assistant_context.topics("Посоветуй что-то похожее на мои любимые сериалы") == ["leisure"]
    assert assistant_context.topics("Как дела?") == []


def test_wardrobe_section_lists_my_items_with_colors(monkeypatch):
    monkeypatch.setattr(assistant_context.store, "load_wardrobe", lambda _cid: {"zones": {
        "Низ": {"Джинсы": [{"id": "1", "zone": "Низ", "name": "Тёмно-синие джинсы", "colors": ["тёмно-синий"]}],
                "Брюки": [{"id": "2", "zone": "Низ", "name": "Брюки", "colors": ["оливковый"]}]},
        "Обувь": {"Кеды": [{"id": "3", "zone": "Обувь", "name": "Белые кеды", "colors": ["белый"]}]},
    }})
    monkeypatch.setattr(settings, "wardrobe_styles", lambda _cid: ["Городской"])
    monkeypatch.setattr(assistant_context, "_weather", lambda _cid: "")

    block = assistant_context.build("42", "Какие носки мне купить?")

    assert "Брюки и шорты: Тёмно-синие джинсы, Брюки (оливковый)" in block
    assert "Обувь: Белые кеды" in block and "Любимый стиль: Городской" in block


def test_chat_sends_personal_data_only_with_the_current_question(monkeypatch):
    cid = "ctx-chat"
    sent, calls = [], []
    monkeypatch.setattr(assistant.store, "chat_history", {})
    monkeypatch.setattr(assistant.research, "requires_explicit_web_search", lambda _text: False)
    monkeypatch.setattr(assistant_context, "build", lambda _cid, _text: "Шкаф пользователя:\nОбувь: Белые кеды")

    async def no_draft(*_a, **_k):
        return None

    class Status:
        async def stop(self, delete=True):
            return None

        async def replace(self, text, **_kwargs):
            sent.append({"text": text})

    async def start(*_a, **_k):
        return Status()

    async def chain(history, _cid):
        calls.append(history)
        return "Под белые кеды подойдут белые или серые носки."

    class Bot:
        async def send_chat_action(self, **_k):
            return None

        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(assistant.rich_delivery, "start_draft", no_draft)
    monkeypatch.setattr(assistant.util.StatusManager, "start", start)
    monkeypatch.setattr(assistant.ai, "achat_chain", chain)

    asyncio.run(assistant.chat_reply(Bot(), cid, "Какие носки мне купить?"))

    question = calls[0][-1]["content"]
    assert question.startswith("Какие носки мне купить?") and "Белые кеды" in question
    assert "не выдумывай" in question
    # В сохраняемой истории — только сам вопрос, без данных шкафа.
    assert assistant.store.chat_history[cid][0]["content"] == "Какие носки мне купить?"
