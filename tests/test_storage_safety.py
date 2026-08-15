import pytest

import config
import service_monitor
import storage_driver
import store


def test_configured_database_never_falls_back_to_memory(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://configured")
    monkeypatch.setattr(storage_driver, "_connection", None)
    monkeypatch.setattr(storage_driver, "_memory", {"profile": {"lost": True}})
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    monkeypatch.setattr(storage_driver, "db", lambda: None)

    with pytest.raises(storage_driver.StorageUnavailableError):
        storage_driver.load("profile")
    with pytest.raises(storage_driver.StorageUnavailableError):
        storage_driver.save("profile", {"new": True})

    assert storage_driver._memory == {"profile": {"lost": True}}


def test_database_probe_uses_real_backend_ping(monkeypatch):
    calls = []

    def fail_ping():
        calls.append(True)
        raise storage_driver.StorageUnavailableError("down")

    monkeypatch.setattr(service_monitor.storage_driver, "ping", fail_ping)
    monkeypatch.setattr(service_monitor.provider_runtime, "record_result", lambda *args, **kwargs: None)

    assert service_monitor.probe("database") is False
    assert calls == [True]


def test_profile_mutations_preserve_unrelated_fields(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(storage_driver, "_memory", {config.PROFILE_KEY: {"42": {"name": "Света"}}})
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    monkeypatch.setattr(store, "_profile_cache", {})

    store.mutate_profile("42", lambda profile: ({**profile, "meal": "lunch"}, None))
    store.mutate_profile("42", lambda profile: ({**profile, "music": "indie"}, None))

    assert store.get_profile("42") == {
        "name": "Света",
        "meal": "lunch",
        "music": "indie",
    }
