"""Локальный движок подбора и оценки образа."""

import hashlib
import re
from datetime import datetime, timedelta

import config
import store
import wardrobe_rules as rules
from wardrobe_model import (
    CLOTHING_ZONES,
    ZONE_ORDER,
    flat_items as _flat_wardrobe_items,
    public_item_name,
)

WARDROBE_OUTERWEAR_MAX_TEMP = 20
NEUTRAL_COLORS = ("бел", "чёрн", "черн", "сер", "беж", "сини", "деним", "джинс")
SAFE_NEUTRAL_STYLE_TIPS = (
    "Оставь верх навыпуск, чтобы силуэт выглядел расслабленнее.",
    "Носи верх навыпуск, чтобы силуэт выглядел легче.",
    "Сделай обувь финальным акцентом, чтобы образ выглядел собраннее.",
    "Оставь один главный акцент, чтобы образ выглядел цельнее.",
    "Не добавляй второй заметный акцент, чтобы образ не выглядел перегруженным.",
    "Сделай силуэт спокойнее, чтобы образ выглядел современнее.",
    "Оставь линии комплекта чистыми, чтобы образ выглядел собраннее.",
)
_SUNGLASSES_MARKERS = ("солнцезащит", "солнечн", "очки от солнца", "sunglasses")
_RAINCOAT_MARKERS = ("дождевик", "raincoat")
_SHORTS_MARKERS = ("шорт", "shorts")
_CITY_STYLE = "городской"
_CITY_2026_RELAXED_MARKERS = (
    "свобод", "оверсайз", "широк", "прям", "relaxed", "roomy",
    "карго", "utility", "деним", "джинс", "чинос",
)
_CITY_2026_BASE_MARKERS = (
    "футболк", "майк", "лонгслив", "худи", "свитш", "толстовк", "поло",
    "street", "streetwear", "oversize", "оверсайз",
)
_CITY_2026_STRICT_MARKERS = (
    "офисн", "делов", "строг", "костюмн", "formal", "business",
)
_LIGHT_BASE_MARKERS = ("футболк", "майк", "лонгслив", "топ")

# Зона вещи недостаточна для построения комплекта: старые вещи и ответ AI при
# добавлении могли сохранить жилет или overshirt в «Верхе». Роль определяем по
# самой вещи перед финальным выбором, чтобы дополнительный слой не подменял
# базовый верх.
_BASE_TOP_MARKERS = (
    "футболк", "майк", "рубаш", "лонгслив", "поло", "топ", "водолазк",
)
# Кофты (худи, свитеры, кардиганы, флис) — тоже второй слой: под ними всегда футболка. Флис — средний тёплый
# слой, даже если в шкафу записан курткой: под ветровку, дождевик или парку в холод, сам — в прохладу.
_FLEECE_MARKERS = ("флис", "fleece")
_LAYER_MARKERS = (
    "жилет", "кардиган", "overshirt", "оверши", "shacket", "шакет",
    "пиджак", "блейзер", "свитер", "джемпер", "свитш", "худи", "толстовк", "кофт", *_FLEECE_MARKERS,
)
_OUTERWEAR_MARKERS = (
    "куртк", "ветровк", "пальто", "плащ", "дождевик", "парка", "пуховик",
    "тренч", "анорак", "бомбер",
)
_ROLE_DISPLAY_ORDER = {
    "base_top": 0,
    "layer": 1,
    "bottom": 2,
    "shoes": 3,
    "outerwear": 4,
}


def outfit_role(item):
    """Возвращает функциональную роль вещи в комплекте, не меняя её категорию в шкафу."""
    facts = " ".join(str(item.get(key) or "") for key in ("name", "subcategory")).casefold()
    zone = str(item.get("zone") or "")
    if zone == "Кофты" or any(marker in facts for marker in _LAYER_MARKERS):
        return "layer"
    if zone == "Верхняя одежда" or any(marker in facts for marker in _OUTERWEAR_MARKERS):
        return "outerwear"
    if zone == "Низ":
        return "bottom"
    if zone == "Обувь":
        return "shoes"
    if any(marker in facts for marker in _BASE_TOP_MARKERS):
        return "base_top"
    return ""


