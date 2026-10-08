from datetime import timedelta

import api_usage
import config
import storage_driver
import store


def test_get_profile_does_not_cache_value_read_during_mutation(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(storage_driver, "_memory", {config.PROFILE_KEY: {"42": {"v": 1}}})
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    monkeypatch.setattr(store, "_profile_cache", {})
    real_load = store._load

    def racing_load(key):
        value = real_load(key)  # старое значение уже прочитано
        store.mutate_profile("42", lambda prof: ({**prof, "v": 2}, None))
        return value

    monkeypatch.setattr(store, "_load", racing_load)
    assert store.get_profile("42") == {"v": 1}
    monkeypatch.setattr(store, "_load", real_load)

    assert store.get_profile("42") == {"v": 2}


def test_purge_user_keeps_other_users(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(storage_driver, "_memory", {
        config.PROFILE_KEY: {"1": {"a": 1}, "2": {"b": 2}},
    })
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    monkeypatch.setattr(store, "_profile_cache", {})

    store.purge_user("1")

    assert storage_driver._memory[config.PROFILE_KEY] == {"2": {"b": 2}}


def test_usage_prune_drops_stale_minute_buckets():
    now = api_usage._now()
    old = now - timedelta(days=3)
    svc = {"counts": {
        f"minute:requests:{api_usage._bucket('minute', old)}": 5,
        f"minute:requests:{api_usage._bucket('minute', now)}": 1,
        f"day:requests:{api_usage._bucket('day', old)}": 7,
    }}

    api_usage._prune(svc, int(now.timestamp()))

    assert svc["counts"] == {
        f"minute:requests:{api_usage._bucket('minute', now)}": 1,
        f"day:requests:{api_usage._bucket('day', old)}": 7,
    }


def test_legacy_flat_wardrobe_is_migrated_without_crashing():
    """Старый плоский шкаф {категория: [вещи]} мигрирует в zones без AttributeError."""
    import store

    migrated = store._migrate_legacy_wardrobe({"_v": 3, "футболки": ["белая", "Белая"], "джинсы": ["синие"]})

    items = [item for zone in migrated["zones"].values() for bucket in zone.values() for item in bucket]
    assert sorted(item["name"] for item in items) == ["белая", "синие"]
    assert all(item["id"] and item["zone"] and item["subcategory"] for item in items)
