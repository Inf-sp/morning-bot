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
    # Флаг — перед именем, страна кратко — в строке «жанр · страна · годы»; после заголовка — пустая строка.
    assert leisure_ui.artist_card({"artist": "Alvvays", "country_label": "🇨🇦 CA"}).text == "🇨🇦 Alvvays\n\nCA"
    assert leisure_ui.artist_card({"artist": "Alvvays", "country_label": ""}).text == "🎸 Alvvays"


def test_group_years_come_from_musicbrainz_and_solo_artists_have_none(monkeypatch):
    def artists(*rows):
        monkeypatch.setattr(countries, "_musicbrainz_artists", lambda _name: list(rows))
        return countries._group_years("X")

    assert artists({"type": "Group", "life-span": {"begin": "2011-05", "ended": None}}) == "с 2011"
    assert artists({"type": "Group", "life-span": {"begin": "2005", "end": "2019-02-01", "ended": True}}) == "2005–2019"
    assert artists({"type": "Group", "life-span": {"begin": "2005", "ended": True}}) == ""  # распалась, дата неизвестна
    assert artists({"type": "Person", "life-span": {"begin": "1990-01-01"}}) == ""  # годы жизни, а не карьеры
    assert artists() == ""


def _claim(value):
    return {"mainsnak": {"datavalue": {"value": value}}}


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


def test_latest_album_is_the_newest_released_studio_album(monkeypatch):
    monkeypatch.setattr(countries, "_musicbrainz_artists", lambda _name: [{"id": "mbid-1", "type": "Group"}])
    asked = []

    def mb_get(path, params):
        asked.append((path, params))
        return {"release-groups": [
            {"title": "Antisocialites", "primary-type": "Album", "first-release-date": "2017-09-08"},
            {"title": "Blue Rev", "primary-type": "Album", "first-release-date": "2022-10-07"},
            {"title": "Live at KEXP", "primary-type": "Album", "secondary-types": ["Live"],
             "first-release-date": "2023-05-01"},
            {"title": "Future Album", "primary-type": "Album", "first-release-date": "2999-01-01"},
        ]}

    monkeypatch.setattr(countries, "_mb_get", mb_get)

    assert countries._latest_album("Alvvays") == {"title": "Blue Rev", "date": "2022-10-07"}
    assert asked == [("release-group", {"artist": "mbid-1", "type": "album", "limit": 100})]
    monkeypatch.setattr(countries, "_musicbrainz_artists", lambda _name: [])
    assert countries._latest_album("Nobody") == {}


def test_deezer_fans_match_the_exact_artist_and_errors_are_not_answers(monkeypatch):
    import pytest

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    rows = {"data": [{"name": "Alvvays Tribute", "nb_fan": 900000}, {"name": "ALVVAYS", "nb_fan": 61000}]}
    monkeypatch.setattr(countries.requests, "get", lambda *_a, **_k: Response(rows))
    assert countries._deezer_fans("Alvvays") == {"fans": 61000}  # трибьют с похожим именем не в счёт
    monkeypatch.setattr(countries.requests, "get", lambda *_a, **_k: Response({"data": []}))
    assert countries._deezer_fans("Nobody") == {"fans": 0}
    monkeypatch.setattr(countries.requests, "get", lambda *_a, **_k: Response({"error": {"code": 4}}))
    with pytest.raises(RuntimeError):  # лимит Deezer — сбой, а не «0 поклонников»
        countries._deezer_fans("Alvvays")


def test_band_with_ampersand_is_looked_up_by_its_full_name(monkeypatch):
    known = {"Chase & Status": [{"name": "Chase & Status", "type": "Group", "country": "GB",
                                 "life-span": {"begin": "2003"}}],
             "Chase": [{"name": "Chase", "type": "Group", "country": "US",
                        "life-span": {"begin": "1969", "end": "1974", "ended": True}}],
             "Doja Cat": [{"name": "Doja Cat", "type": "Person", "country": "US"}]}
    monkeypatch.setattr(countries, "_musicbrainz_artists", lambda name: known.get(name, []))

    assert countries._known_name("Chase & Status") == "Chase & Status"  # группа, а не американская «Chase»
    assert countries._musicbrainz_country(countries._known_name("Chase & Status")) == "GB"
    assert countries._group_years("Chase & Status") == "с 2003"
    assert countries._known_name("Doja Cat & SZA") == "Doja Cat"  # совместного релиза целиком нет
