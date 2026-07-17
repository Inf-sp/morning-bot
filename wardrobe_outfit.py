"""Локальный движок подбора и оценки образа."""

import re
from datetime import datetime, timedelta

import config
import store
from wardrobe_model import (
    flat_items as _flat_wardrobe_items,
    public_item_name,
    strip_internal_tags,
)

WARDROBE_OUTERWEAR_MAX_TEMP = 20
NEUTRAL_COLORS = ("бел", "чёрн", "черн", "сер", "беж", "сини", "деним", "джинс")
SAFE_NEUTRAL_STYLE_TIP = "Слегка заправь верх спереди, чтобы силуэт выглядел собраннее."
_SUNGLASSES_MARKERS = ("солнцезащит", "солнечн", "очки от солнца", "sunglasses")

def _day_key():
    return datetime.now(config.TZ).date().isoformat()

# ---------- локальный подбор образа (без AI) ----------
_TEMP_CONFLICT_MARGIN = 10  # °C — насколько диапазон temp_range должен разойтись с погодой, чтобы вещь исключалась


def _temp_conflicts(item, weather_ctx):
    tmax = weather_ctx.get("tmax")
    if item.get("warmth") == "тёплые" and (weather_ctx.get("hot") or (tmax is not None and tmax >= 24)):
        # Физический комфорт — жёсткий guard до любого скоринга цвета и стиля.
        return True
    if item.get("zone") == "Аксессуары":
        # Обычные аксессуары не имеют верхнего температурного порога одежды:
        # очки, часы и ремень остаются допустимыми даже в сильную жару.
        return False
    tr = item.get("temp_range")
    if not tr or tmax is None:
        return False
    lo, hi = tr
    return tmax > hi + _TEMP_CONFLICT_MARGIN or tmax < lo - _TEMP_CONFLICT_MARGIN


def _is_sunglasses(item):
    facts = f"{item.get('name', '')} {item.get('subcategory', '')}".casefold()
    return item.get("zone") == "Аксессуары" and any(marker in facts for marker in _SUNGLASSES_MARKERS)


def select_outfit_candidates(w, weather_ctx):
    """Жёсткая фильтрация кандидатов по зонам (не скоринг). Возвращает
    {zone: [item, ...]} — зона «Верхняя одежда» опциональна по погоде и может
    вернуть пустой список кандидатов, даже если в шкафу есть куртки."""
    candidates = {}
    for zone in store.ZONE_ORDER:
        if zone == "Другое":
            continue
        items = [it for _s, items in (w.get("zones", {}).get(zone, {}) or {}).items() for it in items]
        items = [it for it in items if not _temp_conflicts(it, weather_ctx)]
        if zone == "Верхняя одежда":
            too_warm_for_outer = (weather_ctx.get("tmax") or 0) > WARDROBE_OUTERWEAR_MAX_TEMP
            outerwear_needed = weather_ctx.get("has_rain") or weather_ctx.get("strong_wind") or not too_warm_for_outer
            if not outerwear_needed:
                candidates[zone] = []
                continue
            if weather_ctx.get("has_rain"):
                items = sorted(items, key=lambda it: not it.get("rain_ok"))
        elif zone == "Аксессуары" and (weather_ctx.get("hot") or weather_ctx.get("sunny")):
            # Очки могут лежать дальше первых двух аксессуаров, которые попадут
            # в перебор, поэтому поднимаем их до ограничения пула кандидатов.
            items = sorted(items, key=lambda item: not _is_sunglasses(item))
        candidates[zone] = items
    return candidates


def _is_neutral_color(color):
    c = str(color or "").lower()
    return any(p in c for p in NEUTRAL_COLORS)


def _color_penalty(items):
    """Штраф за 2+ ярких (не-нейтральных) цвета одновременно в наборе."""
    bright = []
    for it in items:
        for c in (it.get("colors") or []):
            if not _is_neutral_color(c):
                bright.append(c.lower())
    if len(bright) <= 1:
        return 0
    return -10 * (len(bright) - 1)


