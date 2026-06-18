import random
from datetime import datetime
import requests
import config
import store
import ai

TZ = config.TZ

DESC = {0: "ясно", 1: "малооблачно", 2: "переменно", 3: "пасмурно", 45: "туман", 48: "туман",
        51: "морось", 53: "морось", 55: "морось", 61: "дождь", 63: "дождь", 65: "сильный дождь",
        71: "снег", 73: "снег", 75: "сильный снег", 80: "ливень", 81: "ливень", 95: "гроза"}


def fetch_weather(lat, lon, days=2):
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weathercode",
        "hourly": "precipitation_probability",
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


# ---------- ветер (шкала) ----------
def wind_scale(ms):
    if ms < 3:
        return "🌬️", "почти без ветра"
    if ms < 5:
        return "💨", "слабый ветер"
    if ms < 8:
        return "🌪️", "умеренно сильный ветер"
    if ms < 11:
        return "⚠️", "сильный ветер"
    return "🚨", "очень сильный ветер"

# для совместимости (myday)
def wind_note(ms):
    return wind_scale(ms)[1]


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


# ---------- период дождя по часам ----------
def rain_periods(data, day_index=0):
    try:
        hours = data["hourly"]["time"]
        probs = data["hourly"]["precipitation_probability"]
    except Exception:
        return []
    target = data["daily"]["time"][day_index]
    buckets = {"утром": (6, 12), "днём": (12, 18), "вечером": (18, 24), "ночью": (0, 6)}
    hit = []
    for name, (h1, h2) in buckets.items():
        for t, p in zip(hours, probs):
            if not t.startswith(target):
                continue
            hh = int(t[11:13])
            if h1 <= hh < h2 and (p or 0) >= 40:
                hit.append(name)
                break
    return hit


# ---------- блок погоды (используется в myday) ----------
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


# ---------- мировой факт + концовка (свежие, из реальных температур) ----------
WORLD_POINTS = [
    ("🇰🇼", "Кувейт", 29.37, 47.98), ("🇦🇪", "Дубай", 25.20, 55.27), ("🇮🇳", "Дели", 28.61, 77.21),
    ("🇦🇶", "Антарктида", -75.25, 0.07), ("🇷🇺", "Оймякон", 63.46, 142.79), ("🇺🇸", "Долина Смерти", 36.46, -116.87),
    ("🇮🇸", "Рейкьявик", 64.15, -21.94), ("🇸🇬", "Сингапур", 1.35, 103.82), ("🇪🇬", "Каир", 30.04, 31.24),
    ("🇧🇷", "Манаус", -3.12, -60.02), ("🇨🇦", "Йеллоунайф", 62.45, -114.37), ("🇦🇺", "Алис-Спрингс", -23.70, 133.88),
    ("🇲🇳", "Улан-Батор", 47.89, 106.91), ("🇨🇱", "Атакама", -24.5, -69.25), ("🇳🇴", "Шпицберген", 78.22, 15.63),
]

def _world_fact_and_closer(city):
    # берём 4 случайные точки, тянем реальную температуру сейчас
    pts = random.sample(WORLD_POINTS, 4)
    readings = []
    for flag, name, lat, lon in pts:
        t = fetch_current_temp(lat, lon)
        if t is not None:
            readings.append((flag, name, t))
    fact = ""
    if readings:
        flag, name, t = max(readings, key=lambda x: abs(x[2]))
        try:
            fact = ai.llm(
                f"Сейчас в {name} {t:+.0f}°C (реальные данные). Напиши ОДНУ короткую необычную фразу-факт про эту погоду "
                f"на русском, начни строку с эмодзи {flag} НЕ ставь флаг перед страной. 1 предложение, с лёгким юмором, без markdown.",
                120, 1.0).strip().splitlines()[0]
        except Exception:
            fact = f"{flag} Сейчас в {name} около {t:+.0f}°C."
    try:
        closer = ai.llm(
            f"Придумай ОДНУ дерзкую и тёплую фразу-концовку прогноза погоды для города {city}, на русском, "
            f"каждый раз новую, 1 предложение, без markdown.", 100, 1.1).strip().splitlines()[0]
    except Exception:
        closer = f"Сегодня {city} явно выиграл погодную лотерею."
    return fact, closer


