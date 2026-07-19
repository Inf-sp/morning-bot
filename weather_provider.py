"""OpenWeather provider, quota accounting and persistent forecast cache."""

import logging
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
import time
import requests

import api_usage
import config
import store

_log = logging.getLogger(__name__)
TZ = config.TZ

_WX_CACHE = {}          # (lat2, lon2, days) -> (ts, json)
_WX_TTL = 3 * 3600      # сек: обновляем прогноз раз в 3 часа вместо 12
_WX_STALE_TTL = 24 * 3600
_CURRENT_CACHE = {}     # (lat2, lon2) -> (ts, current conditions)
_CURRENT_TTL = 10 * 60


def _owm_weathercode(weather_id):
    try:
        wid = int(weather_id)
    except (TypeError, ValueError):
        return 1
    if 200 <= wid < 300:
        return 95
    if 300 <= wid < 400:
        return 51
    if 500 <= wid < 600:
        if wid in (502, 503, 504, 522, 531):
            return 65
        if wid in (520, 521):
            return 80
        return 61
    if 600 <= wid < 700:
        if wid in (602, 622):
            return 75
        if wid in (611, 612, 613, 615, 616):
            return 77
        return 71
    if 700 <= wid < 800:
        return 45
    if wid == 800:
        return 0
    if wid == 801:
        return 1
    if wid == 802:
        return 2
    if wid in (803, 804):
        return 3
    return 1


def _owm_iso(ts):
    return datetime.fromtimestamp(int(ts), TZ).strftime("%Y-%m-%dT%H:%M")


def _owm_precip_mm(item):
    rain = item.get("rain")
    snow = item.get("snow")
    total = 0.0
    if isinstance(rain, dict):
        total += float(rain.get("1h") or rain.get("3h") or 0)
    elif isinstance(rain, (int, float)):
        total += float(rain)
    if isinstance(snow, dict):
        total += float(snow.get("1h") or snow.get("3h") or 0)
    elif isinstance(snow, (int, float)):
        total += float(snow)
    return total


