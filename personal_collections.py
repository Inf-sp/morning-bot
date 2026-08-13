"""Личные коллекции и экспорт данных.

Исторические сохранённые карточки больше не участвуют в интерфейсе и подборках.
Они остаются только в пользовательском экспорте, чтобы не терять старые данные.
"""

import io
import json
import re

import config
from leisure_collection import movie_title_for_lookup, normalize_movie_items, plain_label
import store


_ARCHIVED_CONTENT_RECORDS_KEY = "content_records.json"
_COLLECTIONS = {
    "movies": (config.FAVORITE_MOVIES_KEY, "cinema_favorites"),
    "books": (config.FAVORITE_BOOKS_KEY, "books_favorites"),
    "artists": (config.FAVORITE_ARTISTS_KEY, "music_favorite_artists"),
}


def _item_text(item):
    if isinstance(item, dict):
        return str(item.get("name") or item.get("value") or "").strip()
    return str(item or "").strip()


def _love_items(cid, key):
    if key == "countries":
        return [_item_text(item) for item in store.get_list(config.SAVED_COUNTRIES_KEY, cid)]
    collection = _COLLECTIONS.get(key)
    if collection is None:
        return []
    return [_item_text(item) for item in store.get_list(collection[0], cid)]


def _unique_items(values):
    result = []
    seen = set()
    for value in values:
        text = _item_text(value)
        normalized = text.casefold()
        if text and normalized not in seen:
            seen.add(normalized)
            result.append(text)
    return result


def _export_payload(cid):
    archived = store.get_list(_ARCHIVED_CONTENT_RECORDS_KEY, cid)
    payload = {
        "settings": store.get_settings(cid),
        "wardrobe": store.load_wardrobe(cid),
        "fridge": store.get_list(config.FRIDGE_KEY, cid),
        "dictionary": store.get_list(config.DICT_KEY, cid),
        "favorites": {
            "movies": store.get_list(config.FAVORITE_MOVIES_KEY, cid),
            "books": store.get_list(config.FAVORITE_BOOKS_KEY, cid),
            "artists": store.get_list(config.FAVORITE_ARTISTS_KEY, cid),
        },
        "visited_countries": store.get_list(config.SAVED_COUNTRIES_KEY, cid),
        "thoughts": store.get_list(config.THOUGHTS_KEY, cid),
    }
    if archived:
        payload["archive"] = {"saved_cards": archived}
    return payload


async def export_data(bot, cid):
    body = json.dumps(_export_payload(cid), ensure_ascii=False, indent=2, default=str)
    document = io.BytesIO(body.encode("utf-8"))
    document.name = "daily-manager-data.json"
    await bot.send_document(
        chat_id=cid,
        document=document,
        filename=document.name,
        caption="📤 Готово. Это копия твоих данных.",
    )


async def love_add_start(bot, cid, key, origin="base"):
    if key == "countries":
        import travel

        await travel.send_country_add_prompt(bot, cid)
        return
    if key not in _COLLECTIONS:
        import settings

        await settings.send_home(bot, cid)
        return
    prefix = "loveaddls" if origin == "leisure" else "loveadd"
    store.pending_input[str(cid)] = f"{prefix}_{key}"
    name = {
        "movies": "фильм или сериал",
        "artists": "артиста",
        "books": "книгу",
    }[key]
    await bot.send_message(chat_id=cid, text=f"Напиши {name} — добавлю в любимые.")


async def love_add_done(bot, cid, key, text, origin="base"):
    if key == "countries":
        import travel

        await travel.add_visited_country(bot, cid, text)
        return
    collection = _COLLECTIONS.get(key)
    if collection is None:
        import settings

        await settings.send_home(bot, cid)
        return
    store_key, collection_id = collection
    items = _unique_items(re.split(r"[,;\n]+", text or ""))
    if key == "movies":
        import asyncio

        try:
            items = await asyncio.wait_for(
                asyncio.to_thread(normalize_movie_items, items), timeout=4.0,
            )
        except asyncio.TimeoutError:
            items = [plain_label(item) for item in items if plain_label(item)]
    else:
        items = [plain_label(item) for item in items if plain_label(item)]
    existing = {
        (movie_title_for_lookup(item) if key == "movies" else item).casefold()
        for item in _love_items(cid, key)
    }
    added = []
    for item in items:
        dedupe_key = (movie_title_for_lookup(item) if key == "movies" else item).casefold()
        if dedupe_key not in existing:
            store.add_to_list(store_key, cid, item)
            existing.add(dedupe_key)
            added.append(item)
    if key == "artists" and added:
        import leisure_music

        leisure_music._kick_off_new_artist_concert_check(cid, added)
        await leisure_music.send_favorite_artists_added_card(bot, cid, added)
        return
    if key == "movies" and added:
        import leisure_movies

        await leisure_movies.send_favorite_movies_added_card(bot, cid, added)
        return
    import cleanup

    back = {"movies": "m_movie", "books": "m_books", "artists": "m_music"}[key]
    await cleanup.open_collection(bot, cid, collection_id, back=back)


async def _open_legacy_collection(bot, cid, key):
    if key == "countries":
        import travel

        await travel.send_countries(bot, cid)
        return
    collection = _COLLECTIONS.get(key)
    if collection is None:
        import settings

        await settings.send_home(bot, cid)
        return
    import cleanup

    back = {"movies": "m_movie", "books": "m_books", "artists": "m_music"}[key]
    await cleanup.open_collection(bot, cid, collection[1], back=back)


async def handle_collection_callback(bot, cid, q, data):
    """Обрабатывает личные коллекции, экспорт и безопасные старые callbacks."""
    if data == "as_export":
        await export_data(bot, cid)
        return
    if data.startswith("ls_loveadd_"):
        await love_add_start(bot, cid, data[len("ls_loveadd_"):], origin="leisure")
        return
    if data.startswith("as_loveadd_"):
        await love_add_start(bot, cid, data[len("as_loveadd_"):])
        return
    if data.startswith("as_love_"):
        await _open_legacy_collection(bot, cid, data[len("as_love_"):])
        return
    if data.startswith("as_loveclean_") or data.startswith("as_lovehidden_"):
        prefix = "as_loveclean_" if data.startswith("as_loveclean_") else "as_lovehidden_"
        await _open_legacy_collection(bot, cid, data[len(prefix):])
        return
    # Старые кнопки не меняют данные и открывают актуальные настройки.
    import settings

    await settings.send_home(bot, cid)
