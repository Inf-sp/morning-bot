"""Движок коллекций: пагинация + мультивыбор + контекстные действия.

Используется из learning, notes, wardrobe, balance, bot. Пользовательское
описание раздела — docs/cleanup.md; история миграции на текущую архитектуру —
docs/archive/audit-cleanup-plan.md.

"view"-режим (стабильный item_id + revision коллекции, короткий callback_data
вида "clt:<view_id>:<short_id>") распространён на все контексты, кроме
гардероба (kast_*, мигрирован раньше через
store.add_wardrobe_items/remove_wardrobe_items) и legacy compatibility-слоя
cfg_* (не мигрирует, пока не решена его судьба):
           d_<lang>_<kind> (словарь), nb/nb_* (закладки),
           wl/rl (watchlist/readlist), lv_<key>/lvls_<key> (любимые),
           hid_<key> (скрытое/чёрный список — действие только убирает из
           чёрного списка, не трогает fav_key, чтобы не превращать «вернуть в
           рекомендации» в скрытый сигнал «мне нравится»),
           fridge/recipes/diary (холодильник/рецепты/История самочувствия).
"""
import secrets
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import recommendation_stoplist
import store
from util import esc
from ui.constants import choose_label, delete_label, ui_label

CLEAN_PAGE = 8

# --- view-режим (PR3a/PR3b): стабильный id + revision, короткий callback_data ---
VIEW_TTL_SECONDS = 24 * 3600
_views = {}  # view_id -> {"ctx", "revision", "selected_ids", "page", "back", "created_at"}

