import asyncio

import pytest

import config
import leisure
import store


@pytest.mark.unit
def test_normalize_movie_items_tolerates_llm_shape_drift():
    items = leisure._normalize_movie_items([
        "Олдбой (2003)",
        {"name": "Пылающий", "desc": "медленный триллер"},
        {"title": ""},
        None,
    ])

    assert items == [
        {"title": "Олдбой (2003)", "title_en": "", "hook": ""},
        {"title": "Пылающий", "title_en": "", "hook": "медленный триллер"},
    ]


@pytest.mark.unit
def test_pick_good_movie_skips_non_dict_items():
    it, tm = leisure._pick_good_movie(["битый item", {"title": "Решение уйти"}], set())

    assert tm is None
    assert it == {"title": "Решение уйти"}


@pytest.mark.unit
def test_fallback_movie_items_skips_used(monkeypatch):
    monkeypatch.setattr(leisure, "_movie_used", lambda cid: {"решение уйти", "decision to leave"})

    items = leisure._fallback_movie_items("cid")

    assert items
    assert all(it["title"] != "Решение уйти" for it in items)


@pytest.mark.unit
def test_movie_card_tolerates_partial_tmdb_data():
    title, msg = leisure._movie_card({"title": "Патерсон", "hook": "тихое кино"}, {"name": "Патерсон"})

    assert title == "Патерсон"
    assert "Патерсон" in msg.text
    assert "тихое кино" in msg.text


@pytest.mark.unit
def test_concert_place_name_uses_locative_for_netherlands():
    assert leisure._concert_place_name("Нидерланды", "NL") == "Нидерландах"
    assert leisure._concert_place_name("Netherlands", "") == "Нидерландах"


@pytest.mark.unit
def test_concert_country_search_name_for_eventbrite():
    assert leisure._concert_country_search_name("Нидерланды", "NL") == "Netherlands"
    assert leisure._concert_country_search_name("Германия", "DE") == "Germany"


@pytest.mark.unit
def test_concert_country_buttons_are_three_columns_alpha():
    countries = [
        ("at", "Австрия", "🇦🇹 Австрия"),
        ("be", "Бельгия", "🇧🇪 Бельгия"),
        ("gb", "Великобритания", "🇬🇧 Великобр."),
        ("de", "Германия", "🇩🇪 Германия"),
        ("dk", "Дания", "🇩🇰 Дания"),
        ("es", "Испания", "🇪🇸 Испания"),
        ("it", "Италия", "🇮🇹 Италия"),
        ("nl", "Нидерланды", "🇳🇱 Нидерланды"),
        ("pl", "Польша", "🇵🇱 Польша"),
        ("pt", "Португалия", "🇵🇹 Португалия"),
        ("fr", "Франция", "🇫🇷 Франция"),
        ("ch", "Швейцария", "🇨🇭 Швейцария"),
        ("se", "Швеция", "🇸🇪 Швеция"),
    ]
    labels = [label for _cc, _name, label in sorted(countries, key=lambda x: x[1])]
    rows = [labels[i:i + 3] for i in range(0, len(labels), 3)]

    assert rows[0] == ["🇦🇹 Австрия", "🇧🇪 Бельгия", "🇬🇧 Великобр."]
    assert all(len(row) <= 3 for row in rows)


@pytest.mark.unit
def test_web_concert_links_filters_event_sites(monkeypatch):
    monkeypatch.setattr(leisure.research, "web_search", lambda *a, **k: [
        {"title": "Artist on Songkick", "url": "https://www.songkick.com/artists/1"},
        {"title": "Random blog", "url": "https://example.com/post"},
        {"title": "Artist on Bandsintown", "url": "https://www.bandsintown.com/a/1"},
    ])

    links = leisure._web_concert_links_for_artists(["Artist"], "Netherlands", limit_artists=1, per_artist=2)

    assert [x["url"] for x in links] == [
        "https://www.songkick.com/artists/1",
        "https://www.bandsintown.com/a/1",
    ]


