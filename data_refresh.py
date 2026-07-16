"""Ручное обновление пользовательской базы до текущих схем."""

import logging
import uuid

import cleanup
import config
import recommendation_stoplist
import store
from fridge_model import _fridge_migrate
from wardrobe_migration import migrate_item_attrs, migration_count

_log = logging.getLogger(__name__)


def _collection_keys():
    keys = {
        cfg.get("storage_key")
        for cfg in cleanup.COLLECTIONS.values()
        if cfg.get("storage_key") and not str(cfg.get("storage_key")).startswith("profile.")
    }
    keys.update({
        config.DICT_KEY, config.DIARY_KEY, config.WORRIES_KEY,
        config.THOUGHT_REVIEWS_KEY, config.COUNTRIES_KEY,
    })
    return sorted(keys)


async def refresh_user_database(cid):
    """Обновляет коллекции, Гардероб и концертную подборку пользователя."""
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

    fridge_before = store.get_list(config.FRIDGE_KEY, cid)
    fridge_after = _fridge_migrate(fridge_before)
    fridge_changed = sum(
        1 for index, item in enumerate(fridge_after)
        if index >= len(fridge_before) or item != fridge_before[index]
    ) + max(0, len(fridge_before) - len(fridge_after))
    if fridge_after != fridge_before:
        store.set_list(config.FRIDGE_KEY, cid, fridge_after)

    stoplist_items = recommendation_stoplist.migrate_legacy(cid)

    try:
        import leisure_concerts
        concerts = await leisure_concerts.refresh_concerts_cache(cid)
    except Exception as error:
        _log.warning("manual concert refresh failed cid=%s: %r", cid, error, exc_info=True)
        concerts = {"status": "failed", "artists": 0, "events": 0}
    return {
        "collection_items": collection_items,
        "changed_items": changed_items,
        "wardrobe_items": max(0, wardrobe_pending - wardrobe_remaining),
        "wardrobe_remaining": wardrobe_remaining,
        "fridge_items": fridge_changed,
        "stoplist_items": stoplist_items,
        "concerts_status": concerts.get("status", "failed"),
        "concerts_artists": concerts.get("artists", 0),
        "concerts_events": concerts.get("events", 0),
    }
