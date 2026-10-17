"""Closet management flows."""

import logging
import re

import ai
import config
import secure
import store
import verify
import wardrobe as _wardrobe
from ui import wardrobe as wardrobe_ui
from ui.constants import delete_label
from wardrobe_model import flat_items as _flat_wardrobe_items
from wardrobe_model import is_clothing, normalize_parsed_item, public_item_name

_ONLY_CLOTHING = ("В шкаф добавляю только одежду и обувь. Аксессуары подскажу в образе по погоде: "
                  "очки в солнце, зонт в дождь, шапку и перчатки в мороз.")

_log = logging.getLogger(__name__)


def get_wardrobe_gaps(cid):
    return store.get_list(config.WARDROBE_GAPS_KEY, cid)


def add_wardrobe_gap(cid, item, reason, priority=True):
    """Добавляет пробел гардероба без дублей (по item, case-insensitive)."""
    gaps = store.get_list(config.WARDROBE_GAPS_KEY, cid)
    if any(g.get("item", "").lower() == item.lower() for g in gaps):
        return False
    gaps.append({"item": item, "reason": reason, "priority": bool(priority)})
    store.set_list(config.WARDROBE_GAPS_KEY, cid, gaps)
    return True


def _local_text_item(text):
    """Безопасный минимальный разбор одной знакомой вещи без внешнего AI.

    Нужен только как запасной путь, когда модель недоступна либо вернула пустой
    JSON: пользователь всё равно может добавить понятную вещь и не теряет ввод.
    Не угадываем неизвестные предметы и не разбиваем потенциальный список.
    """
    name = re.sub(r"\s+", " ", str(text or "")).strip(" ,;.-")
    if not name or len(name) > 160 or re.search(r"[,;\n]", name):
        return None
    item = normalize_parsed_item({
        "name": name,
        "style": "Повседневный",
        "_source_text": name,
    })
    if not item or item.get("zone") == "Другое":
        return None
    return item


async def _parse_items(text):
    try:
        parsed = await ai.allm_json(
            f"Разбери вещи по атрибутам. Зоны и подкатегории (используй ТОЛЬКО эти значения, "
            f"если не подходит ни одна — subcategory=\"Другое\"): {_wardrobe._ZONES_DESC}\n"
            f"Вещи:\n{secure.wrap_untrusted(text, 'список вещей')}\n"
            "Для каждой вещи верни: zone (одна из зон выше, если не ясно — \"Другое\"), "
            "subcategory (строго из списка для этой зоны), name (естественное русское название: цвет перед "
            "типом вещи, затем детали; пример: «Тёмно-оливковые брюки с карманами», но БЕЗ слов "
            "лёгкая/тонкая/тёплая/толстая/плотная/летняя/зимняя — это отдельные поля), "
            "color (основной цвет), color_secondary (доп. цвет или пусто), material (материал или пусто), "
            "length (длина или пусто), warmth (СТРОГО лёгкие/обычные/тёплые; толстая/плотная/утеплённая = тёплые), "
            "fit (свободная/прямая/приталенная или пусто), season (массив сезонов), rain_ok, wind_ok, "
            "occasions (массив подходящих случаев), style (Casual/Formal/Sport/Streetwear и т.п. или пусто). "
            "Сохраняй бренд, если он указан.\n"
            'JSON: {"items": [{"zone":"","subcategory":"","name":"","brand":"","color":"","color_secondary":"",'
            '"material":"","length":"","warmth":"обычные","fit":"","season":[],"rain_ok":false,"wind_ok":false,'
            '"occasions":[],"style":""}]}',
            1100, tier="cheap", module="wardrobe_utility")
        raw_items = parsed.get("items") or []
        source_text = text if len(raw_items) == 1 else ""
        items = [normalize_parsed_item({**item, "_source_text": source_text}) for item in raw_items]
        items = [item for item in items if item]
        if items:
            return items
    except Exception:
        _log.info("Не удалось разобрать вещь через AI; пробуем локальный разбор")

    item = _local_text_item(text)
    return [item] if item else []


