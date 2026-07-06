"""Гардероб как единый источник истины: мутатор, версия, инвалидация кэша, миграция."""
import pytest

import config
import store

CID = "store-wardrobe-cid"


@pytest.fixture(autouse=True)
def _clean():
    for key in (f"wardrobe_user_{CID}", config.PROFILE_KEY):
        store._mem.pop(key, None)
    yield
    for key in (f"wardrobe_user_{CID}", config.PROFILE_KEY):
        store._mem.pop(key, None)


# ---------- мутатор и версия ----------
@pytest.mark.unit
def test_add_wardrobe_items_generates_id_and_bumps_version():
    w0 = store.load_wardrobe(CID)
    assert w0["_v"] == 0
    store.add_wardrobe_items(CID, [
        {"zone": "Верх", "subcategory": "Футболки", "name": "Белая футболка",
         "color": "белый", "color_secondary": None, "material": None, "style": None, "season": None},
    ])
    w1 = store.load_wardrobe(CID)
    assert w1["_v"] == 1
    items = w1["zones"]["Верх"]["Футболки"]
    assert len(items) == 1
    assert items[0]["id"]
    assert items[0]["name"] == "Белая футболка"


@pytest.mark.unit
def test_add_wardrobe_items_dedups_by_name_case_insensitive():
    item = {"zone": "Верх", "subcategory": "Футболки", "name": "Белая футболка",
            "color": "", "color_secondary": None, "material": None, "style": None, "season": None}
    store.add_wardrobe_items(CID, [item])
    store.add_wardrobe_items(CID, [dict(item, name="белая футболка")])
    w = store.load_wardrobe(CID)
    assert len(w["zones"]["Верх"]["Футболки"]) == 1


@pytest.mark.unit
def test_remove_wardrobe_items_by_id_bumps_version_and_removes_only_target():
    store.add_wardrobe_items(CID, [
        {"zone": "Верх", "subcategory": "Футболки", "name": "Белая футболка",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
        {"zone": "Верх", "subcategory": "Футболки", "name": "Чёрная футболка",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
    ])
    w = store.load_wardrobe(CID)
    keep_id = w["zones"]["Верх"]["Футболки"][0]["id"]
    drop_id = w["zones"]["Верх"]["Футболки"][1]["id"]
    removed = store.remove_wardrobe_items(CID, {drop_id})
    assert removed == 1
    w2 = store.load_wardrobe(CID)
    remaining = w2["zones"]["Верх"]["Футболки"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == keep_id
    assert w2["_v"] == 2


# ---------- версионированный кэш образа дня (регрессия на «призрачные вещи») ----------
@pytest.mark.unit
def test_get_valid_wardrobe_daylook_returns_empty_when_item_removed():
    store.add_wardrobe_items(CID, [
        {"zone": "Верх", "subcategory": "Футболки", "name": "Белая футболка",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
    ])
    w = store.load_wardrobe(CID)
    item_id = w["zones"]["Верх"]["Футболки"][0]["id"]
    store.set_wardrobe_daylook(CID, {"date": "2026-01-01", "version": w["_v"],
                                     "item_ids": [item_id], "look_data": {}, "text": "образ"})
    assert store.get_valid_wardrobe_daylook(CID)  # пока вещь на месте — кэш валиден

    store.remove_wardrobe_items(CID, {item_id})
    assert store.get_valid_wardrobe_daylook(CID) == {}  # вещь удалена -> кэш невалиден


@pytest.mark.unit
def test_get_valid_wardrobe_daylook_returns_empty_when_version_mismatch():
    store.add_wardrobe_items(CID, [
        {"zone": "Верх", "subcategory": "Футболки", "name": "Белая футболка",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
    ])
    w = store.load_wardrobe(CID)
    item_id = w["zones"]["Верх"]["Футболки"][0]["id"]
    store.set_wardrobe_daylook(CID, {"date": "2026-01-01", "version": w["_v"] + 1,
                                     "item_ids": [item_id], "look_data": {}, "text": "образ"})
    assert store.get_valid_wardrobe_daylook(CID) == {}


@pytest.mark.unit
def test_get_valid_wardrobe_daylook_valid_cache_returned_as_is():
    store.add_wardrobe_items(CID, [
        {"zone": "Верх", "subcategory": "Футболки", "name": "Белая футболка",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
    ])
    w = store.load_wardrobe(CID)
    item_id = w["zones"]["Верх"]["Футболки"][0]["id"]
    cached = {"date": "2026-01-01", "version": w["_v"], "item_ids": [item_id],
              "look_data": {"weather_intro": "тепло"}, "text": "образ"}
    store.set_wardrobe_daylook(CID, cached)
    assert store.get_valid_wardrobe_daylook(CID) == cached


@pytest.mark.unit
def test_adding_item_does_not_invalidate_unrelated_daylook_cache():
    store.add_wardrobe_items(CID, [
        {"zone": "Верх", "subcategory": "Футболки", "name": "Белая футболка",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
    ])
    w = store.load_wardrobe(CID)
    item_id = w["zones"]["Верх"]["Футболки"][0]["id"]
    store.set_wardrobe_daylook(CID, {"date": "2026-01-01", "version": w["_v"],
                                     "item_ids": [item_id], "look_data": {}, "text": "образ"})
    # версия «застыла» в кэше на прошлом значении -> get_valid должен упасть после добавления новой вещи,
    # т.к. version больше не совпадает (образ дня строился до пополнения шкафа).
    store.add_wardrobe_items(CID, [
        {"zone": "Низ", "subcategory": "Джинсы", "name": "Синие джинсы",
         "color": "", "color_secondary": None, "material": None, "style": None, "season": None},
    ])
    assert store.get_valid_wardrobe_daylook(CID) == {}


# ---------- миграция старого формата ----------
@pytest.mark.unit
def test_migrate_legacy_wardrobe_converts_flat_strings_to_objects():
    legacy = {"куртки": ["дождевик жёлтый"], "футболки": ["белая", "белая"]}
    store._mem[f"wardrobe_user_{CID}"] = legacy
    w = store.load_wardrobe(CID)
    assert "zones" in w
    assert w["_v"] == 0
    coats = w["zones"]["Верхняя одежда"]["Плащи"]
    assert len(coats) == 1
    assert coats[0]["name"] == "дождевик жёлтый"
    assert coats[0]["id"]
    tshirts = w["zones"]["Верх"]["Футболки"]
    assert len(tshirts) == 1  # дедуп дублей "белая"/"белая"


@pytest.mark.unit
def test_migrate_legacy_wardrobe_is_idempotent_on_reread():
    legacy = {"футболки": ["белая"]}
    store._mem[f"wardrobe_user_{CID}"] = legacy
    w1 = store.load_wardrobe(CID)
    w2 = store.load_wardrobe(CID)
    assert w1 == w2
    assert len(w2["zones"]["Верх"]["Футболки"]) == 1
