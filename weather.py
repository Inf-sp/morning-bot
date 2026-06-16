import random
from datetime import datetime
import requests
import config
import store
import ai

TZ = config.TZ

EMOJI = {0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️", 45: "🌫️", 48: "🌫️",
         51: "🌦️", 53: "🌦️", 55: "🌧️", 61: "🌦️", 63: "🌧️", 65: "🌧️",
         71: "🌨️", 73: "🌨️", 75: "❄️", 80: "🌧️", 81: "🌧️", 95: "⛈️"}
DESC = {0: "ясно", 1: "малооблачно", 2: "переменно", 3: "пасмурно", 45: "туман", 48: "туман",
        51: "морось", 53: "морось", 55: "морось", 61: "дождь", 63: "дождь", 65: "сильный дождь",
        71: "снег", 73: "снег", 75: "сильный снег", 80: "ливень", 81: "ливень", 95: "гроза"}

def fetch_weather(lat, lon, days=2):
    r = requests.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weathercode",
        "daily": "temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_probability_max,weathercode,windspeed_10m_max",
        "timezone": "Europe/Amsterdam", "wind_speed_unit": "ms", "forecast_days": days
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

def weather_icon(code, temp, rain, wind_kmh):
    if code in (95, 96, 99):
        return "🌩️"
    if code in (71, 73, 75, 77, 85, 86):
        return "❄️"
    if temp is not None and temp > 30 and rain >= 30:
        return "☀️🌧️"
    if rain >= 30:
        return "🌧️"
    if wind_kmh >= 40:
        return "💨"
    if code in (0, 1):
        return "☀️"
    return "☁️"

def wind_note(ms):
    if ms >= 11:
        return "очень сильный, некомфортно"
    if ms >= 8:
        return "сильный"
    if ms >= 5:
        return "заметный"
    return "слабый"

def weather_block(data, day, city):
    d = data["daily"]
    code = d["weathercode"][day]
    emoji = EMOJI.get(code, "🌡️")
    desc = DESC.get(code, "")
    tmin, tmax = d["temperature_2m_min"][day], d["temperature_2m_max"][day]
    wind = d["windspeed_10m_max"][day]
    rain = d["precipitation_probability_max"][day]
    lines = [f"📍 {city}", f"{emoji} {desc}, {tmin:.0f}-{tmax:.0f}°C", f"💨 ветер до {wind:.0f} м/с"]
    if rain and rain >= 30:
        lines.append(f"🌧️ дождь {rain:.0f}%")
    return "\n".join(lines)

EXTREME_POINTS = [
    ("🇰🇼", "Кувейте", 29.37, 47.98),
    ("🇦🇶", "Антарктиде", -78.46, 106.84),
    ("🇺🇸", "Долине Смерти", 36.46, -116.87),
    ("🇷🇺", "Оймяконе", 63.46, 142.79),
    ("🇮🇳", "Дели", 28.61, 77.21),
    ("🇦🇪", "Дубае", 25.20, 55.27),
]

def world_extreme_line():
    idx = datetime.now(TZ).timetuple().tm_yday
    pts = [EXTREME_POINTS[(idx + k) % len(EXTREME_POINTS)] for k in range(3)]
    readings = []
    for flag, name, lat, lon in pts:
        t = fetch_current_temp(lat, lon)
        if t is not None:
            readings.append((t, flag, name))
    if not readings:
        return None
    hot = max(readings, key=lambda x: x[0])
    cold = min(readings, key=lambda x: x[0])
    pick = hot if abs(hot[0]) >= abs(cold[0]) else cold
    t, flag, name = pick
    if t >= 45:
        return f"🔥 Аномальная жара: в {flag} {name} сейчас {t:+.0f}°C - асфальт почти плавится."
    if t >= 38:
        return f"🥵 В {flag} {name} сейчас {t:+.0f}°C - дышать тяжело."
    if t <= -40:
        return f"🥶 Экстремальный холод: в {flag} {name} {t:+.0f}°C - вдвое холоднее морозилки."
    if t <= -20:
        return f"❄️ В {flag} {name} сейчас {t:+.0f}°C."
    return f"🌍 В {flag} {name} сейчас {t:+.0f}°C."

