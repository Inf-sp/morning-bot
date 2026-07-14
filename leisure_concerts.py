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
import research
import secure
import store
import util
from ui import leisure as leisure_ui

_log = logging.getLogger(__name__)


def _item_text(item):
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


def _movie_service_language(_cid=None):
    return "ru-RU"
def _ensure_artists(cid):
    """Возвращает список артистов пользователя (без авто-сида). Элемент может быть
    строкой или {"id":..., "value": строка} (после захода в удаление, см.
    store.ensure_list_ids_via) — нормализуем сразу здесь, единственной точке чтения."""
    return [_item_text(a) for a in store.get_list(config.ARTISTS_KEY, cid) if _item_text(a)]

_TRIBUTE_MARKERS = ("tribute", "cover", "covers", "candlelight", "songs of", "the music of",
                    "performed by", "celebrating", "by candle", "symphonic", "reimagined",
                    "someone like", "a tribute", "in the style of", "plays the music", "experience:")

# Ticketmaster на бесплатном тарифе держит ~5 запросов/сек — без ограничения параллелизма
# и retry список из 30+ артистов заваливает API 429-ми, которые тихо трактуются как "нет концертов".
_TICKETMASTER_CONCURRENCY = asyncio.Semaphore(5)
_TICKETMASTER_RETRY_DELAYS = (0.5, 1.5, 3.0)

def _ticketmaster_get(url, params, timeout=15):
    """GET с retry только на 429/5xx (экспоненциальный backoff) — сетевые ошибки,
    таймауты и прочие сбои не ретраим, чтобы не блокировать поток попытками, которые не помогут."""
    import requests
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
        if status == 429 or (isinstance(status, int) and status >= 500):
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
    async with _TICKETMASTER_CONCURRENCY:
        return await asyncio.to_thread(fn, *args)

async def _ticketmaster_events_many(artists, cc, start_dt="", end_dt="", size=3, limit=40):
    tasks = [
        _ticketmaster_fetch_throttled(_ticketmaster_events_for_artist, artist, cc, start_dt, end_dt, size)
        for artist in artists[:limit]
    ]
    batches = await asyncio.gather(*tasks, return_exceptions=True)
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
    return sorted(found.values(), key=lambda e: e.get("dates", {}).get("start", {}).get("localDate") or "9999-99-99")

# ---------- Внешний поиск концертов (Tavily + Firecrawl + AI) ----------
# Ticketmaster — основной источник, но не полный: маленькие площадки, локальные
# промоутеры и часть европейских туров туда не попадают. Раз в 7 дней на артиста
# добираем события через веб-поиск (см. find_concerts/refresh_concerts_cache).
_ARTIST_EXTERNAL_TTL = 7 * 86400

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
    if not entry:
        return None
    if time.time() - int(entry.get("ts") or 0) > _ARTIST_EXTERNAL_TTL:
        return None
    return entry.get("events") or []