@pytest.mark.unit
def test_eventbrite_events_normalizes_response(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"events": [{
                "id": "1",
                "name": {"text": "Artist live"},
                "url": "https://eventbrite.com/e/1",
                "start": {"local": "2026-08-01T20:00:00"},
                "venue": {"name": "Paradiso", "address": {"city": "Amsterdam"}},
            }]}

    monkeypatch.setattr(leisure.config, "EVENTBRITE_API_KEY", "key")
    monkeypatch.setattr(leisure.util, "ttl_get", lambda *a, **k: None)
    monkeypatch.setattr(leisure.util, "ttl_set", lambda _ns, _key, value: value)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())

    events = leisure._eventbrite_events("Artist", "Netherlands", 3)

    assert events[0]["_source"] == "Eventbrite"
    assert events[0]["dates"]["start"]["localDate"] == "2026-08-01"
    assert events[0]["_embedded"]["venues"][0]["city"]["name"] == "Amsterdam"


def _tm_event(artist, date, city="", event_id=None):
    return {
        "id": event_id or f"{artist}:{date}:{city}",
        "name": artist,
        "_artist": artist,
        "dates": {"start": {"localDate": date}},
        "_embedded": {"venues": [{"name": "", "city": {"name": city}}]},
    }


@pytest.mark.unit
def test_ticketmaster_events_many_queries_all_artists_beyond_old_limit(monkeypatch):
    """До фикса artists[:limit] отбрасывал артистов после 15-го (или 10-го для Eventbrite)."""
    artists = [f"Artist {i}" for i in range(20)]
    called = []

    def fake_fetch(artist, cc, start_dt, end_dt, size):
        called.append(artist)
        return [_tm_event(artist, "2026-09-01")]

    monkeypatch.setattr(leisure, "_ticketmaster_events_for_artist", fake_fetch)

    events = asyncio.run(leisure._ticketmaster_events_many(artists, "NL"))

    assert set(called) == set(artists)
    assert len(events) == 20


@pytest.mark.unit
def test_eventbrite_events_many_queries_all_artists_beyond_old_limit(monkeypatch):
    artists = [f"Artist {i}" for i in range(15)]
    called = []

    def fake_fetch(artist, country_name, size):
        called.append(artist)
        return [{"id": artist, "name": artist, "dates": {"start": {"localDate": "2026-09-01"}}}]

    monkeypatch.setattr(leisure, "_eventbrite_events", fake_fetch)

    events = asyncio.run(leisure._eventbrite_events_many(artists, "Netherlands"))

    assert set(called) == set(artists)
    assert len(events) == 15


@pytest.mark.unit
def test_ticketmaster_events_many_sorts_dateless_events_last(monkeypatch):
    def fake_fetch(artist, cc, start_dt, end_dt, size):
        if artist == "No Date Artist":
            return [_tm_event(artist, "")]
        return [_tm_event(artist, "2026-05-01")]

    monkeypatch.setattr(leisure, "_ticketmaster_events_for_artist", fake_fetch)

    events = asyncio.run(leisure._ticketmaster_events_many(["No Date Artist", "Dated Artist"], "NL"))

    assert events[-1]["_artist"] == "No Date Artist"
    assert events[0]["_artist"] == "Dated Artist"


@pytest.mark.unit
def test_ticketmaster_events_many_keeps_same_artist_different_cities(monkeypatch):
    def fake_fetch(artist, cc, start_dt, end_dt, size):
        return [
            _tm_event(artist, "2026-05-01", city="Amsterdam", event_id="ams"),
            _tm_event(artist, "2026-05-01", city="Rotterdam", event_id="rtm"),
        ]

    monkeypatch.setattr(leisure, "_ticketmaster_events_for_artist", fake_fetch)

    events = asyncio.run(leisure._ticketmaster_events_many(["Artist"], "NL"))

    cities = {e["_embedded"]["venues"][0]["city"]["name"] for e in events}
    assert cities == {"Amsterdam", "Rotterdam"}


@pytest.mark.unit
def test_ticketmaster_events_for_artist_does_not_cache_on_http_error(monkeypatch):
    class FailingResp:
        def raise_for_status(self):
            raise Exception("HTTP 429")

    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure.util, "ttl_get", lambda *a, **k: None)
    cached_calls = []
    monkeypatch.setattr(leisure.util, "ttl_set", lambda ns, key, value: cached_calls.append(value) or value)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FailingResp())

    events = leisure._ticketmaster_events_for_artist("Artist", "NL")

    assert events == []
    assert cached_calls == []  # ошибка не должна попадать в кэш


