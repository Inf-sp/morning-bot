import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import api_usage
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


def test_catalog_is_shared_by_usage_and_monitor():
    assert service_monitor.SPEC_BY_KEY is provider_runtime.SPEC_BY_KEY
    assert api_usage.SERVICE_LABELS["gemini"] == provider_runtime.LABELS["gemini"]
    assert provider_runtime.validate_fallback_graph() == []


def test_usage_result_updates_authoritative_health_state(monkeypatch):
    _memory_store(monkeypatch)

    api_usage.record_request("gemini", ok=True, latency_ms=120)

    state = provider_runtime.get_state("gemini")
    assert state["status"] == provider_runtime.OK
    assert state["last_success"]
    assert api_usage.service_usage("gemini")["requests_today"] == 1
