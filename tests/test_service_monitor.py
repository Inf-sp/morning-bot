import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import provider_runtime
import service_monitor


def _memory_store(monkeypatch):
    memory = {}

    def load(key):
        return memory.get(key, {})

    def mutate(key, callback):
        data, result = callback(memory.get(key, {}))
        memory[key] = data
        return result

    monkeypatch.setattr(provider_runtime.store, "_load", load)
    monkeypatch.setattr(provider_runtime.store, "mutate_kv", mutate)
    return memory


def test_every_service_exposes_the_same_state_shape(monkeypatch):
    _memory_store(monkeypatch)
    required = {
        "status", "quota_remaining", "quota_total", "fallback",
        "last_check", "last_success", "last_error",
    }

    assert provider_runtime.states()
    assert all(required <= set(state) for state in provider_runtime.states())


def test_category_is_everywhere_for_two_or_more_sections():
    assert provider_runtime.SPEC_BY_KEY["gemini"].category == "Везде"
    assert provider_runtime.SPEC_BY_KEY["spoonacular"].category == "Готовка"


def test_quota_rows_show_remaining_not_usage(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result(
        "openweather", True, quota_remaining=998, quota_total=1000,
    )

    assert service_monitor.format_row("openweather") == (
        "🟢 OpenWeather · Везде · 998 из 1 000"
    )


def test_one_remaining_request_uses_singular(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("spoonacular", True, quota_remaining=1, quota_total=150)

    assert service_monitor.format_row("spoonacular") == (
        "🟡 Spoonacular · Готовка · 1 из 150"
    )


def test_exhausted_quota_is_yellow(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("gemini", True, quota_remaining=0, quota_total=20)

    assert service_monitor.format_row("gemini") == (
        "🟡 Gemini · Везде · лимит исчерпан"
    )


def test_fallback_is_hidden_until_target_really_succeeds(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("tavily", False, status_code=429)

    assert provider_runtime.activate_fallback("tavily", "firecrawl") is False
    assert "Firecrawl" not in service_monitor.format_row("tavily")

    provider_runtime.record_result("firecrawl", True)
    assert provider_runtime.activate_fallback("tavily", "firecrawl") is True
    assert service_monitor.format_row("tavily").endswith("· Firecrawl")


def test_fallback_graph_has_no_cycles():
    assert provider_runtime.validate_fallback_graph() == []
    assert "tavily" not in provider_runtime.SPEC_BY_KEY["firecrawl"].fallbacks


def test_success_clears_error_and_disables_fallback(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("tavily", False, error="timeout")
    provider_runtime.record_result("firecrawl", True)
    provider_runtime.activate_fallback("tavily", "firecrawl")

    provider_runtime.record_result("tavily", True)
    state = provider_runtime.get_state("tavily")

    assert state["status"] == provider_runtime.OK
    assert state["last_error"] == ""
    assert state["fallback"] == ""
    assert any("резерв отключён" in event["text"] for event in provider_runtime.history())


def test_failed_active_fallback_is_removed_immediately(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("tavily", False, error="timeout")
    provider_runtime.record_result("firecrawl", True)
    provider_runtime.activate_fallback("tavily", "firecrawl")

    provider_runtime.record_result("firecrawl", False, error="network error")

    state = provider_runtime.get_state("tavily")
    assert state["status"] == provider_runtime.DOWN
    assert state["fallback"] == ""
    assert service_monitor.format_row("tavily").endswith("· резерв недоступен")


def test_background_rechecks_fallback_before_selecting_it(monkeypatch):
    _memory_store(monkeypatch)
    calls = []

    def fake_probe(service):
        calls.append(service)
        provider_runtime.record_result(service, service == "firecrawl")
        return service == "firecrawl"

    monkeypatch.setattr(service_monitor, "probe", fake_probe)
    monkeypatch.setattr(
        service_monitor, "SPECS",
        (provider_runtime.SPEC_BY_KEY["tavily"], provider_runtime.SPEC_BY_KEY["firecrawl"]),
    )

    service_monitor.check_all(force=True)

    assert calls.count("firecrawl") == 1
    assert provider_runtime.get_state("tavily")["fallback"] == "firecrawl"
