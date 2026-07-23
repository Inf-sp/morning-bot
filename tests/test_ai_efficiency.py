import os
import threading
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

import ai
import tracking


def test_cache_key_does_not_change_when_provider_order_changes():
    first = ai._cache_key(
        ("gemini", "github_models"), "same prompt", 300, 0.2, "travel", "json",
    )
    fallback = ai._cache_key(
        ("github_models", "groq"), "same prompt", 300, 0.2, "travel", "json",
    )

    assert first == fallback


def test_cache_key_ignores_action_id_and_prompt_whitespace():
    compact = ai._cache_key(
        ("gemini", "github_models"), "Выбери страну", 300, 0.2, "travel", "json",
    )
    formatted = ai._cache_key(
        ("github_models", "groq"), "  Выбери\n\n страну  ", 300, 0.2, "travel", "json",
    )

    first = tracking.start_action("42", "Поездка", "first")
    try:
        first_action_key = ai._cache_key(
            ("gemini",), "Выбери страну", 300, 0.2, "travel", "json",
        )
    finally:
        tracking.finish_action(first)
    second = tracking.start_action("42", "Поездка", "second")
    try:
        second_action_key = ai._cache_key(
            ("gemini",), "Выбери страну", 300, 0.2, "travel", "json",
        )
    finally:
        tracking.finish_action(second)

    assert compact == formatted == first_action_key == second_action_key


def test_utility_routes_do_not_start_with_gemini():
    for module in (
        "learning", "learning_trainer", "learning_dict_add", "health", "trainer",
        "medicine", "doctor", "dictionary_import", "wardrobe_utility", "travel_utility",
    ):
        assert "gemini" not in ai._resolve(None, None, module=module)


def test_final_card_routes_keep_gemini_as_the_single_premium_primary():
    for module in ("travel", "food", "wardrobe"):
        assert ai._resolve(None, None, module=module)[0] == "gemini"


def test_cache_hit_does_not_check_gemini_cooldown(monkeypatch):
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: '{"ok":true}')
    monkeypatch.setattr(
        ai, "_gemini_cooldown_error",
        lambda: (_ for _ in ()).throw(AssertionError("cooldown checked before cache")),
    )

    assert ai.llm_json("same", module="travel") == {"ok": True}


def test_gemini_does_not_retry_a_rate_limit(monkeypatch):
    calls = []

    def limited(*_args, **_kwargs):
        calls.append("gemini")
        raise ai.LLMProviderError(
            "gemini", "limited", status_code=429, temporary=True,
            error_type="rate_limit", retry_after=1,
        )

    monkeypatch.setattr(ai, "_post", limited)
    monkeypatch.setattr(ai.api_usage, "gemini_requests", lambda **_kwargs: {"allowed": True})

    with pytest.raises(ai.LLMProviderError):
        ai._gen_gemini("prompt", 20, 0.0, "plain_text")

    assert calls == ["gemini"]


def test_one_action_can_use_gemini_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_reorder_for_cooldown", lambda order: order)
    monkeypatch.setattr(ai, "_gen_gemini", lambda *_args: calls.append("gemini") or "first")
    monkeypatch.setattr(ai, "_gen_groq", lambda *_args: calls.append("groq") or "second")

    trace = tracking.start_action("42", "Поездка", "travel", budget_seconds=10)
    try:
        assert ai.llm("one", order=("gemini", "groq")) == "first"
        assert ai.llm("two", order=("gemini", "groq")) == "second"
    finally:
        tracking.finish_action(trace)

    assert calls == ["gemini", "groq"]


def test_parallel_calls_reserve_one_gemini_slot_atomically():
    class RaceDict(dict):
        def __init__(self):
            super().__init__()
            self.barrier = threading.Barrier(2)

        def get(self, key, default=None):
            value = super().get(key, default)
            try:
                self.barrier.wait(timeout=0.05)
            except threading.BrokenBarrierError:
                pass
            return value

    trace = tracking.start_action("42", "Поездка", "parallel", budget_seconds=10)
    trace.provider_calls = RaceDict()
    def reserve(_i):
        token = tracking._current_action.set(trace)
        try:
            return tracking.consume_provider_budget("gemini", limit=1)
        finally:
            tracking._current_action.reset(token)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
    finally:
        tracking.finish_action(trace)

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_premium_fallback_keeps_action_statistics(monkeypatch):
    memory = {}
    calls = []
    monkeypatch.setattr(tracking.store, "_load", lambda key: memory.get(key, {}))
    monkeypatch.setattr(tracking.store, "_save", lambda key, value: memory.__setitem__(key, value))
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_reorder_for_cooldown", lambda order: order)
    monkeypatch.setattr(ai, "_mark_cooldown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)

    def unavailable(*_args, **_kwargs):
        calls.append("gemini")
        raise ai.LLMProviderError("gemini", "temporary", status_code=503, temporary=True)

    monkeypatch.setattr(ai, "_gen_gemini", unavailable)
    monkeypatch.setattr(ai, "_gen_github_models", lambda *_args: calls.append("github_models") or '{"ok":true}')

    trace = tracking.start_action("42", "Поездка", "country", budget_seconds=10)
    try:
        assert ai.llm_json("country", module="travel") == {"ok": True}
    finally:
        tracking.finish_action(trace)

    row = memory[tracking.config.ACTION_LATENCY_KEY]["log"][0]
    assert calls == ["gemini", "github_models"]
    assert row["requested_tier"] == "premium"
    assert row["primary"] == "gemini"
    assert row["primary_status"] == "503"
    assert row["served_by"] == "github_models"
    assert row["gemini_calls"] == 1


def test_second_action_uses_cached_premium_answer_without_gemini(monkeypatch):
    memory = {}
    calls = []
    monkeypatch.setattr(ai.store, "_load", lambda key: memory.get(key, {}))
    monkeypatch.setattr(ai.store, "_save", lambda key, value: memory.__setitem__(key, value))
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_reorder_for_cooldown", lambda order: order)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_gen_gemini", lambda *_args: calls.append("gemini") or '{"ok":true}')

    first = tracking.start_action("42", "Поездка", "first", budget_seconds=10)
    try:
        assert ai.llm_json("country", module="travel") == {"ok": True}
    finally:
        tracking.finish_action(first)
    second = tracking.start_action("42", "Поездка", "second", budget_seconds=10)
    try:
        assert ai.llm_json("country", module="travel") == {"ok": True}
    finally:
        tracking.finish_action(second)

    assert calls == ["gemini"]
    rows = memory[tracking.config.ACTION_LATENCY_KEY]["log"]
    assert rows[-1]["cache_hit"] is True
    assert rows[-1]["gemini_calls"] == 0
