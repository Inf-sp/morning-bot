import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import weather_provider


def test_month_forecast_loads_three_daily_pages_once_and_uses_cache(monkeypatch):
    base = 1_780_000_000
    calls = []

    def record(index):
        return {"dt": base + index * 86_400, "temp": {"min": 10, "max": 20}}

    def get_page(_path, _lat, _lon, timeout=20, extra_params=None):
        del timeout
        calls.append(extra_params)
        start = (extra_params or {}).get("start")
        offset = 0 if start is None else (int(start) - base) // 86_400
        return {"data": [record(index) for index in range(offset, offset + 10)]}

    monkeypatch.setattr(weather_provider, "_onecall_get", get_page)
    monkeypatch.setattr(weather_provider, "_persistent_cache_load", lambda _key: None)
    monkeypatch.setattr(weather_provider, "_persistent_cache_save", lambda _key, _data: None)
    monkeypatch.setattr(weather_provider.config, "WEATHER_API_KEY", "test-weather-key")
    weather_provider._MONTH_CACHE.clear()

    first = weather_provider.fetch_month_weather(51.5, 4.2)
    second = weather_provider.fetch_month_weather(51.5, 4.2)

    assert len(first["days"]) == 30
    assert second == first
    assert len(calls) == 3
