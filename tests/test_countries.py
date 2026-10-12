import asyncio

import countries

REAL_TITLE_INFO = countries.title_info
import leisure_movies  # noqa: F401 — порядок импорта как в боте (movie_recommendation ↔ leisure_movies)
import movie_engine
import movie_recommendation
import wikidata_books
from ui import leisure as leisure_ui

TABLE = {"JP": ["Япония", "asia"], "US": ["США", "americas"], "RU": ["Россия", "russia"],
         "FR": ["Франция", "europe"], "NG": ["Нигерия", "africa"], "AU": ["Австралия", "oceania"]}


def test_regions_filter_by_country_then_language_unknown_passes(monkeypatch):
    monkeypatch.setattr(countries, "_table", lambda: TABLE)
    off = ["asia", "russia"]

    assert not countries.allowed({"countries": ["JP"]}, off)
    assert countries.allowed({"countries": ["JP", "US"]}, off)  # совместное производство с Америкой
    assert not countries.allowed({"origin_country": ["RU"]}, off)
    assert not countries.allowed({"original_language": "ko"}, off)  # страны нет — по языку оригинала
    assert countries.allowed({"original_language": "en"}, off)  # английский неоднозначен — не отсеиваем
    assert countries.allowed({"countries": ["AU"]}, off) and countries.allowed({}, off)
    assert countries.title_label({"countries": ["JP"]}) == "🇯🇵 JP"
    assert countries.label("US") == "🇺🇸 USA" and countries.label("RU") == "🇷🇺 RU" and countries.label("") == ""
    assert countries.regions_off_text(off) == "без Азии и России"


def test_region_toggle_and_engine_filter(monkeypatch):
    monkeypatch.setattr(countries, "_table", lambda: TABLE)
    saved = {}
    monkeypatch.setattr(movie_recommendation.settings, "get", lambda _cid, key, default=None: saved.get(key, default))
    monkeypatch.setattr(movie_recommendation.settings, "set_", lambda _cid, key, value: saved.__setitem__(key, value))

    async def show(*_a, **_k):
        return None

    monkeypatch.setattr(movie_recommendation, "send_movie_prefs", show)
    asyncio.run(movie_recommendation.toggle_movie_pref(None, "42", "mpref_region_asia"))
    assert movie_recommendation.regions_off("42") == ["asia"]

    monkeypatch.setattr(movie_engine, "_excluded_norms", lambda _cid: set())
    base = {"vote_count": 500, "rating": 8, "release_date": "2020-01-01"}
    pool = {"a": {**base, "name": "Токио", "origin_country": ["JP"]},
            "b": {**base, "name": "Нью-Йорк", "origin_country": ["US"]}}
    assert [c["name"] for c in movie_engine.filter_candidates("42", pool, 7)] == ["Нью-Йорк"]

    asyncio.run(movie_recommendation.toggle_movie_pref(None, "42", "mpref_region_asia"))
    assert movie_recommendation.regions_off("42") == []


def test_cards_show_country_only_when_known():
    title, msg = leisure_ui.movie_card({"title": "Дюна"}, {
        "name": "Дюна", "kind": "movie", "genres": "фантастика", "country_label": "🇺🇸 USA", "year": 2021})
    assert "фантастика · 🇺🇸 USA · 2021" in msg.text
    assert leisure_ui.artist_card({"artist": "Alvvays", "country_label": "🇨🇦 CA"}).text.startswith(
        "🎸 Alvvays\n\n🇨🇦 CA")  # после заголовка — пустая строка по UI-контракту
    assert "\n" not in leisure_ui.artist_card({"artist": "Alvvays", "country_label": ""}).text


def _claim(value):
    return {"mainsnak": {"datavalue": {"value": value}}}


def test_author_country_skips_historic_state_without_iso(monkeypatch):
    writer = {
        "claims": {"P31": [_claim({"id": "Q5"})],
                   "P27": [_claim({"id": "Q174193"}), _claim({"id": "Q145"})]},
        "descriptions": {"en": {"value": "English writer"}},
    }
    states = {"Q174193": {"claims": {}}, "Q145": {"claims": {"P297": [_claim("GB")]}}}
    responses = iter([{"search": [{"id": "Q42511"}]}, {"entities": {"Q42511": writer}}, {"entities": states}])
    monkeypatch.setattr(wikidata_books, "_get", lambda _params: next(responses))

    assert wikidata_books.author_country("H. G. Wells") == "GB"


def test_engine_pick_checks_production_country_from_details(monkeypatch):
    monkeypatch.setattr(countries, "_table", lambda: TABLE)
    details = {1: {"name": "Голливуд", "countries": ["US"]}, 2: {"name": "Париж", "countries": ["FR"]}}
    monkeypatch.setattr(movie_recommendation.tmdb, "detail", lambda tmdb_id, _kind: details[tmdb_id])

    it, tm = movie_recommendation._first_allowed_card(
        "42", [{"id": 1, "kind": "movie"}, {"id": 2, "kind": "movie"}], {"regions_off": ["americas"]})

    assert tm["name"] == "Париж"


def test_country_cache_never_keeps_failures_and_rechecks_empty_on_warm(monkeypatch):
    import tmdb
    saved, calls = {}, []
    monkeypatch.setattr(countries, "title_info", REAL_TITLE_INFO)
    monkeypatch.setattr(countries.store, "_load", lambda _key: dict(saved))
    monkeypatch.setattr(countries.store, "mutate_kv", lambda _key, change: saved.update(change(dict(saved))[0]))
    answers = iter([None, {"countries": ["US"], "network": "HBO"}])
    monkeypatch.setattr(tmdb, "detail", lambda *_a: calls.append(1) or next(answers))

    assert countries.title_info("tv", 7) == {}  # TMDb не ответил — пустоту не запоминаем
    assert saved == {}
    assert countries.title_info("tv", 7) == {"country_label": "🇺🇸 USA", "network": "HBO"}
    assert countries.title_info("tv", 7, fetch=False)["network"] == "HBO" and len(calls) == 2

    people = {"x": {"ts": 9e12, "value": ""}}
    monkeypatch.setattr(countries.store, "_load", lambda _key: dict(people))
    monkeypatch.setattr(countries.store, "mutate_kv", lambda _key, change: people.update(change(dict(people))[0]))
    assert countries._person_cache("k", "X", lambda _n: "GB", fetch=False) == ""  # экран — только кэш
    assert countries._person_cache("k", "X", lambda _n: "GB", fetch=True) == "GB"  # прогрев перепроверил


def test_musicbrainz_match_ignores_case_the_and_diacritics_and_uses_area(monkeypatch):
    class Response:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    rows = {"artists": [
        {"name": "Other Band", "score": 100, "country": "US"},
        {"name": "Weeknd", "sort-name": "Weeknd, The", "score": 92,
         "area": {"iso-3166-1-codes": ["CA"]}},
    ]}
    monkeypatch.setattr(countries.requests, "get", lambda *_a, **_k: Response(rows))
    monkeypatch.setattr(countries, "_MUSICBRAINZ_GAP", 0)

    assert countries._musicbrainz_country("The Weeknd") == "CA"  # страна — из area, имя без «The»
    assert countries.main_artist("Doja Cat & SZA") == "Doja Cat"
    assert countries.main_artist("Alex Warren feat. Jelly Roll") == "Alex Warren"
