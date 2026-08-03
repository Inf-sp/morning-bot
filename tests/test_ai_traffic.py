import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import ai
import admin
import tracking


def test_ai_traffic_groups_attempts_by_actor_and_source(monkeypatch):
    state = {"log": []}
    monkeypatch.setattr(ai.store, "_load", lambda _key: state)
    monkeypatch.setattr(ai.store, "_save", lambda _key, value: state.update(value))
    monkeypatch.setattr(tracking, "current_action", lambda: type("Trace", (), {
        "cid": "42", "section": "Обучение", "action": "a_train_nl",
    })())

    ai._record_ai_attempt("groq_standard", "qwen3.6-27b", "learning", ok=True, latency_ms=320)
    ai._record_ai_attempt("github_models", "gpt-4o-mini", "learning", ok=False,
                          latency_ms=80, failure="HTTP 429")

    summary = ai.ai_traffic_summary()

    assert summary["total"] == 2
    assert summary["failed"] == 1
    assert summary["sources"] == [{
        "origin": "Пользователь", "section": "Обучение", "actor": "42",
        "attempts": 2, "failed": 1, "cache_hits": 0,
    }]


def test_admin_ai_traffic_rows_show_background_and_user_sources(monkeypatch):
    monkeypatch.setattr(ai, "ai_traffic_summary", lambda: {
        "total": 12,
        "failed": 2,
        "cache_hits": 3,
        "sources": [
            {"origin": "Пользователь", "section": "Обучение", "actor": "42",
             "attempts": 7, "failed": 1, "cache_hits": 2},
            {"origin": "Фон", "section": "Готовка", "actor": "",
             "attempts": 5, "failed": 1, "cache_hits": 1},
        ],
    })
    monkeypatch.setattr(admin.store, "get_profile", lambda cid: {"name": "Света"} if cid == "42" else {})

    assert admin._ai_traffic_rows() == [
        "12 попыток · 3 из кэша · 2 ошибки",
        "• Света · Обучение — 7 · 1 ошибка",
        "• Фон · Готовка — 5 · 1 ошибка",
    ]


def test_ai_traffic_records_five_minute_peak(monkeypatch):
    state = {"log": [
        {"ts": 1_784_464_210, "ok": True, "cache_hit": False,
         "origin": "Фон", "section": "Гардероб", "actor": "", "provider": "groq"},
        {"ts": 1_784_464_300, "ok": False, "cache_hit": False,
         "origin": "Фон", "section": "Поездка", "actor": "", "provider": "gemini"},
        {"ts": 1_784_464_400, "ok": False, "cache_hit": False,
         "origin": "Фон", "section": "Готовка", "actor": "", "provider": "openrouter"},
    ]}
    monkeypatch.setattr(ai.store, "_load", lambda _key: state)
    monkeypatch.setattr(ai.time, "time", lambda: 1_784_464_600)

    summary = ai.ai_traffic_summary()

    assert summary["peak"] == {"ts": 1_784_464_200, "attempts": 3, "failed": 2}
