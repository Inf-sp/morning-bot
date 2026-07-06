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


# ---------- клавиатура и память категории (жанр/настроение) на карточке ----------

@pytest.mark.unit
def test_movie_kb_inside_category_has_exactly_4_action_buttons_plus_back():
    """Внутри жанра/настроения — ровно 4 кнопки действия + Назад, без строки
    «По жанру/По настроению» (пользователь уже внутри категории)."""
    category = {"kind": "genre", "value": 35, "reason": {"kind": "genre", "label": "Комедия"}}
    kb = leisure._movie_kb(0, category=category)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert labels == ["✨ Заменить", "⭐️ Сохранить", "❤️ В любимые", "✅ Уже видел", "◀️ Назад"]
    assert not any("жанру" in l or "настроению" in l for l in labels)


@pytest.mark.unit
def test_movie_kb_without_category_has_exactly_4_action_buttons_plus_back():
    """Обычная (не категорийная) карточка — тоже без строки «По жанру/По настроению»,
    выбор происходит на приветственном экране раздела (send_movie_home)."""
    kb = leisure._movie_kb(0)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert labels == ["✨ Заменить", "⭐️ Сохранить", "❤️ В любимые", "✅ Уже видел", "◀️ Назад"]
    assert not any("жанру" in l or "настроению" in l for l in labels)


@pytest.mark.unit
def test_movie_kb_back_button_targets_genre_or_mood_menu():
    genre_kb = leisure._movie_kb(0, category={"kind": "genre", "value": 35, "reason": {}})
    mood_kb = leisure._movie_kb(0, category={"kind": "mood", "value": "scary", "reason": {}})
    plain_kb = leisure._movie_kb(0)

    def back_target(kb):
        return next(btn.callback_data for row in kb.inline_keyboard for btn in row if btn.text == "◀️ Назад")

    assert back_target(genre_kb) == "movie_genre_menu"
    assert back_target(mood_kb) == "movie_mood_menu"
    assert back_target(plain_kb) == "m_leisure"


@pytest.mark.unit
def test_show_discovered_stores_category_in_last_recos(monkeypatch):
    import store

    store.last_recos = {}
    store.last_source = {}
    monkeypatch.setattr(leisure.movie_engine, "mark_shown", lambda cid, name: None)

    sent = {}

    class DummyBot:
        async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
            sent["kb"] = reply_markup

    it = {"title": "Смешной фильм", "title_en": "", "hook": ""}
    tm = {"name": "Смешной фильм", "kind": "movie", "genre_ids": [35]}
    category = {"kind": "genre", "value": 35, "reason": {"kind": "genre", "label": "Комедия"}}

    asyncio.run(leisure._show_discovered(DummyBot(), "cid1", it, tm, category=category))

    rec = store.last_recos["cid1"]
    assert rec["category"] == category
    labels = [btn.text for row in sent["kb"].inline_keyboard for btn in row]
    assert "🎭 По жанру" not in labels  # карточка внутри категории, строка не нужна


@pytest.mark.unit
def test_advance_movie_stays_in_genre_category(monkeypatch):
    """«Заменить»/«В любимые»/«Уже видел»/«Сохранить» внутри жанра должны брать
    следующего кандидата ИЗ ТОЙ ЖЕ категории через _advance_in_category, а не
    сбрасываться на обычный _tmdb_engine_pick."""
    import store

    category = {"kind": "genre", "value": 35, "reason": {"kind": "genre", "label": "Комедия"}}
    store.last_recos = {"cid1": {"kind": "movie", "items": ["Старый фильм"], "category": category}}
    store.last_source = {}

    calls = {"category": 0, "engine": 0}

    async def fake_advance_in_category(cid, cat):
        calls["category"] += 1
        assert cat == category
        return {"title": "Новая комедия", "title_en": "", "hook": ""}, {"name": "Новая комедия", "kind": "movie", "genre_ids": [35]}

    async def fake_engine_pick(cid, prefs=None):
        calls["engine"] += 1
        return None, None

    monkeypatch.setattr(leisure, "_advance_in_category", fake_advance_in_category)
    monkeypatch.setattr(leisure, "_tmdb_engine_pick", fake_engine_pick)
    monkeypatch.setattr(leisure.movie_engine, "mark_shown", lambda cid, name: None)

    sent = []

    class DummyBot:
        async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
            sent.append(reply_markup)

    asyncio.run(leisure._advance_movie(DummyBot(), "cid1"))

    assert calls["category"] == 1
    assert calls["engine"] == 0  # обычный алгоритм не должен вызываться внутри категории
    labels = [btn.text for row in sent[-1].inline_keyboard for btn in row]
    assert "◀️ Назад" in labels
    back = next(btn.callback_data for row in sent[-1].inline_keyboard for btn in row if btn.text == "◀️ Назад")
    assert back == "movie_genre_menu"


