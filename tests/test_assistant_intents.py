"""Ассистент: короткая команда открывает раздел, вопрос с подробностями получает ответ, несколько фильмов — подборка."""
import asyncio
import os

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import assistant

# (фраза, ожидаемое действие; None — свободный ответ ассистента с данными пользователя)
PHRASES = [
    ("Посоветуй фильм", "movie"),
    ("Что посмотреть вечером?", "movie"),
    ("Хочу посмотреть хороший фильм", "movie"),
    ("посоветуй сериал", "tv"),
    ("Посоветуй какие фильмы смотреть в самолете", "movie_list"),
    ("Какие фильмы посмотреть?", "movie_list"),
    ("Сериал как Breaking Bad, но короче", "movie_list"),
    ("Фильм на вечер с детьми", "movie_list"),
    ("Топ сериалов", "movie_list"),
    ("Что за сериал шитс крик", None),
    ("Что надеть?", "outfit"),
    ("Что надеть сегодня", "outfit"),
    ("Что надеть на ужин в ресторан?", None),
    ("Завтра обед с коллегами, что надеть", None),
    ("Что надеть на концерт?", None),
    ("Какая погода?", "weather"),
    ("Будет ли дождь сегодня", "weather"),
    ("Нужен ли зонт", "weather"),
    ("Какая погода завтра", "weather_tomorrow"),
    ("Какая погода в Риме в мае?", None),
    ("Что послушать", "music"),
    ("Музыка для бега", None),
    ("Концерты рядом", "concerts"),
    ("Как перевести деньги в Revolut", None),
    ("Какие события в мире", None),
    ("Учить английский", "learn"),
    ("Как будет по-нидерландски спасибо", None),
    ("Слово дня", "dictionary"),
    ("Что приготовить на ужин?", "meal_picker"),
    ("Что сделать с остатками курицы?", "fridge"),
    ("Мой день", "day_plan"),
    ("Какие носки мне купить?", None),
]


@pytest.mark.parametrize("text, expected", PHRASES)
def test_chat_phrase_opens_section_only_for_a_direct_command(text, expected):
    assert assistant._detect_intent(text) == expected


def test_movie_list_gets_the_whole_request_and_outfit_beats_meal(monkeypatch):
    calls = []

    async def fake_run(_bot, _cid, action, payload=None):
        calls.append((action, payload))

    class Bot:
        async def send_chat_action(self, **_kwargs):
            pass

    for name in ("last_action", "last_source", "chat_history", "last_surface"):
        monkeypatch.setattr(assistant.store, name, {})
    monkeypatch.setattr(assistant, "_run_intent", fake_run)

    asyncio.run(assistant.chat_reply(Bot(), "42", "Посоветуй какие фильмы смотреть в самолете"))
    assert calls == [("movie_list", "Посоветуй какие фильмы смотреть в самолете")]  # просьба целиком — в подборку
    assert assistant._recipe_ingredients_from_chat("Что надеть на ужин с друзьями") is None  # одежда, не рецепт
    assert assistant._recipe_ingredients_from_chat("Ужин с лососем") == "ужин с лососем"


def test_movie_list_is_built_from_tmdb_checked_titles(monkeypatch):
    import leisure_movies  # noqa: F401 — movie_discovery грузится после него, как в боте
    import movie_discovery

    async def fake_json(*_args, **_kwargs):
        return {"heading": "фильмы в самолёт", "items": [
            {"title": "Паддингтон 2", "title_en": "Paddington 2", "year": "2017", "kind": "movie",
             "why": "Лёгкий, без сложного сюжета"},
            {"title": "Выдуманный", "title_en": "Made Up", "year": "2020", "kind": "movie", "why": "нет такого"}]}

    def lookup(title, title_en="", strict=False, kind="", year=""):
        assert strict  # подборка показывает только уверенно найденное
        return {"id": 1, "name": "Паддингтон 2", "year": "2017", "genres": "комедия", "kind": "movie",
                "trailer_url": "https://youtube.example/p2"} if title_en == "Paddington 2" else None

    class Status:
        async def replace(self, text, **kwargs):
            sent.append((text, kwargs["entities"]))
            return True

        async def stop(self, delete=True):
            pass

    async def start(*_args, **_kwargs):
        return Status()

    sent = []
    monkeypatch.setattr(movie_discovery.store, "get_list", lambda *_args: [])
    monkeypatch.setattr("recommendation_stoplist.values", lambda *_args: [])
    monkeypatch.setattr("ai.allm_json", fake_json)
    monkeypatch.setattr(movie_discovery.tmdb, "lookup_title", lookup)
    monkeypatch.setattr("util.StatusManager.start", start)

    assert asyncio.run(movie_discovery.send_movie_list(None, "42", "фильмы в самолёт")) is True
    text, entities = sent[0]
    assert text == "🎬 Фильмы в самолёт\n\n- Паддингтон 2 · 2017 · комедия — лёгкий, без сложного сюжета"  # выдуманного нет
    assert any(entity.type == "text_link" and entity.url == "https://youtube.example/p2" for entity in entities)
