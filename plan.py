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
    """Утро, день и вечер завтра — по часам Open-Meteo во времени места; сбой — []."""
    import vacation
    import weather
    import weather_provider
    try:
        data = weather.fetch_weather(place["lat"], place["lon"], 2)
        hours = weather_provider.week_hourly(place["lat"], place["lon"], str(vacation.local_tz(place.get("_cid"))))
        if hours:
            data = {**data, "hourly": hours}
        periods = weather.forecast_periods(data, now, day=1)
    except Exception:
        _log.warning("plan: tomorrow weather unavailable", exc_info=True)
        return []
    out = []
    for period in periods:
        wet = weather._rain_real(period["rain"], period["rain_mm"])
        frost = period.get("tmin", period["tmax"]) <= 0
        out.append({"title": weather.period_title(period),
                    "temp": f"до {period['tmax']:+.0f}°C" + (" · гололёд" if frost and wet else ""),
                    "rain": f"дождь {period['rain']:.0f}%" if wet else "без дождя",
                    "wind": f"ветер {weather._speed_range(period['winds'])} м/с"})
    return out


def collect(cid, now=None) -> dict:
    """Всё о завтрашнем дне для плана — из кэшей; каждый источник по отдельности: сбой одного не мешает остальным."""
    now = now or datetime.now(config.TZ)
    tomorrow = now.date() + timedelta(days=1)
    place = {**(store.get_settings(cid) or {}), "_cid": cid}
    iso = tomorrow.isoformat()
    import util
    plan = {"day": tomorrow, "city": place.get("city") or "", "cc": str(place.get("cc") or "").upper(),
            "flag": util.flag_from_cc(place.get("cc") or ""),
            "weather": _weather_periods(place, now), "official": [], "holidays": [], "events": [],
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
