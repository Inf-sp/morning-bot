import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import leisure_music


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=len(self.sent))


def test_dutch_learning_language_adds_priority_and_example(monkeypatch):
    monkeypatch.setattr(leisure_music.store, "get_learning_language", lambda _cid: "nl")

    context = leisure_music._language_music_context("42")

    assert "Dutch-language" in context["search"]
    assert "нидерландский" in context["prompt"]
    assert "Eefje de Visser — De Parade" in context["prompt"]
    assert "не жёсткий фильтр" in context["prompt"]


def test_no_language_signal_before_explicit_choice(monkeypatch):
    monkeypatch.setattr(leisure_music.store, "get_learning_language", lambda _cid: "")
    monkeypatch.setattr(leisure_music.settings, "get", lambda *_args, **_kwargs: "")

    assert leisure_music._language_music_context("42") == {"search": "", "prompt": ""}


def test_music_prompt_and_web_search_use_learning_language(monkeypatch):
    prompts = []
    searches = []

    def fake_get_list(key, _cid):
        if key == config.ARTISTS_KEY:
            return ["The xx", "London Grammar"]
        return []

    def fake_search(query, _limit):
        searches.append(query)
        return "Eefje de Visser — De Parade; contemporary Dutch art pop"

    async def fake_model(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return {
            "artist": "Eefje de Visser",
            "desc": "Мелодичный современный арт-поп.",
            "why": ["Воздушная электроника как у The xx", "Нидерландский язык"],
            "tracks": ["De Parade", "Storm", "Ongeveer"],
            "fact": "Современная нидерландская певица.",
        }

    monkeypatch.setattr(leisure_music.store, "get_list", fake_get_list)
    monkeypatch.setattr(leisure_music.store, "get_learning_language", lambda _cid: "nl")
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_music.research, "tavily_snippet", fake_search)
    monkeypatch.setattr(leisure_music.ai, "allm_json", fake_model)
    bot = FakeBot()

    asyncio.run(leisure_music.send_listen(bot, "42"))

    assert "Dutch-language" in searches[0]
    assert "Eefje de Visser — De Parade" in prompts[0]
    assert "сильный дополнительный приоритет" in prompts[0]
    assert "не жёсткий фильтр" in prompts[0]
    assert "популярным или признанным" in prompts[0]
    assert bot.sent and "Eefje de Visser" in bot.sent[0]["text"]
