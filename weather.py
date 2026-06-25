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
from util import esc, _WEEKDAYS, _MONTHS
import verify

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
        "hourly": "precipitation_probability,precipitation,windspeed_10m,temperature_2m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,weathercode,windspeed_10m_max",
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


def rain_text(rain, rain_mm=None, when=""):
    """Кусок строки про дождь. Пусто, если дождя по сути нет."""
    if rain and _rain_real(rain, rain_mm):
        return f"Дождь{when} {rain:.0f}% • "
    return ""


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
    if code in (0, 1):
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


# ---------- блок для myday ----------
def weather_block(data, day, city):
    d = data["daily"]
    code = d["weathercode"][day]
    desc = DESC.get(code, "")
    tmin, tmax = d["temperature_2m_min"][day], d["temperature_2m_max"][day]
    wind = d["windspeed_10m_max"][day]
    rain = d["precipitation_probability_max"][day]
    rain_mm = (d.get("precipitation_sum") or [None])[day] if d.get("precipitation_sum") else None
    lines = [f"📍 {city}", f"{desc}, {tmin:.0f}-{tmax:.0f}°C", f"💨 ветер до {wind:.0f} м/с"]
    if rain and _rain_real(rain, rain_mm):
        lines.append(f"вероятность дождя {rain:.0f}%")
    return "\n".join(lines)


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
    is_nl = (cc or "").upper() == "NL"
    L = ["⚠️ <b>Штормовое предупреждение</b>" + (" (Code Geel)" if is_nl else ""), ""]
    if "wind" in reasons:
        L.append(f"Ожидаются шквалы до {wind_ms:.0f} м/с. Закрепи велосипед, убери лёгкие предметы с балкона.")
        if is_nl:
            L.append("Высокий риск задержек и отмен поездов NS - ветки на путях парализуют движение. Проверь приложение NS.")
        else:
            L.append("Возможны задержки транспорта из-за ветра. Заложи время на дорогу.")
    if "rain" in reasons:
        if is_nl:
            L.append("Сильный дождь и риск подтоплений. Сверься с Buienradar перед выходом.")
        else:
            L.append("Сильный дождь и риск подтоплений. Проверь прогноз осадков перед выходом.")
    if "snow" in reasons:
        L.append("Снег и гололёд. Осторожно на дорогах, заложи время на дорогу.")
    return "\n".join(L)

def _meteo_fact(city, tmax, rain, wind_ms, desc, date_label="",
                country="", cc="", lat=None, lon=None, tz="UTC"):
    """Реальный метео-рекорд из Open-Meteo Archive — без LLM, без галлюцинаций."""
    import research as _r
    if lat is None or lon is None:
        return ""
    records = _r.weather_records(lat, lon, tz=tz, years=10)
    if not records:
        return ""
    # выбираем факт релевантный текущей погоде
    if tmax >= 28 and "heat" in records:
        return records["heat"]
    if rain >= 50 and "rain" in records:
        return records["rain"]
    if tmax < 5 and "cold" in records:
        return records["cold"]
    return random.choice(list(records.values()))


