import asyncio
import logging
import random
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import sun
import ai
from util import cap_sentence, flag_from_cc, _MONTHS, _WEEKDAYS, _WEEKDAY_SHORT
from ui import weather as weather_ui
import weather_provider as _provider
from weather_weekly import (
    qualitative_outlook as _qualitative_outlook,
    week_overview as _week_overview,
)

_log = logging.getLogger(__name__)

fetch_weather = _provider.fetch_weather
fetch_current_temp = _provider.fetch_current_temp
fetch_current_conditions = _provider.fetch_current_conditions
get_weather_usage = _provider.get_weather_usage
WeatherDailyLimitExceeded = _provider.WeatherDailyLimitExceeded
WEATHER_LIMIT_FALLBACK = _provider.WEATHER_LIMIT_FALLBACK
_WX_CACHE = _provider._WX_CACHE
_WX_TTL = _provider._WX_TTL
_WX_STALE_TTL = _provider._WX_STALE_TTL
_weather_cache_key = _provider._weather_cache_key
_onecall_get = _provider._onecall_get
_usage_key = _provider._usage_key
_usage_mutate = _provider._usage_mutate
_adapt_openweather = _provider._adapt_openweather
_first_data_item = _provider._first_data_item
_owm_iso = _provider._owm_iso

TZ = config.TZ

# Порог вероятности дождя: ниже - дождя нет (эмодзи и проценты не показываем)
RAIN_PROB_MIN = 50
# Минимум реальных осадков (мм) для подтверждения дождя при высокой вероятности
RAIN_MM_MIN = 0.1
# Сильный дождь/ливень: мм осадков за сутки (или пик за час в дневном окне)
HEAVY_RAIN_MM_DAY = 4.0
HEAVY_RAIN_MM_HOUR = 2.0
# Сильный ветер (м/с)
STRONG_WIND_MS = 8
# Дневное окно «когда пользователь обычно выходит из дома» (часы)
DAYTIME_START_H = 8
DAYTIME_END_H = 20
_MONTHS_SHORT = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

DESC = {0: "ясно", 1: "малооблачно", 2: "переменно облачно", 3: "пасмурно", 45: "туман", 48: "туман",
        51: "морось", 53: "морось", 55: "морось", 61: "дождь", 63: "дождь", 65: "сильный дождь",
        71: "снег", 73: "снег", 75: "сильный снег", 80: "ливень", 81: "ливень", 95: "гроза"}
RAIN_WEATHER_CODES = {51, 53, 55, 61, 63, 65, 80, 81, 82}


def current_precipitation_text(code):
    if code in (51, 53, 55):
        return "Морось сейчас"
    if code in (61, 63):
        return "Дождь сейчас"
    if code == 65:
        return "Сильный дождь сейчас"
    if code in (80, 81, 82):
        return "Ливень сейчас"
    if code in (95, 96, 99):
        return "Гроза сейчас"
    if code in (71, 73, 75, 77, 85, 86):
        return "Снег сейчас"
    return ""


# Кеш прогноза: один общий ответ OpenWeatherMap на myday/wardrobe/weather в пределах TTL.
# Каждое обновление - это 3 вызова One Call API (current/hourly/daily), но реальный
# расход всё равно на порядок ниже бесплатного потолка 1000/день - есть запас на более
# частое обновление ради точности текущих условий.
# ---------- ветер ----------


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


# ---------- иконка ----------
# Средняя облачность (%) по часам: ниже SUNNY — солнечно, ниже PARTLY — малооблачно.
SUNNY_CLOUDS_MAX = 30
PARTLY_CLOUDS_MAX = 70
_SKY_WORDS = {"sunny": "солнечно", "partly": "малооблачно", "cloudy": "облачно"}


def _sky(clouds, code):
    """Небо по почасовой облачности; без неё — по коду погоды."""
    if clouds is not None:
        return "sunny" if clouds < SUNNY_CLOUDS_MAX else "partly" if clouds < PARTLY_CLOUDS_MAX else "cloudy"
    return "sunny" if code in (0, 1) else "partly" if code == 2 else "cloudy"


