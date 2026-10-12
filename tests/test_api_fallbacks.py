"""Резервные источники: исчерпан лимит или упал основной API — бот берёт другой, а не показывает ошибку."""
import weather_provider


def test_weather_falls_back_to_open_meteo_and_caches_it(monkeypatch):
    monkeypatch.setattr(weather_provider.config, "WEATHER_API_KEY", "")
    monkeypatch.setattr(weather_provider, "_weather_cache_get", lambda *_a, **_k: None)
    saved = []
    monkeypatch.setattr(weather_provider, "_persistent_cache_save", lambda key, data: saved.append(key))
    forecast = {"provider": "open-meteo", "daily": {"time": ["2026-10-12"]}}
    monkeypatch.setattr(weather_provider, "_fetch_open_meteo", lambda *_a: forecast)

    assert weather_provider.fetch_weather(52.63, 4.75, 9) == forecast
    assert saved == [weather_provider._weather_cache_key(52.63, 4.75)]


def test_holidays_use_saved_list_when_nager_is_down(monkeypatch):
    import public_holidays
    monkeypatch.setattr(public_holidays.util, "ttl_get", lambda *_a: None)
    monkeypatch.setattr(public_holidays.requests, "get", lambda *_a, **_k: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(public_holidays.store, "_load", lambda _key: {"NL|2026": [{"date": "2026-04-27"}]})
    assert public_holidays._year_holidays(2026, "NL") == [{"date": "2026-04-27"}]


def test_currency_rate_falls_back_to_open_er_api(monkeypatch):
    import vacation

    class Response:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    def get(url, **_kwargs):
        if "frankfurter" in url:
            raise OSError("down")
        return Response({"rates": {"ZAR": 18.5}})

    monkeypatch.setattr(vacation.requests, "get", get)
    monkeypatch.setattr(vacation, "_cached", lambda _key, _cc, loader, ttl=0: loader())
    assert vacation._eur_rate("ZAR") == 18.5


def test_artist_country_falls_back_to_wikidata(monkeypatch):
    import countries
    import wikidata_books
    monkeypatch.setattr(countries, "_musicbrainz_country", lambda _n: (_ for _ in ()).throw(OSError("503")))
    monkeypatch.setattr(wikidata_books, "artist_country", lambda name: "GB" if name == "Radiohead" else "")
    assert countries._artist_country("Radiohead") == "GB"


def test_search_tries_serpapi_last(monkeypatch):
    import research
    monkeypatch.setattr(research, "firecrawl_search", lambda *_a, **_k: [])
    monkeypatch.setattr(research, "serpapi_search", lambda *_a, **_k: [{"url": "https://x.example", "content": "hit"}])
    assert research.web_search("Amsterdam festivals", scenario="local_events")[0]["content"] == "hit"


def test_after_openweather_failure_it_is_paused_and_open_meteo_answers_at_once(monkeypatch):
    import time
    monkeypatch.setattr(weather_provider.config, "WEATHER_API_KEY", "key")
    monkeypatch.setattr(weather_provider, "_weather_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(weather_provider, "_persistent_cache_save", lambda *_a: None)
    monkeypatch.setattr(weather_provider, "_owm_down_until", [0.0])
    calls = []

    def owm(*_a, **_k):
        calls.append("owm")
        raise TimeoutError("hangs")

    monkeypatch.setattr(weather_provider, "_onecall_get", owm)
    monkeypatch.setattr(weather_provider, "_fetch_open_meteo", lambda *_a: {"provider": "open-meteo"})

    assert weather_provider.fetch_weather(52.6, 4.7)["provider"] == "open-meteo"
    first = len(calls)
    assert weather_provider.fetch_weather(52.6, 4.7)["provider"] == "open-meteo"
    assert len(calls) == first and weather_provider._owm_down_until[0] > time.time()  # OpenWeather не ждём
