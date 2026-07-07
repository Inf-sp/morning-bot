"""PR3a: view-режим cleanup.py для «Сохранённое» (nb/nb_*) — стабильный id +
revision коллекции, короткий callback_data (clt:/clp:/cla:/cld:, двоеточие).

Область: config.NOTES_KEY, bucket="fav". См. docs/audit-cleanup-plan.md, PR3a.
"""
import asyncio

import pytest

import cleanup
import config
import store

CID = "cleanup-view-notes-cid"


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
    store._mem.pop(config.NOTES_KEY, None)
    store._list_revisions.pop(f"{config.NOTES_KEY}:{CID}", None)
    cleanup._views.clear()
    yield
    store._mem.pop(config.NOTES_KEY, None)
    store._list_revisions.pop(f"{config.NOTES_KEY}:{CID}", None)
    cleanup._views.clear()


def _seed(n):
    store.set_list(config.NOTES_KEY, CID, [
        {"date": "01.01", "text": f"заметка {i}", "source": "Прочее", "bucket": "fav"}
        for i in range(n)
    ])


def _kb_rows(bot):
    return bot.messages[-1]["reply_markup"].inline_keyboard


def _callbacks(bot):
    return [btn.callback_data for row in _kb_rows(bot) for btn in row]


def _only_view_id():
    assert len(cleanup._views) == 1
    return next(iter(cleanup._views))


# ---------- базовый флоу: открыть → отметить → удалить ----------

@pytest.mark.unit
def test_open_view_creates_short_lived_state_and_renders_items():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    assert len(cleanup._views) == 1
    texts = [btn.text for row in _kb_rows(bot) for btn in row]
    assert any("заметка 0" in t for t in texts)
    assert any("заметка 1" in t for t in texts)


@pytest.mark.unit
def test_callback_data_uses_colon_format_and_is_compact():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    for cb in _callbacks(bot):
        if cb in ("noop",) or not cb.startswith(("clt:", "clp:", "cla:", "cld:")):
            continue
        assert ":" in cb
        assert 1 <= len(cb.encode("utf-8")) <= 64


@pytest.mark.unit
def test_toggle_marks_item_and_delete_removes_only_selected():
    _seed(3)
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    view_id = _only_view_id()
    notes = store.ensure_list_ids(config.NOTES_KEY, CID)
    target = next(n for n in notes if n["text"] == "заметка 1")
    short_map = cleanup._short_ids([n["id"] for n in notes])
    target_cb = f"clt:{view_id}:{short_map[target['id']]}"
    asyncio.run(cleanup.handle_view_callback(bot, CID, target_cb))
    assert target["id"] in cleanup._views[view_id]["selected_ids"]

    cld_cb = f"cld:{view_id}"
    asyncio.run(cleanup.handle_view_callback(bot, CID, cld_cb))
    remaining = store.get_list(config.NOTES_KEY, CID)
    assert len(remaining) == 2
    assert all(n["text"] != "заметка 1" for n in remaining)


@pytest.mark.unit
def test_delete_bumps_revision():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    view_id = _only_view_id()
    rev_before = store.get_list_revision(config.NOTES_KEY, CID)
    notes = store.ensure_list_ids(config.NOTES_KEY, CID)
    short_map = cleanup._short_ids([n["id"] for n in notes])
    cleanup._views[view_id]["selected_ids"] = {notes[0]["id"]}
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    assert store.get_list_revision(config.NOTES_KEY, CID) > rev_before


@pytest.mark.unit
def test_select_all_and_clear_all_on_page():
    _seed(3)
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    view_id = _only_view_id()
    cla_cb = next(cb for cb in _callbacks(bot) if cb.startswith(f"cla:{view_id}:"))
    asyncio.run(cleanup.handle_view_callback(bot, CID, cla_cb))
    assert len(cleanup._views[view_id]["selected_ids"]) == 3
    asyncio.run(cleanup.handle_view_callback(bot, CID, cla_cb))
    assert len(cleanup._views[view_id]["selected_ids"]) == 0


# ---------- гонка: удалить B напрямую → нажать удаление A должно сработать ----------

