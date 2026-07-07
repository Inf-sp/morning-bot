"""PR3d: view-режим cleanup.py для «Холодильник» (fridge), «Рецепты» (recipes) и
«Здоровье» (lagom) — последний из под-PR3, замыкает миграцию cleanup.py на
стабильный id + revision коллекции для всех контекстов, кроме kast_* (мигрирован
раньше) и cfg_* (legacy compatibility-слой, намеренно не мигрируется).

Область: config.FRIDGE_KEY, config.MY_RECIPES_KEY, memory.get_lagom/set_lagom
(поле внутри профиля, не отдельный KV-ключ — см. store.ensure_list_ids_via).
См. docs/audit-cleanup-plan.md, PR3d.
"""
import asyncio

import pytest

import cleanup
import config
import memory
import store

CID = "cleanup-view-fridge-recipes-lagom-cid"


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
    keys = (config.FRIDGE_KEY, config.MY_RECIPES_KEY)
    for key in keys:
        store._mem.pop(key, None)
        store._list_revisions.pop(f"{key}:{CID}", None)
    store._mem.pop(config.PROFILE_KEY, None)
    store._list_revisions.pop(f"{cleanup._LAGOM_REVISION_SLOT}:{CID}", None)
    cleanup._views.clear()
    yield
    for key in keys:
        store._mem.pop(key, None)
        store._list_revisions.pop(f"{key}:{CID}", None)
    store._mem.pop(config.PROFILE_KEY, None)
    store._list_revisions.pop(f"{cleanup._LAGOM_REVISION_SLOT}:{CID}", None)
    cleanup._views.clear()


def _kb_rows(bot):
    return bot.messages[-1]["reply_markup"].inline_keyboard


def _callbacks(bot):
    return [btn.callback_data for row in _kb_rows(bot) for btn in row]


def _only_view_id():
    assert len(cleanup._views) == 1
    return next(iter(cleanup._views))


# ---------- Холодильник: простые строки ----------

@pytest.mark.unit
def test_open_view_renders_fridge_items():
    store.set_list(config.FRIDGE_KEY, CID, ["молоко", "хлеб"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "fridge"))
    texts = [btn.text for row in _kb_rows(bot) for btn in row]
    assert any("молоко" in t for t in texts)
    assert any("хлеб" in t for t in texts)


@pytest.mark.unit
def test_fridge_toggle_and_delete_removes_only_selected():
    store.set_list(config.FRIDGE_KEY, CID, ["молоко", "хлеб", "яйца"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "fridge"))
    view_id = _only_view_id()
    records = store.ensure_list_ids(config.FRIDGE_KEY, CID)
    target = next(r for r in records if r["value"] == "хлеб")
    short_map = cleanup._short_ids([r["id"] for r in records])
    cb = f"clt:{view_id}:{short_map[target['id']]}"
    asyncio.run(cleanup.handle_view_callback(bot, CID, cb))
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    remaining = {r["value"] if isinstance(r, dict) else r for r in store.get_list(config.FRIDGE_KEY, CID)}
    assert remaining == {"молоко", "яйца"}


# ---------- Рецепты: dict-записи с полем name ----------

@pytest.mark.unit
def test_open_view_renders_recipe_names():
    store.set_list(config.MY_RECIPES_KEY, CID, [{"name": "Омлет"}, {"name": "Салат"}])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "recipes"))
    texts = [btn.text for row in _kb_rows(bot) for btn in row]
    assert any("Омлет" in t for t in texts)
    assert any("Салат" in t for t in texts)


@pytest.mark.unit
def test_recipe_delete_by_id_keeps_other_fields_intact():
    store.set_list(config.MY_RECIPES_KEY, CID, [
        {"name": "Омлет", "ingredients": ["яйца", "молоко"]},
        {"name": "Салат", "ingredients": ["огурцы"]},
    ])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "recipes"))
    view_id = _only_view_id()
    records = store.ensure_list_ids(config.MY_RECIPES_KEY, CID)
    target = next(r for r in records if r["name"] == "Омлет")
    short_map = cleanup._short_ids([r["id"] for r in records])
    cb = f"clt:{view_id}:{short_map[target['id']]}"
    asyncio.run(cleanup.handle_view_callback(bot, CID, cb))
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    remaining = store.get_list(config.MY_RECIPES_KEY, CID)
    assert len(remaining) == 1
    assert remaining[0]["name"] == "Салат"
    assert remaining[0]["ingredients"] == ["огурцы"]


# ---------- Здоровье (lagom): поле внутри профиля, не отдельный KV-ключ ----------

@pytest.mark.unit
def test_open_view_renders_lagom_items_from_profile():
    memory.set_lagom(CID, ["честность", "простота"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "lagom"))
    texts = [btn.text for row in _kb_rows(bot) for btn in row]
    assert any("честность" in t for t in texts)
    assert any("простота" in t for t in texts)