def _item_facts(item):
    return " ".join(
        str(item.get(key) or "")
        for key in ("name", "subcategory", "style", "fit", "formality", "material")
    ).casefold()


def urban_2026_item_score(item):
    """Мягкий сигнал актуального городского силуэта.

    Он не заменяет погоду, посадку из предпочтений и выбранный стиль: нужен
    только для выбора между сопоставимыми вещами из уже существующего шкафа.
    """
    facts = _item_facts(item)
    role = outfit_role(item)
    score = 0
    relaxed = any(marker in facts for marker in _CITY_2026_RELAXED_MARKERS)
    strict = any(marker in facts for marker in _CITY_2026_STRICT_MARKERS)

    if role == "base_top":
        if any(marker in facts for marker in _CITY_2026_BASE_MARKERS):
            score += 3
        if relaxed:
            score += 2
        if strict:
            score -= 4
    elif role == "layer":
        if relaxed or any(marker in facts for marker in ("overshirt", "оверши", "shacket", "шакет")):
            score += 2
    elif role == "bottom":
        if relaxed:
            score += 3
        if any(marker in facts for marker in ("узк", "скинни", "slim")):
            score -= 2
    elif role == "shoes" and any(marker in facts for marker in ("кеды", "кроссов", "sneaker")):
        score += 2
    return score


def is_urban_2026_base_top(item):
    """Есть ли в шкафу базовый верх для современного городского комплекта."""
    if outfit_role(item) != "base_top":
        return False
    facts = _item_facts(item)
    if any(marker in facts for marker in _CITY_2026_STRICT_MARKERS) and not any(
        marker in facts for marker in _CITY_2026_RELAXED_MARKERS
    ):
        return False
    return any(marker in facts for marker in _CITY_2026_BASE_MARKERS + _CITY_2026_RELAXED_MARKERS)


def _city_style_selected(selected_styles):
    return any(str(style or "").casefold() == _CITY_STYLE for style in (selected_styles or []))


def outfit_display_order(item):
    """Порядок вещей в карточке: базовый верх, слой, низ, обувь, верхняя одежда."""
    return _ROLE_DISPLAY_ORDER.get(outfit_role(item), {
        "Верх": 0, "Низ": 2, "Обувь": 3, "Верхняя одежда": 4, "Аксессуары": 5,
    }.get(item.get("zone"), 9))


# ---------- логика комплекта: что с чем не носят (§ docs/wardrobe.md «Оценка комплекта») ----------
# Толстый слой: в комплекте только один (худи + флис, свитер + худи — нельзя); жилет, овершот, пиджак — лёгкие.
_THICK_MARKERS = ("худи", "свитш", "толстов", "свитер", "джемпер", "пуловер", "кардиган", "кофт", *_FLEECE_MARKERS)
_LIGHT_LAYER_MARKERS = ("жилет", "overshirt", "оверши", "shacket", "шакет", "пиджак", "блейзер")
# Мягкое и спортивное (худи, свитшот, флис) — поверх футболки, лонгслива или водолазки, не поверх рубашки.
_KNIT_OVER_SHIRT = ("худи", "свитш", "толстов", *_FLEECE_MARKERS)
_SPORTY_MARKERS = ("худи", "свитш", "толстов", "джоггер", "спортив", "трениров", "треник", "sport",
                   *_FLEECE_MARKERS)
_FORMAL_MARKERS = ("строг", "костюмн", "пиджак", "блейзер", "оксфорд", "дерби", "броги", "туфл", "делов",
                   "офисн", "formal", "business")


def _kind_facts(item):
    """Что это за вещь: название, подкатегория, материал и формальность — без стиля из предпочтений
    («Классический» — вкус пользователя, а не строгость вещи)."""
    return " ".join(str(item.get(key) or "") for key in ("name", "subcategory", "material", "formality")).casefold()


