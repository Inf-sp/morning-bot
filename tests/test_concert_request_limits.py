import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_concerts


def _memory_store(monkeypatch):
    memory = {}

    def load(key):
        return memory.get(key, {})

    def mutate(key, callback):
        data, result = callback(memory.get(key, {}))
        memory[key] = data
        return result

    monkeypatch.setattr(leisure_concerts.store, "_load", load)
    monkeypatch.setattr(leisure_concerts.store, "mutate_kv", mutate)
    return memory


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


def test_known_future_concert_skips_artist_until_it_has_passed(monkeypatch):
    _memory_store(monkeypatch)
    event = {"_artist": "Romy", "dates": {"start": {"localDate": "2099-08-20"}}}
    leisure_concerts._save_artist_check_results("42", ["Romy"], "NL", [event])

    assert not leisure_concerts._artist_is_due("42", "Romy", "NL")

    passed = {"_artist": "Romy", "dates": {"start": {"localDate": "2000-08-20"}}}
    leisure_concerts._save_artist_check_results("42", ["Romy"], "NL", [passed])
    assert leisure_concerts._artist_is_due("42", "Romy", "NL")


def test_new_artist_is_checked_once_then_reused_until_known_concert(monkeypatch):
    _memory_store(monkeypatch)
    ticketmaster_calls = []

    async def ticketmaster(artists, *_args, **_kwargs):
        ticketmaster_calls.append(artists)
        return [{
            "id": "rom-2099", "_artist": "Romy",
            "dates": {"start": {"localDate": "2099-08-20"}},
            "_embedded": {"venues": [{"city": {"name": "Amsterdam"}}]},
        }]

    async def external(*_args, **_kwargs):
        raise AssertionError("Ticketmaster already found the artist")

    monkeypatch.setattr(leisure_concerts, "_ticketmaster_events_many", ticketmaster)
    monkeypatch.setattr(leisure_concerts, "get_external_events_for_artist", external)
    monkeypatch.setattr(leisure_concerts, "filter_concert_events", lambda events, _cc: events)

    first = asyncio.run(leisure_concerts._fetch_concerts(
        ["Romy"], "NL", "Нидерланды", cid="42", force_artists=["Romy"],
    ))
    second = asyncio.run(leisure_concerts._fetch_concerts(
        ["Romy"], "NL", "Нидерланды", cid="42",
    ))

    assert [event["id"] for event in first] == ["rom-2099"]
    assert [event["id"] for event in second] == ["rom-2099"]
    assert ticketmaster_calls == [["Romy"]]


def test_artists_outside_ticketmaster_batch_remain_due_for_the_next_pass(monkeypatch):
    _memory_store(monkeypatch)
    artists = [*[f"Artist {index}" for index in range(8)], "Evanescence"]

    async def ticketmaster(batch, *_args, **_kwargs):
        assert batch == artists[:8]
        return []

    async def external(*_args, **_kwargs):
        return []

    monkeypatch.setattr(leisure_concerts, "_ticketmaster_events_many", ticketmaster)
    monkeypatch.setattr(leisure_concerts, "get_external_events_for_artist", external)
    monkeypatch.setattr(leisure_concerts, "filter_concert_events", lambda events, _cc: events)

    asyncio.run(leisure_concerts._fetch_concerts(artists, "NL", "Нидерланды", cid="42"))

    assert leisure_concerts._artist_is_due("42", "Evanescence", "NL")