# lv_<key>/lvls_<key> (Любимое) и hid_<key> (Скрытое) — один и тот же набор
# storage-ключей по суффиксу key, см. также _ctx_items/_cleanup_delete (старый
# путь) выше — держать в синхроне при изменении набора категорий.
_LOVE_STORE_KEYS = {"movies": config.WATCHLIST_KEY, "countries": config.FAVCOUNTRIES_KEY,
                    "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}
_HIDDEN_STORE_KEYS = {"movies": config.MOVIE_BLACKLIST_KEY, "books": config.BOOK_BLACKLIST_KEY,
                      "artists": config.MUSIC_DISLIKE_KEY, "countries": config.TRAVEL_DISLIKE_KEY}
_SEEN_STORE_KEYS = {"movies": config.MOVIE_SEEN_KEY, "books": config.BOOK_SEEN_KEY,
                    "artists": config.MUSIC_SEEN_KEY}

def _collection(id, owner, title, storage_key, item_type, back, actions,
                note_group=None, add_button=None):
    return {
        "id": id,
        "owner": owner,
        "title": title,
        "storage_key": storage_key,
        "item_type": item_type,
        "back": back,
        "actions": actions,
        "note_group": note_group,
        "add_button": add_button,
    }


COLLECTIONS = {
    "cinema_favorites": _collection(
        "cinema_favorites", "cinema", f"Любимое · {ui_label('cinema', 'Кино')}", config.WATCHLIST_KEY, "movie",
        "a_watch", [{"id": "remove", "label": "Убрать из любимого", "confirm": False},
                    {"id": "hide", "label": "Скрыть", "confirm": False}],
        add_button=("🆕 Добавить фильм", "as_loveadd_movies")),
    "cinema_saved": _collection(
        "cinema_saved", "cinema", f"⭐️ Сохранённое · {ui_label('cinema', 'Кино')}", config.NOTES_KEY, "note",
        "a_watch", [{"id": "remove", "label": "Убрать из сохранённого", "confirm": True}],
        note_group="movies"),
    "cinema_watched": _collection(
        "cinema_watched", "cinema", f"{ui_label('seen', 'Смотрел')} · {ui_label('cinema', 'Кино')}", config.MOVIE_SEEN_KEY, "movie",
        "a_watch", [{"id": "remove", "label": "Убрать из просмотренного", "confirm": False}]),
    "cinema_hidden": _collection(
        "cinema_hidden", "cinema", f"Скрытое · {ui_label('cinema', 'Кино')}", config.MOVIE_BLACKLIST_KEY, "movie",
        "a_watch", [{"id": "restore", "label": "Вернуть в рекомендации", "confirm": False}]),

    "books_favorites": _collection(
        "books_favorites", "books", f"Любимое · {ui_label('books', 'Книги')}", config.BOOKS_KEY, "book",
        "a_read", [{"id": "remove", "label": "Убрать из любимого", "confirm": False},
                   {"id": "hide", "label": "Скрыть", "confirm": False}],
        add_button=("🆕 Добавить книгу", "as_loveadd_books")),
    "books_saved": _collection(
        "books_saved", "books", f"⭐️ Сохранённое · {ui_label('books', 'Книги')}", config.READLIST_KEY, "book",
        "a_read", [{"id": "remove", "label": "Убрать из сохранённого", "confirm": False}]),
    "books_read": _collection(
        "books_read", "books", f"{ui_label('seen', 'Прочитано')} · {ui_label('books', 'Книги')}", config.BOOK_SEEN_KEY, "book",
        "a_read", [{"id": "remove", "label": "Убрать из прочитанного", "confirm": False}]),
    "books_hidden": _collection(
        "books_hidden", "books", f"Скрытое · {ui_label('books', 'Книги')}", config.BOOK_BLACKLIST_KEY, "book",
        "a_read", [{"id": "restore", "label": "Вернуть в рекомендации", "confirm": False}]),

    "music_favorite_artists": _collection(
        "music_favorite_artists", "music", "Любимые артисты", config.ARTISTS_KEY, "artist",
        "a_listen", [{"id": "remove", "label": "Убрать артистов", "confirm": False},
                     {"id": "hide", "label": "Скрыть", "confirm": False}],
        add_button=("🆕 Добавить артиста", "as_loveadd_artists")),
    "music_hidden_artists": _collection(
        "music_hidden_artists", "music", "Скрытые артисты", config.MUSIC_DISLIKE_KEY, "artist",
        "a_listen", [{"id": "restore", "label": "Вернуть в рекомендации", "confirm": False}]),
    "music_saved": _collection(
        "music_saved", "music", f"⭐️ Сохранённое · {ui_label('music', 'Музыка')}", config.NOTES_KEY, "note",
        "a_listen", [{"id": "remove", "label": "Убрать из сохранённого", "confirm": True}],
        note_group="music"),
    "music_seen_artists": _collection(
        "music_seen_artists", "music", f"{ui_label('seen', 'Уже знаю')} · {ui_label('music', 'Музыка')}", config.MUSIC_SEEN_KEY, "artist",
        "a_listen", [{"id": "remove", "label": "Убрать из знакомого", "confirm": False}]),

    "travel_favorite_countries": _collection(
        "travel_favorite_countries", "travel", "🧳 Посещённые страны", config.FAVCOUNTRIES_KEY, "country",
        "m_travel", [{"id": "remove", "label": "Убрать страны", "confirm": False},
                     {"id": "hide", "label": "Скрыть", "confirm": False}],
        add_button=("🆕 Добавить страну", "as_loveadd_countries")),
    "travel_hidden_countries": _collection(
        "travel_hidden_countries", "travel", "Скрытые страны", config.TRAVEL_DISLIKE_KEY, "country",
        "m_travel", [{"id": "restore", "label": "Вернуть в рекомендации", "confirm": False}]),
    "travel_saved_places": _collection(
        "travel_saved_places", "travel", f"⭐️ Сохранённое · {ui_label('travel', 'Поездки')}", config.NOTES_KEY, "note",
        "m_travel", [{"id": "remove", "label": "Убрать из сохранённого", "confirm": True}],
        note_group="travel"),

    "recipes_saved": _collection(
        "recipes_saved", "food", ui_label("recipes", "Рецепты"), config.MY_RECIPES_KEY, "recipe",
        "as_my_recipes", [{"id": "remove", "label": "Удалить рецепты", "confirm": True}]),
    "fridge_items": _collection(
        "fridge_items", "food", ui_label("products", "Продукты"), config.FRIDGE_KEY, "product",
        "as_fridge", [{"id": "remove", "label": "Удалить продукты", "confirm": True}]),
}

_COLLECTION_ALIASES = {
    "lv_movies": "cinema_favorites",
    "lvls_movies": "cinema_favorites",
    "lv_books": "books_favorites",
    "lvls_books": "books_favorites",
    "lv_artists": "music_favorite_artists",
    "lvls_artists": "music_favorite_artists",
    "lv_countries": "travel_favorite_countries",
    "lvls_countries": "travel_favorite_countries",
    "hid_movies": "cinema_hidden",
    "hid_books": "books_hidden",
    "hid_artists": "music_hidden_artists",
    "hid_countries": "travel_hidden_countries",
    "wl": "cinema_favorites",
    "rl": "books_saved",
    "recipes": "recipes_saved",
    "fridge": "fridge_items",
}


def _is_view_ctx(ctx):
    return (ctx in COLLECTIONS or ctx in _COLLECTION_ALIASES
            or ctx == "nb" or ctx.startswith("nb_")
            or ctx.startswith("lv_") or ctx.startswith("lvls_")
            or ctx.startswith("hid_")
            or ctx.startswith("d_") or ctx in ("wl", "rl")
            or ctx in ("fridge", "recipes", "diary"))


def _canonical_ctx(ctx):
    return _COLLECTION_ALIASES.get(ctx, ctx)


def _collection_cfg(ctx):
    return COLLECTIONS.get(_canonical_ctx(ctx))


def _primary_action(ctx):
    cfg = _collection_cfg(ctx)
    if cfg and cfg.get("actions"):
        return cfg["actions"][0]
    return None


def _action_by_id(ctx, action_id):
    cfg = _collection_cfg(ctx)
    if cfg:
        for action in cfg.get("actions") or []:
            if action.get("id") == action_id:
                return action
    return _primary_action(ctx)


def _view_store_key(ctx):
    """Storage-ключ коллекции для view-контекста."""
    cfg = _collection_cfg(ctx)
    if cfg:
        return cfg["storage_key"]
    if ctx == "nb" or ctx.startswith("nb_"):
        return config.NOTES_KEY
    if ctx.startswith("lvls_"):
        return _LOVE_STORE_KEYS.get(ctx[len("lvls_"):])
    if ctx.startswith("lv_"):
        return _LOVE_STORE_KEYS.get(ctx[len("lv_"):])
    if ctx.startswith("hid_"):
        return _HIDDEN_STORE_KEYS.get(ctx[len("hid_"):])
    if ctx.startswith("d_"):
        return config.DICT_KEY
    if ctx == "wl":
        return config.WATCHLIST_KEY
    if ctx == "rl":
        return config.READLIST_KEY
    if ctx == "fridge":
        return config.FRIDGE_KEY
    if ctx == "recipes":
        return config.MY_RECIPES_KEY
    return None


_VIEW_ADD_LABEL = {
    "movies": "🆕 Добавить фильм",
    "countries": "🆕 Добавить страну",
    "artists": "🆕 Добавить артиста",
    "books": "🆕 Добавить книгу",
}


def _view_add_button(ctx):
    """Кнопка «Добавить» для lv_<key>/lvls_<key> (Любимое) — нет у hid_*
    (Скрытое, чёрный список — пополняется автоматически, не вручную) и у nb/nb_*
    (Сохранённое пополняется кнопкой «Сохранить» под ответом бота, не отсюда)."""
    if ctx.startswith("lvls_"):
        key = ctx[len("lvls_"):]
        label = _VIEW_ADD_LABEL.get(key)
        return (label, f"ls_loveadd_{key}") if label else None
    if ctx.startswith("lv_"):
        key = ctx[len("lv_"):]
        label = _VIEW_ADD_LABEL.get(key)
        return (label, f"as_loveadd_{key}") if label else None
    return None


def _view_label(it):
    """Текст для отображения элемента view-коллекции — ensure_list_ids
    оборачивает строки в {"id","value"}, а dict-элементы (например страны
    {"name","flag"}) получают только добавленное поле "id"."""
    if "value" in it:
        return str(it["value"])
    if it.get("text"):
        return str(it.get("text") or "")
    return it.get("name", "")


def _note_in_group(note, group):
    if not group:
        return True
    try:
        import settings as _s
        source = note.get("source", "Прочее") if isinstance(note, dict) else "Прочее"
        return _s._fav_group(source) == group
    except Exception:
        return False


def _collection_records(cfg, cid):
    records = store.ensure_list_ids(cfg["storage_key"], cid)
    if cfg.get("note_group"):
        records = [
            r for r in records
            if r.get("bucket", "fav") == "fav" and _note_in_group(r, cfg["note_group"])
        ]
    return records


def _collection_item_label(cfg, item):
    if cfg["item_type"] == "recipe":
        return item.get("name", "Рецепт")
    return _view_label(item)


def _purge_expired_views():
    now = time.time()
    expired = [vid for vid, v in _views.items() if now - v["created_at"] > VIEW_TTL_SECONDS]
    for vid in expired:
        del _views[vid]


def _new_view_id():
    while True:
        vid = secrets.token_hex(4)
        if vid not in _views:
            return vid


def _short_ids(full_ids):
    """Присваивает каждому полному id короткий уникальный суффикс (в рамках
    переданного набора) — короче, чем полный uuid4 hex (32 симв.), но без
    коллизий на практике (обычно единицы-десятки элементов на странице)."""
    mapping = {}
    used = set()
    for fid in full_ids:
        length = 4
        short = fid[:length]
        while short in used and length < len(fid):
            length += 1
            short = fid[:length]
        used.add(short)
        mapping[fid] = short
    return mapping


def _sel(cid, ctx):
    return store.list_sel.setdefault(f"{cid}:{ctx}", set())


def _list_label(it):
    return it.get("name", "") if isinstance(it, dict) else str(it)


def _sort_items(items):
    """Сортирует отображение по алфавиту, сохраняя исходные id для удаления."""
    return sorted(items, key=lambda item: (item[1] or "").casefold().strip())


def _wardrobe_flat(cid, zone, subcat):
    """[(item_id, item_name), ...] вещей одной подкатегории — адресация по
    стабильному id вещи, а не по пересчитываемому позиционному индексу."""
    w = store.load_wardrobe(cid)
    items = w.get("zones", {}).get(zone, {}).get(subcat, [])
    return [(it["id"], it["name"]) for it in items]


def _ctx_items(cid, ctx):
    """(заголовок, items=[(global_id, label)], back_callback) для контекста чистки."""
    if ctx.startswith("d_"):
        import learning as _l
        lang = ctx[len("d_"):]
        flag = "🇳🇱" if lang == "nl" else "🇬🇧"
        words = _l._ensure_dict(cid)
        items = []
        for i, w in enumerate(words):
            if _l._dict_lang(w) == lang:
                term = _l._entry_term(w)
                ru = _l._entry_translation(w)
                items.append((i, f"{term} — {ru}".strip(" —")))
        return f"{flag} Чистка словаря", items, f"a_dictlang_{lang}"
    if ctx == "nb" or ctx.startswith("nb_"):
        import re as _re
        import settings as _s
        _strip = lambda s: _re.sub(r"<[^>]+>", "", s).strip()
        notes = store.get_list(config.NOTES_KEY, cid)
        group = ctx[len("nb_"):] if ctx.startswith("nb_") else None
        items = [(i, _strip(n.get("text", "") if isinstance(n, dict) else str(n)))
                 for i, n in enumerate(notes)
                 if (n.get("bucket", "fav") if isinstance(n, dict) else "fav") == "fav"
                 and (group is None or _s._fav_group(n.get("source", "Прочее") if isinstance(n, dict) else "Прочее") == group)]
        if group:
            label, _desc = _s._fav_group_info(group)
            return f"{label} · Сохранённое", items, f"as_bucket_favgrp_{group}"
        return "Сохранённое", items, "as_bucket_fav"
    if ctx in ("wl", "rl"):
        key = config.WATCHLIST_KEY if ctx == "wl" else config.READLIST_KEY
        title = "🍿 Чистка: посмотреть" if ctx == "wl" else "📚 Чистка: почитать"
        back = "a_watchlist" if ctx == "wl" else "a_readlist"
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(key, cid))]
        return title, items, back
    if ctx.startswith("kast_"):
        import wardrobe as _w
        _, zone_slug, subcat_idx, origin = ctx.split("_")
        zone = _w.ZONE_BY_SLUG.get(zone_slug, "Другое")
        subcats = store.ZONE_SUBCATS.get(zone, ["Другое"])
        subcat = subcats[int(subcat_idx)] if int(subcat_idx) < len(subcats) else "Другое"
        items = _wardrobe_flat(cid, zone, subcat)
        return subcat, items, f"w_delz_{zone_slug}_{origin}"
    if ctx.startswith("lv_") or ctx.startswith("lvls_"):
        is_leisure = ctx.startswith("lvls_")
        key = ctx[len("lvls_"):] if is_leisure else ctx[len("lv_"):]
        store_key = {"movies": config.WATCHLIST_KEY, "countries": config.FAVCOUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}.get(key)
        title = {"movies": f"{ui_label('cinema', 'Чистка: фильмы')}", "countries": f"{ui_label('countries', 'Чистка: страны')}",
                 "artists": f"{ui_label('music', 'Чистка: музыканты')}", "books": f"{ui_label('books', 'Чистка: книги')}"}.get(key, "Чистка")
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(store_key, cid))] if store_key else []
        return title, items, "m_leisure_settings" if is_leisure else "as_notes"
    if ctx.startswith("hid_"):
        key = ctx[len("hid_"):]
        store_key = {"movies": config.MOVIE_BLACKLIST_KEY, "books": config.BOOK_BLACKLIST_KEY,
                     "artists": config.MUSIC_DISLIKE_KEY, "countries": config.TRAVEL_DISLIKE_KEY}.get(key)
        title = {"movies": "Скрытое: фильмы", "books": "Скрытое: книги",
                 "artists": "Скрытое: музыканты", "countries": "Скрытое: страны"}.get(key, "Скрытое")
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(store_key, cid))] if store_key else []
        return title, items, f"as_love_{key}"
    if ctx.startswith("cfg_"):
        key = ctx[len("cfg_"):]
        store_key = {"countries": config.COUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY,
                     "books": config.BOOKS_KEY}.get(key)
        title = {"countries": ui_label("countries", "Чистка: страны"),
                 "artists": ui_label("music", "Чистка: музыканты"),
                 "books": ui_label("books", "Чистка: книги")}.get(key, "Чистка")
        back = {"countries": "set_countries",
                "artists": "set_artists",
                "books": "set_books"}.get(key, "set_home")
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(store_key, cid))] if store_key else []
        return title, items, back
    if ctx == "fridge":
        raw = store.get_list(config.FRIDGE_KEY, cid)
        items = [(i, it["name"] if isinstance(it, dict) else it) for i, it in enumerate(raw)]
        return "Чистка: холодильник", items, "as_fridge"
    if ctx == "recipes":
        recipes = store.get_list(config.MY_RECIPES_KEY, cid)
        items = [(i, r.get("name", f"Рецепт {i+1}")) for i, r in enumerate(recipes)]
        return ui_label("recipes", "Чистка: рецепты"), items, "as_my_recipes"
    return "Чистка", [], "m_learn"