def _is_thick_layer(item):
    if outfit_role(item) != "layer":
        return False
    facts = _kind_facts(item)
    return any(marker in facts for marker in _THICK_MARKERS) or (
        item.get("zone") == "Кофты" and not any(marker in facts for marker in _LIGHT_LAYER_MARKERS))


def outfit_conflicts(items) -> list:
    """Нарушения логики комплекта; пусто — комплект логичен. Не больше одного толстого слоя; худи и флис — не
    поверх рубашки; спортивное не со строгим; не больше одного заметного элемента (цветной акцент или рисунок)."""
    clothes = [item for item in items if item.get("zone") != "Аксессуары"]
    facts = [_kind_facts(item) for item in clothes]

    def has(markers):
        return any(marker in fact for fact in facts for marker in markers)

    found = []
    if sum(_is_thick_layer(item) for item in clothes) > 1:
        found.append("два толстых слоя")
    if has(_KNIT_OVER_SHIRT) and has(("рубаш",)):
        found.append("худи или флис поверх рубашки")
    if has(_SPORTY_MARKERS) and has(_FORMAL_MARKERS):
        found.append("спортивное со строгим")
    if rules.statement_count(clothes) > 1:
        found.append("больше одного заметного элемента")
    return found


def _soft_conflicts(items) -> list:
    """Мягкие нарушения цвета: отсекаются, если есть варианты без них, но образа никогда не лишают. ИИ-стилист
    выбирает из оставшихся — бежевая футболка под фиолетовым флисом ему уже не попадётся, если есть серая."""
    found = []
    if rules._warm_base_with_cold_accent(items):
        found.append("тёплая база под холодный цвет")
    if rules._earth_neighbours(items):
        found.append("землистые цвета рядом")
    return found


def _least_conflicts(combos):
    """Комплекты без нарушений логики; если без них не собрать — с наименьшим числом: сначала жёстких, затем
    мягких (образ всё равно будет)."""
    counted = [((len(outfit_conflicts(combo)), len(_soft_conflicts(combo))), combo) for combo in combos]
    fewest = min((count for count, _combo in counted), default=(0, 0))
    return [combo for count, combo in counted if count == fewest]


def is_complete_outfit(items):
    """Проверяет, что дополнительный или верхний слой не заменил базовый верх."""
    roles = [outfit_role(item) for item in items]
    needs_base_top = any(role in {"layer", "outerwear"} for role in roles)
    return not needs_base_top or "base_top" in roles


def _completed_layered_combos(combo, candidates, selected_styles=None):
    """Добавляет возможный базовый верх к неполному комплекту.

    Нужны все варианты, а не первая футболка: затем общий скоринг выберет
    подходящий к погоде, стилю и истории вещей вариант.
    """
    if is_complete_outfit(combo):
        return [combo]
    combo_ids = {item.get("id") for item in combo if item.get("id")}
    base_tops = [
        item
        for pool in candidates.values()
        for item in pool
        if outfit_role(item) == "base_top"
        and (not item.get("id") or item.get("id") not in combo_ids)
    ]
    # Под слой — подходящий верх: под фиолетовый флис серая футболка, а не рубашка или бежевая с рисунком.
    fitting = [item for item in base_tops if not outfit_conflicts(combo + [item])] or base_tops
    return [combo + [base_top] for base_top in _top_candidates(fitting, selected_styles=selected_styles)]


def _is_layerable_shirt(item):
    """Можно ли носить рубашку расстёгнутой как лёгкий второй слой."""
    facts = _item_facts(item)
    return (
        "рубаш" in facts
        and not any(marker in facts for marker in _CITY_2026_STRICT_MARKERS)
        and outfit_role(item) == "base_top"
    )


def _is_light_base_top(item):
    facts = _item_facts(item)
    return outfit_role(item) == "base_top" and any(marker in facts for marker in _LIGHT_BASE_MARKERS)


