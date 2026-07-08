"""Движок чистки списков: пагинация + мультивыбор.

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
           fridge/recipes/lagom/diary (холодильник/рецепты/Здоровье/История
           самочувствия — lagom хранится не отдельным KV-ключом, а полем
           внутри профиля пользователя, см. store.ensure_list_ids_via).
"""
import secrets
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
from util import esc

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


# lagom хранится не отдельным KV-ключом, а полем внутри профиля пользователя
# (memory.get_lagom/set_lagom → store.get_profile()["lagom"]) — у него нет
# storage-ключа для _view_store_key/store.ensure_list_ids. Используем
# фиксированное имя слота revision и отдельные ветки в _view_items/_view_delete
# через store.ensure_list_ids_via/remove_from_list_by_ids_via.
_LAGOM_REVISION_SLOT = "profile.lagom"


def _is_view_ctx(ctx):
    return (ctx == "nb" or ctx.startswith("nb_")
            or ctx.startswith("lv_") or ctx.startswith("lvls_")
            or ctx.startswith("hid_")
            or ctx.startswith("d_") or ctx in ("wl", "rl")
            or ctx in ("fridge", "recipes", "lagom", "diary"))


def _view_store_key(ctx):
    """Storage-ключ коллекции для view-контекста. lagom возвращает фиксированный
    revision-слот вместо реального storage-ключа — см. _LAGOM_REVISION_SLOT."""
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
    if ctx == "lagom":
        return _LAGOM_REVISION_SLOT
    if ctx == "diary":
        return config.DIARY_KEY
    return None


_VIEW_ADD_LABEL = {
    "movies": "✏️ Добавить фильм",
    "countries": "✏️ Добавить страну",
    "artists": "✏️ Добавить артиста",
    "books": "✏️ Добавить книгу",
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
    return it.get("name", "")


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
        _, lang, kind = ctx.split("_")
        flag = "🇳🇱" if lang == "nl" else "🇬🇧"
        label = "слов" if kind == "word" else "фраз"
        words = _l._ensure_dict(cid)
        items = []
        for i, w in enumerate(words):
            if _l._dict_lang(w) == lang and _l._dict_kind(w) == kind:
                term = _l._w_field(w, "word", "nl", "en")
                ru = _l._w_field(w, "ru")
                items.append((i, f"{term} — {ru}".strip(" —")))
        return f"{flag} Чистка: {label}", items, f"a_dictlang_{lang}"
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
            return f"{label} · удалить из Сохранить", items, f"as_bucket_favgrp_{group}"
        return "⭐ Чистка: сохранение", items, "as_bucket_fav"
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
        return f"Чистка: {subcat}", items, f"w_delz_{zone_slug}_{origin}"
    if ctx.startswith("lv_") or ctx.startswith("lvls_"):
        is_leisure = ctx.startswith("lvls_")
        key = ctx[len("lvls_"):] if is_leisure else ctx[len("lv_"):]
        store_key = {"movies": config.WATCHLIST_KEY, "countries": config.FAVCOUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}.get(key)
        title = {"movies": "🎬 Чистка: фильмы", "countries": "🧳 Чистка: страны",
                 "artists": "🎸 Чистка: музыканты", "books": "📖 Чистка: книги"}.get(key, "Чистка")
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(store_key, cid))] if store_key else []
        return title, items, "m_leisure_settings" if is_leisure else "as_notes"
    if ctx.startswith("hid_"):
        key = ctx[len("hid_"):]
        store_key = {"movies": config.MOVIE_BLACKLIST_KEY, "books": config.BOOK_BLACKLIST_KEY,
                     "artists": config.MUSIC_DISLIKE_KEY, "countries": config.TRAVEL_DISLIKE_KEY}.get(key)
        title = {"movies": "🚫 Скрытое: фильмы", "books": "🚫 Скрытое: книги",
                 "artists": "🚫 Скрытое: музыканты", "countries": "🚫 Скрытое: страны"}.get(key, "Скрытое")
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(store_key, cid))] if store_key else []
        return title, items, f"as_love_{key}"
    if ctx.startswith("cfg_"):
        key = ctx[len("cfg_"):]
        store_key = {"countries": config.COUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY,
                     "books": config.BOOKS_KEY}.get(key)
        title = {"countries": "🧳 Чистка: страны",
                 "artists": "🎸 Чистка: музыканты",
                 "books": "📖 Чистка: книги"}.get(key, "Чистка")
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
        return "🍳 Чистка: рецепты", items, "as_my_recipes"
    if ctx == "lagom":
        import memory
        items = [(i, it) for i, it in enumerate(memory.get_lagom(cid))]
        return "Чистка: Здоровье", items, "set_lagom"
    return "Чистка", [], "m_learn"


