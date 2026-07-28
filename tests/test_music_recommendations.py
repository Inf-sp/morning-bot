import os

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
