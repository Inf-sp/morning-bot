"""PR3b: view-режим cleanup.py для «Любимое» (lv_*/lvls_*) и «Скрытое» (hid_*) —
переиспользует инфраструктуру PR3a (store.ensure_list_ids/get_list_revision,
cleanup._views/open_view/handle_view_callback), не пишет её заново.

Область: WATCHLIST_KEY/FAVCOUNTRIES_KEY/ARTISTS_KEY/BOOKS_KEY (Любимое) и
MOVIE_BLACKLIST_KEY/BOOK_BLACKLIST_KEY/MUSIC_DISLIKE_KEY/TRAVEL_DISLIKE_KEY
(Скрытое). См. docs/cleanup.md, PR3b.
"""
import asyncio

import pytest

import cleanup
import config
import store

CID = "cleanup-view-love-hidden-cid"

_ALL_KEYS = (
    config.WATCHLIST_KEY, config.FAVCOUNTRIES_KEY, config.ARTISTS_KEY, config.BOOKS_KEY,
    config.MOVIE_BLACKLIST_KEY, config.BOOK_BLACKLIST_KEY, config.MUSIC_DISLIKE_KEY,
    config.TRAVEL_DISLIKE_KEY,
)


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


@pytest.fixture(autouse=True)
def _clean():
    for key in _ALL_KEYS:
        store._mem.pop(key, None)
        store._list_revisions.pop(f"{key}:{CID}", None)
    cleanup._views.clear()
    yield
    for key in _ALL_KEYS:
        store._mem.pop(key, None)
        store._list_revisions.pop(f"{key}:{CID}", None)
    cleanup._views.clear()


def _kb_rows(bot):
    return bot.messages[-1]["reply_markup"].inline_keyboard


def _callbacks(bot):
    return [btn.callback_data for row in _kb_rows(bot) for btn in row]


def _texts(bot):
    return [btn.text for row in _kb_rows(bot) for btn in row]


def _only_view_id():
    assert len(cleanup._views) == 1
    return next(iter(cleanup._views))


# ---------- Любимое: простые строки (фильмы/артисты/книги) ----------

@pytest.mark.unit
def test_lv_movies_open_view_renders_and_uses_watchlist_key():
    store.set_list(config.WATCHLIST_KEY, CID, ["Дюна", "Аритмия"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "lv_movies"))
    assert len(cleanup._views) == 1
    assert any("Дюна" in t for t in _texts(bot))
    assert any("Аритмия" in t for t in _texts(bot))
    assert any(cb.startswith("clt:") for cb in _callbacks(bot) if cb)


@pytest.mark.unit
def test_lv_movies_has_add_button_with_correct_callback():
    store.set_list(config.WATCHLIST_KEY, CID, ["Дюна"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "lv_movies"))
    assert "as_loveadd_movies" in _callbacks(bot)
    assert any("Добавить фильм" in t for t in _texts(bot))


@pytest.mark.unit
def test_lv_movies_delete_removes_only_selected_by_id():
    store.set_list(config.WATCHLIST_KEY, CID, ["Дюна", "Аритмия", "Патерсон"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "lv_movies"))
    view_id = _only_view_id()
    records = store.ensure_list_ids(config.WATCHLIST_KEY, CID)
    target = next(r for r in records if r["value"] == "Аритмия")
    short_map = cleanup._short_ids([r["id"] for r in records])
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"clt:{view_id}:{short_map[target['id']]}"))
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    remaining = {r["value"] if "value" in r else r for r in store.get_list(config.WATCHLIST_KEY, CID)}
    assert remaining == {"Дюна", "Патерсон"}


@pytest.mark.unit
def test_action_label_is_ubrat_iz_lyubimogo_for_lv():
    store.set_list(config.ARTISTS_KEY, CID, ["The xx"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "lv_artists"))
    view_id = _only_view_id()
    cleanup._views[view_id]["selected_ids"] = set(cleanup._views[view_id]["selected_ids"]) | \
        {store.ensure_list_ids(config.ARTISTS_KEY, CID)[0]["id"]}
    asyncio.run(cleanup._render_view(bot, CID, view_id))
    assert any("Убрать из любимого" in t for t in _texts(bot))


# ---------- Любимое: dict-элементы (страны, {"name","flag"}) ----------

@pytest.mark.unit
def test_lv_countries_renders_dict_records_by_name():
    store.set_list(config.FAVCOUNTRIES_KEY, CID, [{"name": "Норвегия", "flag": "🇳🇴"},
                                                    {"name": "Япония", "flag": "🇯🇵"}])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "lv_countries"))
    assert any("Норвегия" in t for t in _texts(bot))
    assert any("Япония" in t for t in _texts(bot))


@pytest.mark.unit
def test_lv_countries_delete_keeps_dict_shape_for_remaining():
    store.set_list(config.FAVCOUNTRIES_KEY, CID, [{"name": "Норвегия", "flag": "🇳🇴"},
                                                    {"name": "Япония", "flag": "🇯🇵"}])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "lv_countries"))
    view_id = _only_view_id()
    records = store.ensure_list_ids(config.FAVCOUNTRIES_KEY, CID)
    target = next(r for r in records if r["name"] == "Норвегия")
    short_map = cleanup._short_ids([r["id"] for r in records])
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"clt:{view_id}:{short_map[target['id']]}"))
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    remaining = store.get_list(config.FAVCOUNTRIES_KEY, CID)
    assert len(remaining) == 1
    assert remaining[0]["name"] == "Япония"
    assert remaining[0]["flag"] == "🇯🇵"  # остальные поля dict не потеряны


