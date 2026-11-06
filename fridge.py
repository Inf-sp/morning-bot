"""Холодильник: список продуктов, категории и приготовление из остатков."""

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
import cooking
from fridge_model import (
    _CAT_BTN_LABEL,
    _CAT_EMOJI,
    _CAT_ORDER,
    _fridge_available,
    _fridge_migrate,
    _fridge_normalize_input,
    _fridge_rejected_lines,
    _fridge_split_input,
    BASE_PRODUCTS,
    BASE_VERSION,
    favorite_keys,
    base_key,
    match_base,
    set_favorite,
)
from ui import food as food_ui
from ui.navigation import nav_row

_log = logging.getLogger(__name__)

send_leftovers = cooking.send_leftovers


def _mark_transient_edit(bot, cid, message):
    marker = getattr(bot, "mark_transient_message", None)
    if marker is not None:
        marker(cid, getattr(message, "message_id", None))


def _load_products(cid) -> list:
    """Продукты пользователя. Переход на единый список (BASE_VERSION): свои любимые продукты прежних версий
    отмечают похожий продукт списка («куриные ножки» → «Курица»); сами свои продукты остаются в данных скрытыми."""
    cid_s = str(cid)
    raw = store.get_list(config.FRIDGE_KEY, cid_s)
    items = _fridge_migrate(raw)
    if (store.get_profile(cid_s) or {}).get("fridge_base_v") != BASE_VERSION:
        favorites = favorite_keys(items)
        for item in list(items):
            if item.get("on", True) and item["name"].casefold() not in favorites:
                found = match_base(item["name"], item.get("cat"))
                if found and base_key(found[1]) not in favorites:
                    items = set_favorite(items, found[0], found[1], True)
                    favorites.add(base_key(found[1]))
        items = _fridge_migrate(items)
        store.mutate_profile(cid_s, lambda profile: ({**profile, "fridge_base_v": BASE_VERSION}, None))
    if items != raw:
        store.set_list(config.FRIDGE_KEY, cid_s, items)
    return items


async def _show(bot, cid, msg, rows, q=None):
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            _log.debug("fridge screen: ignored error", exc_info=True)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb, transient=True)


# ---------- Любимые продукты: главный экран (категории) ----------
async def send_fridge(bot, cid, q=None, back="m_food"):
    """«Любимые продукты»: категории единого списка; на кнопке — сколько в ней любимых. Своих продуктов,
    добавления и удаления нет — только отметка."""
    items = _load_products(cid)
    favorites = favorite_keys(items)
    msg = food_ui.fridge_home(len(favorites))
    rows = [[InlineKeyboardButton(
        f"{_CAT_BTN_LABEL[cat]} · {sum(base_key(name) in favorites for name in BASE_PRODUCTS[cat])}",
        callback_data=f"as_fridge_cat_{ci}_0")] for ci, cat in enumerate(_CAT_ORDER)]
    rows.append([InlineKeyboardButton("📝 Выбрать предпочтения", callback_data="set_pref_cuisines")])
    rows.append(nav_row(back))
    await _show(bot, cid, msg, rows, q)


# ---------- Категория: единый список, ✅ любимый / □ ----------
async def send_fridge_cat(bot, cid, cat_idx: int, page: int = 0, q=None):
    if not 0 <= cat_idx < len(_CAT_ORDER):
        await send_fridge(bot, cid, q)
        return
    cat = _CAT_ORDER[cat_idx]
    favorites = favorite_keys(_load_products(cid))
    names = BASE_PRODUCTS[cat]
    chosen = sum(base_key(name) in favorites for name in names)
    msg = food_ui.fridge_category(_CAT_BTN_LABEL[cat], len(names), chosen)
    rows = [[InlineKeyboardButton(f"{'✅' if base_key(name) in favorites else '□'} {name}",
                                  callback_data=f"as_fridge_fav_{cat_idx}_{index}")]
            for index, name in enumerate(names)]
    rows.append(nav_row("as_fridge_home"))
    await _show(bot, cid, msg, rows, q)


