import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini")
os.environ.setdefault("WEATHER_API_KEY", "test-weather")

import config
import weather
import requests
import storage_driver

NOW_TS = int(weather.datetime.now(weather.TZ).replace(hour=8, minute=0, second=0, microsecond=0).timestamp())


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text
        self.reason = "error"

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._json


CURRENT_OK = {
    "data": [{
        "dt": NOW_TS,
        "temp": 8.4,
        "feels_like": 6.8,
        "weather": [{"id": 500}],
        "alerts": [],
    }]
}
HOURLY_OK = {
    "data": [{
        "dt": NOW_TS,
        "temp": 8.4,
        "humidity": 81,
        "pop": 0.7,
        "rain": {"1h": 0.4},
        "wind_speed": 6.1,
        "wind_gust": 9.2,
        "uvi": 1.1,
        "weather": [{"id": 500}],
    }]
}
DAILY_OK = {
    "data": [{
        "dt": NOW_TS,
        "temp": {"max": 9.1, "min": 4.2},
        "pop": 0.6,
        "rain": 1.2,
        "wind_speed": 7.0,
        "wind_gust": 11.0,
        "wind_deg": 240,
        "uvi": 1.4,
        "weather": [{"id": 500}],
    }]
}


def _install_fake_get(responder):
    def _fake_get(url, params=None, timeout=None):
        assert url.startswith("https://api.openweathermap.org/data/4.0/onecall")
        assert params["appid"] == config.WEATHER_API_KEY
        return responder(url, params)
    weather.requests.get = _fake_get


def _reset_cache():
    weather._WX_CACHE.clear()
    storage_driver._memory.pop(config.WEATHER_CACHE_KEY, None)


# 1) успешный ответ One Call 4.0: current + hourly + daily
def test_success():
    _reset_cache()

    def responder(url, params):
        if url.endswith("/current"):
            return _Resp(200, CURRENT_OK)
        if url.endswith("/timeline/1h"):
            return _Resp(200, HOURLY_OK)
        if url.endswith("/timeline/1day"):
            return _Resp(200, DAILY_OK)
        raise AssertionError(f"unexpected url {url}")

    _install_fake_get(responder)
    data = weather.fetch_weather(52.37, 4.89, 2)
    assert data["provider"] == "openweathermap"
    assert data["current"]["temperature_2m"] == 8.4
    assert data["hourly"]["precipitation_probability"] == [70]
    assert data["daily"]["precipitation_sum"] == [1.2]
    assert data["alert_ids"] == []
    print("ok: success response parsed")


# 5) отсутствует current
def test_missing_current():
    _reset_cache()

    def responder(url, params):
        if url.endswith("/current"):
            return _Resp(200, {"data": []})
        if url.endswith("/timeline/1h"):
            return _Resp(200, HOURLY_OK)
        if url.endswith("/timeline/1day"):
            return _Resp(200, DAILY_OK)
        raise AssertionError(url)

    _install_fake_get(responder)
    data = weather.fetch_weather(52.37, 4.89, 2)
    assert data["current"]["temperature_2m"] is None
    assert data["current"]["weathercode"] == 1
    print("ok: missing current falls back safely")


# 6) отсутствует daily
def test_missing_daily():
    _reset_cache()

    def responder(url, params):
        if url.endswith("/current"):
            return _Resp(200, CURRENT_OK)
        if url.endswith("/timeline/1h"):
            return _Resp(200, HOURLY_OK)
        if url.endswith("/timeline/1day"):
            return _Resp(200, {"data": []})
        raise AssertionError(url)

    _install_fake_get(responder)
    data = weather.fetch_weather(52.37, 4.89, 2)
    assert data["daily"]["time"] == []
    assert data["daily"]["temperature_2m_max"] == []
    print("ok: missing daily falls back safely")


# 7) rain/snow отсутствуют
def test_missing_precipitation():
    _reset_cache()
    hourly_no_precip = {
        "data": [{
            "dt": NOW_TS, "temp": 8.4, "humidity": 81, "pop": 0.1,
            "wind_speed": 6.1, "uvi": 1.1, "weather": [{"id": 800}],
        }]
    }
    daily_no_precip = {
        "data": [{
            "dt": NOW_TS, "temp": {"max": 9.1, "min": 4.2}, "pop": 0.1,
            "wind_speed": 7.0, "wind_deg": 240, "uvi": 1.4, "weather": [{"id": 800}],
        }]
    }

    def responder(url, params):
        if url.endswith("/current"):
            return _Resp(200, CURRENT_OK)
        if url.endswith("/timeline/1h"):
            return _Resp(200, hourly_no_precip)
        if url.endswith("/timeline/1day"):
            return _Resp(200, daily_no_precip)
        raise AssertionError(url)

    _install_fake_get(responder)
    data = weather.fetch_weather(52.37, 4.89, 2)
    assert data["hourly"]["precipitation"] == [0.0]
    assert data["daily"]["precipitation_sum"] == [0.0]
    print("ok: missing rain/snow default to zero")


# 8) alerts отсутствуют
def test_no_alerts():
    _reset_cache()

    def responder(url, params):
        if url.endswith("/current"):
            return _Resp(200, CURRENT_OK)
        if url.endswith("/timeline/1h"):
            return _Resp(200, HOURLY_OK)
        if url.endswith("/timeline/1day"):
            return _Resp(200, DAILY_OK)
        raise AssertionError(url)

    _install_fake_get(responder)
    data = weather.fetch_weather(52.37, 4.89, 2)
    assert data["alert_ids"] == []
    assert data["alerts"] == []
    print("ok: no alerts leaves empty alert list")


def test_no_stale_references():
    for path in ("weather.py", "research.py", "docs/weather.md"):
        text = open(ROOT / path, encoding="utf-8").read()
        assert "open-meteo" not in text.lower(), path
        assert "data/3.0/onecall" not in text, path
    print("ok: no stale open-meteo / One Call 3.0 references")


if __name__ == "__main__":
    test_success()
    test_missing_current()
    test_missing_daily()
    test_missing_precipitation()
    test_no_alerts()
    test_no_stale_references()
    print("ok: weather uses OpenWeatherMap One Call API 4.0 via WEATHER_API_KEY")