CLOSERS = [
    "Сегодня {city} явно выиграл погодную лотерею.",
    "Хорошая новость: зонтик сегодня может отдохнуть.",
    "Погода для прогулки. Отмазка «плохая погода» не принимается.",
    "На улице комфортно. Даже велосипед не возражает.",
    "Сегодня погода дружелюбнее некоторых людей.",
]

async def send_weather(bot, cid, days):
    s = store.get_settings(cid)
    data = fetch_weather(s["lat"], s["lon"], max(days, 2))
    d = data["daily"]
    names = ["Сегодня", "Завтра"]
    out = []
    if days == 1:
        code = d["weathercode"][0]
        rain = d["precipitation_probability_max"][0] or 0
        wind_ms = d["windspeed_10m_max"][0] or 0
        icon = weather_icon(code, d["temperature_2m_max"][0], rain, wind_ms * 3.6)
        out += [f"📍 {s['city']}",
                f"{icon} {d['temperature_2m_min'][0]:.0f}-{d['temperature_2m_max'][0]:.0f}°C",
                f"Вероятность дождя {rain:.0f}%",
                f"💨 Ветер {wind_ms:.0f} м/с - {wind_note(wind_ms)}"]
        rubric = world_extreme_line()
        if rubric:
            out += ["", rubric]
        out += ["", random.choice(CLOSERS).format(city=s["city"])]
    else:
        out.append(f"📍 {s['city']} - прогноз на {days} дн.")
        out.append("")
        for i in range(days):
            label = names[i] if i < 2 else d["time"][i]
            code = d["weathercode"][i]
            rain = d["precipitation_probability_max"][i] or 0
            wind_kmh = (d["windspeed_10m_max"][i] or 0) * 3.6
            icon = weather_icon(code, d["temperature_2m_max"][i], rain, wind_kmh)
            out.append(f"{icon} {label}: {d['temperature_2m_min'][i]:.0f}-{d['temperature_2m_max'][i]:.0f}°C, "
                       f"дождь {rain:.0f}%, ветер {wind_kmh:.0f} км/ч")
    await bot.send_message(chat_id=cid, text="\n".join(out))

async def setcity_command(update, context):
    cid = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Формат: /setcity Амстердам")
        return
    name = " ".join(context.args)
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": name, "count": 1, "language": "ru"}, timeout=20)
        res = r.json().get("results")
        if not res:
            await update.message.reply_text(f"Не нашёл город: {name}")
            return
        c = res[0]
        store.set_settings(cid, c["latitude"], c["longitude"], c["name"])
        try:
            fact = ai.llm(f"Один короткий интересный факт про город {c['name']}. Одно предложение.", 120, 0.8).strip()
        except Exception:
            fact = ""
        msg = f"Готово. Ты находишься в городе {c['name']}."
        if fact:
            msg += f"\n\n💡 {fact}"
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def location_handler(update, context):
    cid = update.effective_chat.id
    loc = update.message.location
    city = "ваш город"
    try:
        r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                         params={"name": "", "count": 1, "language": "ru",
                                 "latitude": loc.latitude, "longitude": loc.longitude}, timeout=15)
        res = r.json().get("results")
        if res:
            city = res[0].get("name", city)
    except Exception:
        pass
    store.set_settings(cid, loc.latitude, loc.longitude, city)
    try:
        data = fetch_weather(loc.latitude, loc.longitude, 1)
        await update.message.reply_text(f"Готово. Ты находишься в городе {city}.\n\n" + weather_block(data, 0, city))
    except Exception as e:
        await update.message.reply_text(f"Локация сохранена. Ошибка погоды: {e}")