def _with_light_shirt_layers(combos, candidates, weather_ctx, selected_styles=None):
    """Добавляет варианты «футболка + расстёгнутая рубашка» для мягкой погоды."""
    tmax = weather_ctx.get("tmax")
    if tmax is None or not 14 <= tmax <= 22 or weather_ctx.get("hot"):
        return combos
    shirts = _top_candidates([
        item for item in candidates.get("Верх", []) if _is_layerable_shirt(item)
    ], limit=2, selected_styles=selected_styles)
    if not shirts:
        return combos
    layered = []
    for combo in combos:
        if not any(_is_light_base_top(item) for item in combo):
            continue
        combo_ids = {item.get("id") for item in combo if item.get("id")}
        for shirt in shirts:
            if not shirt.get("id") or shirt.get("id") not in combo_ids:
                layered.append(combo + [shirt])
    return combos + layered


def _has_light_shirt_layer(items):
    """Есть ли в комплекте именно связка лёгкого базового верха и рубашки."""
    return (
        any(_is_layerable_shirt(item) for item in items)
        and any(_is_light_base_top(item) and not _is_layerable_shirt(item) for item in items)
    )

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


def _is_shorts(item):
    facts = f"{item.get('name', '')} {item.get('subcategory', '')}".casefold()
    return outfit_role(item) == "bottom" and any(marker in facts for marker in _SHORTS_MARKERS)


def _shorts_weather(weather_ctx):
    """Шорты уместны: тепло весь окно, сухо, без порывов (см. wardrobe_rules)."""
    return rules.summer_weather(weather_ctx)


def _is_raincoat(item):
    facts = f"{item.get('name', '')} {item.get('subcategory', '')}".casefold()
    return item.get("zone") == "Верхняя одежда" and any(marker in facts for marker in _RAINCOAT_MARKERS)


def select_outfit_candidates(w, weather_ctx):
    """Жёсткая фильтрация кандидатов по зонам (не скоринг). Возвращает
    {zone: [item, ...]} — зона «Верхняя одежда» опциональна по погоде и может
    вернуть пустой список кандидатов, даже если в шкафу есть куртки."""
    candidates = {}
    for zone in ZONE_ORDER:
        if zone not in CLOTHING_ZONES:
            continue  # аксессуары не подбираются из шкафа — советом по погоде в строке-причине
        items = [it for _s, items in (w.get("zones", {}).get(zone, {}) or {}).items() for it in items]
        # Ротация: в пул перебора (топ-3 зоны) сначала идут давно не надетые вещи, иначе
        # четвёртая и дальше вещь шкафа не попадала в образ никогда. Погодные сортировки
        # ниже стабильны и сохраняют этот порядок внутри своих групп.
        items = sorted(items, key=lambda it: str(it.get("last_used") or ""))
        # Жёсткие правила погоды: шорты в дождь и ветер, тёплое в жару и т. п. — исключаются.
        items = [it for it in items if not _temp_conflicts(it, weather_ctx) and rules.allowed(it, weather_ctx)]
        if zone == "Обувь" and weather_ctx.get("has_rain"):
            # В дождь без замши и текстиля, если есть закрытая непромокающая альтернатива.
            items = [it for it in items if rules.rain_shoe_ok(it)] or items
        if zone == "Верхняя одежда":
            if not weather_ctx.get("has_rain"):
                items = [it for it in items if not _is_raincoat(it)]
            too_warm_for_outer = (weather_ctx.get("tmax") or 0) > WARDROBE_OUTERWEAR_MAX_TEMP
            outerwear_needed = rules.needs_outerwear(weather_ctx) or not too_warm_for_outer
            if not outerwear_needed:
                candidates[zone] = []
                continue
            if weather_ctx.get("has_rain"):
                # Есть непромокаемая — только она.
                items = [it for it in items if it.get("rain_ok")] or items
            elif weather_ctx.get("strong_wind"):
                # Порывы: есть непродуваемая — только она.
                items = [it for it in items if it.get("wind_ok") or it.get("rain_ok")] or items
        elif zone == "Аксессуары" and (weather_ctx.get("hot") or weather_ctx.get("sunny")):
            # Очки могут лежать дальше первых двух аксессуаров, которые попадут
            # в перебор, поэтому поднимаем их до ограничения пула кандидатов.
            items = sorted(items, key=lambda item: not _is_sunglasses(item))
        elif zone == "Низ" and weather_ctx.get("has_rain"):
            # В дождь светлые брюки — в конец очереди: брызги на них заметнее.
            items = sorted(items, key=rules.is_light)
        elif zone == "Низ" and _shorts_weather(weather_ctx):
            # При устойчивой жаре шорты должны дойти до конечного скоринга,
            # даже если брюки были добавлены в шкаф раньше.
            items = sorted(items, key=lambda item: not _is_shorts(item))
        candidates[zone] = items
    return candidates


