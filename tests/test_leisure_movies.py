import asyncio

import pytest

import leisure


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
