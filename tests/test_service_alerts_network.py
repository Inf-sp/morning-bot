"""Разовый обрыв сети — не тревога админу; долгий сбой — тревога."""
import time

import provider_runtime
import service_monitor


def _state(monkeypatch, state):
    monkeypatch.setattr(service_monitor, "_load", lambda: {"services": {"tavily": state}})
    monkeypatch.setattr(service_monitor, "_configured", lambda service: service == "tavily")
    monkeypatch.setattr(service_monitor.config, "API_FREE_LIMITS", {})
    monkeypatch.setattr(service_monitor.api_usage, "tavily_budget", lambda: {"total": 0, "remaining": 0})


def test_network_error_is_a_warning_not_down():
    assert provider_runtime._friendly_error("ConnectionError", None, "tavily") == ("network", "ошибка сети")


def test_short_network_blip_is_not_an_admin_alert(monkeypatch):
    now = int(time.time())
    _state(monkeypatch, {"status": "warning", "error_type": "network", "last_error": "ошибка сети",
                         "incident_started_at": now - 600, "last_success": now - 900})
    assert service_monitor.attention() == []


def test_long_outage_is_reported(monkeypatch):
    now = int(time.time())
    _state(monkeypatch, {"status": "warning", "error_type": "network", "last_error": "ошибка сети",
                         "incident_started_at": now - 4 * 3600, "last_success": now - 5 * 3600})
    assert service_monitor.attention() == ["Tavily — не отвечает больше 3 часов (ошибка сети)"]


def test_admin_chat_gets_only_critical_not_usage_percent(monkeypatch):
    _state(monkeypatch, {"status": "ok"})
    monkeypatch.setattr(service_monitor.config, "API_FREE_LIMITS", {"openrouter": ("day", "requests", 50)})
    monkeypatch.setattr(service_monitor, "_configured", lambda service: service == "openrouter")
    monkeypatch.setattr(service_monitor.api_usage, "limit_state",
                        lambda _s: {"used": 42, "limit": 50, "period": "day", "share": 0.84})
    monkeypatch.setattr(service_monitor.api_usage, "limit_warning", lambda *_a: "OpenRouter — израсходовано 84%")
    assert service_monitor.attention(critical_only=True) == []  # 84% — не повод писать
    assert service_monitor.attention() == ["OpenRouter — израсходовано 84%"]  # на экране админки — видно
    monkeypatch.setattr(service_monitor.api_usage, "limit_state",
                        lambda _s: {"used": 50, "limit": 50, "period": "day", "share": 1.0})
    assert service_monitor.attention(critical_only=True) == [
        "OpenRouter — лимит на сегодня исчерпан: бот перешёл на резерв"]


def test_gemini_timeouts_are_one_summary_line_not_errors(monkeypatch):
    import admin
    now = int(time.time())
    history = [{"ts": now - 60 * i, "event_type": "error", "service": "gemini", "text": "Gemini: сервис не ответил."}
               for i in range(3)] + [{"ts": now - 30, "event_type": "error", "service": "gemini",
                                      "text": "Gemini: временно недоступен.", "status_code": 503}]
    monkeypatch.setattr(admin.provider_runtime, "history", lambda limit=200: history)
    monkeypatch.setattr(admin.tracking, "get_errors", lambda limit=200: [])
    rows = admin._active_error_rows()
    assert len(rows) == 1 and rows[0].startswith("Gemini · не отвечал · 4 раза за сутки")
    assert rows[0].endswith("ответы через резерв")
    assert admin._active_error_rows(summaries=False) == []  # кнопки «⚠️ Ошибки» нет — ошибок бота нет
