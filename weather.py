import random
from datetime import datetime, timedelta
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
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
        "hourly": "precipitation_probability,windspeed_10m,temperature_2m",
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

    if mode == "full":
        dt = now
        header = f"Полный прогноз на сегодня • {_WEEKDAYS[dt.weekday()]}, {dt.day} {_MONTHS[dt.month-1]} • {s['city']}"
        try:
            hours = data["hourly"]["time"]
            temps = data["hourly"].get("temperature_2m") or []
            probs = data["hourly"]["precipitation_probability"]
            winds = data["hourly"]["windspeed_10m"]
        except Exception:
            temps = probs = winds = []
        day_str = d["time"][0]
        L = [f"<b>{esc(header)}</b>", ""]
        parts = [("Утром", 6, 12), ("Днём", 12, 18), ("Вечером", 18, 24)]
        for label, h1, h2 in parts:
            t_vals, p_vals, w_vals, code_v = [], [], [], 1
            for i, ts in enumerate(hours):
                if ts.startswith(day_str) and h1 <= int(ts[11:13]) < h2:
                    if i < len(temps): t_vals.append(temps[i] or 0)
                    if i < len(probs): p_vals.append(probs[i] or 0)
                    if i < len(winds): w_vals.append(winds[i] or 0)
            if not t_vals:
                continue
            tmx = max(t_vals); rn = max(p_vals) if p_vals else 0; wd = max(w_vals) if w_vals else 0
            icon = weather_icon(d["weathercode"][0], tmx, rn, wd)
            wemoji, wword = wind_scale(wd)
            wind_str = f"{wemoji} {wword} {wd:.0f} м/с" if wd >= 8 else f"💨 Ветер {wd:.0f} м/с"
            L += [f"<b>{label}:</b>", f"{icon} До {tmx:+.0f}°C • Дождь {rn:.0f}% • {wind_str}", ""]
        joke = _joke_outfit(s["city"], d["temperature_2m_max"][0], d["precipitation_probability_max"][0] or 0,
                            d["windspeed_10m_max"][0] or 0, DESC.get(d["weathercode"][0], ""), "сегодня")
        if joke:
            L.append(esc(joke))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="m_close")]])
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
        wind_ms = d["windspeed_10m_max"][day] or 0
        icon = weather_icon(code, tmax, rain, wind_ms)
        wemoji, wword = wind_scale(wind_ms)
        day_str = d["time"][day]
        rain_p = _periods(data, day_str, "precipitation_probability", 40)
        rain_when = (" (" + ", ".join(rain_p) + ")") if rain_p else ""
        wind_str = f"{wemoji} {wword} {wind_ms:.0f} м/с" if wind_ms >= 8 else f"💨 Ветер {wind_ms:.0f} м/с"

        L = [f"<b>{esc(header)}</b>", "",
             f"{icon} До {tmax:+.0f}°C • Дождь{rain_when} {rain:.0f}% • {wind_str}"]
        fact = _world_fact()
        if fact:
            L += ["", esc(fact)]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="m_close")]])
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
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
    days_ru = [_WEEKDAYS[(now + timedelta(days=i)).weekday()] for i in range(7)]
    short = {"Понедельник": "Пн", "Вторник": "Вт", "Среда": "Ср", "Четверг": "Чт",
             "Пятница": "Пт", "Суббота": "Сб", "Воскресенье": "Вс"}
    per_day = "; ".join(f"{short[days_ru[i]]}: {tmaxs[i]:.0f}°C, дождь {int(rains[i] or 0)}%, ветер {winds[i]:.0f}"
                        for i in range(7))
    tmin, tmax = min(tmins), max(tmaxs)
    flag = __import__("util").flag_from_cc(s.get("cc", ""))
    try:
        body = ai.llm(
            f"Сводка погоды на неделю, город {s['city']}. По дням: {per_day}.\n"
            f"Дни недели сокращай как Пн, Вт, Ср, Чт, Пт, Сб, Вс. Группируй диапазонами (например 'Ср - Сб').\n"
            f"СТРОГО такой формат, без markdown, каждая строка отдельно:\n"
            f"☀️ {{диапазон дней}}: лучшая погода\n"
            f"🌧️ {{диапазон дней}}: возможны дожди\n"
            f"🔥 {{диапазон дней}}: пик жары (ТОЛЬКО если есть жаркие дни, иначе пропусти строку)\n"
            f"💨 {{ветер: слабый/умеренный/сильный}}\n"
            f"Итог: {{1 предложение, тёплый вывод}}", 350, 0.8)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    L = [f"<b>Ближайшая неделя • {esc(rng)} • {esc(s['city'])} {flag}</b>", "",
         f"<b>Температура {tmin:+.0f}°C → {tmax:+.0f}°C</b>", ""]
    for ln in body.splitlines():
        t = ln.strip()
        if not t:
            continue
        if t.lower().startswith("итог"):
            L.append(f"<b>{esc(t)}</b>")
        else:
            L.append(esc(t))
            L.append("")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="m_close")]])
    await bot.send_message(chat_id=cid, text="\n".join(L).strip(), parse_mode="HTML", reply_markup=kb)


# ---------- смена города ----------
async def set_city_text(bot, cid, name):
    import re as _re
    # нормализация: убрать пробелы вокруг тире, лишние пробелы
    q = _re.sub(r"\s*-\s*", "-", (name or "").strip())
    q = _re.sub(r"\s+", " ", q)
    try:
        res = None
        for lang in ("ru", "en"):
            r = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": q, "count": 1, "language": lang}, timeout=20)
            res = r.json().get("results")
            if res:
                break
        # запасной геокодер
        if not res:
            r2 = requests.get("https://nominatim.openstreetmap.org/search",
                              params={"q": q, "format": "json", "limit": 1, "accept-language": "ru"},
                              headers={"User-Agent": "DM-bot"}, timeout=20)
            arr = r2.json()
            if arr:
                a = arr[0]
                disp = a.get("display_name", q).split(",")
                res = [{"latitude": float(a["lat"]), "longitude": float(a["lon"]),
                        "name": disp[0].strip(), "country": disp[-1].strip(), "country_code": ""}]
        if not res:
            store.pending_input[str(cid)] = "setcity"
            await bot.send_message(chat_id=cid, text=f"😕 Не нашёл город: {name}.\n\n🌍 Напиши название города ещё раз - исправив ошибки!")
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
        await bot.send_message(chat_id=cid, text=f"Готово. Город переключён на {c['name']}"
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