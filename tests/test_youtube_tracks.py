import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_music
import youtube_tracks


class _Response:
    status_code = 200
    headers = {}
    content = b"{}"

    def json(self):
        return {
            "items": [
                {
                    "id": {"videoId": "wrongvideo12"},
                    "snippet": {
                        "title": "Introvert piano cover",
                        "channelTitle": "Someone Else",
                    },
                },
                {
                    "id": {"videoId": "rightvideo34"},
                    "snippet": {
                        "title": "Little Simz — Introvert (Official Video)",
                        "channelTitle": "Little Simz",
                    },
                },
            ],
        }


def _memory_cache(monkeypatch):
    memory = {}

    def load(key):
        return memory.get(key, {})

    def mutate(key, callback):
        data, result = callback(memory.get(key, {}))
        memory[key] = data
        return result

    monkeypatch.setattr(youtube_tracks.store, "_load", load)
    monkeypatch.setattr(youtube_tracks.store, "mutate_kv", mutate)
    youtube_tracks.util._TTL_CACHE.clear()
    return memory


def test_no_youtube_key_skips_network(monkeypatch):
    monkeypatch.setattr(youtube_tracks.config, "YOUTUBE_API_KEY", "")
    monkeypatch.setattr(
        youtube_tracks.requests, "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )

    assert youtube_tracks.find_track_url("Introvert", "Little Simz") == ""


def test_track_lookup_uses_youtube_api_and_caches_exact_video(monkeypatch):
    _memory_cache(monkeypatch)
    monkeypatch.setattr(youtube_tracks.config, "YOUTUBE_API_KEY", "youtube-secret")
    calls = []
    usage = []

    def fake_get(url, *, params, timeout):
        calls.append({"url": url, "params": params, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(youtube_tracks.requests, "get", fake_get)
    monkeypatch.setattr(
        youtube_tracks.api_usage, "record_request",
        lambda service, ok=True, **kwargs: usage.append((service, ok, kwargs)),
    )

    first = youtube_tracks.find_track_url("Introvert", "Little Simz")
    second = youtube_tracks.find_track_url("Introvert", "Little Simz")

    assert first == "https://music.youtube.com/watch?v=rightvideo34"
    assert second == first
    assert len(calls) == 1
    assert calls[0]["url"] == "https://www.googleapis.com/youtube/v3/search"
    assert calls[0]["params"] == {
        "key": "youtube-secret",
        "part": "snippet",
        "q": "Little Simz Introvert official audio",
        "type": "video",
        "videoCategoryId": "10",
        "maxResults": 5,
    }
    assert usage[0][0:2] == ("youtube", True)


def test_old_youtube_cache_link_opens_in_youtube_music_without_a_request(monkeypatch):
    _memory_cache(monkeypatch)
    monkeypatch.setattr(youtube_tracks.config, "YOUTUBE_API_KEY", "youtube-secret")
    key = youtube_tracks._cache_key("Introvert", "Little Simz")
    youtube_tracks.util.ttl_set(
        "youtube_tracks", key, "https://www.youtube.com/watch?v=oldvideo123",
    )
    monkeypatch.setattr(
        youtube_tracks.requests, "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )

    assert youtube_tracks.find_track_url("Introvert", "Little Simz") == (
        "https://music.youtube.com/watch?v=oldvideo123"
    )


def test_daily_music_content_has_no_day_track(monkeypatch):
    monkeypatch.setattr(leisure_music, "_load_music_legend", lambda _day: {})

    result = asyncio.run(leisure_music._daily_music_content("42"))

    assert "vibe" not in result
