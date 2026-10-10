"""Рассылка «Поезда NS»: новые сбои и аварии на станции города пользователя.

Проверка каждые 10 минут с 06:00 до 23:00. Каждый сбой присылается один раз;
когда NS снимает сбой — короткая строка о восстановлении. Состояние хранится в
профиле (``ns_alerts``), поэтому перезапуск бота не дублирует предупреждения.
"""
import asyncio
import logging
import re
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
_CYRILLIC = re.compile("[а-яё]", re.IGNORECASE)


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
            cache_context={"scenario": "ns_disruption_ru", "fields": fields, "schema_version": 2},
            # Ответ без кириллицы (модель вернула нидерландский) — брак: пробуем следующий
            # провайдер и не кэшируем.
            result_validator=lambda value: isinstance(value, dict) and all(
                _CYRILLIC.search(str(value.get(key) or "")) for key in fields),
        )
    except Exception:
        _log.info("NS disruption translation unavailable; using original text")
        return disruption
    translated = {key: str(data.get(key) or value) for key, value in fields.items()} if isinstance(data, dict) else {}
    return {**disruption, **translated}


def incident_signature(item):
    """Один инцидент: та же причина и то же ожидаемое окончание; без них — сама запись."""
    cause = " ".join(str(item.get("cause") or "").casefold().split())
    until = str(item.get("until") or "")[:16]
    return f"{cause}|{until}" if cause and until else str(item.get("id") or "")


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
    previous = {
        disruption_id: value if isinstance(value, dict) else {"title": str(value), "sig": disruption_id}
        for disruption_id, value in ((store.get_profile(cid).get(_STATE_KEY) or {}).get("active") or {}).items()
    }
    active = {item["id"]: {"title": item["title"], "sig": incident_signature(item)} for item in current}
    # NS заводит одну неисправность отдельной записью на каждый маршрут (Amsterdam - Alkmaar,
    # Haarlem - Alkmaar) — сообщаем об инциденте один раз.
    known = {value["sig"] for value in previous.values()}
    for item in current:
        sig = active[item["id"]]["sig"]
        if item["id"] in previous or sig in known:
            continue
        known.add(sig)
        msg = transport_ui.ns_alert(city, await asyncio.to_thread(_translate, item))
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_keyboard())
    still_active = {value["sig"] for value in active.values()}
    restored = {}
    for disruption_id, value in previous.items():
        if disruption_id not in active and value["sig"] not in still_active:
            restored.setdefault(value["sig"], value["title"])
    for title in restored.values():
        msg = transport_ui.ns_restored(title)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)

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
