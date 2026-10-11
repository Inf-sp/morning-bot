import asyncio

import countries
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
    assert countries.title_label({"countries": ["JP"]}) == "🇯🇵 Япония"
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
        "name": "Дюна", "kind": "movie", "genres": "фантастика", "country_label": "🇺🇸 США", "year": 2021})
    assert "фантастика · 🇺🇸 США · 2021" in msg.text
    assert leisure_ui.artist_card({"artist": "Alvvays", "country_label": "🇨🇦 Канада"}).text.startswith(
        "🎸 Alvvays\n\n🇨🇦 Канада")  # после заголовка — пустая строка по UI-контракту
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