def _is_neutral_color(color):
    c = str(color or "").lower()
    return any(p in c for p in NEUTRAL_COLORS)


def outfit_style_score(items, style):
    """Насколько конкретный комплект соответствует одному из шести UI-стилей."""
    style = str(style or "").casefold()
    facts = " ".join(_item_facts(item) for item in items)
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
    elif style == _CITY_STYLE:
        score += sum(urban_2026_item_score(item) for item in items)
    elif style in ("повседневный", "классический") and any(marker in facts for marker in ("sport", "спортив", "джоггер")):
        score -= 3
    return score


# ---------- подсчёт образов шкафа (статистика «Твой шкаф · 37 вещей · 528 образов») ----------
# Два представительных дня: тёплый (без верхней одежды) и холодный (верхняя
# одежда обязательна). Жёсткие фильтры — те же, что у подбора образа дня.
_COUNT_DAYS = (({"tmax": 22, "warm": True}, False), ({"tmax": 2}, True))
_MAX_BRIGHT_COLORS = 2  # три ярких цвета — уже не образ


def _bright_count(item):
    return min(_MAX_BRIGHT_COLORS + 1, sum(not _is_neutral_color(c) for c in (item.get("colors") or [])))


def _count_pools(w, weather_ctx, cold):
    candidates = select_outfit_candidates(w, weather_ctx)
    pools = [
        [it for it in candidates.get("Верх", []) if outfit_role(it) not in ("layer", "outerwear")],
        candidates.get("Низ", []),
        candidates.get("Обувь", []),
    ]
    if cold:
        pools.append([it for it in candidates.get("Верхняя одежда", []) if outfit_role(it) == "outerwear"])
    return pools


def count_outfits(w):
    """Точное число полных образов шкафа: верх + низ + обувь (+ верхняя одежда
    в холод) без конфликта по погоде и не больше двух ярких цветов.

    Считается по гистограмме ярких цветов, без перебора комбинаций, поэтому
    результат точный и для большого шкафа.
    """
    total = 0
    for weather_ctx, cold in _COUNT_DAYS:
        dist = {0: 1}
        for pool in _count_pools(w, weather_ctx, cold):
            nxt = {}
            for bright, count in dist.items():
                for item in pool:
                    value = bright + _bright_count(item)
                    if value <= _MAX_BRIGHT_COLORS:
                        nxt[value] = nxt.get(value, 0) + count
            dist = nxt
        total += sum(dist.values())
    return total


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
    # В прохладную мягкую погоду рубашка поверх футболки — полезный вариант,
    # но при +20…+22 однослойный комплект снова остаётся конкурентным.
    if tmax is not None and 14 <= tmax < 20 and _has_light_shirt_layer(items):
        score += 2
    # Прохладно: кофта поверх футболки получает приоритет.
    low = rules.feels_low(weather_ctx)
    if low is not None and low < rules.SWEATER_FEELS_MAX and any(it.get("zone") == "Кофты" for it in items):
        score += 2
    # Холодное утро и тёплый день: нужен слой, который можно снять.
    if weather_ctx.get("layering") and (
        _has_light_shirt_layer(items) or any(it.get("zone") in ("Верхняя одежда", "Кофты") for it in items)
    ):
        score += 3
    for it in items:
        tr = it.get("temp_range")
        # Бонус «по погоде» — только верху, низу и обуви: кофта и куртка добавляются, когда их требует погода
        # (бонусы выше и обязательная верхняя одежда), а не потому, что ещё одна вещь даёт ещё +5.
        if tr and tmax is not None and tr[0] <= tmax <= tr[1] and outfit_role(it) not in ("layer", "outerwear"):
            score += 5
        if it.get("zone") == "Аксессуары":
            # Подходящий аксессуар должен выигрывать у пустого optional-слота;
            # очки получают дополнительный приоритет в солнечную или жаркую погоду.
            score += 2
            if (weather_ctx.get("hot") or weather_ctx.get("sunny")) and _is_sunglasses(it):
                score += 5
        if _shorts_weather(weather_ctx) and _is_shorts(it):
            # Это сильнее цветового тай-брейкера, но не отменяет защиту от
            # дождя и ветра, историю ношения или явные ограничения пользователя.
            score += 8
    score += rules.harmony_score(items)
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


