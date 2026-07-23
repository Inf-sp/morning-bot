import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_concerts


class _RateLimitedResponse:
    status_code = 429
    headers = {"Retry-After": "90"}


def test_ticketmaster_429_stops_batch_without_retry(monkeypatch):
    """The first 429 must prevent retries and all queued artist requests."""
    network_calls = []
    recorded = []
    cooldown = {"seconds": 0}

    def fake_get(*_args, **_kwargs):
        network_calls.append("request")
        return _RateLimitedResponse()

    def fake_record(_service, **kwargs):
        recorded.append(kwargs)
        if kwargs.get("status_code") == 429:
            cooldown["seconds"] = 90

    monkeypatch.setattr(leisure_concerts.config, "TICKETMASTER_API_KEY", "ticketmaster-key")
    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setattr(leisure_concerts.api_usage, "record_request", fake_record)
    monkeypatch.setattr(
        leisure_concerts, "_ticketmaster_cooldown_remaining", lambda: cooldown["seconds"],
    )
    monkeypatch.setattr(leisure_concerts.util, "ttl_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(leisure_concerts.util, "ttl_set", lambda _namespace, _key, value: value)

    result = asyncio.run(leisure_concerts._ticketmaster_events_many(
        ["Artist A", "Artist B", "Artist C", "Artist D", "Artist E"], "NL",
    ))

    assert result == []
    assert network_calls == ["request"]
    assert [row["status_code"] for row in recorded] == [429]
