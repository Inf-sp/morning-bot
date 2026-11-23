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
    BASE_VERSION,
    GROUPS,
    favorite_ids,
    group_label,
    match_group,
    set_group,
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
    """Продукты пользователя. Переход на новую версию списка (BASE_VERSION): прежние отметки — продукты
    («куриные ножки», «сыр гауда») — отмечают свою группу (Курица, Сыр); сами прежние записи остаются скрытыми."""
    cid_s = str(cid)
    raw = store.get_list(config.FRIDGE_KEY, cid_s)
    items = _fridge_migrate(raw)
    if (store.get_profile(cid_s) or {}).get("fridge_base_v") != BASE_VERSION:
        marked = set(favorite_ids(items))
        for item in list(items):
            if not item.get("group") and item.get("on", True):
                gid = match_group(item["name"])
                if gid and gid not in marked:
                    items = set_group(items, gid, True)
                    marked.add(gid)
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


# ---------- Мои продукты: главный экран (категории) ----------
async def send_fridge(bot, cid, q=None, back="m_food"):
    """«Мои продукты»: категории единого списка; на кнопке — сколько групп в ней отмечено. Своих продуктов,
    добавления и удаления нет — только отметка."""
    marked = set(favorite_ids(_load_products(cid)))
    msg = food_ui.fridge_home(len(marked))
    rows = [[InlineKeyboardButton(
        f"{_CAT_BTN_LABEL[cat]} · {sum(gid in marked for gid, *_rest in GROUPS[cat])}",
        callback_data=f"as_fridge_cat_{ci}_0")] for ci, cat in enumerate(_CAT_ORDER)]
    rows.append([InlineKeyboardButton("📝 Выбрать предпочтения", callback_data="set_pref_cuisines")])
    rows.append(nav_row(back))
    await _show(bot, cid, msg, rows, q)


# ---------- Категория: пять групп, ✅ отмечено / □ ----------
async def send_fridge_cat(bot, cid, cat_idx: int, page: int = 0, q=None):
    if not 0 <= cat_idx < len(_CAT_ORDER):
        await send_fridge(bot, cid, q)
        return
    cat = _CAT_ORDER[cat_idx]
    marked = set(favorite_ids(_load_products(cid)))
    groups = GROUPS[cat]
    msg = food_ui.fridge_category(_CAT_BTN_LABEL[cat], len(groups), sum(gid in marked for gid, *_r in groups))
    rows = [[InlineKeyboardButton(f"{'✅' if gid in marked else '□'} {group_label(gid)}",
                                  callback_data=f"as_fridge_fav_{cat_idx}_{index}")]
            for index, (gid, *_rest) in enumerate(groups)]
    rows.append(nav_row("as_fridge_home"))
    await _show(bot, cid, msg, rows, q)


async def fridge_fav(bot, cid, cat_idx: int, index: int, q=None):
    """Нажатие на группу: ✅ ↔ □."""
    if 0 <= cat_idx < len(_CAT_ORDER) and 0 <= index < len(GROUPS[_CAT_ORDER[cat_idx]]):
        gid = GROUPS[_CAT_ORDER[cat_idx]][index][0]
        items = _load_products(cid)
        on = gid not in favorite_ids(items)
        store.set_list(config.FRIDGE_KEY, str(cid), _fridge_migrate(set_group(items, gid, on)))
    await send_fridge_cat(bot, cid, cat_idx, 0, q)


async def fridge_add_done(bot, cid, text, cat_idx: int = -1):
    """«Добавь в продукты: курица, креветки» — отмечает группы списка; чего в списке нет — «Не добавил: … — нет в
    списке продуктов»."""
    cid_s = str(cid)
    items = _load_products(cid)
    first_fill = not _fridge_available(items)
    marked = set(favorite_ids(items))
    added, duplicates, rejected = [], [], []
    for name in _fridge_split_input(text):
        gid = match_group(name)
        if not gid:
            rejected.append((name, "нет в списке продуктов"))
        elif gid in marked:
            duplicates.append(group_label(gid))
        elif gid not in {g for _c, g in added}:
            items = set_group(items, gid, True)
            marked.add(gid)
            added.append((GROUPS_CAT[gid], gid))
    store.set_list(config.FRIDGE_KEY, cid_s, _fridge_migrate(items))
    added_by_cat = {}
    for cat, gid in added:
        added_by_cat.setdefault(cat, []).append(group_label(gid))
    rejected = [*rejected, *_fridge_rejected_lines(text)]
    if (added or duplicates or rejected) and not first_fill:
        msg = food_ui.fridge_updated(
            added_by_cat, [group_label(gid) for _cat, gid in added], duplicates, rejected, _CAT_ORDER, _CAT_EMOJI,
            _CAT_BTN_LABEL)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    if first_fill and _fridge_available(items):
        await _send_cooking_home(bot, cid)
        return
    await send_fridge(bot, cid)


GROUPS_CAT = {gid: cat for cat, groups in GROUPS.items() for gid, *_rest in groups}


async def _send_cooking_home(bot, cid, q=None):
    """После первых моих продуктов сразу показываем рецепт дня."""
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
