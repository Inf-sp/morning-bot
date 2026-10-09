"""Золотой час по высоте солнца (формулы NOAA), без внешних API.

Золотой час — солнце между −4° и +6° над горизонтом, синий час — между −6° и −4°.
Точность — около минуты, этого достаточно для съёмки.
"""
import math
from datetime import date, datetime, time, timedelta, timezone

GOLDEN_HIGH = 6.0
GOLDEN_LOW = -4.0
BLUE_LOW = -6.0
SUNRISE = -0.833  # видимый край диска с учётом рефракции


def _solar_params(day):
    """(уравнение времени в минутах, склонение в радианах) на полдень дня."""
    gamma = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    return eqtime, decl


def crossing(lat, lon, day, elevation, tz, *, rising):
    """Момент, когда солнце проходит высоту elevation (утром — rising); None, если не бывает."""
    eqtime, decl = _solar_params(day)
    lat_r = math.radians(lat)
    cos_h = ((math.sin(math.radians(elevation)) - math.sin(lat_r) * math.sin(decl))
             / (math.cos(lat_r) * math.cos(decl)))
    if not -1 <= cos_h <= 1:
        return None
    hour_angle = math.degrees(math.acos(cos_h))
    noon = 720 - 4 * lon - eqtime
    minutes = noon - 4 * hour_angle if rising else noon + 4 * hour_angle
    utc = datetime.combine(day, time(0), tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return utc.astimezone(tz)


def light_windows(lat, lon, day, tz):
    """{"morning_golden": (от, до), "evening_golden": (от, до), "evening_blue": (от, до)}; None — нет окна."""
    def window(low, high, rising):
        a, b = crossing(lat, lon, day, low, tz, rising=rising), crossing(lat, lon, day, high, tz, rising=rising)
        if a is None or b is None:
            return None
        return (a, b) if rising else (b, a)

    return {
        "morning_golden": window(GOLDEN_LOW, GOLDEN_HIGH, True),
        "evening_golden": window(GOLDEN_LOW, GOLDEN_HIGH, False),
        "evening_blue": window(BLUE_LOW, GOLDEN_LOW, False),
    }


def _span(window):
    return f"{window[0]:%H:%M}–{window[1]:%H:%M}" if window else ""


def golden_hour_line(lat, lon, day: date, tz) -> str:
    """«Золотой час будет с 18:05 по 18:55» — только вечернее окно, без эмодзи и синего часа."""
    span = evening_golden_range(lat, lon, day, tz)
    return f"Золотой час будет {span}" if span else ""


def evening_golden_range(lat, lon, day: date, tz) -> str:
    """Вечерний золотой час «с 18:15 по 19:15» для строки погоды «Моего дня»."""
    if lat is None or lon is None:
        return ""
    window = light_windows(float(lat), float(lon), day, tz)["evening_golden"]
    return f"с {window[0]:%H:%M} по {window[1]:%H:%M}" if window else ""
