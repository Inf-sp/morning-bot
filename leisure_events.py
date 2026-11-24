"""«🎟️ События» в Досуге: одна лента карточек по дате — концерты (твои артисты и звёзды рядом), фестивали,
выставки, праздники, городские мероприятия и премьеры; ← n/N → по кругу, как «Премьеры».

Всё — из готовых кэшей разделов (концерты, афиша города, праздники, лента премьер), без AI; при открытии сеть нужна
только для фото артиста текущей карточки (Deezer, кэш месяц). Нет кэша раздела — его карточек просто нет.
"""
import asyncio
import logging
from datetime import timedelta

import config
import store

_log = logging.getLogger(__name__)

_HORIZON_DAYS = 30  # фестивали, выставки, праздники и город — на месяц, как «Праздники»
_CONCERT_DAYS = 365  # концерты — на год вперёд, как экран «Концерты»
_MINE_MAX, _STARS_MAX, _CITY_MAX, _HOLIDAYS_MAX = 5, 3, 12, 3
_WORLD_FAME = 8
_PHOTO_WAIT = 4  # фото артиста при открытии карточки ждём не дольше; нет — карточка текстом
_KIND_ORDER = ("holiday", "festival", "concert", "exhibition", "city", "premiere")


def _today(cid):
    import leisure_hub
    return leisure_hub._today_and_cc(cid)


def _concerts(cid, today, cc) -> list:
    """Ближайшие концерты своих артистов и до трёх концертов звёзд мировой величины рядом (оценка из кэша заметок)."""
    import leisure_concerts as concerts
    end = today + timedelta(days=_CONCERT_DAYS)
    mine = concerts.concert_items_between(concerts._concerts_cache_get(cid, cc) or [], today, end, _MINE_MAX)
    taken = {concerts._concert_identity_text(item["artist"]) for item in mine}
    notes = store._load(config.CONCERT_ARTIST_NOTES_KEY) or {}
    notes = notes if isinstance(notes, dict) else {}

    def note(item):
        return notes.get(concerts._concert_identity_text(item["artist"])) or {}

    popular = concerts.concert_items_between(concerts._popular_events_cache_get(cc, today) or [], today, end,
                                             concerts._POPULAR_EVENTS_LIMIT)
    stars = [item for item in popular if concerts._concert_identity_text(item["artist"]) not in taken
             and int(note(item).get("fame") or 0) >= _WORLD_FAME][:_STARS_MAX]
    cards = []
    for item, mine_flag in [*((item, True) for item in mine), *((item, False) for item in stars)]:
        place = ", ".join(part for part in (item.get("venue"), item.get("city")) if part)
        cards.append({"kind": "concert", "name": item["artist"], "url": item.get("url") or "",
                      "date": item["date"], "end": item["date"], "place": place, "mine": mine_flag,
                      "about": note(item).get("about") or "", "image": ""})
    return cards


def _city(cid, today) -> list:
    """Фестивали, выставки, праздники и городские мероприятия из афиши города (кэш «Праздников»), без сети."""
    import local_events
    found = local_events.agenda(cid, today, today + timedelta(days=_HORIZON_DAYS - 1), fetch=False)
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
    found = public_holidays.week_holidays(cc, today, today + timedelta(days=_HORIZON_DAYS - 1))
    return [{"kind": "holiday", "name": item["name"], "url": "", "date": item["date"], "end": item["date"],
             "about": item.get("about") or "", "image": ""} for item in found[:_HOLIDAYS_MAX]]


def _premieres(cid, today) -> list:
    import leisure_hub
    from ui import leisure as leisure_ui
    cards = []
    for kind, item in leisure_hub.premieres_feed(cid, today):
        name, url, meta, about = leisure_ui._week_item(item, kind)
        values = [str(value).lstrip("— ").strip() for value in meta if str(value or "").strip()]
        day = leisure_hub._release_day(item)
        cards.append({"kind": "premiere", "premiere": kind, "name": name or "", "url": url or "",
                      "date": day or today.isoformat(), "end": day or today.isoformat(), "meta": values,
                      "about": item.get("note") or about or "",
                      "image": str(item.get("cover" if kind == "music" else "poster") or "")})
    return cards


def feed(cid) -> list:
    """Лента «Событий»: все карточки по дате (уже идущие и вышедшие — как сегодня), без пустых имён."""
    today, cc = _today(cid)
    cards = []
    for name, build in (("concerts", lambda: _concerts(cid, today, cc)), ("city", lambda: _city(cid, today)),
                        ("holidays", lambda: _holidays(cid, today)), ("premieres", lambda: _premieres(cid, today))):
        try:
            cards += build()
        except Exception:
            _log.warning("events: %s unavailable cid=%s", name, cid, exc_info=True)
    seen, unique = set(), []
    for card in cards:
        key = (str(card["name"]).casefold(), card["date"])
        if card["name"] and key not in seen:
            seen.add(key)
            unique.append(card)
    iso = today.isoformat()
    return sorted(unique, key=lambda card: (max(card["date"], iso), _KIND_ORDER.index(card["kind"]),
                                            str(card["name"]).casefold()))


async def image_of(card) -> str:
    """Картинка карточки: фото артиста концерта (Deezer, кэш месяц), постер премьеры, картинка страницы события."""
    image = str(card.get("image") or "")
    if image.startswith("https://") or card.get("kind") != "concert":
        return image if image.startswith("https://") else ""
    import countries
    try:
        photo = await asyncio.wait_for(asyncio.to_thread(countries.artist_photo, card["name"]), _PHOTO_WAIT)
    except Exception:
        _log.debug("events: artist photo unavailable", exc_info=True)
        return ""
    return photo if str(photo or "").startswith("https://") else ""


async def send_events(bot, cid, arg="", q=None):
    """«🎟️ События»: карточка ленты с картинкой, ← n/N → по кругу (lz_ev_<n>) в том же сообщении."""
    import leisure_hub
    from ui import leisure as leisure_ui
    cards = await asyncio.to_thread(feed, cid)
    index = int(arg) % len(cards) if cards and str(arg).isdigit() else 0
    image = await image_of(cards[index]) if cards else ""
    await leisure_hub._show_card(bot, cid, leisure_ui.event_card(cards, index), image, q)
