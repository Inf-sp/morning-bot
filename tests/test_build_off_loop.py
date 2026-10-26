import os
import threading
import time

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import util


def test_build_off_loop_from_worker_thread_runs_instead_of_failing():
    """Экран Досуга в отпуске собирается в asyncio.to_thread: там нет цикла событий. Раньше build_off_loop падал
    RuntimeError, и экран открывался только со второго нажатия."""
    done = threading.Event()
    errors = []

    def worker():
        try:
            util.build_off_loop(("test_off_loop", time.time()), done.set)
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert errors == []
    assert done.wait(2)