async def _show_added_items(bot, cid, items):
    if not items:
        await bot.send_message(chat_id=cid, text="Такая вещь уже есть в шкафу.", reply_markup=_wardrobe.closet_kb())
        return
    msg = wardrobe_ui.add_success(items[0]) if len(items) == 1 else wardrobe_ui.add_batch_success(items)
    if len(items) == 1:
        rows = [[(delete_label("Удалить"), f"w_delete_{items[0]['id']}")],
                [("Собрать образ", f"w_with_{items[0]['id']}")]]  # образ дня с новой вещью, как из карточки вещи
    else:
        rows = [[(delete_label(f"Удалить: {public_item_name(item)[:28]}"), f"w_delete_{item['id']}")]
                for item in items]
    rows.append([("🎚️ Настроить шкаф", "w_closet")])  # синяя: категории «Моего шкафа»
    rows.append([("⬅️ Назад", "w_closet"), ("#️⃣ Главная", "m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_wardrobe._kb(rows))

async def add_item(bot, cid, text, *, return_to_home=False):
    try:
        items = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not items:
        await bot.send_message(chat_id=cid, text="Не удалось распознать вещь. Опиши её одним сообщением.", reply_markup=_wardrobe._back_kb())
        return
    if not any(is_clothing(item) for item in items):
        await bot.send_message(chat_id=cid, text=_ONLY_CLOTHING, reply_markup=_wardrobe._back_kb())
        return
    saved = store.add_wardrobe_items(cid, items)
    if return_to_home and saved:
        await _wardrobe.send_home(bot, cid)
        return
    await _show_added_items(bot, cid, saved)

async def add_item_settings(bot, cid, text):
    await add_item(bot, cid, text)


async def add_item_photo(bot, cid, image_bytes, mime_type="image/jpeg", caption=""):
    try:
        parsed = await ai.allm_image_json(
            image_bytes,
            mime_type,
            f"""Распознай только предметы одежды и обувь на фото. Подпись пользователя: {secure.wrap_untrusted(caption, 'подпись')}
Зоны и подкатегории: {_wardrobe._ZONES_DESC}
Для каждого отчётливо видимого предмета верни zone, subcategory, name, brand, color, color_secondary,
material, length, warmth (строго лёгкие/обычные/тёплые), fit, season, rain_ok, wind_ok,
occasions и style. Физические свойства храни полями, не добавляй их в name.
Не выдумывай бренд и невидимые физические свойства.
JSON: {{"items":[{{"zone":"","subcategory":"","name":"","brand":"","color":"","color_secondary":"","material":"","length":"","warmth":"обычные","fit":"","season":[],"rain_ok":false,"wind_ok":false,"occasions":[],"style":""}}]}}""",
            max_tokens=1100,
        )
        raw_items = parsed.get("items") or []
        source_text = caption if len(raw_items) == 1 else ""
        items = [normalize_parsed_item({**item, "_source_text": source_text}) for item in raw_items]
        items = [item for item in items if item]
    except Exception as e:
        store.pending_input[str(cid)] = "wardrobe_add"
        await verify.safe_error(bot, cid, e, back="m_wardrobe")
        return
    if not items:
        store.pending_input[str(cid)] = "wardrobe_add"
        await bot.send_message(chat_id=cid, text="Не удалось уверенно распознать вещь. Опиши её одним сообщением.", reply_markup=_wardrobe._back_kb())
        return
    if not any(is_clothing(item) for item in items):
        await bot.send_message(chat_id=cid, text=_ONLY_CLOTHING, reply_markup=_wardrobe._back_kb())
        return
    saved = store.add_wardrobe_items(cid, items)
    await _show_added_items(bot, cid, saved)


def _find_item(cid, item_id):
    for zone, subcat, item in _flat_wardrobe_items(store.load_wardrobe(cid)):
        if item.get("id") == item_id:
            return zone, subcat, item
    return None, None, None


def _replace_item(cid, item_id, replacement):
    changed = {"ok": False}

    def _mut(w):
        for zone, subcats in w.get("zones", {}).items():
            for subcat, items in subcats.items():
                for index, item in enumerate(list(items)):
                    if item.get("id") != item_id:
                        continue
                    items.pop(index)
                    new_item = dict(replacement)
                    new_item["id"] = item_id
                    target = w.setdefault("zones", {}).setdefault(new_item["zone"], {}).setdefault(new_item["subcategory"], [])
                    target.append(new_item)
                    changed["ok"] = True
                    return

    store.mutate_wardrobe(cid, _mut)
    return changed["ok"]


async def edit_item_text(bot, cid, text):
    item_id = store.wardrobe_edit_item.pop(str(cid), None)
    if not item_id:
        await _wardrobe.send_wardrobe_zones(bot, cid)
        return
    try:
        parsed = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not parsed or not _replace_item(cid, item_id, parsed[0]):
        await bot.send_message(
            chat_id=cid,
            text="Не удалось изменить вещь. Открой карточку и попробуй ещё раз.",
            reply_markup=_wardrobe._back_kb(),
        )
        return
    await _wardrobe.send_item_card(bot, cid, item_id)


async def edit_add_preview(bot, cid, text):
    store.wardrobe_add_queue.pop(str(cid), None)
    try:
        parsed = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not parsed:
        await bot.send_message(
            chat_id=cid, text="Не удалось распознать исправление.",
            reply_markup=_wardrobe._back_kb())
        return
    saved = store.add_wardrobe_items(cid, parsed)
    await _show_added_items(bot, cid, saved)


async def handle_wardrobe_search(bot, cid, query):
    """Ищет обычным текстом по названию, бренду, цвету, категории и сезону."""
    query_norm = re.sub(r"\s+", " ", (query or "").strip()).casefold()
    if not query_norm:
        await bot.send_message(chat_id=cid, text="Пришли название вещи или часть названия.")
        return
    w = store.load_wardrobe(cid)
    aliases = {"летняя": "лето", "летний": "лето", "зимняя": "зима", "зимний": "зима"}
    terms = [aliases.get(term, term) for term in query_norm.split()]
    matches = []
    for zone, subcat, item in _flat_wardrobe_items(w):
        values = [item.get("name"), zone, subcat, item.get("color"), item.get("material"), item.get("style")]
        values.extend(item.get("season") or [])
        haystack = " ".join(str(value or "") for value in values).casefold()
        if all(term in haystack for term in terms):
            matches.append(item)
    if not matches:
        await bot.send_message(
            chat_id=cid, text="Ничего не нашлось. Попробуй цвет, бренд или категорию.",
            reply_markup=_wardrobe._kb([[("⬅️ Назад", "w_closet"), ("#️⃣ Главная", "m_menu")]]),
        )
        return
    msg = wardrobe_ui.search_results(query, matches)
    rows = [[(str(item.get("name") or "Вещь")[:48], f"w_item_{item.get('id')}")] for item in matches[:10]]
    rows.append([("⬅️ Назад", "w_closet"), ("#️⃣ Главная", "m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_wardrobe._kb(rows))

# ---------- шкаф, категории и карточки вещей ----------


# ---------- добавление файлом (старый режим, оставлен) ----------
