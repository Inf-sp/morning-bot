import asyncio
import logging
import random
from datetime import datetime, timedelta
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

DESC = {0: "ясно", 1: "малооблачно", 2: "переменно облачно", 3: "пасмурно", 45: "туман", 48: "туман",
        51: "морось", 53: "морось", 55: "морось", 61: "дождь", 63: "дождь", 65: "сильный дождь",
        71: "снег", 73: "снег", 75: "сильный снег", 80: "ливень", 81: "ливень", 95: "гроза"}


# Кеш прогноза: один общий ответ open-meteo на myday/wardrobe/weather в пределах TTL
_WX_CACHE = {}          # (lat2, lon2, days) -> (ts, json)
_WX_TTL = 600           # сек

def fetch_weather(lat, lon, days=2):
    import time
    days = max(days, 2)
    key = (round(lat, 2), round(lon, 2), days)
    hit = _WX_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _WX_TTL:
        return hit[1]
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weathercode",
        "hourly": "precipitation_probability,precipitation,windspeed_10m,temperature_2m,relativehumidity_2m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,weathercode,windspeed_10m_max,winddirection_10m_dominant",
        "timezone": "Europe/Amsterdam", "wind_speed_unit": "ms", "forecast_days": days
    }, timeout=20)
    r.raise_for_status()
    data = r.json()
    _WX_CACHE[key] = (time.time(), data)
    return data

def fetch_current_temp(lat, lon):
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": lat, "longitude": lon, "current": "temperature_2m"}, timeout=15)
        return r.json()["current"]["temperature_2m"]
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
    """Реальный метео-рекорд из Open-Meteo Archive + LLM-нарративизация."""
    import research as _r
    if lat is None or lon is None:
        return ""
    records = _r.weather_records(lat, lon, tz=tz, years=10)
    if not records:
        return ""
    # выбираем факт релевантный текущей погоде
    if tmax >= 28 and "heat" in records:
        raw = records["heat"]
    elif rain >= 50 and "rain" in records:
        raw = records["rain"]
    elif tmax < 5 and "cold" in records:
        raw = records["cold"]
    else:
        raw = random.choice(list(records.values()))
    # Добавляем живой нарратив через LLM
    try:
        narrative = ai.llm(
            f"Реальный метеорекорд для города {city}: «{raw}»\n"
            f"Завтра: {desc}, до {tmax:+.0f}°C, дождь {rain:.0f}%.\n"
            "Перепиши факт в одно живое, ироничное предложение — как будто рассказываешь другу. "
            "Сохрани цифры и дату. Без markdown, на русском. "
            "Пример хорошего: «В июне 2024-го здесь лило как из ведра — 39 мм за день, рекорд за 10 лет.»",
            150, 1.05, tier="cheap"
        ).strip().splitlines()[0]
        return narrative
    except Exception:
        return raw


# ---------- отправка ----------
async def send_weather(bot, cid, mode="today"):
    s = store.get_settings(cid)
    country = s.get("country", "")
    place = f"{s['city']}, {country}" if country else s["city"]
    data = fetch_weather(s["lat"], s["lon"], 9)
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
        # 1) Open-Meteo geocoder: по вариантам и языкам
        for v in variants:
            for lang in ("ru", "en", "nl"):
                try:
                    r = await asyncio.to_thread(requests.get,
                                     "https://geocoding-api.open-meteo.com/v1/search",
                                     params={"name": v, "count": 5, "language": lang}, timeout=20)
                    results = r.json().get("results")
                except Exception:
                    results = None
                if results:
                    res = results[:1]
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
