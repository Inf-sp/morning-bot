import asyncio
import json
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import assistant
import bot_text
import config
import myday


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def test_chat_command_adds_dutch_article_lifehack_to_json(tmp_path, monkeypatch):
    path = tmp_path / "lifehacks.json"
    path.write_text(json.dumps([
        {"cat": "Быт и дом", "emoji": "🏠", "tips": []},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(myday, "_HERE", tmp_path)

    bot = FakeBot()
    text = (
        "Добавь лайфхак\n\n"
        "DE — синий, HET — оранжевый. Представляй слова с de синими, "
        "а с het — оранжевыми. Цветовые ассоциации помогают быстрее запомнить артикли."
    )

    assert asyncio.run(assistant.try_add_lifehack_from_chat(bot, "42", text)) is True

    saved = json.loads(path.read_text(encoding="utf-8"))
    language = next(item for item in saved if item["category"] == "язык")
    assert language["source"] == "user"
    assert language["tags"] == ["язык"]
    assert language["text"].startswith("DE — синий")
    assert bot.sent[0]["text"] == (
        "✅ Лайфхак сохранён\n\n"
        "Категория: 🇳🇱 Язык\n"
        "Будет появляться в разделе «Полезное» в «Мой день»."
    )


def test_local_lifehack_is_mixed_with_existing_ai_pool(monkeypatch):
    monkeypatch.setattr(myday, "_pool_ensure_fresh", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(myday, "_pool_get", lambda *_args: {
        "items": [{
            "id": 1,
            "text": "Чтобы быстрее собрать вещи, подготовь одежду вечером.",
            "category": "продуктивность",
            "shown_at": None,
        }],
    })
    monkeypatch.setattr(myday, "_local_lifehack_candidates", lambda *_args, **_kwargs: [{
        "id": "local:1",
        "text": "DE — синий, HET — оранжевый. Представляй слова с de синими, а с het — оранжевыми.",
        "category": "язык",
        "emoji": "🇳🇱",
    }])
    monkeypatch.setattr(myday.random, "random", lambda: 0.1)
    monkeypatch.setattr(myday.store, "set_list", lambda *_args: None)

    label, text = myday.daily_lifehack("42")

    assert label == "🇳🇱 Язык"
    assert text.startswith("DE — синий")


def test_lifehack_command_can_collect_text_in_second_message(monkeypatch):
    bot = FakeBot()
    saved = []
    monkeypatch.setattr(
        assistant.myday,
        "add_lifehack_to_file",
        lambda text: saved.append(text) or {"duplicate": False, "category": "🇳🇱 Язык"},
    )
    assistant.store.pending_input.pop("42", None)

    assert asyncio.run(assistant.try_add_lifehack_from_chat(bot, "42", "Добавь лайфхак")) is True
    assert "Напиши текст лайфхака" in bot.sent[-1]["text"]
    assert asyncio.run(
        assistant.try_add_lifehack_from_chat(bot, "42", "DE — синий, HET — оранжевый.")
    ) is True
    assert saved == ["DE — синий, HET — оранжевый."]
    assert bot.sent[-1]["text"].startswith("✅ Лайфхак сохранён")


def test_chat_router_prioritizes_lifehack_command_over_dictionary(monkeypatch):
    routed = []

    async def fake_lifehack(_bot, cid, text):
        routed.append((cid, text))
        return True

    async def fail_dictionary(*_args, **_kwargs):
        raise AssertionError("lifehack command must bypass dictionary routing")

    async def remove_keyboard(_bot, _cid):
        return None

    monkeypatch.setattr(bot_text.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_text.tracking, "touch", lambda _cid: None)
    monkeypatch.setattr(bot_text.dictionary_import, "try_add_dict_from_chat", fail_dictionary)
    monkeypatch.setattr(bot_text.assistant, "try_add_lifehack_from_chat", fake_lifehack)
    monkeypatch.setattr(bot_text.balance.thoughts, "cancel_capture", lambda *_args, **_kwargs: None)

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id="lifehack-router"),
        message=SimpleNamespace(text="Добавь лайфхак\nDE — синий, HET — оранжевый."),
    )
    context = SimpleNamespace(bot=FakeBot())

    asyncio.run(bot_text.handle(update, context, remove_keyboard))

    assert routed == [("lifehack-router", "Добавь лайфхак\nDE — синий, HET — оранжевый.")]
