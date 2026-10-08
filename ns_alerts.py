"""Рассылка «Поезда NS»: новые сбои и аварии на станции города пользователя.

Проверка каждые 10 минут с 06:00 до 23:00. Каждый сбой присылается один раз;
когда NS снимает сбой — короткая строка о восстановлении. Состояние хранится в
профиле (``ns_alerts``), поэтому перезапуск бота не дублирует предупреждения.
"""
import asyncio
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
import ns_api
import secure
import store
from ui import transport as transport_ui

_log = logging.getLogger(__name__)

KIND = "ns_disruptions"
ACTIVE_FROM_HOUR = 6
ACTIVE_TO_HOUR = 23
_STATE_KEY = "ns_alerts"


def is_active_time(now=None):
    hour = (now or datetime.now(config.TZ)).hour
    return ACTIVE_FROM_HOUR <= hour < ACTIVE_TO_HOUR


def _keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎚️ Настроить", callback_data="set_notif_new"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]])


def _translate(disruption):
    """Причина, ситуация и время в пути по-русски; без AI — оригинал NS."""
    fields = {key: disruption[key] for key in ("cause", "situation", "extra") if disruption.get(key)}
    if not fields:
        return disruption
    try:
        data = ai.llm_json(
            "Переведи на русский короткие сообщения NS о сбое поездов. Названия станций не переводи. "
            f"Данные: {secure.wrap_untrusted(str(fields), 'сообщение NS')}. "
            "Верни JSON без markdown с теми же ключами и переводом значений.",
            300, tier="cheap", module="ns",
            cache_context={"scenario": "ns_disruption_ru", "fields": fields, "schema_version": 1},
        )
    except Exception:
        _log.info("NS disruption translation unavailable; using original text")
        return disruption
    translated = {key: str(data.get(key) or value) for key, value in fields.items()} if isinstance(data, dict) else {}
    return {**disruption, **translated}


async def check_user(bot, cid):
    """Присылает новые сбои и восстановления для одного пользователя."""
    settings_data = store.get_settings(cid) or {}
    city = str(settings_data.get("city") or "")
    if str(settings_data.get("cc") or "").upper() != "NL" or not city:
        return
    codes = await asyncio.to_thread(ns_api.station_codes, city)
    if not codes:
        return
    feeds = await asyncio.gather(*(asyncio.to_thread(ns_api.active_disruptions, code) for code in codes))
    if any(feed is None for feed in feeds):  # NS не ответил — состояние не трогаем
        return
    # Один сбой на нескольких станциях города (Alkmaar и Alkmaar Noord) — одно предупреждение.
    current = list({item["id"]: item for feed in feeds for item in feed}.values())
    previous = (store.get_profile(cid).get(_STATE_KEY) or {}).get("active") or {}
    current_by_id = {item["id"]: item for item in current}
    for item in current:
        if item["id"] not in previous:
            msg = transport_ui.ns_alert(city, await asyncio.to_thread(_translate, item))
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_keyboard())
    for disruption_id, title in previous.items():
        if disruption_id not in current_by_id:
            msg = transport_ui.ns_restored(title)
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)

    active = {item["id"]: item["title"] for item in current}
    if active != previous:
        def save(profile):
            profile[_STATE_KEY] = {"active": active}
            return profile, None

        store.mutate_profile(cid, save)


MAX_WORKS_LINES = 2


def todays_works(cid, today=None):
    """Плановые работы на станциях города для «Моего дня»: только в дни работ, без AI."""
    settings_data = store.get_settings(cid) or {}
    city = str(settings_data.get("city") or "")
    if not config.NS_API_KEY or str(settings_data.get("cc") or "").upper() != "NL" or not city:
        return []
    today = today or datetime.now(config.TZ).date()
    works = {}
    for code in ns_api.station_codes(city):
        for item in ns_api.planned_works(code, today):
            works.setdefault(item["id"], item)
    return sorted(works.values(), key=lambda item: (item["end"], item["title"]))[:MAX_WORKS_LINES]
