"""Холодильник: список продуктов, категории и приготовление из остатков."""

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
    _fridge_detect_cat,
    _fridge_migrate,
    _fridge_normalize_input,
    _fridge_rejected_lines,
    _fridge_split_input,
)
from ui import food as food_ui

send_leftovers = cooking.send_leftovers

_FRIDGE_PAGE = 8  # продуктов на страницу в категории
_pending_category_choices: dict[str, dict] = {}


def _mark_transient_edit(bot, cid, message):
    marker = getattr(bot, "mark_transient_message", None)
    if marker is not None:
        marker(cid, getattr(message, "message_id", None))


def _fridge_by_cat(items: list) -> dict:
    """Словарь cat → [(global_idx, item)] для отображения."""
    by_cat: dict = {category: [] for category in _CAT_ORDER}
    for i, it in enumerate(items):
        cat = it.get("cat")
        if cat in by_cat:
            by_cat[cat].append((i, it))
    return by_cat


def _fridge_by_cat_display(items: list) -> dict:
    """Все шесть категорий в стабильном порядке, включая пустые."""
    by_cat = _fridge_by_cat(items)
    return {
        category: sorted(values, key=lambda value: value[1].get("name", "").casefold())
        for category, values in by_cat.items()
    }


# ---------- Мой холодильник: главный экран (категории) ----------
async def send_fridge(bot, cid, q=None, back="m_food"):
    cid_s = str(cid)
    raw = store.get_list(config.FRIDGE_KEY, cid_s)
    items = _fridge_migrate(raw)
    if items != raw:
        store.set_list(config.FRIDGE_KEY, cid_s, items)

    available = sum(1 for it in items if it.get("on", True))
    by_cat = _fridge_by_cat_display(items)
    msg = food_ui.fridge_home(available)
    rows = [[InlineKeyboardButton("🆕 Добавить продукт", callback_data="as_fridge_add")]]
    for ci, cat in enumerate(_CAT_ORDER):
        cat_items = by_cat[cat]
        on_cnt = sum(1 for _, it in cat_items if it.get("on", True))
        label = _CAT_BTN_LABEL[cat]
        rows.append([InlineKeyboardButton(
            f"{label} · {on_cnt}",
            callback_data=f"as_fridge_cat_{ci}_0",
        )])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])

    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=kb, transient=True)


# ---------- Экран категории (пагинация + переключение наличия) ----------
async def send_fridge_cat(bot, cid, cat_idx: int, page: int, q=None):
    cid_s = str(cid)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    by_cat = _fridge_by_cat_display(items)

    if not 0 <= cat_idx < len(_CAT_ORDER):
        await send_fridge(bot, cid, q)
        return
    cat = _CAT_ORDER[cat_idx]
    cat_items = by_cat[cat]  # [(global_idx, item)]

    total = len(cat_items)
    pages = max(1, (total + _FRIDGE_PAGE - 1) // _FRIDGE_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = cat_items[page * _FRIDGE_PAGE:(page + 1) * _FRIDGE_PAGE]

    on_cnt = sum(1 for _, it in cat_items if it.get("on", True))
    msg = food_ui.fridge_category(_CAT_BTN_LABEL[cat], total, on_cnt)

    # Один продукт в строку: названия должны читаться полностью.
    rows = []
    for gi, it in chunk:
        mark = "✅" if it.get("on", True) else "□"
        name_short = it["name"][:40]
        rows.append([
            InlineKeyboardButton(f"{mark} {name_short}", callback_data=f"as_fridge_tgl_{gi}_{cat_idx}_{page}")
        ])

    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"as_fridge_cat_{cat_idx}_{(page-1) % pages}"),
            InlineKeyboardButton(f"{page + 1} / {pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"as_fridge_cat_{cat_idx}_{(page+1) % pages}"),
        ])
    if cat_items:
        rows.append([InlineKeyboardButton(
            "✏️ Изменить",
            callback_data=f"as_fridge_clean_{cat_idx}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_fridge_home"), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])

    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=kb, transient=True)


async def fridge_add_done(bot, cid, text, cat_idx: int = -1):
    cid_s = str(cid)
    items_new = _fridge_split_input(text)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    existing = {it["name"].lower() for it in items}
    added = []
    duplicates = []
    needs_category = []
    selected_category = _CAT_ORDER[cat_idx] if 0 <= cat_idx < len(_CAT_ORDER) else None
    for name in items_new:
        key = name.lower()
        if name and key not in existing:
            detected = _fridge_detect_cat(name)
            category = detected or selected_category
            if category is None:
                needs_category.append(name)
            else:
                items.append({
                    "name": name,
                    "cat": category,
                    "on": True,
                    **({"cat_manual": True} if detected is None else {}),
                })
                existing.add(key)
                added.append(name)
        elif name:
            duplicates.append(name)
    items = _fridge_migrate(items)
    store.set_list(config.FRIDGE_KEY, cid_s, items)
    added_by_cat = {}
    for item in items:
        if item["name"] in added:
            added_by_cat.setdefault(item["cat"], []).append(item["name"])
    rejected = _fridge_rejected_lines(text)
    if added or duplicates or rejected:
        msg = food_ui.fridge_updated(
            added_by_cat, added, duplicates, rejected, _CAT_ORDER, _CAT_EMOJI, _CAT_BTN_LABEL)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    if needs_category:
        _pending_category_choices[cid_s] = {"names": needs_category, "return_cat": cat_idx}
        await send_fridge_category_choice(bot, cid)
        return
    if cat_idx >= 0:
        await send_fridge_cat(bot, cid, cat_idx, 0)
    else:
        await send_fridge(bot, cid)


async def send_fridge_category_choice(bot, cid, q=None):
    """Просит выбрать одну из шести категорий для нераспознанного продукта."""
    pending = _pending_category_choices.get(str(cid)) or {}
    names = pending.get("names") or []
    if not names:
        await send_fridge(bot, cid, q)
        return
    msg = food_ui.fridge_category_choice(names[0])
    rows = [
        [InlineKeyboardButton(_CAT_BTN_LABEL[cat], callback_data=f"as_fridge_pick_{index}")]
        for index, cat in enumerate(_CAT_ORDER)
    ]
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="as_fridge_home"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb, transient=True)


async def fridge_assign_category(bot, cid, cat_idx: int, q=None):
    """Сохраняет выбранную категорию и продолжает очередь уточнений."""
    cid_s = str(cid)
    pending = _pending_category_choices.get(cid_s) or {}
    names = pending.get("names") or []
    if not names or not 0 <= cat_idx < len(_CAT_ORDER):
        await send_fridge(bot, cid, q)
        return
    name = names.pop(0)
    items = _fridge_migrate(store.get_list(config.FRIDGE_KEY, cid_s))
    if name.casefold() not in {item["name"].casefold() for item in items}:
        items.append({
            "name": name,
            "cat": _CAT_ORDER[cat_idx],
            "cat_manual": True,
            "on": True,
        })
        store.set_list(config.FRIDGE_KEY, cid_s, _fridge_migrate(items))
    if names:
        pending["names"] = names
        await send_fridge_category_choice(bot, cid, q)
        return
    _pending_category_choices.pop(cid_s, None)
    return_cat = pending.get("return_cat", -1)
    if isinstance(return_cat, int) and 0 <= return_cat < len(_CAT_ORDER):
        await send_fridge_cat(bot, cid, return_cat, 0, q)
    else:
        await send_fridge(bot, cid, q)


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
