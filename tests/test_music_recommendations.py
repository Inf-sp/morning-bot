import os
import asyncio

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_music


def test_recent_artist_history_is_unique_and_limited(monkeypatch):
    profile = {"music_recent_artists": ["The xx", "Bicep", "the xx"]}
    saved = []

    def set_profile(_cid, value):
        profile.clear()
        profile.update(value)
        saved.append(dict(value))

    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "set_profile", set_profile)

    leisure_music._remember_artist("42", "BICEP")
    leisure_music._remember_artist("42", "FKA twigs")

    assert saved[-1]["music_recent_artists"] == ["The xx", "BICEP", "FKA twigs"]


def test_music_module_has_no_learning_language_priority():
    assert not hasattr(leisure_music, "_language_music_context")
    assert not hasattr(leisure_music, "_learning_language_code")


def test_music_shows_a_local_artist_when_the_ai_chain_is_unavailable(monkeypatch):
    calls = []
    profile = {}

    class Status:
        async def replace(self, text, **kwargs):
            calls.append((text, kwargs))

    async def unavailable(*_args, **_kwargs):
        raise Exception("AI cooldown")

    monkeypatch.setattr(leisure_music.ai, "allm_json", unavailable)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "set_profile", lambda _cid, value: profile.update(value))
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert len(calls) == 1
    assert "FKA twigs" in calls[0][0]
    assert "Не удалось подобрать" not in calls[0][0]
