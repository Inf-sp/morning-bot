import asyncio
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from copy import deepcopy
import requests

_log = logging.getLogger(__name__)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
from util import cap_sentence, _WEEKDAYS, _WEEKDAY_SHORT, _MONTHS
import verify
from ui import weather as weather_ui

TZ = config.TZ

# Порог вероятности дождя: ниже - дождя нет (эмодзи и проценты не показываем)
RAIN_PROB_MIN = 50
# Минимум реальных осадков (мм) для подтверждения дождя при высокой вероятности
RAIN_MM_MIN = 0.1
# Сильный дождь/ливень: мм осадков за сутки (или пик за час в дневном окне)
HEAVY_RAIN_MM_DAY = 4.0
HEAVY_RAIN_MM_HOUR = 2.0
# Сильный ветер (м/с): согласовано с wind_scale («Сильный ветер» начинается с 8)
STRONG_WIND_MS = 8
# Дневное окно «когда пользователь обычно выходит из дома» (часы)
DAYTIME_START_H = 8
DAYTIME_END_H = 22

DESC = {0: "ясно", 1: "малооблачно", 2: "переменно облачно", 3: "пасмурно", 45: "туман", 48: "туман",
        51: "морось", 53: "морось", 55: "морось", 61: "дождь", 63: "дождь", 65: "сильный дождь",
        71: "снег", 73: "снег", 75: "сильный снег", 80: "ливень", 81: "ливень", 95: "гроза"}


# Кеш прогноза: один общий ответ OpenWeatherMap на myday/wardrobe/weather в пределах TTL
_WX_CACHE = {}          # (lat2, lon2, days) -> (ts, json)
_WX_TTL = 600           # сек


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


def get_weather_usage_last_days(days=7):
    today = datetime.now(TZ).date()
    rows = []
    for i in range(max(1, days)):
        day = today - timedelta(days=i)
        rows.append(get_weather_usage(datetime(day.year, day.month, day.day, tzinfo=TZ)))
    return rows


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


def _mark_weather_failed(reason):
    now = datetime.now(TZ)

    def _mut(data):
        data["requests_failed"] = int(data.get("requests_failed") or 0) + 1
        data["last_error_at"] = now.isoformat()
        data["last_error_reason"] = str(reason or "request failed")[:80]
        return data, True
    _usage_mutate(_mut, now)


def _mark_weather_cache_hit():
    def _mut(data):
        data["cache_hits"] = int(data.get("cache_hits") or 0) + 1
        return data, True
    _usage_mutate(_mut)


def weather_usage_status(usage=None):
    usage = usage or get_weather_usage()
    total = int(usage.get("requests_total") or 0)
    if total >= config.WEATHER_HARD_DAILY_LIMIT:
        return "blocked"
    if total >= config.WEATHER_CRITICAL_LIMIT:
        return "critical"
    if total >= config.WEATHER_WARNING_LIMIT:
        return "warning"
    return "ok"


def weather_usage_status_text(usage=None):
    status = weather_usage_status(usage)
    return {
        "ok": "🟢 Лимит в норме",
        "warning": "🟡 Использование растёт",
        "critical": "🟠 Почти достигнут бесплатный лимит",
        "blocked": "🔴 Новые запросы заблокированы до следующего дня",
    }[status]


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
    import time
    days = max(days, 2)
    key = (round(lat, 2), round(lon, 2), days)
    hit = _WX_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _WX_TTL:
        _mark_weather_cache_hit()
        return hit[1]
    if not config.WEATHER_API_KEY:
        raise Exception("no weather api key")

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
    _WX_CACHE[key] = (time.time(), deepcopy(data))
    return data

def fetch_current_temp(lat, lon):
    try:
        if not config.WEATHER_API_KEY:
            return None
        payload = _onecall_get("current", lat, lon, timeout=15)
        items = _first_data_item(payload)
        return items[0].get("temp") if items else None
    except Exception:
        return None