def sky_word(clouds, code):
    return _SKY_WORDS[_sky(clouds, code)]


def weather_icon(code, temp, rain, wind_ms=0, rain_mm=None, clouds=None):
    # Суточный код «дождь/гроза/снег» ставится, даже если капнет только ночью:
    # осадочные значки — только когда осадки реальны в показанном окне.
    wet = _rain_real(rain, rain_mm)
    if wet and code in (95, 96, 99):
        return "🌩️"
    if wet and code in SNOW_CODES:
        return "❄️"
    if temp is not None and temp > 30 and wet:
        return "☀️🌧️"
    if wet:
        return "🌧️"
    if wind_ms >= 8:
        return "💨"
    if temp is not None and temp >= 30:
        return "🥵"
    sky = _sky(clouds, code)
    if sky == "sunny" or (clouds is None and temp is not None and temp >= 28):
        return "☀️"
    return "🌤️" if sky == "partly" else "☁️"


def _week_icon(code, temp, rain, wind_ms=0, rain_mm=None, clouds=None):
    """Одна иконка преобладающей погоды дня без составных эмодзи."""
    wet = _rain_real(rain, rain_mm)
    if wet and code in (95, 96, 99):
        return "🌩️"
    if wet and code in SNOW_CODES:
        return "❄️"
    if wet:
        return "🌧️"
    if code in (45, 48):
        return "🌫️"
    if clouds is not None:
        return {"sunny": "☀️", "partly": "🌤️"}.get(_sky(clouds, code), "☁️")
    if code == 0:
        return "☀️"
    if code in (1, 2):
        return "🌤️"
    if code in (45, 48):
        return "🌫️"
    return "☁️"


# ---------- периоды по часам ----------
def _periods(data, day_str, key, threshold):
    try:
        hours = data["hourly"]["time"]
        vals = data["hourly"][key]
    except Exception:
        return []
    buckets = {"утром": (DAYTIME_START_H, 12), "днём": (12, 18), "вечером": (18, DAYTIME_END_H)}
    hit = []
    for name, (h1, h2) in buckets.items():
        for t, v in zip(hours, vals):
            if t.startswith(day_str) and h1 <= int(t[11:13]) < h2 and (v or 0) >= threshold:
                hit.append(name)
                break
    return [p for p in ["утром", "днём", "вечером"] if p in hit]


def _join_periods(periods):
    periods = list(periods or [])
    if len(periods) > 1:
        return ", ".join(periods[:-1]) + " и " + periods[-1]
    return "".join(periods)


def _compact_forecast_line(icon, tmax, rain, rain_mm, rain_periods, wind_ms):
    """Единая короткая строка прогноза для уведомления и экрана «на завтра»."""
    parts = [f"до {tmax:+.0f}°C"]
    if _rain_real(rain, rain_mm):
        when = _join_periods(rain_periods)
        parts.append(f"Дождь {when}".rstrip())
    wind_label = "Сильный ветер" if float(wind_ms or 0) > 10 else "Ветер"
    parts.append(f"{wind_label} до {float(wind_ms or 0):.0f} м/с")
    return f"{icon} Погода: " + " · ".join(parts)


def _daytime_values(data, day_str, key):
    """Значения hourly-показателя в дневном окне DAYTIME_START_H..DAYTIME_END_H."""
    try:
        hours = data["hourly"]["time"]
        vals = data["hourly"][key]
    except (KeyError, TypeError):
        return []
    return [v for t, v in zip(hours, vals)
            if t.startswith(day_str)
            and DAYTIME_START_H <= int(t[11:13]) < DAYTIME_END_H
            and v is not None]


def _daytime_max(data, day_str, key):
    """Максимум hourly-показателя в дневном окне DAYTIME_START_H..DAYTIME_END_H."""
    day_vals = _daytime_values(data, day_str, key)
    return max(day_vals) if day_vals else None