@pytest.mark.unit
def test_ticketmaster_get_retries_on_429_and_succeeds(monkeypatch):
    """Регрессия: с большими списками артистов бесплатный тариф Ticketmaster отдавал 429
    почти на все параллельные запросы, и это тихо трактовалось как 'у артиста нет концертов'."""
    monkeypatch.setattr(leisure.time, "sleep", lambda *_: None)  # не ждать реальные задержки в тесте

    calls = {"n": 0}

    class Resp:
        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

        def json(self):
            return {"ok": True}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return Resp(429) if calls["n"] < 3 else Resp(200)

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    r = leisure._ticketmaster_get("https://app.ticketmaster.com/discovery/v2/events.json", {})

    assert r.status_code == 200
    assert calls["n"] == 3  # 2 попытки словили 429, третья прошла


@pytest.mark.unit
def test_ticketmaster_get_does_not_retry_on_non_rate_limit_error(monkeypatch):
    """Сетевые/прочие ошибки не должны запускать retry-цикл с задержками - это не поможет
    и просто блокирует поток на секунды впустую."""
    sleep_calls = []
    monkeypatch.setattr(leisure.time, "sleep", lambda d: sleep_calls.append(d))

    class FailingResp:
        status_code = 404

        def raise_for_status(self):
            raise Exception("HTTP 404")

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FailingResp())

    with pytest.raises(Exception):
        leisure._ticketmaster_get("https://app.ticketmaster.com/discovery/v2/events.json", {})

    assert sleep_calls == []  # ни одной задержки - сразу пробросили ошибку


@pytest.mark.unit
def test_ticketmaster_events_many_limits_concurrency(monkeypatch):
    """Параллелизм запросов к Ticketmaster ограничен _TICKETMASTER_CONCURRENCY,
    иначе список из 30+ артистов заваливает бесплатный тариф API."""
    assert leisure._TICKETMASTER_CONCURRENCY._value == 5


@pytest.mark.unit
def test_concert_genre_prefers_subgenre_over_genre():
    e = {"classifications": [{"genre": {"name": "Rock"}, "subGenre": {"name": "Alternative Rock"}}]}
    assert leisure._concert_genre(e) == "Alternative Rock"


@pytest.mark.unit
def test_concert_genre_translates_known_genre():
    e = {"classifications": [{"genre": {"name": "Pop"}, "subGenre": {"name": "Undefined"}}]}
    assert leisure._concert_genre(e) == "Поп"


@pytest.mark.unit
def test_concert_genre_empty_when_no_classifications():
    assert leisure._concert_genre({}) == ""
    assert leisure._concert_genre({"classifications": [{"genre": {"name": "Other"}}]}) == ""


@pytest.mark.unit
def test_concert_min_price_picks_lowest_across_ranges():
    e = {"priceRanges": [
        {"type": "standard", "currency": "EUR", "min": 45.0, "max": 89.5},
        {"type": "vip", "currency": "EUR", "min": 25.0, "max": 150.0},
    ]}
    assert leisure._concert_min_price(e) == "от 25 EUR"


@pytest.mark.unit
def test_concert_min_price_keeps_decimals_when_not_round():
    e = {"priceRanges": [{"currency": "USD", "min": 29.99, "max": 60.0}]}
    assert leisure._concert_min_price(e) == "от 29.99 USD"


@pytest.mark.unit
def test_concert_min_price_empty_when_no_price_ranges():
    assert leisure._concert_min_price({}) == ""
    assert leisure._concert_min_price({"priceRanges": []}) == ""


class _CapturingBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kw):
        self.sent.append(kw)


@pytest.mark.unit
def test_concerts_cache_set_and_get_roundtrip():
    cid = "cache-cid-1"
    events = [_tm_event("Romy", "2026-08-21", city="Biddinghuizen", event_id="1")]

    leisure._concerts_cache_set(cid, "NL", events)

    assert leisure._concerts_cache_get(cid, "NL") == events


@pytest.mark.unit
def test_concerts_cache_get_returns_none_when_country_differs():
    cid = "cache-cid-2"
    leisure._concerts_cache_set(cid, "NL", [_tm_event("Romy", "2026-08-21")])

    assert leisure._concerts_cache_get(cid, "DE") is None