# ---------- ветер ----------
def wind_direction_text(deg):
    """Градусы метео-направления → русское название (откуда дует)."""
    if deg is None:
        return ""
    sectors = [
        "северный", "северо-восточный", "восточный", "юго-восточный",
        "южный", "юго-западный", "западный", "северо-западный",
    ]
    return sectors[round(float(deg) / 45) % 8]


def wind_scale(ms):
    if ms < 3:
        return "🌬️", "Почти без ветра"
    if ms < 5:
        return "💨", "Лёгкий ветер"
    if ms < 8:
        return "🌪️", "Умеренный ветер"
    if ms < 11:
        return "⚠️", "Сильный ветер"
    return "🚨", "Очень сильный ветер"


def _rain_real(rain, rain_mm=None):
    """True, если дождь стоит показывать: вероятность >= порога и (мм неизвестны или >= минимума)."""
    if rain < RAIN_PROB_MIN:
        return False
    if rain_mm is not None and rain_mm < RAIN_MM_MIN:
        return False
    return True


def _finish_sentence(text):
    text = (text or "").strip()
    if text and text[-1] not in ".!?…":
        return text + "."
    return text


def rain_text(rain, rain_mm=None, when=""):
    """Кусок строки про дождь. Пусто, только если вероятность нулевая."""
    if rain:
        return f"Дождь{when} {rain:.0f}%"
    return ""


def _weather_main_lines(icon, tmax, rain, rain_mm, rain_when, wind_ms):
    rain_part = rain_text(rain, rain_mm, rain_when)
    wemoji, wword = wind_scale(wind_ms)
    wind_str = f"{wemoji} {wword} {wind_ms:.0f} м/с" if wind_ms >= 8 else f"💨 Ветер {wind_ms:.0f} м/с"

    first = f"{icon} До {tmax:+.0f}°C"
    if rain_part:
        first += f" • {rain_part}"
    if wind_ms >= 8:
        return [first, "", wind_str]
    return [f"{first} • {wind_str}"]


def rain_character(code, rain_mm, rain_prob, data, day_str):
    """Доп. фраза о характере осадков — только для нетривиальных типов."""
    if not _rain_real(rain_prob, rain_mm):
        return ""
    if code in (95, 96, 99):
        return "Возможны кратковременные грозы"
    if code in (65, 82):
        return "Сильный дождь" + (f" ({rain_mm:.0f} мм)" if rain_mm else "")
    if code in (80, 81):
        return "Ливень"
    if code in (51, 53, 55):
        return "Морось"
    return ""


def humidity_phrase(data, day_str, tmax, cc):
    """Заголовок и пояснение о комфорте с учётом влажности; ('', '') если нечего добавить."""
    try:
        hours = data["hourly"]["time"]
        hum_vals = data["hourly"].get("relativehumidity_2m") or []
    except Exception:
        return "", ""
    if not hum_vals:
        return "", ""
    day_hum = [
        v for t, v in zip(hours, hum_vals)
        if t.startswith(day_str) and 6 <= int(t[11:13]) < 21 and v is not None
    ]
    if not day_hum:
        return "", ""
    rh = sum(day_hum) / len(day_hum)
    if rh >= 80 and tmax >= 22:
        return "💧 Высокая влажность", "Может ощущаться теплее, чем показывает температура"
    if rh >= 70 and tmax >= 20:
        return "💧 Высокая влажность", "Из-за влажности может казаться жарче"
    if rh >= 75 and (cc or "").upper() == "NL":
        return "💧 Высокая влажность", "Вечерами у каналов будет свежо"
    if rh < 35:
        return "💧 Низкая влажность", "Воздух сухой"
    return "", ""


# ---------- иконка ----------
def weather_icon(code, temp, rain, wind_ms=0, rain_mm=None):
    if code in (95, 96, 99):
        return "🌩️"
    if code in (71, 73, 75, 77, 85, 86):
        return "❄️"
    wet = _rain_real(rain, rain_mm)
    if temp is not None and temp > 30 and wet:
        return "☀️🌧️"
    if wet:
        return "🌧️"
    if wind_ms >= 8:
        return "💨"
    if temp is not None and temp >= 30:
        return "🥵"
    if code in (0, 1) or (temp is not None and temp >= 28):
        return "☀️"
    return "☁️"