def _mean(values):
    return sum(values) / len(values) if values else None


def day_conditions(data, idx):
    """Погода дня d["time"][idx] для недели и «Завтра…» — по окну 08–20, как и температура.

    Почасовые данные берутся, только если покрывают всё окно (у OpenWeather часы есть
    на ~48 ч); иначе — суточный агрегат провайдера целиком, без смешивания источников.
    """
    d = data["daily"]
    day_str = d["time"][idx]
    code = d["weathercode"][idx]
    temps = _daytime_values(data, day_str, "temperature_2m")
    if len(temps) >= DAYTIME_END_H - DAYTIME_START_H:
        tmin, tmax = min(temps), max(temps)
        rain = max(_daytime_values(data, day_str, "precipitation_probability"), default=0)
        rain_mm = sum(_daytime_values(data, day_str, "precipitation"))
        wind = max(_daytime_values(data, day_str, "windspeed_10m"), default=0)
        clouds = _mean(_daytime_values(data, day_str, "cloudcover"))
    else:
        tmin, tmax = d["temperature_2m_min"][idx], d["temperature_2m_max"][idx]
        rain = d["precipitation_probability_max"][idx] or 0
        rain_mm = (d.get("precipitation_sum") or [None] * (idx + 1))[idx]
        wind = d["windspeed_10m_max"][idx] or 0
        clouds = None
    return {
        "day_str": day_str, "code": code, "tmin": tmin, "tmax": tmax,
        "rain": rain, "rain_mm": rain_mm, "rain_real": _rain_real(rain, rain_mm),
        "wind": wind, "clouds": clouds, "sky": _sky(clouds, code),
    }


def _daytime_temperature_range(data, day_str, fallback_min=None, fallback_max=None):
    """Температуры, которые видит пользователь с 08:00 до 20:00.

    Почасовой горизонт короче недельного, поэтому для дальних дней остаётся
    дневной прогноз провайдера, но ночной минимум нигде не показывается.
    """
    try:
        hours = data["hourly"]["time"]
        temperatures = data["hourly"].get("temperature_2m") or []
    except (KeyError, TypeError):
        hours = temperatures = []
    values = [value for stamp, value in zip(hours, temperatures)
              if stamp.startswith(day_str)
              and DAYTIME_START_H <= int(stamp[11:13]) < DAYTIME_END_H
              and value is not None]
    if values:
        return min(values), max(values)
    return fallback_min, fallback_max


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
    ("Кувейте", 29.37, 47.98), ("Дубае", 25.20, 55.27), ("Дели", 28.61, 77.21),
    ("Антарктиде", -75.25, 0.07), ("Оймяконе", 63.46, 142.79), ("Долине Смерти", 36.46, -116.87),
    ("Рейкьявике", 64.15, -21.94), ("Сингапуре", 1.35, 103.82), ("Каире", 30.04, 31.24),
    ("Манаусе", -3.12, -60.02), ("Йеллоунайфе", 62.45, -114.37), ("Алис-Спрингсе", -23.70, 133.88),
    ("Улан-Баторе", 47.89, 106.91), ("Атакаме", -24.5, -69.25), ("Шпицбергене", 78.22, 15.63),
]

def _world_fact():
    pts = random.sample(WORLD_POINTS, 4)
    readings = []
    for name, lat, lon in pts:
        t = fetch_current_temp(lat, lon)
        if t is not None:
            readings.append((name, t))
    if not readings:
        return ""
    name, t = max(readings, key=lambda x: abs(x[1]))
    try:
        line = ai.llm(
            f"Сейчас в {name} {t:+.0f}°C (реальные данные). Напиши ОДНУ фразу, начни СТРОГО со слов "
            f"«Кстати, сегодня в {name} ...», с лёгким юмором, на русском, 1 предложение, без markdown.",
            120, 1.05, tier="cheap", fallback_allowed=True,
            privacy_level="public", response_mode="plain_text").strip().splitlines()[0]
    except Exception:
        line = f"Кстати, сегодня в {name} около {t:+.0f}°C."
    return line

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


