import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import config
import provider_runtime
import research
import service_monitor


def _memory_store(monkeypatch):
    memory = {}

    def load(key):
        return memory.get(key, {})

    def mutate(key, callback):
        data, result = callback(memory.get(key, {}))
        memory[key] = data
        return result

    monkeypatch.setattr(provider_runtime.store, "_load", load)
    monkeypatch.setattr(provider_runtime.store, "mutate_kv", mutate)
    return memory


def _ts(year, month, day):
    return int(datetime(year, month, day, 12, tzinfo=config.TZ).timestamp())


def test_tavily_monthly_limit_blocks_until_first_day_of_next_month(monkeypatch):
    _memory_store(monkeypatch)
    july_23 = _ts(2026, 7, 23)

    provider_runtime.record_result("tavily", False, status_code=432,
                                   error="monthly credits exhausted", checked_at=july_23)

    state = provider_runtime.get_state("tavily")
    assert state["quota_state"] == provider_runtime.MONTHLY_QUOTA_EXHAUSTED
    assert provider_runtime.reset_date_label(state["quota_reset_at"]) == "1 августа"
    assert provider_runtime.tavily_monthly_quota_exhausted(july_23)
    assert service_monitor.format_row("tavily").endswith("до 1 августа")
    assert "восстановлен" not in " ".join(event["text"] for event in provider_runtime.history())


def test_tavily_monthly_block_skips_search_and_health_check(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.mark_tavily_monthly_quota_exhausted(checked_at=_ts(2026, 7, 23), quota_total=1000)
    monkeypatch.setattr(research.config, "TAVILY_API_KEY", "test-key")
    calls = []

    def no_request(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("Tavily must be skipped during monthly block")

    monkeypatch.setattr(research.requests, "post", no_request)
    monkeypatch.setattr(service_monitor.requests, "request", no_request)

    assert research.tavily_search("artist tour dates") == []
    assert service_monitor.probe("tavily") is False
    assert calls == []


def test_web_search_keeps_firecrawl_result_without_consulting_tavily(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.mark_tavily_monthly_quota_exhausted(checked_at=_ts(2026, 7, 23), quota_total=1000)
    monkeypatch.setattr(research, "firecrawl_search", lambda *_args, **_kwargs: [
        {"title": "Official", "url": "https://example.org", "content": "source"},
    ])
    monkeypatch.setattr(research, "tavily_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("fallback should not run")))

    assert research.web_search("official source", max_results=1)[0]["url"] == "https://example.org"


def test_web_search_skips_tavily_fallback_after_empty_firecrawl(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.mark_tavily_monthly_quota_exhausted(checked_at=_ts(2026, 7, 23), quota_total=1000)
    monkeypatch.setattr(research, "firecrawl_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(research, "tavily_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("monthly Tavily fallback must be skipped")))

    assert research.web_search("official source", max_results=1) == []


def test_regular_tavily_429_is_short_rate_limit_not_monthly(monkeypatch):
    _memory_store(monkeypatch)
    now = _ts(2026, 7, 23)

    provider_runtime.record_result("tavily", False, status_code=429,
                                   error="HTTP 429 too many requests", checked_at=now)

    state = provider_runtime.get_state("tavily")
    assert state["quota_state"] == provider_runtime.RATE_LIMIT_COOLDOWN
    assert state["cooldown_until"] >= now + 60
    assert not provider_runtime.tavily_monthly_quota_exhausted(now)


def test_monthly_block_is_cleared_at_reset_without_probe(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.mark_tavily_monthly_quota_exhausted(checked_at=_ts(2026, 7, 23), quota_total=1000)

    assert not provider_runtime.tavily_monthly_quota_exhausted(_ts(2026, 8, 1))
    state = provider_runtime.get_state("tavily")
    assert state["quota_state"] == ""
    assert state["quota_remaining"] is None


def test_zero_usage_snapshot_confirms_monthly_block(monkeypatch):
    _memory_store(monkeypatch)

    provider_runtime.record_result("tavily", True, quota_remaining=0, quota_total=1000,
                                   checked_at=_ts(2026, 7, 23), record_history=False)

    assert provider_runtime.get_state("tavily")["quota_state"] == provider_runtime.MONTHLY_QUOTA_EXHAUSTED
