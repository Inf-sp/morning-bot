import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import ai
import api_usage
import tracking
from ui import admin as admin_ui

REAL_RECENTLY_ACTIVE = tracking.recently_active


def _memory(monkeypatch):
    saved = {}
    monkeypatch.setattr(api_usage.store, "mutate_kv", lambda _key, fn: saved.update(fn(saved)[0]))
    monkeypatch.setattr(api_usage.store, "_load", lambda _key: saved)
    return saved


def test_tokens_are_read_from_gemini_and_openai_compatible_answers():
    assert api_usage.response_tokens({"usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 120,
                                                        "thoughtsTokenCount": 30}}) == (900, 150)
    assert api_usage.response_tokens({"usage": {"prompt_tokens": 400, "completion_tokens": 80}}) == (400, 80)
    assert api_usage.response_tokens({"result": {"usage": {"prompt_tokens": 5, "completion_tokens": 7}}}) == (5, 7)
    assert api_usage.response_tokens({"choices": []}) == (0, 0)


def test_module_usage_counts_requests_tokens_background_and_cache(monkeypatch):
    _memory(monkeypatch)
    api_usage.record_ai_module("food", provider="gemini", tokens_in=1000, tokens_out=200)
    api_usage.record_ai_module("food", provider="groq", ok=False, background=True)
    api_usage.record_ai_module("food", cached=True)
    row = api_usage.ai_module_usage(1)["food"]
    assert (row["req"], row["fail"], row["bg"], row["cache"], row["in"], row["out"]) == (2, 1, 1, 1, 1000, 200)
    assert row["providers"] == {"gemini": 1, "groq": 1}
    text = admin_ui.ai_usage(api_usage.ai_module_usage(1), api_usage.ai_module_usage(7)).text
    assert "• Кухня — 2 запр. · 1 тыс. ток. · сбоев 1 · фон 1 · кэш 1" in text


def test_successful_provider_answer_is_counted_under_the_calling_module(monkeypatch):
    seen = []
    monkeypatch.setattr(ai.api_usage, "record_ai_module", lambda module, **kw: seen.append((module, kw)))

    class Resp:
        status_code, headers, text = 200, {}, ""

        def json(self):
            return {"usage": {"prompt_tokens": 50, "completion_tokens": 10}}

    monkeypatch.setattr(ai.requests, "post", lambda *_a, **_k: Resp())
    monkeypatch.setattr(ai.api_usage, "record_request", lambda *_a, **_k: None)
    token = ai._AI_MODULE.set("ns")
    try:
        ai._post("https://example.invalid", {}, {}, 5, "groq")
    finally:
        ai._AI_MODULE.reset(token)
    ai._post("https://example.invalid", {}, {}, 5, "groq")  # служебный запрос без раздела — не считается
    assert seen == [("ns", {"provider": "groq", "ok": True, "tokens_in": 50, "tokens_out": 10, "background": False})]


def test_night_warm_is_only_for_recently_active_users(monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(tracking, "_all", lambda: {"1": {"last_ts": now - 3600}, "2": {"last_ts": now - 5 * 86400}})
    assert REAL_RECENTLY_ACTIVE(["1", "2", "3"], 3, now=now) == ["1"]