# ---------- отправка ----------
async def send_weather(bot, cid, mode="today"):
    s = store.get_settings(cid)
    country = s.get("country", "")
    place = f"{s['city']}, {country}" if country else s["city"]
    data = fetch_weather(s["lat"], s["lon"], 7)
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
        L = [f"<b>{esc(header)}</b>", ""]
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
            L += [f"<b>{label}:</b>", f"{icon} До {tmx:+.0f}°C • {rain_text(rn, mm)}{wind_str}", ""]
        joke = _joke_outfit(s["city"], d["temperature_2m_max"][0], d["precipitation_probability_max"][0] or 0,
                            d["windspeed_10m_max"][0] or 0, DESC.get(d["weathercode"][0], ""), "сегодня")
        if joke:
            L.append(esc(joke))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="a_plany")]])
        await bot.send_message(chat_id=cid, text="\n".join(L).strip(), parse_mode="HTML", reply_markup=kb)
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
        icon = weather_icon(code, tmax, rain, wind_ms, rain_mm)
        wemoji, wword = wind_scale(wind_ms)
        day_str = d["time"][day]
        rain_p = _periods(data, day_str, "precipitation_probability", RAIN_PROB_MIN)
        rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
        wind_str = f"{wemoji} {wword} {wind_ms:.0f} м/с" if wind_ms >= 8 else f"💨 Ветер {wind_ms:.0f} м/с"

        L = [f"<b>{esc(header)}</b>", "",
             f"{icon} До {tmax:+.0f}°C • {rain_text(rain, rain_mm, rain_when)}{wind_str}"]

        if mode == "tomorrow":
            desc = DESC.get(code, "")
            cc = s.get("cc", "")
            country = s.get("country", "")
            alert = storm_alert(wind_ms, code, rain, rain_mm, cc=cc)
            if alert:
                # экстремальная погода: показываем угрозу, метео-факт блокируется
                L += ["", alert]
            else:
                date_lbl = header.split("•")[1].strip() if "•" in header else ""
                mf = _meteo_fact(s["city"], tmax, rain, wind_ms, desc, date_lbl,
                                country=country, cc=cc,
                                lat=s["lat"], lon=s["lon"], tz=str(TZ))
                if mf:
                    L += ["", "🔬 <b>Метео-факт</b>", esc(mf)]
        else:
            fact = _world_fact()
            if fact:
                L += ["", esc(fact)]
        if mode == "tomorrow":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🗓️ Погода на неделю", callback_data="a_w_week")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="a_plany")],
            ])
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="a_plany")]])
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
        return

    # week
    d1 = now
    d2 = now + timedelta(days=6)
    if d1.month == d2.month:
        rng = f"{d1.day}–{d2.day} {_MONTHS[d1.month-1]}"
    else:
        rng = f"{d1.day} {_MONTHS[d1.month-1]} – {d2.day} {_MONTHS[d2.month-1]}"
    tmins = d["temperature_2m_min"][:7]
    tmaxs = d["temperature_2m_max"][:7]
    rains = d["precipitation_probability_max"][:7]
    rmms = (d.get("precipitation_sum") or [None] * 7)[:7]
    winds = d["windspeed_10m_max"][:7]
    codes = d["weathercode"][:7]
    flag = __import__("util").flag_from_cc(s.get("cc", ""))
    _SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    def _day_range_str(indices):
        if not indices:
            return ""
        shorts = [_SHORT[(now + timedelta(days=i)).weekday()] for i in indices]
        if len(shorts) == 1:
            return shorts[0]
        is_consec = all(indices[j+1] == indices[j]+1 for j in range(len(indices)-1))
        return f"{shorts[0]}–{shorts[-1]}" if is_consec else ", ".join(shorts)

    # раскладка дней по рубрикам; приоритет: шторм > жара > дождь > комфорт
    storm_i, hot_i, wet_i, comfort_i = [], [], [], []
    for i in range(7):
        t = tmaxs[i] or 0
        rp = rains[i] or 0
        mm = rmms[i]
        wd = winds[i] or 0
        code = codes[i]
        is_storm = (wd > STORM_WIND_MS) or (code in SNOW_CODES) or (code in HEAVY_RAIN_CODES) \
                   or (mm is not None and mm >= 15)
        if is_storm:
            storm_i.append(i)
        elif t > 25:
            hot_i.append(i)
        elif _rain_real(rp, mm):
            wet_i.append(i)
        else:
            comfort_i.append(i)

    def _gtmax(indices):
        return max(tmaxs[i] for i in indices) if indices else 0

    wmax = max(winds) if winds else 0
    wmin = min(winds) if winds else 0
    if wmax < 5:
        wlabel = "слабый"
    elif wmax < 10:
        wlabel = "умеренный"
    else:
        wlabel = "сильный"
    wind_line = f"💨 Ветер: {wlabel}, {wmin:.0f}–{wmax:.0f} м/с"

    L = [f"<b>Ближайшая неделя • {esc(rng)} • {esc(s['city'])} {flag}</b>", ""]
    if storm_i:
        L.append(f"⚠️ {esc(_day_range_str(storm_i))}: до {_gtmax(storm_i):+.0f}°C — шторм, осторожно")
    if comfort_i:
        L.append(f"☀️ {esc(_day_range_str(comfort_i))}: до {_gtmax(comfort_i):+.0f}°C — комфортно")
    if wet_i:
        L.append(f"🌧️ {esc(_day_range_str(wet_i))}: до {_gtmax(wet_i):+.0f}°C — возможны дожди")
    if hot_i:
        L.append(f"🔥 {esc(_day_range_str(hot_i))}: до {_gtmax(hot_i):+.0f}°C — жара, осторожно")
    L += ["", wind_line]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="a_plany")]])
    await bot.send_message(chat_id=cid, text="\n".join(L).strip(), parse_mode="HTML", reply_markup=kb)


# ---------- смена города ----------
async def set_city_text(bot, cid, name):
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
            await bot.send_message(chat_id=cid,
                text=f"😕 Не нашёл город: {raw}.\n\n🌍 Проверь написание и пришли название ещё раз.")
            return
        c = res[0]
        country = c.get("country", "")
        cc = c.get("country_code", "")
        store.set_settings(cid, c["latitude"], c["longitude"], c["name"], country, cc)
        try:
            import myday
            myday.reset_day_cache(cid)
        except Exception:
            pass
        await bot.send_message(chat_id=cid, text=f"✅ Готово. Город переключён на {c['name']}"
                                                 + (f", {country}." if country else "."))
        # сразу показываем обновлённую сводку "Мой день" под новую локацию
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
    await update.message.reply_text(f"Готово. Ты находишься в городе {city}" + (f", {country}." if country else "."))
    try:
        await send_weather(context.bot, cid, "today")
    except Exception:
        pass