"""Ручное обновление пользовательской базы до текущих схем."""

import asyncio
import logging
import uuid

import cleanup
import config
import recommendation_stoplist
import learning_data_quality
import store
from fridge_model import _fridge_migrate
from wardrobe_migration import migrate_item_attrs, migration_count

_log = logging.getLogger(__name__)


def _clear_legacy_backups(cid):
    """Удаляет старые копии прежнего сценария; новые больше не создаются."""
    cid = str(cid)

    def change(data):
        removed = len(data.get(cid, [])) if isinstance(data.get(cid), list) else int(cid in data)
        data.pop(cid, None)
        return data, removed

    return store.mutate_kv(config.DATA_REFRESH_BACKUP_KEY, change)


async def _refresh_daily_caches(cid):
    """Удаляет сохранённый UI старого формата и сразу собирает текущие карточки."""
    refreshed = 0
    failed = 0

    try:
        import learning
        learning.reset_daily_material_cache(cid)
        await asyncio.to_thread(learning.warm_home_cache, cid)
        refreshed += 1
    except Exception as error:
        failed += 1
        _log.warning("learning cache refresh failed cid=%s: %r", cid, error)

    try:
        import wardrobe
        store.clear_wardrobe_daylook(cid)
        await wardrobe.warm_home_cache(cid)
        refreshed += 1
    except Exception as error:
        failed += 1
        _log.warning("wardrobe cache refresh failed cid=%s: %r", cid, error)

    # «Мой день» собирается последним: он использует материал обучения и образ.
    try:
        import myday
        myday.reset_day_cache(cid)
        await myday.warm_day_cache(cid)
        refreshed += 1
    except Exception as error:
        failed += 1
        _log.warning("myday cache refresh failed cid=%s: %r", cid, error)

    return {"refreshed": refreshed, "failed": failed}


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
    collection_keys = _collection_keys()
    backups_removed = _clear_legacy_backups(cid)
    collection_items = 0
    changed_items = 0
    for key in collection_keys:
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

    language_result = await learning_data_quality.refresh_dictionary(cid)
    cache_result = await _refresh_daily_caches(cid)

    try:
        import leisure_concerts
        concerts = await leisure_concerts.refresh_concerts_cache(cid)
    except Exception as error:
        _log.warning("manual concert refresh failed cid=%s: %r", cid, error, exc_info=True)
        concerts = {"status": "failed", "artists": 0, "events": 0}
    fixed_total_raw = (
        changed_items + max(0, wardrobe_pending - wardrobe_remaining)
        + fridge_changed + int(language_result.get("fixed") or 0)
    )
    duplicate_total_raw = int(language_result.get("duplicates") or 0) + int(stoplist_items or 0)
    review_total_raw = int(language_result.get("review") or 0) + int(wardrobe_remaining or 0)
    checked_total = collection_items + len(wardrobe_before)
    if config.FRIDGE_KEY not in collection_keys:
        checked_total += len(fridge_before)
    duplicate_total = min(checked_total, duplicate_total_raw)
    review_total = min(max(0, checked_total - duplicate_total), review_total_raw)
    fixed_total = min(max(0, checked_total - duplicate_total - review_total), fixed_total_raw)
    return {
        "backups_removed": backups_removed,
        "checked": checked_total,
        "fixed": fixed_total,
        "duplicates": duplicate_total,
        "review": review_total,
        "unchanged": max(0, checked_total - fixed_total - duplicate_total - review_total),
        "language_checked": language_result.get("checked", 0),
        "language_pending": language_result.get("pending", 0),
        "language_review_items": language_result.get("review_items", 0),
        "collection_items": collection_items,
        "changed_items": changed_items,
        "wardrobe_items": max(0, wardrobe_pending - wardrobe_remaining),
        "wardrobe_remaining": wardrobe_remaining,
        "fridge_items": fridge_changed,
        "stoplist_items": stoplist_items,
        "concerts_status": concerts.get("status", "failed"),
        "concerts_artists": concerts.get("artists", 0),
        "concerts_events": concerts.get("events", 0),
        "cache_refreshed": cache_result["refreshed"],
        "cache_failed": cache_result["failed"],
    }