def _action_label(ctx):
    """Текст кнопки группового действия — называет последствие, а не факт
    удаления записи. Таблица зафиксирована в docs/cleanup.md."""
    if ctx.startswith("lv_") or ctx.startswith("lvls_"):
        return "Убрать из любимого"
    if ctx.startswith("hid_"):
        return "Вернуть в рекомендации"
    if ctx == "nb" or ctx.startswith("nb_"):
        return "Удалить сохранённое"
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
        return "Удалить слова" if ctx.endswith("_word") else "Удалить фразы"
    if ctx == "lagom":
        return "Удалить принципы"
    if ctx == "diary":
        return "Удалить записи"
    return "Удалить отмеченные"


# Контексты, для которых удаление обратимо штатными средствами интерфейса
# (добавить обратно / нажать повторно) — не требуют экрана подтверждения перед
# групповым удалением. Все остальные view-контексты физически стирают запись
# без возможности программного восстановления — см. docs/cleanup.md,
# «Групповое действие и подтверждение».
def _is_reversible_ctx(ctx):
    return ctx.startswith("lv_") or ctx.startswith("lvls_") or ctx.startswith("hid_")


# Контексты, где помимо «Выбрать все на странице» доступна кнопка «Удалить все
# N» (выбор всей коллекции, не только видимой страницы) — см. P2-1: сохраняет
# прежнее поведение кнопки «🗑 Удалить все» из самодельного чистильщика словаря
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
    lines = [f"🧹 <b>{esc(title)}</b>", f"Всего: {total} · отмечено: {len(sel)}", "", hint]
    _lv_add_label = {
        "lv_movies": "✏️ Добавить фильм",
        "lv_countries": "✏️ Добавить страну",
        "lv_artists": "✏️ Добавить артиста",
        "lv_books": "✏️ Добавить книгу",
        "lvls_movies": "✏️ Добавить фильм",
        "lvls_countries": "✏️ Добавить страну",
        "lvls_artists": "✏️ Добавить артиста",
        "lvls_books": "✏️ Добавить книгу",
    }
    rows = []
    if ctx == "fridge":
        for idx, lbl in chunk:
            mark = "✅" if idx in sel else "▫️"
            rows.append([InlineKeyboardButton(f"{mark} {lbl[:40]}", callback_data=f"clt_{ctx}_{idx}_{page}")])
    else:
        for idx, lbl in chunk:
            mark = "✅" if idx in sel else "▫️"
            rows.append([InlineKeyboardButton(f"{mark} {lbl[:36]}", callback_data=f"clt_{ctx}_{idx}_{page}")])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"clp_{ctx}_{(page - 1) % pages}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"clp_{ctx}_{(page + 1) % pages}"),
        ])
    if len(chunk) >= 2:
        page_ids = {i for i, _ in chunk}
        page_label = "✅ Снять выбор на странице" if page_ids <= sel else "✅ Выбрать все на странице"
        rows.append([InlineKeyboardButton(page_label, callback_data=f"cla_{ctx}_{page}")])
    if sel:
        rows.append([InlineKeyboardButton(f"{_action_label(ctx)} ({len(sel)})", callback_data=f"cld_{ctx}_{page}")])
    if ctx in _lv_add_label:
        if ctx.startswith("lvls_"):
            rows.append([InlineKeyboardButton(_lv_add_label[ctx], callback_data=f"ls_loveadd_{ctx[5:]}")])
        else:
            rows.append([InlineKeyboardButton(_lv_add_label[ctx], callback_data=f"as_loveadd_{ctx[3:]}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back)])
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
    elif ctx == "fridge":
        store.set_list(config.FRIDGE_KEY, cid, [it for i, it in enumerate(store.get_list(config.FRIDGE_KEY, cid)) if i not in sel])
    elif ctx == "recipes":
        store.set_list(config.MY_RECIPES_KEY, cid, [r for i, r in enumerate(store.get_list(config.MY_RECIPES_KEY, cid)) if i not in sel])
    elif ctx == "lagom":
        import memory
        memory.set_lagom(cid, [it for i, it in enumerate(memory.get_lagom(cid)) if i not in sel])
    store.list_sel[f"{cid}:{ctx}"] = set()


def _view_items(ctx, cid):
    """(заголовок, items=[(full_id, label)], back_callback) для view-контекста —
    id стабильны (store.ensure_list_ids), не позиционные индексы."""
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
            return f"{label} · удалить из Сохранить", items, f"as_bucket_favgrp_{group}"
        return "⭐ Чистка: сохранение", items, "as_bucket_fav"
    if ctx.startswith("lv_") or ctx.startswith("lvls_"):
        is_leisure = ctx.startswith("lvls_")
        key = ctx[len("lvls_"):] if is_leisure else ctx[len("lv_"):]
        store_key = _LOVE_STORE_KEYS.get(key)
        title = {"movies": "🎬 Чистка: фильмы", "countries": "🧳 Чистка: страны",
                 "artists": "🎸 Чистка: музыканты", "books": "📖 Чистка: книги"}.get(key, "Чистка")
        records = store.ensure_list_ids(store_key, cid) if store_key else []
        items = [(r["id"], _view_label(r)) for r in records]
        return title, items, "m_leisure_settings" if is_leisure else "as_notes"
    if ctx.startswith("hid_"):
        key = ctx[len("hid_"):]
        store_key = _HIDDEN_STORE_KEYS.get(key)
        title = {"movies": "🚫 Скрытое: фильмы", "books": "🚫 Скрытое: книги",
                 "artists": "🚫 Скрытое: музыканты", "countries": "🚫 Скрытое: страны"}.get(key, "Скрытое")
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
        _, lang, kind = ctx.split("_")
        flag = "🇳🇱" if lang == "nl" else "🇬🇧"
        label = "слов" if kind == "word" else "фраз"
        words = store.ensure_list_ids(config.DICT_KEY, cid)
        items = [
            (w["id"], f"{_l._w_field(w, 'word', 'nl', 'en')} — {_l._w_field(w, 'ru')}".strip(" —"))
            for w in words
            if _l._dict_lang(w) == lang and _l._dict_kind(w) == kind
        ]
        return f"{flag} Чистка: {label}", items, f"a_dictlang_{lang}"
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
        return "🍳 Чистка: рецепты", items, "as_my_recipes"
    if ctx == "lagom":
        import memory
        records = store.ensure_list_ids_via(memory.get_lagom, memory.set_lagom, _LAGOM_REVISION_SLOT, cid)
        items = [(r["id"], _view_label(r)) for r in records]
        return "Чистка: Здоровье", items, "set_lagom"
    if ctx == "diary":
        records = store.ensure_list_ids(config.DIARY_KEY, cid)
        items = [(r["id"], f"{r.get('date', '')} — {r.get('text', '')}".strip(" —")) for r in records]
        return "📝 История самочувствия", items, "m_balance"
    return "Чистка", [], "m_learn"


def _view_delete(ctx, cid, ids):
    """Удаляет выбранные записи из storage view-контекста по стабильным id."""
    if not ids:
        return 0
    if ctx == "lagom":
        import memory
        return store.remove_from_list_by_ids_via(memory.get_lagom, memory.set_lagom, _LAGOM_REVISION_SLOT, cid, ids)
    store_key = _view_store_key(ctx)
    if not store_key:
        return 0
    return store.remove_from_list_by_ids(store_key, cid, ids)


async def open_view(bot, cid, ctx, back=None):
    """Открывает новый view (PR3a) — снимает свежий revision коллекции и
    заводит короткоживущее серверное состояние просмотра."""
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
    hint = f"Отметь нужное ✅ и нажми «{_action_label(ctx)}»."
    lines = [f"🧹 <b>{esc(title)}</b>", f"Всего: {total} · отмечено: {len(sel)}", "", hint]
    rows = []
    for full_id, lbl in chunk:
        mark = "✅" if full_id in sel else "▫️"
        rows.append([InlineKeyboardButton(f"{mark} {lbl[:36]}", callback_data=f"clt:{view_id}:{short_of[full_id]}")])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"clp:{view_id}:{(page - 1) % pages}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"clp:{view_id}:{(page + 1) % pages}"),
        ])
    if len(chunk) >= 2:
        page_ids = {i for i, _ in chunk}
        page_label = "✅ Снять выбор на странице" if page_ids <= sel else "✅ Выбрать все на странице"
        rows.append([InlineKeyboardButton(page_label, callback_data=f"cla:{view_id}:{page}")])
    if _has_select_all_collection_button(ctx) and total > len(chunk) and all_ids != sel:
        rows.append([InlineKeyboardButton(f"🗑 Удалить все {total}", callback_data=f"clx:{view_id}")])
    if sel:
        rows.append([InlineKeyboardButton(f"{_action_label(ctx)} ({len(sel)})", callback_data=f"cld:{view_id}")])
    add_button = _view_add_button(ctx)
    if add_button:
        label, callback_data = add_button
        rows.append([InlineKeyboardButton(label, callback_data=callback_data)])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=view["back"])])
    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)
    if q is not None:
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)


