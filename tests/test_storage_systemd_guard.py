import pytest

import storage_driver


def test_systemd_start_without_database_path_refuses_in_memory_storage(monkeypatch):
    monkeypatch.setattr(storage_driver.config, "DATABASE_PATH", "")
    monkeypatch.setattr(storage_driver.config, "DATABASE_URL", "")
    monkeypatch.setenv("INVOCATION_ID", "abc")  # так systemd запускает сервис на VM
    with pytest.raises(storage_driver.StorageUnavailableError):
        storage_driver._persistent()
    monkeypatch.delenv("INVOCATION_ID")
    assert storage_driver._persistent() is False  # локально без базы — память, как раньше
