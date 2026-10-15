"""Движок рекомендаций кино на данных TMDb.

Профиль вкуса собирается НА ЛЕТУ из актуальных списков пользователя (любимые/
просмотренные/отклонённые/показанные). Кандидаты берутся из TMDb Recommendations +
Similar по каждому любимому (anchor), фильтруются и ранжируются по совпадению со
вкусом. LLM здесь не используется — только TMDb-данные.

Каждый кандидат несёт поле `because` — от какого любимого он пришёл (для причины
«Потому что вам понравился X»).
"""
import re
import math
from datetime import date, timedelta

import config
import recommendation_stoplist
import recommendation_rotation as rotation
import store
import tmdb

# Стартовый порог рейтинга и ступени понижения при пустом результате.
RATING_STEPS = (7.0,)
# Сколько последних показанных помнить (кольцевой список).
SHOWN_LIMIT = 40
# Сколько любимых брать как anchors для сбора кандидатов.
MAX_ANCHORS = 12
# Рейтинг на нескольких случайных оценках не является сигналом качества.
MIN_VOTE_COUNT = 50
MIN_SIMILAR_GENRE_RATIO = 0.5
# Планка «топового» — ступенями: (рейтинг TMDb, голосов у фильма, голосов у сериала). Сначала строгая; пусто —
# мягче, последняя — прежние 7.0 и 50 голосов, чтобы карточка не оставалась пустой. У сериалов голосов меньше.
QUALITY_STEPS = ((7.5, 1000, 300), (7.0, 300, 100), (7.0, MIN_VOTE_COUNT, MIN_VOTE_COUNT))


def top_quality(items, min_rating=0.0) -> list:
    """Кандидаты самой строгой ступени QUALITY_STEPS, где кто-то есть; порядок сохраняется.
    min_rating — порог из настроек пользователя, если он выше ступени."""
    for rating, movie_votes, tv_votes in QUALITY_STEPS:
        passed = [c for c in items if float(c.get("rating") or 0) >= max(rating, float(min_rating or 0))
                  and int(c.get("vote_count") or 0) >= (tv_votes if c.get("kind") == "tv" else movie_votes)]
        if passed:
            return passed
    return []


