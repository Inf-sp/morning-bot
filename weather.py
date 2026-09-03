import asyncio
import logging
import random
from datetime import datetime, timedelta
import requests

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
from util import cap_sentence, _MONTHS, _WEEKDAYS, _WEEKDAY_SHORT
import verify
from ui import weather as weather_ui
import weather_provider as _provider
from weather_weekly import (
    qualitative_outlook as _qualitative_outlook,
    week_advice as _weekly_advice,
    week_overview as _week_overview,
)
from module_binding import bind_functions as _bind_functions
import weather_location as _weather_location

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
# Сильный ветер (м/с): согласовано с wind_scale («Сильный ветер» начинается с 8)
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
def wind_scale(ms):
    if ms < 3:
        return "🌬️", "Почти без ветра"
    if ms < 5:
        return "💨", "Лёгкий ветер"
    if ms < 8:
        return "🌪️", "Умеренный ветер"
    if ms < 11:
        return "⚠️", "Сильный ветер"
    return "⚠️", "Очень сильный ветер"


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


def _rain_description(rain, rain_mm, periods=()):
    """Короткая фактическая фраза для AI-сводки без неинициализированных данных."""
    if not _rain_real(rain, rain_mm):
        return "без осадков"
    when = ", ".join(str(period).strip() for period in (periods or []) if str(period).strip())
    return f"дождь {rain:.0f}%" + (f" {when}" if when else "")


def _weather_main_lines(
    icon, tmax, rain, rain_mm, rain_when, wind_ms, *, plain_wind=False,
):
    rain_part = rain_text(rain, rain_mm, rain_when)
    wemoji, wword = wind_scale(wind_ms)
    if plain_wind:
        classification = wword.lower()
        if classification.endswith(" ветер"):
            classification = classification[:-6]
        wind_str = f"Ветер {wind_ms:.0f} м/с · {classification}"
    else:
        wind_str = f"{wemoji} {wword} {wind_ms:.0f} м/с" if wind_ms >= 8 else f"💨 Ветер {wind_ms:.0f} м/с"

    first = f"{icon} До {tmax:+.0f}°C"
    if rain_part:
        first += f" • {rain_part}"
    if wind_ms >= 8 and not plain_wind:
        return [first, "", wind_str]
    return [f"{first} • {wind_str}"]


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
        if t.startswith(day_str) and DAYTIME_START_H <= int(t[11:13]) < DAYTIME_END_H and v is not None
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
    wet = code in RAIN_WEATHER_CODES or _rain_real(rain, rain_mm)
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


def _week_icon(code, temp, rain, wind_ms=0, rain_mm=None):
    """Одна иконка преобладающей погоды дня без составных эмодзи."""
    if code in (95, 96, 99):
        return "🌩️"
    if code in (71, 73, 75, 77, 85, 86):
        return "❄️"
    if _rain_real(rain, rain_mm):
        return "🌧️"
    if code == 0:
        return "☀️"
    if code in (1, 2):
        return "🌤️"
    if code in (45, 48):
        return "🌫️"
    return "☁️"


def _week_advice(days):
    return _weekly_advice(days, STRONG_WIND_MS)


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

def _joke_outfit(city, tmax, rain, wind_ms, desc, when="сегодня"):
    try:
        return ai.llm(
            f"Город {city}, {when}: {desc}, до {tmax:+.0f}°C, дождь {rain:.0f}%, ветер {wind_ms:.0f} м/с. "
            f"Напиши ОДНУ дерзкую дружелюбную фразу + короткий совет по одежде (нужна ли куртка/зонт). "
            f"1 предложение, на русском, без markdown.", 120, 1.05, tier="cheap",
            fallback_allowed=True, privacy_level="public", response_mode="plain_text").strip().splitlines()[0]
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