def _top_candidates(items, limit=3, selected_styles=None):
    """Ограничивает размер зоны до перебора комбинаций (см. _combos) без
    предвзятости по частоте ношения — какая вещь выигрывает, решает score_outfit
    (цельность образа), а не то, что вещь ещё не использовалась.

    Для активного городского стиля сначала поднимаем актуальные свободные и
    утилитарные вещи. Иначе четвёртая-пятая вещь в категории не дошла бы до
    конечного скоринга вовсе.
    """
    if not _city_style_selected(selected_styles):
        return items[:limit]
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (-urban_2026_item_score(pair[1]), pair[0]),
    )
    return [item for _index, item in ranked[:limit]]


TOP_OUTFITS = 6  # столько разных комплектов видит ИИ-стилист
_POOL_WIDTH, _POOL_WIDE = 3, 5  # вещей зоны в переборе; второй проход — если в первом все комплекты нелогичны


FRESH_DAYS = 2           # образы этих дней ИИ-стилист не повторяет
FRESH_MAX_SHARED = 1     # столько общих вещей с недавним образом ещё не считается повтором


def fresh_outfits(outfits, wardrobe_history, today=None):
    """Комплекты без повтора образов последних FRESH_DAYS дней; маленькому шкафу — все.

    ИИ-стилист выбирает «самый стильный» и без этого фильтра каждый день брал бы
    один и тот же любимый комплект, игнорируя антиповтор скоринга.
    """
    today = today or datetime.now(config.TZ).date()
    cutoff = (today - timedelta(days=FRESH_DAYS)).isoformat()
    recent = [set(e.get("item_ids") or []) for e in wardrobe_history or [] if e.get("date", "") >= cutoff]
    fresh = [o for o in outfits
             if all(len({it.get("id") for it in o} & worn) <= FRESH_MAX_SHARED for worn in recent)]
    return fresh or list(outfits)


def pick_best_outfit(w, weather_ctx, wardrobe_history, prefs_text, previous_item_ids=None,
                     selected_styles=None):
    """Лучший полный набор вещей (list[item]) или None, если нет кандидатов
    для базовой части образа (верх/низ/обувь)."""
    best = top_outfits(w, weather_ctx, wardrobe_history, prefs_text, previous_item_ids,
                       selected_styles, limit=1)
    return best[0] if best else None