def _first_data_item(payload):
    """One Call 4.0 оборачивает точку в {"data": [{...}]} (current) или {"data": [...]} (timeline).
    Безопасно достаём список записей независимо от формы ответа."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("data")
    if isinstance(items, list):
        return items
    return []


def _adapt_openweather(current_payload, hourly_payload, daily_payload, alerts=None):
    current_items = _first_data_item(current_payload)
    current = current_items[0] if current_items else {}
    hourly = _first_data_item(hourly_payload)
    daily = _first_data_item(daily_payload)

    hourly_out = {
        "time": [],
        "precipitation_probability": [],
        "precipitation": [],
        "windspeed_10m": [],
        "windgusts_10m": [],
        "temperature_2m": [],
        "relativehumidity_2m": [],
        "uv_index": [],
    }
    for h in hourly:
        hourly_out["time"].append(_owm_iso(h.get("dt", 0)))
        hourly_out["precipitation_probability"].append(round(float(h.get("pop") or 0) * 100))
        hourly_out["precipitation"].append(round(_owm_precip_mm(h), 2))
        hourly_out["windspeed_10m"].append(h.get("wind_speed") or 0)
        hourly_out["windgusts_10m"].append(h.get("wind_gust") or h.get("wind_speed") or 0)
        hourly_out["temperature_2m"].append(h.get("temp"))
        hourly_out["relativehumidity_2m"].append(h.get("humidity"))
        hourly_out["uv_index"].append(h.get("uvi"))

    daily_out = {
        "time": [],
        "temperature_2m_max": [],
        "temperature_2m_min": [],
        "precipitation_probability_max": [],
        "precipitation_sum": [],
        "weathercode": [],
        "windspeed_10m_max": [],
        "windgusts_10m_max": [],
        "winddirection_10m_dominant": [],
        "uv_index_max": [],
    }
    for d in daily:
        temp = d.get("temp") or {}
        weather_id = ((d.get("weather") or [{}])[0] or {}).get("id")
        daily_out["time"].append(datetime.fromtimestamp(int(d.get("dt", 0)), TZ).strftime("%Y-%m-%d"))
        daily_out["temperature_2m_max"].append(temp.get("max"))
        daily_out["temperature_2m_min"].append(temp.get("min"))
        daily_out["precipitation_probability_max"].append(round(float(d.get("pop") or 0) * 100))
        daily_out["precipitation_sum"].append(round(float(d.get("rain") or 0) + float(d.get("snow") or 0), 2))
        daily_out["weathercode"].append(_owm_weathercode(weather_id))
        daily_out["windspeed_10m_max"].append(d.get("wind_speed") or 0)
        daily_out["windgusts_10m_max"].append(d.get("wind_gust") or d.get("wind_speed") or 0)
        daily_out["winddirection_10m_dominant"].append(d.get("wind_deg"))
        daily_out["uv_index_max"].append(d.get("uvi"))

    current_weather_id = ((current.get("weather") or [{}])[0] or {}).get("id")
    alert_ids = [a.get("id") for a in (current.get("alerts") or []) if isinstance(a, dict) and a.get("id")]
    return {
        "current": {
            "temperature_2m": current.get("temp"),
            "apparent_temperature": current.get("feels_like"),
            "weathercode": _owm_weathercode(current_weather_id),
        },
        "hourly": hourly_out,
        "daily": daily_out,
        "alert_ids": alert_ids,
        "alerts": alerts or [],
        "provider": "openweathermap",
    }


_ONECALL_BASE = "https://api.openweathermap.org/data/4.0/onecall"
WEATHER_LIMIT_FALLBACK = "Погодный лимит на сегодня исчерпан. Попробую снова после полуночи."


class WeatherDailyLimitExceeded(Exception):
    pass


def _usage_key(dt=None):
    return f"weather_usage:{(dt or datetime.now(TZ)).strftime('%Y-%m-%d')}"


def _usage_template(date_str=None):
    return {
        "date": date_str or datetime.now(TZ).strftime("%Y-%m-%d"),
        "requests_total": 0,
        "requests_success": 0,
        "requests_failed": 0,
        "requests_retry": 0,
        "cache_hits": 0,
        "last_request_at": None,
        "last_error_at": None,
        "last_error_reason": "",
    }


def _safe_error_reason(exc=None, response=None):
    if response is not None:
        return f"HTTP {getattr(response, 'status_code', '?')}"
    text = str(exc or "")
    low = text.lower()
    if "timeout" in low:
        return "timeout"
    if text:
        return text[:80]
    return "request failed"


def _weather_cache_key(lat, lon, days):
    return f"{round(float(lat), 2):.2f}:{round(float(lon), 2):.2f}:{int(days)}"


def _cache_date_ok(data, now=None):
    """Не используем вчерашний кэш как сегодняшний: myday читает daily[0]."""
    if not isinstance(data, dict):
        return False
    today = (now or datetime.now(TZ)).strftime("%Y-%m-%d")
    daily = data.get("daily") or {}
    dates = daily.get("time") or []
    return bool(dates) and dates[0] == today


def _persistent_cache_load(cache_key):
    cache = store._load(config.WEATHER_CACHE_KEY)
    if not isinstance(cache, dict):
        return None
    entry = cache.get(cache_key)
    if not isinstance(entry, dict):
        return None
    data = entry.get("data")
    try:
        ts = float(entry.get("ts") or 0)
    except (TypeError, ValueError):
        ts = 0
    if not ts or not isinstance(data, dict):
        return None
    return ts, data


def _persistent_cache_save(cache_key, data):
    cache = store._load(config.WEATHER_CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
    now = time.time()
    # Держим только свежую историю, чтобы KV не разрастался без пользы.
    clean = {}
    for k, v in cache.items():
        if not isinstance(v, dict):
            continue
        try:
            ts = float(v.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if now - ts <= (_WX_STALE_TTL * 2):
            clean[k] = v
    cache = clean
    cache[cache_key] = {"ts": now, "data": deepcopy(data)}
    store._save(config.WEATHER_CACHE_KEY, cache)


def _weather_cache_get(mem_key, cache_key, *, max_age):
    now = time.time()
    hit = _WX_CACHE.get(mem_key)
    if hit and (now - hit[0]) <= max_age and _cache_date_ok(hit[1]):
        _mark_weather_cache_hit()
        return deepcopy(hit[1])

    hit = _persistent_cache_load(cache_key)
    if hit:
        ts, data = hit
        if (now - ts) <= max_age and _cache_date_ok(data):
            _WX_CACHE[mem_key] = (ts, deepcopy(data))
            _mark_weather_cache_hit()
            return deepcopy(data)
    return None


def _usage_mutate(mutator, dt=None):
    key = _usage_key(dt)
    date_str = key.split(":", 1)[1]

    def _mut(current):
        data = _usage_template(date_str)
        data.update(current or {})
        return mutator(data)

    return store.mutate_kv(key, _mut)


def get_weather_usage(dt=None):
    key = _usage_key(dt)
    data = store._load(key)
    out = _usage_template(key.split(":", 1)[1])
    if isinstance(data, dict):
        out.update(data)
    return out


def _reserve_weather_request(is_retry=False):
    now = datetime.now(TZ)

    def _mut(data):
        if int(data.get("requests_total") or 0) >= config.WEATHER_HARD_DAILY_LIMIT:
            return data, False
        data["requests_total"] = int(data.get("requests_total") or 0) + 1
        if is_retry:
            data["requests_retry"] = int(data.get("requests_retry") or 0) + 1
        data["last_request_at"] = now.isoformat()
        return data, True

    ok = _usage_mutate(_mut, now)
    if not ok:
        raise WeatherDailyLimitExceeded(WEATHER_LIMIT_FALLBACK)


def _mark_weather_success():
    def _mut(data):
        data["requests_success"] = int(data.get("requests_success") or 0) + 1
        return data, True
    _usage_mutate(_mut)
    api_usage.record_request("openweather", ok=True)


def _mark_weather_failed(reason):
    now = datetime.now(TZ)

    def _mut(data):
        data["requests_failed"] = int(data.get("requests_failed") or 0) + 1
        data["last_error_at"] = now.isoformat()
        data["last_error_reason"] = str(reason or "request failed")[:80]
        return data, True
    _usage_mutate(_mut, now)
    api_usage.record_request("openweather", ok=False, error=reason)


def _mark_weather_cache_hit():
    def _mut(data):
        data["cache_hits"] = int(data.get("cache_hits") or 0) + 1
        return data, True
    _usage_mutate(_mut)
    api_usage.record_cache_hit("openweather")


def _http_get_counted(url, *, params=None, timeout=20, is_retry=False):
    _reserve_weather_request(is_retry=is_retry)
    try:
        r = requests.get(url, params=params, timeout=timeout)
    except Exception as e:
        _mark_weather_failed(_safe_error_reason(e))
        raise
    try:
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        _mark_weather_failed(_safe_error_reason(e, r))
        raise
    _mark_weather_success()
    return payload


def _should_retry(exc):
    if isinstance(exc, WeatherDailyLimitExceeded):
        return False
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status in (401, 403, 404):
        return False
    if status == 429:
        return True
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    return status is not None and 500 <= int(status) < 600


def _retry_allowed_by_limit():
    usage = get_weather_usage()
    return int(usage.get("requests_total") or 0) <= config.WEATHER_HARD_DAILY_LIMIT - 2


def _onecall_get(path, lat, lon, timeout=20, extra_params=None):
    params = {
        "appid": config.WEATHER_API_KEY,
        "units": "metric",
        "lang": "ru",
    }
    if lat is not None:
        params["lat"] = lat
    if lon is not None:
        params["lon"] = lon
    if extra_params:
        params.update(extra_params)
    url = f"{_ONECALL_BASE}/{path}"
    try:
        return _http_get_counted(url, params=params, timeout=timeout)
    except Exception as e:
        if _should_retry(e) and _retry_allowed_by_limit():
            return _http_get_counted(url, params=params, timeout=timeout, is_retry=True)
        raise


def _fetch_alert_details(alert_ids, timeout=15):
    """Доп. запрос за деталями алерта — только когда алерты действительно есть."""
    details = []
    if not alert_ids or not config.WEATHER_API_KEY:
        return details
    with ThreadPoolExecutor(max_workers=min(len(alert_ids), 4)) as pool:
        futures = {
            pool.submit(
                _onecall_get,
                f"alert/{alert_id}",
                None,
                None,
                timeout,
                {},
            ): alert_id
            for alert_id in alert_ids
        }
        for fut in futures:
            try:
                details.append(fut.result())
            except Exception as e:
                _log.warning("weather: alert detail fetch failed: %s", e)
    return details


def fetch_weather(lat, lon, days=2):
    days = max(days, 2)
    key = (round(lat, 2), round(lon, 2), days)
    cache_key = _weather_cache_key(lat, lon, days)
    cached = _weather_cache_get(key, cache_key, max_age=_WX_TTL)
    if cached is not None:
        return cached
    if not config.WEATHER_API_KEY:
        stale = _weather_cache_get(key, cache_key, max_age=_WX_STALE_TTL)
        if stale is not None:
            return stale
        raise Exception("no weather api key")

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_current = pool.submit(_onecall_get, "current", lat, lon)
            fut_hourly = pool.submit(_onecall_get, "timeline/1h", lat, lon)
            fut_daily = pool.submit(_onecall_get, "timeline/1day", lat, lon)
            current_payload = fut_current.result()
            hourly_payload = fut_hourly.result()
            daily_payload = fut_daily.result()

        current_items = _first_data_item(current_payload)
        alert_ids = []
        if current_items:
            alert_ids = [a.get("id") for a in (current_items[0].get("alerts") or [])
                         if isinstance(a, dict) and a.get("id")]
        alerts = _fetch_alert_details(alert_ids)

        data = _adapt_openweather(current_payload, hourly_payload, daily_payload, alerts)
        now = time.time()
        _WX_CACHE[key] = (now, deepcopy(data))
        _persistent_cache_save(cache_key, data)
        return data
    except Exception:
        stale = _weather_cache_get(key, cache_key, max_age=_WX_STALE_TTL)
        if stale is not None:
            _log.warning("weather: using stale same-day forecast cache after fetch failure")
            return stale
        raise

def fetch_current_temp(lat, lon):
    try:
        if not config.WEATHER_API_KEY:
            return None
        payload = _onecall_get("current", lat, lon, timeout=15)
        items = _first_data_item(payload)
        return items[0].get("temp") if items else None
    except Exception:
        return None


def fetch_current_conditions(lat, lon):
    """Current conditions for an explicit screen open, cached for 10 minutes."""
    key = (round(float(lat), 2), round(float(lon), 2))
    cached = _CURRENT_CACHE.get(key)
    if cached and time.time() - cached[0] < _CURRENT_TTL:
        return deepcopy(cached[1])
    try:
        if not config.WEATHER_API_KEY:
            return None
        payload = _onecall_get("current", lat, lon, timeout=15)
        items = _first_data_item(payload)
        if not items:
            return None
        current = items[0]
        weather_id = ((current.get("weather") or [{}])[0] or {}).get("id")
        result = {
            "temperature_2m": current.get("temp"),
            "apparent_temperature": current.get("feels_like"),
            "weathercode": _owm_weathercode(weather_id),
        }
        _CURRENT_CACHE[key] = (time.time(), result)
        return deepcopy(result)
    except Exception:
        return None

