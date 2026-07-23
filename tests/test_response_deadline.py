import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

import ai
import bot
import leisure_books
import tracking


class _Response:
    status_code = 200
    headers = {}
    text = ""

    def json(self):
        return {"ok": True}


def test_provider_timeout_is_clamped_to_remaining_deadline(monkeypatch):
    seen = {}

    def fake_post(*_args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return _Response()

    monkeypatch.setattr(ai.requests, "post", fake_post)
    monkeypatch.setattr(ai.api_usage, "record_request", lambda *_args, **_kwargs: None)

    ai._run_with_deadline(
        "assistant", 0.5,
        lambda: ai._post("https://example.invalid", {}, {}, 40, "groq"),
    )

    assert 0.2 <= seen["timeout"] <= 0.5


def test_chain_does_not_start_another_provider_after_deadline(monkeypatch):
    clock = {"now": 0.0}
    calls = []

    monkeypatch.setattr(ai.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)

    def slow_failure(*_args, **_kwargs):
        calls.append("gemini")
        clock["now"] = 11.0
        raise RuntimeError("slow")

    monkeypatch.setattr(ai, "_gen_gemini", slow_failure)
    monkeypatch.setattr(
        ai, "_gen_groq",
        lambda *_args, **_kwargs: calls.append("groq") or "late answer",
    )

    with pytest.raises(Exception, match="вовремя"):
        ai.llm("Ответь", order=("gemini", "groq"), budget_seconds=10)

    assert calls == ["gemini"]


def test_free_chat_gives_openrouter_its_reserved_remaining_budget(monkeypatch):
    clock = {"now": 0.0}
    calls = []

    monkeypatch.setattr(ai.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_mark_cooldown", lambda *_args: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai.provider_runtime, "activate_fallback", lambda *_args, **_kwargs: None)

    def provider(provider, _history, _system, timeout_cap=None):
        calls.append((provider, timeout_cap))
        if provider == "groq":
            clock["now"] = 3.0
            raise ai.LLMProviderError(provider, "groq timeout", temporary=True)
        if provider == "github_models":
            clock["now"] = 6.0
            raise ai.LLMProviderError(provider, "github timeout", temporary=True)
        return "Ответ OpenRouter"

    monkeypatch.setattr(ai, "_chat", provider)

    result = ai.chat_chain([{"role": "user", "content": "test"}])

    assert result == "Ответ OpenRouter"
    assert calls == [
        ("groq", 3.0),
        ("github_models", 3.0),
        ("openrouter", 4.0),
    ]


def test_free_chat_does_not_start_provider_after_deadline(monkeypatch):
    clock = {"now": 0.0}
    calls = []

    monkeypatch.setattr(ai.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_mark_cooldown", lambda *_args: None)

    def slow_provider(provider, *_args, **_kwargs):
        calls.append(provider)
        clock["now"] = 10.0
        raise ai.LLMProviderError(provider, "timeout", temporary=True)

    monkeypatch.setattr(ai, "_chat", slow_provider)

    with pytest.raises(Exception, match="вовремя"):
        ai.chat_chain([{"role": "user", "content": "test"}])

    assert calls == ["groq"]


def test_free_chat_route_is_utility_without_gemini():
    assert ai.CHAT_ORDER == ("groq", "github_models", "openrouter")
    assert "gemini" not in ai.CHAT_ORDER
    assert ai.FREE_CHAT_TIER == "utility"


def test_free_chat_route_log_identifies_deployment_and_serving_provider(monkeypatch):
    records = []
    monkeypatch.setattr(ai._log, "info", lambda message, *args: records.append(message % args))
    monkeypatch.setattr(ai.config, "APP_VERSION", "1.16.236")
    monkeypatch.setattr(ai.config, "RAILWAY_DEPLOYMENT_ID", "deployment-42")
    monkeypatch.setattr(ai.config, "RAILWAY_REPLICA_ID", "replica-2")

    ai._log_free_chat_route(served_by="openrouter", outcome="success")

    line = records[0]
    assert "scenario=assistant/free_chat" in line
    assert "tier=utility" in line
    assert "provider_chain=groq,github_models,openrouter" in line
    assert "served_by=openrouter" in line
    assert "version=1.16.236" in line
    assert "deployment=deployment-42" in line


def test_action_latency_keeps_only_technical_metadata(monkeypatch):
    memory = {}
    clock = {"now": 10.0}

    monkeypatch.setattr(tracking.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(tracking.store, "_load", lambda key: memory.get(key, {}))
    monkeypatch.setattr(
        tracking.store, "_save", lambda key, value: memory.__setitem__(key, value),
    )

    trace = tracking.start_action("42", "Ассистент", "text", budget_seconds=10)
    clock["now"] = 10.2
    tracking.mark_first_feedback(trace)
    tracking.annotate_action(provider="gemini", cache_hit=False)
    clock["now"] = 12.0
    tracking.finish_action(trace)

    row = memory[tracking.config.ACTION_LATENCY_KEY]["log"][0]
    assert 199 <= row["first_feedback_ms"] <= 200
    assert row["duration_ms"] == 2000
    assert row["provider"] == "gemini"
    assert "prompt" not in row
    assert "response" not in row
    assert tracking.has_active_actions() is False


def test_book_card_skips_optional_network_after_action_budget(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(
        leisure_books.google_books, "enrich_book",
        lambda _item: (_ for _ in ()).throw(AssertionError("network called")),
    )
    monkeypatch.setattr(
        leisure_books, "_book_cover",
        lambda *_args: (_ for _ in ()).throw(AssertionError("network called")),
    )

    trace = tracking.start_action("42", "Книги", "book", budget_seconds=0.1)
    try:
        asyncio.run(leisure_books._send_book_card(
            Bot(), "42", {"title": "1984", "author": "Джордж Оруэлл"}, 0,
        ))
    finally:
        tracking.finish_action(trace)

    assert sent and "1984" in sent[0]["text"]


def test_home_cache_warm_yields_to_active_user_action(monkeypatch):
    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["42"])
    monkeypatch.setattr(bot.tracking, "has_active_actions", lambda: True)
    monkeypatch.setattr(
        bot.wardrobe, "warm_home_cache",
        lambda _cid: (_ for _ in ()).throw(AssertionError("warm started")),
    )

    asyncio.run(bot.job_warm_home_pages(object()))
