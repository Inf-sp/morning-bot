"""Холодильник: список продуктов, категории и приготовление из остатков."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
import cooking
from fridge_model import (
    FRIDGE_MIN_CAT as _FRIDGE_MIN_CAT,
    _CAT_BTN_LABEL,
    _CAT_EMOJI,
    _CAT_ORDER,
    _FRIDGE_FALLBACK_TARGET,
    _fridge_available,
    _fridge_cat,
    _fridge_migrate,
    _fridge_normalize_input,
    _fridge_rejected_lines,
    _fridge_split_input,
)
from ui import food as food_ui
from ui.constants import delete_label

send_leftovers = cooking.send_leftovers

_FRIDGE_PAGE = 8  # продуктов на страницу в категории


def _fridge_by_cat(items: list) -> dict:
    """Словарь cat → [(global_idx, item)] для отображения."""
    by_cat: dict = {}
    for i, it in enumerate(items):
        cat = it.get("cat", "прочее")
        by_cat.setdefault(cat, []).append((i, it))
    return by_cat


def _fridge_by_cat_display(items: list) -> dict:
    """Категории для UI; маленькие группы объединяются в «прочее»."""
    by_cat = _fridge_by_cat(items)
    result = {category: [] for category in _CAT_ORDER}
    for category in _CAT_ORDER:
        for global_index, item in by_cat.get(category, []):
            target = (category if len(by_cat.get(category, [])) >= _FRIDGE_MIN_CAT
                      or category == "прочее"
                      else _FRIDGE_FALLBACK_TARGET.get(category, "прочее"))
            result[target].append((global_index, item))
    return {category: sorted(values, key=lambda value: value[1].get("name", "").casefold())
            for category, values in result.items() if values}


# ---------- Мой холодильник: главный экран (категории) ----------
async def send_fridge(bot, cid, q=None, back="m_food"):
    cid_s = str(cid)
    raw = store.get_list(config.FRIDGE_KEY, cid_s)
    items = _fridge_migrate(raw)
    if items != raw:
        store.set_list(config.FRIDGE_KEY, cid_s, items)

    if not items:
        msg = food_ui.fridge_home_empty()
        rows = [
            [InlineKeyboardButton("🆕 Добавить продукт", callback_data="as_fridge_add")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
        ]
    else:
        available = sum(1 for it in items if it.get("on", True))
        by_cat = _fridge_by_cat_display(items)
        msg = food_ui.fridge_home(len(items), available)
        present_cats = [c for c in _CAT_ORDER if c in by_cat]
        cat_btns = []
        for ci, cat in enumerate(present_cats):
            cat_items = by_cat[cat]
            on_cnt = sum(1 for _, it in cat_items if it.get("on", True))
            label = _CAT_BTN_LABEL.get(cat, cat.capitalize())
            cat_btns.append(InlineKeyboardButton(
                f"{label} {on_cnt}/{len(cat_items)}",
                callback_data=f"as_fridge_cat_{ci}_0"
            ))
        rows = [[InlineKeyboardButton("🆕 Добавить продукт", callback_data="as_fridge_add")]]
        rows.append([InlineKeyboardButton(delete_label("Удалить продукты"), callback_data="as_fridge_clean")])
        rows.extend([[btn] for btn in cat_btns])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])

    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ---------- Экран категории (пагинация + toggle + отдельная чистка) ----------
async def send_fridge_cat(bot, cid, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    by_cat = _fridge_by_cat_display(items)

    # Определяем имя категории по индексу в present_cats (с учётом мержа малых)
    present_cats = [c for c in _CAT_ORDER if c in by_cat]
    if cat_idx >= len(present_cats):
        await send_fridge(bot, cid, q); return
    cat = present_cats[cat_idx]
    cat_items = by_cat[cat]  # [(global_idx, item)]

    total = len(cat_items)
    pages = max(1, (total + _FRIDGE_PAGE - 1) // _FRIDGE_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = cat_items[page * _FRIDGE_PAGE:(page + 1) * _FRIDGE_PAGE]

    on_cnt = sum(1 for _, it in cat_items if it.get("on", True))
    msg = food_ui.fridge_category("", cat.capitalize(), total, on_cnt)

    # Один продукт в строку: названия должны читаться полностью.
    rows = [[InlineKeyboardButton("🆕 Добавить продукт", callback_data=f"as_fridge_add_{cat_idx}")]]
    rows.append([InlineKeyboardButton(delete_label("Удалить продукты"), callback_data="as_fridge_clean")])
    for gi, it in chunk:
        mark = "✅" if it.get("on", True) else "□"
        name_short = it["name"][:40]
        rows.append([
            InlineKeyboardButton(f"{mark} {name_short}", callback_data=f"as_fridge_tgl_{gi}_{cat_idx}_{page}")
        ])

    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"as_fridge_cat_{cat_idx}_{(page-1) % pages}"),
            InlineKeyboardButton(f"{page+1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"as_fridge_cat_{cat_idx}_{(page+1) % pages}"),
        ])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_fridge_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])

    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def fridge_add_done(bot, cid, text, cat_idx: int = -1):
    cid_s = str(cid)
    items_new = _fridge_split_input(text)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    existing = {it["name"].lower() for it in items}
    added = []
    duplicates = []
    for name in items_new:
        key = name.lower()
        if name and key not in existing:
            cat = _fridge_cat(name)
            items.append({"name": name, "cat": cat, "on": True})
            existing.add(key)
            added.append(name)
        elif name:
            duplicates.append(name)
    store.set_list(config.FRIDGE_KEY, cid_s, items)
    added_by_cat = {}
    for name in added:
        added_by_cat.setdefault(_fridge_cat(name), []).append(name)
    rejected = _fridge_rejected_lines(text)
    msg = food_ui.fridge_updated(added_by_cat, added, duplicates, rejected, _CAT_ORDER, _CAT_EMOJI, _CAT_BTN_LABEL)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    if cat_idx >= 0:
        await send_fridge_cat(bot, cid, cat_idx, 0)
    else:
        await send_fridge(bot, cid)


def _fridge_payload_from_chat(text: str) -> str:
    raw = str(text or "").strip()
    low = raw.lower()
    if "<li" in low and "продукт" in low:
        return _fridge_normalize_input(raw)

    patterns = [
        r"(?:добавь|добавить|закинь|запиши|сохрани)\s+"
        r"(?:это\s+)?(?:в\s+)?(?:список\s+)?(?:моих\s+)?"
        r"(?:продуктов|продукты|холодильник)\s*[:\-—]?\s*(.+)",
        r"(?:в\s+)?(?:продукты|холодильник)\s*[:\-—]\s*(.+)",
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


async def fridge_toggle(bot, cid, idx: int, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    if 0 <= idx < len(items):
        items[idx]["on"] = not items[idx].get("on", True)
        store.set_list(config.FRIDGE_KEY, cid_s, items)
    await send_fridge_cat(bot, cid, cat_idx, page, q)


async def fridge_del(bot, cid, idx: int, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    if 0 <= idx < len(items):
        items.pop(idx)
        store.set_list(config.FRIDGE_KEY, cid_s, items)
    await send_fridge_cat(bot, cid, cat_idx, page, q)


async def send_fridge_recipe(bot, cid):
    raw = store.get_list(config.FRIDGE_KEY, str(cid))
    available = _fridge_available(raw)
    if not available:
        msg = food_ui.fridge_empty_for_recipe()
        await bot.send_message(chat_id=cid, text=msg.text)
        return
    await send_leftovers(bot, cid, ", ".join(available))