def outfit_style_score(items, style):
    """Насколько конкретный комплект соответствует одному из шести UI-стилей."""
    style = str(style or "").casefold()
    facts = " ".join(
        " ".join(str(item.get(key) or "") for key in ("name", "subcategory", "style", "fit", "material"))
        for item in items
    ).casefold()
    colors = [str(color).casefold() for item in items for color in (item.get("colors") or [])]
    accessories = sum(item.get("zone") == "Аксессуары" for item in items)
    neutral = sum(_is_neutral_color(color) for color in colors)

    profiles = {
        "минимализм": ("minimal", "лаконич", "прост", "прям"),
        "скандинавский": ("scandi", "свобод", "лён", "лен", "хлоп", "шерст", "кардиган"),
        "повседневный": ("casual", "рубаш", "прямые брюки", "чинос", "лофер", "кеды"),
        "городской": ("street", "оверсайз", "худи", "футбол", "широк", "карго", "кеды", "кроссов"),
        "классический": ("formal", "classic", "класс", "рубаш", "пиджак", "пальто", "лофер"),
        "спортивный": ("sport", "спортив", "худи", "джоггер", "кроссов", "функцион"),
    }
    score = sum(2 for marker in profiles.get(style, ()) if marker in facts)
    if style == "минимализм":
        score += neutral
        score -= max(accessories - 1, 0) * 2
    elif style == "скандинавский":
        score += sum(any(marker in color for marker in ("беж", "сер", "бел", "олив", "корич")) for color in colors)
    elif style in ("повседневный", "классический") and any(marker in facts for marker in ("sport", "спортив", "джоггер")):
        score -= 3
    return score


def choose_outfit_style(items, selected_styles):
    """Главный стиль комплекта; при равенстве сохраняет порядок настроек."""
    selected = [style for style in (selected_styles or []) if str(style).strip()]
    if not selected:
        return ""
    return max(selected, key=lambda style: outfit_style_score(items, style))


def score_outfit(items, weather_ctx, wardrobe_history, prefs_text, selected_styles=None):
    """Скоринг одной комбинации вещей (одна вещь на зону, максимум 5 вещей).
    Возвращает float — выше лучше."""
    score = 0.0
    tmax = weather_ctx.get("tmax")
    for it in items:
        tr = it.get("temp_range")
        if tr and tmax is not None and tr[0] <= tmax <= tr[1]:
            score += 5
        if it.get("zone") == "Аксессуары":
            # Аксессуар не добавляется автоматически: он должен получить пользу
            # от погоды или соответствия выбранному стилю.
            if (weather_ctx.get("hot") or weather_ctx.get("sunny")) and _is_sunglasses(it):
                score += 5
    score += _color_penalty(items)
    if selected_styles:
        score += 2 * max(outfit_style_score(items, style) for style in selected_styles)
    prefs_low = str(prefs_text or "").lower()
    for it in items:
        colors = [str(color).lower() for color in (it.get("colors") or [])]
        fit = str(it.get("fit") or "").lower()
        style = " ".join((str(it.get("style") or ""), str(it.get("formality") or ""))).lower()
        name = str(it.get("name") or "").lower()
        if fit and f"посадка одежды: {fit}" in prefs_low:
            score += 3
        if "яркие цвета" in prefs_low and any(not _is_neutral_color(color) for color in colors):
            score -= 6
        if "крупные принты" in prefs_low and any(word in name for word in ("крупный принт", "логотип", "график")):
            score -= 7
        if "узкий крой" in prefs_low and any(word in fit + " " + name for word in ("узк", "скинни", "slim")):
            score -= 8
        if "слишком спортивное" in prefs_low and any(word in style + " " + name for word in ("sport", "спортив")):
            score -= 5
        if "тёмные" in prefs_low and any(any(marker in color for marker in ("чёрн", "черн", "тём", "темн", "бордов")) for color in colors):
            score += 1
        if "светлые" in prefs_low and any(any(marker in color for marker in ("бел", "беж", "светл")) for color in colors):
            score += 1
        if "предпочитаемая палитра" in prefs_low and "яркие" in prefs_low and any(
            not _is_neutral_color(color) for color in colors
        ):
            score += 1
        # Маленький тай-брейкер, не решающий фактор: при прочих равных чуть
        # предпочитаем вещь, которая реже носилась в последнее время — но только
        # после того, как цельность образа (цвет/погода/антиповтор) уже учтена.
        score -= 0.1 * min(int(it.get("use_count", 0)), 20)
    item_ids = {it.get("id") for it in items}
    cutoff_3d = (datetime.now(config.TZ) - timedelta(days=3)).date().isoformat()
    for entry in wardrobe_history:
        if entry.get("date", "") < cutoff_3d:
            continue
        if item_ids & set(entry.get("item_ids") or []):
            score -= 3
    cutoff_7d = (datetime.now(config.TZ) - timedelta(days=7)).date().isoformat()
    for entry in wardrobe_history:
        if entry.get("date", "") < cutoff_7d:
            continue
        if item_ids and item_ids == set(entry.get("item_ids") or []):
            score -= 100
    if prefs_text:
        avoid_raw = str(prefs_text)
        for it in items:
            for c in (it.get("colors") or []):
                if c and c.lower() in avoid_raw.lower() and "нежелательные цвета" in avoid_raw.lower():
                    score -= 4
    return score


