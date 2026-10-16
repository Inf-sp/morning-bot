"""OpenWeather provider, quota accounting and persistent forecast cache."""

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import time
import requests

import api_usage
import config
import tracking
import store

_log = logging.getLogger(__name__)
TZ = config.TZ

_WX_CACHE = {}          # (lat2, lon2) -> (ts, json)
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


def _weather_time_iso(value):
    """Нормализует время солнца из epoch или ISO-строки разных ответов API."""
    if value in (None, "", 0, "0"):
        return ""
    try:
        return _owm_iso(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.astimezone(TZ).strftime("%Y-%m-%dT%H:%M")
        except (TypeError, ValueError):
            return ""


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
        "winddirection_10m": [],
        "temperature_2m": [],
        "apparent_temperature": [],
        "relativehumidity_2m": [],
        "uv_index": [],
        "cloudcover": [],
    }
    for h in hourly:
        hourly_out["time"].append(_owm_iso(h.get("dt", 0)))
        hourly_out["precipitation_probability"].append(round(float(h.get("pop") or 0) * 100))
        hourly_out["precipitation"].append(round(_owm_precip_mm(h), 2))
        hourly_out["windspeed_10m"].append(h.get("wind_speed") or 0)
        hourly_out["windgusts_10m"].append(h.get("wind_gust") or h.get("wind_speed") or 0)
        hourly_out["winddirection_10m"].append(h.get("wind_deg"))
        hourly_out["temperature_2m"].append(h.get("temp"))
        # «Ощущается как» — для подбора одежды по реальному комфорту, а не по градуснику.
        hourly_out["apparent_temperature"].append(h.get("feels_like", h.get("temp")))
        hourly_out["relativehumidity_2m"].append(h.get("humidity"))
        hourly_out["uv_index"].append(h.get("uvi"))
        hourly_out["cloudcover"].append(h.get("clouds"))

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
        "sunrise": [],
        "sunset": [],
    }
    for index, d in enumerate(daily):
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
        sunrise = d.get("sunrise") or d.get("sunriseTime")
        sunset = d.get("sunset") or d.get("sunsetTime")
        if index == 0:
            sunrise = sunrise or current.get("sunrise") or current.get("sunriseTime")
            sunset = sunset or current.get("sunset") or current.get("sunsetTime")
        daily_out["sunrise"].append(_weather_time_iso(sunrise))
        daily_out["sunset"].append(_weather_time_iso(sunset))

    current_weather_id = ((current.get("weather") or [{}])[0] or {}).get("id")
    alert_ids = [a.get("id") for a in (current.get("alerts") or []) if isinstance(a, dict) and a.get("id")]
    return {
        "current": {
            "temperature_2m": current.get("temp"),
            "apparent_temperature": current.get("feels_like"),
            "weathercode": _owm_weathercode(current_weather_id),
            "windspeed_10m": current.get("wind_speed"),
            "windgusts_10m": current.get("wind_gust") or current.get("wind_speed"),
            "winddirection_10m": current.get("wind_deg"),
            "visibility": current.get("visibility"),
            "uv_index": current.get("uvi"),
            "cloudcover": current.get("clouds"),
        },
        "hourly": hourly_out,
        "daily": daily_out,
        "alert_ids": alert_ids,
        "alerts": alerts or [],
        "provider": "openweathermap",
        # Сырой дневной timeline нужен только месячному экрану: он позволяет
        # не скачивать первую страницу повторно после открытия недели.
        "daily_timeline": deepcopy(daily),
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


def _weather_cache_key(lat, lon, _days=None):
    """Один кэш на точку: все экраны («Мой день», Гардероб, полный и недельный прогноз) получают
    один и тот же ответ API, поэтому число дней в ключ не входит."""
    return f"{round(float(lat), 2):.2f}:{round(float(lon), 2):.2f}"


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
    now = time.time()
    entry = {"ts": now, "data": deepcopy(data)}

    def change(cache):
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
        clean[cache_key] = entry
        return clean, None

    store.mutate_kv(config.WEATHER_CACHE_KEY, change)


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
        r = requests.get(url, params=params, timeout=tracking.bounded_timeout(timeout))
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


_OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
_OPEN_METEO_HOURLY = ("temperature_2m", "apparent_temperature", "precipitation_probability", "precipitation",
                      "windspeed_10m", "windgusts_10m", "winddirection_10m", "relativehumidity_2m",
                      "uv_index", "cloudcover")
_OPEN_METEO_DAILY = ("weathercode", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max",
                     "precipitation_sum", "windspeed_10m_max", "windgusts_10m_max", "winddirection_10m_dominant",
                     "uv_index_max", "sunrise", "sunset")
_OPEN_METEO_CURRENT = ("temperature_2m", "apparent_temperature", "weathercode", "windspeed_10m", "windgusts_10m",
                       "winddirection_10m", "uv_index", "cloudcover")


_OWM_PAUSE_SECONDS = 30 * 60  # после сбоя OpenWeather полчаса сразу берём Open-Meteo, не ждём таймаутов
_owm_down_until = [0.0]
_OPEN_METEO_MIN_TIMEOUT = 5.0  # резерв получает своё время, даже если OpenWeather съел бюджет действия


def _fetch_open_meteo(lat, lon):
    """Резерв без ключа: Open-Meteo в том же формате (WMO-коды, м/с, время Амстердама); без алертов."""
    timeout = max(_OPEN_METEO_MIN_TIMEOUT, tracking.bounded_timeout(10))
    response = requests.get(_OPEN_METEO_URL, timeout=timeout, params={
        "latitude": lat, "longitude": lon, "timezone": str(TZ), "forecast_days": 9, "wind_speed_unit": "ms",
        "hourly": ",".join(_OPEN_METEO_HOURLY), "daily": ",".join(_OPEN_METEO_DAILY),
        "current": ",".join(_OPEN_METEO_CURRENT),
    })
    response.raise_for_status()
    payload = response.json()
    hourly, daily = payload.get("hourly") or {}, payload.get("daily") or {}
    if not daily.get("time") or not hourly.get("time"):
        raise ValueError("open-meteo: empty forecast")
    return {
        "current": {**{key: (payload.get("current") or {}).get(key) for key in _OPEN_METEO_CURRENT},
                    "visibility": None},
        "hourly": {key: list(hourly.get(key) or []) for key in ("time", *_OPEN_METEO_HOURLY)},
        "daily": {key: list(daily.get(key) or []) for key in ("time", *_OPEN_METEO_DAILY)},
        "alert_ids": [], "alerts": [], "provider": "open-meteo", "daily_timeline": [],
    }


_OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_HORIZON_DAYS = 15  # Open-Meteo даёт прогноз на 16 дней вперёд, включая сегодня


def period_weather(lat, lon, start, end, today) -> dict:
    """Дневная погода поездки: {tmin, tmax, rainy, days, wind, typical}. Поездка в ближайшие 16 дней — прогноз
    Open-Meteo (дальше горизонта — обрезается); позже — фактическая погода тех же дней год назад (архив,
    typical=True: «обычно в эти дни»). Без ключа; сбой — исключение."""
    from datetime import timedelta
    typical = start > today + timedelta(days=_FORECAST_HORIZON_DAYS)
    if typical:
        first, last = start.replace(year=start.year - 1), end.replace(year=end.year - 1)
        url, daily = _OPEN_METEO_ARCHIVE_URL, ("temperature_2m_max", "precipitation_sum", "windspeed_10m_max")
    else:
        first, last = start, min(end, today + timedelta(days=_FORECAST_HORIZON_DAYS))
        url, daily = _OPEN_METEO_URL, ("temperature_2m_max", "precipitation_probability_max", "precipitation_sum",
                                       "windspeed_10m_max")
    response = requests.get(url, timeout=max(_OPEN_METEO_MIN_TIMEOUT, tracking.bounded_timeout(12)), params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "wind_speed_unit": "ms", "daily": ",".join(daily),
        "start_date": first.isoformat(), "end_date": last.isoformat(),
    })
    response.raise_for_status()
    rows = (response.json() or {}).get("daily") or {}
    highs = [value for value in rows.get("temperature_2m_max") or [] if value is not None]
    if not highs:
        raise ValueError("open-meteo: empty period")
    count = len(rows.get("time") or highs)
    chance, rain_mm = rows.get("precipitation_probability_max") or [], rows.get("precipitation_sum") or []
    rainy = sum(1 for index in range(count)
                if (index < len(chance) and (chance[index] or 0) >= 50)
                or (typical and index < len(rain_mm) and (rain_mm[index] or 0) >= 1))
    return {"tmin": round(min(highs)), "tmax": round(max(highs)), "rainy": rainy, "days": count,
            "wind": round(max((value or 0) for value in rows.get("windspeed_10m_max") or [0])), "typical": typical}


