"""Регресс на баги «Рекомендации по жанру/настроению» (кино, Досуг):

1. Нажатие 😂/😱/🚀/... не отвечало — NameError: 'util' не был импортирован в bot.py,
   плюс отсутствие try/except оставляло пользователя с вечной «крутилкой».
2/3. Жанр/настроение были подсказкой, а не обязательным фильтром — TMDb discover мог
   вернуть тайтл без нужного genre_id, и это не проверялось перед показом карточки.
4. Блок «💡 Потому что вам понравился X» брался из tmdb.detail(), закэшированного
   ОБЩИМ TTL-кэшем по ссылке — мутация этого объекта «утекала» между не связанными
   рекомендациями (у сериала мог остаться because от совсем другого запроса/юзера).
"""
import asyncio

import pytest

import leisure
import movie_engine
import tmdb
from ui import leisure as leisure_ui


@pytest.mark.unit
def test_bot_imports_util_module_directly():
    """bot.py вызывает util.ack_loading/util.clear_loading как атрибуты модуля —
    без прямого `import util` это NameError при первом же нажатии жанра/настроения."""
    import bot
    assert hasattr(bot, "util")
    assert bot.util is not None


# ---------- баг №4: мутация общего TMDb-detail кэша ----------

@pytest.mark.unit
def test_candidate_to_card_does_not_mutate_shared_tmdb_cache(monkeypatch):
    cached_detail = {"id": 42, "name": "Разделение", "name_en": "Severance",
                      "kind": "tv", "genre_ids": [18], "rating": 8.2}

    monkeypatch.setattr(tmdb, "detail", lambda tid, kind: cached_detail)

    candidate_a = {"id": 42, "kind": "tv", "because": "Элита", "via": "recommendations"}
    it_a, tm_a = leisure._candidate_to_card("cid1", candidate_a)
    assert tm_a.get("because") == "Элита"

    # Тот же тайтл, другой контекст (discover по жанру) - НЕ должен унаследовать because
    # от предыдущего вызова через мутацию общего закэшированного объекта.
    candidate_b = {"id": 42, "kind": "tv", "genre_ids": [18]}
    reason_b = {"kind": "genre", "label": "Драма"}
    it_b, tm_b = leisure._candidate_to_card("cid2", candidate_b, reason=reason_b)

    assert tm_b.get("because") is None
    assert tm_b.get("reason") == reason_b
    # Исходный закэшированный объект остался нетронутым.
    assert "because" not in cached_detail
    assert "reason" not in cached_detail


@pytest.mark.unit
def test_reason_line_distinguishes_recommendations_vs_similar():
    tm_rec = {"because": "Элита", "via": "recommendations"}
    tm_sim = {"because": "Разделение", "via": "similar"}
    line_rec = leisure_ui._reason_line({}, tm_rec)
    line_sim = leisure_ui._reason_line({}, tm_sim)
    assert "понравился «Элита»" in line_rec
    assert "Похоже на «Разделение»" in line_sim
    assert "понравился" not in line_sim


@pytest.mark.unit
def test_reason_line_genre_and_mood_never_say_liked():
    tm_genre = {"reason": {"kind": "genre", "label": "Комедия"}}
    tm_mood = {"reason": {"kind": "mood", "label": "Хочу подумать"}}
    line_genre = leisure_ui._reason_line({}, tm_genre)
    line_mood = leisure_ui._reason_line({}, tm_mood)
    assert "Подборка в жанре «Комедия»" in line_genre
    assert "Подборка для настроения «Хочу подумать»" in line_mood
    assert "понравился" not in line_genre
    assert "понравился" not in line_mood


# ---------- баги №2/3: жанр/настроение как обязательный фильтр ----------

@pytest.mark.unit
def test_passes_genre_gate_requires_all_for_genre_search():
    comedy_and_drama = {"genre_ids": [35, 18]}
    drama_only = {"genre_ids": [18]}
    assert leisure._passes_genre_gate(comedy_and_drama, require_genre_ids=[35]) is True
    assert leisure._passes_genre_gate(drama_only, require_genre_ids=[35]) is False


