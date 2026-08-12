import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_concerts


def test_concert_refresh_searches_externally_only_for_a_small_unresolved_subset(monkeypatch):
    searched = []
    ticketmaster_calls = []

    async def ticketmaster(*args, **kwargs):
        ticketmaster_calls.append((args, kwargs))
        return [{"_artist": "Already found", "id": "ticketmaster-event"}]

    async def external(artist, *_args, **_kwargs):
        searched.append(artist)
        return []

    monkeypatch.setattr(leisure_concerts, "_ticketmaster_events_many", ticketmaster)
    monkeypatch.setattr(leisure_concerts, "get_external_events_for_artist", external)
    monkeypatch.setattr(leisure_concerts, "filter_concert_events", lambda events, _cc: events)

    artists = ["Already found", *[f"Artist {index}" for index in range(8)]]
    asyncio.run(leisure_concerts._fetch_concerts(artists, "NL", "Нидерланды"))

    assert ticketmaster_calls[0][1]["size"] == 200
    assert ticketmaster_calls[0][1]["limit"] == 8
    assert searched == [f"Artist {index}" for index in range(5)]