def _top_candidates(items, limit=3):
    """Ограничивает размер зоны до перебора комбинаций (см. _combos) без
    предвзятости по частоте ношения — какая вещь выигрывает, решает score_outfit
    (цельность образа), а не то, что вещь ещё не использовалась."""
    return items[:limit]


def pick_best_outfit(w, weather_ctx, wardrobe_history, prefs_text, previous_item_ids=None,
                     selected_styles=None):
    """Собирает кандидатов, перебирает ограниченные комбинации (топ-3 на зону),
    возвращает лучший набор вещей (list[item]) или None, если нет кандидатов хотя
    бы на одну обязательную зону (Верх/Низ/Обувь)."""
    candidates = select_outfit_candidates(w, weather_ctx)
    required = ["Верх", "Низ", "Обувь"]
    if any(not candidates.get(z) for z in required):
        return None

    def _combos():
        import itertools
        pools = [_top_candidates(candidates[z]) for z in required]
        optional_zones = [z for z in ("Верхняя одежда", "Аксессуары") if candidates.get(z)]
        for zone in optional_zones:
            pools.append([None] + _top_candidates(candidates[zone], limit=2))
        for combo in itertools.product(*pools):
            yield [it for it in combo if it is not None]

    previous_item_ids = set(previous_item_ids or [])
    combos = list(_combos())
    if previous_item_ids:
        # «Другой образ» должен менять основу комплекта, а не одну случайную вещь.
        # Для полного набора из 4–5 элементов требуем минимум две замены; для
        # маленького шкафа оставляем честную возможность заменить хотя бы одну.
        min_changes = 2 if any(len(combo) >= 4 for combo in combos) else 1
        combos = [combo for combo in combos
                  if len(previous_item_ids - {it.get("id") for it in combo}) >= min_changes]
    scored = [(
        score_outfit(combo, weather_ctx, wardrobe_history, prefs_text, selected_styles), combo,
    ) for combo in combos]
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_combo = scored[0]
    if best_score <= -50:
        # Похоже, единственный приемлемый вариант — это тот самый 7-дневный повтор
        # (guard). Пересчитываем без 7-дневного штрафа — маленький гардероб важнее антиповтора.
        rescored = [(score_outfit(combo, weather_ctx, [
            e for e in wardrobe_history
            if e.get("date", "") >= (datetime.now(config.TZ) - timedelta(days=3)).date().isoformat()
        ], prefs_text, selected_styles), combo) for _s, combo in scored]
        rescored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_combo = rescored[0]
    return best_combo


# ---------- текст образа: локальный fallback ----------
def _sentence_item_name(item):
    name = public_item_name(item)
    return name[:1].lower() + name[1:] if name else "вещь"


def _shoe_finishing_verb(item):
    name = public_item_name(item).casefold()
    if any(marker in name for marker in ("обувь", "пара ", "модель ")):
        return "завершает"
    plural_subcategories = {"Кеды", "Кроссовки", "Лоферы", "Ботинки", "Сандалии", "Тапочки"}
    return "завершают" if item.get("subcategory") in plural_subcategories else "завершает"


