"""«🎟️ События» в Досуге — журнал недели: что будет в городе и стране в ближайшие 7 дней, по дате, как расписание.
Концерты (твои артисты и крупные концерты рядом), фестивали, выставки, праздники, городские мероприятия, премьеры и
статьи недели — одна лента карточек с картинкой и датой, ← n/N → по кругу, как «Премьеры».

Всё — из готовых кэшей разделов (концерты, афиша города, праздники, лента премьер, набор статей), без AI. Картинка
есть у каждой карточки: своя (постер, обложка, картинка статьи или страницы события), фото исполнителя (Deezer) или
картинка из Википедии — по названию, площадке или городу (кэш месяц; прогревается ночью, при открытии — до 5 с).
"""
import asyncio
import logging
from datetime import timedelta

import config
import store

_log = logging.getLogger(__name__)

WEEK_DAYS = 7  # журнал недели: повод заглядывать, а не афиша на месяц
_MINE_MAX, _POPULAR_MAX, _CITY_MAX, _READS_MAX = 5, 6, 12, 6
_IMAGE_WAIT = 5  # картинку текущей карточки при открытии ждём не дольше; не успели — карточка текстом
_KIND_ORDER = ("holiday", "festival", "concert", "exhibition", "city", "premiere", "read")


def _today(cid):
    import leisure_hub
    return leisure_hub._today_and_cc(cid)


def _week(today):
    return today, today + timedelta(days=WEEK_DAYS - 1)


def _concerts(cid, today, cc) -> list:
    """Концерты недели: твои артисты и крупные концерты страны (самые известные — по кэшу заметок, без AI)."""
    import leisure_concerts as concerts
    start, end = _week(today)
    mine = concerts.concert_items_between(concerts._concerts_cache_get(cid, cc) or [], start, end, _MINE_MAX)
    taken = {concerts._concert_identity_text(item["artist"]) for item in mine}
    notes = store._load(config.CONCERT_ARTIST_NOTES_KEY) or {}
    notes = notes if isinstance(notes, dict) else {}

    def note(item):
        return notes.get(concerts._concert_identity_text(item["artist"])) or {}

    popular = [item for item in concerts.concert_items_between(
        concerts._popular_events_cache_get(cc, today) or [], start, end, concerts._POPULAR_EVENTS_LIMIT)
        if concerts._concert_identity_text(item["artist"]) not in taken]
    popular = sorted(popular, key=lambda item: (-int(note(item).get("fame") or 0), item["date"]))[:_POPULAR_MAX]
    cards = []
    for item, mine_flag in [*((item, True) for item in mine), *((item, False) for item in popular)]:
        cards.append({"kind": "concert", "name": item["artist"], "url": item.get("url") or "",
                      "date": item["date"], "end": item["date"], "venue": item.get("venue") or "",
                      "place": ", ".join(part for part in (item.get("venue"), item.get("city")) if part),
                      "mine": mine_flag, "about": note(item).get("about") or "", "image": ""})
    return cards


def _city(cid, today) -> list:
    """Фестивали, выставки, праздники и городские мероприятия недели из афиши города (кэш «Праздников»)."""
    import local_events
    start, end = _week(today)
    found = local_events.agenda(cid, start, end, fetch=False)
    cards = []
    for event in found[:_CITY_MAX]:
        kind = event.get("kind") if event.get("kind") in ("festival", "exhibition", "concert", "holiday") else "city"
        cards.append({"kind": kind, "name": event["name"], "url": event.get("url") or "",
                      "date": event["start"], "end": event["end"], "place": event.get("place") or "",
                      "free": bool(event.get("free")), "about": event.get("about") or "",
                      "image": event.get("image") or ""})
    return cards


def _holidays(cid, today) -> list:
    import public_holidays
    cc = (store.get_settings(cid) or {}).get("cc")
    return [{"kind": "holiday", "name": item["name"], "url": "", "date": item["date"], "end": item["date"],
             "about": item.get("about") or "", "image": ""}
            for item in public_holidays.week_holidays(cc, *_week(today))]


