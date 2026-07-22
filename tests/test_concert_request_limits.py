import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_concerts


def test_concert_refresh_searches_externally_only_for_a_small_unresolved_subset(monkeypatch):
    searched = []

    async def ticketmaster(*_args, **_kwargs):
        return [{"_artist": "Already found", "id": "ticketmaster-event"}]

    async def external(artist, *_args, **_kwargs):
        searched.append(artist)
        return []

    monkeypatch.setattr(leisure_concerts, "_ticketmaster_events_many", ticketmaster)
    monkeypatch.setattr(leisure_concerts, "get_external_events_for_artist", external)
    monkeypatch.setattr(leisure_concerts, "filter_concert_events", lambda events, _cc: events)

    artists = ["Already found", *[f"Artist {index}" for index in range(8)]]
    asyncio.run(leisure_concerts._fetch_concerts(artists, "NL", "Нидерланды"))

    assert searched == [f"Artist {index}" for index in range(5)]
