"""Closet management and purchase evaluation flows."""

import hashlib
import json

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wardrobe import (
        _PURCHASE_FLAGS, _PURCHASE_REJECT_REASONS, _PURCHASE_VERDICTS,
        _ZONES_DESC, _back_kb, _build_purchase_message,
        _build_purchase_recommendation_message, _build_purchase_suggestions_message, _clean_text,
        _flat_wardrobe_items, _get_cached_look, _kb, _log,
        _purchase_candidate, _settings, ai, closet_kb, config,
        delete_label, has_wardrobe_items, normalize_parsed_item,
        public_item_name, re, rotation, secure, send_home, send_item_card,
        send_wardrobe_zones, store, verify, wardrobe_stats, wardrobe_ui,
    )


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
            f"если не подходит ни одна — subcategory=\"Другое\"): {_ZONES_DESC}\n"
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
        await bot.send_message(chat_id=cid, text="Такая вещь уже есть в шкафу.", reply_markup=closet_kb())
        return
    msg = wardrobe_ui.add_success(items[0]) if len(items) == 1 else wardrobe_ui.add_batch_success(items)
    if len(items) == 1:
        rows = [[(delete_label("Удалить"), f"w_delete_{items[0]['id']}")]]
    else:
        rows = [[(delete_label(f"Удалить: {public_item_name(item)[:28]}"), f"w_delete_{item['id']}")]
                for item in items]
    rows.append([("⬅️ Назад", "w_closet"), ("#️⃣ Главная", "m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_kb(rows))

async def add_item(bot, cid, text, *, return_to_home=False):
    try:
        items = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not items:
        await bot.send_message(chat_id=cid, text="Не удалось распознать вещь. Опиши её одним сообщением.", reply_markup=_back_kb())
        return
    saved = store.add_wardrobe_items(cid, items)
    if return_to_home and saved:
        await send_home(bot, cid)
        return
    await _show_added_items(bot, cid, saved)

async def add_item_settings(bot, cid, text):
    await add_item(bot, cid, text)


async def add_item_photo(bot, cid, image_bytes, mime_type="image/jpeg", caption=""):
    try:
        parsed = await ai.allm_image_json(
            image_bytes,
            mime_type,
            f"""Распознай только предметы одежды и аксессуары на фото. Подпись пользователя: {secure.wrap_untrusted(caption, 'подпись')}
Зоны и подкатегории: {_ZONES_DESC}
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
        await bot.send_message(chat_id=cid, text="Не удалось уверенно распознать вещь. Опиши её одним сообщением.", reply_markup=_back_kb())
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
        await send_wardrobe_zones(bot, cid)
        return
    try:
        parsed = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not parsed or not _replace_item(cid, item_id, parsed[0]):
        await bot.send_message(
            chat_id=cid,
            text="Не удалось изменить вещь. Открой карточку и попробуй ещё раз.",
            reply_markup=_back_kb(),
        )
        return
    await send_item_card(bot, cid, item_id)


async def edit_add_preview(bot, cid, text):
    store.wardrobe_add_queue.pop(str(cid), None)
    try:
        parsed = await _parse_items(text)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_wardrobe"); return
    if not parsed:
        await bot.send_message(
            chat_id=cid, text="Не удалось распознать исправление.",
            reply_markup=_back_kb())
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
            reply_markup=_kb([[("⬅️ Назад", "w_closet"), ("#️⃣ Главная", "m_menu")]]),
        )
        return
    msg = wardrobe_ui.search_results(query, matches)
    rows = [[(str(item.get("name") or "Вещь")[:48], f"w_item_{item.get('id')}")] for item in matches[:10]]
    rows.append([("⬅️ Назад", "w_closet"), ("#️⃣ Главная", "m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_kb(rows))

# ---------- шкаф, категории и карточки вещей ----------


def _normalize_purchase_check(data, wardrobe=None):
    """Не пропускает неподдерживаемый вердикт и беспричинное «не брать»."""
    data = data if isinstance(data, dict) else {}
    verdict_key = _clean_text(data.get("verdict")).casefold().rstrip(".!?")
    verdict = _PURCHASE_VERDICTS.get(verdict_key, "недостаточно данных")

    flag_values = {}
    for key in ("duplicates", "closes_gap"):
        value = _clean_text(data.get(key)).casefold().rstrip(".!?")
        flag_values[key] = value if value in _PURCHASE_FLAGS else "недостаточно данных"

    try:
        if isinstance(data.get("fits_count"), bool):
            raise ValueError
        fits_count = int(data.get("fits_count"))
        if fits_count < 0:
            raise ValueError
    except (TypeError, ValueError):
        fits_count = "недостаточно данных"
    if wardrobe is not None and isinstance(fits_count, int):
        total, _counts = wardrobe_stats(wardrobe)
        fits_count = min(fits_count, total)

    why = data.get("why")
    if isinstance(why, list):
        why = why[0] if why else ""
    why = _clean_text(why)
    reject_reason = _clean_text(data.get("not_buy_reason")).casefold()
    if verdict == "не брать" and (reject_reason not in _PURCHASE_REJECT_REASONS or not why):
        verdict = "недостаточно данных"
        why = "Нет подтверждённой конкретной причины отказываться от покупки. Нужны дополнительные данные о вещи."
    elif verdict == "недостаточно данных" and not why:
        why = "Не хватает подтверждённых свойств вещи, которые влияют на решение."

    wear_with = data.get("wear_with")
    if not isinstance(wear_with, list):
        wear_with = []
    wear_with = [_clean_text(value) for value in wear_with if _clean_text(value)][:3]

    return {
        "verdict": verdict,
        "fits_count": fits_count,
        "duplicates": flag_values["duplicates"],
        "closes_gap": flag_values["closes_gap"],
        "why": why,
        "wear_with": wear_with,
    }


async def check_purchase(bot, cid, text):
    w = store.load_wardrobe(cid)
    prefs = _settings.wardrobe_prefs_context(cid)
    prefs_ctx = f"{secure.wrap_untrusted(prefs, 'предпочтения')}\n" if prefs else ""
    wardrobe_ctx = secure.wrap_untrusted(store.wardrobe_to_text(w), "гардероб")
    prompt = f"""Ты честный стилист-аналитик. Пользователь думает купить: {secure.wrap_untrusted(text, 'покупка')}
{prefs_ctx}
Гардероб пользователя:
{wardrobe_ctx}
Ответь на один вопрос: есть ли смысл добавлять эту вещь в гардероб пользователя?

Правила:
1. Вердикт — строго один из четырёх: «брать», «брать только со скидкой», «не брать», «недостаточно данных».
2. Если из описания нельзя подтвердить важные для решения свойства (например длину, крой, материал, сезонность, состояние или цену), выбери «недостаточно данных». Не додумывай их.
3. «Не брать» разрешено только при одной конкретной подтверждённой причине: почти полный дубль; неподходящая посадка; цвет прямо указан в запретах пользователя; сочетаемость лишь с одной-двумя позициями; неподходящие материал или сезонность; завышенная цена относительно пользы; плохое состояние.
4. Нельзя писать «не соответствует стилю» без конкретного объяснения из фактов выше. Общего несовпадения со стилем недостаточно для вердикта «не брать».
5. Посчитай, со сколькими конкретными вещами из шкафа покупка сочетается. Не считай саму покупку и не выдумывай отсутствующие вещи.
6. Дублирование и закрытие пробела обозначь только как «да», «нет» или «недостаточно данных».
7. В why дай одно конкретное компактное объяснение, максимум два предложения. Для «недостаточно данных» назови недостающие свойства. Для «не брать» объясни подтверждённую причину.
8. В wear_with дай до трёх готовых сочетаний только с реальными вещами из шкафа. При нехватке данных можно дать условное сочетание, но явно назвать условие. Если честного сочетания нет — верни пустой список.

Верни JSON (без markdown):
{{"verdict":"брать / брать только со скидкой / не брать / недостаточно данных","fits_count":0,"duplicates":"да / нет / недостаточно данных","closes_gap":"да / нет / недостаточно данных","not_buy_reason":"duplicate / fit / forbidden_color / low_compatibility / material_or_season / price_vs_utility / poor_condition / пустая строка","why":"одно конкретное объяснение","wear_with":["до трёх готовых сочетаний"]}}

Если гардероб пустой, fits_count должен быть 0, а вывод не должен притворяться точным."""
    try:
        d = await ai.allm_json(
            prompt, 600, tier="smart", module="wardrobe",
            cache_context={
                "scenario": "wardrobe_purchase_check",
                "item": text,
                "wardrobe": w,
                "preferences": prefs,
                "language": "ru",
                "profile_version": 1,
                "schema_version": 1,
            },
        )
    except Exception:
        d = {
            "verdict": "недостаточно данных",
            "fits_count": "недостаточно данных",
            "duplicates": "недостаточно данных",
            "closes_gap": "недостаточно данных",
            "why": "Не хватило данных о вещи для честной оценки. Попробуй указать цвет, материал и крой.",
            "wear_with": [],
        }
    text_out, entities = _build_purchase_message(_normalize_purchase_check(d, wardrobe=w))
    store.last_source[str(cid)] = "Гардероб · Покупка"
    store.last_answer[str(cid)] = text_out
    await bot.send_message(chat_id=cid, text=text_out, entities=entities,
        reply_markup=_kb([[("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")]]))


def _purchase_hub_kb():
    return _kb([
        [("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")],
    ])


def _purchase_result_kb():
    return _kb([
        [("✨ Подобрать другую вещь", "w_buy_gap")],
        [("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")],
    ])


async def send_purchase_hub(bot, cid):
    """Совместимость со старыми маршрутами: сразу запускает подбор покупки."""
    await recommend_missing_purchase(bot, cid)


def _missing_purchase_candidates(cid, wardrobe, *, exclude_names=None):
    """Берёт актуальный пробел из образа и добирает ещё две полезные покупки."""
    cached = _get_cached_look(cid) or {}
    recommended = (cached.get("look_data") or {}).get("purchase_recommendation") or {}
    primary = recommended if (
        _clean_text(recommended.get("item")) and _clean_text(recommended.get("reason"))
        and recommended.get("version") == PURCHASE_RECOMMENDATION_VERSION
    ) else None
    candidates = _purchase_candidates(
        wardrobe, {}, _settings.wardrobe_styles(cid), primary=primary, limit=3,
        exclude_names=exclude_names,
    )
    enriched = [_purchase_card_details(candidate, wardrobe) for candidate in candidates]
    return sorted(
        enriched,
        key=lambda candidate: (
            int(candidate.get("priority") or 0),
            int(candidate.get("combinations_count") or 0),
        ),
        reverse=True,
    )


def _purchase_photo_audience(cid):
    """Определяет тип фотопоиска только при достаточно надёжном сигнале профиля."""
    if config.CHAT_ID and str(cid) == str(config.CHAT_ID):
        return "male"
    profile = store.get_profile(cid) or {}
    explicit = _clean_text(profile.get("gender")).casefold()
    if explicit in {"male", "man", "m", "мужской", "мужчина"}:
        return "male"
    if explicit in {"female", "woman", "f", "женский", "женщина"}:
        return "female"
    name = _clean_text(profile.get("name")).casefold().split(" ", 1)[0]
    male_names = {
        "vladimir", "владимир", "alexander", "александр", "alexey", "алексей",
        "andrey", "андрей", "anton", "антон", "dmitry", "дмитрий", "ivan", "иван",
        "maxim", "максим", "mikhail", "михаил", "nikita", "никита", "oleg", "олег",
        "pavel", "павел", "sergey", "сергей", "yuri", "юрий", "denis", "денис",
    }
    return "male" if name in male_names else "neutral"


def _purchase_card_details(item, wardrobe):
    """Дополняет GAP-рекомендацию реальными сочетаниями из шкафа."""
    item = dict(item or {})
    by_zone = {}
    for zone, _subcategory, entry in _flat_wardrobe_items(wardrobe):
        name = _clean_text(public_item_name(entry))
        if name:
            by_zone.setdefault(_clean_text(zone), []).append(name)

    category = _clean_text(item.get("category")).casefold()
    if category in {"низ", "брюки", "джинсы"}:
        first, second = by_zone.get("Верх", []), by_zone.get("Обувь", [])
        tip = "Выбирай посадку, которая не спорит с объёмом твоего верха."
    elif category == "обувь":
        first, second = by_zone.get("Верх", []), by_zone.get("Низ", [])
        tip = "Проверь посадку и материал: пара должна подходить для твоей обычной погоды."
    else:
        first, second = by_zone.get("Верх", []), by_zone.get("Низ", [])
        if not first:
            first = by_zone.get("Платья", [])
        tip = "Выбирай свободную посадку, чтобы вещь легко работала вторым слоем."

    combinations = [(left, right) for left in first for right in second if left != right]
    item["outfits"] = [f"{left} + {right}" for left, right in combinations[:3]]
    item["combinations_count"] = len(combinations)
    item["gap_reason"] = _clean_text(item.get("reason"))
    item["choice_tip"] = tip
    return item


def _purchase_carousel_kb(page, count):
    rows = [[("✨ Обновить", f"w_buy_new:{page}")]]
    rows.append([("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")])
    return _kb(rows)


def _purchase_carousel_signature(cid, wardrobe):
    return hashlib.sha256(json.dumps({
        "wardrobe": wardrobe,
        "styles": _settings.wardrobe_styles(cid),
    }, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _purchase_carousel_candidates(cid, wardrobe, *, reset=False, exclude_names=None):
    """Фиксирует вещи карусели для стабильного листания рекомендаций."""
    signature = _purchase_carousel_signature(cid, wardrobe)
    profile = store.get_profile(cid) or {}
    cached = profile.get("wardrobe_purchase_carousel") or {}
    if (not reset and cached.get("signature") == signature
            and isinstance(cached.get("items"), list) and cached["items"]):
        return [dict(item) for item in cached["items"] if isinstance(item, dict)]
    name_key = lambda value: _clean_text(value).casefold()
    explicit_excluded = rotation.markers(exclude_names or [], key=name_key)
    rejected = profile.get("wardrobe_purchase_rejections") or {}
    rejected_names = rejected.get("items") or []
    rejected_markers = rotation.markers(rejected_names, key=name_key)
    shown = profile.get("wardrobe_purchase_seen") or {}
    shown_names = shown.get("items") or []
    excluded = set(explicit_excluded)
    excluded.update(rejected_markers)
    excluded.update(rotation.markers(shown_names, key=name_key))
    items = _missing_purchase_candidates(cid, wardrobe, exclude_names=excluded)
    cycle_reset = False
    if not items and shown_names:
        # После полного круга начинаем новый, но не возвращаем текущую карточку
        # первой и никогда не оживляем явно отклонённые варианты.
        cycle_excluded = set(explicit_excluded)
        cycle_excluded.update(rejected_markers)
        cycle_excluded.add(name_key(shown_names[-1]))
        items = _missing_purchase_candidates(
            cid, wardrobe, exclude_names=cycle_excluded,
        )
        cycle_reset = bool(items)
    if not items:
        reserves = [
            ("Универсальный верхний слой", "Верхняя одежда"),
            ("Фактурный трикотажный джемпер", "Верх"),
            ("Прямые брюки нейтрального цвета", "Низ"),
            ("Минималистичные кожаные кеды", "Обувь"),
            ("Компактная сумка на каждый день", "Аксессуары"),
        ]
        reserve_key = lambda value: name_key(value[0] if isinstance(value, tuple) else value)
        reserve_history = [*shown_names, *(exclude_names or [])]
        available = rotation.candidates_for_cycle(
            [item for item in reserves if reserve_key(item) not in rejected_markers],
            reserve_history,
            current=(reserve_history or [None])[-1], key=reserve_key,
        )
        if not available:
            # Все резервные варианты отклонены — сбрасываем rejected-фильтр
            # и выбираем следующий в цикле (без уже показанного последним).
            available = rotation.candidates_for_cycle(
                reserves, reserve_history,
                current=(reserve_history or [None])[-1], key=reserve_key,
            )
        if available:
            name, category = available[0]
            items = [{
                "item": name,
                "category": category,
                "style": "Базовый",
                "season": "Межсезонье",
                "reason": "добавит шкафу новый слой и увеличит число сочетаний с уже имеющимися вещами",
                "version": PURCHASE_RECOMMENDATION_VERSION,
            }]

    def change(current):
        if cycle_reset:
            current["wardrobe_purchase_seen"] = {"items": []}
        current["wardrobe_purchase_carousel"] = {
            "signature": signature,
            "items": [dict(item) for item in items],
            "seen_items": sorted(excluded),
        }
        return current, None

    store.mutate_profile(cid, change)
    return items


async def show_purchase_page(
        bot, cid, page=0, q=None, reset_candidates=False, exclude_names=None):
    wardrobe = store.load_wardrobe(cid)
    candidates = _purchase_carousel_candidates(
        cid, wardrobe, reset=reset_candidates, exclude_names=exclude_names,
    )
    if not candidates:
        # Полный сброс seen + rejections, чтобы экран всегда показывал рекомендацию
        def full_reset(current):
            current["wardrobe_purchase_seen"] = {"items": []}
            current["wardrobe_purchase_rejections"] = {"items": []}
            current.pop("wardrobe_purchase_carousel", None)
            return current, None
        store.mutate_profile(cid, full_reset)
        candidates = _purchase_carousel_candidates(cid, wardrobe, reset=True)
    if not candidates:
        return
    page = max(0, min(int(page), len(candidates) - 1))
    item = candidates[page]

    def remember_page(current):
        carousel = current.get("wardrobe_purchase_carousel") or {}
        if isinstance(carousel, dict):
            carousel = dict(carousel)
            carousel["page"] = page
            current["wardrobe_purchase_carousel"] = carousel
        history = current.get("wardrobe_purchase_seen") or {}
        current["wardrobe_purchase_seen"] = {
            "items": rotation.remember(
                history.get("items") or [], _clean_text(item.get("item")),
                limit=50, key=lambda value: _clean_text(value).casefold(),
            ),
        }
        return current, None

    store.mutate_profile(cid, remember_page)
    card_item = _purchase_card_details(item, wardrobe)
    if not card_item.get("product_url"):
        query = quote_plus(_clean_text(item.get("item")))
        card_item["product_url"] = f"https://www.google.com/search?tbm=shop&q={query}"
    text_out, entities = _build_purchase_recommendation_message(card_item)
    store.last_source[str(cid)] = "Гардероб · Что докупить"
    store.last_answer[str(cid)] = text_out
    kb = _purchase_carousel_kb(page, len(candidates))
    if q is not None:
        try:
            await q.edit_message_text(
                text=text_out, entities=entities, reply_markup=kb,
            )
            return
        except Exception:
            pass
    if q is not None:
        try:
            await q.delete_message()
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text_out, entities=entities, reply_markup=kb)


async def recommend_missing_purchase(bot, cid):
    """Показывает первую из трёх персональных покупок текстовой карточкой."""
    wardrobe = store.load_wardrobe(cid)
    if not has_wardrobe_items(cid):
        await bot.send_message(
            chat_id=cid,
            text="Сначала заполни шкаф — тогда я смогу понять, каких вещей не хватает именно тебе.",
            reply_markup=_kb([[("🆕 Заполнить шкаф", "w_fill")],
                              [("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")]]),
        )
        return
    store.pending_input[str(cid)] = "wardrobe_buy"
    await show_purchase_page(bot, cid, 0, reset_candidates=True)


async def recommend_another_purchase(bot, cid, q=None, page=None):
    """Запоминает отвергнутую карточку и подбирает новую без её возврата."""
    profile = store.get_profile(cid) or {}
    cached = profile.get("wardrobe_purchase_carousel") or {}
    items = [item for item in (cached.get("items") or []) if isinstance(item, dict)]
    try:
        selected_page = int(cached.get("page", 0) if page is None else page)
    except (TypeError, ValueError):
        selected_page = 0
    selected_page = max(0, min(selected_page, len(items) - 1)) if items else 0
    rejected_name = _clean_text(items[selected_page].get("item")) if items else ""

    if rejected_name:
        def remember_rejection(current):
            history = current.get("wardrobe_purchase_rejections") or {}
            current["wardrobe_purchase_rejections"] = {
                "items": rotation.remember(
                    history.get("items") or [], rejected_name,
                    limit=50, key=lambda value: _clean_text(value).casefold(),
                ),
            }
            return current, None

        store.mutate_profile(cid, remember_rejection)
    await show_purchase_page(
        bot, cid, 0, q=q, reset_candidates=True,
        exclude_names=[rejected_name] if rejected_name else None,
    )


def _local_purchase_suggestions(item, wardrobe):
    """Короткий ответ без AI, чтобы временный сбой не прерывал покупку."""
    names = [
        public_item_name(entry)
        for _zone, _subcat, entry in _flat_wardrobe_items(wardrobe)
        if public_item_name(entry)
    ]
    facts = " ".join(names).casefold()
    if "олив" in facts and "сер" in facts:
        colors = [
            {"color": "тёмно-синий", "reason": "собирает оливковые и серые вещи в спокойный комплект"},
            {"color": "молочный", "reason": "делает сочетания со спокойной базой светлее"},
            {"color": "бордовый", "reason": "добавляет акцент, не споря с оливковым"},
        ]
    else:
        colors = [
            {"color": "тёмно-синий", "reason": "легко сочетается с нейтральной базой"},
            {"color": "молочный", "reason": "освежает повседневные комплекты"},
            {"color": "графитовый", "reason": "даёт спокойную альтернативу чёрному"},
        ]
    outfits = []
    for first, second in zip(names[::2], names[1::2]):
        outfits.append(f"{item.capitalize()} + {first} + {second}")
        if len(outfits) == 3:
            break
    return {
        "item": item,
        "headline": f"Для «{item}» начни с этих цветов: они дадут больше сочетаний с тем, что уже есть",
        "colors": colors,
        "avoid": "не бери ещё один оттенок, который почти повторяет уже имеющийся верх",
        "outfits": outfits,
    }


def _normalize_purchase_suggestions(data, item, wardrobe):
    """Оставляет только проверяемые рекомендации и реальные вещи из шкафа."""
    fallback = _local_purchase_suggestions(item, wardrobe)
    data = data if isinstance(data, dict) else {}
    headline = _clean_text(data.get("headline")) or fallback["headline"]
    colors = []
    for entry in data.get("colors") or []:
        if not isinstance(entry, dict):
            continue
        color = _clean_text(entry.get("color"))
        reason = _clean_text(entry.get("reason"))
        if color and reason:
            colors.append({"color": color[:40], "reason": reason[:160]})
    wardrobe_names = [
        _clean_text(public_item_name(entry))
        for _zone, _subcat, entry in _flat_wardrobe_items(wardrobe)
        if _clean_text(public_item_name(entry))
    ]
    outfits = []
    for value in data.get("outfits") or []:
        outfit = _clean_text(value)
        matches = sum(name.casefold() in outfit.casefold() for name in wardrobe_names)
        if outfit and matches >= min(2, len(wardrobe_names)):
            outfits.append(outfit)
        if len(outfits) == 3:
            break
    return {
        "item": _clean_text(data.get("item")) or item,
        "headline": headline[:220],
        "colors": colors[:3] or fallback["colors"],
        "avoid": _clean_text(data.get("avoid"))[:180],
        "outfits": outfits or fallback["outfits"],
    }


async def recommend_purchase(bot, cid, item):
    """Подбирает нужную вещь по полному шкафу: цвет и до трёх реальных луков."""
    item = _clean_text(item)
    wardrobe = store.load_wardrobe(cid)
    if not item:
        await bot.send_message(chat_id=cid, text="Напиши, какую вещь ищешь: например «худи».",
                               reply_markup=_purchase_hub_kb())
        return
    if not has_wardrobe_items(cid):
        await bot.send_message(
            chat_id=cid,
            text="Сначала заполни шкаф — тогда я смогу подобрать цвет и сочетания именно к твоим вещам.",
            reply_markup=_kb([[("🆕 Заполнить шкаф", "w_fill")],
                              [("⬅️ Назад", "m_wardrobe"), ("#️⃣ Главная", "m_menu")]]),
        )
        return
    prefs = _settings.wardrobe_prefs_context(cid)
    prompt = f"""Ты персональный стилист. Пользователь хочет купить: {secure.wrap_untrusted(item, 'покупка')}.
Подбери эту вещь к его реальному гардеробу, не советуя абстрактные вещи.

Предпочтения:
{secure.wrap_untrusted(prefs, 'предпочтения')}

Гардероб:
{secure.wrap_untrusted(store.wardrobe_to_text(wardrobe), 'гардероб')}

Верни JSON без Markdown:
{{
  "item":"название покупки из входных данных",
  "headline":"один короткий прямой вывод",
  "colors":[
    {{"color":"конкретный цвет или оттенок","reason":"почему сочетается с вещами из шкафа"}}
  ],
  "avoid":"один оттенок или тип, который лучше не покупать, если это подтверждает шкаф; иначе пусто",
  "outfits":["до трёх готовых сочетаний только с реальными вещами из шкафа"]
}}

Правила: дай 2–3 цвета, не выдумывай вещи, не пиши общих советов и не повторяй один
и тот же комплект. Если данных мало, честно оставь поле пустым."""
    try:
        data = await ai.allm_json(
            prompt, 650, tier="smart", module="wardrobe",
            cache_context={
                "scenario": "wardrobe_purchase_suggestions",
                "item": item,
                "wardrobe": wardrobe,
                "preferences": prefs,
                "language": "ru",
                "profile_version": 1,
                "schema_version": 1,
            },
        )
    except Exception:
        data = {}
    result = _normalize_purchase_suggestions(data, item, wardrobe)
    text_out, entities = _build_purchase_suggestions_message(result)
    store.last_source[str(cid)] = "Гардероб · Что докупить"
    store.last_answer[str(cid)] = text_out
    await bot.send_message(chat_id=cid, text=text_out, entities=entities,
                           reply_markup=_purchase_result_kb())


# ---------- добавление файлом (старый режим, оставлен) ----------
