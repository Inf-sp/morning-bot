"""Ручное обновление пользовательской базы до текущих схем."""

import uuid

import cleanup
import config
import store
from wardrobe_migration import migrate_item_attrs, migration_count


def _collection_keys():
    keys = {
        cfg.get("storage_key")
        for cfg in cleanup.COLLECTIONS.values()
        if cfg.get("storage_key") and not str(cfg.get("storage_key")).startswith("profile.")
    }
    keys.update({config.DICT_KEY, config.DIARY_KEY, config.WORRIES_KEY, config.COUNTRIES_KEY})
    return sorted(keys)


async def refresh_user_database(cid):
    """Идемпотентно обновляет коллекции и физическую схему Гардероба."""
    collection_items = 0
    changed_items = 0
    for key in _collection_keys():
        items = store.get_list(key, cid)
        normalized = []
        changed = False
        for item in items:
            # Строка остаётся строкой: для книг, артистов и части поездок это
            # действующая предметная схема. В объектных записях безопасно
            # добавляем только стабильный id, не переписывая содержимое.
            if isinstance(item, dict) and not item.get("id"):
                item = {**item, "id": uuid.uuid4().hex}
                changed = True
                changed_items += 1
            normalized.append(item)
        if changed:
            store.set_list(key, cid, normalized)
        collection_items += len(normalized)

    wardrobe_before = store.load_wardrobe(cid)
    wardrobe_pending = migration_count(wardrobe_before)
    wardrobe_after = await migrate_item_attrs(cid, wardrobe_before)
    wardrobe_remaining = migration_count(wardrobe_after)
    return {
        "collection_items": collection_items,
        "changed_items": changed_items,
        "wardrobe_items": max(0, wardrobe_pending - wardrobe_remaining),
        "wardrobe_remaining": wardrobe_remaining,
    }
