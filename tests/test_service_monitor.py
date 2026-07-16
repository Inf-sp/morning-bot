import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import service_monitor


def _memory_store(monkeypatch):
    memory = {}

    def load(key):
        return memory.get(key, {})

    def mutate(key, callback):
        data, result = callback(memory.get(key, {}))
        memory[key] = data
        return result

    monkeypatch.setattr(service_monitor.store, "_load", load)
    monkeypatch.setattr(service_monitor.store, "mutate_kv", mutate)
    return memory


def test_every_service_exposes_the_same_state_shape(monkeypatch):
    _memory_store(monkeypatch)
    required = {
        "status", "quota_remaining", "quota_total", "fallback",
        "last_check", "last_success", "last_error",
    }

    assert service_monitor.states()
    assert all(required <= set(state) for state in service_monitor.states())


def test_category_is_everywhere_for_two_or_more_sections():
    assert service_monitor.SPEC_BY_KEY["gemini"].category == "Везде"
    assert service_monitor.SPEC_BY_KEY["spoonacular"].category == "Готовка"


def test_quota_rows_show_remaining_not_usage(monkeypatch):
    _memory_store(monkeypatch)
    service_monitor.record_result(
        "openweather", True, quota_remaining=998, quota_total=1000,
    )

    assert service_monitor.format_row("openweather") == (
        "🟢 OpenWeather · Везде · осталось 998 из 1 000"
    )


def test_one_remaining_request_uses_singular(monkeypatch):
    _memory_store(monkeypatch)
    service_monitor.record_result("spoonacular", True, quota_remaining=1, quota_total=150)

    assert service_monitor.format_row("spoonacular") == (
        "🟢 Spoonacular · Готовка · остался 1 запрос"
    )


def test_exhausted_quota_is_yellow(monkeypatch):
    _memory_store(monkeypatch)
    service_monitor.record_result("gemini", True, quota_remaining=0, quota_total=20)

    assert service_monitor.format_row("gemini") == (
        "🟡 Gemini · Везде · лимит исчерпан"
    )


def test_fallback_is_hidden_until_target_really_succeeds(monkeypatch):
    _memory_store(monkeypatch)
    service_monitor.record_result("tavily", False, status_code=429)

    assert service_monitor.activate_fallback("tavily", "firecrawl") is False
    assert "Firecrawl" not in service_monitor.format_row("tavily")

    service_monitor.record_result("firecrawl", True)
    assert service_monitor.activate_fallback("tavily", "firecrawl") is True
    assert service_monitor.format_row("tavily").endswith("· Firecrawl")


def test_fallback_graph_has_no_cycles():
    assert service_monitor.validate_fallback_graph() == []
    assert "tavily" not in service_monitor.SPEC_BY_KEY["firecrawl"].fallbacks


def test_success_clears_error_and_disables_fallback(monkeypatch):
    _memory_store(monkeypatch)
    service_monitor.record_result("tavily", False, error="timeout")
    service_monitor.record_result("firecrawl", True)
    service_monitor.activate_fallback("tavily", "firecrawl")

    service_monitor.record_result("tavily", True)
    state = service_monitor.get_state("tavily")

    assert state["status"] == service_monitor.OK
    assert state["last_error"] == ""
    assert state["fallback"] == ""
    assert any("резерв отключён" in event["text"] for event in service_monitor.history())


def test_failed_active_fallback_is_removed_immediately(monkeypatch):
    _memory_store(monkeypatch)
    service_monitor.record_result("tavily", False, error="timeout")
    service_monitor.record_result("firecrawl", True)
    service_monitor.activate_fallback("tavily", "firecrawl")

    service_monitor.record_result("firecrawl", False, error="network error")

    state = service_monitor.get_state("tavily")
    assert state["status"] == service_monitor.DOWN
    assert state["fallback"] == ""
    assert service_monitor.format_row("tavily").endswith("· резерв недоступен")


def test_background_rechecks_fallback_before_selecting_it(monkeypatch):
    _memory_store(monkeypatch)
    calls = []

    def fake_probe(service):
        calls.append(service)
        service_monitor.record_result(service, service == "firecrawl")
        return service == "firecrawl"

    monkeypatch.setattr(service_monitor, "probe", fake_probe)
    monkeypatch.setattr(
        service_monitor, "SPECS",
        (service_monitor.SPEC_BY_KEY["tavily"], service_monitor.SPEC_BY_KEY["firecrawl"]),
    )

    service_monitor.check_all(force=True)

    assert calls.count("firecrawl") == 1
    assert service_monitor.get_state("tavily")["fallback"] == "firecrawl"
