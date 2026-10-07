"""Удаляет данные снятого раздела «✈️ Поездки».

По умолчанию — dry-run: печатает ключи и поля, которые будут удалены, и их
количество (без значений). ``--apply`` удаляет через store/storage_driver.
Работает с PostgreSQL (DATABASE_URL) и с memory-backend.

    python3 scripts/purge_travel_data.py           # что будет удалено
    python3 scripts/purge_travel_data.py --apply   # удалить
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TELEGRAM_TOKEN", "purge-script")  # токен скрипту не нужен

import config  # noqa: E402
import settings  # noqa: E402
import storage_driver  # noqa: E402
import store  # noqa: E402

# Ключи, которые читал и писал только раздел «Поездки».
WHOLE_KEYS = (
    config.SAVED_COUNTRIES_KEY,
    *config.LEGACY_STORAGE_KEYS.get(config.SAVED_COUNTRIES_KEY, ()),
    config.LEGACY_COUNTRIES_KEY,
    config.TRAVEL_DISLIKE_KEY,
    config.TRAVEL_IDEA_KEY,
    config.TRAVEL_COUNTRY_CARDS_KEY,
)
SETTINGS_FIELD = "travel_country_codes_migrated"
STOPLIST_TYPE = "country"
CATEGORY = "travel"


def _whole_keys():
    return tuple(dict.fromkeys(WHOLE_KEYS))


def _raw(key):
    data = store._load(key)
    return data if isinstance(data, dict) else {}


def plan():
    """[(описание, количество)] — только непустые позиции, без значений."""
    rows = [(f"key {key}: entries", len(_raw(key))) for key in _whole_keys()]
    rows.append((
        f"{settings.SETTINGS_KEY}: users with field {SETTINGS_FIELD}",
        sum(1 for v in _raw(settings.SETTINGS_KEY).values() if isinstance(v, dict) and SETTINGS_FIELD in v),
    ))
    rows.append((
        f"{config.RECOMMENDATION_STOPLIST_KEY}: entries type={STOPLIST_TYPE}",
        sum(
            1 for items in _raw(config.RECOMMENDATION_STOPLIST_KEY).values() if isinstance(items, list)
            for item in items if isinstance(item, dict) and item.get("type") == STOPLIST_TYPE
        ),
    ))
    rows.append((f"{config.MONTHLY_REBUSES_CACHE_KEY}: category {CATEGORY}",
                 int(CATEGORY in _raw(config.MONTHLY_REBUSES_CACHE_KEY))))
    news = _raw(config.CATEGORY_NEWS_CACHE_KEY).get("categories")
    rows.append((f"{config.CATEGORY_NEWS_CACHE_KEY}: category {CATEGORY}",
                 int(isinstance(news, dict) and CATEGORY in news)))
    return [(label, count) for label, count in rows if count]


def _drop_settings_field(data):
    for value in (data or {}).values():
        if isinstance(value, dict):
            value.pop(SETTINGS_FIELD, None)
    return data, None


def _drop_stoplist_countries(data):
    for cid, items in list((data or {}).items()):
        if isinstance(items, list):
            data[cid] = [i for i in items if not (isinstance(i, dict) and i.get("type") == STOPLIST_TYPE)]
    return data, None


def _drop_category(data):
    data = data if isinstance(data, dict) else {}
    data.pop(CATEGORY, None)
    return data, None


def _drop_news_category(data):
    data = data if isinstance(data, dict) else {}
    if isinstance(data.get("categories"), dict):
        data["categories"].pop(CATEGORY, None)
    return data, None


def apply():
    for key in _whole_keys():
        storage_driver.delete(key)
    store.mutate_kv(settings.SETTINGS_KEY, _drop_settings_field)
    store.mutate_kv(config.RECOMMENDATION_STOPLIST_KEY, _drop_stoplist_countries)
    store.mutate_kv(config.MONTHLY_REBUSES_CACHE_KEY, _drop_category)
    store.mutate_kv(config.CATEGORY_NEWS_CACHE_KEY, _drop_news_category)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="удалить (по умолчанию только показать)")
    args = parser.parse_args(argv)
    backend = "postgres" if config.DATABASE_URL else "memory"
    rows = plan()
    print(f"backend: {backend}")
    if not rows:
        print("nothing to purge")
        return 0
    for label, count in rows:
        print(f"{label}: {count}")
    if not args.apply:
        print("dry-run: nothing deleted; rerun with --apply")
        return 0
    apply()
    print("purged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
