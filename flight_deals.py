"""Рассылка «Дешёвые билеты»: новые публикации авиадилов с вылетом из Амстердама.

Цены сами не ищем и не сравниваем — только читаем RSS сайтов с ошибочными тарифами
и распродажами (``config.FLIGHT_DEAL_FEEDS``). Ленты проверяются каждые 10 минут
с 07:00 до 23:00; публикации, вышедшие ночью, приходят утром. Каждая публикация
присылается один раз: отправленные id хранятся в профиле (``flight_deals``), поэтому
перезапуск бота не даёт дублей. При первом включении текущая лента только
запоминается, чтобы не прислать сразу пачку старых предложений.
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
from ui import transport as transport_ui

_log = logging.getLogger(__name__)

KIND = "flight_deals"
ACTIVE_FROM_HOUR = 7
ACTIVE_TO_HOUR = 23
_STATE_KEY = "flight_deals"
_SEEN_LIMIT = 400
_MAX_PER_CHECK = 3  # больше трёх новых за раз — значит, лента «проснулась»; остальное позже
_TIMEOUT_SECONDS = 15
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; morning-bot/1.0; personal deal alerts)"}

# Аэропорт вылета ищем в части заголовка до « to »: «Amsterdam, Netherlands to …»,
# «Brussels or Amsterdam to …»; иначе — явное «from Amsterdam» в любом месте.
_ORIGIN_RE = re.compile(r"\b(?:Amsterdam|Schiphol|AMS)\b")
_FROM_RE = re.compile(r"\bfrom\s+(?:[A-Z][\w'-]*,?\s+(?:or\s+|&\s+|and\s+)?)*?(?:Amsterdam|Schiphol)\b")
_PRICE_RE = re.compile(r"(?:[€£$]\s?\d[\d,.]*|\d[\d,.]*\s?(?:EUR|€))")
_ATOM = "{http://www.w3.org/2005/Atom}"


def is_active_time(now=None):
    hour = (now or datetime.now(config.TZ)).hour
    return ACTIVE_FROM_HOUR <= hour < ACTIVE_TO_HOUR


def _clean_link(link):
    """Ссылка без query/fragment — одна публикация с разными utm-метками остаётся одной."""
    parts = urlsplit(str(link or "").strip())
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", "")) if parts.netloc else ""


def parse_feed(xml_text, source):
    """RSS 2.0 или Atom → [{id, title, link, source}]; битый XML → []."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for node in root.iter("item"):
        title = " ".join(str(node.findtext("title") or "").split())
        link = str(node.findtext("link") or "").strip()
        guid = str(node.findtext("guid") or "").strip()
        items.append({"title": title, "link": link, "guid": guid})
    for node in root.iter(f"{_ATOM}entry"):
        title = " ".join(str(node.findtext(f"{_ATOM}title") or "").split())
        link_node = node.find(f"{_ATOM}link")
        link = str(link_node.get("href") if link_node is not None else "").strip()
        items.append({"title": title, "link": link, "guid": str(node.findtext(f"{_ATOM}id") or "").strip()})
    out = []
    for item in items:
        link = _clean_link(item["link"])
        if not item["title"] or not link.startswith("http"):
            continue
        # guid вроде «…/?p=612105» — id как есть; у ссылки срезаем utm-метки.
        out.append({"id": item["guid"] or link,
                    "title": item["title"], "link": item["link"].strip(), "source": source})
    return out


def fetch_feed(source, url):
    """Публикации одной ленты; None — источник не ответил (состояние тогда не трогаем)."""
    try:
        response = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as error:
        _log.warning("flight_deals: feed unavailable source=%s error=%s", source, type(error).__name__)
        return None
    items = parse_feed(response.content, source)
    if not items:
        _log.warning("flight_deals: feed has no items source=%s", source)
    return items


def departs_from_amsterdam(title):
    title = str(title or "")
    origin = re.split(r"\s+to\s+", title, maxsplit=1, flags=re.IGNORECASE)[0]
    return bool(_ORIGIN_RE.search(origin) and len(origin) < len(title)) or bool(_FROM_RE.search(title))


def deal_details(item):
    """Куда, цена и тип билета из заголовка; что не распознано — пусто."""
    title = item["title"]
    destination = ""
    match = re.search(r"\bto\s+(.+?)(?:\s+(?:for|from)\s+(?:only\s+)?[€£$\d]|,\s*returning|\s+\(|$)", title,
                      flags=re.IGNORECASE)
    if match:
        destination = match.group(1).strip(" ,.-")
    price = _PRICE_RE.search(title)
    lowered = title.casefold()
    trip = ("туда-обратно" if re.search(r"round[- ]?trip|return\b", lowered)
            else "в одну сторону" if re.search(r"one[- ]?way", lowered) else "")
    return {
        "destination": destination,
        "price": price.group(0).replace(" ", "") if price else "",
        "trip": trip,
        "error_fare": "error fare" in lowered,
    }


def _keyboard(link):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Открыть публикацию", url=link)],
        [InlineKeyboardButton("🎚️ Настроить", callback_data="set_notif_new"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def current_deals():
    """Публикации всех лент с вылетом из Амстердама; None — ни одна лента не ответила."""
    feeds = [fetch_feed(source, url) for source, url in config.FLIGHT_DEAL_FEEDS]
    if all(feed is None for feed in feeds):
        return None
    deals = {}
    for feed in feeds:
        for item in feed or ():
            if departs_from_amsterdam(item["title"]):
                deals.setdefault(item["id"], item)
    return list(deals.values())


async def check_user(bot, cid, deals=None):
    """Присылает пользователю новые публикации; первая проверка только запоминает ленту."""
    if deals is None:
        import asyncio
        deals = await asyncio.to_thread(current_deals)
    if deals is None:
        return
    state = store.get_profile(cid).get(_STATE_KEY) or {}
    seen = list(state.get("seen") or [])
    fresh = [item for item in deals if item["id"] not in set(seen)]
    to_send = fresh[:_MAX_PER_CHECK] if state.get("primed") else []
    for item in to_send:
        msg = transport_ui.flight_deal(item, deal_details(item))
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                               reply_markup=_keyboard(item["link"]))
    remembered = to_send if state.get("primed") else fresh
    if not remembered and state.get("primed"):
        return
    new_seen = (seen + [item["id"] for item in remembered])[-_SEEN_LIMIT:]

    def save(profile):
        profile[_STATE_KEY] = {"primed": True, "seen": new_seen}
        return profile, None

    store.mutate_profile(cid, save)