@pytest.mark.unit
def test_advance_movie_without_category_uses_normal_engine(monkeypatch):
    """Без category (обычная сессия рекомендаций) поведение прежнее — обычный движок."""
    import store

    store.last_recos = {"cid1": {"kind": "movie", "items": ["Старый фильм"]}}
    store.last_source = {}

    calls = {"category": 0, "engine": 0}

    async def fake_advance_in_category(cid, cat):
        calls["category"] += 1
        return None, None

    async def fake_engine_pick(cid, prefs=None):
        calls["engine"] += 1
        return {"title": "Обычная рекомендация", "title_en": "", "hook": ""}, {"name": "Обычная рекомендация", "kind": "movie"}

    monkeypatch.setattr(leisure, "_advance_in_category", fake_advance_in_category)
    monkeypatch.setattr(leisure, "_tmdb_engine_pick", fake_engine_pick)
    monkeypatch.setattr(leisure.movie_engine, "mark_shown", lambda cid, name: None)

    sent = []

    class DummyBot:
        async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
            sent.append(reply_markup)

    asyncio.run(leisure._advance_movie(DummyBot(), "cid1"))

    assert calls["engine"] == 1
    assert calls["category"] == 0
    labels = [btn.text for row in sent[-1].inline_keyboard for btn in row]
    assert labels == ["✨ Заменить", "⭐️ Сохранить", "❤️ В любимые", "✅ Уже видел", "◀️ Назад"]


# ---------- приветственный экран раздела «Кино» (вместо мгновенной рекомендации) ----------

@pytest.mark.unit
def test_movie_home_screen_shows_loved_count_and_genres():
    from ui import leisure as leisure_ui

    msg = leisure_ui.movie_home_screen(3, ["🎭 Комедия", "😱 Ужасы"])
    assert "🎬 Кино" in msg.text
    assert "В любимых 3 фильма/сериала" in msg.text
    assert "Жанры в предпочтениях" in msg.text
    assert "🎭 Комедия" in msg.text
    assert "😱 Ужасы" in msg.text


@pytest.mark.unit
def test_movie_home_screen_empty_state():
    from ui import leisure as leisure_ui

    msg = leisure_ui.movie_home_screen(0, [])
    assert "пусто" in msg.text
    assert "Жанры в предпочтениях" not in msg.text


@pytest.mark.unit
def test_movie_home_keyboard_has_exactly_3_buttons_plus_back():
    kb = leisure._movie_home_kb()
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert labels == ["✨ Обычная рекомендация", "🎭 По жанру", "😊 По настроению", "◀️ Назад"]


@pytest.mark.unit
def test_send_movie_home_reads_loved_count_and_genre_prefs(monkeypatch):
    import store
    import settings

    monkeypatch.setattr(store, "get_list", lambda k, cid: ["Элита", "Разделение"])
    monkeypatch.setattr(settings, "get", lambda cid, key, default=None:
                         ["35", "27"] if key == "movie_genres" else default)

    sent = {}

    class DummyBot:
        async def send_message(self, chat_id, text=None, reply_markup=None, **kw):
            sent["text"] = text
            sent["kb"] = reply_markup

    asyncio.run(leisure.send_movie_home(DummyBot(), "cid1"))

    assert "В любимых 2 фильма/сериала" in sent["text"]
    assert "Комедия" in sent["text"]
    assert "Ужасы" in sent["text"]
    labels = [btn.text for row in sent["kb"].inline_keyboard for btn in row]
    assert labels == ["✨ Обычная рекомендация", "🎭 По жанру", "😊 По настроению", "◀️ Назад"]


@pytest.mark.unit
def test_watch_action_routes_to_home_screen_not_instant_reco():
    """Регресс: вход в раздел «Кино» не должен больше сразу дёргать send_recos —
    сначала приветственный экран (см. bot.py act == 'watch')."""
    import inspect

    src = inspect.getsource(__import__("bot"))
    watch_block = src.split('elif act == "watch":')[1].split("elif act ==")[0]
    assert "send_movie_home" in watch_block
    assert "send_recos" not in watch_block
