import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import pytest

import ai
import provider_runtime
import tracking


@pytest.fixture
def chain(monkeypatch):
    """Real breaker + routing, fake providers; returns the list of called providers."""
    calls = []
    provider_runtime._ai_breaker.clear()
    monkeypatch.setattr(ai, "_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_gemini_cooldown_error", lambda: None)
    monkeypatch.setattr(ai, "_reserve_gemini_for_action", lambda: True)
    monkeypatch.setattr(provider_runtime, "is_configured", lambda _p: True)
    monkeypatch.setattr(provider_runtime, "activate_fallback", lambda *_a, **_k: False)
    monkeypatch.setattr(provider_runtime, "store", _NoStore())

    def dead_gemini(*_a, **_k):
        calls.append("gemini")
        raise ai.LLMProviderError("gemini", "gemini timeout", temporary=True, error_type="ReadTimeout")

    monkeypatch.setattr(ai, "_gen_gemini", dead_gemini)
    monkeypatch.setattr(ai, "_gen_groq", lambda *_a, **_k: calls.append("groq") or "ok")
    yield calls
    provider_runtime._ai_breaker.clear()


class _NoStore:
    def mutate_kv(self, *_a, **_k):
        return None

    def _load(self, *_a, **_k):
        return None


def _ask():
    return ai.llm("q", order=("gemini", "groq"), budget_seconds=10)


def test_dead_gemini_is_skipped_after_two_failures(chain, caplog):
    assert _ask() == "ok" and _ask() == "ok"
    assert chain == ["gemini", "groq", "gemini", "groq"]
    assert "gemini skipped" in caplog.text
    chain.clear()
    _ask()
    assert chain == ["groq"]
    assert caplog.text.count("gemini skipped") == 1


def test_auth_error_opens_breaker_immediately(chain, monkeypatch):
    def denied(*_a, **_k):
        chain.append("gemini")
        raise ai.LLMProviderError("gemini", "gemini 403", status_code=403)

    monkeypatch.setattr(ai, "_gen_gemini", denied)
    _ask()
    chain.clear()
    _ask()
    assert chain == ["groq"]


def test_breaker_lifts_after_cooldown_or_probe(chain, monkeypatch):
    _ask(), _ask()
    assert provider_runtime.ai_breaker_kind("gemini") == "outage"
    # A passing monitor probe (record_history=False) closes it early.
    provider_runtime.record_result("gemini", True, record_history=False)
    assert provider_runtime.ai_breaker_kind("gemini") == ""
    # Expired cooldown: gemini is tried again on the next call.
    _ask(), _ask()
    provider_runtime._ai_breaker["gemini"]["until"] = 0
    chain.clear()
    _ask()
    assert chain == ["gemini", "groq"]


def test_probe_does_not_lift_rate_limit(chain):
    provider_runtime.note_ai_failure("gemini", kind="rate_limit", seconds=300)
    provider_runtime.record_result("gemini", True, record_history=False)
    assert provider_runtime.ai_breaker_kind("gemini") == "rate_limit"
    provider_runtime.record_result("gemini", True)
    assert provider_runtime.ai_breaker_kind("gemini") == ""


def test_all_cooling_providers_are_still_tried_in_order(chain):
    provider_runtime.note_ai_failure("gemini", kind="auth")
    provider_runtime.note_ai_failure("groq", kind="auth")
    _ask()
    assert chain == ["gemini", "groq"]


def test_background_waits_for_primary_with_timeout_breaker_only(chain, monkeypatch):
    monkeypatch.setattr(ai, "BACKGROUND_RETRY_PAUSE_SECONDS", 0)
    _ask(), _ask()
    chain.clear()
    with ai.background_mode():
        _ask()
    assert chain == ["gemini", "gemini", "groq"]  # primary tried + one retry
    provider_runtime.note_ai_failure("gemini", kind="rate_limit", seconds=300)
    chain.clear()
    with ai.background_mode():
        _ask()
    assert chain == ["groq"]


def test_timeouts_and_budget_per_mode(monkeypatch):
    seen = {}

    class _Resp:
        status_code, headers, text = 200, {}, ""

    def fake_post(*_a, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return _Resp()

    monkeypatch.setattr(ai.requests, "post", fake_post)
    monkeypatch.setattr(ai.api_usage, "record_request", lambda *_a, **_k: None)
    post = lambda: ai._post("https://example.invalid", {}, {}, 40, "groq")  # noqa: E731
    budget = lambda module: ai._run_with_deadline(module, None, ai._remaining_seconds)  # noqa: E731

    post()
    assert seen["timeout"] == 5.0
    assert budget("helper") == pytest.approx(ai.STANDARD_BUDGET_SECONDS, abs=0.1)
    assert budget("food") == pytest.approx(ai.COMPLEX_BUDGET_SECONDS, abs=0.1)
    with ai.background_mode():
        post()
        assert seen["timeout"] == ai.BACKGROUND_PROVIDER_TIMEOUT_SECONDS
        assert budget("food") == pytest.approx(ai.BACKGROUND_BUDGET_SECONDS, abs=0.1)
    trace = tracking.start_action("42", "Готовка", "food", budget_seconds=15)
    try:
        assert budget("food") == pytest.approx(ai.LIVE_INTERACTIVE_BUDGET_SECONDS, abs=0.1)
        assert ai._run_with_deadline("food", 12, ai._remaining_seconds) == pytest.approx(12, abs=0.1)
    finally:
        tracking.finish_action(trace)


def test_background_mode_propagates_into_to_thread():
    async def run():
        with ai.background_mode():
            inside = await asyncio.to_thread(ai._is_background)
        outside = await asyncio.to_thread(ai._is_background)
        return inside, outside

    assert asyncio.run(run()) == (True, False)


def test_live_gemini_does_not_wait_for_lock_held_by_background(monkeypatch):
    monkeypatch.setattr(ai, "_gemini_cooldown_error", lambda: None)
    ai._GEMINI_RATE_LOCK.acquire()
    try:
        with pytest.raises(ai.LLMProviderError, match="deadline"):
            ai._run_with_deadline("helper", 0.5, lambda: ai._gen_gemini("q", 10, 0.1))
    finally:
        ai._GEMINI_RATE_LOCK.release()