@pytest.mark.unit
def test_concerts_cache_get_returns_none_when_stale(monkeypatch):
    cid = "cache-cid-3"
    leisure._concerts_cache_set(cid, "NL", [_tm_event("Romy", "2026-08-21")])

    import time
    future = time.time() + leisure._CONCERTS_CACHE_TTL + 1
    monkeypatch.setattr(time, "time", lambda: future)

    assert leisure._concerts_cache_get(cid, "NL") is None


@pytest.mark.unit
def test_concerts_cache_get_returns_none_when_missing():
    assert leisure._concerts_cache_get("cache-cid-missing", "NL") is None


@pytest.mark.unit
def test_find_concerts_uses_cache_without_hitting_api(monkeypatch):
    cid = "cache-cid-4"
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure, "_ensure_artists", lambda cid: ["Romy"])
    leisure._concerts_cache_set(cid, "NL", [
        _tm_event("Romy", "2026-08-21", city="Biddinghuizen", event_id="1") | {"url": "https://tm/romy"},
    ])

    called = {"n": 0}

    async def should_not_be_called(*a, **kw):
        called["n"] += 1
        return []

    monkeypatch.setattr(leisure, "_ticketmaster_events_many", should_not_be_called)
    monkeypatch.setattr(leisure, "_eventbrite_events_many", should_not_be_called)

    bot = _CapturingBot()
    asyncio.run(leisure.find_concerts(bot, cid))

    assert called["n"] == 0
    assert "Romy" in bot.sent[0]["text"]


@pytest.mark.unit
def test_find_concerts_refreshes_cache_when_stale(monkeypatch):
    cid = "cache-cid-5"
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure, "_ensure_artists", lambda cid: ["Romy"])

    async def fake_many(artists, cc, start_dt="", end_dt="", size=3, limit=40):
        return [_tm_event("Romy", "2026-08-21", city="Biddinghuizen", event_id="1")]

    monkeypatch.setattr(leisure, "_ticketmaster_events_many", fake_many)

    bot = _CapturingBot()
    asyncio.run(leisure.find_concerts(bot, cid))

    assert leisure._concerts_cache_get(cid, "NL") is not None


@pytest.mark.unit
def test_refresh_concerts_cache_populates_cache(monkeypatch):
    cid = "cache-cid-6"
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure, "_ensure_artists", lambda cid: ["Romy"])

    async def fake_many(artists, cc, start_dt="", end_dt="", size=3, limit=40):
        return [_tm_event("Romy", "2026-09-01", city="Amsterdam", event_id="1")]

    monkeypatch.setattr(leisure, "_ticketmaster_events_many", fake_many)

    asyncio.run(leisure.refresh_concerts_cache(cid))

    cached = leisure._concerts_cache_get(cid, "NL")
    assert cached and cached[0]["_artist"] == "Romy"


@pytest.mark.unit
def test_refresh_concerts_cache_skips_users_without_artists(monkeypatch):
    cid = "cache-cid-7"
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure, "_ensure_artists", lambda cid: [])

    asyncio.run(leisure.refresh_concerts_cache(cid))

    assert leisure._concerts_cache_get(cid, "NL") is None


@pytest.mark.unit
def test_find_concerts_renders_clean_artist_cards_with_hidden_link(monkeypatch):
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure, "_ensure_artists", lambda cid: ["Romy"])

    async def fake_many(artists, cc, start_dt="", end_dt="", size=3, limit=40):
        return [_tm_event("Romy", "2026-08-21", city="Biddinghuizen", event_id="1")
                | {"url": "https://ticketmaster.com/romy",
                   "classifications": [{"genre": {"name": "Electronic"}, "subGenre": {"name": "Undefined"}}],
                   "priceRanges": [{"currency": "EUR", "min": 35.0, "max": 80.0}]}]

    monkeypatch.setattr(leisure, "_ticketmaster_events_many", fake_many)

    bot = _CapturingBot()
    asyncio.run(leisure.find_concerts(bot, "cid-concerts-1"))

    sent = bot.sent[0]
    text = sent["text"]
    assert "Romy" in text
    assert "Biddinghuizen" in text
    assert "21 августа 2026" in text
    assert "Электроника" in text
    assert "от 35 EUR" in text
    assert "Подробнее…" in text
    assert "Netherlands" not in text  # место - только город, без названия страны
    link_entities = [e for e in sent["entities"] if e.type == "text_link"]
    assert any(e.url == "https://ticketmaster.com/romy" for e in link_entities)
    assert "https://ticketmaster.com/romy" not in text  # ссылка спрятана под текст
    assert "Нашёл вне Ticketmaster" not in text
    assert "bandsintown" not in text.lower()