def top_outfits(w, weather_ctx, wardrobe_history, prefs_text, previous_item_ids=None,
                selected_styles=None, limit=TOP_OUTFITS, anchor_id=None):
    """До limit лучших разных полных комплектов, лучший первым.

    Перебирает ограниченные комбинации (топ-3 на зону) из вещей, прошедших
    жёсткие правила погоды; соседние варианты отличаются хотя бы одной вещью.
    anchor_id — вещь из «Собрать образ»: она есть в каждом комплекте, даже если
    погода для неё не идеальна, остальное подбирается под неё.
    """
    candidates = select_outfit_candidates(w, weather_ctx)
    anchor_zone, anchor = next(((zone, item) for zone, _sub, item in _flat_wardrobe_items(w)
                                if anchor_id and item.get("id") == anchor_id), (None, None))
    if anchor_id and anchor_zone not in ("Верх", "Кофты", "Низ", "Обувь", "Верхняя одежда"):
        return []
    required = ["Верх", "Низ", "Обувь"]
    if any(not candidates.get(z) and z != anchor_zone for z in required):
        return []

    def _zone_items(zone):
        """Вещи зоны; с закреплённой вещью — сначала те, что с ней не спорят (с флисом — не рубашки)."""
        items = candidates.get(zone) or []
        if anchor is None:
            return items
        return [it for it in items if not outfit_conflicts([anchor, it])] or items

    def _pool(zone, top):
        return [anchor] if zone == anchor_zone else top

    def _combos(width):
        import itertools
        pools = [_pool(z, _top_candidates(_zone_items(z), limit=width, selected_styles=selected_styles))
                 for z in required]
        optional_zones = [z for z in ("Кофты", "Верхняя одежда") if candidates.get(z) or z == anchor_zone]
        for zone in optional_zones:
            top = _top_candidates(_zone_items(zone), limit=width - 1, selected_styles=selected_styles)
            # В дождь, ветер и прохладу верхняя одежда обязательна, если она есть в шкафу.
            mandatory = zone == "Верхняя одежда" and rules.needs_outerwear(weather_ctx)
            pools.append(_pool(zone, top if mandatory else [None] + top))
        for combo in itertools.product(*pools):
            yield [it for it in combo if it is not None]

    def _complete(width):
        # Финальная программная проверка комплектности: жилет, кардиган или
        # overshirt не может стать единственным верхом. Если базовый верх есть в
        # шкафу, добавляем его в вариант и только затем считаем общий score.
        built = [
            complete
            for combo in _combos(width)
            for complete in _completed_layered_combos(combo, candidates, selected_styles)
        ]
        built = _with_light_shirt_layers(built, candidates, weather_ctx, selected_styles)
        if anchor:
            built = [combo for combo in built if any(it.get("id") == anchor_id for it in combo)]
        return _least_conflicts(built)

    previous_item_ids = set(previous_item_ids or [])
    combos = _complete(_POOL_WIDTH)
    if combos and (outfit_conflicts(combos[0]) or _soft_conflicts(combos[0])):
        # В топ-3 зон логичного комплекта нет — шире: четвёртая-пятая вещь может его дать.
        combos = _complete(_POOL_WIDE)
    if anchor and combos and outfit_conflicts(combos[0]):
        # «Собрать образ» с вещью: нелогичный комплект (худи поверх рубашки) хуже честного «не собрать».
        return []
    if previous_item_ids:
        # «Новый образ» должен менять основу комплекта, а не одну случайную вещь:
        # минимум две замены; маленькому шкафу оставляем честную одну замену.
        previous_was_layered = any(
            _has_light_shirt_layer(combo)
            and {it.get("id") for it in combo if it.get("id")} == previous_item_ids
            for combo in combos
        )

        def changed_enough(combo, min_changes):
            changes = len(previous_item_ids - {it.get("id") for it in combo})
            return changes >= min_changes or (
                previous_was_layered != _has_light_shirt_layer(combo) and changes >= 1)

        combos = ([combo for combo in combos if changed_enough(combo, 2)]
                  or [combo for combo in combos if changed_enough(combo, 1)])
    scored = [(
        score_outfit(combo, weather_ctx, wardrobe_history, prefs_text, selected_styles), combo,
    ) for combo in combos]
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored[0][0] <= -50:
        # Похоже, единственный приемлемый вариант — это тот самый 7-дневный повтор
        # (guard). Пересчитываем без 7-дневного штрафа — маленький гардероб важнее антиповтора.
        rescored = [(score_outfit(combo, weather_ctx, [
            e for e in wardrobe_history
            if e.get("date", "") >= (datetime.now(config.TZ) - timedelta(days=3)).date().isoformat()
        ], prefs_text, selected_styles), combo) for _s, combo in scored]
        rescored.sort(key=lambda x: x[0], reverse=True)
        scored = rescored
    picked = []
    for _score, combo in scored:
        ids = {it.get("id") for it in combo}
        if is_complete_outfit(combo) and all(
            len(ids ^ {it.get("id") for it in other}) >= 2 for other in picked
        ):
            picked.append(combo)
            if len(picked) >= limit:
                break
    return picked


