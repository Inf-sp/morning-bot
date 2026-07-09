"""Погодное предупреждение: библиотека последствий.

Уведомление строится вокруг последствий для человека, а не пересказа прогноза:
⚠️ заголовок → 1–3 самых важных события → Когда → Что сделать.

Каждое явление (hazard) описывается записью WeatherHazard: условие срабатывания,
текст события, рекомендации, приоритет. Тексты — детерминированные шаблоны (без LLM).
Отправлять только при наличии хотя бы одного значимого фактора (тихие дни — молчим).
"""
from dataclasses import dataclass
from typing import Callable

import config
import store
import weather

# Максимумы по ТЗ: не перегружать уведомление.
MAX_EVENTS = 3
MAX_ADVICE = 4

# --- weathercode-наборы ---
THUNDER_CODES = (95, 96, 99)
SNOW_CODES = (71, 73, 75, 77, 85, 86)
FOG_CODES = (45, 48)
FREEZING_CODES = (56, 57, 66, 67)  # переохлаждённая морось/дождь → гололёд
HEAVY_RAIN_CODES = (65, 81, 82)

# --- пороги ---
RAIN_PROB_WARN = 60       # «сильный дождь»: вероятность > 60% (по ТЗ)
STORM_GUST_MS = 15        # штормовой ветер по порывам
HEAT_TMAX = 28            # жара
COLD_TMAX = 0             # мороз (весь день ниже нуля)
UV_WARN = 6               # высокий UV
HUMIDITY_WARN = 85        # высокая влажность (%)
HUMIDITY_MIN_TEMP = 22    # душно только если при этом тепло


@dataclass(frozen=True)
class WeatherHazard:
    key: str
    priority: int                       # меньше = важнее
    triggers: Callable[["WarnContext"], bool]
    event: Callable[["WarnContext"], str]
    advice: Callable[["WarnContext"], list]


@dataclass
class WarnContext:
    # погодные числа/флаги
    tmax: float | None = None
    tmin: float | None = None
    wind_ms: float | None = None
    gust_ms: float | None = None
    rain_prob: float = 0.0
    rain_mm: float | None = None
    weathercode: int | None = None
    uv: float | None = None
    humidity: float | None = None
    when_rain: str = ""
    when_wind: str = ""
    when_uv: str = ""
    when_heat: str = ""
    # персональные флаги
    bike: bool = False
    has_raincoat: bool = False
    pollen_allergy: bool = False
    has_plan_today: bool = False


# ---------- условия и тексты ----------
def _f(v, digits=0):
    return f"{v:.{digits}f}"


