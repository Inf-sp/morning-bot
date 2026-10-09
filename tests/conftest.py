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


@pytest.fixture(autouse=True)
def _no_wikidata_network(monkeypatch):
    """Wikidata в тестах не вызывается по сети; нужный тест подменяет lookup сам."""
    import wikidata_books

    monkeypatch.setattr(wikidata_books, "_get", lambda _params: (_ for _ in ()).throw(
        wikidata_books._Unavailable()))
    yield