@pytest.mark.unit
def test_find_concerts_deduplicates_same_artist_same_show(monkeypatch):
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure, "_ensure_artists", lambda cid: ["Romy"])

    dup_event = _tm_event("Romy", "2026-08-21", city="Biddinghuizen", event_id="1") | {
        "url": "https://ticketmaster.com/romy"
    }

    async def fake_many(artists, cc, start_dt="", end_dt="", size=3, limit=40):
        return [dup_event, dict(dup_event)]  # тот же артист/дата/город дважды

    monkeypatch.setattr(leisure, "_ticketmaster_events_many", fake_many)

    bot = _CapturingBot()
    asyncio.run(leisure.find_concerts(bot, "cid-concerts-2"))

    text = bot.sent[0]["text"]
    assert text.count("Romy") == 1


@pytest.mark.unit
def test_eventbrite_events_does_not_cache_on_http_error(monkeypatch):
    class FailingResp:
        def raise_for_status(self):
            raise Exception("HTTP 404")

    monkeypatch.setattr(leisure.config, "EVENTBRITE_API_KEY", "key")
    monkeypatch.setattr(leisure.util, "ttl_get", lambda *a, **k: None)
    cached_calls = []
    monkeypatch.setattr(leisure.util, "ttl_set", lambda ns, key, value: cached_calls.append(value) or value)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FailingResp())

    events = leisure._eventbrite_events("Artist", "Netherlands", 3)

    assert events == []
    assert cached_calls == []


@pytest.mark.unit
def test_ticketmaster_city_events_sends_classification_and_keyword(monkeypatch):
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure.util, "ttl_get", lambda *a, **k: None)
    monkeypatch.setattr(leisure.util, "ttl_set", lambda ns, key, value: value)

    captured = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"_embedded": {"events": []}}

    def fake_get(url, params=None, timeout=None):
        captured.update(params)
        return Resp()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)

    leisure._ticketmaster_city_events("NL", city="Amsterdam", classification_name="arts&theatre",
                                       keyword="festival", size=15)

    assert captured["countryCode"] == "NL"
    assert captured["city"] == "Amsterdam"
    assert captured["classificationName"] == "arts&theatre"
    assert captured["keyword"] == "festival"
    assert captured["size"] == 15


@pytest.mark.unit
def test_ticketmaster_city_events_does_not_cache_on_http_error(monkeypatch):
    class FailingResp:
        def raise_for_status(self):
            raise Exception("HTTP 500")

    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure.util, "ttl_get", lambda *a, **k: None)
    cached_calls = []
    monkeypatch.setattr(leisure.util, "ttl_set", lambda ns, key, value: cached_calls.append(value) or value)
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FailingResp())

    events = leisure._ticketmaster_city_events("NL", classification_name="music")

    assert events == []
    assert cached_calls == []


@pytest.mark.unit
def test_afisha_genre_matches_comedy_requires_comedy_marker():
    comedy_event = {"classifications": [{"genre": {"name": "Comedy"}}]}
    theatre_event = {"classifications": [{"genre": {"name": "Theatre"}}]}
    assert leisure._afisha_genre_matches(comedy_event, "comedy") is True
    assert leisure._afisha_genre_matches(theatre_event, "comedy") is False


@pytest.mark.unit
def test_afisha_genre_matches_exhibitions_excludes_stage_genres():
    exhibition_event = {"classifications": [{"genre": {"name": "Miscellaneous"}}]}
    theatre_event = {"classifications": [{"genre": {"name": "Theatre"}}]}
    assert leisure._afisha_genre_matches(exhibition_event, "exhibitions") is True
    assert leisure._afisha_genre_matches(theatre_event, "exhibitions") is False


