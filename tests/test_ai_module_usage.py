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


def test_failed_or_empty_background_build_waits_an_hour(monkeypatch):
    import util
    clock = [1000.0]
    monkeypatch.setattr(util.time, "monotonic", lambda: clock[0])
    runs = []

    class SyncThread:
        def __init__(self, target, **_k):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(util.threading, "Thread", SyncThread)
    util._OFF_LOOP_STARTED.pop("k", None)
    util.build_off_loop("k", lambda: runs.append(1) or {})  # пусто — ИИ не ответил
    clock[0] += util.OFF_LOOP_RETRY_SECONDS + 1
    util.build_off_loop("k", lambda: runs.append(2) or {})
    assert runs == [1]  # через 10 минут — ещё рано
    clock[0] += util.OFF_LOOP_FAIL_SECONDS
    util.build_off_loop("k", lambda: runs.append(3) or {"ok": 1})
    assert runs == [1, 3]


def test_city_event_pages_go_to_ai_only_around_dates_of_the_window():
    from datetime import date
    import local_events
    pattern = local_events._date_pattern(date(2026, 11, 5), date(2026, 11, 19))
    page = "Welkom in Alkmaar. " + "x" * 2000 + " Sinterklaasintocht op zaterdag 14 november 2026 bij de Kaasmarkt. " + "y" * 2000
    focused = local_events._focus(page, pattern)
    assert "14 november 2026" in focused and len(focused) < 600 and "Welkom" not in focused
    assert local_events._focus("Openingstijden van het museum en contact.", pattern) == ""
    assert local_events._focus("Markt op 21-11 en 2026-11-28.", pattern)