# ---------- Скрытое: без кнопки «Добавить», текст действия «Вернуть в рекомендации» ----------

@pytest.mark.unit
def test_hid_movies_no_add_button():
    store.set_list(config.MOVIE_BLACKLIST_KEY, CID, ["Плохой фильм"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "hid_movies"))
    assert not any("Добавить" in t for t in _texts(bot))
    assert not any(cb and cb.startswith(("as_loveadd_", "ls_loveadd_")) for cb in _callbacks(bot))


@pytest.mark.unit
def test_hid_movies_action_label_is_vernut_v_rekomendatsii():
    store.set_list(config.MOVIE_BLACKLIST_KEY, CID, ["Плохой фильм"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "hid_movies"))
    view_id = _only_view_id()
    full_id = store.ensure_list_ids(config.MOVIE_BLACKLIST_KEY, CID)[0]["id"]
    cleanup._views[view_id]["selected_ids"] = {full_id}
    asyncio.run(cleanup._render_view(bot, CID, view_id))
    assert any("Вернуть в рекомендации" in t for t in _texts(bot))


@pytest.mark.unit
def test_hid_movies_return_does_not_touch_favorites():
    """«Вернуть в рекомендации» убирает элемент из чёрного списка, но НЕ
    добавляет его в WATCHLIST_KEY — не превращать нейтральный откат в скрытый
    сигнал «мне нравится» (см. docstring cleanup.py)."""
    store.set_list(config.MOVIE_BLACKLIST_KEY, CID, ["Плохой фильм"])
    store.set_list(config.WATCHLIST_KEY, CID, [])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "hid_movies"))
    view_id = _only_view_id()
    full_id = store.ensure_list_ids(config.MOVIE_BLACKLIST_KEY, CID)[0]["id"]
    cleanup._views[view_id]["selected_ids"] = {full_id}
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    assert store.get_list(config.MOVIE_BLACKLIST_KEY, CID) == []
    assert store.get_list(config.WATCHLIST_KEY, CID) == []


# ---------- гонка и устаревший callback — та же схема, что и в PR3a ----------

@pytest.mark.unit
def test_race_on_lv_movies_blocks_stale_deletion():
    store.set_list(config.WATCHLIST_KEY, CID, ["Дюна", "Аритмия", "Патерсон"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "lv_movies"))
    view_id = _only_view_id()
    records = store.ensure_list_ids(config.WATCHLIST_KEY, CID)
    a = next(r for r in records if r["value"] == "Дюна")
    b = next(r for r in records if r["value"] == "Аритмия")
    revision_at_open = cleanup._views[view_id]["revision"]
    cleanup._views[view_id]["selected_ids"] = {a["id"]}

    store.remove_from_list_by_ids(config.WATCHLIST_KEY, CID, {b["id"]})
    assert store.get_list_revision(config.WATCHLIST_KEY, CID) != revision_at_open

    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))

    assert "уже изменился" in bot.messages[-1]["text"].lower()
    remaining = {r["value"] if "value" in r else r for r in store.get_list(config.WATCHLIST_KEY, CID)}
    assert "Дюна" in remaining  # A не удалена вслепую
    assert "Аритмия" not in remaining  # B удалена гонкой, это ожидаемо
    assert view_id not in cleanup._views


@pytest.mark.unit
def test_expired_view_on_hid_context_shows_reopen_message():
    store.set_list(config.BOOK_BLACKLIST_KEY, CID, ["Плохая книга"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "hid_books"))
    view_id = _only_view_id()
    cleanup._views[view_id]["created_at"] -= cleanup.VIEW_TTL_SECONDS + 60
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    assert "уже изменился" in bot.messages[-1]["text"].lower()
    assert view_id not in cleanup._views
    # open_cleanup уже проставил id при первом чтении (ensure_list_ids) — это
    # ожидаемая ленивая миграция формата хранения, не связана с TTL-веткой;
    # важно, что сама запись не удалена и не потеряна.
    remaining = store.get_list(config.BOOK_BLACKLIST_KEY, CID)
    assert len(remaining) == 1
    assert remaining[0]["value"] == "Плохая книга"


# ---------- lvls_* (leisure-путь, тот же storage-ключ, другой back) ----------

@pytest.mark.unit
def test_lvls_movies_uses_leisure_back_and_add_callback():
    store.set_list(config.WATCHLIST_KEY, CID, ["Дюна"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "lvls_movies"))
    cb = _callbacks(bot)
    assert "ls_loveadd_movies" in cb
    assert "m_leisure_settings" in cb


# ---------- регрессия: остальные немигрированные контексты не затронуты ----------

@pytest.mark.unit
def test_cfg_context_still_uses_old_format_untouched():
    """cfg_* (legacy-путь настроек стран/артистов/книг) не входит в PR3b и
    должен продолжать работать на старом позиционном формате."""
    store._mem.pop(config.COUNTRIES_KEY, None)
    store.set_list(config.COUNTRIES_KEY, CID, ["Италия"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "cfg_countries"))
    assert len(cleanup._views) == 0
    assert any(cb.startswith("clt_cfg_countries_") for cb in _callbacks(bot) if cb)
    store._mem.pop(config.COUNTRIES_KEY, None)
