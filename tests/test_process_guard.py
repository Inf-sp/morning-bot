import process_guard


def test_polling_lease_allows_only_one_process(tmp_path):
    path = str(tmp_path / "polling.lock")
    first, second = process_guard.PollingLease(path), process_guard.PollingLease(path)
    try:
        assert first.acquire(wait_seconds=0)
        assert not second.acquire(wait_seconds=0)
        first.release()
        assert second.acquire(wait_seconds=0)
    finally:
        first.release()
        second.release()
