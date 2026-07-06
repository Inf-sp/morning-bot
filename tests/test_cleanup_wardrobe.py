"""Чистка гардероба: иерархия Зона→Подкатегория→мультивыбор, удаление по стабильному id."""
import asyncio

import pytest

import cleanup
import config
import store
import wardrobe

CID = "cleanup-wardrobe-cid"


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


@pytest.fixture(autouse=True)
def _clean():
    for key in (f"wardrobe_user_{CID}",):
        store._mem.pop(key, None)
    store.list_sel.pop(f"{CID}:kast_top_0_m", None)
    yield
    for key in (f"wardrobe_user_{CID}",):
        store._mem.pop(key, None)
    store.list_sel.pop(f"{CID}:kast_top_0_m", None)


def _seed():
    store.add_wardrobe_items(CID, [
        {"zone": "Верх", "subcategory": "Футболки", "name": "Белая футболка",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
        {"zone": "Верх", "subcategory": "Футболки", "name": "Чёрная футболка",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
        {"zone": "Низ", "subcategory": "Джинсы", "name": "Синие джинсы",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
    ])


@pytest.mark.unit
def test_ctx_items_resolves_zone_and_subcat_from_kast_ctx():
    _seed()
    w = store.load_wardrobe(CID)
    fut_idx = store.ZONE_SUBCATS["Верх"].index("Футболки")
    title, items, back = cleanup._ctx_items(CID, f"kast_top_{fut_idx}_m")
    assert "Футболки" in title
    names = {name for _id, name in items}
    assert names == {"Белая футболка", "Чёрная футболка"}
    assert back == "w_delz_top_m"


@pytest.mark.unit
def test_cleanup_delete_by_id_removes_only_selected_item():
    _seed()
    w = store.load_wardrobe(CID)
    items = w["zones"]["Верх"]["Футболки"]
    white_id = next(it["id"] for it in items if it["name"] == "Белая футболка")
    black_id = next(it["id"] for it in items if it["name"] == "Чёрная футболка")

    fut_idx = store.ZONE_SUBCATS["Верх"].index("Футболки")
    ctx = f"kast_top_{fut_idx}_m"
    cleanup._sel(CID, ctx).add(white_id)
    cleanup._cleanup_delete(CID, ctx)

    w2 = store.load_wardrobe(CID)
    remaining = w2["zones"]["Верх"]["Футболки"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == black_id
    # джинсы из другой подкатегории не затронуты
    assert w2["zones"]["Низ"]["Джинсы"][0]["name"] == "Синие джинсы"


@pytest.mark.unit
def test_handle_cleanup_toggle_accepts_uuid_index_and_deletes_by_id():
    _seed()
    w = store.load_wardrobe(CID)
    items = w["zones"]["Верх"]["Футболки"]
    white_id = next(it["id"] for it in items if it["name"] == "Белая футболка")
    black_id = next(it["id"] for it in items if it["name"] == "Чёрная футболка")
    fut_idx = store.ZONE_SUBCATS["Верх"].index("Футболки")
    ctx = f"kast_top_{fut_idx}_m"
    bot = _FakeBot()

    # Отмечаем вещь по её uuid-id (не числовой индекс) — не должно падать на int().
    asyncio.run(cleanup.handle_cleanup(bot, CID, f"clt_{ctx}_{white_id}_0"))
    assert white_id in cleanup._sel(CID, ctx)

    # Удаляем отмеченное — должна остаться только вторая вещь.
    asyncio.run(cleanup.handle_cleanup(bot, CID, f"cld_{ctx}_0"))
    w2 = store.load_wardrobe(CID)
    remaining = w2["zones"]["Верх"]["Футболки"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == black_id