HAZARDS = [
    WeatherHazard(
        key="thunderstorm", priority=1,
        triggers=lambda c: c.weathercode in THUNDER_CODES,
        event=lambda c: "⛈️ Возможна гроза.",
        advice=lambda c: [
            "По возможности оставайся в помещении.",
            "Не укрывайся под одинокими деревьями.",
        ],
    ),
    WeatherHazard(
        key="ice", priority=2,
        triggers=lambda c: (
            (c.weathercode in FREEZING_CODES)
            or (c.tmin is not None and c.tmin <= 0
                and ((c.rain_mm or 0) > 0 or (c.humidity or 0) >= 90))
        ),
        event=lambda c: "🧊 Возможен гололёд.",
        advice=lambda c: [
            "Надень обувь с хорошим сцеплением.",
            "Будь осторожен на мостах и лестницах.",
        ],
    ),
    WeatherHazard(
        key="storm_wind", priority=3,
        triggers=lambda c: (c.gust_ms or c.wind_ms or 0) >= STORM_GUST_MS,
        event=lambda c: f"💨 Порывы ветра до {_f(c.gust_ms or c.wind_ms)} м/с.",
        advice=lambda c: (
            ["На велосипеде будь осторожен при боковом ветре, особенно на мостах и открытых участках."]
            if c.bike else
            ["Будь осторожен на велосипеде — возможны сильные порывы ветра."]
        ) + ["Убери с балкона лёгкие предметы."],
    ),
    WeatherHazard(
        key="heavy_rain", priority=4,
        triggers=lambda c: c.rain_prob > RAIN_PROB_WARN and (
            (c.rain_mm or 0) >= weather.HEAVY_RAIN_MM_DAY or c.weathercode in HEAVY_RAIN_CODES
            or c.rain_prob >= 70
        ),
        event=lambda c: f"🌧️ Сегодня ожидается дождь с вероятностью {_f(c.rain_prob)}%."
                        + (" Возможны сильные осадки." if (c.rain_mm or 0) >= weather.HEAVY_RAIN_MM_DAY else ""),
        advice=lambda c: _rain_advice(c),
    ),
    WeatherHazard(
        key="heat", priority=5,
        triggers=lambda c: c.tmax is not None and c.tmax >= HEAT_TMAX,
        event=lambda c: f"🌡️ Жарко, до +{_f(c.tmax)}°C.",
        advice=lambda c: [
            "Пей больше воды.",
            "По возможности избегай солнца с 12:00 до 16:00.",
        ],
    ),
    WeatherHazard(
        key="high_uv", priority=6,
        triggers=lambda c: c.uv is not None and c.uv >= UV_WARN,
        event=lambda c: f"☀️ Высокий UV-индекс — {_f(c.uv)}.",
        advice=lambda c: [
            "Используй солнцезащитный крем.",
            "Возьми очки и головной убор.",
        ],
    ),
    WeatherHazard(
        key="snow", priority=7,
        triggers=lambda c: c.weathercode in SNOW_CODES,
        event=lambda c: "❄️ Ожидается снег.",
        advice=lambda c: [
            "Планируй больше времени на дорогу.",
            "Возможны скользкие тротуары.",
        ],
    ),
    WeatherHazard(
        key="cold", priority=8,
        triggers=lambda c: c.tmax is not None and c.tmax <= COLD_TMAX,
        event=lambda c: f"🥶 Мороз, днём около {_f(c.tmax):+}°C.",
        advice=lambda c: [
            "Одевайся теплее.",
            "На дорогах возможен гололёд.",
        ],
    ),
    WeatherHazard(
        key="fog", priority=9,
        triggers=lambda c: c.weathercode in FOG_CODES,
        event=lambda c: "🌫️ Ожидается туман, видимость снижена.",
        advice=lambda c: (
            ["На велосипеде или в машине включи освещение."] if c.bike else
            ["Если едешь на велосипеде или автомобиле — включи освещение."]
        ),
    ),
    WeatherHazard(
        key="high_humidity", priority=10,
        triggers=lambda c: (c.humidity is not None and c.humidity >= HUMIDITY_WARN
                            and c.tmax is not None and c.tmax >= HUMIDITY_MIN_TEMP),
        event=lambda c: "💧 Высокая влажность — может казаться жарче.",
        advice=lambda c: ["Если долго находишься на улице — чаще пей воду."],
    ),
]


def _rain_advice(c: WarnContext) -> list:
    advice = []
    if c.has_raincoat:
        advice.append("Лучше надень дождевик — сегодня он пригодится.")
    else:
        advice.append("🌧️ Возьми дождевик или зонт — без него будет некомфортно.")
    advice.append("На дорогах будет мокро, тормозной путь увеличится.")
    if c.bike:
        advice.append("На велосипеде будь осторожнее на мокрой дороге.")
    if c.has_plan_today:
        advice.append("Перед выходом на встречу возьми зонт — дождь ожидается днём.")
    return advice


# ---------- сбор контекста ----------
def _day_str(data):
    try:
        return data["daily"]["time"][0]
    except (KeyError, IndexError, TypeError):
        return None


def collect_context(data, cid) -> WarnContext:
    """Собирает WarnContext из ответа fetch_weather и данных пользователя."""
    import settings as _s
    d = data.get("daily", {})

    def _first(key, default=None):
        vals = d.get(key)
        return vals[0] if isinstance(vals, list) and vals else default

    day_str = _day_str(data)
    tmax = _first("temperature_2m_max")
    tmin = _first("temperature_2m_min")
    wind_ms = _first("windspeed_10m_max") or 0
    gust_ms = _first("windgusts_10m_max")
    rain_prob = _first("precipitation_probability_max") or 0
    rain_mm = _first("precipitation_sum")
    weathercode = _first("weathercode")
    uv = _first("uv_index_max")
    humidity = _daytime_max_hourly(data, day_str, "relativehumidity_2m")

    # персонализация
    bike = bool(_s.get(cid, "bike", False))
    pollen_allergy = bool(_s.get(cid, "pollen_allergy", False))
    has_raincoat = _raincoat_present(cid)
    has_plan_today = _has_plan_today(cid)

    ctx = WarnContext(
        tmax=tmax, tmin=tmin, wind_ms=wind_ms, gust_ms=gust_ms,
        rain_prob=rain_prob, rain_mm=rain_mm, weathercode=weathercode,
        uv=uv, humidity=humidity,
        bike=bike, has_raincoat=has_raincoat, pollen_allergy=pollen_allergy,
        has_plan_today=has_plan_today,
    )
    # интервалы «когда» из hourly в дневном окне
    ctx.when_rain = _hourly_when(data, day_str, "precipitation_probability", RAIN_PROB_WARN)
    ctx.when_wind = _hourly_when(data, day_str, "windgusts_10m", STORM_GUST_MS)
    ctx.when_uv = _hourly_when(data, day_str, "uv_index", UV_WARN)
    ctx.when_heat = _hourly_when(data, day_str, "temperature_2m", HEAT_TMAX)
    return ctx