async def fridge_fav(bot, cid, cat_idx: int, index: int, q=None):
    """Нажатие на продукт списка: ✅ любимый ↔ □."""
    if 0 <= cat_idx < len(_CAT_ORDER) and 0 <= index < len(BASE_PRODUCTS[_CAT_ORDER[cat_idx]]):
        cat = _CAT_ORDER[cat_idx]
        name = BASE_PRODUCTS[cat][index]
        items = _load_products(cid)
        on = base_key(name) not in favorite_keys(items)
        store.set_list(config.FRIDGE_KEY, str(cid), _fridge_migrate(set_favorite(items, cat, name, on)))
    await send_fridge_cat(bot, cid, cat_idx, 0, q)


async def fridge_add_done(bot, cid, text, cat_idx: int = -1):
    """«Добавь в продукты: курица, креветки» — отмечает продукты единого списка любимыми; чего в списке нет —
    «Не добавил: … — нет в списке»."""
    cid_s = str(cid)
    items = _load_products(cid)
    first_fill = not _fridge_available(items)
    favorites = favorite_keys(items)
    added, duplicates, rejected = [], [], []
    for name in _fridge_split_input(text):
        found = match_base(name)
        if not found:
            rejected.append((name, "нет в списке продуктов"))
        elif base_key(found[1]) in favorites:
            duplicates.append(found[1])
        else:
            items = set_favorite(items, found[0], found[1], True)
            favorites.add(base_key(found[1]))
            added.append(found)
    store.set_list(config.FRIDGE_KEY, cid_s, _fridge_migrate(items))
    added_by_cat = {}
    for cat, name in added:
        added_by_cat.setdefault(cat, []).append(name)
    rejected = [*rejected, *_fridge_rejected_lines(text)]
    if (added or duplicates or rejected) and not first_fill:
        msg = food_ui.fridge_updated(
            added_by_cat, [name for _cat, name in added], duplicates, rejected, _CAT_ORDER, _CAT_EMOJI, _CAT_BTN_LABEL)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    if first_fill and _fridge_available(items):
        await _send_cooking_home(bot, cid)
        return
    await send_fridge(bot, cid)


async def _send_cooking_home(bot, cid, q=None):
    """После первых любимых продуктов сразу показываем рецепт дня."""
    import menu

    await menu.send_food_menu(bot, cid, q=q)


def _fridge_payload_from_chat(text: str) -> str:
    raw = str(text or "").strip()
    low = raw.lower()
    if "<li" in low and "продукт" in low:
        return _fridge_normalize_input(raw)

    patterns = [
        r"(?:добавь|добавить|закинь|запиши|сохрани)\s+"
        r"(?:это\s+)?(?:в\s+)?(?:список\s+)?(?:моих\s+)?(?:любимы[ехм]\s+)?"
        r"(?:продуктов|продукты|холодильник)\s*[:\-—]?\s*(.+)",
        r"(?:в\s+)?(?:продукты|холодильник)\s*[:\-—]\s*(.+)",
        # «добавь молоко и яйца в холодильник»
        r"^\s*(?:добавь|добавить|закинь|запиши|сохрани)\s+(.+?)\s+в\s+(?:мой\s+)?(?:список\s+)?"
        r"(?:продуктов|продукты|холодильник)\s*[.!]?\s*$",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
    return ""


async def try_add_fridge_from_chat(bot, cid, text) -> bool:
    payload = _fridge_payload_from_chat(text)
    if not payload:
        return False
    if not _fridge_split_input(payload):
        return False
    await fridge_add_done(bot, cid, payload)
    return True


async def send_fridge_recipe(bot, cid):
    raw = store.get_list(config.FRIDGE_KEY, str(cid))
    available = _fridge_available(raw)
    if not available:
        msg = food_ui.fridge_empty_for_recipe()
        await bot.send_message(chat_id=cid, text=msg.text)
        return
    await send_leftovers(bot, cid, ", ".join(available))
