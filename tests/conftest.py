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
def _no_concert_notes_ai(monkeypatch):
    """Фразы об артистах концертов в тестах не запрашиваются у AI; нужный тест вернёт настоящую."""
    import leisure_concerts

    async def no_notes(_names):
        return {}

    monkeypatch.setattr(leisure_concerts, "artist_notes", no_notes)
    yield


@pytest.fixture(autouse=True)
def _no_country_network(monkeypatch):
    """Таблица стран, MusicBrainz и Wikidata в тестах не запрашиваются."""
    import countries

    monkeypatch.setattr(countries, "_table", lambda: {})
    monkeypatch.setattr(countries, "artist_label", lambda _name, fetch=True: "")
    monkeypatch.setattr(countries, "artist_years", lambda _name, fetch=True: "")
    monkeypatch.setattr(countries, "artist_latest_album", lambda _name, fetch=True: {})
    monkeypatch.setattr(countries, "artist_fans", lambda _name, fetch=True: None)
    monkeypatch.setattr(countries, "title_info", lambda *_a, **_k: {})
    yield


@pytest.fixture(autouse=True)
def _no_deezer(monkeypatch):
    """Резервные чарты Deezer в тестах не запрашиваются."""
    import apple_music

    monkeypatch.setattr(apple_music, "_deezer_releases", lambda *_a: [])
    yield


@pytest.fixture(autouse=True)
def _no_open_meteo(monkeypatch):
    """Резервная погода Open-Meteo в тестах не запрашивается — как будто тоже недоступна."""
    import weather_provider

    monkeypatch.setattr(weather_provider, "_fetch_open_meteo",
                        lambda *_a: (_ for _ in ()).throw(ConnectionError("no network in tests")))
    yield


@pytest.fixture(autouse=True)
def _no_local_events_search(monkeypatch):
    """Веб-поиск местных событий в тестах не выполняется."""
    import local_events

    monkeypatch.setattr(local_events, "_search", lambda *_a: [])
    yield


@pytest.fixture(autouse=True)
def _no_wikidata_network(monkeypatch):
    """Wikidata в тестах не вызывается по сети; нужный тест подменяет lookup сам."""
    import wikidata_books

    monkeypatch.setattr(wikidata_books, "_get", lambda _params: (_ for _ in ()).throw(
        wikidata_books._Unavailable()))
    yield
