import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini")
os.environ.setdefault("WEATHER_API_KEY", "test-weather")

import admin
import config
import weather
import requests


class _Resp:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "current": {
                "dt": 1_704_067_200,
                "temp": 8.4,
                "feels_like": 6.8,
                "weather": [{"id": 500}],
            },
            "hourly": [
                {
                    "dt": 1_704_067_200,
                    "temp": 8.4,
                    "humidity": 81,
                    "pop": 0.7,
                    "rain": {"1h": 0.4},
                    "wind_speed": 6.1,
                    "wind_gust": 9.2,
                    "uvi": 1.1,
                    "weather": [{"id": 500}],
                }
            ],
            "daily": [
                {
                    "dt": 1_704_067_200,
                    "temp": {"max": 9.1, "min": 4.2},
                    "pop": 0.6,
                    "rain": 1.2,
                    "wind_speed": 7.0,
                    "wind_gust": 11.0,
                    "wind_deg": 240,
                    "uvi": 1.4,
                    "weather": [{"id": 500}],
                }
            ],
        }


def _fake_get(url, params=None, timeout=None):
    assert url == "https://api.openweathermap.org/data/3.0/onecall"
    assert params["appid"] == config.WEATHER_API_KEY
    assert params["units"] == "metric"
    return _Resp()


def _fake_request(method, url, timeout=None, **kwargs):
    if url == "https://api.openweathermap.org/data/3.0/onecall":
        assert kwargs["params"]["appid"] == config.WEATHER_API_KEY
    return _Resp()


weather.requests.get = _fake_get
requests.request = _fake_request
weather._WX_CACHE.clear()
data = weather.fetch_weather(52.37, 4.89, 2)

assert data["provider"] == "openweathermap"
assert data["current"]["temperature_2m"] == 8.4
assert data["hourly"]["precipitation_probability"] == [70]
assert data["daily"]["precipitation_sum"] == [1.2]

labels = [row[0] for row in admin._external_api_probe_results()]
assert "Weather" in labels

for path in ("weather.py", "research.py", "docs/weather.md"):
    text = open(path, encoding="utf-8").read()
    assert "open-meteo" not in text.lower(), path

print("ok: weather uses OpenWeatherMap via WEATHER_API_KEY")
