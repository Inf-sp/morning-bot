"""Рассылка «Поезда NS»: новые сбои и аварии на станции города пользователя.

Проверка каждые 10 минут с 06:00 до 23:00. Каждый сбой присылается один раз;
когда NS снимает сбой — короткая строка о восстановлении. Состояние хранится в
профиле (``ns_alerts``), поэтому перезапуск бота не дублирует предупреждения.
"""
import asyncio
import logging
import re
from datetime import datetime

from telegram import InlineKeyboardMarkup

import ai
import config
import ns_api
import secure
import store
from ui import transport as transport_ui
from ui.navigation import notif_row

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
    return InlineKeyboardMarkup([notif_row()])


# Частые формулировки NS — перевод без AI (и запас, если AI вернул нидерландский).
_KNOWN_RU = {
    "defect spoor": "неисправность пути", "defecte trein": "неисправный поезд", "een defecte trein": "неисправный поезд",
    "seinstoring": "неисправность сигнализации", "wisselstoring": "неисправность стрелки",
    "storing aan het spoor": "неисправность на путях", "storing aan de bovenleiding": "неисправность контактной сети",
    "stroomstoring": "отключение электричества", "aanrijding met een persoon": "наезд на человека",
    "aanrijding": "столкновение", "aanrijding met een voertuig": "столкновение с автомобилем",
    "werkzaamheden": "ремонтные работы", "onverwachte werkzaamheden": "внеплановые работы",
    "een eerdere verstoring": "предыдущий сбой", "koperdiefstal": "кража кабеля", "brand": "пожар",
    "weersomstandigheden": "погодные условия", "politieonderzoek": "работа полиции",
    "hulpverlening aan een reiziger": "помощь пассажиру", "personen langs het spoor": "люди у путей",
    "minder treinen": "меньше поездов", "geen treinen": "поездов нет",
    "de vertraging neemt af": "задержки уменьшаются",
}


def _known_ru(value) -> str:
    return _KNOWN_RU.get(" ".join(str(value or "").casefold().strip(" .").split()), "")


# Сбой уже заканчивается: «beperkingen voorbij», «rijden weer» — не повод для нового предупреждения.
_ENDING_RE = re.compile(r"\b(?:voorbij|opgelost|hersteld|rijden (?:de treinen )?weer|neemt af)\b", re.IGNORECASE)


def is_ending(item) -> bool:
    return bool(_ENDING_RE.search(str(item.get("situation") or "")))


def concerns_city(item, city) -> bool:
    """Сбой касается города: его станция в участке, тексте NS или секциях («Amsterdam – Hoofddorp» для Алкмара —
    нет, даже если NS прислал его в ленте станции)."""
    city = str(city or "").casefold()
    texts = [item.get("title"), item.get("situation"), *(item.get("sections") or [])]
    return bool(city) and any(city in str(text or "").casefold() for text in texts)


def _translate(disruption):
    """Причина, ситуация, время в пути, замена и совет по-русски. Что не перевелось: частая формулировка — из словаря,
    ситуация — «Сбой в движении поездов», остальное убирается (нидерландского текста пользователь не видит)."""
    fields = {key: disruption[key] for key in ("cause", "situation", "extra", "alternative", "advice")
              if disruption.get(key)}
    if not fields:
        return disruption
    try:
        data = ai.llm_json(
            "Переведи на русский короткие сообщения NS о сбое поездов. Названия станций не переводи. "
            f"Данные: {secure.wrap_untrusted(str(fields), 'сообщение NS')}. "
            "Верни JSON без markdown с теми же ключами и переводом значений.",
            300, tier="cheap", module="ns",
            cache_context={"scenario": "ns_disruption_ru", "fields": fields, "schema_version": 3},
            # Ответ без кириллицы (модель вернула нидерландский) — брак: пробуем следующий
            # провайдер и не кэшируем.
            result_validator=lambda value: isinstance(value, dict) and all(
                _CYRILLIC.search(str(value.get(key) or "")) for key in fields),
        )
    except Exception:
        _log.info("NS disruption translation unavailable; using dictionary")
        data = {}
    data = data if isinstance(data, dict) else {}
    translated = {}
    for key, value in fields.items():
        text = str(data.get(key) or "")
        if not _CYRILLIC.search(text):
            text = _known_ru(value) or ("Сбой в движении поездов" if key == "situation" else "")
        translated[key] = text
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
    # Один сбой на нескольких станциях города (Alkmaar и Alkmaar Noord) — одно предупреждение; только сбои, которые
    # касаются города.
    current = [item for item in {item["id"]: item for feed in feeds for item in feed}.values()
               if concerns_city(item, city)]
    previous = {
        disruption_id: value if isinstance(value, dict) else {"title": str(value), "sig": disruption_id}
        for disruption_id, value in ((store.get_profile(cid).get(_STATE_KEY) or {}).get("active") or {}).items()
    }
    # Новый сбой, который уже заканчивается («beperkingen voorbij»), не присылаем и не запоминаем — иначе потом
    # пришло бы «восстановлено» о том, о чём не предупреждали.
    current = [item for item in current if item["id"] in previous or not is_ending(item)]
    from datetime import datetime
    now = datetime.now(config.TZ).strftime("%Y-%m-%dT%H:%M")
    # Для сообщения «восстановлено»: с какого времени шёл сбой и его причина (по-русски — из предупреждения).
    active = {item["id"]: {"title": item["title"], "sig": incident_signature(item),
                           "cause": (previous.get(item["id"]) or {}).get("cause") or item.get("cause") or "",
                           "since": (previous.get(item["id"]) or {}).get("since") or item.get("start") or now}
              for item in current}
    # NS заводит одну неисправность отдельной записью на каждый маршрут (Amsterdam - Alkmaar,
    # Haarlem - Alkmaar) — сообщаем об инциденте один раз.
    known = {value["sig"] for value in previous.values()}
    for item in current:
        sig = active[item["id"]]["sig"]
        if item["id"] in previous or sig in known:
            continue
        known.add(sig)
        translated = await asyncio.to_thread(_translate, item)
        active[item["id"]]["cause"] = translated.get("cause") or active[item["id"]]["cause"]
        msg = transport_ui.ns_alert(city, translated)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_keyboard())
    still_active = {value["sig"] for value in active.values()}
    restored = {}
    for disruption_id, value in previous.items():
        if disruption_id not in active and value["sig"] not in still_active:
            restored.setdefault(value["sig"], value)
    for value in restored.values():
        msg = transport_ui.ns_restored(value.get("title"), since=value.get("since"), until=now,
                                       cause=value.get("cause"))
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_keyboard())

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