# ---------- текст образа: локальный fallback ----------
_OPENABLE_LAYER_MARKERS = ("рубаш", "куртк", "пиджак", "кардиган", "пальто", "плащ", "ветровк")


def _has_confirmed_long_sleeves(item):
    text = f"{item.get('name', '')} {item.get('subcategory', '')}".casefold()
    if "коротк" in text and "рукав" in text:
        return False
    return "лонгслив" in text or ("длинн" in text and "рукав" in text)


def _tip_key(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _tip_order(items, candidates):
    """Детерминированно меняет порядок нейтральных советов между комплектами."""
    fingerprint = "|".join(
        str(item.get("id") or item.get("name") or "")
        for item in items
    ).encode("utf-8")
    offset = int(hashlib.sha256(fingerprint).hexdigest()[:8], 16) % len(candidates)
    return candidates[offset:] + candidates[:offset]


def build_style_tip(items, weather_ctx=None, avoid_tips=None):
    """Один разнообразный совет по носке, использующий только вещи из items.

    ``avoid_tips`` нужен для явного «Другого образа»: новый комплект не должен
    получать тот же финальный совет, если есть безопасная альтернатива.
    """
    weather_ctx = weather_ctx or {}
    avoided = {_tip_key(tip) for tip in (avoid_tips or ()) if _tip_key(tip)}

    def first_available(candidates):
        return next((tip for tip in candidates if _tip_key(tip) not in avoided), None)

    outer = next((it for it in items if it.get("zone") == "Верхняя одежда"), None)
    if outer and weather_ctx.get("warm") and any(
        marker in str(outer.get("name") or "").casefold() for marker in _OPENABLE_LAYER_MARKERS
    ):
        tip = first_available(["Оставь верхний слой расстёгнутым, чтобы силуэт выглядел легче."])
        if tip:
            return tip
    sleeved = next((it for it in items
                    if it.get("zone") == "Верх" and _has_confirmed_long_sleeves(it)),
                   None)
    if sleeved:
        tip = first_available(["Подверни рукава до середины предплечья, чтобы образ выглядел легче."])
        if tip:
            return tip
    long_bottom = next((it for it in items
                        if it.get("zone") == "Низ"
                        and "длинн" in str(it.get("name") or "").casefold()), None)
    if long_bottom:
        tip = first_available(["Подверни низ на один оборот, чтобы открыть обувь и облегчить силуэт."])
        if tip:
            return tip
    accessory = next((it for it in items if it.get("zone") == "Аксессуары"), None)
    if accessory:
        tip = first_available(["Оставь один заметный аксессуар, чтобы образ выглядел собраннее."])
        if tip:
            return tip

    ordered = _tip_order(list(items), list(SAFE_NEUTRAL_STYLE_TIPS))
    return first_available(ordered) or ordered[0]


def build_how_to_wear(items, style_tip=""):
    """До трёх коротких действий только для вещей из готового комплекта."""
    actions = []
    tops = [item for item in items if item.get("zone") == "Верх"]
    shirt = next((item for item in tops if "рубаш" in _item_facts(item)), None)
    base = next((item for item in tops if item is not shirt and outfit_role(item) == "base_top"), None)

    if shirt and base:
        actions.append("Рубашку полностью расстегнуть")
    if shirt and _has_confirmed_long_sleeves(shirt):
        actions.append("Рукава слегка подвернуть")
    if base and any(marker in _item_facts(base) for marker in ("футболк", "майк", "лонгслив")):
        base_facts = _item_facts(base)
        garment = "Футболку" if "футболк" in base_facts else "Майку" if "майк" in base_facts else "Лонгслив"
        actions.append(f"{garment} оставить навыпуск")

    if not actions and style_tip:
        action = re.split(r",?\s+чтобы\s+", str(style_tip).strip(), maxsplit=1, flags=re.IGNORECASE)[0]
        action = action.rstrip(". ")
        if action:
            actions.append(action[:1].upper() + action[1:])
    return actions[:3]


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
