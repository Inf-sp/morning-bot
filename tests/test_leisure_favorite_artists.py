"""⭐ Новые концерты любимых артистов: дедупликация seen-ID и событийная отправка."""
import asyncio

import pytest

import config
import leisure
import store


def _fake_event(id_=None, artist="Charli xcx", date="2026-08-01", city="Amsterdam", url="https://example.com"):
    return {
        "id": id_,
        "_artist": artist,
        "url": url,
        "dates": {"start": {"localDate": date}},
        "_embedded": {"venues": [{"city": {"name": city}}]},
    }


class _NoopBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


@pytest.mark.unit
def test_concert_event_id_prefers_native_id():
    e = _fake_event(id_="tm-123")
    assert leisure._concert_event_id(e) == "tm-123"


@pytest.mark.unit
def test_concert_event_id_falls_back_to_artist_date_city():
    e = _fake_event(id_=None, artist="Charli XCX", date="2026-08-01", city="Amsterdam")
    assert leisure._concert_event_id(e) == "charli xcx:2026-08-01:amsterdam"


@pytest.mark.unit
def test_seen_concerts_roundtrip():
    cid = "fav-artists-seen-1"
    assert not leisure._seen_concerts_has_history(cid)

    leisure._seen_concerts_add(cid, ["a", "b"])

    assert leisure._seen_concerts_has_history(cid)
    assert leisure._seen_concerts_get(cid) == {"a", "b"}


@pytest.mark.unit
def test_seen_concerts_add_deduplicates_preserving_order():
    cid = "fav-artists-seen-2"
    leisure._seen_concerts_add(cid, ["a", "b"])
    leisure._seen_concerts_add(cid, ["b", "c"])

    assert store._load(config.SEEN_CONCERTS_KEY)[cid] == ["a", "b", "c"]


@pytest.mark.unit
def test_seen_concerts_add_respects_limit(monkeypatch):
    monkeypatch.setattr(leisure, "_SEEN_CONCERTS_LIMIT", 3)
    cid = "fav-artists-seen-3"
    leisure._seen_concerts_add(cid, ["a", "b", "c"])
    leisure._seen_concerts_add(cid, ["d"])

    assert store._load(config.SEEN_CONCERTS_KEY)[cid] == ["b", "c", "d"]


@pytest.mark.unit
def test_find_new_favorite_concerts_returns_only_unseen(monkeypatch):
    cid = "fav-artists-find-1"
    events = [_fake_event(id_="e1", artist="A"), _fake_event(id_="e2", artist="B")]
    monkeypatch.setattr(leisure, "_fetch_favorite_events", _async_return(events))
    leisure._seen_concerts_add(cid, ["e1"])

    new_events = asyncio.run(leisure.find_new_favorite_concerts(cid))

    assert [e["id"] for e in new_events] == ["e2"]


@pytest.mark.unit
def test_first_run_seeds_seen_without_sending(monkeypatch):
    cid = "fav-artists-first-1"
    events = [_fake_event(id_="e1"), _fake_event(id_="e2")]
    monkeypatch.setattr(leisure, "_fetch_favorite_events", _async_return(events))
    bot = _NoopBot()

    asyncio.run(leisure.send_new_concerts_notif(bot, cid))

    assert bot.sent == []
    assert leisure._seen_concerts_get(cid) == {"e1", "e2"}


@pytest.mark.unit
def test_second_run_sends_only_new_events(monkeypatch):
    cid = "fav-artists-second-1"
    leisure._seen_concerts_add(cid, ["e1"])
    events = [_fake_event(id_="e1", artist="Zemfira"), _fake_event(id_="e2", artist="Monetochka")]
    monkeypatch.setattr(leisure, "_fetch_favorite_events", _async_return(events))
    bot = _NoopBot()

    asyncio.run(leisure.send_new_concerts_notif(bot, cid))

    assert len(bot.sent) == 1
    assert "Monetochka" in bot.sent[0]["text"]
    assert "Zemfira" not in bot.sent[0]["text"]
    assert leisure._seen_concerts_get(cid) == {"e1", "e2"}


@pytest.mark.unit
def test_no_new_events_sends_nothing(monkeypatch):
    cid = "fav-artists-nonew-1"
    events = [_fake_event(id_="e1")]
    leisure._seen_concerts_add(cid, ["e1"])
    monkeypatch.setattr(leisure, "_fetch_favorite_events", _async_return(events))
    bot = _NoopBot()

    asyncio.run(leisure.send_new_concerts_notif(bot, cid))

    assert bot.sent == []
