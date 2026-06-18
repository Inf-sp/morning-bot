import random
from datetime import datetime, timedelta
import requests
import config
import store
import ai
from util import esc, _WEEKDAYS, _MONTHS

TZ = config.TZ

DESC = {0: "ясно", 1: "малооблачно", 2: "переменно облачно", 3: "пасмурно", 45: "туман", 48: "туман",
        51: "морось", 53: "морось", 55: "морось", 61: "дождь", 63: "дождь", 65: "сильный дождь",
        71: "снег", 73: "снег", 75: "сильный снег", 80: "ливень", 81: "ливень", 95: "гроза"}


def fetch_weather(lat, lon, days=2):
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weathercode",
        "hourly": "precipitation_probability,windspeed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode,windspeed_10m_max",
        "timezone": "Europe/Amsterdam", "wind_speed_unit": "ms", "forecast_days": max(days, 2)
    }, timeout=20)
    r.raise_for_status()
    return r.json()

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

def wind_note(ms):
    return wind_scale(ms)[1].lower()


# ---------- иконка ----------
def weather_icon(code, temp, rain, wind_ms=0):
    if code in (95, 96, 99):
        return "🌩️"
    if code in (71, 73, 75, 77, 85, 86):
        return "❄️"
    if temp is not None and temp > 30 and rain >= 30:
        return "☀️🌧️"
    if rain >= 30:
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
    lines = [f"📍 {city}", f"{desc}, {tmin:.0f}-{tmax:.0f}°C", f"💨 ветер до {wind:.0f} м/с"]
    if rain and rain >= 30:
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
            120, 1.05).strip().splitlines()[0]
    except Exception:
        line = f"Кстати, сегодня в {name} около {t:+.0f}°C."
    return f"{flag} {line}"

def _joke_outfit(city, tmax, rain, wind_ms, desc, when="сегодня"):
    try:
        return ai.llm(
            f"Город {city}, {when}: {desc}, до {tmax:+.0f}°C, дождь {rain:.0f}%, ветер {wind_ms:.0f} м/с. "
            f"Напиши ОДНУ дерзкую дружелюбную фразу + короткий совет по одежде (нужна ли куртка/зонт). "
            f"1 предложение, на русском, без markdown.", 120, 1.05).strip().splitlines()[0]
    except Exception:
        return f"Сегодня {city} явно выиграл погодную лотерею."


# ---------- отправка ----------
async def send_weather(bot, cid, mode="today"):
    s = store.get_settings(cid)
    country = s.get("country", "")
    place = f"{s['city']}, {country}" if country else s["city"]
    data = fetch_weather(s["lat"], s["lon"], 7)
    d = data["daily"]
    now = datetime.now(TZ)

    if mode in ("today", "tomorrow"):
        day = 0 if mode == "today" else 1
        dt = now + timedelta(days=day)
        title = "сегодня" if mode == "today" else "завтра"
        header = f"Погода на {title} • {_WEEKDAYS[dt.weekday()]}, {dt.day} {_MONTHS[dt.month-1]}"
        code = d["weathercode"][day]
        tmax = d["temperature_2m_max"][day]
        rain = d["precipitation_probability_max"][day] or 0
        wind_ms = d["windspeed_10m_max"][day] or 0
        icon = weather_icon(code, tmax, rain, wind_ms)
        wemoji, wword = wind_scale(wind_ms)
        day_str = d["time"][day]
        rain_p = _periods(data, day_str, "precipitation_probability", 40)
        wind_p = _periods(data, day_str, "windspeed_10m", 6)
        rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
        wind_when = (" (" + ", ".join(wind_p) + ")") if wind_p else ""

        L = [f"<b>{esc(header)}</b>", "",
             f"<b>🌡️ {esc(place)}</b>",
             f"{icon} {tmax:+.0f}°C • 🌧️ Дождь{rain_when} {rain:.0f}% • {wemoji} {wword}{wind_when} {wind_ms:.0f} м/с"]
        joke = _joke_outfit(s["city"], tmax, rain, wind_ms, DESC.get(code, ""), title)
        if joke:
            L += ["", esc(joke)]
        fact = _world_fact()
        if fact:
            L += ["", esc(fact)]
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
        return

    # week
    d1 = now
    d2 = now + timedelta(days=6)
    if d1.month == d2.month:
        rng = f"{d1.day} - {d2.day} {_MONTHS[d1.month-1]}"
    else:
        rng = f"{d1.day} {_MONTHS[d1.month-1]} - {d2.day} {_MONTHS[d2.month-1]}"
    tmins = d["temperature_2m_min"][:7]
    tmaxs = d["temperature_2m_max"][:7]
    rains = d["precipitation_probability_max"][:7]
    winds = d["windspeed_10m_max"][:7]
    summary_data = (f"мин {min(tmins):.0f}°C, макс {max(tmaxs):.0f}°C; "
                    f"дожди по дням %: {[int(x or 0) for x in rains]}; "
                    f"ветер {min(winds):.0f}-{max(winds):.0f} м/с")
    try:
        body = ai.llm(
            f"Сделай краткую сводку погоды на неделю для города {s['city']}. Данные: {summary_data}.\n"
            f"СТРОГО формат, без markdown, каждая строка с эмодзи:\n"
            f"🌤️ {{характер недели и диапазон температур}}\n"
            f"🌧️ {{про дожди, в какие части дня чаще}}\n"
            f"💨 {{ветер диапазон м/с}}\n"
            f"☁️ {{общая облачность}}\n\n"
            f"Лучшие дни: {{когда}}\n"
            f"{{одна строка про сложные дни}}", 500, 0.8)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    L = [f"<b>Ближайшая неделя • {esc(rng)}</b>", "", f"<b>🌡️ {esc(place)}</b>", esc(body)]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")


# ---------- смена города ----------
async def set_city_text(bot, cid, name):
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 1, "language": "ru"}, timeout=20)
        res = r.json().get("results")
        if not res:
            await bot.send_message(chat_id=cid, text=f"Не нашёл город: {name}. Попробуй иначе.")
            return
        c = res[0]
        country = c.get("country", "")
        cc = c.get("country_code", "")
        store.set_settings(cid, c["latitude"], c["longitude"], c["name"], country, cc)
        await bot.send_message(chat_id=cid, text=f"Готово. Ты находишься в городе {c['name']}"
                                                 + (f", {country}." if country else "."))
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")

async def setcity_command(update, context):
    if not context.args:
        await update.message.reply_text("Формат: /setcity Амстердам")
        return
    await set_city_text(context.bot, update.effective_chat.id, " ".join(context.args))

async def location_handler(update, context):
    cid = update.effective_chat.id
    loc = update.message.location
    city, country = "твой город", ""
    try:
        r = requests.get("https://api.bigdatacloud.net/data/reverse-geocode-client",
                         params={"latitude": loc.latitude, "longitude": loc.longitude, "localityLanguage": "ru"},
                         timeout=15)
        j = r.json()
        city = j.get("city") or j.get("locality") or j.get("principalSubdivision") or "твой город"
        country = j.get("countryName", "")
        cc = j.get("countryCode", "")
    except Exception:
        cc = ""
    store.set_settings(cid, loc.latitude, loc.longitude, city, country, cc)
    await update.message.reply_text(f"Готово. Ты находишься в городе {city}" + (f", {country}." if country else "."))
    try:
        await send_weather(context.bot, cid, "today")
    except Exception:
        pass