# ---------- отправка прогноза ----------
async def send_weather(bot, cid, days):
    s = store.get_settings(cid)
    country = s.get("country", "")
    place = f"{country}, {s['city']}" if country else s["city"]
    data = fetch_weather(s["lat"], s["lon"], max(days, 2))
    d = data["daily"]
    names = ["Сегодня", "Завтра"]
    out = []

    if days == 1:
        out.append(f"📍 {place} • прогноз на сегодня")
        out.append("")
        code = d["weathercode"][0]
        tmin, tmax = d["temperature_2m_min"][0], d["temperature_2m_max"][0]
        rain = d["precipitation_probability_max"][0] or 0
        wind_ms = d["windspeed_10m_max"][0] or 0
        icon = weather_icon(code, tmax, rain, wind_ms)
        wemoji, wword = wind_scale(wind_ms)
        out.append(f"{icon} Сегодня")
        if rain >= 30:
            periods = rain_periods(data, 0)
            when = (" (" + ", ".join(periods) + ")") if periods else ""
            out.append(f"{tmin:.0f}…{tmax:.0f}°C • 🌧️ Вероятность дождя{when} {rain:.0f}%")
        else:
            out.append(f"{tmin:.0f}…{tmax:.0f}°C • ☁️ Вероятность дождя {rain:.0f}%")
        out.append(f"{wemoji} {wind_ms:.1f} м/с ({wword})")
        # факт + концовка из реальных данных
        fact, closer = _world_fact_and_closer(s["city"])
        if fact:
            out += ["", fact]
        if closer:
            out += ["", closer]
    else:
        out.append(f"📍 {place} • прогноз на 3 дня")
        out.append("")
        for i in range(min(days, 3)):
            if i < 2:
                label = names[i]
            else:
                dt = datetime.fromisoformat(d["time"][i])
                from util import _WEEKDAYS
                label = f"{_WEEKDAYS[dt.weekday()][:2]}, {dt.day} {['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'][dt.month-1]}"
            code = d["weathercode"][i]
            tmin, tmax = d["temperature_2m_min"][i], d["temperature_2m_max"][i]
            rain = d["precipitation_probability_max"][i] or 0
            wind_ms = d["windspeed_10m_max"][i] or 0
            icon = weather_icon(code, tmax, rain, wind_ms)
            wemoji, wword = wind_scale(wind_ms)
            out.append(f"{icon} {label}")
            out.append(f"{tmin:.0f}…{tmax:.0f}°C • 🌧️ {rain:.0f}%")
            out.append(f"{wemoji} {wind_ms:.1f} м/с ({wword})")
            out.append("")

    await bot.send_message(chat_id=cid, text="\n".join(out).strip())


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
        store.set_settings(cid, c["latitude"], c["longitude"], c["name"], country)
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
    city, country = "", ""
    try:
        r = requests.get("https://api.bigdatacloud.net/data/reverse-geocode-client",
                         params={"latitude": loc.latitude, "longitude": loc.longitude, "localityLanguage": "ru"},
                         timeout=15)
        j = r.json()
        city = j.get("city") or j.get("locality") or j.get("principalSubdivision") or "твой город"
        country = j.get("countryName", "")
    except Exception:
        city = "твой город"
    store.set_settings(cid, loc.latitude, loc.longitude, city, country)
    await update.message.reply_text(f"Готово. Ты находишься в городе {city}" + (f", {country}." if country else "."))
    try:
        await send_weather(context.bot, cid, 1)
    except Exception:
        pass