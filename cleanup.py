"""Движок чистки списков: пагинация + мультивыбор.

Используется из learning, notes, wardrobe, balance, bot.
Контексты: d_<lang>_<kind> (словарь), t_<lang> (темы), nb (закладки),
           wl/rl (watchlist/readlist), kast (шкаф), lv_<key> (любимые),
           fridge (холодильник), recipes (рецепты).
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
from util import esc

CLEAN_PAGE = 8


def _sel(cid, ctx):
    return store.list_sel.setdefault(f"{cid}:{ctx}", set())


def _list_label(it):
    return it.get("name", "") if isinstance(it, dict) else str(it)


def _sort_items(items):
    """Сортирует отображение по алфавиту, сохраняя исходные id для удаления."""
    return sorted(items, key=lambda item: (item[1] or "").casefold().strip())


def _wardrobe_flat(cid):
    """Плоский стабильный список (категория, вещь) шкафа."""
    flat = []
    for cat, items in store.load_wardrobe(cid).items():
        if cat == "_v" or not isinstance(items, list):
            continue
        for it in items:
            flat.append((cat, it))
    return flat


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
    if ctx.startswith("t_"):
        import learning as _l
        _, lang = ctx.split("_")
        language = "нидерландский" if lang == "nl" else "английский"
        topics = _l.get_topics(cid, language)
        items = [(i, (t.get("text", "") if isinstance(t, dict) else str(t))) for i, t in enumerate(topics)]
        return f"{_l._flag(language)} Чистка: темы", items, f"a_topics_{lang}"
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
            return f"{label} · удалить из Позже", items, f"as_bucket_favgrp_{group}"
        return "⭐ Чистка: закладки", items, "as_bucket_fav"
    if ctx in ("wl", "rl"):
        key = config.WATCHLIST_KEY if ctx == "wl" else config.READLIST_KEY
        title = "🍿 Чистка: посмотреть" if ctx == "wl" else "📚 Чистка: почитать"
        back = "a_watchlist" if ctx == "wl" else "a_readlist"
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(key, cid))]
        return title, items, back
    if ctx == "kast":
        flat = _wardrobe_flat(cid)
        items = [(i, it) for i, (cat, it) in enumerate(flat)]
        return "🗄 Чистка: шкаф", items, "w_closet"
    if ctx == "kast_s":
        flat = _wardrobe_flat(cid)
        items = [(i, it) for i, (cat, it) in enumerate(flat)]
        return "🗄 Чистка: шкаф", items, "set_wardrobe"
    if ctx.startswith("lv_") or ctx.startswith("lvls_"):
        is_leisure = ctx.startswith("lvls_")
        key = ctx[len("lvls_"):] if is_leisure else ctx[len("lv_"):]
        store_key = {"movies": config.WATCHLIST_KEY, "countries": config.COUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}.get(key)
        title = {"movies": "🎬 Чистка: фильмы", "countries": "🧳 Чистка: страны",
                 "artists": "🎸 Чистка: артисты", "books": "📖 Чистка: книги"}.get(key, "Чистка")
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(store_key, cid))] if store_key else []
        return title, items, "m_leisure_settings" if is_leisure else "as_notes"
    if ctx.startswith("cfg_"):
        key = ctx[len("cfg_"):]
        store_key = {"countries": config.COUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY,
                     "books": config.BOOKS_KEY}.get(key)
        title = {"countries": "🧳 Чистка: страны",
                 "artists": "🎸 Чистка: артисты",
                 "books": "📖 Чистка: книги"}.get(key, "Чистка")
        back = {"countries": "set_countries",
                "artists": "set_artists",
                "books": "set_books"}.get(key, "set_home")
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(store_key, cid))] if store_key else []
        return title, items, back
    if ctx == "fridge":
        raw = store.get_list(config.FRIDGE_KEY, cid)
        items = [(i, it["name"] if isinstance(it, dict) else it) for i, it in enumerate(raw)]
        return "🧊 Чистка: холодильник", items, "as_fridge"
    if ctx == "recipes":
        recipes = store.get_list(config.MY_RECIPES_KEY, cid)
        items = [(i, r.get("name", f"Рецепт {i+1}")) for i, r in enumerate(recipes)]
        return "🍳 Чистка: рецепты", items, "as_my_recipes"
    if ctx == "lagom":
        import memory
        items = [(i, it) for i, it in enumerate(memory.get_lagom(cid))]
        return "🍃 Чистка: Лагом", items, "set_lagom"
    return "Чистка", [], "m_learn"


async def send_cleanup(bot, cid, ctx, page=0, q=None):
    title, items, back = _ctx_items(cid, ctx)
    items = _sort_items(items)
    sel = _sel(cid, ctx)
    sel &= {i for i, _ in items}
    total = len(items)
    pages = max(1, (total + CLEAN_PAGE - 1) // CLEAN_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * CLEAN_PAGE:(page + 1) * CLEAN_PAGE]
    hint = "Отметь лишнее ✅ и нажми «Удалить отмеченные»." if ctx == "nb" or ctx.startswith("nb_") else "Отметь выученное ✅ и нажми «Удалить отмеченные»."
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
    if ctx in _lv_add_label:
        if ctx.startswith("lvls_"):
            rows.append([InlineKeyboardButton(_lv_add_label[ctx], callback_data=f"ls_loveadd_{ctx[5:]}")])
        else:
            rows.append([InlineKeyboardButton(_lv_add_label[ctx], callback_data=f"as_loveadd_{ctx[3:]}")])
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
    rows.append([InlineKeyboardButton("☑️ Отметить всё на странице", callback_data=f"cla_{ctx}_{page}")])
    if sel:
        rows.append([InlineKeyboardButton(f"❌ Удалить отмеченные ({len(sel)})", callback_data=f"cld_{ctx}_{page}")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=back)])
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
    elif ctx.startswith("t_"):
        import learning as _l
        _, lang = ctx.split("_")
        language = "нидерландский" if lang == "nl" else "английский"
        topics = [t for i, t in enumerate(_l.get_topics(cid, language)) if i not in sel]
        store.set_list(_l._topics_key(language), cid, topics)
    elif ctx == "nb" or ctx.startswith("nb_"):
        notes = [n for i, n in enumerate(store.get_list(config.NOTES_KEY, cid)) if i not in sel]
        store.set_list(config.NOTES_KEY, cid, notes)
    elif ctx in ("wl", "rl"):
        key = config.WATCHLIST_KEY if ctx == "wl" else config.READLIST_KEY
        store.set_list(key, cid, [it for i, it in enumerate(store.get_list(key, cid)) if i not in sel])
    elif ctx in ("kast", "kast_s"):
        flat = _wardrobe_flat(cid)
        drop = {flat[i] for i in sel if i < len(flat)}
        w = store.load_wardrobe(cid)
        for cat, it in drop:
            if cat in w and it in w[cat]:
                w[cat].remove(it)
                if not w[cat]:
                    del w[cat]
        store.save_wardrobe(w, cid)
    elif ctx.startswith("lv_") or ctx.startswith("lvls_"):
        key = ctx[len("lvls_"):] if ctx.startswith("lvls_") else ctx[len("lv_"):]
        store_key = {"movies": config.WATCHLIST_KEY, "countries": config.COUNTRIES_KEY,
                     "artists": config.ARTISTS_KEY, "books": config.BOOKS_KEY}.get(key)
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


async def open_cleanup(bot, cid, ctx):
    """Свежий вход в режим чистки — сбрасываем выбор."""
    store.list_sel[f"{cid}:{ctx}"] = set()
    await send_cleanup(bot, cid, ctx, 0)


async def handle_cleanup(bot, cid, data, q=None):
    parts = data.split("_")
    op = parts[0]
    if op == "clt":
        page, idx, ctx = int(parts[-1]), int(parts[-2]), "_".join(parts[1:-2])
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
