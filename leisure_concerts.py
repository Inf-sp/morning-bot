"""Концерты: Ticketmaster, внешний поиск, кэш, уведомления и UI."""

import asyncio
import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from ui.constants import COUNTRY_EMOJI

import ai
import api_usage
import config
import leisure_books
import leisure_games
import leisure_movies
import provider_runtime
import settings
import store
import util
from ui import leisure as leisure_ui

_log = logging.getLogger(__name__)


def _music_home_only_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")]])


def _item_text(item):
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


def _ensure_artists(cid):
    """Возвращает список артистов пользователя (без авто-сида). Элемент может быть
    строкой или {"id":..., "value": строка} (после захода в удаление, см.
    store.ensure_list_ids_via) — нормализуем сразу здесь, единственной точке чтения."""
    return [_item_text(a) for a in store.get_list(config.FAVORITE_ARTISTS_KEY, cid) if _item_text(a)]

_TRIBUTE_MARKERS = ("tribute", "cover", "covers", "candlelight", "songs of", "the music of",
                    "performed by", "celebrating", "by candle", "symphonic", "reimagined",
                    "someone like", "a tribute", "in the style of", "plays the music", "experience:")

# Ticketmaster ограничивает запросы глобально. Один последовательный поток здесь
# намеренный: он позволяет остановить весь batch сразу после первого 429, прежде
# чем очередные артисты превратят ограничение в storm запросов.
_TICKETMASTER_CONCURRENCY = asyncio.Semaphore(1)
_TICKETMASTER_RETRY_DELAYS = (0.5, 1.5, 3.0)
_TICKETMASTER_ARTIST_BATCH_LIMIT = 8
_TICKETMASTER_REQUEST_INTERVAL = 0.5
_ticketmaster_next_request_at = 0.0
_EXTERNAL_ARTIST_LIMIT = 5
_EXTERNAL_CONCURRENCY = asyncio.Semaphore(2)
_POPULAR_EVENTS_CACHE_TTL = 31 * 86400
_POPULAR_EVENTS_CACHE_VERSION = 2
_POPULAR_EVENTS_LIMIT = 12


class TicketmasterRateLimitError(RuntimeError):
    """Ticketmaster rejected the request and all queued work must stop."""


def _ticketmaster_cooldown_remaining() -> int:
    return provider_runtime.cooldown_remaining("ticketmaster")


def _ticketmaster_get(url, params, timeout=15):
    """GET с retry на 5xx, но никогда на 429.

    Cooldown сохраняется в общем runtime-state, поэтому следующие задачи и
    реплики Railway видят rate limit до начала сетевого запроса.
    """
    import requests
    remaining = _ticketmaster_cooldown_remaining()
    if remaining:
        raise TicketmasterRateLimitError(f"ticketmaster cooldown {remaining}s")
    delays = (0,) + _TICKETMASTER_RETRY_DELAYS
    for i, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except Exception as e:
            api_usage.record_request("ticketmaster", ok=False, error=type(e).__name__)
            raise
        status = getattr(r, "status_code", None)
        api_usage.record_request("ticketmaster", ok=200 <= int(status or 0) < 300,
                                 status_code=status,
                                 error="" if 200 <= int(status or 0) < 300 else f"HTTP {status}",
                                 headers=r.headers)
        if status == 429:
            # record_request persists Retry-After as the shared cooldown.
            # Retrying a rate-limited request only wastes the remaining quota.
            raise TicketmasterRateLimitError("ticketmaster HTTP 429")
        if isinstance(status, int) and status >= 500:
            if i == len(delays) - 1:
                r.raise_for_status()
            continue
        r.raise_for_status()
        return r

def _ticketmaster_events_for_artist(artist, cc, start_dt="", end_dt="", size=3):
    if not config.TICKETMASTER_API_KEY:
        return []
    cache_key = f"{artist}|{cc}|{start_dt}|{end_dt}|{size}".lower()
    cached = util.ttl_get("ticketmaster", cache_key, 86400)
    if cached is not None:
        return cached
    params = {
        "apikey": config.TICKETMASTER_API_KEY,
        "keyword": artist,
        "countryCode": cc,
        "classificationName": "music",
        "size": size,
        "sort": "date,asc",
    }
    if start_dt:
        params["startDateTime"] = start_dt
    if end_dt:
        params["endDateTime"] = end_dt
    try:
        r = _ticketmaster_get("https://app.ticketmaster.com/discovery/v2/events.json", params)
    except TicketmasterRateLimitError:
        raise
    except Exception as e:
        _log.warning("ticketmaster events failed for artist=%s: %s", artist, e)
        return []
    events = []
    al = artist.lower()
    for e in r.json().get("_embedded", {}).get("events", []):
        name_l = e.get("name", "").lower()
        attractions = [att.get("name", "").lower()
                       for att in (e.get("_embedded", {}).get("attractions") or [])]
        attr_match = any(al in nm or nm in al for nm in attractions)
        if any(k in name_l for k in _TRIBUTE_MARKERS):
            continue
        if not (al in name_l or attr_match):
            continue
        e["_artist"] = artist
        events.append(e)
    return util.ttl_set("ticketmaster", cache_key, events)


async def _ticketmaster_fetch_throttled(fn, *args):
    """Ограничивает параллелизм запросов к Ticketmaster (_TICKETMASTER_CONCURRENCY),
    чтобы большие списки артистов не заваливали бесплатный тариф API 429-ми."""
    global _ticketmaster_next_request_at
    async with _TICKETMASTER_CONCURRENCY:
        delay = _ticketmaster_next_request_at - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        _ticketmaster_next_request_at = time.monotonic() + _TICKETMASTER_REQUEST_INTERVAL
        return await asyncio.to_thread(fn, *args)

async def _ticketmaster_events_many(artists, cc, start_dt="", end_dt="", size=3,
                                    limit=_TICKETMASTER_ARTIST_BATCH_LIMIT,
                                    include_checked=False):
    batches = []
    checked_artists = []
    for artist in artists[:limit]:
        if _ticketmaster_cooldown_remaining():
            break
        try:
            batch = await _ticketmaster_fetch_throttled(
                _ticketmaster_events_for_artist, artist, cc, start_dt, end_dt, size,
            )
        except TicketmasterRateLimitError:
            # Do not start the rest of this batch once the shared cooldown is
            # known. Already completed artist results remain useful.
            break
        except Exception:
            continue
        batches.append(batch)
        checked_artists.append(artist)
    found, seen_pairs = {}, set()
    for batch in batches:
        if isinstance(batch, Exception):
            continue
        for e in batch:
            artist = e.get("_artist", "")
            date = e.get("dates", {}).get("start", {}).get("localDate", "")
            city = ((e.get("_embedded", {}).get("venues") or [{}])[0].get("city") or {}).get("name", "")
            pair = (artist.lower(), date, city.lower())
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            found[e.get("id") or f"{artist}:{date}:{e.get('name', '')}"] = e
    events = sorted(
        found.values(),
        key=lambda e: e.get("dates", {}).get("start", {}).get("localDate") or "9999-99-99",
    )
    return (events, checked_artists) if include_checked else events


