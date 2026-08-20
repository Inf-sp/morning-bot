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
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_reorder_for_cooldown", lambda order: order)

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
        if provider == "groq_standard":
            clock["now"] = 2.5
            raise ai.LLMProviderError(provider, "groq timeout", temporary=True)
        if provider == "cf":
            clock["now"] = 4.5
            raise ai.LLMProviderError(provider, "cloudflare timeout", temporary=True)
        return "Ответ OpenRouter"

    monkeypatch.setattr(ai, "_chat", provider)

    result = ai.chat_chain([{"role": "user", "content": "test"}])

    assert result == "Ответ OpenRouter"
    assert calls == [
        ("groq_standard", 2.5),
        ("cf", 2.0),
        ("openrouter", 2.5),
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

    assert calls == ["groq_standard"]


def test_free_chat_route_uses_the_standard_chain():
    assert ai.CHAT_ORDER == ("groq_standard", "cf", "openrouter")
    assert ai.FREE_CHAT_TIER == "smart"


def test_free_chat_prompt_requires_short_human_answers_for_europe_and_america():
    system = ai._chat_system()

    assert "коротко" in system.casefold()
    assert "простым человеческим языком" in system.casefold()
    assert "европ" in system.casefold()
    assert "сша" in system.casefold()
    assert "до 6 коротких строк" in system.casefold()


def test_free_chat_has_a_small_response_and_time_budget():
    assert ai.FREE_CHAT_MAX_TOKENS <= 350
    assert ai.FREE_CHAT_BUDGET_SECONDS <= 7


def test_free_chat_route_log_identifies_deployment_and_serving_provider(monkeypatch):
    records = []
    monkeypatch.setattr(ai._log, "info", lambda message, *args: records.append(message % args))
    monkeypatch.setattr(ai.config, "APP_VERSION", "1.16.236")
    monkeypatch.setattr(ai.config, "RAILWAY_DEPLOYMENT_ID", "deployment-42")
    monkeypatch.setattr(ai.config, "RAILWAY_REPLICA_ID", "replica-2")

    ai._log_free_chat_route(served_by="openrouter", outcome="success")

    line = records[0]
    assert "scenario=assistant/free_chat" in line
    assert "tier=smart" in line
    assert "provider_chain=groq_standard,cf,openrouter" in line
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


def test_home_cache_warm_schedule_separates_heavy_sections():
    assert bot._HOME_WARM_SCHEDULE == (
        ("myday", "07:00"),
        ("wardrobe", "08:05"),
        ("cooking", "03:20"),
        ("travel", "08:15"),
        ("cinema", "08:20"),
        ("books", "08:25"),
        ("music", "08:30"),
        ("learning", "08:35"),
    )


def test_nightly_premieres_warm_movies_books_and_games(monkeypatch):
    movie_calls = []
    book_calls = []
    game_calls = []

    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["42", "43", "44"])
    monkeypatch.setattr(bot.tracking, "has_active_actions", lambda: False)
    monkeypatch.setattr(
        bot.store, "get_settings",
        lambda cid: {"cc": "NL" if cid in {"42", "43"} else "BE"},
    )

    async def warm_movie(cid):
        movie_calls.append(cid)

    async def warm_books():
        book_calls.append(True)

    async def warm_games(cid):
        game_calls.append(cid)

    monkeypatch.setattr(bot.leisure_movies, "warm_movie_premieres_cache", warm_movie)
    monkeypatch.setattr(bot.leisure_books, "warm_book_premieres_cache", warm_books)
    monkeypatch.setattr(bot.leisure_games, "warm_game_premieres_cache", warm_games)
    monkeypatch.setattr(bot.settings, "notif_on", lambda *_args: True)

    asyncio.run(bot.job_warm_movie_premieres_cache(object()))
    asyncio.run(bot.job_warm_book_premieres_cache(object()))
    asyncio.run(bot.job_warm_game_premieres_cache(object()))

    assert movie_calls == ["42", "44"]
    assert book_calls == [True]
    assert game_calls == ["42", "43", "44"]


def test_nightly_game_premieres_warm_without_weekend_notification(monkeypatch):
    calls = []

    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["42"])
    monkeypatch.setattr(bot.tracking, "has_active_actions", lambda: False)
    monkeypatch.setattr(bot.settings, "notif_on", lambda *_args: False)

    async def warm(cid):
        calls.append(cid)

    monkeypatch.setattr(bot.leisure_games, "warm_game_premieres_cache", warm)

    asyncio.run(bot.job_warm_game_premieres_cache(object()))

    assert calls == ["42"]
