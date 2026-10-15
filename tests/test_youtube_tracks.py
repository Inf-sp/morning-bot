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



def test_trailer_lookup_picks_the_official_trailer_and_keeps_a_youtube_link(monkeypatch):
    _memory_cache(monkeypatch)
    monkeypatch.setattr(youtube_tracks.config, "YOUTUBE_API_KEY", "youtube-secret")
    calls = []

    class Response(_Response):
        def json(self):
            return {"items": [
                {"id": {"videoId": "reaction001"},
                 "snippet": {"title": "Dune Part Two Trailer REACTION", "publishedAt": "2024-01-02T00:00:00Z"}},
                {"id": {"videoId": "olddune1984"},
                 "snippet": {"title": "Dune (1984) Official Trailer", "publishedAt": "2011-05-02T00:00:00Z"}},
                {"id": {"videoId": "official002"},
                 "snippet": {"title": "Dune: Part Two | Official Trailer 3", "publishedAt": "2023-12-18T00:00:00Z"}},
            ]}

    class Details(_Response):
        def json(self):
            return {"items": [{"id": "official002", "contentDetails": {"definition": "hd"},
                               "snippet": {"channelTitle": "Warner Bros. Pictures"}, "statistics": {"viewCount": "900"}}]}

    def fake_get(url, *, params, timeout):
        calls.append((url, params))
        return Details() if url.endswith("/videos") else Response()

    monkeypatch.setattr(youtube_tracks.requests, "get", fake_get)
    monkeypatch.setattr(youtube_tracks.api_usage, "record_request", lambda *_args, **_kwargs: None)

    first = youtube_tracks.find_trailer_url(["Dune: Part Two", "Dune: Part Two"], "2024",
                                            channels=["Legendary Pictures", "Warner Bros. Pictures"])
    second = youtube_tracks.find_trailer_url(["Dune: Part Two"], "2024")

    assert first == second == "https://www.youtube.com/watch?v=official002"  # не YouTube Music, не реакция
    assert len(calls) == 2 and calls[0][1]["q"] == "Dune: Part Two 2024 official trailer"
    assert calls[1][1]["id"] == "official002"  # проверяются только ролики, подошедшие по названию


def test_trailer_lookup_rejects_videos_of_another_film(monkeypatch):
    _memory_cache(monkeypatch)
    monkeypatch.setattr(youtube_tracks.config, "YOUTUBE_API_KEY", "youtube-secret")

    class Response(_Response):
        def json(self):
            return {"items": [{"id": {"videoId": "otherfilm12"},
                               "snippet": {"title": "Another Movie Official Trailer", "publishedAt": "2026-01-01"}}]}

    monkeypatch.setattr(youtube_tracks.requests, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(youtube_tracks.api_usage, "record_request", lambda *_args, **_kwargs: None)

    assert youtube_tracks.find_trailer_url(["De Tatta's 2"], "2026") == ""


def test_trusted_trailer_needs_hd_and_a_studio_channel_or_many_views():
    def video(definition="hd", channel="Some Uploader", views="500"):
        return {"contentDetails": {"definition": definition}, "snippet": {"channelTitle": channel},
                "statistics": {"viewCount": views}}

    studios = ["Warner Bros. Pictures", "HBO"]
    assert youtube_tracks._trusted_trailer(video(channel="Warner Bros. Pictures"), studios)
    assert youtube_tracks._trusted_trailer(video(channel="HBO Max"), studios)  # канал телесети
    assert not youtube_tracks._trusted_trailer(video(definition="sd", channel="HBO"), studios)  # не HD
    assert not youtube_tracks._trusted_trailer(video(), studios)  # чужой канал, мало просмотров — перезалив
    assert youtube_tracks._trusted_trailer(video(views="2500000"), studios)  # очень популярный ролик
    assert not youtube_tracks._trusted_trailer(None, studios)