def _premieres(cid, today) -> list:
    """Премьеры недели: вышли за последние 7 дней или выходят в ближайшие 7; без даты — нет."""
    import leisure_hub
    from ui import leisure as leisure_ui
    since, end = (today - timedelta(days=WEEK_DAYS - 1)).isoformat(), _week(today)[1].isoformat()
    cards = []
    for kind, item in leisure_hub.premieres_feed(cid, today):
        day = leisure_hub._release_day(item)
        if not day or not since <= day <= end:
            continue
        name, url, meta, about = leisure_ui._week_item(item, kind)
        values = [str(value).lstrip("— ").strip() for value in meta if str(value or "").strip()]
        cards.append({"kind": "premiere", "premiere": kind, "name": name or "", "url": url or "",
                      "date": day, "end": day, "meta": values, "about": item.get("note") or about or "",
                      "image": str(item.get("cover" if kind == "music" else "poster") or "")})
    return cards


def _reads(cid, today) -> list:
    """Статьи недели — набор дня «Статей» (свежее за неделю), самые интересные первыми; дата — публикации."""
    import leisure_reads
    since = (today - timedelta(days=WEEK_DAYS - 1)).isoformat()
    cards = []
    for item in leisure_reads.ranked(cid, today)[:_READS_MAX]:
        day = str(item.get("published") or "") or today.isoformat()
        if day < since or not str(item.get("link") or "").startswith("http"):
            continue
        minutes = int(item.get("minutes") or 0)
        cards.append({"kind": "read", "name": item.get("title") or "", "url": item["link"], "date": day, "end": day,
                      "meta": [item.get("source") or "", item.get("lang") or "", f"{minutes} мин" if minutes else ""],
                      "about": item.get("about") or "", "image": item.get("image") or ""})
    return cards


def feed(cid) -> list:
    """Лента «Событий» на неделю по дате (уже вышедшее и идущее — как сегодня), без пустых имён и повторов."""
    today, cc = _today(cid)
    cards = []
    for name, build in (("concerts", lambda: _concerts(cid, today, cc)), ("city", lambda: _city(cid, today)),
                        ("holidays", lambda: _holidays(cid, today)), ("premieres", lambda: _premieres(cid, today)),
                        ("reads", lambda: _reads(cid, today))):
        try:
            cards += build()
        except Exception:
            _log.warning("events: %s unavailable cid=%s", name, cid, exc_info=True)
    seen, unique = set(), []
    for card in cards:
        key = (str(card["name"]).casefold(), card["date"])
        if card["name"] and card["date"] and key not in seen:
            seen.add(key)
            unique.append(card)
    iso = today.isoformat()
    return sorted(unique, key=lambda card: (max(card["date"], iso), _KIND_ORDER.index(card["kind"]),
                                            str(card["name"]).casefold()))


def _image_queries(card, city) -> list:
    """Запросы картинки к Википедии: само событие, площадка в городе, город (последний — почти всегда есть)."""
    queries = []
    if card["kind"] != "read":
        queries.append(f"{card['name']} {city}".strip() if card["kind"] in ("festival", "city", "exhibition")
                       else card["name"])
    place = str(card.get("venue") or card.get("place") or "").strip()
    if place:
        queries.append(f"{place} {city}".strip())
    if city:
        queries.append(city)
    return queries


def image_for(card, city, fetch=True) -> str:
    """Картинка карточки: своя → фото исполнителя (концерт) → Википедия по событию, площадке, городу."""
    import countries
    image = str(card.get("image") or "")
    if image.startswith("https://"):
        return image
    if card["kind"] == "concert":
        photo = countries.artist_photo(card["name"], fetch=fetch)
        if str(photo or "").startswith("https://"):
            return photo
    for query in _image_queries(card, city):
        found = countries.wiki_image(query, fetch=fetch)
        if str(found or "").startswith("https://"):
            return found
    return ""


def warm_images(cid) -> int:
    """Ночной прогрев: картинки всех карточек ленты — чтобы открытие и листание не ждали сеть."""
    city = str((store.get_settings(cid) or {}).get("city") or "")
    return sum(1 for card in feed(cid) if image_for(card, city))


async def send_events(bot, cid, arg="", q=None):
    """«🎟️ События»: карточка ленты недели с картинкой и датой, ← n/N → по кругу (lz_ev_<n>) в том же сообщении."""
    import leisure_hub
    from ui import leisure as leisure_ui
    cards = await asyncio.to_thread(feed, cid)
    index = int(arg) % len(cards) if cards and str(arg).isdigit() else 0
    image = ""
    if cards:
        city = str((store.get_settings(cid) or {}).get("city") or "")
        try:
            image = await asyncio.wait_for(asyncio.to_thread(image_for, cards[index], city), _IMAGE_WAIT)
        except Exception:
            _log.debug("events: image unavailable", exc_info=True)
    await leisure_hub._show_card(bot, cid, leisure_ui.event_card(cards, index), image, q)
