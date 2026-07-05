"""Движок рекомендаций кино на TMDb: вкус, кандидаты, фильтрация, ранжирование, карточка."""
import pytest

import config
import store
import tmdb
import movie_engine as me

CID = "cinema-cid"


@pytest.fixture(autouse=True)
def _clean():
    for key in (config.WATCHLIST_KEY, config.MOVIE_SEEN_KEY, config.MOVIE_BLACKLIST_KEY,
                config.MOVIE_SHOWN_KEY, config.NOTES_KEY, config.SETTINGS_FILE):
        store._mem.pop(key, None)
    yield
    for key in (config.WATCHLIST_KEY, config.MOVIE_SEEN_KEY, config.MOVIE_BLACKLIST_KEY,
                config.MOVIE_SHOWN_KEY, config.NOTES_KEY, config.SETTINGS_FILE):
        store._mem.pop(key, None)


# ---------- нормализация ----------
@pytest.mark.unit
def test_norm_strips_year_and_punct():
    assert me._norm("Тьма (2017)!") == "тьма"
    assert me._title_only("Дюна (2021)") == "Дюна"


# ---------- недавно показанные ----------
@pytest.mark.unit
def test_mark_shown_ring_and_dedup():
    for n in range(50):
        me.mark_shown(CID, f"Film {n}")
    shown = store.get_list(config.MOVIE_SHOWN_KEY, CID)
    assert len(shown) == me.SHOWN_LIMIT
    me.mark_shown(CID, "Film 49")  # уже есть → не дублируется
    assert store.get_list(config.MOVIE_SHOWN_KEY, CID).count("Film 49") == 1


# ---------- исключения ----------
@pytest.mark.unit
def test_excluded_includes_all_lists():
    store.set_list(config.WATCHLIST_KEY, CID, ["Тьма"])
    store.set_list(config.MOVIE_SEEN_KEY, CID, ["Дюна"])
    store.set_list(config.MOVIE_BLACKLIST_KEY, CID, ["Отбросы"])
    me.mark_shown(CID, "Разделение")
    ex = me._excluded_norms(CID)
    assert {"тьма", "дюна", "отбросы", "разделение"} <= ex


# ---------- профиль вкуса ----------
@pytest.mark.unit
def test_taste_profile_aggregates(monkeypatch):
    store.set_list(config.WATCHLIST_KEY, CID, ["Тьма", "Дюна"])
    monkeypatch.setattr(tmdb, "search_id", lambda t, kind=None: {"id": 1 if "тьм" in me._norm(t) else 2, "kind": "tv"})
    monkeypatch.setattr(tmdb, "detail", lambda i, k: {
        1: {"id": 1, "kind": "tv", "genre_ids": [18, 9648], "countries": ["DE"], "director": "", "year": "2017", "rating": 8.7},
        2: {"id": 2, "kind": "movie", "genre_ids": [878, 18], "countries": ["US"], "director": "Вильнёв", "year": "2021", "rating": 8.0},
    }[i])
    taste = me.taste_profile(CID)
    assert len(taste["anchors"]) == 2
    assert taste["genres"][18] == 2   # драма в обоих
    assert taste["directors"]["Вильнёв"] == 1


# ---------- кандидаты + freq ----------
@pytest.mark.unit
def test_collect_candidates_dedup_and_freq(monkeypatch):
    store.set_list(config.WATCHLIST_KEY, CID, ["Тьма", "Дюна"])
    monkeypatch.setattr(tmdb, "search_id", lambda t, kind=None: {"id": 1 if "тьм" in me._norm(t) else 2, "kind": "tv"})
    monkeypatch.setattr(tmdb, "detail", lambda i, k: {"id": i, "kind": k, "genre_ids": [18], "countries": [], "director": "", "year": "2020", "rating": 8})
    shared = {"id": 99, "kind": "tv", "name": "Разделение", "genre_ids": [18], "rating": 8.4, "countries": ["US"]}
    monkeypatch.setattr(tmdb, "recommendations", lambda i, k: [dict(shared)])
    monkeypatch.setattr(tmdb, "similar", lambda i, k: [])
    taste = me.taste_profile(CID)
    pool = me.collect_candidates(taste)
    key = "tv:99"
    assert key in pool
    assert pool[key]["freq"] == 2  # от обоих anchors


# ---------- фильтрация по рейтингу и исключениям ----------
@pytest.mark.unit
def test_filter_by_rating_and_exclusions():
    store.set_list(config.MOVIE_SEEN_KEY, CID, ["Уже смотрел"])
    pool = {
        "movie:1": {"id": 1, "kind": "movie", "name": "Уже смотрел", "rating": 9.0, "genre_ids": []},
        "movie:2": {"id": 2, "kind": "movie", "name": "Хороший", "rating": 7.5, "genre_ids": []},
        "movie:3": {"id": 3, "kind": "movie", "name": "Низкий", "rating": 6.0, "genre_ids": []},
    }
    out = me.filter_candidates(CID, pool, 7.0)
    names = {c["name"] for c in out}
    assert names == {"Хороший"}


