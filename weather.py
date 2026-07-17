import asyncio
import logging
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from copy import deepcopy
import time
import requests

_log = logging.getLogger(__name__)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import api_usage
import config
import store
import ai
from util import cap_sentence, _WEEKDAYS, _WEEKDAY_SHORT, _MONTHS
import verify
from ui import weather as weather_ui
import weather_provider as _provider

fetch_weather = _provider.fetch_weather
fetch_current_temp = _provider.fetch_current_temp
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
_owm_weathercode = _provider._owm_weathercode
_owm_iso = _provider._owm_iso
_owm_precip_mm = _provider._owm_precip_mm

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


def _week_overview(days):
    """Короткий итог по дневной погоде без ночных минимумов."""
    low = min(day["tmax"] for day in days)
    high = max(day["tmax"] for day in days)
    wet = sum(day["rain_real"] for day in days)
    clear = sum(day["code"] in (0, 1) and not day["rain_real"] for day in days)
    cloudy = sum(day["code"] in (3, 45, 48) for day in days)
    snow = sum(day["code"] in SNOW_CODES for day in days)
    max_wind = max(day["wind"] for day in days)
    avg_wind = sum(day["wind"] for day in days) / len(days)

    if snow:
        icon, description = "❄️", "Временами снег"
    elif wet >= 4:
        icon, description = "🌧️", "Часто дождь"
    elif wet >= 2:
        icon, description = "🌦️", "Переменная облачность, временами дождь"
    elif clear >= 5:
        icon, description = "☀️", "В основном ясно"
    elif clear >= 3:
        icon, description = "🌤️", "В основном малооблачно"
    elif cloudy >= 4:
        icon, description = "☁️", "В основном облачно"
    else:
        icon, description = "🌤️", "Переменная облачность"

    if max_wind >= 11:
        description += ", сильный ветер"
    elif max_wind >= 8:
        description += ", временами ветрено"
    elif avg_wind >= 5:
        description += ", умеренный ветер"
    return f"{icon} {low:+.0f}…{high:.0f}°C · {description}"


def _week_advice(days):
    """Одно практическое предложение, привязанное к реальному прогнозу."""
    strong_wind = [day for day in days if day["wind"] >= STRONG_WIND_MS]
    rainy = [day for day in days if day["rain_real"]]
    outdoor = [day for day in days if not day["rain_real"] and day["wind"] < STRONG_WIND_MS]

    if strong_wind and all(day in strong_wind for day in days[-2:]):
        return "В конце недели ожидается усиление ветра"
    if len(strong_wind) >= 3:
        return "Для велосипеда выбирай дни без сильного ветра"
    if len(rainy) >= 4:
        return "Для прогулок выбирай сухие окна между дождями"
    if rainy and len(outdoor) >= 2:
        best = sorted(outdoor, key=lambda day: (-day["tmax"], day["wind"]))[:2]
        labels = " и ".join(day["name"] for day in sorted(best, key=lambda day: day["index"]))
        return f"Лучшие дни для отдыха на улице — {labels}"
    if min(day["tmin"] for day in days) <= 12:
        return "Возьми лёгкую куртку — утром и вечером будет прохладно"
    if len(outdoor) >= 5:
        return "Можно спокойно планировать прогулки, велосипед и поездки"
    hottest = max(days, key=lambda day: day["tmax"])
    return f"Самый тёплый день — {hottest['name']}"


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


# ---------- отправка ----------
async def send_weather(bot, cid, mode="today"):
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
            t_vals, p_vals, w_vals, mm_vals = [], [], [], []
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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="a_plany"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]])
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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="a_plany"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]])
        msg = weather_ui.day_forecast(header, main_lines, alert=alert, fact_title=fact_title, fact=fact)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return

    if mode == "tomorrow_plain":
        day = 1
        dt = now + timedelta(days=1)
        header = (
            f"Завтра · {_WEEKDAY_SHORT[dt.weekday()]}, "
            f"{dt.day} {_MONTHS[dt.month-1]} · {s['city']} 📍"
        )
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
        main_lines = _weather_main_lines(
            icon, tmax, rain, rain_mm, rain_when, wind_ms, plain_wind=True,
        )
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
                    150, 0.6, tier="cheap", module="weather",
                    fallback_allowed=True, privacy_level="public", response_mode="plain_text"
                ).strip()
                if summary:
                    fact = _finish_sentence(cap_sentence(summary))
            except Exception:
                pass
        msg = weather_ui.day_forecast(header, main_lines, alert=alert, fact_title="Метео-итог", fact=fact)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
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
        tmax = d["temperature_2m_max"][idx]
        tmin = d["temperature_2m_min"][idx]
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
        rng = f"{d1.day}–{d2.day} {_MONTHS[d1.month-1]}"
    else:
        rng = f"{d1.day} {_MONTHS[d1.month-1]} – {d2.day} {_MONTHS[d2.month-1]}"
    overview = _week_overview(day_data)
    advice = _week_advice(day_data)

    kb = None if week_plain else InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="a_plany"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]])
    msg = weather_ui.week_forecast(rng, s["city"], overview, day_data, advice)
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
        await verify.safe_error(bot, cid, e, back="m_myday")

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
