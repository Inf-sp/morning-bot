"""PR3c: view-режим cleanup.py для «Словарь» (d_<lang>_<kind>) и
«Просмотренное/Прочитанное» (wl/rl) — стабильный id + revision коллекции,
короткий callback_data (clt:/clp:/cla:/cld:).

Область: config.DICT_KEY (фильтр по lang+kind внутри общей коллекции),
config.WATCHLIST_KEY, config.READLIST_KEY. См. docs/audit-cleanup-plan.md, PR3c.
"""
import asyncio

import pytest

import cleanup
import config
import store

CID = "cleanup-view-dict-watchlist-cid"


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


class _FakeMessage:
    def __init__(self, bot, cid):
        self._bot = bot
        self._cid = cid

    async def edit_text(self, text, **kw):
        self._bot.messages.append({"chat_id": self._cid, "text": text, **kw})


class _FakeQuery:
    def __init__(self, bot, cid):
        self.message = _FakeMessage(bot, cid)


@pytest.fixture(autouse=True)
def _clean():
    keys = (config.DICT_KEY, config.WATCHLIST_KEY, config.READLIST_KEY)
    for key in keys:
        store._mem.pop(key, None)
        store._list_revisions.pop(f"{key}:{CID}", None)
    cleanup._views.clear()
    yield
    for key in keys:
        store._mem.pop(key, None)
        store._list_revisions.pop(f"{key}:{CID}", None)
    cleanup._views.clear()


def _seed_dict():
    store.set_list(config.DICT_KEY, CID, [
        {"lang": "nl", "word": "huis", "ru": "дом", "kind": "word"},
        {"lang": "nl", "word": "kat", "ru": "кот", "kind": "word"},
        {"lang": "nl", "word": "tot ziens", "ru": "до свидания", "kind": "phrase"},
        {"lang": "en", "word": "house", "ru": "дом", "kind": "word"},
    ])


def _seed_watchlist(items):
    store.set_list(config.WATCHLIST_KEY, CID, items)


def _seed_readlist(items):
    store.set_list(config.READLIST_KEY, CID, items)


def _kb_rows(bot):
    return bot.messages[-1]["reply_markup"].inline_keyboard


def _callbacks(bot):
    return [btn.callback_data for row in _kb_rows(bot) for btn in row]


def _only_view_id():
    assert len(cleanup._views) == 1
    return next(iter(cleanup._views))


# ---------- Словарь: фильтр lang+kind внутри общей коллекции ----------

@pytest.mark.unit
def test_open_view_filters_dict_by_lang_and_kind():
    _seed_dict()
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "d_nl_word"))
    texts = [btn.text for row in _kb_rows(bot) for btn in row]
    assert any("huis" in t for t in texts)
    assert any("kat" in t for t in texts)
    assert not any("tot ziens" in t for t in texts)  # phrase, не word
    assert not any("house" in t for t in texts)  # en, не nl


@pytest.mark.unit
def test_dict_delete_by_id_does_not_touch_other_lang_or_kind():
    _seed_dict()
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "d_nl_word"))
    view_id = _only_view_id()
    words = store.ensure_list_ids(config.DICT_KEY, CID)
    target = next(w for w in words if w["word"] == "kat")
    short_map = cleanup._short_ids([w["id"] for w in words])
    cb = f"clt:{view_id}:{short_map[target['id']]}"
    asyncio.run(cleanup.handle_view_callback(bot, CID, cb))
    assert target["id"] in cleanup._views[view_id]["selected_ids"]

    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    remaining = store.get_list(config.DICT_KEY, CID)
    remaining_words = {w["word"] for w in remaining}
    assert remaining_words == {"huis", "tot ziens", "house"}


@pytest.mark.unit
def test_dict_delete_bumps_revision():
    _seed_dict()
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "d_nl_word"))
    view_id = _only_view_id()
    rev_before = store.get_list_revision(config.DICT_KEY, CID)
    words = store.ensure_list_ids(config.DICT_KEY, CID)
    target = next(w for w in words if w["word"] == "huis")
    cleanup._views[view_id]["selected_ids"] = {target["id"]}
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    assert store.get_list_revision(config.DICT_KEY, CID) > rev_before


@pytest.mark.unit
def test_callback_data_uses_colon_format_and_is_compact_for_dict():
    _seed_dict()
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "d_nl_word"))
    for cb in _callbacks(bot):
        if cb in ("noop",) or not cb.startswith(("clt:", "clp:", "cla:", "cld:")):
            continue
        assert ":" in cb
        assert 1 <= len(cb.encode("utf-8")) <= 64


# ---------- Просмотренное/Прочитанное: простые строки ----------

@pytest.mark.unit
def test_open_view_renders_watchlist_items():
    _seed_watchlist(["Дюна", "Матрица"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "wl"))
    texts = [btn.text for row in _kb_rows(bot) for btn in row]
    assert any("Дюна" in t for t in texts)
    assert any("Матрица" in t for t in texts)