@pytest.mark.unit
def test_recommend_lowers_threshold(monkeypatch):
    store.set_list(config.WATCHLIST_KEY, CID, ["Тьма"])
    monkeypatch.setattr(tmdb, "search_id", lambda t, kind=None: {"id": 1, "kind": "tv"})
    monkeypatch.setattr(tmdb, "detail", lambda i, k: {"id": 1, "kind": "tv", "genre_ids": [18], "countries": [], "director": "", "year": "2020", "rating": 8})
    # единственный кандидат с рейтингом 6.6 — проходит только на ступени 6.5
    monkeypatch.setattr(tmdb, "recommendations", lambda i, k: [{"id": 5, "kind": "tv", "name": "Средний", "genre_ids": [18], "rating": 6.6, "countries": []}])
    monkeypatch.setattr(tmdb, "similar", lambda i, k: [])
    cands, _ = me.recommend(CID)
    assert cands and cands[0]["name"] == "Средний"


@pytest.mark.unit
def test_recommend_empty_when_no_anchors():
    cands, taste = me.recommend(CID)
    assert cands == []


# ---------- ранжирование: freq и совпадение жанров ----------
@pytest.mark.unit
def test_rank_prefers_multi_anchor_and_genre_match():
    taste = {"genres": {18: 2, 878: 1}, "countries": {}, "directors": {}, "kind_pref": None}
    a = {"id": 1, "kind": "movie", "name": "A", "genre_ids": [18], "rating": 7.5, "freq": 2}
    b = {"id": 2, "kind": "movie", "name": "B", "genre_ids": [35], "rating": 7.5, "freq": 1}
    ranked = me.rank([b, a], taste)
    assert ranked[0]["name"] == "A"


# ---------- tmdb.normalize ----------
@pytest.mark.unit
def test_tmdb_normalize_maps_fields():
    d = tmdb.normalize({"id": 7, "title": "Прибытие", "original_title": "Arrival",
                        "vote_average": 7.9, "genre_ids": [878, 18], "media_type": "movie",
                        "release_date": "2016-11-11", "poster_path": "/p.jpg", "overview": "x"})
    assert d["name"] == "Прибытие" and d["year"] == "2016" and d["kind"] == "movie"
    assert "фантастика" in d["genres"] and d["poster"].endswith("/p.jpg")


# ---------- карточка: детали и причина ----------
@pytest.mark.unit
def test_card_shows_series_details_and_reason():
    from ui import leisure as lu
    tm = {"kind": "tv", "name": "Разделение", "name_en": "Severance", "year": "2022",
          "genres": "драма", "rating": 8.4, "seasons": 2, "episodes": 16,
          "status": "Returning Series", "next_episode": {"air_date": "2024-10-18"},
          "episode_runtime": 50, "overview": "о", "because": "Тьма", "url": "u"}
    _, msg = lu.movie_card({"title": "Разделение", "hook": ""}, tm)
    # Новый компактный формат: одна строка деталей, статус — ровно один вариант.
    assert "2 сезона • 16 серий" in msg.text
    assert "Следующая серия — 18 октября" in msg.text
    # Нет дубля статуса: раз есть дата серии, «Продолжается»/«Новый сезон» не показываем.
    assert "Продолжается" not in msg.text
    assert "Новый сезон ожидается" not in msg.text
    assert "Потому что вам понравился «Тьма»" in msg.text


@pytest.mark.unit
def test_normalize_uses_latin_when_localized_unreadable():
    # Тайское локализованное название → показываем латинский оригинал.
    d = tmdb.normalize({"id": 1, "name": "กุหลาบเกราะเพชร", "original_name": "Petch Roy Ruk",
                        "vote_average": 8.0, "genre_ids": [18], "media_type": "tv",
                        "first_air_date": "2019-01-01"})
    assert d["name"] == "Petch Roy Ruk"


@pytest.mark.unit
def test_card_finished_series_single_status():
    from ui import leisure as lu
    tm = {"kind": "tv", "name": "X", "year": "2019", "genres": "драма", "rating": 8.0,
          "seasons": 1, "episodes": 15, "status": "Ended", "next_episode": None,
          "episode_runtime": 97, "overview": "о", "because": "Y"}
    _, msg = lu.movie_card({"title": "X", "hook": ""}, tm)
    assert "Завершено · 1 сезон • 15 серий" in msg.text
    assert "Новый сезон" not in msg.text and "Продолжается" not in msg.text


@pytest.mark.unit
def test_reason_clips_long_anchor():
    from ui import leisure as lu
    long_anchor = "Изгнанный из отряда героя, я решил поселиться в глубинке одиноко"
    tm = {"kind": "movie", "name": "A", "year": "2020", "genres": "драма", "rating": 7.5,
          "runtime": 100, "countries": ["JP"], "overview": "о", "because": long_anchor}
    _, msg = lu.movie_card({"title": "A", "hook": ""}, tm)
    assert "…»" in msg.text  # длинный anchor обрезан


@pytest.mark.unit
def test_card_shows_movie_details():
    from ui import leisure as lu
    tm = {"kind": "movie", "name": "Прибытие", "year": "2016", "genres": "фантастика",
          "rating": 7.9, "runtime": 116, "countries": ["US"], "studio": "Paramount",
          "overview": "о", "because": "Дюна", "url": "u"}
    _, msg = lu.movie_card({"title": "Прибытие", "hook": ""}, tm)
    # Новый формат: длительность и страна в одной строке; студия убрана как лишняя.
    assert "116 мин · US" in msg.text
    assert "Paramount" not in msg.text
    assert "/10 TMDb" not in msg.text
