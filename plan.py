"""«🗓️ План на завтра»: погода по частям суток и всё, что завтра стоит учесть — официальные предупреждения KNMI,
праздники, события города, концерты твоих артистов, премьеры, работы на ЖД. Рассылка вечером (вместо «Погоды на
завтра» и «Праздников и концертов» накануне) и кнопка в «Моём дне». Данные — из готовых кэшей разделов; сеть —
только погода и предупреждения KNMI.
"""
import asyncio
import logging
from datetime import datetime, timedelta

import config
import store
from ui import myday as myday_ui

_log = logging.getLogger(__name__)


def _weather_periods(place, now):
    """Утро, день и вечер завтра — по часам Open-Meteo во времени места (weather.forecast_periods); сбой — []."""
    import vacation
    import weather
    import weather_provider
    try:
        data = weather.fetch_weather(place["lat"], place["lon"], 2)
        hours = weather_provider.week_hourly(place["lat"], place["lon"], str(vacation.local_tz(place.get("_cid"))))
        if hours:
            data = {**data, "hourly": hours}
        return weather.forecast_periods(data, now, day=1)
    except Exception:
        _log.warning("plan: tomorrow weather unavailable", exc_info=True)
        return []


def _degrees(value) -> str:
    """«+2°», «-3°», а около нуля — «0°» (не «-0°»)."""
    value = round(float(value))
    return "0°" if value == 0 else f"{value:+d}°"


def _labels(names) -> str:
    """«утром», «утром и вечером», «утром, днём и вечером»."""
    names = [name.lower() for name in names]
    return " и ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " и " + names[-1]


ICE_TMAX = 3  # °C: в эту часть суток не теплее — при минусе ночью возможна наледь
SCARF_TMIN = 6  # °C: холоднее — шарф


def weather_day(periods) -> dict:
    """Завтрашняя погода одной строкой вместо трёх частей суток: значок, небо или дождь, от и до, ветер, наледь;
    и что взять с собой. Без AI; частей суток нет — {}."""
    import weather
    if not periods:
        return {}
    wet = [period for period in periods if weather._rain_real(period["rain"], period["rain_mm"])]
    frost = [period for period in periods if period.get("tmin", period["tmax"]) <= 0]
    ice = [period["label"] for period in frost if wet and period in wet or period["tmax"] <= ICE_TMAX]
    clouds = [period["clouds"] for period in periods if period.get("clouds") is not None]
    main = next((period for period in periods if period["label"] == "Днём"), periods[0])
    sky = weather.sky_word(sum(clouds) / len(clouds) if clouds else None, main.get("code"))
    low = min(period.get("tmin", period["tmax"]) for period in periods)
    high = max(period["tmax"] for period in periods)
    wind = max((value for period in periods for value in period.get("winds") or []), default=0)
    temps = f"около {_degrees(high)}" if _degrees(low) == _degrees(high) else f"от {_degrees(low)} до {_degrees(high)}"
    parts = [f"{'дождь ' + _labels([p['label'] for p in wet]) if wet else sky}, {temps}",
             f"{'сильный ветер' if wind > 10 else 'ветер'} до {wind:.0f} м/с"]
    if ice:
        parts.append(f"{'гололёд' if wet else 'наледь'} {_labels(ice)}")
    take = (["шапку", "перчатки"] if low <= 0 else ["шарф"] if low <= SCARF_TMIN else []) + (["зонт"] if wet else [])
    icon = (max(wet, key=lambda period: period["rain"])["icon"] if wet and not frost
            else "❄️" if frost else main["icon"])
    return {"icon": icon, "line": " · ".join(parts), "take": take}


def daylight(place, day) -> str:
    """Световой день «08:22–16:35» по координатам (sun.py, без сети); нет координат — ""."""
    import sun
    import vacation
    try:
        tz = vacation.local_tz(place.get("_cid"))
        lat, lon = float(place["lat"]), float(place["lon"])
        rise = sun.crossing(lat, lon, day, -0.833, tz, rising=True)
        sunset = sun.crossing(lat, lon, day, -0.833, tz, rising=False)
    except (KeyError, TypeError, ValueError):
        return ""
    return f"{rise:%H:%M}–{sunset:%H:%M}" if rise and sunset else ""


def collect(cid, now=None) -> dict:
    """Всё о завтрашнем дне для плана — из кэшей; каждый источник по отдельности: сбой одного не мешает остальным."""
    now = now or datetime.now(config.TZ)
    tomorrow = now.date() + timedelta(days=1)
    place = {**(store.get_settings(cid) or {}), "_cid": cid}
    iso = tomorrow.isoformat()
    import util
    plan = {"day": tomorrow, "city": place.get("city") or "", "cc": str(place.get("cc") or "").upper(),
            "flag": util.flag_from_cc(place.get("cc") or ""),
            "weather": weather_day(_weather_periods(place, now)), "light": daylight(place, tomorrow), "official": [], "holidays": [], "events": [],
            "premieres": [], "works": []}

    def safe(name, call):
        try:
            return call()
        except Exception:
            _log.warning("plan: %s unavailable cid=%s", name, cid, exc_info=True)
            return []

    import weather_warn
    import meteoalarm
    plan["official"] = [meteoalarm.line(item, now) for item in safe(
        "knmi", lambda: weather_warn._official(place, tomorrow=True))]
    import public_holidays
    plan["holidays"] = safe("holidays", lambda: public_holidays.week_holidays(place.get("cc"), tomorrow, tomorrow))
    import leisure_events
    cards = safe("events", lambda: leisure_events.feed(cid))
    plan["events"] = [card for card in cards if card["kind"] not in ("premiere", "read", "holiday")
                      and card["date"] <= iso <= card["end"]][:4]
    plan["premieres"] = [card for card in cards if card["kind"] == "premiere" and card["date"] == iso][:2]
    if plan["cc"] == "NL":
        import ns_alerts
        plan["works"] = safe("works", lambda: ns_alerts.todays_works(cid, tomorrow))
    return plan


async def build(cid, reply_markup=None):
    plan = await asyncio.to_thread(collect, cid)
    return myday_ui.tomorrow_plan(plan, reply_markup=reply_markup)


async def send(bot, cid, reply_markup=None, q=None):
    """Рассылка или экран из «Моего дня»: из кнопки — на месте прежнего сообщения."""
    msg = await build(cid, reply_markup)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=msg.reply_markup,
                                      disable_web_page_preview=True)
            return
        except Exception:
            _log.debug("plan: edit failed, sending new", exc_info=True)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup,
                           disable_web_page_preview=True)