@pytest.mark.unit
def test_lagom_delete_updates_profile_not_a_kv_key():
    memory.set_lagom(CID, ["честность", "простота", "доброта"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "lagom"))
    view_id = _only_view_id()
    records = store.ensure_list_ids_via(memory.get_lagom, memory.set_lagom, cleanup._LAGOM_REVISION_SLOT, CID)
    target = next(r for r in records if r["value"] == "простота")
    short_map = cleanup._short_ids([r["id"] for r in records])
    cb = f"clt:{view_id}:{short_map[target['id']]}"
    asyncio.run(cleanup.handle_view_callback(bot, CID, cb))
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    remaining = {r["value"] if isinstance(r, dict) else r for r in memory.get_lagom(CID)}
    assert remaining == {"честность", "доброта"}
    prof = store.get_profile(CID)
    assert "lagom" in prof, "lagom обязан жить в поле профиля, а не отдельным KV-ключом"


@pytest.mark.unit
def test_lagom_delete_bumps_dedicated_revision_slot():
    memory.set_lagom(CID, ["честность", "простота"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "lagom"))
    view_id = _only_view_id()
    rev_before = store.get_list_revision(cleanup._LAGOM_REVISION_SLOT, CID)
    records = store.ensure_list_ids_via(memory.get_lagom, memory.set_lagom, cleanup._LAGOM_REVISION_SLOT, CID)
    cleanup._views[view_id]["selected_ids"] = {records[0]["id"]}
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))
    assert store.get_list_revision(cleanup._LAGOM_REVISION_SLOT, CID) > rev_before


# ---------- гонка данных и TTL (та же схема, что PR3a-c) ----------

@pytest.mark.unit
def test_race_deleting_other_lagom_item_blocks_stale_deletion_not_silently():
    memory.set_lagom(CID, ["честность", "простота", "доброта"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "lagom"))
    view_id = _only_view_id()
    records = store.ensure_list_ids_via(memory.get_lagom, memory.set_lagom, cleanup._LAGOM_REVISION_SLOT, CID)
    a = next(r for r in records if r["value"] == "честность")
    b = next(r for r in records if r["value"] == "простота")

    cleanup._views[view_id]["selected_ids"] = {a["id"]}
    store.remove_from_list_by_ids_via(memory.get_lagom, memory.set_lagom, cleanup._LAGOM_REVISION_SLOT, CID, {b["id"]})

    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}"))

    assert "уже изменился" in bot.messages[-1]["text"].lower()
    remaining = {r["value"] if isinstance(r, dict) else r for r in memory.get_lagom(CID)}
    assert "честность" in remaining, "A не должна быть удалена вслепую при устаревшей revision"
    assert "простота" not in remaining
    assert "доброта" in remaining
    assert view_id not in cleanup._views


@pytest.mark.unit
def test_expired_view_on_fridge_context_shows_reopen_message():
    store.set_list(config.FRIDGE_KEY, CID, ["молоко"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_view(bot, CID, "fridge"))
    view_id = _only_view_id()
    cleanup._views[view_id]["created_at"] -= cleanup.VIEW_TTL_SECONDS + 60
    q = _FakeQuery(bot, CID)
    asyncio.run(cleanup.handle_view_callback(bot, CID, f"cld:{view_id}", q=q))
    assert "уже изменился" in bot.messages[-1]["text"].lower()
    assert view_id not in cleanup._views


@pytest.mark.unit
def test_unknown_view_id_on_recipes_shows_reopen_message_without_side_effects():
    store.set_list(config.MY_RECIPES_KEY, CID, [{"name": "Омлет"}])
    bot = _FakeBot()
    asyncio.run(cleanup.handle_view_callback(bot, CID, "cld:deadbeef"))
    assert "уже изменился" in bot.messages[-1]["text"].lower()
    assert store.get_list(config.MY_RECIPES_KEY, CID) == [{"name": "Омлет"}]


# ---------- open_cleanup делегирует на view-режим ----------

@pytest.mark.unit
def test_open_cleanup_delegates_to_view_for_fridge_recipes_lagom():
    store.set_list(config.FRIDGE_KEY, CID, ["молоко"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "fridge"))
    assert len(cleanup._views) == 1
    cleanup._views.clear()

    store.set_list(config.MY_RECIPES_KEY, CID, [{"name": "Омлет"}])
    asyncio.run(cleanup.open_cleanup(bot, CID, "recipes"))
    assert len(cleanup._views) == 1
    cleanup._views.clear()

    memory.set_lagom(CID, ["честность"])
    asyncio.run(cleanup.open_cleanup(bot, CID, "lagom"))
    assert len(cleanup._views) == 1


@pytest.mark.unit
def test_open_cleanup_still_uses_old_format_for_cfg_context():
    """Регрессия: cfg_* (legacy compatibility-слой) намеренно не мигрируется ни
    в одном из PR3a-d — судьба этого контекста ждёт отдельного решения P1-2."""
    store._mem.pop(config.COUNTRIES_KEY, None)
    store.set_list(config.COUNTRIES_KEY, CID, ["Испания", "Италия"])
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "cfg_countries"))
    assert len(cleanup._views) == 0
    cb = _callbacks(bot)
    assert any(c.startswith("clt_cfg_countries_") for c in cb if c)
    store._mem.pop(config.COUNTRIES_KEY, None)