def build_outfit_reasons(items, weather_ctx, score_details=None):
    """Одна естественная строка о цельности образа на подтверждённых фактах.

    Для обуви отдельно выбирается число глагола, потому что названия вещей —
    свободный текст: «кеды завершают», но «обувь завершает»."""
    reasons = []
    colors = [c for it in items for c in (it.get("colors") or [])]
    bright = [c for c in colors if not _is_neutral_color(c)]
    neutral_anchor = next((it for it in items if any(_is_neutral_color(c) for c in (it.get("colors") or []))), None)
    if neutral_anchor and len(set(bright)) >= 2:
        return [
            f"{_sentence_item_name(neutral_anchor)} поддерживает спокойную основу, "
            f"а {' и '.join(sorted(set(bright))[:2])} добавляют цвет."
        ]
    outer = next((it for it in items if it.get("zone") == "Верхняя одежда"), None)
    if outer and weather_ctx.get("has_rain") and outer.get("rain_ok"):
        return [f"{_sentence_item_name(outer)} завершает образ и защищает от дождя."]
    elif weather_ctx.get("has_rain") and not (outer and outer.get("rain_ok")):
        return ["Вещи сочетаются между собой, но в шкафу нет подтверждённой защиты от дождя."]
    elif outer and weather_ctx.get("warm"):
        return [f"{_sentence_item_name(outer)} завершает образ и даёт слой для прохладного утра."]
    low = next((it for it in items if it.get("zone") == "Низ"), None)
    top = next((it for it in items if it.get("zone") == "Верх"), None)
    shoe = next((it for it in items if it.get("zone") == "Обувь"), None)
    if top and low and shoe:
        return [
            f"{_sentence_item_name(top)} и {_sentence_item_name(low)} создают лёгкую базу, "
            f"а {_sentence_item_name(shoe)} {_shoe_finishing_verb(shoe)} образ."
        ]
    if top and low:
        return [f"{_sentence_item_name(top)} и {_sentence_item_name(low)} создают цельную основу образа."]
    if shoe:
        return [f"{_sentence_item_name(shoe)} {_shoe_finishing_verb(shoe)} образ."]
    return reasons


_LONG_SLEEVE_MARKERS = ("длинн", "лонгслив")
_OPENABLE_LAYER_MARKERS = ("рубаш", "куртк", "пиджак", "кардиган", "пальто", "плащ", "ветровк")


def _has_confirmed_long_sleeves(item):
    text = f"{item.get('name', '')} {item.get('subcategory', '')}".casefold()
    if "коротк" in text and "рукав" in text:
        return False
    return "лонгслив" in text or ("длинн" in text and "рукав" in text)


def build_style_tip(items, weather_ctx=None):
    """Один совет по носке, использующий только вещи из items. Пустая строка,
    если нет подходящего шаблона — не выдумываем совет ради совета."""
    weather_ctx = weather_ctx or {}
    outer = next((it for it in items if it.get("zone") == "Верхняя одежда"), None)
    if outer and weather_ctx.get("warm") and any(
        marker in str(outer.get("name") or "").casefold() for marker in _OPENABLE_LAYER_MARKERS
    ):
        return "Оставь верхний слой расстёгнутым, чтобы силуэт выглядел легче."
    sleeved = next((it for it in items
                    if it.get("zone") == "Верх" and _has_confirmed_long_sleeves(it)),
                   None)
    if sleeved:
        return "Подверни рукава до середины предплечья, чтобы образ выглядел легче."
    return SAFE_NEUTRAL_STYLE_TIP