def _action_label(ctx):
    """Текст кнопки группового действия — называет последствие, а не факт
    удаления записи. Таблица зафиксирована в docs/cleanup.md."""
    action = _primary_action(ctx)
    if action:
        return action["label"]
    if ctx.startswith("lv_") or ctx.startswith("lvls_"):
        return "Убрать из любимого"
    if ctx.startswith("hid_"):
        return "Вернуть в рекомендации"
    if ctx == "nb" or ctx.startswith("nb_"):
        return "Убрать из сохранённого"
    if ctx.startswith("kast_"):
        return "Удалить вещи"
    if ctx == "recipes":
        return "Удалить рецепты"
    if ctx == "fridge":
        return "Удалить продукты"
    if ctx in ("wl", "rl"):
        return "Убрать из просмотренного"
    if ctx.startswith("d_broken_"):
        return "Удалить битые записи"
    if ctx.startswith("d_"):
        return "Удалить из словаря"
    if ctx == "diary":
        return "Удалить записи"
    return "Применить действие"


def _button_action_label(label, action_id="remove"):
    """Удаляющие действия всегда отмечены единым ❌; restore/hide не меняем."""
    return delete_label(label) if action_id == "remove" else label


# Контексты, для которых удаление обратимо штатными средствами интерфейса
# (добавить обратно / нажать повторно) — не требуют экрана подтверждения перед
# групповым удалением. Все остальные view-контексты физически стирают запись
# без возможности программного восстановления — см. docs/cleanup.md,
# «Групповое действие и подтверждение».
def _is_reversible_ctx(ctx):
    action = _primary_action(ctx)
    if action:
        return not bool(action.get("confirm"))
    return ctx.startswith("lv_") or ctx.startswith("lvls_") or ctx.startswith("hid_")