# ---------- периоды по часам ----------
def _periods(data, day_str, key, threshold):
    try:
        hours = data["hourly"]["time"]
        vals = data["hourly"][key]
    except Exception:
        return []
    buckets = {"утром": (6, 12), "днём": (12, 18), "вечером": (18, 24), "ночью": (0, 6)}
    hit = []
    for name, (h1, h2) in buckets.items():
        for t, v in zip(hours, vals):
            if t.startswith(day_str) and h1 <= int(t[11:13]) < h2 and (v or 0) >= threshold:
                hit.append(name)
                break
    # порядок: утром, днём, вечером, ночью
    return [p for p in ["утром", "днём", "вечером", "ночью"] if p in hit]


# ---------- дневное среднее ветра ----------
def _daytime_avg_wind(data, day_str):
    """Среднее ветра 6–21ч из hourly — то, что показывают Buienradar/KNMI вместо суточного пика."""
    try:
        hours = data["hourly"]["time"]
        vals = data["hourly"]["windspeed_10m"]
    except Exception:
        return None
    day_vals = [v for t, v in zip(hours, vals)
                if t.startswith(day_str) and 6 <= int(t[11:13]) < 21 and v is not None]
    return sum(day_vals) / len(day_vals) if day_vals else None


def _daytime_max(data, day_str, key):
    """Максимум hourly-показателя в дневном окне DAYTIME_START_H..DAYTIME_END_H."""
    try:
        hours = data["hourly"]["time"]
        vals = data["hourly"][key]
    except (KeyError, TypeError):
        return None
    day_vals = [v for t, v in zip(hours, vals)
                if t.startswith(day_str)
                and DAYTIME_START_H <= int(t[11:13]) < DAYTIME_END_H
                and v is not None]
    return max(day_vals) if day_vals else None


def daytime_outfit_weather(data, day_str, tmax, wind_ms, rain_prob_day, rain_mm_day, weathercode):
    """Погодные флаги для подбора образа с учётом дневного окна 8–22.

    Возвращает dict с числами и булевыми флагами. Дождь оценивается по максимуму
    в дневном окне (когда человек выходит из дома), с фолбэком на суточный агрегат.
    """
    prob_win = _daytime_max(data, day_str, "precipitation_probability")
    mm_win = _daytime_max(data, day_str, "precipitation")
    wind_win = _daytime_max(data, day_str, "windspeed_10m")

    rain_prob = prob_win if prob_win is not None else (rain_prob_day or 0)
    rain_mm = mm_win if mm_win is not None else rain_mm_day
    wind = wind_win if wind_win is not None else wind_ms

    rain_daytime = _rain_real(rain_prob, rain_mm)
    heavy_rain = bool(
        (rain_mm_day is not None and rain_mm_day >= HEAVY_RAIN_MM_DAY)
        or (mm_win is not None and mm_win >= HEAVY_RAIN_MM_HOUR)
        or (weathercode in (65, 80, 81, 82, 95, 96, 99))
    )
    strong_wind = wind is not None and wind >= STRONG_WIND_MS
    sunny = (weathercode in (0, 1)) and (tmax is not None and tmax >= 24) and not rain_daytime

    return {
        "rain_prob": round(rain_prob) if rain_prob is not None else 0,
        "rain_mm": round(rain_mm, 1) if rain_mm is not None else None,
        "wind_ms": round(wind) if wind is not None else wind_ms,
        "rain_daytime": rain_daytime,
        "heavy_rain": heavy_rain,
        "strong_wind": strong_wind,
        "sunny": sunny,
    }