def _speed_range(values):
    rounded = [int(round(float(value or 0))) for value in values]
    if not rounded:
        return "0"
    low, high = min(rounded), max(rounded)
    return str(high) if low == high else f"{low}–{high}"


def _clock(value, tz=None):
    """«07:12» в часовом поясе tz (в отпуске — места поездки); без tz — TZ."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ""
    # Провайдер отдаёт наивное время уже в TZ; astimezone() считал бы его временем сервера (UTC на VM).
    parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)
    return parsed.astimezone(tz or TZ).strftime("%H:%M")


def _period_weather_icon(label, code, temp, rain, wind_ms=0, rain_mm=None, clouds=None):
    icon = weather_icon(code, temp, rain, wind_ms, rain_mm, clouds)
    return "🌙" if label == "Ночью" and icon in ("☀️", "🌤️") else icon


def _full_forecast_parts(now):
    """Текущий (по оставшимся часам) и следующие дневные периоды без ночного блока."""
    parts = [("Утром", 8, 12), ("Днём", 12, 18), ("Вечером", 18, 24)]
    return [(label, start, end) for label, start, end in parts if end > now.hour]


def forecast_periods(data, now, day=0):
    """Периоды полного прогноза — единый источник для экрана прогноза, строки «Моего дня» и прогноза на завтра.

    Сегодня (day=0) периоды считаются только по часам с текущего (Open-Meteo отдаёт и прошедшие),
    плюс «Ночью» — 00–08 следующего дня. Другой день — утро, день и вечер целиком.
    """
    d = data.get("daily") or {}
    days = d.get("time") or []
    hourly = data.get("hourly") or {}
    hours = hourly.get("time") or []
    if len(days) <= day:
        return []
    if day:
        parts = [(label, start, end, days[day], day) for label, start, end in _full_forecast_parts(now.replace(hour=0))]
    else:
        parts = [(label, max(start, now.hour), end, days[0], 0) for label, start, end in _full_forecast_parts(now)]
        if len(days) > 1:
            parts.append(("Ночью", 0, 8, days[1], 1))

    def column(key, rows):
        values = hourly.get(key) or []
        return [values[i] for i in rows if i < len(values) and values[i] is not None]

    periods = []
    for label, start, end, day, index in parts:
        rows = [i for i, ts in enumerate(hours) if ts.startswith(day) and start <= int(ts[11:13]) < end]
        temps = column("temperature_2m", rows)
        if not temps:
            continue
        winds = column("windspeed_10m", rows)
        rain = max(column("precipitation_probability", rows), default=0)
        rain_mm = sum(float(value) for value in column("precipitation", rows))
        clouds = _mean(column("cloudcover", rows))
        code = (d.get("weathercode") or [None] * (index + 1))[index]
        tmax = max(temps)
        periods.append({
            "label": label, "tmax": tmax, "rain": rain, "rain_mm": rain_mm,
            "winds": winds, "clouds": clouds, "code": code,
            "icon": _period_weather_icon(label, code, tmax, rain, max(winds, default=0), rain_mm, clouds),
        })
    return periods


def _period_lines(period) -> list[str]:
    """Блок части суток в прогнозе на завтра: «🌧️ Днём», ниже «· Температура до +18°C», «· Дождь 70%»,
    «· Ветер 3–5 м/с» — каждое с новой строки."""
    rain = f"Дождь {period['rain']:.0f}%" if _rain_real(period["rain"], period["rain_mm"]) else "Без дождя"
    wind = "Сильный ветер" if max(period["winds"], default=0) > 10 else "Ветер"
    return [f"{period['icon']} {period['label']}", f"· Температура до {period['tmax']:+.0f}°C", f"· {rain}",
            f"· {wind} {_speed_range(period['winds'])} м/с"]


def today_conditions(data, now):
    """Погода сегодняшнего дня ровно как в «Моём дне»: дневное окно, оставшаяся часть дня, осадки сейчас —
    {tmax, tmin, rain (реальный дождь), wind, code, clouds, icon}; нет данных — None."""
    d = (data or {}).get("daily") or {}
    if not d.get("time"):
        return None
    day_str, code = d["time"][0], d["weathercode"][0]
    tmin, tmax = _daytime_temperature_range(data, day_str, d["temperature_2m_min"][0], d["temperature_2m_max"][0])
    rain_mm_day = (d.get("precipitation_sum") or [None])[0] if d.get("precipitation_sum") else None
    wind_ms = d["windspeed_10m_max"][0] or 0
    daytime = daytime_outfit_weather(data, day_str, tmax, wind_ms, d["precipitation_probability_max"][0] or 0,
                                     rain_mm_day, code)
    rain, rain_mm = daytime["rain_prob"], daytime["rain_mm"]
    wind = daytime.get("wind_ms") or wind_ms
    clouds = _mean(_daytime_values(data, day_str, "cloudcover"))
    outlook = today_outlook(data, now)
    if outlook:
        tmax, rain, rain_mm = outlook["tmax"], outlook["rain"], outlook["rain_mm"]
        wind, clouds = outlook["wind_ms"], outlook["clouds"]
    current_code = (data.get("current") or {}).get("weathercode")
    raining_now = bool(current_precipitation_text(current_code))
    icon = (weather_icon(current_code, tmax, 100, wind) if raining_now
            else weather_icon(code, tmax, rain, wind, rain_mm, clouds))
    return {"tmax": tmax, "tmin": tmin, "rain": raining_now or _rain_real(rain, rain_mm), "wind": wind,
            "code": code, "clouds": clouds, "icon": icon}


def today_outlook(data, now):
    """Оставшаяся часть сегодняшнего дня из тех же периодов, что и «Полный прогноз»; None — нет часов."""
    periods = [p for p in forecast_periods(data, now) if p["label"] != "Ночью"]
    if not periods:
        return None
    wet = [p for p in periods if _rain_real(p["rain"], p["rain_mm"])]
    current = periods[0]
    return {
        "tmax": max(p["tmax"] for p in periods),
        # Небо и ветер — текущего периода: строка совпадает с его блоком в «Полном прогнозе».
        "wind_ms": max(current["winds"], default=0),
        # Дождь — по всем оставшимся периодам, где он реален: иконка и текст не расходятся.
        "rain": max((p["rain"] for p in wet), default=0),
        "rain_mm": sum(p["rain_mm"] for p in wet),
        "rain_periods": [p["label"].lower() for p in wet],
        "clouds": current["clouds"],
    }


def current_period_label(now):
    """Текущий период дня («Утром / Днём / Вечером»), с которым сверяется строка погоды «Моего дня»."""
    parts = _full_forecast_parts(now)
    return parts[0][0] if parts else "Ночью"


# ---------- отправка ----------
async def send_weather(bot, cid, mode="today", status=None, reply_markup=None):
    s = store.get_settings(cid)
    try:
        # fetch_weather синхронно ходит в сеть: не блокируем event loop бота.
        data = await asyncio.to_thread(fetch_weather, s["lat"], s["lon"], 9)
    except WeatherDailyLimitExceeded:
        await bot.send_message(chat_id=cid, text=WEATHER_LIMIT_FALLBACK)
        return
    except Exception as error:
        # API погоды недоступен и свежего кэша нет: понятный текст вместо общей ошибки.
        _log.warning("send_weather: forecast unavailable mode=%s cid=%s: %r", mode, cid, error, exc_info=True)
        msg = weather_ui.forecast_unavailable()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                               reply_markup=InlineKeyboardMarkup([nav_row("m_myday")]))
        return
    d = data["daily"]
    now = datetime.now(TZ)

    if mode == "full":
        dt = now
        cc = str(s.get("cc") or "").upper()
        flag = flag_from_cc(cc) or ""
        place = f"{s['city']}{f', {cc}' if cc else ''}{f' {flag}' if flag else ''}"
        header = f"Полный прогноз • {_WEEKDAY_SHORT[dt.weekday()]}, {dt.day} {_MONTHS[dt.month-1]} · {place}"
        periods = [
            {
                "title": f"{p['icon']} {p['label']}",
                "lines": [
                    f"Температура до {p['tmax']:+.0f}°C",
                    f"Ветер {_speed_range(p['winds'])} м/с",
                    f"Дождь {p['rain']:.0f}%",
                ],
            }
            for p in forecast_periods(data, dt)
        ]
        import vacation
        local = await asyncio.to_thread(vacation.local_tz, cid)  # в отпуске — время места, не Амстердама
        sunrise = _clock((d.get("sunrise") or [""])[0], local)
        sunset = _clock((d.get("sunset") or [""])[0], local)
        sunrise_line = (
            f"Восход {sunrise} → Закат {sunset}" if sunrise and sunset
            else f"Восход {sunrise}" if sunrise
            else f"Закат {sunset}" if sunset
            else ""
        )
        # Вечерний золотой час — по координатам города, без внешних API; идёт в «Полезно».
        golden = sun.golden_hour_line(s.get("lat"), s.get("lon"), datetime.now(local).date(), local)
        sunset_line = ""
        advice = _qualitative_outlook([day_conditions(data, 1)], "Завтра")
        if golden:
            # Золотой час — последним предложением строки «Полезно».
            advice = str(advice or "").strip()
            if advice and advice[-1] not in ".!?…":
                advice += "."
            advice = f"{advice} {golden}.".strip()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(weather_ui.WEEK_FORECAST_BUTTON, callback_data="a_w_week")],
            nav_row("m_myday"),
        ])
        msg = weather_ui.full_forecast(
            header, None, periods, sunrise_line, sunset_line, advice,
        )
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return

    if mode in ("today", "tomorrow"):
        day = 0 if mode == "today" else 1
        dt = now + timedelta(days=day)
        title = "сегодня" if mode == "today" else "завтра"
        flag = flag_from_cc(s.get("cc", "")) or ""
        header = f"Погода на {title} • {_WEEKDAYS[dt.weekday()]}, {dt.day} {_MONTHS_SHORT[dt.month-1]} • {s['city']} {flag}"
        code = d["weathercode"][day]
        _tmin, tmax = _daytime_temperature_range(
            data, day_str := d["time"][day], d["temperature_2m_min"][day], d["temperature_2m_max"][day],
        )
        day_weather = daytime_outfit_weather(
            data, day_str, tmax, d["windspeed_10m_max"][day] or 0,
            d["precipitation_probability_max"][day] or 0,
            (d.get("precipitation_sum") or [None] * (day + 1))[day] if d.get("precipitation_sum") else None,
            code,
        )
        rain = day_weather["rain_prob"]
        rain_mm = day_weather["rain_mm"]
        wind_ms = day_weather["wind_ms"] or 0
        icon = weather_icon(code, tmax, rain, wind_ms, rain_mm,
                            _mean(_daytime_values(data, day_str, "cloudcover")))
        rain_p = _periods(data, day_str, "precipitation_probability", RAIN_PROB_MIN)
        main_lines = [_compact_forecast_line(icon, tmax, rain, rain_mm, rain_p, wind_ms)]
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
            fact = await asyncio.to_thread(_world_fact)
        kb = InlineKeyboardMarkup([nav_row("m_myday")])
        msg = weather_ui.day_forecast(header, main_lines, alert=alert, fact_title=fact_title, fact=fact)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return

    if mode == "tomorrow_plain":
        day = 1
        dt = now + timedelta(days=1)
        cc = str(s.get("cc") or "").upper()
        flag = flag_from_cc(cc) or ""
        place = f"{s['city']}{f', {cc}' if cc else ''}{f' {flag}' if flag else ''}"  # «Alkmaar, NL 🇳🇱»
        header = f"Завтра · {_WEEKDAY_SHORT[dt.weekday()]}, {dt.day} {_MONTHS_SHORT[dt.month-1]} · {place}"
        code = d["weathercode"][day]
        _tmin, tmax = _daytime_temperature_range(
            data, day_str := d["time"][day], d["temperature_2m_min"][day], d["temperature_2m_max"][day],
        )
        day_weather = daytime_outfit_weather(
            data, day_str, tmax, d["windspeed_10m_max"][day] or 0,
            d["precipitation_probability_max"][day] or 0,
            (d.get("precipitation_sum") or [None] * (day + 1))[day] if d.get("precipitation_sum") else None,
            code,
        )
        rain = day_weather["rain_prob"]
        rain_mm = day_weather["rain_mm"]
        wind_ms = day_weather["wind_ms"] or 0
        icon = weather_icon(code, tmax, rain, wind_ms, rain_mm,
                            _mean(_daytime_values(data, day_str, "cloudcover")))
        rain_p = _periods(data, day_str, "precipitation_probability", RAIN_PROB_MIN)
        alert = storm_alert(wind_ms, code, rain, rain_mm, cc=cc)
        # Утро, день и вечер — блоками через пустую строку ("" в списке — отступ); без почасовых данных — одна
        # общая строка. Метео-итога нет.
        blocks = [_period_lines(p) for p in forecast_periods(data, now, day=1)]
        main_lines = [line for index, block in enumerate(blocks) for line in ([""] if index else []) + block] \
            or [_compact_forecast_line(icon, tmax, rain, rain_mm, rain_p, wind_ms)]
        msg = weather_ui.day_forecast(header, main_lines, alert=alert)
        await bot.send_message(
            chat_id=cid,
            text=msg.text,
            entities=msg.entities,
            reply_markup=reply_markup,
        )
        return

    # week/week_plain: семь фактических дневных строк без группировки и повторов
    week_plain = mode == "week_plain"
    _SKIP = 1

    # Сбор данных для 7 дней
    day_data = []
    for i in range(7):
        idx = _SKIP + i
        if idx >= len(d["weathercode"]):
            break
        day = day_conditions(data, idx)
        if day["tmax"] is None or day["tmin"] is None:
            continue
        dt_i = datetime.fromisoformat(day["day_str"])
        day_data.append({
            **day,
            "index": i,
            "abbrev": _WEEKDAY_SHORT[dt_i.weekday()],
            "name": _WEEKDAYS[dt_i.weekday()].lower(),
            "date": dt_i,
            "icon": _week_icon(day["code"], day["tmax"], day["rain"], day["wind"], day["rain_mm"], day["clouds"]),
        })

    if len(day_data) != 7:
        raise ValueError("weather API returned incomplete weekly forecast")
    d1, d2 = day_data[0]["date"], day_data[-1]["date"]
    if d1.month == d2.month:
        rng = f"{d1.day}–{d2.day} {_MONTHS_SHORT[d1.month-1]}"
    else:
        rng = f"{d1.day} {_MONTHS_SHORT[d1.month-1]} – {d2.day} {_MONTHS_SHORT[d2.month-1]}"
    overview = _week_overview(day_data)
    advice = _qualitative_outlook(day_data)

    kb = None if week_plain else InlineKeyboardMarkup([nav_row("m_myday")])
    msg = weather_ui.week_forecast(
        rng, s["city"], overview, day_data, advice,
        country=s.get("country", ""), country_code=s.get("cc", ""),
    )
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
        return
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


from weather_location import (  # noqa: E402 — after definitions weather_location uses
    set_city_text,
    location_handler,
)
from ui.navigation import nav_row