@pytest.mark.unit
def test_open_view_renders_readlist_items():
    _seed_readlist(["1984", "Мастер и Маргарита"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "rl"))
    texts = [btn.text for row in _kb_rows(bot) for btn in row]
    assert any("1984" in t for t in texts)
    assert any("Мастер и Маргарита" in t for t in texts)


@pytest.mark.unit
def test_watchlist_toggle_and_delete_removes_only_selected():
    _seed_watchlist(["Дюна", "Матрица", "Интерстеллар"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "wl"))
    view_id = _only_view_id()
    records = store.ensure_list_ids(config.WATCHLIST_KEY, CID)
    target = next(r for r in records if r["value"] == "Матрица")
    short_map = cleanup._short_ids([r["id"] for r in records])
    cb = f"clt:{view_id}:{short_map[target['id']]}"
    asyncio.run(cleanup.handle_view_callback(bot, CID, cb))
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    remaining = {r["value"] if isinstance(r, dict) else r for r in store.get_list(config.WATCHLIST_KEY, CID)}
    assert remaining == {"Дюна", "Интерстеллар"}


@pytest.mark.unit
def test_select_all_and_clear_all_on_page_for_readlist():
    _seed_readlist(["1984", "Дюна", "Солярис"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "rl"))
    view_id = _only_view_id()
    cla_cb = next(cb for cb in _callbacks(bot) if cb.startswith(f"cla:{view_id}:"))
    asyncio.run(cleanup.handle_view_callback(bot, CID, cla_cb))
    assert len(cleanup._views[view_id]["selected_ids"]) == 3
    asyncio.run(cleanup.handle_view_callback(bot, CID, cla_cb))
    assert len(cleanup._views[view_id]["selected_ids"]) == 0


# ---------- гонка данных и TTL (та же схема, что PR3a/PR3b) ----------

@pytest.mark.unit
def test_race_deleting_other_item_blocks_stale_deletion_not_silently():
    _seed_watchlist(["Дюна", "Матрица", "Интерстеллар"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "wl"))
    view_id = _only_view_id()
    records = store.ensure_list_ids(config.WATCHLIST_KEY, CID)
    a = next(r for r in records if r["value"] == "Дюна")
    b = next(r for r in records if r["value"] == "Матрица")

    cleanup._views[view_id]["selected_ids"] = {a["id"]}
    store.remove_from_list_by_ids(config.WATCHLIST_KEY, CID, {b["id"]})

    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))

    assert "уже изменился" in bot.messages[-1]["text"].lower()
    remaining = {r["value"] if isinstance(r, dict) else r for r in store.get_list(config.WATCHLIST_KEY, CID)}
    assert "Дюна" in remaining, "A не должна быть удалена вслепую при устаревшей revision"
    assert "Матрица" not in remaining
    assert "Интерстеллар" in remaining
    assert view_id not in cleanup._views


@pytest.mark.unit
def test_expired_view_on_dict_context_shows_reopen_message():
    _seed_dict()
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "d_nl_word"))
    view_id = _only_view_id()
    cleanup._views[view_id]["created_at"] -= cleanup.VIEW_TTL_SECONDS + 60
    q = _FakeQuery(bot, CID)
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}", q=q))
    assert "уже изменился" in bot.messages[-1]["text"].lower()
    assert view_id not in cleanup._views


@pytest.mark.unit
def test_unknown_view_id_on_readlist_shows_reopen_message_without_side_effects():
    _seed_readlist(["1984"])
    bot = _FakeBot()
    asyncio.run(cleanup.handle_view_callback(bot, CID, "cld:deadbeef"))
    assert "уже изменился" in bot.messages[-1]["text"].lower()
    assert store.get_list(config.READLIST_KEY, CID) == ["1984"]


# ---------- open_cleanup делегирует на view-режим ----------

@pytest.mark.unit
def test_open_cleanup_delegates_to_view_for_dict_context():
    _seed_dict()
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "d_nl_word"))
    assert len(cleanup._views) == 1
    cb = _callbacks(bot)
    assert any(c.startswith("clt:") for c in cb if c)


@pytest.mark.unit
def test_open_cleanup_delegates_to_view_for_wl_and_rl():
    _seed_watchlist(["Дюна"])
    _seed_readlist(["1984"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "wl"))
    assert len(cleanup._views) == 1
    cleanup._views.clear()
    asyncio.run(cleanup.open_cleanup(bot, CID, "rl"))
    assert len(cleanup._views) == 1


@pytest.mark.unit
def test_open_cleanup_still_uses_old_format_for_cfg_context():
    """Регрессия: cfg_* (legacy compatibility-слой) не мигрирует ни в одном из
    PR3a-d — старый формат (позиционный индекс, подчёркивание) должен
    продолжать работать."""
    store._mem.pop(config.COUNTRIES_KEY, None)
    store.set_list(config.COUNTRIES_KEY, CID, ["Испания", "Италия"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "cfg_countries"))
    assert len(cleanup._views) == 0
    cb = _callbacks(bot)
    assert any(c.startswith("clt_cfg_countries_") for c in cb if c)
    store._mem.pop(config.COUNTRIES_KEY, None)