# ---------- мировой факт ----------
WORLD_POINTS = [
    ("🇰🇼", "Кувейте", 29.37, 47.98), ("🇦🇪", "Дубае", 25.20, 55.27), ("🇮🇳", "Дели", 28.61, 77.21),
    ("🇦🇶", "Антарктиде", -75.25, 0.07), ("🇷🇺", "Оймяконе", 63.46, 142.79), ("🇺🇸", "Долине Смерти", 36.46, -116.87),
    ("🇮🇸", "Рейкьявике", 64.15, -21.94), ("🇸🇬", "Сингапуре", 1.35, 103.82), ("🇪🇬", "Каире", 30.04, 31.24),
    ("🇧🇷", "Манаусе", -3.12, -60.02), ("🇨🇦", "Йеллоунайфе", 62.45, -114.37), ("🇦🇺", "Алис-Спрингсе", -23.70, 133.88),
    ("🇲🇳", "Улан-Баторе", 47.89, 106.91), ("🇨🇱", "Атакаме", -24.5, -69.25), ("🇳🇴", "Шпицбергене", 78.22, 15.63),
]

def _world_fact():
    pts = random.sample(WORLD_POINTS, 4)
    readings = []
    for flag, name, lat, lon in pts:
        t = fetch_current_temp(lat, lon)
        if t is not None:
            readings.append((flag, name, t))
    if not readings:
        return ""
    flag, name, t = max(readings, key=lambda x: abs(x[2]))
    try:
        line = ai.llm(
            f"Сейчас в {name} {t:+.0f}°C (реальные данные). Напиши ОДНУ фразу, начни СТРОГО со слов "
            f"«Кстати, сегодня в {name} ...», с лёгким юмором, на русском, 1 предложение, без markdown.",
            120, 1.05, tier="cheap").strip().splitlines()[0]
    except Exception:
        line = f"Кстати, сегодня в {name} около {t:+.0f}°C."
    return f"{flag} {line}"

def _joke_outfit(city, tmax, rain, wind_ms, desc, when="сегодня"):
    try:
        return ai.llm(
            f"Город {city}, {when}: {desc}, до {tmax:+.0f}°C, дождь {rain:.0f}%, ветер {wind_ms:.0f} м/с. "
            f"Напиши ОДНУ дерзкую дружелюбную фразу + короткий совет по одежде (нужна ли куртка/зонт). "
            f"1 предложение, на русском, без markdown.", 120, 1.05, tier="cheap").strip().splitlines()[0]
    except Exception:
        return f"Сегодня {city} явно выиграл погодную лотерею."


# ---------- экстремальная погода (Code Geel и сильнее) ----------
STORM_WIND_MS = 15      # порог шквалов
SNOW_CODES = (71, 73, 75, 77, 85, 86)
HEAVY_RAIN_CODES = (65, 81, 82, 95, 96, 99)

def storm_alert(wind_ms, code, rain, rain_mm=None, cc=""):
    """Возвращает текст штормового блока или '' если угрозы нет.
    Триггер: ветер > 15 м/с, снегопад, ливень/гроза. NS/Buienradar - только для NL."""
    reasons = []
    if wind_ms and wind_ms > STORM_WIND_MS:
        reasons.append("wind")
    if code in SNOW_CODES:
        reasons.append("snow")
    if code in HEAVY_RAIN_CODES or (rain_mm is not None and rain_mm >= 15):
        reasons.append("rain")
    if not reasons:
        return ""
    return weather_ui.storm_alert_html(reasons, wind_ms, is_nl=(cc or "").upper() == "NL")

def _meteo_fact(city, tmax, rain, wind_ms, desc, date_label="",
                country="", cc="", lat=None, lon=None, tz="UTC"):
    """Исторические погодные рекорды отключены: текущая погода берётся только из OpenWeatherMap."""
    return ""


