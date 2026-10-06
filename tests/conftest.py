import os

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")


@pytest.fixture(autouse=True)
def _reset_ai_breaker():
    """Пауза провайдеров живёт в памяти процесса — не даём ей перетекать между тестами."""
    import provider_runtime

    provider_runtime._ai_breaker.clear()
    yield
    provider_runtime._ai_breaker.clear()
