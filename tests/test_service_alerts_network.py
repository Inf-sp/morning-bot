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