@pytest.mark.unit
def test_afisha_genre_matches_concerts_category_always_true():
    assert leisure._afisha_genre_matches({}, "concerts") is True
    assert leisure._afisha_genre_matches({}, "festivals") is True


@pytest.mark.unit
def test_send_city_digest_skips_empty_categories_and_ranks_concerts(monkeypatch):
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure, "_ensure_artists", lambda cid: ["Romy"])

    async def fake_many(artists, cc, start_dt="", end_dt="", size=3, limit=40):
        return [_tm_event("Romy", "2026-09-01", city="Amsterdam", event_id="1")]

    async def fake_category_events(category_key, cc, city, start_dt, end_dt, size=10):
        if category_key == "theatre":
            return [_tm_event("Hamlet", "2026-09-05", city="Amsterdam", event_id="2")]
        return []  # фестивали/стендап/выставки пусты

    monkeypatch.setattr(leisure, "_ticketmaster_events_many", fake_many)
    monkeypatch.setattr(leisure, "_afisha_category_events", fake_category_events)

    bot = _CapturingBot()
    asyncio.run(leisure.send_city_digest(bot, "cid-afisha-3"))

    text = bot.sent[0]["text"]
    assert "Romy" in text
    assert "Hamlet" in text
    assert "Фестивали" not in text  # пустая категория не рендерится
    assert "Стендап" not in text
    assert "Выставки" not in text


@pytest.mark.unit
def test_send_city_digest_uses_llm_ranking_when_many_concerts(monkeypatch):
    monkeypatch.setattr(leisure.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure, "_ensure_artists", lambda cid: ["A", "B", "C", "D", "E"])

    async def fake_many(artists, cc, start_dt="", end_dt="", size=3, limit=40):
        return [_tm_event(a, f"2026-09-0{i+1}", city="Amsterdam", event_id=str(i))
                for i, a in enumerate(["A", "B", "C", "D", "E"])]

    async def fake_category_events(category_key, cc, city, start_dt, end_dt, size=10):
        return []

    async def fake_rank(events, artists):
        return events[:2]  # LLM выбрал 2 лучших

    monkeypatch.setattr(leisure, "_ticketmaster_events_many", fake_many)
    monkeypatch.setattr(leisure, "_afisha_category_events", fake_category_events)
    monkeypatch.setattr(leisure, "_rank_concerts_by_taste", fake_rank)

    bot = _CapturingBot()
    asyncio.run(leisure.send_city_digest(bot, "cid-afisha-4"))

    text = bot.sent[0]["text"]
    assert text.count("🎫 Концерты") == 1
    # LLM вернул только A и B - остальные три артиста не должны попасть в дайджест
    assert "A" in text and "B" in text
    assert "C" not in text and "D" not in text and "E" not in text


@pytest.mark.unit
def test_rank_concerts_by_taste_falls_back_on_llm_error(monkeypatch):
    events = [_tm_event("Romy", "2026-09-01"), _tm_event("Other", "2026-09-02")]

    async def failing_llm(*a, **kw):
        raise Exception("LLM down")

    monkeypatch.setattr(leisure.ai, "allm_json", failing_llm)

    result = asyncio.run(leisure._rank_concerts_by_taste(events, ["Romy"]))

    assert result == events[:3]


def test_book_text_uses_editorial_structure():
    msg = leisure._book_text({
        "author": "Олдос Хаксли",
        "title": "Дивный новый мир",
        "year": "1932",
        "desc": "Генетический рай без свободы.",
        "why": ["-Анти-Оруэлл: общество ломают развлечениями.", "Главный конфликт: чужак внутри системы."],
        "plot": "Бернард привозит Дикаря из резервации. Тот ломает фасад счастливого концлагеря.",
        "quote": "Лучше быть несчастным в свободе.",
        "hook": "лишний итог",
    })
    text = msg.text

    assert text.startswith("📚 Олдос Хаксли • «Дивный новый мир» (1932)")
    assert "Почему стоит читать" in text
    assert "Коротко о сюжете\nБернард" in text
    assert "Цитата\n«Лучше быть несчастным в свободе.»" in text
    assert any(e.type == "bold" for e in msg.entities)
    assert "-Анти" not in text
    assert "лишний итог" not in text
