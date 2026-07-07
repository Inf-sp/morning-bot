import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini")
os.environ.setdefault("WEATHER_API_KEY", "test-weather")

import requests

import config
import store
import weather


class _Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {"data": [{"dt": 1_704_067_200, "temp": 8.0, "weather": [{"id": 800}]}]}
        self.text = text
        self.reason = "error"

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._json


CURRENT_OK = {"data": [{"dt": 1_704_067_200, "temp": 8.4, "feels_like": 6.8, "weather": [{"id": 500}], "alerts": []}]}
HOURLY_OK = {"data": [{"dt": 1_704_067_200, "temp": 8.4, "humidity": 81, "pop": 0.7, "rain": {"1h": 0.4}, "wind_speed": 6.1, "weather": [{"id": 500}]}]}
DAILY_OK = {"data": [{"dt": 1_704_067_200, "temp": {"max": 9.1, "min": 4.2}, "pop": 0.6, "rain": 1.2, "wind_speed": 7.0, "weather": [{"id": 500}]}]}


def _reset():
    config.DATABASE_URL = ""
    weather._WX_CACHE.clear()
    for key in list(store._mem.keys()):
        if str(key).startswith("weather_usage:"):
            del store._mem[key]


def _usage():
    return weather.get_weather_usage()


def _seed_total(n):
    def _mut(data):
        data["requests_total"] = n
        return data, True
    weather._usage_mutate(_mut)


def test_new_day_counter():
    _reset()
    today = weather.get_weather_usage()
    tomorrow = weather.get_weather_usage(weather.datetime.now(weather.TZ) + weather.timedelta(days=1))
    assert today["date"] != tomorrow["date"]
    assert today["requests_total"] == 0 and tomorrow["requests_total"] == 0
    print("ok: new day creates separate counter")


def test_cache_hit_does_not_increment_total():
    _reset()

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/current"):
            return _Resp(200, CURRENT_OK)
        if url.endswith("/timeline/1h"):
            return _Resp(200, HOURLY_OK)
        if url.endswith("/timeline/1day"):
            return _Resp(200, DAILY_OK)
        raise AssertionError(url)

    weather.requests.get = fake_get
    weather.fetch_weather(52.37, 4.89, 2)
    before = _usage()["requests_total"]
    weather.fetch_weather(52.37, 4.89, 2)
    after = _usage()
    assert after["requests_total"] == before
    assert after["cache_hits"] == 1
    print("ok: cache hit does not increment total")


def test_success_increments_total_and_success():
    _reset()
    weather.requests.get = lambda url, params=None, timeout=None: _Resp(200)
    weather._onecall_get("current", 52.37, 4.89)
    u = _usage()
    assert u["requests_total"] == 1
    assert u["requests_success"] == 1
    print("ok: success increments total and success")


def test_401_no_retry():
    _reset()
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(401, {}, text="unauthorized")

    weather.requests.get = fake_get
    try:
        weather._onecall_get("current", 52.37, 4.89)
    except requests.HTTPError:
        pass
    u = _usage()
    assert calls["n"] == 1
    assert u["requests_total"] == 1
    assert u["requests_failed"] == 1
    assert u["requests_retry"] == 0
    print("ok: 401 increments failed without retry")


def test_timeout_one_retry_then_success():
    _reset()
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.Timeout("timeout")
        return _Resp(200)

    weather.requests.get = fake_get
    weather._onecall_get("current", 52.37, 4.89)
    u = _usage()
    assert calls["n"] == 2
    assert u["requests_total"] == 2
    assert u["requests_retry"] == 1
    assert u["requests_failed"] == 1
    assert u["requests_success"] == 1
    print("ok: timeout retries once and records retry")


def test_429_no_infinite_retry():
    _reset()
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(429, {}, text="Too many requests")

    weather.requests.get = fake_get
    try:
        weather._onecall_get("current", 52.37, 4.89)
    except requests.HTTPError:
        pass
    u = _usage()
    assert calls["n"] == 2
    assert u["requests_total"] == 2
    assert u["requests_retry"] == 1
    assert u["requests_failed"] == 2
    print("ok: 429 retries at most once")


def test_999_last_call_allowed():
    _reset()
    _seed_total(999)
    weather.requests.get = lambda url, params=None, timeout=None: _Resp(200)
    weather._onecall_get("current", 52.37, 4.89)
    u = _usage()
    assert u["requests_total"] == 1000
    assert u["requests_success"] == 1
    print("ok: request at 999 is allowed")


def test_1000_blocks_new_http():
    _reset()
    _seed_total(1000)
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return _Resp(200)

    weather.requests.get = fake_get
    try:
        weather._onecall_get("current", 52.37, 4.89)
    except weather.WeatherDailyLimitExceeded:
        pass
    assert calls["n"] == 0
    assert _usage()["requests_total"] == 1000
    print("ok: request at 1000 is blocked before HTTP")


def test_cache_available_when_blocked():
    _reset()
    weather._WX_CACHE[(52.37, 4.89, 2)] = (__import__("time").time(), {"provider": "cached"})
    _seed_total(1000)
    data = weather.fetch_weather(52.37, 4.89, 2)
    assert data["provider"] == "cached"
    assert _usage()["cache_hits"] == 1
    print("ok: cache remains available when blocked")


def test_amsterdam_date_key():
    _reset()
    late = weather.datetime(2026, 7, 7, 23, 59, tzinfo=weather.TZ)
    next_day = weather.datetime(2026, 7, 8, 0, 0, tzinfo=weather.TZ)
    assert weather._usage_key(late) == "weather_usage:2026-07-07"
    assert weather._usage_key(next_day) == "weather_usage:2026-07-08"
    print("ok: Europe/Amsterdam date boundary is used")


if __name__ == "__main__":
    test_new_day_counter()
    test_cache_hit_does_not_increment_total()
    test_success_increments_total_and_success()
    test_401_no_retry()
    test_timeout_one_retry_then_success()
    test_429_no_infinite_retry()
    test_999_last_call_allowed()
    test_1000_blocks_new_http()
    test_cache_available_when_blocked()
    test_amsterdam_date_key()
    print("ok: weather usage accounting")
