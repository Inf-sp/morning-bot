"""Погода на часы, когда образ будут носить: от текущего часа до 22:00.

Сводит почасовой прогноз к тому, что важно для одежды: «ощущается как» в самый
холодный и самый тёплый час, когда начинается дождь, максимальные порывы и разброс
утро/день. Результат совместим со старым ``weather_ctx`` (tmax, has_rain, …) и
добавляет точные поля для жёстких правил (``wardrobe_rules``).
"""
from datetime import datetime, timedelta

WINDOW_START_H = 8
WINDOW_END_H = 22
RAIN_PROB = 50          # %, час считается дождливым
RAIN_MM = 0.3           # мм за час
STRONG_GUST = 10        # м/с — нужна непродуваемая верхняя одежда
STRONG_WIND = 8         # м/с — средний ветер
LAYERING_SPREAD = 7     # °C между самым холодным и тёплым часом — нужен снимаемый слой
LAYERING_COLD_BELOW = 18  # °C «ощущается» утром: теплее — слой уже не нужен

# Класс комфорта по «ощущается как» в самый холодный час окна.
COMFORT_CLASSES = ((8, "cold"), (14, "cool"), (19, "mild"), (24, "warm"))


def window_bounds(now):
    """(начало, конец) окна: с текущего часа до 22:00; до 08:00 — с 08:00; после 22:00 — завтра."""
    if now.hour >= WINDOW_END_H:
        day = now.date() + timedelta(days=1)
        return datetime(day.year, day.month, day.day, WINDOW_START_H), datetime(day.year, day.month, day.day, WINDOW_END_H)
    start_hour = max(now.hour, WINDOW_START_H)
    return (datetime(now.year, now.month, now.day, start_hour),
            datetime(now.year, now.month, now.day, WINDOW_END_H))


def comfort_class(feels_min, feels_max):
    if feels_min is None:
        return "mild"
    if feels_max is not None and feels_min >= 18 and feels_max >= 26:
        return "hot"
    for limit, name in COMFORT_CLASSES:
        if feels_min < limit:
            return name
    return "hot"


def wear_window(wdata, now, *, sunny=False):
    """dict погоды на окно или None, если почасового прогноза на это окно нет."""
    hourly = (wdata or {}).get("hourly") or {}
    times = hourly.get("time") or []
    start, end = window_bounds(now)

    def column(key, fallback=None):
        values = hourly.get(key) or hourly.get(fallback) or []
        return list(values) + [None] * (len(times) - len(values))

    temps = column("temperature_2m")
    feels = column("apparent_temperature", "temperature_2m")
    probs, mms = column("precipitation_probability"), column("precipitation")
    winds, gusts = column("windspeed_10m"), column("windgusts_10m", "windspeed_10m")
    rows = []
    for index, stamp in enumerate(times):
        try:
            moment = datetime.strptime(str(stamp)[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        if start <= moment < end and temps[index] is not None:
            rows.append({
                "hour": moment.hour,
                "temp": float(temps[index]),
                "feels": float(feels[index] if feels[index] is not None else temps[index]),
                "rain": (probs[index] or 0) >= RAIN_PROB or (mms[index] or 0) >= RAIN_MM,
                "wind": float(winds[index] or 0),
                "gust": float(gusts[index] or winds[index] or 0),
            })
    if not rows:
        return None
    feels_min = min(row["feels"] for row in rows)
    feels_max = max(row["feels"] for row in rows)
    temp_min = min(row["temp"] for row in rows)
    temp_max = max(row["temp"] for row in rows)
    rain_hours = [row["hour"] for row in rows if row["rain"]]
    gust_max = max(row["gust"] for row in rows)
    wind_max = max(row["wind"] for row in rows)
    comfort = comfort_class(feels_min, feels_max)
    has_rain = bool(rain_hours)
    strong_wind = gust_max >= STRONG_GUST or wind_max >= STRONG_WIND
    hot = temp_max >= 24
    tags = (["rain"] if has_rain else []) + (["strong_wind"] if strong_wind else [])
    tags.append("hot" if hot else "warm" if temp_max >= 17 else "cool")
    if sunny and not has_rain:
        tags.append("sunny")
    return {
        # Совместимость со старым weather_ctx.
        "tmin": round(temp_min), "tmax": round(temp_max), "has_rain": has_rain,
        "wind_ms": round(wind_max), "strong_wind": strong_wind,
        "sunny": bool(sunny and not has_rain and hot), "hot": hot, "warm": 17 <= temp_max < 24,
        "tags": tags,
        # Точные поля окна для правил и строки-причины.
        "window": (start.hour, end.hour), "feels_min": round(feels_min), "feels_max": round(feels_max),
        "rain_from": rain_hours[0] if rain_hours else None, "gust_max": round(gust_max),
        "comfort": comfort,
        "layering": feels_max - feels_min >= LAYERING_SPREAD and feels_min < LAYERING_COLD_BELOW,
    }
