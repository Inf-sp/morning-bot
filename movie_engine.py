"""Движок рекомендаций кино на данных TMDb.

Профиль вкуса собирается НА ЛЕТУ из актуальных списков пользователя (любимые/
просмотренные/отклонённые/показанные). Кандидаты берутся из TMDb Recommendations +
Similar по каждому любимому (anchor), фильтруются и ранжируются по совпадению со
вкусом. LLM здесь не используется — только TMDb-данные.

Каждый кандидат несёт поле `because` — от какого любимого он пришёл (для причины
«Потому что вам понравился X»).
"""
import re

import config
import store
import tmdb

# Стартовый порог рейтинга и ступени понижения при пустом результате.
RATING_STEPS = (7.0, 6.8, 6.5)
# Сколько последних показанных помнить (кольцевой список).
SHOWN_LIMIT = 40
# Сколько любимых брать как anchors для сбора кандидатов.
MAX_ANCHORS = 12


def _norm(s):
    """Нормализует название для сравнения: нижний регистр, без года/скобок/пунктуации."""
    s = str(s or "").lower()
    s = re.sub(r"\(\s*\d{4}\s*\)", "", s)   # убрать (2022)
    s = re.sub(r"[^\wа-яё ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_only(s):
    """Строка вида 'Название (2022)' → 'Название'. Элемент может быть строкой или
    {"id":..., "value": строка} (после захода в удаление, см. store.ensure_list_ids_via)."""
    if isinstance(s, dict):
        s = s.get("value", "")
    return re.sub(r"\s*\(\s*\d{4}\s*\)\s*$", "", str(s or "")).strip()


# ---------- недавно показанные (персистентно) ----------
def mark_shown(cid, name):
    if not name:
        return
    shown = store.get_list(config.MOVIE_SHOWN_KEY, cid)
    key = _norm(name)
    shown = [s for s in shown if _norm(s) != key] + [name]
    store.set_list(config.MOVIE_SHOWN_KEY, cid, shown[-SHOWN_LIMIT:])


def _shown_norms(cid):
    return {_norm(s) for s in store.get_list(config.MOVIE_SHOWN_KEY, cid)}


# ---------- множества исключений ----------
def _excluded_norms(cid, include_shown=True):
    """Названия, которые нельзя показывать: любимые/seen/blacklist/закладки(+показанные)."""
    keys = [config.WATCHLIST_KEY, config.MOVIE_SEEN_KEY, config.MOVIE_BLACKLIST_KEY]
    names = []
    for k in keys:
        names += store.get_list(k, cid)
    notes = store.get_list(config.NOTES_KEY, cid)
    names += [n.get("text", "") for n in notes
              if isinstance(n, dict) and "кино" in str(n.get("source", "")).lower()]
    ex = {_norm(x) for x in names}
    if include_shown:
        ex |= _shown_norms(cid)
    return ex


# ---------- профиль вкуса на лету ----------
def taste_profile(cid, resolve_details=True):
    """Собирает приоритеты вкуса из любимых. Возвращает dict + список anchors.

    anchors: [{title, id, kind, detail}] — резолвленные через TMDb любимые.
    """
    loved = [_title_only(x) for x in store.get_list(config.WATCHLIST_KEY, cid)]
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
        for via, fn in endpoints:
            for c in fn(aid, kind):
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
                    cand["anchors"] = {atitle}
                    cand["freq"] = 1
                    pool[key] = cand
    return pool


# ---------- фильтрация ----------
def filter_candidates(cid, pool, min_rating):
    excluded = _excluded_norms(cid)
    out = []
    for c in pool.values():
        if _norm(c.get("name")) in excluded:
            continue
        if (c.get("rating") or 0) < min_rating:
            continue
        out.append(c)
    return out


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
    score += (c.get("freq", 1) - 1) * 2.0  # рекомендован от нескольких любимых
    # предпочтения из настроек (приоритет, не запрет)
    if prefs:
        pg = set(prefs.get("genres") or [])
        if pg and pg.intersection(c.get("genre_ids", [])):
            score += 3.0
        if prefs.get("type_pref") in ("movie", "tv") and c.get("kind") == prefs["type_pref"]:
            score += 2.0
        if prefs.get("countries"):
            if set(prefs["countries"]).intersection(c.get("countries", []) or []):
                score += 2.0
    return score


def rank(candidates, taste, prefs=None):
    return sorted(candidates, key=lambda c: _score(c, taste, prefs), reverse=True)


# ---------- главный вход ----------
def recommend(cid, prefs=None, limit=10):
    """Список ранжированных кандидатов (без LLM). Пустой — если данных мало.

    prefs — dict предпочтений из настроек (genres/type_pref/countries/min_rating/...).
    """
    taste = taste_profile(cid)
    if not taste.get("anchors"):
        return [], taste
    pool = collect_candidates(taste)
    if not pool:
        return [], taste
    start = (prefs or {}).get("min_rating") or RATING_STEPS[0]
    steps = [r for r in RATING_STEPS if r <= start] or [RATING_STEPS[-1]]
    if start not in steps:
        steps = [start] + steps
    for min_rating in steps:
        filtered = filter_candidates(cid, pool, min_rating)
        if filtered:
            return rank(filtered, taste, prefs)[:limit], taste
    return [], taste