def _fallback_weather(key, cache_key, lat, lon, error):
    """OpenWeather недоступен или лимит исчерпан: сегодняшний кэш, затем Open-Meteo; иначе — исходная ошибка."""
    stale = _weather_cache_get(key, cache_key, max_age=_WX_STALE_TTL)
    if stale is not None:
        _log.warning("weather: using stale same-day forecast cache after fetch failure")
        return stale
    try:
        data = _fetch_open_meteo(lat, lon)
    except Exception:
        _log.warning("weather: open-meteo fallback failed", exc_info=True)
        raise error
    _log.warning("weather: OpenWeather unavailable (%s), using Open-Meteo", type(error).__name__)
    _WX_CACHE[key] = (time.time(), deepcopy(data))
    _persistent_cache_save(cache_key, data)
    return data


def fetch_weather(lat, lon, days=2):
    """Прогноз точки (текущая погода, по часам, по дням); days — для совместимости, API отдаёт всё сразу."""
    key = (round(lat, 2), round(lon, 2))
    cache_key = _weather_cache_key(lat, lon)
    cached = _weather_cache_get(key, cache_key, max_age=_WX_TTL)
    if cached is not None:
        return cached
    if not config.WEATHER_API_KEY:
        return _fallback_weather(key, cache_key, lat, lon, Exception("no weather api key"))
    if time.time() < _owm_down_until[0]:
        try:
            return _fallback_weather(key, cache_key, lat, lon, Exception("openweather paused after failure"))
        except Exception:
            _log.info("weather: fallback failed during OpenWeather pause, retrying OpenWeather")

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_current = pool.submit(contextvars.copy_context().run, _onecall_get, "current", lat, lon)
            fut_hourly = pool.submit(contextvars.copy_context().run, _onecall_get, "timeline/1h", lat, lon)
            fut_daily = pool.submit(contextvars.copy_context().run, _onecall_get, "timeline/1day", lat, lon)
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
    except Exception as error:
        _owm_down_until[0] = time.time() + _OWM_PAUSE_SECONDS
        _log.warning("weather: OpenWeather failed (%r), pausing it for %s min", error, _OWM_PAUSE_SECONDS // 60)
        return _fallback_weather(key, cache_key, lat, lon, error)


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