def _popular_events_cache_key(cc, period_start):
    return f"{str(cc or '').upper()}:{period_start.strftime('%Y-%m')}"


def _popular_events_cache_get(cc, period_start):
    data = store._load(config.POPULAR_MUSIC_EVENTS_CACHE_KEY) or {}
    entry = data.get(_popular_events_cache_key(cc, period_start)) or {}
    if (entry.get("version") != _POPULAR_EVENTS_CACHE_VERSION
            or time.time() - float(entry.get("ts") or 0) > _POPULAR_EVENTS_CACHE_TTL):
        return None
    events = entry.get("events")
    return list(events) if isinstance(events, list) else None


def _popular_events_cache_set(cc, period_start, events):
    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[_popular_events_cache_key(cc, period_start)] = {
            "version": _POPULAR_EVENTS_CACHE_VERSION,
            "ts": time.time(),
            "events": list(events or []),
        }
        return data, None

    store.mutate_kv(config.POPULAR_MUSIC_EVENTS_CACHE_KEY, mutate)


def _is_large_music_event(event):
    """Отбирает только крупные события, не выдавая случайный концерт за хит."""
    name = str(event.get("name") or "").casefold()
    attractions = event.get("_embedded", {}).get("attractions") or []
    venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
    venue_name = str(venue.get("name") or "").casefold()
    large_venue = ("arena", "stadium", "ziggo dome", "afas live", "ahoy",
                   "parc", "palais", "forum", "hallen", "park")
    return (
        any(marker in f" {name} " for marker in _FESTIVAL_MARKERS)
        or len(attractions) >= 2
        or any(marker in venue_name for marker in large_venue)
    )


def _ticketmaster_popular_music_events(cc, start_dt, end_dt):
    if not config.TICKETMASTER_API_KEY:
        return []
    params = {
        "apikey": config.TICKETMASTER_API_KEY,
        "countryCode": cc,
        "classificationName": "music",
        "startDateTime": start_dt,
        "endDateTime": end_dt,
        "size": 80,
        "sort": "relevance,desc",
    }
    try:
        response = _ticketmaster_get(
            "https://app.ticketmaster.com/discovery/v2/events.json", params)
    except TicketmasterRateLimitError:
        raise
    except Exception as error:
        _log.warning("ticketmaster popular events failed cc=%s: %s", cc, error)
        return []
    selected, seen = [], set()
    for event in response.json().get("_embedded", {}).get("events", []):
        name = str(event.get("name") or "").strip()
        if not name or any(marker in name.casefold() for marker in _TRIBUTE_MARKERS):
            continue
        if not _is_large_music_event(event):
            continue
        date = event.get("dates", {}).get("start", {}).get("localDate", "")
        venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
        city = str((venue.get("city") or {}).get("name") or "").strip()
        key = (name.casefold(), date, city.casefold())
        if key in seen:
            continue
        seen.add(key)
        selected.append(event)
        if len(selected) >= _POPULAR_EVENTS_LIMIT:
            break
    return selected


async def get_popular_music_events(cc, period_start, period_end):
    """Крупные события из месячного Ticketmaster-кэша в нужном окне дат."""
    cached = _popular_events_cache_get(cc, period_start)
    if cached is not None:
        return [event for event in cached if period_start.isoformat() <=
                event.get("dates", {}).get("start", {}).get("localDate", "") <= period_end.isoformat()]
    from datetime import timedelta
    refresh_end = period_start + timedelta(days=31)
    start_dt = f"{period_start.isoformat()}T00:00:00Z"
    end_dt = f"{refresh_end.isoformat()}T23:59:59Z"
    try:
        events = await _ticketmaster_fetch_throttled(
            _ticketmaster_popular_music_events, cc, start_dt, end_dt)
    except TicketmasterRateLimitError:
        return []
    _popular_events_cache_set(cc, period_start, events)
    return [event for event in events if period_start.isoformat() <=
            event.get("dates", {}).get("start", {}).get("localDate", "") <= period_end.isoformat()]

# ---------- Внешний поиск концертов (Tavily + Firecrawl + AI) ----------
# Ticketmaster — основной источник, но не полный: маленькие площадки, локальные
# промоутеры и часть европейских туров туда не попадают. Раз в 7 дней на артиста
# добираем события через веб-поиск (см. find_concerts/refresh_concerts_cache).
_ARTIST_EXTERNAL_TTL = 7 * 86400
_ARTIST_EXTERNAL_CACHE_VERSION = 2
_EXTERNAL_SEARCH_INFLIGHT = {}

_EXTERNAL_SOURCE_PRIORITY = {
    "official_site": 0,
    "venue": 1,
    "ticketmaster": 2,
    "ticket_service": 3,
    "other": 4,
}

_EXTERNAL_SOURCE_LABEL = {
    "official_site": "сайт исполнителя",
    "venue": "сайт площадки",
    "ticket_service": "билетный сервис",
    "other": "веб-поиск",
}

_NL_VENUE_DOMAINS = (
    "paradiso.nl", "melkweg.nl", "afaslive.nl", "ziggodome.nl",
    "013.nl", "tivolivredenburg.nl", "doornroosje.nl", "paard.nl",
    "effenaar.nl", "grenswerk.nl",
)

# География служит защитным инвариантом, а не способом расширить поиск. Если
# город однозначно известен, он имеет приоритет над ошибочным country_cc от AI.
_CITY_COUNTRY_CC = {
    "amsterdam": "NL", "rotterdam": "NL", "utrecht": "NL",
    "den haag": "NL", "the hague": "NL", "eindhoven": "NL",
    "tilburg": "NL", "nijmegen": "NL", "maastricht": "NL",
    "groningen": "NL", "arnhem": "NL", "haarlem": "NL",
    "biddinghuizen": "NL", "lievelde": "NL", "venlo": "NL",
    "leipzig": "DE", "hamburg": "DE", "berlin": "DE", "köln": "DE",
    "cologne": "DE", "düsseldorf": "DE", "munich": "DE", "münchen": "DE",
    "brussels": "BE", "brussel": "BE", "antwerp": "BE", "antwerpen": "BE",
    "ghent": "BE", "gent": "BE", "paris": "FR", "london": "GB",
}