# ---------- отправка ----------
async def send_weather(bot, cid, mode="today"):
    s = store.get_settings(cid)
    country = s.get("country", "")
    place = f"{s['city']}, {country}" if country else s["city"]
    try:
        data = fetch_weather(s["lat"], s["lon"], 9)
    except WeatherDailyLimitExceeded:
        await bot.send_message(chat_id=cid, text=WEATHER_LIMIT_FALLBACK)
        return
    d = data["daily"]
    now = datetime.now(TZ)

    if mode == "full":
        dt = now
        header = f"Полный прогноз на сегодня • {_WEEKDAYS[dt.weekday()]}, {dt.day} {_MONTHS[dt.month-1]} • {s['city']}"
        try:
            hours = data["hourly"]["time"]
            temps = data["hourly"].get("temperature_2m") or []
            probs = data["hourly"]["precipitation_probability"]
            precs = data["hourly"].get("precipitation") or []
            winds = data["hourly"]["windspeed_10m"]
        except Exception:
            temps = probs = precs = winds = []
        day_str = d["time"][0]
        periods = []
        parts = [("Утром", 6, 12), ("Днём", 12, 18), ("Вечером", 18, 24)]
        for label, h1, h2 in parts:
            t_vals, p_vals, w_vals, mm_vals, code_v = [], [], [], [], 1
            for i, ts in enumerate(hours):
                if ts.startswith(day_str) and h1 <= int(ts[11:13]) < h2:
                    if i < len(temps): t_vals.append(temps[i] or 0)
                    if i < len(probs): p_vals.append(probs[i] or 0)
                    if i < len(winds): w_vals.append(winds[i] or 0)
                    if i < len(precs): mm_vals.append(precs[i] or 0)
            if not t_vals:
                continue
            tmx = max(t_vals); rn = max(p_vals) if p_vals else 0; wd = max(w_vals) if w_vals else 0
            mm = max(mm_vals) if mm_vals else None
            icon = weather_icon(d["weathercode"][0], tmx, rn, wd, mm)
            wemoji, wword = wind_scale(wd)
            wind_str = f"{wemoji} {wword} {wd:.0f} м/с" if wd >= 8 else f"💨 Ветер {wd:.0f} м/с"
            rain_part = rain_text(rn, mm)
            line = f"{icon} До {tmx:+.0f}°C"
            if rain_part:
                line += f" • {rain_part}"
            line += f" • {wind_str}"
            periods.append({"label": label, "line": line})
        joke = _joke_outfit(s["city"], d["temperature_2m_max"][0], d["precipitation_probability_max"][0] or 0,
                            d["windspeed_10m_max"][0] or 0, DESC.get(d["weathercode"][0], ""), "сегодня")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="a_plany")]])
        msg = weather_ui.full_forecast(header, periods, joke)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return

    if mode in ("today", "tomorrow"):
        day = 0 if mode == "today" else 1
        dt = now + timedelta(days=day)
        title = "сегодня" if mode == "today" else "завтра"
        flag = __import__("util").flag_from_cc(s.get("cc", "")) or ""
        header = f"Погода на {title} • {_WEEKDAYS[dt.weekday()]}, {dt.day} {_MONTHS[dt.month-1]} • {s['city']} {flag}"
        code = d["weathercode"][day]
        tmax = d["temperature_2m_max"][day]
        rain = d["precipitation_probability_max"][day] or 0
        rain_mm = (d.get("precipitation_sum") or [None] * (day + 1))[day] if d.get("precipitation_sum") else None
        wind_ms = d["windspeed_10m_max"][day] or 0
        day_str = d["time"][day]
        icon = weather_icon(code, tmax, rain, wind_ms, rain_mm)
        rain_p = _periods(data, day_str, "precipitation_probability", RAIN_PROB_MIN)
        rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""

        main_lines = _weather_main_lines(icon, tmax, rain, rain_mm, rain_when, wind_ms)
        alert = ""
        fact_title = ""
        fact = ""

        if mode == "tomorrow":
            desc = DESC.get(code, "")
            cc = s.get("cc", "")
            country = s.get("country", "")
            alert = storm_alert(wind_ms, code, rain, rain_mm, cc=cc)
            if not alert:
                date_lbl = header.split("•")[1].strip() if "•" in header else ""
                mf = _meteo_fact(s["city"], tmax, rain, wind_ms, desc, date_lbl,
                                country=country, cc=cc,
                                lat=s["lat"], lon=s["lon"], tz=str(TZ))
                if mf:
                    fact_title = "Метео-факт"
                    fact = mf
        else:
            fact = _world_fact()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="a_plany")]])
        msg = weather_ui.day_forecast(header, main_lines, alert=alert, fact_title=fact_title, fact=fact)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return

    if mode == "tomorrow_plain":
        day = 1
        dt = now + timedelta(days=1)
        flag = __import__("util").flag_from_cc(s.get("cc", "")) or ""
        header = f"Погода на завтра • {_WEEKDAYS[dt.weekday()]}, {dt.day} {_MONTHS[dt.month-1]} • {s['city']} {flag}"
        code = d["weathercode"][day]
        tmax = d["temperature_2m_max"][day]
        rain = d["precipitation_probability_max"][day] or 0
        rain_mm = (d.get("precipitation_sum") or [None] * (day + 1))[day] if d.get("precipitation_sum") else None
        wind_ms = d["windspeed_10m_max"][day] or 0
        day_str = d["time"][day]
        icon = weather_icon(code, tmax, rain, wind_ms, rain_mm)
        rain_p = _periods(data, day_str, "precipitation_probability", RAIN_PROB_MIN)
        rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
        desc = DESC.get(code, "")
        cc = s.get("cc", "")
        alert = storm_alert(wind_ms, code, rain, rain_mm, cc=cc)
        main_lines = _weather_main_lines(icon, tmax, rain, rain_mm, rain_when, wind_ms)
        fact = ""
        if alert:
            pass
        else:
            try:
                rain_desc = f"дождь {rain:.0f}%{rain_when}" if _rain_real(rain, rain_mm) else "без осадков"
                summary = await ai.allm(
                    f"Погода завтра в {s['city']}: {desc}, до {tmax:+.0f}°C, {rain_desc}, "
                    f"ветер {wind_ms:.0f} м/с.\n\n"
                    "Напиши короткий метео-итог: 2-3 предложения — общая картина, что ждать. "
                    "Без слова 'зонт'. Без markdown. На русском.",
                    150, 0.6, tier="cheap", module="weather"
                ).strip()
                if summary:
                    fact = _finish_sentence(cap_sentence(summary))
            except Exception:
                pass
        msg = weather_ui.day_forecast(header, main_lines, alert=alert, fact_title="Метео-итог", fact=fact)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
        return

    # week/week_plain: компактный формат — одна строка на день/группу
    week_plain = mode == "week_plain"
    _SKIP = 1
    d1 = now + timedelta(days=_SKIP)
    d2 = now + timedelta(days=_SKIP + 6)
    if d1.month == d2.month:
        rng = f"{d1.day}–{d2.day} {_MONTHS[d1.month-1]}"
    else:
        rng = f"{d1.day} {_MONTHS[d1.month-1]} – {d2.day} {_MONTHS[d2.month-1]}"
    flag = __import__("util").flag_from_cc(s.get("cc", ""))

    # Сбор данных для 7 дней
    day_data = []
    for i in range(7):
        idx = _SKIP + i
        if idx >= len(d["weathercode"]):
            break
        dt_i = now + timedelta(days=idx)
        code = d["weathercode"][idx]
        tmax = d["temperature_2m_max"][idx] or 0
        rain = d["precipitation_probability_max"][idx] or 0
        rain_mm = (d.get("precipitation_sum") or [None] * 10)[idx]
        day_str = d["time"][idx]
        wind_max = d["windspeed_10m_max"][idx] or 0
        rain_p = _periods(data, day_str, "precipitation_probability", RAIN_PROB_MIN)
        rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
        day_data.append({
            "abbrev": _WEEKDAY_SHORT[dt_i.weekday()],
            "icon": weather_icon(code, tmax, rain, wind_max, rain_mm),
            "tmax": tmax,
            "code": code,
            "rain": rain,
            "rain_mm": rain_mm,
            "rain_when": rain_when,
            "rain_real": _rain_real(rain, rain_mm),
            "wind": wind_max,
        })

    # LLM: компактные описания дней (с группировкой) + итог — один вызов
    ordered_abbrevs = [dd["abbrev"] for dd in day_data]
    abbrev_to_idx = {a: i for i, a in enumerate(ordered_abbrevs)}

    prompt_lines = [
        f"{dd['abbrev']}: {DESC.get(dd['code'], 'ясно')}, до {dd['tmax']:+.0f}°C"
        + (f", дождь {dd['rain']:.0f}%{dd['rain_when']}" if dd["rain_real"] else "")
        + f", ветер {dd['wind']:.0f} м/с"
        for dd in day_data
    ]
    groups = []
    summary = ""
    try:
        llm_result = await ai.allm_json(
            f"Погода на неделю в {s['city']}:\n" + "\n".join(prompt_lines) + "\n\n"
            "Верни JSON:\n"
            '{"groups":[{"abbrevs":["Пн"],"desc":"дождь утром и ночью"},'
            '{"abbrevs":["Вт","Ср"],"desc":"облачно"}],"summary":"1-2 предложения"}\n\n'
            "Правила: desc — 3-7 слов, суть без цифр; "
            "объединять ТОЛЬКО идущие подряд дни (Ср-Чт-Пт — можно, Пн-Сб через пропуск — нельзя); "
            "все 7 дней должны войти в группы; "
            "summary — 1-2 предложения без слова «зонт», без markdown.",
            300, tier="cheap", module="weather"
        )
        groups = llm_result.get("groups", [])
        summary = (llm_result.get("summary") or "").strip()
    except Exception:
        groups = [{"abbrevs": [dd["abbrev"]], "desc": DESC.get(dd["code"], "")} for dd in day_data]

    # Валидация: разбиваем группу на одиночные дни если дни не идут подряд
    def _split_if_gaps(grp):
        abbrevs = [a for a in (grp.get("abbrevs") or []) if a in abbrev_to_idx]
        if len(abbrevs) <= 1:
            return [grp] if abbrevs else []
        idxs = [abbrev_to_idx[a] for a in abbrevs]
        if all(idxs[i + 1] == idxs[i] + 1 for i in range(len(idxs) - 1)):
            return [grp]
        return [{"abbrevs": [a], "desc": grp.get("desc", "")} for a in abbrevs]

    validated = []
    for grp in groups:
        validated.extend(_split_if_gaps(grp))
    groups = validated

    abbrev_map = {dd["abbrev"]: dd for dd in day_data}

    ui_groups = []
    for grp in groups:
        abbrevs = grp.get("abbrevs") or []
        desc = (grp.get("desc") or "").strip()
        grp_days = [abbrev_map[a] for a in abbrevs if a in abbrev_map]
        if not grp_days or not desc:
            continue
        rep = max(grp_days, key=lambda x: x["rain"])
        icon = rep["icon"]
        tmaxes = [gd["tmax"] for gd in grp_days]
        tmin_g, tmax_g = min(tmaxes), max(tmaxes)
        temp_str = f"+{tmin_g:.0f}…{tmax_g:.0f}°C" if tmax_g - tmin_g > 1 else f"до {tmax_g:+.0f}°C"
        day_label = abbrevs[0] if len(abbrevs) == 1 else f"{abbrevs[0]}-{abbrevs[-1]}"
        ui_groups.append({"icon": icon, "label": day_label, "desc": desc, "temp": temp_str})

    kb = None if week_plain else InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="a_plany")]])
    msg = weather_ui.week_forecast(rng, s["city"], flag, ui_groups, summary)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ---------- смена города ----------