def _external_events_cache_set(artist: str, cc: str, events: list):
    data = store._load(config.ARTIST_EXTERNAL_EVENTS_KEY) or {}
    data[_artist_cache_key(artist, cc)] = {"ts": int(time.time()), "events": events}
    store._save(config.ARTIST_EXTERNAL_EVENTS_KEY, data)


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
    queries = [
        f'{artist} official tour dates {cname} {year}',
        f'{artist} Netherlands {year} ' + " ".join(f"site:{domain}" for domain in _NL_VENUE_DOMAINS),
        f'{artist} concert {cname} {year}',
    ]
    try:
        results_batches = await asyncio.gather(
            *[asyncio.to_thread(research.tavily_search, q, 5) for q in queries],
            return_exceptions=True,
        )
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
"event_url": "ссылка на страницу события", "ticket_url": "ссылка на билеты или пусто",
"source_url": "URL страницы-источника, откуда взято событие"}}]}}"""
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
            "source": _classify_external_source(source_url, artist),
        })
    return events


async def get_external_events_for_artist(artist: str, cc: str, cname: str = "", force: bool = False):
    """Внешние (не-Ticketmaster) события артиста с недельным глобальным кэшем.
    force=True — пропустить кэш (используется при добавлении нового артиста)."""
    if not force:
        cached = _external_events_cache_get(artist, cc)
        if cached is not None:
            return cached
    events = await _collect_external_events_for_artist(artist, cc, cname or cc)
    _external_events_cache_set(artist, cc, events)
    return events


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
    return {
        "id": f"ext:{source}:{artist.lower()}:{date}:{city.lower()}",
        "name": f"{artist} — {venue}".strip(" —"),
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

_CONCERTS_CACHE_TTL = 7 * 86400  # неделя — кэш обновляется job'ом по воскресеньям перед уведомлением


def _concerts_cache_get(cid, cc):
    """Кэшированный список концертов пользователя за неделю; None если нет/устарел/не тот cc."""
    entry = store._load(config.CONCERTS_CACHE_KEY).get(str(cid))
    if not entry or entry.get("cc") != cc:
        return None
    import time
    if time.time() - entry.get("ts", 0) > _CONCERTS_CACHE_TTL:
        return None
    return filter_concert_events(entry.get("events", []), cc)


def _concerts_cache_set(cid, cc, events):
    import time
    d = store._load(config.CONCERTS_CACHE_KEY)
    d[str(cid)] = {"ts": time.time(), "cc": cc, "events": filter_concert_events(events, cc)}
    store._save(config.CONCERTS_CACHE_KEY, d)


async def _fetch_concerts(artists, cc, cname):
    """Живой запрос к Ticketmaster + внешний поиск (Tavily/Firecrawl/AI, кэш 7 дней
    на артиста) без кэша — общая часть для find_concerts/send_weekly_events и для
    job'а прогрева кэша по воскресеньям. Ticketmaster — основной источник, но не
    полный: внешний поиск добирает события, которых там нет."""
    from datetime import datetime, timedelta
    now = datetime.now(config.TZ)
    date_from = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    date_to = (now + timedelta(days=182)).strftime("%Y-%m-%dT%H:%M:%SZ")  # ~6 месяцев

    tm_events = await _ticketmaster_events_many(artists, cc, start_dt=date_from, end_dt=date_to, size=10, limit=40)
    external_batches = await asyncio.gather(
        *[get_external_events_for_artist(artist, cc, cname) for artist in artists[:40]],
        return_exceptions=True,
    )
    external_events = []
    for batch in external_batches:
        if isinstance(batch, Exception):
            continue
        external_events.extend(batch or [])
    return filter_concert_events(merge_concert_events(tm_events, external_events), cc)


async def refresh_concerts_cache(cid):
    """Прогревает недельный кэш концертов пользователя — вызывается job'ом по воскресеньям
    перед уведомлением «Афиша недели», чтобы само уведомление и последующие «Концерты» не ждали API."""
    artists = _ensure_artists(cid)
    if not artists or not config.TICKETMASTER_API_KEY:
        return
    s = store.get_settings(cid)
    cc = (s.get("cc") or "NL").upper()
    cname = s.get("country") or "твоя страна"
    events = await _fetch_concerts(artists, cc, cname)
    _concerts_cache_set(cid, cc, events)


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
    events = cached if cached is not None else await _fetch_concerts(artists, cc, cname)

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
        source = e.get("_source", "ticketmaster")
        rows_data.append({
            "artist": e.get("_artist", ""),
            "flag": flag,
            "place": city,
            "genre": _concert_genre(e),
            "price": _concert_min_price(e),
            "date": _fmt_date(date) if date else "",
            "url": e.get("url", ""),
            "verification": "confirmed" if source in ("official_site", "venue", "ticketmaster") else "review",
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


async def find_concerts(bot, cid, mode="home"):
    if not config.TICKETMASTER_API_KEY:
        await bot.send_message(chat_id=cid,
            text="Поиск мероприятий требует бесплатный ключ Ticketmaster.\n"
                 "Заведи его на developer.ticketmaster.com и добавь на Railway переменную TICKETMASTER_API_KEY.")
        return
    artists = _ensure_artists(cid)
    if not artists:
        await bot.send_message(chat_id=cid, text="Не удалось загрузить артистов. Добавь их в настройках.")
        return
    s = store.get_settings(cid)
    home_cc = (s.get("cc") or "NL").upper()
    home_flag = util.flag_from_cc(home_cc)
    home_name = s.get("country") or "твоя страна"
    if mode in _CONCERT_CC_MAP:
        cc, flag, cname = _CONCERT_CC_MAP[mode]
    else:
        cc, flag, cname = home_cc, home_flag, home_name
    cname_place = _concert_place_name(cname, cc)

    from util import _MONTHS
    from datetime import datetime

    events = _concerts_cache_get(cid, cc)
    if events is None:
        events = await _fetch_concerts(artists, cc, cname)
        _concerts_cache_set(cid, cc, events)

    rows = [
        [InlineKeyboardButton("🌍 Сменить страну", callback_data="a_concerts_pick")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")],
    ]
    kb = InlineKeyboardMarkup(rows)

    def _fmt_date(ds):
        try:
            y, m, dd = ds.split("-")
            return f"{int(dd)} {_MONTHS[int(m)-1]} {y}"
        except Exception:
            return ds

    place_label = f"Концерты в {cname_place}"
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
        source = e.get("_source", "ticketmaster")
        rows_data.append({
            "artist": artist,
            "flag": flag,
            "place": place,
            "genre": _concert_genre(e),
            "price": _concert_min_price(e),
            "date": _fmt_date(date) if date else "",
            "url": e.get("url", ""),
            "verification": "confirmed" if source in ("official_site", "venue", "ticketmaster") else "review",
        })

    msg = leisure_ui.concerts_list(place_label, rows_data)
    store.last_source[str(cid)] = "Досуг · Концерты"
    store.last_answer[str(cid)] = msg.text
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb,
                           disable_web_page_preview=True)




async def _build_weekly_events_msg(cid):
    """Афиша недели: концерты артистов пользователя + кинопремьеры ближайших дней -> MessageSpec."""
    from datetime import datetime, timedelta

    s = store.get_settings(cid)
    cc = (s.get("cc") or config.DEFAULT_CITY.get("cc", "")).upper()
    cname = _concert_place_name(s.get("country"), cc)
    now = datetime.now(config.TZ)
    period_start = now.date()
    period_end = (now + timedelta(days=7)).date()
    today_str = period_start.isoformat()
    date_to_str = period_end.isoformat()

    # --- Концерты ---
    # Читаем недельный кэш (обновлён job'ом refresh_concerts_cache перед этим уведомлением),
    # чтобы не делать живой запрос к Ticketmaster по всем артистам прямо в момент отправки.
    concert_items = []
    if config.TICKETMASTER_API_KEY:
        artists = _ensure_artists(cid)
        if artists:
            cached = _concerts_cache_get(cid, cc)
            events = cached if cached is not None else await _fetch_concerts(artists, cc, cname)
            if cached is None:
                _concerts_cache_set(cid, cc, events)
            events = [e for e in events
                      if today_str <= e.get("dates", {}).get("start", {}).get("localDate", "9999") <= date_to_str]
            for e in events[:5]:
                artist = e.get("_artist", "")
                date_str = e.get("dates", {}).get("start", {}).get("localDate", "")
                ven = (e.get("_embedded", {}).get("venues") or [{}])[0]
                vn = ven.get("name", "")
                city = (ven.get("city") or {}).get("name", "")
                venue_str = ", ".join(x for x in [vn, city] if x)
                concert_items.append({
                    "title": artist,
                    "place": venue_str,
                    "date": date_str,
                })

    # --- Кинопремьеры ---
    movie_items = []
    if config.TMDB_API_KEY:
        try:
            movie_items = await asyncio.to_thread(
                tmdb.get_upcoming_theatrical_releases,
                cc,
                period_start,
                period_end,
                _movie_service_language(cid),
            )
        except Exception:
            movie_items = []

    return leisure_ui.weekly_events_card(period_start, period_end, concert_items, movie_items[:5])


async def send_weekend_events(bot, cid):
    """Пятница 10:00 — «Куда сходить»: афиша недели (концерты + кино) и новые концерты
    любимых артистов одним сообщением."""
    from ui.builder import MessageBuilder
    weekly_msg = await _build_weekly_events_msg(cid)
    new_concerts_msg = await _build_new_concerts_msg(cid)
    combined = MessageBuilder()
    combined.embed(weekly_msg)
    if new_concerts_msg is not None:
        combined.embed(new_concerts_msg)
    msg = combined.build_stripped()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, disable_web_page_preview=True)


async def concert_pick_country(bot, cid):
    countries = [
        ("at", "Австрия", f"{COUNTRY_EMOJI['at']} Австрия"),
        ("be", "Бельгия", f"{COUNTRY_EMOJI['be']} Бельгия"),
        ("gb", "Великобритания", f"{COUNTRY_EMOJI['gb']} Великобр."),
        ("de", "Германия", f"{COUNTRY_EMOJI['de']} Германия"),
        ("dk", "Дания", f"{COUNTRY_EMOJI['dk']} Дания"),
        ("es", "Испания", f"{COUNTRY_EMOJI['es']} Испания"),
        ("it", "Италия", f"{COUNTRY_EMOJI['it']} Италия"),
        ("nl", "Нидерланды", f"{COUNTRY_EMOJI['nl']} Нидерланды"),
        ("pl", "Польша", f"{COUNTRY_EMOJI['pl']} Польша"),
        ("pt", "Португалия", f"{COUNTRY_EMOJI['pt']} Португалия"),
        ("fr", "Франция", f"{COUNTRY_EMOJI['fr']} Франция"),
        ("ch", "Швейцария", f"{COUNTRY_EMOJI['ch']} Швейцария"),
        ("se", "Швеция", f"{COUNTRY_EMOJI['se']} Швеция"),
    ]
    buttons = [
        InlineKeyboardButton(label, callback_data=f"a_concerts_{cc}")
        for cc, _name, label in sorted(countries, key=lambda x: x[1])
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_leisure"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")])
    await bot.send_message(chat_id=cid, text="🌍 Выбери страну для поиска концертов:",
                           reply_markup=InlineKeyboardMarkup(rows))