def _normalized_city(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _verified_country_cc(city: str, declared_cc: str = "") -> str:
    """Страна площадки: известный город сильнее извлечённого AI-кода."""
    inferred = _CITY_COUNTRY_CC.get(_normalized_city(city))
    return inferred or str(declared_cc or "").strip().upper()


def _artist_cache_key(artist: str, cc: str) -> str:
    return f"{artist.strip().lower()}|{cc.upper()}"


def _external_events_cache_get(artist: str, cc: str):
    data = store._load(config.ARTIST_EXTERNAL_EVENTS_KEY) or {}
    entry = data.get(_artist_cache_key(artist, cc))
    if not entry or entry.get("version") != _ARTIST_EXTERNAL_CACHE_VERSION:
        return None
    if time.time() - int(entry.get("ts") or 0) > _ARTIST_EXTERNAL_TTL:
        return None
    return entry.get("events") or []


def _external_events_cache_set(artist: str, cc: str, events: list):
    cache_key = _artist_cache_key(artist, cc)

    def change(data):
        data[cache_key] = {
            "version": _ARTIST_EXTERNAL_CACHE_VERSION,
            "ts": int(time.time()),
            "events": events,
        }
        return data, None

    store.mutate_kv(config.ARTIST_EXTERNAL_EVENTS_KEY, change)


def _classify_external_source(url: str, artist: str) -> str:
    """Грубая эвристика источника по URL — используется и для приоритета отбора,
    и как подпись 'откуда' событие в UI (§ докс поиска концертов)."""
    low = (url or "").lower()
    artist_slug = re.sub(r"[^a-z0-9]+", "", artist.lower())
    host = re.sub(r"^https?://(www\.)?", "", low).split("/")[0]
    if artist_slug and artist_slug in re.sub(r"[^a-z0-9]+", "", host):
        return "official_site"
    if any(k in low for k in ("ticketmaster.", "eventim.", "songkick.", "bandsintown.")):
        return "ticket_service" if "ticketmaster." not in low else "ticketmaster"
    if any(domain in low for domain in _NL_VENUE_DOMAINS) or any(k in low for k in ("ahoy",
                              "arena", "stadium", "hall", "venue", "club", "theater", "theatre")):
        return "venue"
    return "other"


async def _collect_external_events_for_artist(artist: str, cc: str, cname: str):
    """Tavily ищет упоминания, Firecrawl достаёт содержимое найденных страниц,
    AI извлекает из каждой структурированные события. Только будущие концерты
    в cc и его соседях (см. _neighbor_ccs)."""
    import secure
    from datetime import datetime
    import research
    year = time.strftime('%Y')
    query = f'{artist} official tour dates {cname} {year}'
    try:
        results_batches = [await asyncio.to_thread(
            research.web_search, query, 5, scenario="concert_specific",
            allow_tavily=True, search_priority="tavily",
        )]
    except Exception as e:
        _log.warning("concerts external: tavily failed for artist=%s: %r", artist, e)
        return []
    urls = []
    for batch in results_batches:
        if isinstance(batch, Exception):
            continue
        for r in batch or []:
            u = (r.get("url") or "").strip()
            if u and u not in urls:
                urls.append(u)
    urls = urls[:8]
    if not urls:
        return []

    # firecrawl_search работает по запросу, а не по конкретному URL — для извлечения
    # содержимого уже найденных Tavily-страниц переиспользуем сами tavily-сниппеты
    # (content уже получен на шаге поиска) вместо повторного похода в Firecrawl per-URL,
    # плюс один точечный firecrawl-поиск по официальному сайту артиста для полноты.
    firecrawl_extra = []
    try:
        firecrawl_extra = await asyncio.to_thread(
            research.firecrawl_search, f"{artist} official tour dates {cname}", 3)
    except Exception as e:
        _log.warning("concerts external: firecrawl failed for artist=%s: %r", artist, e)

    context_parts = []
    for batch in results_batches:
        if isinstance(batch, Exception):
            continue
        for r in batch or []:
            content = (r.get("content") or "").strip()
            url = (r.get("url") or "").strip()
            if content and url:
                context_parts.append(f"URL: {url}\n{content[:500]}")
    for r in firecrawl_extra or []:
        content = (r.get("content") or "").strip()
        url = (r.get("url") or "").strip()
        if content and url:
            context_parts.append(f"URL: {url}\n{content[:500]}")
    if not context_parts:
        return []
    raw_context = "\n---\n".join(context_parts)[:8000]

    allowed_cc = [cc]
    today = datetime.now(config.TZ).date().isoformat()
    prompt = f"""Ты извлекаешь реальные концертные события артиста "{artist}" из текста веб-страниц.

{secure.wrap_untrusted(raw_context, "материалы поиска")}

Извлеки только БУДУЩИЕ концерты (дата не раньше {today}), которые проходят в одной из стран:
{', '.join(allowed_cc)}. Игнорируй прошедшие даты, другие страны, tribute-концерты и кавер-группы.
Добавляй событие ТОЛЬКО если дата, исполнитель и площадка прямо подтверждены текстом страницы —
не додумывай и не угадывай недостающие поля.

Верни JSON (без markdown):
{{"events": [{{"artist": "{artist}", "date": "YYYY-MM-DD", "time": "HH:MM или пусто",
"venue": "название площадки", "city": "город", "country_cc": "двухбуквенный код страны",
"event_type": "own или festival", "festival_name": "название фестиваля или пусто",
"event_url": "ссылка на страницу события", "ticket_url": "ссылка на билеты или пусто",
"source_url": "URL страницы-источника, откуда взято событие"}}]}}.
Ставь event_type=festival и заполняй festival_name только если название фестиваля прямо указано
в материале. Иначе ставь own и оставляй festival_name пустым."""
    try:
        d = await ai.allm_json(prompt, 1500, module="leisure_concerts", route=None)
    except Exception as e:
        _log.warning("concerts external: AI extraction failed for artist=%s: %r", artist, e)
        return []
    raw_events = d.get("events") if isinstance(d, dict) else None
    if not isinstance(raw_events, list):
        return []

    events = []
    for e in raw_events:
        if not isinstance(e, dict):
            continue
        date = str(e.get("date") or "").strip()
        venue = str(e.get("venue") or "").strip()
        city = str(e.get("city") or "").strip()
        country_cc = _verified_country_cc(city, e.get("country_cc"))
        source_url = str(e.get("source_url") or "").strip()
        if not (date and venue and source_url):
            continue  # не подтверждено страницей источника — не добавляем
        # Не подставляем выбранную страну, если источник не подтвердил географию.
        if country_cc != cc:
            continue
        if date < today:
            continue
        events.append({
            "artist": artist,
            "date": date,
            "time": str(e.get("time") or "").strip(),
            "venue": venue,
            "city": city,
            "country_cc": country_cc,
            "event_url": str(e.get("event_url") or source_url).strip(),
            "ticket_url": str(e.get("ticket_url") or "").strip(),
            "event_type": "festival" if e.get("event_type") == "festival" and e.get("festival_name") else "own",
            "festival_name": str(e.get("festival_name") or "").strip(),
            "source": _classify_external_source(source_url, artist),
        })
    return events


async def get_external_events_for_artist(artist: str, cc: str, cname: str = "", force: bool = False):
    """Внешние (не-Ticketmaster) события артиста с недельным глобальным кэшем.
    force=True — пропустить кэш (используется при добавлении нового артиста)."""
    cache_key = _artist_cache_key(artist, cc)
    if not force:
        cached = _external_events_cache_get(artist, cc)
        if cached is not None:
            return cached
    running = _EXTERNAL_SEARCH_INFLIGHT.get(cache_key)
    if running is not None:
        return await running

    async def _load():
        events = await _collect_external_events_for_artist(artist, cc, cname or cc)
        _external_events_cache_set(artist, cc, events)
        return events

    task = asyncio.create_task(_load())
    _EXTERNAL_SEARCH_INFLIGHT[cache_key] = task
    try:
        return await task
    finally:
        if _EXTERNAL_SEARCH_INFLIGHT.get(cache_key) is task:
            _EXTERNAL_SEARCH_INFLIGHT.pop(cache_key, None)


def _external_event_to_tm_shape(ev: dict) -> dict:
    """Оборачивает нормализованное внешнее событие в ту же форму, что отдаёт
    Ticketmaster (dates.start.localDate, _embedded.venues[0], _artist, url) —
    так весь существующий даунстрим-код (жанр/цена/рендер/дедуп по id) продолжает
    работать без изменений, не различая источник события."""
    artist = ev.get("artist", "")
    date = ev.get("date", "")
    city = ev.get("city", "")
    venue = ev.get("venue", "")
    source = ev.get("source", "other")
    event_type = ev.get("event_type") or "own"
    festival_name = str(ev.get("festival_name") or "").strip()
    return {
        "id": f"ext:{source}:{artist.lower()}:{date}:{city.lower()}",
        "name": festival_name if event_type == "festival" and festival_name else f"{artist} — {venue}".strip(" —"),
        "url": ev.get("ticket_url") or ev.get("event_url") or "",
        "dates": {"start": {"localDate": date, "localTime": ev.get("time", "")}},
        "_embedded": {
            "venues": [{
                "name": venue,
                "city": {"name": city},
                "country": {"countryCode": ev.get("country_cc", "")},
            }],
        },
        "_artist": artist,
        "_source": source,
        "_event_url": ev.get("event_url", ""),
        "_event_type": event_type,
        "_festival_name": festival_name,
    }


def _tm_event_key(e: dict) -> tuple:
    """Ключ дедупликации по нормализованному артисту/дате/городу/площадке —
    работает и на сырых Ticketmaster-событиях, и на обёрнутых внешних."""
    artist = e.get("_artist", "")
    date = e.get("dates", {}).get("start", {}).get("localDate", "")
    venue_obj = (e.get("_embedded", {}).get("venues") or [{}])[0]
    city = (venue_obj.get("city") or {}).get("name", "")
    venue = venue_obj.get("name", "")
    place = city.strip().lower() or venue.strip().lower()
    return (artist.strip().lower(), date.strip(), place)


def _event_country_cc(event: dict) -> str:
    venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
    city = (venue.get("city") or {}).get("name", "")
    declared = (venue.get("country") or {}).get("countryCode", "")
    return _verified_country_cc(city, declared)


def filter_concert_events(events: list, cc: str) -> list:
    """Оставляет только события с подтверждённой страной площадки."""
    target = str(cc or "").upper()
    return [event for event in events if _event_country_cc(event) == target]


def merge_concert_events(tm_events: list, external_events: list) -> list:
    """Объединяет Ticketmaster и внешние события (уже в TM-подобной форме), убирает
    дубли по (артист, дата, город), при конфликте оставляет источник
    с наивысшим приоритетом (официальный сайт → площадка → Ticketmaster →
    билетный сервис → прочее)."""
    def prio(e):
        source = e.get("_source", "ticketmaster")
        return _EXTERNAL_SOURCE_PRIORITY.get(source, 9)

    best = {}
    for e in list(tm_events) + [_external_event_to_tm_shape(ev) for ev in external_events]:
        key = _tm_event_key(e)
        if not key[0] or not key[1]:
            continue
        current = best.get(key)
        if current is None or prio(e) < prio(current):
            best[key] = e
    return sorted(best.values(), key=lambda e: e.get("dates", {}).get("start", {}).get("localDate") or "9999-99-99")


async def refresh_artist_external_events(artist: str, cc: str, cname: str = ""):
    """Запускает проверку внешних источников сразу для одного артиста — вызывается
    при добавлении нового артиста в любимые, не дожидаясь недельного цикла."""
    return await get_external_events_for_artist(artist, cc, cname, force=True)


_GENRE_TRANSLATIONS = {
    "rock": "Рок", "pop": "Поп", "hip-hop/rap": "Хип-хоп", "hip hop": "Хип-хоп",
    "electronic": "Электроника", "dance/electronic": "Электроника", "jazz": "Джаз",
    "classical": "Классика", "r&b": "R&B", "country": "Кантри", "metal": "Метал",
    "reggae": "Регги", "blues": "Блюз", "folk": "Фолк", "world": "Мировая музыка",
    "alternative": "Альтернатива", "indie": "Инди", "punk": "Панк", "other": "",
    "undefined": "",
}

def _concert_genre(e):
    """Жанр из Ticketmaster classifications (genre/subGenre); '' если не найден или не музыка."""
    for c in (e.get("classifications") or []):
        genre = (c.get("genre") or {}).get("name", "")
        sub = (c.get("subGenre") or {}).get("name", "")
        label = sub if sub and sub.lower() not in ("other", "undefined") else genre
        if not label or label.lower() in ("other", "undefined"):
            continue
        return _GENRE_TRANSLATIONS.get(label.lower(), label)
    return ""

def _concert_min_price(e):
    """Минимальная цена из Ticketmaster priceRanges, отформатированная как '25 EUR'; '' если нет данных."""
    ranges = e.get("priceRanges") or []
    mins = [r.get("min") for r in ranges if isinstance(r.get("min"), (int, float))]
    if not mins:
        return ""
    best = min(mins)
    currency = (ranges[0].get("currency") or "").upper()
    amount = int(best) if best == int(best) else round(best, 2)
    return f"от {amount} {currency}".strip()


_FESTIVAL_MARKERS = (
    "festival", " fest", "fest ", "pinkpop", "lowlands", "paaspop",
    "down the rabbit hole", "best kept secret", "roadburn", "defqon",
    "mysteryland", "north sea jazz", "zwarte cross",
)


def _clean_event_label(value):
    """Убирает билетный суффикс из названия фестиваля для пользовательского UI."""
    label = " ".join(str(value or "").split()).strip()
    return re.sub(r"\s*[-–—]\s*(?:festival\s*)?ticket(?:s)?\s*$", "", label, flags=re.IGNORECASE).strip()


def _concert_context(e):
    """Возвращает пользовательский формат события без догадок через AI."""
    explicit_type = str(e.get("_event_type") or "").strip().lower()
    explicit_name = _clean_event_label(e.get("_festival_name"))
    if explicit_type == "festival" and explicit_name:
        return f"Фестиваль · {explicit_name}"

    artist = " ".join(str(e.get("_artist") or "").split()).strip()
    event_name = _clean_event_label(e.get("name"))
    event_low = event_name.casefold()
    artist_low = artist.casefold()
    attractions = [
        " ".join(str(item.get("name") or "").split()).strip()
        for item in (e.get("_embedded", {}).get("attractions") or [])
        if str(item.get("name") or "").strip()
    ]
    is_named_festival = any(marker in f" {event_low} " for marker in _FESTIVAL_MARKERS)
    is_umbrella_event = bool(event_name and artist and artist_low not in event_low and len(attractions) > 1)
    if is_named_festival or is_umbrella_event:
        return f"Фестиваль · {event_name}"
    return "Сольный концерт"

def _concert_place_name(name, cc=""):
    cc = (cc or "").upper()
    by_cc = {
        "NL": "Нидерландах",
        "BE": "Бельгии",
        "DE": "Германии",
        "FR": "Франции",
        "GB": "Великобритании",
        "ES": "Испании",
        "IT": "Италии",
        "AT": "Австрии",
        "CH": "Швейцарии",
        "PL": "Польше",
        "SE": "Швеции",
        "DK": "Дании",
        "PT": "Португалии",
    }
    if cc in by_cc:
        return by_cc[cc]
    low = str(name or "").strip().lower()
    if low in ("нидерланды", "netherlands", "nl"):
        return "Нидерландах"
    return str(name or "твоей стране").strip()

_CONCERTS_CACHE_TTL = 31 * 86400
_CONCERTS_CACHE_VERSION = 6
_ARTIST_CONCERT_CHECKS_VERSION = 2
_ARTIST_CONCERT_CHECK_INTERVAL = 31 * 86400


def _concerts_cache_get(cid, cc):
    """Месячный список концертов; прошедшая афиша не считается актуальной."""
    entry = store._load(config.CONCERTS_CACHE_KEY).get(str(cid))
    if (
        not entry
        or entry.get("version") != _CONCERTS_CACHE_VERSION
        or entry.get("cc") != cc
    ):
        return None
    import time
    if time.time() - entry.get("ts", 0) > _CONCERTS_CACHE_TTL:
        return None
    from datetime import datetime
    today = datetime.now(config.TZ).date().isoformat()
    if any(str(event.get("dates", {}).get("start", {}).get("localDate") or "") < today
           for event in entry.get("events", []) if isinstance(event, dict)):
        return None
    return filter_concert_events(entry.get("events", []), cc)


def cached_concerts(cid, cc):
    """Подтверждённая недельная афиша из кэша без обращения к внешним сервисам."""
    return _concerts_cache_get(cid, str(cc or "").upper()) or []


def _concerts_cache_set(cid, cc, events):
    import time
    d = store._load(config.CONCERTS_CACHE_KEY)
    d[str(cid)] = {
        "version": _CONCERTS_CACHE_VERSION,
        "ts": time.time(),
        "cc": cc,
        "events": filter_concert_events(events, cc),
    }
    store._save(config.CONCERTS_CACHE_KEY, d)


def invalidate_user_concerts_cache(cid):
    """Сбрасывает только сводную подборку пользователя.

    Кэши конкретных исполнителей остаются: при следующей сборке старые артисты
    переиспользуют свежие данные, а новый артист запрашивается отдельно.
    """
    data = store._load(config.CONCERTS_CACHE_KEY)
    if str(cid) not in data:
        return False
    data.pop(str(cid), None)
    store._save(config.CONCERTS_CACHE_KEY, data)
    return True


def _artist_check_bucket(cid, cc):
    data = store._load(config.CONCERT_ARTIST_CHECKS_KEY) or {}
    user = data.get(str(cid)) or {}
    return dict(user.get(str(cc or "").upper()) or {})


def _artist_check_key(artist):
    return _item_text(artist).casefold()


def _event_date(event):
    return str((event or {}).get("dates", {}).get("start", {}).get("localDate") or "")


def _artist_is_due(cid, artist, cc, *, force=False, now=None):
    """Новый артист проверяется сразу; без событий — раз в месяц.

    Если концерт уже найден, повторно артиста не запрашиваем, пока не пройдёт
    последний известный концерт в выбранной стране.
    """
    if force:
        return True
    entry = _artist_check_bucket(cid, cc).get(_artist_check_key(artist)) or {}
    if entry.get("version") != _ARTIST_CONCERT_CHECKS_VERSION:
        return True
    from datetime import datetime
    today = (now or datetime.now(config.TZ).date()).isoformat()
    known_dates = [_event_date(event) for event in entry.get("events", []) if _event_date(event)]
    if known_dates:
        return not any(date >= today for date in known_dates)
    return time.time() - float(entry.get("checked_at") or 0) >= _ARTIST_CONCERT_CHECK_INTERVAL


def _stored_artist_events(cid, artists, cc):
    from datetime import datetime
    today = datetime.now(config.TZ).date().isoformat()
    wanted = {_artist_check_key(artist) for artist in artists}
    events = []
    for key, entry in _artist_check_bucket(cid, cc).items():
        if key not in wanted:
            continue
        events.extend(event for event in entry.get("events", [])
                      if isinstance(event, dict) and _event_date(event) >= today)
    return events


def _save_artist_check_results(cid, artists, cc, events):
    by_artist = {_artist_check_key(artist): [] for artist in artists}
    for event in events:
        key = _artist_check_key(event.get("_artist"))
        if key in by_artist:
            by_artist[key].append(event)
    now = int(time.time())

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        user = data.setdefault(str(cid), {})
        bucket = user.setdefault(str(cc or "").upper(), {})
        for artist in artists:
            key = _artist_check_key(artist)
            bucket[key] = {
                "version": _ARTIST_CONCERT_CHECKS_VERSION,
                "artist": _item_text(artist),
                "checked_at": now,
                "events": by_artist.get(key, []),
            }
        return data, None

    store.mutate_kv(config.CONCERT_ARTIST_CHECKS_KEY, mutate)


async def _fetch_concerts(artists, cc, cname, *, explicit_artist_search=False,
                          cid=None, force_artists=()):
    """Ticketmaster для обычной афиши; web-search только для явного поиска артиста."""
    from datetime import datetime, timedelta
    now = datetime.now(config.TZ)
    date_from = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (now + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")  # 1 год вперёд

    # Берём максимально возможную страницу Ticketmaster: короткая выдача не должна
    # прятать даты дальнего конца годового горизонта.
    artists = list(dict.fromkeys(_item_text(artist) for artist in artists if _item_text(artist)))
    force = {_artist_check_key(artist) for artist in force_artists}
    due_artists = artists if cid is None else [
        artist for artist in artists
        if _artist_is_due(cid, artist, cc, force=_artist_check_key(artist) in force)
    ]
    retained_events = [] if cid is None else _stored_artist_events(cid, artists, cc)
    if not due_artists:
        return filter_concert_events(retained_events, cc)
    # Один внутренний batch остаётся небольшим, но за один пользовательский
    # refresh проходим всю очередь. Иначе девятый любимый артист не попадал в
    # текущую афишу и мог ждать следующего недельного запуска.
    tm_events = []
    checked_artists = []
    for start in range(0, len(due_artists), _TICKETMASTER_ARTIST_BATCH_LIMIT):
        batch_artists = due_artists[start:start + _TICKETMASTER_ARTIST_BATCH_LIMIT]
        ticketmaster_result = await _ticketmaster_events_many(
            batch_artists, cc, start_dt=date_from, end_dt=date_to, size=200,
            limit=_TICKETMASTER_ARTIST_BATCH_LIMIT, include_checked=True,
        )
        # Совместимость с небольшими тестовыми/локальными адаптерами: в рабочем
        # пути функция возвращает и события, и действительно проверенных артистов.
        if isinstance(ticketmaster_result, tuple):
            batch_events, actually_checked = ticketmaster_result
        else:
            batch_events, actually_checked = ticketmaster_result, batch_artists
        tm_events.extend(batch_events or [])
        checked_artists.extend(actually_checked or [])
        if len(actually_checked or []) < len(batch_artists):
            break
    # External search is a bounded last fallback for unresolved artists.
    found_artists = {
        _item_text(event.get("_artist")).casefold()
        for event in tm_events
        if isinstance(event, dict) and _item_text(event.get("_artist"))
    }
    unresolved = [artist for artist in checked_artists
                  if _item_text(artist).casefold() not in found_artists]

    async def external_for_artist(artist):
        async with _EXTERNAL_CONCURRENCY:
            return await get_external_events_for_artist(artist, cc, cname)

    external_batches = await asyncio.gather(
        *(external_for_artist(artist) for artist in unresolved[:_EXTERNAL_ARTIST_LIMIT]),
        return_exceptions=True,
    )
    external_events = []
    for batch in external_batches:
        if isinstance(batch, Exception):
            continue
        external_events.extend(batch or [])
    fresh_events = filter_concert_events(merge_concert_events(tm_events, external_events), cc)
    if cid is not None:
        _save_artist_check_results(cid, checked_artists, cc, fresh_events)
    return filter_concert_events(merge_concert_events([*retained_events, *fresh_events], []), cc)


async def refresh_concerts_cache(cid):
    """Обновляет только артистов, которым пора сверить афишу.

    Пятничный job по-прежнему готовит уведомление, но Ticketmaster для каждого
    артиста используется максимум раз в месяц, либо после его известного концерта.
    перед уведомлением «Афиша недели», чтобы само уведомление и последующие «Концерты» не ждали API.
    Возвращает короткий результат для планового задания."""
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    cname = s.get("country") or "твоя страна"
    from datetime import datetime, timedelta
    period_start = datetime.now(config.TZ).date()
    popular_events = await get_popular_music_events(
        cc, period_start, period_start + timedelta(days=31))
    artists = _ensure_artists(cid)
    if not artists:
        invalidate_user_concerts_cache(cid)
        return {"status": "no_artists", "artists": 0, "events": 0,
                "popular_events": len(popular_events)}
    if not config.TICKETMASTER_API_KEY:
        return {"status": "unavailable", "artists": len(artists), "events": 0,
                "popular_events": len(popular_events)}
    events = await _fetch_concerts(artists, cc, cname, cid=cid)
    _concerts_cache_set(cid, cc, events)
    return {
        "status": "updated",
        "artists": len(artists),
        "events": len(events),
        "popular_events": len(popular_events),
    }


async def refresh_new_artist_concerts(cid, artist, cc=None, cname=None):
    """Проверяет только что добавленного артиста сразу и обновляет общую афишу."""
    s = store.get_settings(cid)
    cc = str(cc or s.get("cc") or "NL").upper()
    cname = cname or s.get("country") or "твоя страна"
    events = await _fetch_concerts(
        [artist], cc, cname, cid=cid, force_artists=[artist],
    )
    cached = _concerts_cache_get(cid, cc) or []
    artist_key = _artist_check_key(artist)
    other_events = [event for event in cached
                    if _artist_check_key(event.get("_artist")) != artist_key]
    _concerts_cache_set(cid, cc, merge_concert_events([*other_events, *events], []))
    return events


_SEEN_CONCERTS_LIMIT = 300  # ограничение размера истории «виденных» concert ID на пользователя


def _concert_event_id(e):
    """Стабильный ID концерта для сравнения «уже видел / новый»: нативный id источника,
    иначе (артист, дата, город) — тот же ключ, которым события дедуплицируются в _ticketmaster_events_many."""
    if e.get("id"):
        return str(e["id"])
    artist = e.get("_artist", "")
    date = e.get("dates", {}).get("start", {}).get("localDate", "")
    city = ((e.get("_embedded", {}).get("venues") or [{}])[0].get("city") or {}).get("name", "")
    return f"{artist.lower()}:{date}:{city.lower()}"


def _concert_date_unix(local_date):
    """Return local noon for an ISO event date, or ``None`` when it is invalid.

    Concert sources currently confirm a date but not always a usable start
    time.  Noon in the configured local timezone keeps that date stable when
    Telegram renders the RichTextDateTime entity in the recipient's timezone.
    """
    from datetime import datetime

    try:
        return int(
            datetime.strptime(str(local_date), "%Y-%m-%d")
            .replace(hour=12, tzinfo=config.TZ)
            .timestamp()
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _seen_concerts_has_history(cid):
    return str(cid) in store._load(config.SEEN_CONCERTS_KEY)


def _seen_concerts_get(cid):
    return set(store._load(config.SEEN_CONCERTS_KEY).get(str(cid), []))


def _seen_concerts_add(cid, ids):
    d = store._load(config.SEEN_CONCERTS_KEY)
    merged = list(dict.fromkeys([*d.get(str(cid), []), *ids]))
    d[str(cid)] = merged[-_SEEN_CONCERTS_LIMIT:]
    store._save(config.SEEN_CONCERTS_KEY, d)


async def _fetch_favorite_events(cid):
    """Концерты избранных артистов пользователя в его стране: сперва недельный кэш (его прогревает
    job_refresh_concerts_cache по вс перед этой же проверкой), иначе живой запрос. [] если артистов/ключа нет."""
    artists = _ensure_artists(cid)
    if not artists or not config.TICKETMASTER_API_KEY:
        return []
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    cname = s.get("country") or "твоя страна"
    cached = _concerts_cache_get(cid, cc)
    events = cached if cached is not None else await _fetch_concerts(artists, cc, cname, cid=cid)

    from datetime import datetime
    today_str = datetime.now(config.TZ).date().isoformat()
    return [e for e in events
            if e.get("dates", {}).get("start", {}).get("localDate", "9999") >= today_str]


async def find_new_favorite_concerts(cid):
    """Сравнивает свежие концерты избранных артистов с уже виденными и возвращает только новые
    (без побочных эффектов — запись в seen делает вызывающий код после успешной отправки)."""
    events = await _fetch_favorite_events(cid)
    seen = _seen_concerts_get(cid)
    return [e for e in events if _concert_event_id(e) not in seen]


async def _build_new_concerts_msg(cid):
    """Новые концерты любимых артистов -> MessageSpec, либо None если показывать нечего.
    Молчит, если ничего нового не появилось с прошлой проверки. При первом включении
    (нет истории seen) тихо запоминает текущие концерты, ничего не шлёт — иначе первый
    запуск продублировал бы всю афишу как «новое»."""
    if not _seen_concerts_has_history(cid):
        events = await _fetch_favorite_events(cid)
        _seen_concerts_add(cid, [_concert_event_id(e) for e in events])
        return None

    new_events = await find_new_favorite_concerts(cid)
    if not new_events:
        return None
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    flag = util.flag_from_cc(cc)

    from util import _MONTHS

    def _fmt_date(ds):
        try:
            y, m, dd = ds.split("-")
            return f"{int(dd)} {_MONTHS[int(m)-1]} {y}"
        except Exception:
            return ds

    rows_data = []
    for e in new_events:
        date = e.get("dates", {}).get("start", {}).get("localDate", "")
        city = ((e.get("_embedded", {}).get("venues") or [{}])[0].get("city") or {}).get("name", "")
        rows_data.append({
            "artist": e.get("_artist", ""),
            "context": _concert_context(e),
            "flag": flag,
            "place": city,
            "genre": _concert_genre(e),
            "price": _concert_min_price(e),
            "date": _fmt_date(date) if date else "",
            "date_unix": _concert_date_unix(date),
            "url": e.get("url", ""),
        })

    msg = leisure_ui.concerts_list("Новые концерты твоих артистов", rows_data)
    _seen_concerts_add(cid, [_concert_event_id(e) for e in new_events])
    return msg


_CONCERT_CC_MAP = {
    "nl": ("NL", COUNTRY_EMOJI["nl"], "Нидерланды"),
    "be": ("BE", COUNTRY_EMOJI["be"], "Бельгия"),
    "de": ("DE", COUNTRY_EMOJI["de"], "Германия"),
    "fr": ("FR", COUNTRY_EMOJI["fr"], "Франция"),
    "gb": ("GB", COUNTRY_EMOJI["gb"], "Великобритания"),
    "es": ("ES", COUNTRY_EMOJI["es"], "Испания"),
    "it": ("IT", COUNTRY_EMOJI["it"], "Италия"),
    "at": ("AT", COUNTRY_EMOJI["at"], "Австрия"),
    "ch": ("CH", COUNTRY_EMOJI["ch"], "Швейцария"),
    "pl": ("PL", COUNTRY_EMOJI["pl"], "Польша"),
    "se": ("SE", COUNTRY_EMOJI["se"], "Швеция"),
    "dk": ("DK", COUNTRY_EMOJI["dk"], "Дания"),
    "pt": ("PT", COUNTRY_EMOJI["pt"], "Португалия"),
}


def _concert_country_name(cc: str, fallback: str = "") -> str:
    """Полное локализованное название страны для UI, даже если в профиле лежит код."""
    normalized_cc = str(cc or "").upper()
    for code, _flag, name in _CONCERT_CC_MAP.values():
        if code == normalized_cc:
            return name
    return str(fallback or normalized_cc or "твоя страна").strip()


def _concert_country_label(cc: str, fallback: str = "") -> str:
    name = _concert_country_name(cc, fallback)
    flag = util.flag_from_cc(str(cc or "").upper())
    return f"{flag} {name}".strip()

# Реальные географические соседи (сухопутная граница/ближайший регион), ограничены
# набором стран выше — используется для "соседние регионы" в поиске концертов
# (§ внешний поиск по артисту), не для смены страны кнопкой.
_NEIGHBOR_CC = {
    "NL": ["BE", "DE"],
    "BE": ["NL", "FR", "DE"],
    "DE": ["NL", "BE", "FR", "CH", "AT", "PL", "DK"],
    "FR": ["BE", "DE", "CH", "IT", "ES", "GB"],
    "GB": ["FR"],
    "ES": ["FR", "PT"],
    "IT": ["FR", "CH", "AT"],
    "AT": ["DE", "CH", "IT"],
    "CH": ["DE", "FR", "IT", "AT"],
    "PL": ["DE"],
    "SE": ["DK"],
    "DK": ["DE", "SE"],
    "PT": ["ES"],
}


def _neighbor_ccs(cc: str) -> list:
    """Соседние страны для cc из _CONCERT_CC_MAP; [] если cc вне этого набора."""
    return list(_NEIGHBOR_CC.get((cc or "").upper(), []))


async def send_concerts_home(bot, cid, q=None):
    """Open the actual nearest-events result, not a second introductory screen."""
    await find_concerts(bot, cid, "home")


async def prompt_artist_search(bot, cid):
    store.pending_input[str(cid)] = "concert_artist_search"
    await bot.send_message(
        chat_id=cid,
        text="🔍 Найти артиста\n\nНапиши имя исполнителя — проверю концерты на ближайший год.",
        reply_markup=_music_home_only_kb(),
        transient=True,
    )


async def find_artist_concerts(bot, cid, artist):
    artist = " ".join(str(artist or "").split()).strip()[:100]
    if not artist:
        await prompt_artist_search(bot, cid)
        return
    await find_concerts(bot, cid, "home", artists_override=[artist])


async def find_concerts(bot, cid, mode="home", artists_override=None):
    s = store.get_settings(cid)
    home_cc = (s.get("cc") or "NL").upper()
    home_flag = util.flag_from_cc(home_cc)
    home_name = _concert_country_name(home_cc, s.get("country") or "")
    if mode in _CONCERT_CC_MAP:
        cc, flag, cname = _CONCERT_CC_MAP[mode]
    else:
        cc, flag, cname = home_cc, home_flag, home_name
    artists = list(artists_override or _ensure_artists(cid))

    rows = []
    if not artists:
        rows.append([InlineKeyboardButton("🆕 Добавить артиста", callback_data="as_loveadd_artists")])
    rows.append([InlineKeyboardButton(_concert_country_label(cc, cname), callback_data="a_concerts_pick")])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_music"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    kb = InlineKeyboardMarkup(rows)

    if not artists and not artists_override:
        await bot.send_message(
            chat_id=cid,
            text=(f"🎫 Концерты · {cname}\n\nЛюбимых артистов пока нет.\n\n"
                  "Добавь исполнителя, чтобы я проверял его будущие выступления."),
            reply_markup=kb,
        )
        return

    if not config.TICKETMASTER_API_KEY:
        await bot.send_message(
            chat_id=cid,
            text=(f"🎫 Концерты · {cname}\n\nПока не удалось проверить ближайшие концерты. "
                  "Попробуй поискать артиста или выбери другую страну."),
            reply_markup=kb,
        )
        return

    from util import _MONTHS
    from datetime import datetime

    events = _concerts_cache_get(cid, cc)
    if events is None:
        events = await _fetch_concerts(
            artists, cc, cname, explicit_artist_search=bool(artists_override), cid=cid,
            force_artists=artists_override or (),
        )
        _concerts_cache_set(cid, cc, events)

    def _fmt_date(ds):
        try:
            y, m, dd = ds.split("-")
            return f"{int(dd)} {_MONTHS[int(m)-1]} {y}"
        except Exception:
            return ds

    place_label = f"Концерты · {cname}"
    today_str = datetime.now(config.TZ).date().isoformat()
    seen_artist_events = set()
    rows_data = []
    for e in events:
        artist = e.get("_artist", "")
        date = e.get("dates", {}).get("start", {}).get("localDate", "")
        if date and date < today_str:
            continue
        city = ((e.get("_embedded", {}).get("venues") or [{}])[0].get("city") or {}).get("name", "")
        dedup_key = (artist.lower(), date, city.lower())
        if dedup_key in seen_artist_events:
            continue
        seen_artist_events.add(dedup_key)

        place = city
        rows_data.append({
            "artist": artist,
            "context": _concert_context(e),
            "flag": flag,
            "place": place,
            "genre": _concert_genre(e),
            "price": _concert_min_price(e),
            "date": _fmt_date(date) if date else "",
            "date_unix": _concert_date_unix(date),
            "url": e.get("url", ""),
        })

    empty_hint = (
        f"Пока не нашёл концертов {artists[0]} в этой стране.\n\nМожно сменить страну или поискать другого исполнителя."
        if artists_override else
        "Пока не нашёл ближайших концертов любимых артистов.\n\nМожно поискать другого исполнителя или сменить страну."
    )
    msg = leisure_ui.concerts_list(
        place_label,
        rows_data,
        empty_hint=empty_hint,
    )
    store.last_source[str(cid)] = "Музыка · Концерты"
    store.last_answer[str(cid)] = msg.text
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=kb,
        disable_web_page_preview=True,
    )




async def _build_weekly_events_msg(cid):
    """Компактная пятничная подборка из готовых премьерных кэшей."""
    from datetime import datetime, timedelta

    s = store.get_settings(cid)
    cc = (s.get("cc") or config.DEFAULT_CITY.get("cc", "")).upper()
    period_start = datetime.now(config.TZ).date()
    period_end = period_start + timedelta(days=31)
    today_str = period_start.isoformat()
    date_to_str = period_end.isoformat()

    # Концерты уже прогреты отдельным пятничным заданием. В момент рассылки не
    # запускаем Ticketmaster заново: объединяем персональный и месячный кэши.
    personal_events = _concerts_cache_get(cid, cc) or []
    popular_events = _popular_events_cache_get(cc, period_start) or []
    concert_items, existing = [], set()
    for event in [*personal_events, *popular_events]:
        date_str = event.get("dates", {}).get("start", {}).get("localDate", "")
        if not (today_str <= date_str <= date_to_str):
            continue
        venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
        city = str((venue.get("city") or {}).get("name") or "").strip()
        title = str(event.get("_artist") or event.get("name") or "").strip()
        key = (title.casefold(), date_str, city.casefold())
        if not title or key in existing:
            continue
        existing.add(key)
        concert_items.append({
            "title": title,
            "date": date_str,
            "genre": _concert_genre(event),
            "url": str(event.get("url") or "").strip(),
        })
    concert_items.sort(key=lambda item: item.get("date") or "9999-99-99")

    results = await asyncio.gather(
        leisure_movies.get_movie_premieres(cid),
        leisure_books.get_book_premieres(),
        leisure_games.get_game_premieres(cid),
        return_exceptions=True,
    )
    labels = ("movie", "book", "game")
    loaded = {}
    for label, result in zip(labels, results):
        if isinstance(result, Exception):
            _log.warning("weekly events %s cache failed: %r", label, result)
            loaded[label] = []
        else:
            loaded[label] = list(result or [])

    return leisure_ui.weekly_events_card(
        loaded["movie"], concert_items, loaded["book"], loaded["game"],
    )


async def send_weekend_events(bot, cid):
    """Пятница 10:00 — одно сообщение с премьерами и переходами в категории."""
    msg = await _build_weekly_events_msg(cid)
    kb = settings.notification_markup("weekend_events", [
        [InlineKeyboardButton("🎬 Кино", callback_data="movie_premieres"),
         InlineKeyboardButton("🎫 Концерты", callback_data="a_concerts_find")],
        [InlineKeyboardButton("📚 Книги", callback_data="book_premieres"),
         InlineKeyboardButton("👾 Игры", callback_data="vg_premieres")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def concert_pick_country(bot, cid):
    countries = [
        (key, name, _concert_country_label(code, name))
        for key, (code, _flag, name) in _CONCERT_CC_MAP.items()
    ]
    buttons = [
        InlineKeyboardButton(label, callback_data=f"a_concerts_{cc}")
        for cc, _name, label in sorted(countries, key=lambda x: x[1])
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="a_concerts_find"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    await bot.send_message(chat_id=cid, text="🌍 Выбери страну для поиска концертов:",
                           reply_markup=InlineKeyboardMarkup(rows))