def _wind_direction(value):
    try:
        degrees = float(value) % 360
    except (TypeError, ValueError):
        return ""
    labels = ("северный", "северо-восточный", "восточный", "юго-восточный",
              "южный", "юго-западный", "западный", "северо-западный")
    return labels[int((degrees + 22.5) // 45) % 8]


def _speed_range(values):
    rounded = [int(round(float(value or 0))) for value in values]
    if not rounded:
        return "0"
    low, high = min(rounded), max(rounded)
    return str(high) if low == high else f"{low}–{high}"


def _clock(value):
    try:
        return datetime.fromisoformat(str(value)).astimezone(TZ).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def _period_weather_icon(label, code, temp, rain, wind_ms=0, rain_mm=None):
    # Код погоды приходит суточным агрегатом и может означать дождь в другом
    # периоде. При нулевой вероятности в этом периоде не показываем дождь.
    if not float(rain or 0):
        return "☁️"
    icon = weather_icon(code, temp, rain, wind_ms, rain_mm)
    return "🌙" if label == "Ночью" and icon in ("☀️", "🌤️") else icon


def _full_forecast_parts(now):
    """Возвращает только ещё актуальные дневные периоды без ночного блока.

    После 17:00 блок «Днём» не показывается — пользователь уже ориентируется
    на погоду вечером.
    """
    hour = int(now.hour)
    parts = [("Утром", 8, 12), ("Днём", 12, 18), ("Вечером", 18, 24)]
    if hour < 8:
        return parts
    return [
        (label, max(start, hour), end)
        for label, start, end in parts
        if hour < end and not (label == "Днём" and hour >= 17)
    ]


# ---------- отправка ----------
async def send_weather(bot, cid, mode="today", status=None, reply_markup=None):
    s = store.get_settings(cid)
    try:
        data = fetch_weather(s["lat"], s["lon"], 9)
    except WeatherDailyLimitExceeded:
        await bot.send_message(chat_id=cid, text=WEATHER_LIMIT_FALLBACK)
        return
    d = data["daily"]
    now = datetime.now(TZ)

    if mode == "full":
        dt = now
        cc = str(s.get("cc") or "").upper()
        flag = __import__("util").flag_from_cc(cc) or ""
        place = f"{s['city']}{f', {cc}' if cc else ''}{f' {flag}' if flag else ''}"
        header = f"Полный прогноз • {_WEEKDAY_SHORT[dt.weekday()]}, {dt.day} {_MONTHS[dt.month-1]} · {place}"
        hourly = data.get("hourly") or {}
        try:
            hours = hourly["time"]
            temps = hourly.get("temperature_2m") or []
            probs = hourly.get("precipitation_probability") or []
            precs = hourly.get("precipitation") or []
            winds = hourly.get("windspeed_10m") or []
        except Exception:
            hours = temps = probs = precs = winds = []
        day_str = d["time"][0]
        periods = []
        parts = [(label, h1, h2, day_str, 0) for label, h1, h2 in _full_forecast_parts(dt)]
        if len(d.get("time") or []) > 1:
            parts.append(("Ночью", 0, 8, d["time"][1], 1))
        for label, h1, h2, period_day, daily_index in parts:
            t_vals, p_vals, w_vals, mm_vals = [], [], [], []
            for i, ts in enumerate(hours):
                if ts.startswith(period_day) and h1 <= int(ts[11:13]) < h2:
                    if i < len(temps): t_vals.append(temps[i] or 0)
                    if i < len(probs): p_vals.append(probs[i] or 0)
                    if i < len(winds): w_vals.append(winds[i] or 0)
                    if i < len(precs): mm_vals.append(precs[i] or 0)
            if not t_vals:
                continue
            tmx = max(t_vals); rn = max(p_vals) if p_vals else 0; wd = max(w_vals) if w_vals else 0
            mm = sum(float(value or 0) for value in mm_vals)
            icon = _period_weather_icon(
                label, d["weathercode"][daily_index], tmx, rn, wd, mm,
            )
            lines = [
                f"Температура до {tmx:+.0f}°C",
                f"Ветер {_speed_range(w_vals)} м/с",
            ]
            lines.append(f"Дождь {rn:.0f}%")
            periods.append({
                "title": f"{icon} {label}",
                "lines": lines,
            })
        _tmin, daytime_tmax = _daytime_temperature_range(
            data, day_str, d["temperature_2m_min"][0], d["temperature_2m_max"][0],
        )
        sunrise = _clock((d.get("sunrise") or [""])[0])
        sunset = _clock((d.get("sunset") or [""])[0])
        sunrise_line = (
            f"Восход {sunrise} → Закат {sunset}" if sunrise and sunset
            else f"Восход {sunrise}" if sunrise
            else f"Закат {sunset}" if sunset
            else ""
        )
        sunset_line = ""
        tomorrow = {
            "code": d["weathercode"][1],
            "tmax": d["temperature_2m_max"][1],
            "rain_real": _rain_real(
                d["precipitation_probability_max"][1] or 0,
                (d.get("precipitation_sum") or [None, None])[1],
            ),
            "wind": d["windspeed_10m_max"][1] or 0,
        }
        advice = _qualitative_outlook([tomorrow], "Завтра")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(weather_ui.WEEK_FORECAST_BUTTON, callback_data="a_w_week")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="m_myday"),
             InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
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
        flag = __import__("util").flag_from_cc(s.get("cc", "")) or ""
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
        icon = weather_icon(code, tmax, rain, wind_ms, rain_mm)
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
            fact = _world_fact()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="m_myday"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])
        msg = weather_ui.day_forecast(header, main_lines, alert=alert, fact_title=fact_title, fact=fact)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return

    if mode == "tomorrow_plain":
        day = 1
        dt = now + timedelta(days=1)
        header = (
            f"Завтра · {_WEEKDAY_SHORT[dt.weekday()]}, "
            f"{dt.day} {_MONTHS_SHORT[dt.month-1]} · {s['city']} 📍"
        )
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
        icon = weather_icon(code, tmax, rain, wind_ms, rain_mm)
        rain_p = _periods(data, day_str, "precipitation_probability", RAIN_PROB_MIN)
        desc = DESC.get(code, "")
        cc = s.get("cc", "")
        alert = storm_alert(wind_ms, code, rain, rain_mm, cc=cc)
        main_lines = [_compact_forecast_line(icon, tmax, rain, rain_mm, rain_p, wind_ms)]
        fact = ""
        if alert:
            pass
        else:
            try:
                rain_desc = _rain_description(rain, rain_mm, rain_p)
                summary = await ai.allm(
                    f"Погода завтра в {s['city']}: {desc}, до {tmax:+.0f}°C, {rain_desc}, "
                    f"ветер {wind_ms:.0f} м/с.\n\n"
                    "Напиши короткий метео-итог: 2-3 предложения — общая картина, что ждать. "
                    "Без слова 'зонт'. Без markdown. На русском.",
                    150, 0.6, tier="cheap", module="weather",
                    fallback_allowed=True, privacy_level="public", response_mode="plain_text"
                ).strip()
                if summary:
                    fact = _finish_sentence(cap_sentence(summary))
            except Exception:
                pass
        msg = weather_ui.day_forecast(header, main_lines, alert=alert, fact_title="Метео-итог", fact=fact)
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
        day_str = d["time"][idx]
        dt_i = datetime.fromisoformat(day_str)
        code = d["weathercode"][idx]
        tmin, tmax = _daytime_temperature_range(
            data, day_str, d["temperature_2m_min"][idx], d["temperature_2m_max"][idx],
        )
        if tmax is None or tmin is None:
            continue
        rain = d["precipitation_probability_max"][idx] or 0
        rain_mm = (d.get("precipitation_sum") or [None] * 10)[idx]
        wind_max = d["windspeed_10m_max"][idx] or 0
        day_data.append({
            "index": i,
            "abbrev": _WEEKDAY_SHORT[dt_i.weekday()],
            "name": _WEEKDAYS[dt_i.weekday()].lower(),
            "date": dt_i,
            "icon": _week_icon(code, tmax, rain, wind_max, rain_mm),
            "tmax": tmax,
            "tmin": tmin,
            "code": code,
            "rain": rain,
            "rain_mm": rain_mm,
            "rain_real": _rain_real(rain, rain_mm),
            "wind": wind_max,
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

    kb = None if week_plain else InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="m_myday"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])
    msg = weather_ui.week_forecast(
        rng, s["city"], overview, day_data, advice,
        country=s.get("country", ""), country_code=s.get("cc", ""),
    )
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
        return
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


_bind_functions(globals(), _weather_location, ["set_city_text", "location_handler"])
