import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_concerts
import research


def test_product_research_queries_are_normalized_to_english():
    query = research.english_search_query(
        "Romy official tour dates Нидерланды 2026", "concert_specific",
    )

    assert query == "Romy official tour dates Netherlands 2026"
    assert not any("а" <= char.casefold() <= "я" for char in query)


def test_explicit_user_search_is_not_silently_rewritten():
    query = "Проверь новости на русском"

    assert research.english_search_query(query, "explicit_research") == query


def test_generic_web_search_never_falls_back_to_tavily(monkeypatch):
    monkeypatch.setattr(research, "firecrawl_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(research, "tavily_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("generic search must not call Tavily")))

    assert research.web_search("music recommendation") == []


def test_only_explicit_research_phrase_enables_chat_web_search():
    assert research.requires_explicit_web_search("Проверь в интернете, что сейчас известно")
    assert not research.requires_explicit_web_search("Посоветуй фильм на вечер")


def test_economy_mode_keeps_explicit_research_but_skips_optional_travel(monkeypatch):
    monkeypatch.setattr(research.api_usage, "tavily_budget", lambda: {"mode": "economy"})
    monkeypatch.setattr(research.provider_runtime, "tavily_monthly_quota_exhausted", lambda: False)
    events = []
    monkeypatch.setattr(research.api_usage, "record_tavily_event", lambda scenario, event, **_kw: events.append((scenario, event)))

    assert research._tavily_allowed("explicit_research")
    assert not research._tavily_allowed("travel_current")
    assert ("travel_current", "skipped_policy") in events


def test_advanced_search_requires_explicit_advanced_scenario(monkeypatch):
    monkeypatch.setattr(research.api_usage, "tavily_budget", lambda: {"mode": "normal"})
    monkeypatch.setattr(research.provider_runtime, "tavily_monthly_quota_exhausted", lambda: False)
    monkeypatch.setattr(research.api_usage, "record_tavily_event", lambda *_args, **_kwargs: None)

    assert not research._tavily_allowed("explicit_research", search_depth="advanced")
    assert research._tavily_allowed("explicit_research_advanced", search_depth="advanced")


def test_tavily_cache_avoids_second_network_request(monkeypatch):
    research._TV_CACHE.clear()
    monkeypatch.setattr(research.config, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(research, "_tavily_allowed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(research.api_usage, "record_tavily_event", lambda *_args, **_kwargs: None)
    calls = []

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"results": [{"title": "Official", "url": "https://example.org", "content": "x"}]}

    monkeypatch.setattr(research.requests, "post", lambda *_args, **_kwargs: calls.append(True) or Response())

    assert research.tavily_search("official sources", scenario="explicit_research")
    assert research.tavily_search("  OFFICIAL   sources ", scenario="explicit_research")
    assert len(calls) == 1


def test_category_news_search_uses_news_topic_and_week_filter(monkeypatch):
    research._TV_CACHE.clear()
    monkeypatch.setattr(research.config, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(research, "_tavily_allowed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(research.api_usage, "record_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(research.api_usage, "record_tavily_event", lambda *_args, **_kwargs: None)
    payloads = []

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"results": []}

    monkeypatch.setattr(
        research.requests, "post",
        lambda *_args, **kwargs: payloads.append(kwargs["json"]) or Response(),
    )

    research.tavily_search(
        "important textile news", scenario="category_news",
        topic="news", time_range="week",
    )

    assert payloads[0]["topic"] == "news"
    assert payloads[0]["time_range"] == "week"


def test_bulk_concert_refresh_never_starts_external_artist_search(monkeypatch):
    async def ticketmaster(*_args, **_kwargs):
        return []

    async def external(*_args, **_kwargs):
        raise AssertionError("bulk concert refresh must not call Tavily fallback")

    monkeypatch.setattr(leisure_concerts, "_ticketmaster_events_many", ticketmaster)
    monkeypatch.setattr(leisure_concerts, "get_external_events_for_artist", external)

    assert asyncio.run(leisure_concerts._fetch_concerts(["A", "B"], "NL", "Нидерланды")) == []