_COLOR_CLAIM_MARKERS = (
    "бел", "чёрн", "черн", "сер", "беж", "син", "голуб", "красн", "зелён",
    "зелен", "корич", "бордов", "жёлт", "желт", "оранж", "розов", "фиолет",
    "серебр", "золот",
)
_MATERIAL_CLAIM_MARKERS = (
    "лён", "лен", "льнян", "хлоп", "шерст", "кашемир", "кож", "замш", "деним",
    "шёлк", "шелк", "полиэстер", "вискоз", "трикотаж",
)
_FIT_CLAIM_MARKERS = ("посадк", "объём", "объем", "свободн", "широк", "узк", "прям", "притал", "оверсайз")
_WARMTH_CLAIM_MARKERS = ("тёпл", "тепл", "утепл", "толст", "плотн", "лёгк", "легк", "тонк")
_LENGTH_CLAIM_MARKERS = ("длинн", "коротк", "укороч")
_DETAIL_CLAIM_MARKERS = ("воротник", "карман", "манжет", "капюшон", "принт", "вышив", "пуговиц", "молни", "фактур")
_GARMENT_CLAIM_MARKERS = (
    "верх", "низ", "обув", "аксессуар", "рубаш", "футбол", "лонгслив", "свитер", "худи", "куртк", "пиджак", "пальто",
    "плащ", "ветровк", "брюк", "джинс", "чинос", "шорт", "юбк", "кед", "кроссов",
    "лофер", "ботин", "сандал", "часы", "ремн", "сумк", "рюкзак", "шарф", "кепк", "очк",
)
_ACCESSORY_CLAIM_MARKERS = ("аксессуар", "часы", "ремн", "сумк", "рюкзак", "шарф", "кепк", "шапк", "очк", "украшен", "кольц", "цепоч")
_STYLE_TIP_ACTION_RE = re.compile(
    r"\b(?:заправ|подверн|закат|остав|расстег|застег|подтян|сдвин|слож|нос|"
    r"добав|сними|убери|возьми)\w*",
    re.IGNORECASE,
)
_STYLE_TIP_RESULT_RE = re.compile(r"(?:чтобы|так\s+(?:образ|силуэт|сочетание))", re.IGNORECASE)
_UNHELPFUL_STYLE_TIP_MARKERS = (
    "без дополнительных", "без изменений", "ничего добавлять", "ничего менять",
    "образ готов", "не нужно", "носи комплект", "носи этот наряд",
)


def _facts_text(items):
    chunks = []
    for item in items:
        chunks.extend(str(item.get(key) or "") for key in (
            "name", "zone", "subcategory", "color", "color_secondary", "material", "length", "warmth",
            "style", "fit", "formality",
        ))
        for key in ("colors", "season", "occasions"):
            chunks.extend(str(value) for value in (item.get(key) or []))
    return " ".join(chunks).casefold()


def _claims_are_grounded(text, items):
    """Проверяет только проверяемые фактические утверждения, без AI-семантики."""
    claim = str(text or "").casefold()
    facts = _facts_text(items)
    if not claim:
        return False
    for markers in (_COLOR_CLAIM_MARKERS, _MATERIAL_CLAIM_MARKERS, _LENGTH_CLAIM_MARKERS,
                    _DETAIL_CLAIM_MARKERS, _GARMENT_CLAIM_MARKERS):
        for marker in markers:
            if marker in claim and marker not in facts:
                return False
    if "светл" in claim and not any(marker in facts for marker in ("светл", "бел", "беж", "серебр", "голуб")):
        return False
    if any(marker in claim for marker in ("тём", "темн")) and not any(
        marker in facts for marker in ("тём", "темн", "чёрн", "черн", "син", "корич", "бордов")
    ):
        return False
    if any(marker in claim for marker in _FIT_CLAIM_MARKERS):
        fit_facts = " ".join(str(item.get("fit") or "") + " " + str(item.get("name") or "") for item in items).casefold()
        if not any(marker in fit_facts for marker in _FIT_CLAIM_MARKERS):
            return False
    physical_warmth_claim = re.sub(
        r"л[её]гк\w*\s+(?:баз\w*|палитр\w*|образ\w*)",
        "",
        claim,
    )
    if any(marker in physical_warmth_claim for marker in _WARMTH_CLAIM_MARKERS):
        warmth_facts = " ".join(str(item.get("warmth") or "") + " " + str(item.get("name") or "") for item in items).casefold()
        if not any(marker in warmth_facts for marker in _WARMTH_CLAIM_MARKERS):
            return False
    if re.search(r"(?:объ[её]мн\w*\s+рукав|рукав\w*\s+объ[её]мн)", claim):
        return False
    return True


def _sanitize_generated_text(text, items):
    clean = str(text or "")
    for item in items:
        raw_name = str(item.get("name") or "")
        if raw_name:
            clean = clean.replace(raw_name, public_item_name(item))
    return strip_internal_tags(clean).strip()


def _natural_reason(reason):
    text = re.sub(r"\s+", " ", str(reason or "")).strip().casefold()
    banned = (
        "составляют основу комплекта",
        "светлый цвет обуви",
        "поддерживает палитру комплекта",
        "завершает комплект",
        "завершают комплект",
    )
    return (
        bool(text)
        and "(" not in text
        and ")" not in text
        and text.count("комплект") <= 1
        and not any(phrase in text for phrase in banned)
    )