@pytest.mark.unit
def test_passes_genre_gate_requires_any_for_mood_search():
    thriller = {"genre_ids": [53]}
    romance = {"genre_ids": [10749]}
    mood_genres = [27, 53]  # "scary" -> ужасы, триллер
    assert leisure._passes_genre_gate(thriller, require_any_genre_ids=mood_genres) is True
    assert leisure._passes_genre_gate(romance, require_any_genre_ids=mood_genres) is False


@pytest.mark.unit
def test_discover_pick_skips_candidate_missing_required_genre(monkeypatch):
    """Discover может (в силу устаревшего кэша/неполных данных TMDb) вернуть тайтл без
    нужного жанра первым в списке — такой кандидат должен быть отброшен, а не показан."""
    off_genre = {"id": 1, "kind": "movie", "name": "Драма без комедии",
                 "genre_ids": [18], "rating": 8.0}
    on_genre = {"id": 2, "kind": "movie", "name": "Настоящая комедия",
                "genre_ids": [35], "rating": 7.0}

    def fake_discover(kind, genre_ids=None, min_rating=None, keywords=None, **kw):
        if kind != "movie":
            return []
        return [off_genre, on_genre]

    monkeypatch.setattr(tmdb, "discover", fake_discover)
    monkeypatch.setattr(movie_engine, "taste_profile", lambda cid, resolve_details=False: {})
    monkeypatch.setattr(movie_engine, "_excluded_norms", lambda cid: set())
    monkeypatch.setattr(tmdb, "detail", lambda tid, kind: None)

    it, tm = leisure._discover_pick(
        "cid", [35], {}, require_genre_ids=[35],
        reason={"kind": "genre", "label": "Комедия"})

    assert tm is not None
    assert tm["name"] == "Настоящая комедия"
    assert 35 in tm["genre_ids"]


@pytest.mark.unit
def test_discover_pick_returns_none_when_nothing_matches_genre_gate(monkeypatch):
    off_genre = {"id": 1, "kind": "movie", "name": "Только драма", "genre_ids": [18], "rating": 8.0}

    monkeypatch.setattr(tmdb, "discover", lambda kind, **kw: [off_genre] if kind == "movie" else [])
    monkeypatch.setattr(movie_engine, "taste_profile", lambda cid, resolve_details=False: {})
    monkeypatch.setattr(movie_engine, "_excluded_norms", lambda cid: set())

    it, tm = leisure._discover_pick("cid", [35], {}, require_genre_ids=[35])
    assert it is None
    assert tm is None


# ---------- баг №1: нажатие кнопки жанра/настроения не должно молчать при ошибке ----------

@pytest.mark.unit
def test_send_movie_by_genre_reports_error_instead_of_hanging(monkeypatch):
    """Если _discover_pick бросает исключение (сеть TMDb упала), пользователь обязан
    получить сообщение об ошибке, а не остаться без ответа."""
    sent = []

    async def fake_safe_error(bot, cid, exc, **kw):
        sent.append(str(exc))

    def boom(*a, **kw):
        raise RuntimeError("TMDb недоступен")

    monkeypatch.setattr(leisure, "_discover_pick", boom)
    monkeypatch.setattr(leisure.verify, "safe_error", fake_safe_error)

    class DummyBot:
        async def send_message(self, **kw):
            pass

    asyncio.run(leisure.send_movie_by_genre(DummyBot(), "cid", "35"))
    assert sent and "TMDb" in sent[0]


@pytest.mark.unit
def test_send_movie_by_mood_reports_error_instead_of_hanging(monkeypatch):
    sent = []

    async def fake_safe_error(bot, cid, exc, **kw):
        sent.append(str(exc))

    def boom(*a, **kw):
        raise RuntimeError("LLM недоступен")

    monkeypatch.setattr(leisure, "_mood_to_genres", boom)
    monkeypatch.setattr(leisure.verify, "safe_error", fake_safe_error)

    class DummyBot:
        async def send_message(self, **kw):
            pass

    asyncio.run(leisure.send_movie_by_mood(DummyBot(), "cid", "think"))
    assert sent and "LLM" in sent[0]