@pytest.mark.unit
def test_race_deleting_other_item_blocks_stale_deletion_not_silently():
    """Отметить A -> в параллельном вызове (эмуляция другого устройства/сессии)
    удалить B напрямую через store, что бампает revision -> нажать
    подтверждение удаления A в устаревшем view -> A НЕ удаляется вслепую
    (revision разошлась), показывается сообщение о необходимости переоткрыть
    список. Это и есть защита от гонки «удалить не тот элемент», описанная в
    P1-4/PR3a: сравнение revision важнее позиционного индекса."""
    _seed(3)
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    view_id = _only_view_id()
    notes = store.ensure_list_ids(config.NOTES_KEY, CID)
    a = next(n for n in notes if n["text"] == "заметка 0")
    b = next(n for n in notes if n["text"] == "заметка 1")

    revision_at_open = cleanup._views[view_id]["revision"]
    cleanup._views[view_id]["selected_ids"] = {a["id"]}

    # "параллельная" гонка: кто-то другой удаляет B напрямую через store,
    # revision коллекции при этом бампается — view остался на старой revision.
    store.remove_from_list_by_ids(config.NOTES_KEY, CID, {b["id"]})
    assert store.get_list_revision(config.NOTES_KEY, CID) != revision_at_open

    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))

    assert "уже изменился" in bot.messages[-1]["text"].lower()
    remaining_texts = {n["text"] for n in store.get_list(config.NOTES_KEY, CID)}
    assert "заметка 0" in remaining_texts, "A не должна быть удалена вслепую при устаревшей revision"
    assert "заметка 1" not in remaining_texts, "B удалена гонкой напрямую, это ожидаемо"
    assert "заметка 2" in remaining_texts
    assert view_id not in cleanup._views, "устаревший view инвалидируется"


# ---------- устаревший callback: revision разошлась ----------

@pytest.mark.unit
def test_stale_revision_shows_message_and_does_not_delete_blindly():
    _seed(3)
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    view_id = _only_view_id()
    notes = store.ensure_list_ids(config.NOTES_KEY, CID)
    a = next(n for n in notes if n["text"] == "заметка 0")
    cleanup._views[view_id]["selected_ids"] = {a["id"]}

    # Искусственно "состариваем" revision, зафиксированную в view (эмулируем,
    # что коллекция изменилась после открытия экрана, но до нажатия).
    cleanup._views[view_id]["revision"] = -999

    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))

    assert "уже изменился" in bot.messages[-1]["text"].lower()
    remaining = store.get_list(config.NOTES_KEY, CID)
    assert len(remaining) == 3  # ничего не удалено вслепую
    assert view_id not in cleanup._views  # view инвалидирован


@pytest.mark.unit
def test_expired_view_id_shows_reopen_message():
    """handle_view_callback сам обнаруживает истёкший TTL — не полагается на
    внешний вызов _purge_expired_views()."""
    _seed(1)
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "nb"))
    view_id = _only_view_id()
    cleanup._views[view_id]["created_at"] -= cleanup.VIEW_TTL_SECONDS + 60
    q = _FakeQuery(bot, CID)
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}", q=q))
    assert "уже изменился" in bot.messages[-1]["text"].lower()
    assert view_id not in cleanup._views


@pytest.mark.unit
def test_unknown_view_id_shows_reopen_message_without_side_effects():
    bot = _FakeBot()
    asyncio.run(cleanup.handle_view_callback(bot, CID, "cld:deadbeef"))
    assert "уже изменился" in bot.messages[-1]["text"].lower()


# ---------- open_cleanup делегирует на view-режим для nb/nb_* ----------

@pytest.mark.unit
def test_open_cleanup_delegates_to_view_for_nb_context():
    _seed(1)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "nb"))
    assert len(cleanup._views) == 1
    cb = _callbacks(bot)
    assert any(c.startswith("clt:") for c in cb if c)


@pytest.mark.unit
def test_open_cleanup_still_uses_old_format_for_other_contexts():
    """Регрессия: остальные 8 контекстов не мигрируют в PR3a — старый формат
    (позиционный индекс, подчёркивание) должен продолжать работать как есть."""
    store._mem.pop(config.FRIDGE_KEY, None)
    store.set_list(config.FRIDGE_KEY, CID, ["молоко", "хлеб"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "fridge"))
    assert len(cleanup._views) == 0
    cb = _callbacks(bot)
    assert any(c.startswith("clt_fridge_") for c in cb if c)
    store._mem.pop(config.FRIDGE_KEY, None)