# Контексты, где помимо «*️⃣ Выбрать все на странице» доступна кнопка «Удалить все
# N» (выбор всей коллекции, не только видимой страницы) — см. P2-1: сохраняет
# прежнее поведение кнопки «Удалить все» из самодельного чистильщика словаря
# без чекбоксов, но проводит её через общее правило подтверждения P2-2.
def _has_select_all_collection_button(ctx):
    return ctx.startswith("d_broken_")


async def send_cleanup(bot, cid, ctx, page=0, q=None):
    title, items, back = _ctx_items(cid, ctx)
    items = _sort_items(items)
    sel = _sel(cid, ctx)
    sel &= {i for i, _ in items}
    total = len(items)
    pages = max(1, (total + CLEAN_PAGE - 1) // CLEAN_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * CLEAN_PAGE:(page + 1) * CLEAN_PAGE]
    hint = f"Отметь нужное ✅ и нажми «{_action_label(ctx)}»."
    lines = [f"<b>{esc(title)}</b>", "", f"Всего: {total} · отмечено: {len(sel)}", "", hint]
    _lv_add_label = {
        "lv_movies": "🆕 Добавить фильм",
        "lv_countries": "🆕 Добавить страну",
        "lv_artists": "🆕 Добавить артиста",
        "lv_books": "🆕 Добавить книгу",
        "lvls_movies": "🆕 Добавить фильм",
        "lvls_countries": "🆕 Добавить страну",
        "lvls_artists": "🆕 Добавить артиста",
        "lvls_books": "🆕 Добавить книгу",
    }
    rows = []
    if ctx in _lv_add_label:
        if ctx.startswith("lvls_"):
            rows.append([InlineKeyboardButton(_lv_add_label[ctx], callback_data=f"ls_loveadd_{ctx[5:]}")])
        else:
            rows.append([InlineKeyboardButton(_lv_add_label[ctx], callback_data=f"as_loveadd_{ctx[3:]}")])
    if ctx == "fridge":
        for idx, lbl in chunk:
            mark = "✅" if idx in sel else "□"
            rows.append([InlineKeyboardButton(f"{mark} {lbl[:40]}", callback_data=f"clt_{ctx}_{idx}_{page}")])
    else:
        for idx, lbl in chunk:
            mark = "✅" if idx in sel else "□"
            rows.append([InlineKeyboardButton(f"{mark} {lbl[:36]}", callback_data=f"clt_{ctx}_{idx}_{page}")])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"clp_{ctx}_{(page - 1) % pages}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"clp_{ctx}_{(page + 1) % pages}"),
        ])
    if sel:
        rows.append([InlineKeyboardButton(
            f"{_button_action_label(_action_label(ctx))} ({len(sel)})",
            callback_data=f"cld_{ctx}_{page}",
        )])
    if len(chunk) >= 2:
        page_ids = {i for i, _ in chunk}
        page_label = "✅ Снять выбор на странице" if page_ids <= sel else choose_label("Выбрать все на странице")
        rows.append([InlineKeyboardButton(page_label, callback_data=f"cla_{ctx}_{page}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)
    if q is not None:
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)


def _cleanup_delete(cid, ctx):
    sel = _sel(cid, ctx)
    if not sel:
        return
    if ctx.startswith("d_"):
        import learning as _l
        words = [w for i, w in enumerate(_l._ensure_dict(cid)) if i not in sel]
        store.set_list(config.DICT_KEY, cid, words)
    elif ctx == "nb" or ctx.startswith("nb_"):
        notes = [n for i, n in enumerate(store.get_list(config.NOTES_KEY, cid)) if i not in sel]
        store.set_list(config.NOTES_KEY, cid, notes)
    elif ctx in ("wl", "rl"):
        key = config.WATCHLIST_KEY if ctx == "wl" else config.READLIST_KEY
        store.set_list(key, cid, [it for i, it in enumerate(store.get_list(key, cid)) if i not in sel])
    elif ctx.startswith("kast_"):
        store.remove_wardrobe_items(cid, sel)
    elif ctx.startswith("lv_") or ctx.startswith("lvls_"):
        key = ctx[len("lvls_"):] if ctx.startswith("lvls_") else ctx[len("lv_"):]
        store_key = {"movies": config.WATCHLIST_KEY, "countries": config.FAVCOUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}.get(key)
        if store_key:
            store.set_list(store_key, cid, [it for i, it in enumerate(store.get_list(store_key, cid)) if i not in sel])
            if key == "artists":
                import leisure_concerts
                leisure_concerts.invalidate_user_concerts_cache(cid)
    elif ctx.startswith("hid_"):
        key = ctx[len("hid_"):]
        store_key = {"movies": config.MOVIE_BLACKLIST_KEY, "books": config.BOOK_BLACKLIST_KEY,
                     "artists": config.MUSIC_DISLIKE_KEY, "countries": config.TRAVEL_DISLIKE_KEY}.get(key)
        if store_key:
            store.set_list(store_key, cid, [it for i, it in enumerate(store.get_list(store_key, cid)) if i not in sel])
    elif ctx.startswith("cfg_"):
        key = ctx[len("cfg_"):]
        store_key = {"countries": config.COUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY,
                     "books": config.BOOKS_KEY}.get(key)
        if store_key:
            store.set_list(store_key, cid, [it for i, it in enumerate(store.get_list(store_key, cid)) if i not in sel])
            if key == "artists":
                import leisure_concerts
                leisure_concerts.invalidate_user_concerts_cache(cid)
    elif ctx == "fridge":
        store.set_list(config.FRIDGE_KEY, cid, [it for i, it in enumerate(store.get_list(config.FRIDGE_KEY, cid)) if i not in sel])
    elif ctx == "recipes":
        store.set_list(config.MY_RECIPES_KEY, cid, [r for i, r in enumerate(store.get_list(config.MY_RECIPES_KEY, cid)) if i not in sel])
    store.list_sel[f"{cid}:{ctx}"] = set()


def _view_items(ctx, cid):
    """(заголовок, items=[(full_id, label)], back_callback) для view-контекста —
    id стабильны (store.ensure_list_ids), не позиционные индексы."""
    cfg = _collection_cfg(ctx)
    if cfg:
        records = _collection_records(cfg, cid)
        items = [(r["id"], _collection_item_label(cfg, r)) for r in records]
        return cfg["title"], items, cfg["back"]
    if ctx == "nb" or ctx.startswith("nb_"):
        import re as _re
        import settings as _s
        _strip = lambda s: _re.sub(r"<[^>]+>", "", s).strip()
        notes = store.ensure_list_ids(config.NOTES_KEY, cid)
        group = ctx[len("nb_"):] if ctx.startswith("nb_") else None
        items = [(n["id"], _strip(n.get("text", "")))
                 for n in notes
                 if n.get("bucket", "fav") == "fav"
                 and (group is None or _s._fav_group(n.get("source", "Прочее")) == group)]
        if group:
            label, _desc = _s._fav_group_info(group)
            return f"{label} · Сохранённое", items, f"as_bucket_favgrp_{group}"
        return "Сохранённое", items, "as_bucket_fav"
    if ctx.startswith("lv_") or ctx.startswith("lvls_"):
        is_leisure = ctx.startswith("lvls_")
        key = ctx[len("lvls_"):] if is_leisure else ctx[len("lv_"):]
        store_key = _LOVE_STORE_KEYS.get(key)
        title = {"movies": ui_label("cinema", "Чистка: фильмы"), "countries": ui_label("countries", "Чистка: страны"),
                 "artists": ui_label("music", "Чистка: музыканты"), "books": ui_label("books", "Чистка: книги")}.get(key, "Чистка")
        records = store.ensure_list_ids(store_key, cid) if store_key else []
        items = [(r["id"], _view_label(r)) for r in records]
        return title, items, "m_leisure_settings" if is_leisure else "as_notes"
    if ctx.startswith("hid_"):
        key = ctx[len("hid_"):]
        store_key = _HIDDEN_STORE_KEYS.get(key)
        title = {"movies": "Скрытое: фильмы", "books": "Скрытое: книги",
                 "artists": "Скрытое: музыканты", "countries": "Скрытое: страны"}.get(key, "Скрытое")
        records = store.ensure_list_ids(store_key, cid) if store_key else []
        items = [(r["id"], _view_label(r)) for r in records]
        return title, items, f"as_love_{key}"
    if ctx.startswith("d_broken_"):
        import learning as _l
        lang = ctx[len("d_broken_"):]
        flag = "🇳🇱" if lang == "nl" else "🇬🇧"
        words = store.ensure_list_ids(config.DICT_KEY, cid)
        items = [
            (w["id"], f"{_l._w_field(w, 'word', 'nl', 'en') or '(пусто)'} — {_l._w_field(w, 'ru') or '(нет перевода)'}")
            for w in words
            if _l._dict_lang(w) == lang
            and _l._is_bad_dict_item(_l._w_field(w, "word", "nl", "en"), _l._w_field(w, "ru"))
        ]
        return f"{flag} Чистка: битые записи", items, f"a_dictlang_{lang}"
    if ctx.startswith("d_"):
        import learning as _l
        lang = ctx[len("d_"):]
        flag = "🇳🇱" if lang == "nl" else "🇬🇧"
        words = store.ensure_list_ids(config.DICT_KEY, cid)
        items = [
            (w["id"], f"{_l._entry_term(w)} — {_l._entry_translation(w)}".strip(" —"))
            for w in words
            if _l._dict_lang(w) == lang
        ]
        return f"{flag} Чистка словаря", items, f"a_dictlang_{lang}"
    if ctx in ("wl", "rl"):
        key = config.WATCHLIST_KEY if ctx == "wl" else config.READLIST_KEY
        title = "🍿 Чистка: посмотреть" if ctx == "wl" else "📚 Чистка: почитать"
        back = "a_watchlist" if ctx == "wl" else "a_readlist"
        records = store.ensure_list_ids(key, cid)
        items = [(r["id"], _view_label(r)) for r in records]
        return title, items, back
    if ctx == "fridge":
        records = store.ensure_list_ids(config.FRIDGE_KEY, cid)
        items = [(r["id"], _view_label(r)) for r in records]
        return "Чистка: холодильник", items, "as_fridge"
    if ctx == "recipes":
        records = store.ensure_list_ids(config.MY_RECIPES_KEY, cid)
        items = [(r["id"], r.get("name", "Рецепт")) for r in records]
        return ui_label("recipes", "Чистка: рецепты"), items, "as_my_recipes"
    return "Чистка", [], "m_learn"


def _view_delete(ctx, cid, ids):
    """Удаляет выбранные записи из storage view-контекста по стабильным id."""
    if not ids:
        return 0
    cfg = _collection_cfg(ctx)
    store_key = _view_store_key(ctx)
    if not store_key:
        return 0
    removed = store.remove_from_list_by_ids(store_key, cid, ids)
    if removed and store_key == config.ARTISTS_KEY:
        import leisure_concerts
        leisure_concerts.invalidate_user_concerts_cache(cid)
    return removed


def _hidden_key_for_collection(ctx):
    canonical = _canonical_ctx(ctx)
    return {
        "cinema_favorites": config.MOVIE_BLACKLIST_KEY,
        "books_favorites": config.BOOK_BLACKLIST_KEY,
        "music_favorite_artists": config.MUSIC_DISLIKE_KEY,
        "travel_favorite_countries": config.TRAVEL_DISLIKE_KEY,
    }.get(canonical)


def _stoplist_kind_for_collection(ctx):
    canonical = _canonical_ctx(ctx)
    if canonical.startswith("cinema_"):
        return "movie"
    if canonical.startswith("books_"):
        return "book"
    if canonical.startswith("music_"):
        return "artist"
    if canonical.startswith("travel_"):
        return "country"
    return None


def _add_unique_raw(key, cid, value):
    target = str(value.get("name", value.get("value", value)) if isinstance(value, dict) else value).strip().lower()
    if not target:
        return False
    for item in store.get_list(key, cid):
        cur = str(item.get("name", item.get("value", item)) if isinstance(item, dict) else item).strip().lower()
        if cur == target:
            return False
    store.add_to_list(key, cid, value)
    return True


def _selected_values(ctx, cid, ids):
    cfg = _collection_cfg(ctx)
    if not cfg:
        return []
    ids = set(ids)
    out = []
    for item in _collection_records(cfg, cid):
        if item.get("id") not in ids:
            continue
        if "value" in item:
            out.append(item["value"])
        else:
            out.append({k: v for k, v in item.items() if k != "id"})
    return out


def _apply_collection_action(ctx, cid, action_id, ids):
    if not ids:
        return 0
    selected_values = _selected_values(ctx, cid, ids)
    stoplist_kind = _stoplist_kind_for_collection(ctx)
    if stoplist_kind:
        if action_id == "restore":
            for value in selected_values:
                recommendation_stoplist.remove(cid, stoplist_kind, value)
        else:
            canonical = _canonical_ctx(ctx)
            reason = "hidden" if action_id == "hide" else (
                "seen" if canonical in {"cinema_watched", "books_read", "music_seen_artists"}
                else "removed"
            )
            for value in selected_values:
                recommendation_stoplist.add(cid, stoplist_kind, value, reason)
    if action_id == "hide":
        return _view_delete(ctx, cid, ids)
    # remove and restore both mean: remove from the current collection only.
    # For hidden collections this is "Вернуть в рекомендации" and does not add
    # the item to favorites/saved lists.
    return _view_delete(ctx, cid, ids)


async def open_view(bot, cid, ctx, back=None):
    """Открывает новый view (PR3a) — снимает свежий revision коллекции и
    заводит короткоживущее серверное состояние просмотра."""
    ctx = _canonical_ctx(ctx)
    _purge_expired_views()
    title, items, default_back = _view_items(ctx, cid)
    store_key = _view_store_key(ctx)
    revision = store.get_list_revision(store_key, cid) if store_key else 0
    view_id = _new_view_id()
    _views[view_id] = {
        "ctx": ctx,
        "revision": revision,
        "selected_ids": set(),
        "page": 0,
        "back": back or default_back,
        "created_at": time.time(),
        "confirming": False,
    }
    await _render_view(bot, cid, view_id)


async def open_collection(bot, cid, collection_id, back=None):
    """Публичный вход в каноническую коллекцию."""
    await open_view(bot, cid, collection_id, back=back)


async def _render_view(bot, cid, view_id, q=None):
    view = _views.get(view_id)
    if view is None:
        await _send_view_stale_message(bot, cid, q)
        return
    ctx = view["ctx"]
    title, items, _back = _view_items(ctx, cid)
    items = _sort_items(items)
    all_ids = {i for i, _ in items}
    view["selected_ids"] &= all_ids
    sel = view["selected_ids"]
    total = len(items)
    pages = max(1, (total + CLEAN_PAGE - 1) // CLEAN_PAGE)
    page = max(0, min(view["page"], pages - 1))
    view["page"] = page
    chunk = items[page * CLEAN_PAGE:(page + 1) * CLEAN_PAGE]
    short_of = _short_ids([i for i, _ in chunk])
    if sel:
        count_line = f"Отмечено: {len(sel)} из {total}"
    else:
        count_line = f"Всего: {total}"
    lines = [f"<b>{esc(title)}</b>", "", count_line]
    if total:
        lines.append("")
    rows = []
    cfg = _collection_cfg(ctx)
    add_button = cfg.get("add_button") if cfg else _view_add_button(ctx)
    if add_button:
        label, callback_data = add_button
        rows.append([InlineKeyboardButton(label, callback_data=callback_data)])
    for full_id, lbl in chunk:
        mark = "✅" if full_id in sel else "□"
        rows.append([InlineKeyboardButton(f"{mark} {lbl[:36]}", callback_data=f"clt:{view_id}:{short_of[full_id]}")])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"clp:{view_id}:{(page - 1) % pages}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"clp:{view_id}:{(page + 1) % pages}"),
        ])
    if _has_select_all_collection_button(ctx) and total > len(chunk) and all_ids != sel:
        rows.append([InlineKeyboardButton(delete_label(f"Удалить все {total}"), callback_data=f"clx:{view_id}")])
    if sel:
        actions = (_collection_cfg(ctx) or {}).get("actions") or [{"id": "remove", "label": _action_label(ctx)}]
        for action in actions:
            action_label = _button_action_label(action["label"], action.get("id"))
            rows.append([InlineKeyboardButton(f"{action_label} ({len(sel)})",
                                              callback_data=f"clact:{view_id}:{action['id']}")])
    if len(chunk) >= 2:
        page_ids = {i for i, _ in chunk}
        page_label = "✅ Снять выбор на странице" if page_ids <= sel else choose_label("Выбрать все на странице")
        rows.append([InlineKeyboardButton(page_label, callback_data=f"cla:{view_id}:{page}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=view["back"]),
                 InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)
    if q is not None:
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)


async def _render_confirm(bot, cid, view_id, action_id="remove", q=None):
    """Промежуточный экран подтверждения перед необратимым групповым удалением
    (P2-2) — реальное удаление происходит только после явного повторного
    нажатия «Удалить N», не от одного нажатия финальной кнопки чистки."""
    view = _views[view_id]
    n = len(view["selected_ids"])
    action = _action_by_id(view["ctx"], action_id) or {"label": "Удалить"}
    label = _button_action_label(action.get("label") or "Удалить", action_id)
    text = f"{label} ({n})? Это действие нельзя отменить."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{label} ({n})", callback_data=f"clactc:{view_id}:{action_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"clcancel:{view_id}"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def _send_view_stale_message(bot, cid, q=None):
    text = "Список уже изменился. Откройте его заново."
    if q is not None:
        try:
            await q.message.edit_text(text)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text)


async def handle_view_callback(bot, cid, data, q=None):
    """Обрабатывает clt:/clp:/cla:/clx:/cld:/cldc:/clcancel: (двоеточие —
    маркер view-формата, отличает его от старого формата clt_/clp_/cla_/cld_ на
    подчёркивании).

    cld:/cldc: — двухшаговое групповое удаление (P2-2): для необратимых
    контекстов первое нажатие «cld» только показывает экран подтверждения, само
    удаление происходит на «cldc» (после явного повторного нажатия). Для
    обратимых контекстов (Любимое/Скрытое) «cld» удаляет сразу, как раньше —
    без confirm-экрана, так как оба действия обратимы штатными средствами."""
    op, view_id, *rest = data.split(":")
    view = _views.get(view_id)
    if view is not None and time.time() - view["created_at"] > VIEW_TTL_SECONDS:
        del _views[view_id]
        view = None
    if view is None:
        await _send_view_stale_message(bot, cid, q)
        return
    ctx = view["ctx"]
    store_key = _view_store_key(ctx)
    current_revision = store.get_list_revision(store_key, cid) if store_key else 0
    if op in ("cld", "cldc", "clact", "clactc") and view["revision"] != current_revision:
        # Коллекция изменилась параллельно с момента открытия view — не
        # выполняем удаление вслепую, инвалидируем и просим переоткрыть.
        del _views[view_id]
        await _send_view_stale_message(bot, cid, q)
        return
    if op == "clt":
        short_id = rest[0]
        _, items, _ = _view_items(ctx, cid)
        matches = [i for i, _ in items if i.startswith(short_id)]
        if matches:
            full_id = matches[0]
            view["selected_ids"].symmetric_difference_update({full_id})
        await _render_view(bot, cid, view_id, q=q)
        return
    if op == "clp":
        view["page"] = int(rest[0])
        await _render_view(bot, cid, view_id, q=q)
        return
    if op == "cla":
        page = int(rest[0])
        _, items, _ = _view_items(ctx, cid)
        items = _sort_items(items)
        page_ids = {i for i, _ in items[page * CLEAN_PAGE:(page + 1) * CLEAN_PAGE]}
        if page_ids <= view["selected_ids"]:
            view["selected_ids"] -= page_ids
        else:
            view["selected_ids"] |= page_ids
        await _render_view(bot, cid, view_id, q=q)
        return
    if op == "clx":
        _, items, _ = _view_items(ctx, cid)
        view["selected_ids"] = {i for i, _ in items}
        await _render_view(bot, cid, view_id, q=q)
        return
    if op == "cld":
        action = _primary_action(ctx) or {"id": "remove", "confirm": not _is_reversible_ctx(ctx)}
        if not action.get("confirm"):
            _apply_collection_action(ctx, cid, action["id"], view["selected_ids"])
            del _views[view_id]
            await open_view(bot, cid, ctx, back=view["back"])
            return
        view["confirming"] = True
        await _render_confirm(bot, cid, view_id, action["id"], q=q)
        return
    if op == "clact":
        action_id = rest[0] if rest else "remove"
        action = _action_by_id(ctx, action_id) or {"id": action_id, "confirm": True}
        if not action.get("confirm"):
            _apply_collection_action(ctx, cid, action_id, view["selected_ids"])
            del _views[view_id]
            await open_view(bot, cid, ctx, back=view["back"])
            return
        view["confirming"] = True
        await _render_confirm(bot, cid, view_id, action_id, q=q)
        return
    if op == "cldc":
        _apply_collection_action(ctx, cid, "remove", view["selected_ids"])
        del _views[view_id]
        await open_view(bot, cid, ctx, back=view["back"])
        return
    if op == "clactc":
        action_id = rest[0] if rest else "remove"
        _apply_collection_action(ctx, cid, action_id, view["selected_ids"])
        del _views[view_id]
        await open_view(bot, cid, ctx, back=view["back"])
        return
    if op == "clcancel":
        view["confirming"] = False
        await _render_view(bot, cid, view_id, q=q)
        return


async def open_cleanup(bot, cid, ctx, back=None):
    """Свежий вход в режим чистки — сбрасываем выбор.

    Для view-контекстов (nb/nb_*, PR3a) делегирует на новую инфраструктуру
    (стабильный id + revision + короткий callback_data); для остальных —
    прежний позиционный формат без изменений."""
    if _is_view_ctx(ctx):
        await open_view(bot, cid, ctx, back=back)
        return
    store.list_sel[f"{cid}:{ctx}"] = set()
    await send_cleanup(bot, cid, ctx, 0)


async def handle_cleanup(bot, cid, data, q=None):
    parts = data.split("_")
    op = parts[0]
    if op == "clt":
        page = int(parts[-1])
        idx_raw = parts[-2]
        ctx = "_".join(parts[1:-2])
        # индекс — int для большинства контекстов, но uuid-строка для гардероба (kast_*):
        # приводим только если это действительно число, иначе оставляем строкой как есть.
        try:
            idx = int(idx_raw)
        except ValueError:
            idx = idx_raw
        _sel(cid, ctx).symmetric_difference_update({idx})
        await send_cleanup(bot, cid, ctx, page, q=q)
        return
    page, ctx = int(parts[-1]), "_".join(parts[1:-1])
    if op == "clp":
        await send_cleanup(bot, cid, ctx, page, q=q)
        return
    if op == "cla":
        _, items, _ = _ctx_items(cid, ctx)
        items = _sort_items(items)
        page_ids = {i for i, _ in items[page * CLEAN_PAGE:(page + 1) * CLEAN_PAGE]}
        sel = _sel(cid, ctx)
        if page_ids <= sel:
            sel -= page_ids
        else:
            sel |= page_ids
        await send_cleanup(bot, cid, ctx, page, q=q)
        return
    if op == "cld":
        _cleanup_delete(cid, ctx)
        await send_cleanup(bot, cid, ctx, 0, q=q)
        return