async def _render_confirm(bot, cid, view_id, q=None):
    """Промежуточный экран подтверждения перед необратимым групповым удалением
    (P2-2) — реальное удаление происходит только после явного повторного
    нажатия «Удалить N», не от одного нажатия финальной кнопки чистки."""
    view = _views[view_id]
    n = len(view["selected_ids"])
    text = f"Удалить {n}? Это действие нельзя отменить."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🗑 Удалить {n}", callback_data=f"cldc:{view_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"clcancel:{view_id}")],
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
    if op in ("cld", "cldc") and view["revision"] != current_revision:
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
        if _is_reversible_ctx(ctx):
            _view_delete(ctx, cid, view["selected_ids"])
            del _views[view_id]
            await open_view(bot, cid, ctx, back=view["back"])
            return
        view["confirming"] = True
        await _render_confirm(bot, cid, view_id, q=q)
        return
    if op == "cldc":
        _view_delete(ctx, cid, view["selected_ids"])
        del _views[view_id]
        await open_view(bot, cid, ctx, back=view["back"])
        return
    if op == "clcancel":
        view["confirming"] = False
        await _render_view(bot, cid, view_id, q=q)
        return


async def open_cleanup(bot, cid, ctx):
    """Свежий вход в режим чистки — сбрасываем выбор.

    Для view-контекстов (nb/nb_*, PR3a) делегирует на новую инфраструктуру
    (стабильный id + revision + короткий callback_data); для остальных —
    прежний позиционный формат без изменений."""
    if _is_view_ctx(ctx):
        await open_view(bot, cid, ctx)
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