async def set_city_text(bot, cid, name, show_brief=True):
    import re as _re
    raw = (name or "").strip()
    # нормализация: убрать пробелы вокруг тире, схлопнуть пробелы
    q = _re.sub(r"\s*-\s*", "-", raw)
    q = _re.sub(r"\s+", " ", q)
    # варианты запроса: нормализованный, без тире (через пробел), оригинал
    variants = []
    for v in (q, q.replace("-", " "), raw):
        v = v.strip()
        if v and v not in variants:
            variants.append(v)
    # LLM-нормализация: "Питер" → "Санкт-Петербург", "Лондон" → "London" и т.п.
    try:
        official = await ai.allm(
            f"Какое официальное название у города «{raw}»? "
            "Если это прозвище или сокращение (Питер, Нью-Йорк, Первопрестольная…), "
            "верни официальное название. Если уже официальное — верни как есть. "
            "Только название, без пояснений.",
            40, 0.1, tier="cheap", route="cf"
        )
        official = official.strip().strip("«»\"'.").split("\n")[0].strip()
        if official and official.lower() not in {v.lower() for v in variants}:
            variants.insert(0, official)
    except Exception:
        pass
    try:
        res = None
        # 1) OpenWeatherMap geocoder: по вариантам запроса
        for v in variants:
            if not config.WEATHER_API_KEY:
                break
            try:
                r = await asyncio.to_thread(
                    requests.get,
                    "https://api.openweathermap.org/geo/1.0/direct",
                    params={"q": v, "limit": 5, "appid": config.WEATHER_API_KEY},
                    timeout=20,
                )
                arr = r.json()
            except Exception:
                arr = []
            if arr:
                item = arr[0]
                country_code = (item.get("country") or "").upper()
                res = [{
                    "latitude": float(item["lat"]),
                    "longitude": float(item["lon"]),
                    "name": item.get("local_names", {}).get("ru") or item.get("name") or v,
                    "country": item.get("country") or "",
                    "country_code": country_code.lower(),
                }]
                break
            if res:
                break
        # 2) запасной геокодер Nominatim
        if not res:
            for v in variants:
                try:
                    r2 = await asyncio.to_thread(requests.get,
                                      "https://nominatim.openstreetmap.org/search",
                                      params={"q": v, "format": "json", "limit": 1, "accept-language": "ru"},
                                      headers={"User-Agent": "DM-bot"}, timeout=20)
                    arr = r2.json()
                except Exception:
                    arr = []
                if arr:
                    a = arr[0]
                    disp = a.get("display_name", v).split(",")
                    res = [{"latitude": float(a["lat"]), "longitude": float(a["lon"]),
                            "name": disp[0].strip(), "country": disp[-1].strip(), "country_code": ""}]
                    break
        if not res:
            store.pending_input[str(cid)] = "setcity"
            msg = weather_ui.city_not_found(raw)
            await bot.send_message(chat_id=cid, text=msg.text)
            return
        c = res[0]
        country = c.get("country", "")
        cc = c.get("country_code", "")
        city_name = c["name"]
        try:
            hint = f" ({country})" if country else ""
            ru = await ai.allm(
                f"Как правильно пишется название города «{city_name}»{hint} на русском языке, "
                "как в Википедии? Ответь ТОЛЬКО названием города, без пояснений.",
                40, 0.1, tier="cheap", route="cf"
            )
            ru = ru.strip().strip("«»\"'.").split("\n")[0].strip()
            if ru and len(ru) <= 80 and not any(ch.isdigit() for ch in ru):
                city_name = ru
        except Exception:
            pass
        store.set_settings(cid, c["latitude"], c["longitude"], city_name, country, cc)
        try:
            import myday
            myday.reset_day_cache(cid)
        except Exception:
            pass
        msg = weather_ui.city_changed(city_name, country)
        await bot.send_message(chat_id=cid, text=msg.text)
        # сразу показываем обновлённую сводку "Мой день" (не во время онбординга)
        if show_brief:
            try:
                import myday
                await myday.send_plany(bot, cid)
            except Exception:
                pass
    except Exception as e:
        await verify.safe_error(bot, cid, e)

async def location_handler(update, context):
    cid = update.effective_chat.id
    loc = update.message.location
    city, country = "твой город", ""
    try:
        r = await asyncio.to_thread(requests.get,
                         "https://api.bigdatacloud.net/data/reverse-geocode-client",
                         params={"latitude": loc.latitude, "longitude": loc.longitude, "localityLanguage": "ru"},
                         timeout=15)
        j = r.json()
        city = j.get("city") or j.get("locality") or j.get("principalSubdivision") or "твой город"
        country = j.get("countryName", "")
        cc = j.get("countryCode", "")
    except Exception as e:
        _log.warning("location_handler: reverse geocode failed: %s", e)
        cc = ""
    store.set_settings(cid, loc.latitude, loc.longitude, city, country, cc)
    try:
        import myday
        myday.reset_day_cache(cid)
    except Exception:
        pass
    msg = weather_ui.location_changed(city, country)
    await update.message.reply_text(msg.text)
    try:
        await send_weather(context.bot, cid, "today")
    except Exception:
        pass