def _valid_style_tip(tip, items):
    tip_low = str(tip or "").casefold()
    if (any(marker in tip_low for marker in _UNHELPFUL_STYLE_TIP_MARKERS)
            or not _STYLE_TIP_ACTION_RE.search(tip_low)
            or not _STYLE_TIP_RESULT_RE.search(tip_low)):
        return False
    if not _claims_are_grounded(tip, items):
        return False
    if any(action in tip_low for action in ("подверни рукав", "подвернуть рукав", "закатай рукав", "закатать рукав")):
        return any(_has_confirmed_long_sleeves(item) for item in items)
    if "подверн" in tip_low and any(word in tip_low for word in ("брюк", "джинс", "чинос")):
        return any(
            item.get("zone") == "Низ" and "длинн" in str(item.get("name") or "").casefold()
            for item in items
        )
    return True


def validate_outfit_copy(items, wardrobe, weather_ctx, reasons, tip, final_heading, final_text):
    """Финальный guard между генерацией и UI: только факты из выбранных вещей.

    Заодно сверяет id аксессуаров с текущей базой гардероба и не разрешает
    финальному штриху предлагать аксессуар, которого нет в выбранном комплекте.
    """
    database_items = [item for _zone, _subcategory, item in _flat_wardrobe_items(wardrobe)]
    database_ids = {item.get("id") for item in database_items if item.get("id")}
    verified_items = [item for item in items if not item.get("id") or item.get("id") in database_ids]

    clean_reasons = []
    for reason in reasons or []:
        clean = _sanitize_generated_text(reason, verified_items)
        if _claims_are_grounded(clean, verified_items) and _natural_reason(clean):
            clean_reasons.append(clean)
    if not clean_reasons:
        clean_reasons = build_outfit_reasons(verified_items, weather_ctx)

    clean_tip = _sanitize_generated_text(tip, verified_items)
    if not _valid_style_tip(clean_tip, verified_items):
        clean_tip = SAFE_NEUTRAL_STYLE_TIP

    clean_final = _sanitize_generated_text(final_text, verified_items)
    if (final_heading or "Образ готов") == "Образ готов" and any(
        marker in clean_final.casefold() for marker in _ACCESSORY_CLAIM_MARKERS
    ):
        selected_accessories = [item for item in verified_items if item.get("zone") == "Аксессуары"]
        if not selected_accessories or not _claims_are_grounded(clean_final, selected_accessories):
            clean_final = "Комплект собран из вещей твоего шкафа"

    return {
        "items": verified_items,
        "reasons": clean_reasons[:1],
        "style_tip": clean_tip,
        "final_text": clean_final or "Комплект собран из вещей твоего шкафа",
    }


def build_wardrobe_insight(cid, items, wardrobe_history):
    """Один инсайт по фиксированному приоритету правил, первое совпавшее — оно и
    возвращается. None, если ничего не подошло."""
    item_ids = {it.get("id") for it in items}
    if wardrobe_history:
        last = wardrobe_history[-1]
        if item_ids and item_ids == set(last.get("item_ids") or []):
            return "Этот образ был и вчера."
    today = datetime.now(config.TZ).date()
    for it in items:
        last_used = it.get("last_used")
        if last_used:
            try:
                days = (today - datetime.fromisoformat(last_used).date()).days
            except ValueError:
                days = None
            if days is not None and days > 14:
                return f"{it.get('name')} — впервые за {days} дней."
    if len(wardrobe_history) >= 3:
        last3 = wardrobe_history[-3:]
        for it in items:
            if all(it.get("id") in (e.get("item_ids") or []) for e in last3):
                return f"{it.get('name')} — в последних образах подряд."
    return None


def save_outfit_feedback(cid, item_ids, weather_tags):
    """Вызывается только при НОВОЙ генерации образа (не при показе кэша дня):
    use_count/last_used вещей + запись в персистентную историю образов."""
    today = _day_key()
    id_set = set(item_ids)

    def _mut(w):
        for _zone, _subcat, item in _flat_wardrobe_items(w):
            if item.get("id") in id_set:
                item["use_count"] = int(item.get("use_count", 0)) + 1
                item["last_used"] = today

    store.mutate_wardrobe(cid, _mut)
    store.add_wardrobe_history_entry(cid, today, weather_tags, item_ids)