def _norm(s):
    """Нормализует название для сравнения: нижний регистр, без года/скобок/пунктуации."""
    s = str(s or "").lower()
    s = re.sub(r"\(\s*\d{4}\s*\)", "", s)   # убрать (2022)
    s = re.sub(r"[^\wа-яё ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_only(s):
    """Строка кино → название для TMDb, включая формат «сериал, 2023»."""
    from leisure_collection import movie_title_for_lookup

    return movie_title_for_lookup(s)


# ---------- недавно показанные (персистентно) ----------
def mark_shown(cid, name):
    if not name:
        return
    shown = store.get_list(config.MOVIE_SHOWN_KEY, cid)
    store.set_list(
        config.MOVIE_SHOWN_KEY, cid,
        rotation.remember(shown, name, limit=SHOWN_LIMIT, key=_norm),
    )


def _shown_norms(cid):
    return {_norm(s) for s in store.get_list(config.MOVIE_SHOWN_KEY, cid)}


# ---------- множества исключений ----------
def _excluded_norms(cid, include_shown=True):
    """Названия, которые нельзя показывать: любимые, стоп-лист и показанные."""
    keys = [config.FAVORITE_MOVIES_KEY]
    names = []
    for k in keys:
        names += store.get_list(k, cid)
    names += recommendation_stoplist.values(cid, "movie")
    # Элементы бывают {"id", "value"} и «Название (сериал, 2023)»: сравниваем
    # только название, иначе любимое не исключается из подбора.
    ex = {_norm(_title_only(x)) for x in names}
    if include_shown:
        ex |= _shown_norms(cid)
    return ex


# ---------- профиль вкуса на лету ----------
def taste_profile(cid, resolve_details=True):
    """Собирает приоритеты вкуса из любимых. Возвращает dict + список anchors.

    anchors: [{title, id, kind, detail}] — резолвленные через TMDb любимые.
    """
    loved = [_title_only(x) for x in store.get_list(config.FAVORITE_MOVIES_KEY, cid)]
    loved = [x for x in loved if x][:MAX_ANCHORS]
    anchors = []
    genre_freq = {}
    country_freq = {}
    director_freq = {}
    years = []
    ratings = []
    kind_freq = {"movie": 0, "tv": 0}
    for title in loved:
        base = tmdb.search_id(title)
        if not base or not base.get("id"):
            continue
        det = tmdb.detail(base["id"], base["kind"]) if resolve_details else base
        info = det or base
        anchors.append({"title": title, "id": base["id"], "kind": base["kind"], "detail": info})
        for g in info.get("genre_ids", []):
            genre_freq[g] = genre_freq.get(g, 0) + 1
        for c in info.get("countries", []) or []:
            country_freq[c] = country_freq.get(c, 0) + 1
        if info.get("director"):
            director_freq[info["director"]] = director_freq.get(info["director"], 0) + 1
        if info.get("year"):
            try:
                years.append(int(info["year"]))
            except ValueError:
                pass
        if info.get("rating"):
            ratings.append(info["rating"])
        kind_freq[base["kind"]] = kind_freq.get(base["kind"], 0) + 1
    return {
        "anchors": anchors,
        "genres": genre_freq,
        "countries": country_freq,
        "directors": director_freq,
        "median_year": sorted(years)[len(years) // 2] if years else None,
        "avg_rating": (sum(ratings) / len(ratings)) if ratings else None,
        "kind_pref": ("tv" if kind_freq["tv"] > kind_freq["movie"]
                      else "movie" if kind_freq["movie"] > kind_freq["tv"] else None),
    }


# ---------- кандидаты ----------
def collect_candidates(taste):
    """Пул кандидатов из Recommendations + Similar по каждому anchor.

    Возвращает dict нормализованный_id → candidate, где candidate имеет:
    поля TMDb + because(anchor title) + anchors(set) + freq + via(endpoint источника,
    "recommendations"|"similar" — нужен для точного текста причины: TMDb Recommendations
    даёт «Потому что понравился X», Similar — «Похоже на X», это разные утверждения).

    Если один и тот же тайтл встречается и через recommendations, и через similar (или
    от нескольких anchors), сохраняем самый ранний найденный via — recommendations сильнее
    как сигнал вкуса, поэтому проверяем его первым и не перезаписываем при повторных находках.
    """
    pool = {}
    endpoints = (("recommendations", tmdb.recommendations), ("similar", tmdb.similar))
    for a in taste.get("anchors", []):
        aid, kind, atitle = a["id"], a["kind"], a["title"]
        anchor_genres = set((a.get("detail") or {}).get("genre_ids") or [])
        for via, fn in endpoints:
            for c in fn(aid, kind):
                if c.get("adult"):
                    continue
                shared_genres = []
                if via == "similar":
                    candidate_genres = set(c.get("genre_ids") or [])
                    union = anchor_genres | candidate_genres
                    genre_ratio = len(anchor_genres & candidate_genres) / len(union) if union else 0
                    if genre_ratio < MIN_SIMILAR_GENRE_RATIO:
                        continue
                    shared_genres = [
                        tmdb.GENRES[genre_id]
                        for genre_id in sorted(anchor_genres & candidate_genres)
                        if genre_id in tmdb.GENRES
                    ]
                cid_ = c.get("id")
                if not cid_:
                    continue
                key = f"{c['kind']}:{cid_}"
                if key in pool:
                    pool[key]["freq"] += 1
                    pool[key]["anchors"].add(atitle)
                else:
                    cand = dict(c)
                    cand["because"] = atitle
                    cand["via"] = via
                    cand["shared_genres"] = shared_genres
                    cand["anchors"] = {atitle}
                    cand["freq"] = 1
                    pool[key] = cand
    return pool


# ---------- фильтрация ----------
def filter_candidates(cid, pool, min_rating):
    """Кандидаты пула без исключённых, выключенных регионов, старше 2000 года — и на самой строгой ступени
    качества, где кто-то остался (`top_quality`)."""
    import countries
    import movie_recommendation
    excluded = _excluded_norms(cid)
    regions_off = movie_recommendation.regions_off(cid)
    out = []
    for c in pool.values():
        if _norm(c.get("name")) in excluded:
            continue
        if not countries.allowed(c, regions_off):  # выключенный регион — строгий фильтр
            continue
        if c.get("adult"):
            continue
        raw_date = str(c.get("release_date") or "")[:10]
        try:
            released = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if released < date(2000, 1, 1):
            continue
        out.append(c)
    return top_quality(out, min_rating)


# ---------- ранжирование ----------
def _score(c, taste, prefs=None):
    score = 0.0
    genres = taste.get("genres", {})
    for g in c.get("genre_ids", []):
        score += genres.get(g, 0) * 2.0
    countries = taste.get("countries", {})
    for co in c.get("countries", []) or []:
        score += countries.get(co, 0) * 1.5
    if taste.get("kind_pref") and c.get("kind") == taste["kind_pref"]:
        score += 1.5
    score += (c.get("rating") or 0) * 0.5
    score += math.log10(max(1, int(c.get("vote_count") or 0))) * 0.8
    score += min(float(c.get("popularity") or 0), 100.0) * 0.02
    score += (c.get("freq", 1) - 1) * 4.0  # TMDb советует по нескольким любимым — самый надёжный сигнал вкуса
    # Действующие предпочтения из настроек (приоритет, не запрет).
    if prefs:
        if prefs.get("type_pref") in ("movie", "tv") and c.get("kind") == prefs["type_pref"]:
            score += 2.0
        if prefs.get("recency") == "new":
            try:
                released = date.fromisoformat(str(c.get("release_date") or "")[:10])
            except ValueError:
                released = None
            if released and released >= date.today() - timedelta(days=730):
                score += 2.5
    return score


def rank(candidates, taste, prefs=None):
    return sorted(candidates, key=lambda c: _score(c, taste, prefs), reverse=True)


# ---------- главный вход ----------
def recommend(cid, prefs=None, limit=10):
    """Список ранжированных кандидатов (без LLM). Пустой — если данных мало.

    prefs — dict действующих предпочтений (type_pref/recency/min_rating).
    """
    taste = taste_profile(cid)
    if not taste.get("anchors"):
        return [], taste
    pool = collect_candidates(taste)
    if not pool:
        return [], taste
    only = (prefs or {}).get("only")  # «Фильмы» / «Сериалы»: ступень качества — среди своего типа
    pool = {key: c for key, c in pool.items() if not only or (c.get("kind") or "movie") == only}
    filtered = filter_candidates(cid, pool, float((prefs or {}).get("min_rating") or 0))
    return rank(filtered, taste, prefs)[:limit], taste