def _daytime_max_hourly(data, day_str, key):
    try:
        hours = data["hourly"]["time"]
        vals = data["hourly"][key]
    except (KeyError, TypeError):
        return None
    day_vals = [v for t, v in zip(hours, vals)
                if day_str and t.startswith(day_str)
                and weather.DAYTIME_START_H <= int(t[11:13]) < weather.DAYTIME_END_H
                and v is not None]
    return max(day_vals) if day_vals else None


def _hourly_when(data, day_str, key, threshold):
    """Интервал 'HH:00–HH:00' в дневном окне, где фактор >= threshold."""
    try:
        hours = data["hourly"]["time"]
        vals = data["hourly"][key]
    except (KeyError, TypeError):
        return ""
    active = [int(t[11:13]) for t, v in zip(hours, vals)
              if day_str and t.startswith(day_str)
              and weather.DAYTIME_START_H <= int(t[11:13]) < weather.DAYTIME_END_H
              and v is not None and v >= threshold]
    if not active:
        return ""
    lo, hi = min(active), max(active)
    if lo == hi:
        return f"{lo:02d}:00"
    return f"{lo:02d}:00–{hi + 1:02d}:00"


def _raincoat_present(cid) -> bool:
    try:
        import wardrobe
        return wardrobe._has_rain_outerwear(store.load_wardrobe(cid))
    except Exception:
        return False


def _has_plan_today(cid) -> bool:
    """Есть ли заметка-план на сегодняшнюю дату (формат '%d.%m', без времени)."""
    try:
        from datetime import datetime
        today = datetime.now(config.TZ).strftime("%d.%m")
        notes = store.get_list(config.NOTES_KEY, cid)
        for n in notes:
            if isinstance(n, dict) and n.get("bucket") == "plan" and n.get("date") == today:
                return True
    except Exception:
        pass
    return False


# ---------- сборка предупреждения ----------
def _when_for(ctx: WarnContext, keys) -> str:
    """Объединённый интервал 'когда' по вошедшим hazard-ключам."""
    mapping = {
        "heavy_rain": ctx.when_rain,
        "thunderstorm": ctx.when_rain,
        "storm_wind": ctx.when_wind,
        "high_uv": ctx.when_uv,
        "heat": ctx.when_heat or ctx.when_uv,
    }
    for k in keys:
        w = mapping.get(k)
        if w:
            return w
    return "в течение дня"


def evaluate(ctx: WarnContext):
    """Возвращает список сработавших hazards, отсортированный по приоритету."""
    fired = [h for h in HAZARDS if _safe_trigger(h, ctx)]
    fired.sort(key=lambda h: h.priority)
    return fired


def _safe_trigger(hazard, ctx) -> bool:
    try:
        return bool(hazard.triggers(ctx))
    except Exception:
        return False


def build_warning(data, cid):
    """Собирает MessageSpec погодного предупреждения или None (тихий день)."""
    from ui import weather as weather_ui
    ctx = collect_context(data, cid)
    fired = evaluate(ctx)
    if not fired:
        return None
    top = fired[:MAX_EVENTS]
    events = [h.event(ctx) for h in top]
    when = _when_for(ctx, [h.key for h in top])
    advice = []
    seen = set()
    for h in top:
        for a in h.advice(ctx):
            key = a.strip().lower()
            if key not in seen:
                seen.add(key)
                advice.append(a)
    advice = advice[:MAX_ADVICE]
    return weather_ui.weather_warning(events, when, advice)
