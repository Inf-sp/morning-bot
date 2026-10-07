"""Чистая логика «💳 Что докупить»: разбор шкафа, кандидаты и +N образов.

Без store, AI, сети и Telegram: вызывающий код передаёт шкаф, сезон, историю
отказов и ответ AI как данные. Все числа считает движок образов.
"""

import hashlib
import re

import recommendation_rotation as rotation
from ui.text import ru_plural
from wardrobe_model import ZONE_SUBCATS, flat_items, normalize_parsed_item, public_item_name, zone_of
from wardrobe_outfit import _claims_are_grounded, count_outfits, outfit_role, outfits_with_item

PROFILE_KEY = "wardrobe_purchase_screen"
MIN_ITEMS = 5
BATCH_SIZE = 3
# Зона считается закрытой, когда вещей в ней уже достаточно для разнообразия.
_ZONE_COVERED = {"Верх": 12, "Низ": 8, "Обувь": 6, "Верхняя одежда": 4, "Аксессуары": 6}
_SUBCATEGORY_COVERED = 3
_WARM_OUTER_MARKERS = ("пуховик", "пальто", "парк", "утепл", "дублён", "дублен", "шуб")
_COLOR_MARKERS = (
    "бел", "чёрн", "черн", "сер", "беж", "син", "голуб", "красн", "зелён", "зелен",
    "корич", "бордов", "жёлт", "желт", "оранж", "розов", "фиолет", "олив", "молочн",
    "графит", "хаки", "тёмн", "темн", "светл", "кремов", "песоч",
)
_TIP_BY_ZONE = {
    "Обувь": "Бери гладкую кожу без крупных логотипов — так пара подойдёт и к брюкам, и к джинсам.",
    "Низ": "Выбирай посадку, которая не спорит с объёмом твоего верха.",
    "Верх": "Бери однотонный вариант — он проще сочетается с тем, что уже есть.",
    "Верхняя одежда": "Примеряй поверх самого объёмного свитера — так слой точно сядет в холод.",
    "Аксессуары": "Выбирай цвет, который уже есть в обуви или ремне, — так деталь свяжет образ.",
}
_TIP_FORBIDDEN_RE = re.compile(r"\d|₽|€|\$|руб|цен|скидк|бренд", re.IGNORECASE)

# Базовые идеи без AI: явная зона, цвет и тепло, чтобы честно посчитать +N.
BASIC_IDEAS = (
    {"item": "Белые кожаные кеды", "zone": "Обувь", "subcategory": "Кеды", "color": "белые", "markers": ("бел", "кед")},
    {"item": "Тёмно-синий пуховик", "zone": "Верхняя одежда", "subcategory": "Пуховики", "color": "тёмно-синий",
     "warmth": "тёплые", "markers": ("пуховик",)},
    {"item": "Серое шерстяное пальто", "zone": "Верхняя одежда", "subcategory": "Пальто", "color": "серое",
     "warmth": "тёплые", "markers": ("пальто",)},
    {"item": "Тёмно-коричневые кожаные ботинки", "zone": "Обувь", "subcategory": "Ботинки",
     "color": "тёмно-коричневые", "markers": ("ботин",)},
    {"item": "Серые шерстяные брюки", "zone": "Низ", "subcategory": "Брюки", "color": "серые", "markers": ("сер", "брюк")},
    {"item": "Синие прямые джинсы", "zone": "Низ", "subcategory": "Джинсы", "color": "синие", "markers": ("джинс",)},
    {"item": "Белая базовая футболка", "zone": "Верх", "subcategory": "Футболки", "color": "белая",
     "markers": ("бел", "футболк")},
    {"item": "Серый трикотажный джемпер", "zone": "Верх", "subcategory": "Свитеры", "color": "серый",
     "markers": ("джемпер",)},
    {"item": "Бежевые льняные брюки", "zone": "Низ", "subcategory": "Брюки", "color": "бежевые",
     "warmth": "лёгкие", "markers": ("льнян",)},
    {"item": "Тёмно-синяя непромокаемая ветровка", "zone": "Верхняя одежда", "subcategory": "Ветровки",
     "color": "тёмно-синяя", "markers": ("ветровк",)},
)


def item_key(value):
    return rotation.identity(value)


def item_id(name):
    """Короткий стабильный id для callback_data — без названия вещи."""
    return hashlib.sha1(item_key(name).encode("utf-8")).hexdigest()[:8]


def is_cold_season(month, lat=None):
    """Ближайший сезон холодный: осень–зима (в южном полушарии — наоборот)."""
    try:
        south = float(lat) < 0
    except (TypeError, ValueError):
        south = False
    month = (int(month) + 5) % 12 + 1 if south else int(month)
    return month >= 9 or month <= 2


def _items(wardrobe):
    """Вещи шкафа с зоной из структуры (у старых вещей поля zone может не быть)."""
    return [
        item if item.get("zone") == zone else {**item, "zone": zone}
        for zone, _subcat, item in flat_items(wardrobe) if isinstance(item, dict)
    ]


def _colors(item):
    return [str(c) for c in (item.get("colors") or [item.get("color")]) if str(c or "").strip()]


def _is_warm_outer(item):
    facts = f"{item.get('name', '')} {item.get('subcategory', '')}".casefold()
    return item.get("warmth") == "тёплые" or any(marker in facts for marker in _WARM_OUTER_MARKERS)


def wardrobe_facts(wardrobe, *, cold_season):
    """Проверяемые числа шкафа: на них опираются анализ, AI-промпт и «Почему тебе»."""
    from wardrobe_outfit import _is_neutral_color

    items = _items(wardrobe)
    zone = lambda it: it.get("zone")
    tops = [it for it in items if zone(it) == "Верх" and outfit_role(it) not in ("layer", "outerwear")]
    outer = [it for it in items if zone(it) == "Верхняя одежда"]
    colors = [color for it in items for color in _colors(it)]
    neutral = sum(_is_neutral_color(color) for color in colors)
    return {
        "total": len(items),
        "tops": len(tops),
        "bottoms": sum(zone(it) == "Низ" for it in items),
        "shoes": sum(zone(it) == "Обувь" for it in items),
        "outer": len(outer),
        "accessories": sum(zone(it) in ("Аксессуары", "Другое") for it in items),
        "warm_outer": any(_is_warm_outer(it) for it in outer),
        "light": any(
            it.get("warmth") == "лёгкие" or it.get("subcategory") == "Шорты"
            for it in items if zone(it) in ("Верх", "Низ")
        ),
        "colored": len(colors),
        "neutral_share": round(neutral / len(colors), 2) if colors else None,
        "cold_season": bool(cold_season),
        "outfits": count_outfits(wardrobe),
    }


def _pairs(n):
    return f"{n} {ru_plural(n, 'пара', 'пары', 'пар')} обуви"


def analysis(facts):
    """1–2 сильные и слабые стороны по детерминированным правилам."""
    f = facts
    share = f.get("neutral_share")
    enough_colors = f.get("colored", 0) >= 5
    strengths = [text for ok, text in (
        (f["tops"] >= 6, "много базового верха"),
        (f["bottoms"] >= 4, "хватает низа на разные образы"),
        (f["shoes"] >= 3, "обувь на разные случаи"),
        (f["cold_season"] and f["warm_outer"], "есть тёплая верхняя одежда к зиме"),
        (enough_colors and share is not None and share >= 0.7, "спокойная палитра — вещи легко сочетаются"),
        (f["accessories"] >= 3, "есть аксессуары для акцентов"),
    ) if ok]
    bottoms = f["bottoms"]
    weaknesses = [text for ok, text in (
        (f["shoes"] == 0, "нет обуви"),
        (0 < f["shoes"] <= 2, f"{_pairs(f['shoes'])} на все случаи"),
        (f["cold_season"] and not f["warm_outer"], "нет тёплой куртки к зиме"),
        (not f["cold_season"] and not f["light"], "нет лёгких вещей к лету"),
        (bottoms == 0, "нет низа"),
        (0 < bottoms <= 2, f"только {bottoms} {ru_plural(bottoms, 'низ', 'низа', 'низов')}"),
        (f["tops"] <= 2, "мало верха"),
        (enough_colors and share is not None and share <= 0.3, "мало нейтральной базы"),
        (enough_colors and share == 1.0, "нет цветных акцентов"),
        (f["accessories"] == 0, "нет аксессуаров"),
    ) if ok]
    return {
        "total": f["total"],
        "outfits": f["outfits"],
        "strengths": strengths[:2],
        "weaknesses": weaknesses[:2],
    }


def _color_from_name(name):
    first = str(name or "").split(" ", 1)[0].casefold()
    return first if any(marker in first for marker in _COLOR_MARKERS) else ""


def wardrobe_item(candidate):
    """Вещь шкафа в формате обычного добавления: зона, подкатегория, цвет, тепло."""
    return normalize_parsed_item({
        "name": candidate.get("item"),
        "zone": candidate.get("zone"),
        "subcategory": candidate.get("subcategory"),
        "color": candidate.get("color") or _color_from_name(candidate.get("item")),
        "warmth": candidate.get("warmth") or "",
        "style": "Повседневный",
    })


def _with_item(wardrobe, item):
    zones = dict((wardrobe or {}).get("zones") or {})
    subcats = dict(zones.get(item["zone"]) or {})
    subcats[item["subcategory"]] = [*(subcats.get(item["subcategory"]) or []), item]
    zones[item["zone"]] = subcats
    return {**(wardrobe or {}), "zones": zones}


def _clean(value, limit):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _normalize_candidate(raw, source):
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("item"), 60).strip(" .")
    if not name:
        return None
    zone = next((value for value in (raw.get("zone"), raw.get("category"))
                 if value in ZONE_SUBCATS and value != "Другое"), None) or zone_of(name)
    if zone == "Другое":
        return None
    candidate = {
        "id": item_id(name),
        "item": name[:1].upper() + name[1:],
        "zone": zone,
        "subcategory": raw.get("subcategory") if raw.get("subcategory") in ZONE_SUBCATS[zone] else "",
        "color": _clean(raw.get("color"), 30),
        "warmth": raw.get("warmth") if raw.get("warmth") in ("лёгкие", "обычные", "тёплые") else "",
        "why": _clean(raw.get("why"), 240),
        "tip": _clean(raw.get("tip"), 160),
        "source": source,
    }
    item = wardrobe_item(candidate)
    if not item:
        return None
    candidate["subcategory"] = item["subcategory"]
    return candidate


def _owned(wardrobe_items, candidate, markers=()):
    names = {item_key(public_item_name(it)) for it in wardrobe_items}
    if item_key(candidate["item"]) in names or item_key(wardrobe_item(candidate)["name"]) in names:
        return True
    return bool(markers) and any(
        all(marker in item_key(it.get("name")) for marker in markers) for it in wardrobe_items
    )


def _covered(wardrobe_items, candidate):
    in_zone = [it for it in wardrobe_items if it.get("zone") == candidate["zone"]]
    if len(in_zone) >= _ZONE_COVERED.get(candidate["zone"], 99):
        return True
    subcat = candidate["subcategory"]
    return subcat != "Другое" and sum(it.get("subcategory") == subcat for it in in_zone) >= _SUBCATEGORY_COVERED


def rank_candidates(wardrobe, ai_items=(), local_items=()):
    """Все кандидаты с +N образов: AI → локальные → базовые, сортировка по N.

    Уже имеющиеся вещи и хорошо закрытые категории исключаются. Отказы
    применяются при показе, чтобы «❌ Не нужно» не требовал пересборки.
    """
    items = _items(wardrobe)
    base = count_outfits(wardrobe)
    sources = (
        *((raw, "ai") for raw in ai_items or ()),
        *((raw, "local") for raw in local_items or ()),
        *((raw, "basic") for raw in BASIC_IDEAS),
    )

    def collect(skip_covered):
        ranked, seen = [], set()
        for raw, source in sources:
            candidate = _normalize_candidate(raw, source)
            if not candidate or candidate["id"] in seen:
                continue
            seen.add(candidate["id"])
            markers = raw.get("markers") if source == "basic" else ()
            # AI-идеи персональны: «закрытая категория» отсекает только шаблонные.
            if _owned(items, candidate, markers) or (
                    skip_covered and source != "ai" and _covered(items, candidate)):
                continue
            candidate["gain"] = count_outfits(_with_item(wardrobe, wardrobe_item(candidate))) - base
            ranked.append(candidate)
        ranked.sort(key=lambda candidate: -candidate["gain"])
        return ranked

    # В большом шкафу все категории «закрыты» — экран не должен остаться пустым:
    # тогда берём недостающие вещи с реальным приростом образов.
    return collect(True) or [c for c in collect(False) if c["gain"] > 0]


def rejected_names(profile):
    """История отказов: {"items": [...]} или старый плоский список."""
    value = (profile or {}).get("wardrobe_purchase_rejections") or {}
    values = value.get("items") if isinstance(value, dict) else value
    return [name for name in (values or []) if isinstance(name, str)]


def visible_pool(pool, rejected):
    blocked = rotation.markers(rejected, key=item_key)
    return [c for c in pool or [] if isinstance(c, dict) and item_key(c.get("item")) not in blocked]


def next_batch(pool, seen_names, current_ids, size=BATCH_SIZE):
    """Следующие варианты без показанных; после полного круга — новый цикл.

    Возвращает (batch, cycle_reset). Текущие карточки первыми не возвращаются.
    """
    seen = rotation.markers(seen_names, key=item_key)
    current = set(current_ids or ())
    others = [c for c in pool if c["id"] not in current]
    fresh = [c for c in others if item_key(c["item"]) not in seen]
    if len(fresh) >= size:
        return fresh[:size], False
    batch = (fresh + [c for c in others if c not in fresh])[:size]
    return (batch or pool[:size]), True


def _grounded_why(why, wardrobe, candidate, facts):
    if not why:
        return ""
    allowed = {str(value) for value in facts.values() if isinstance(value, int) and not isinstance(value, bool)}
    if any(number not in allowed for number in re.findall(r"\d+", why)):
        return ""
    return why if _claims_are_grounded(why, [*_items(wardrobe), wardrobe_item(candidate)]) else ""


def _fallback_why(candidate, facts):
    tops = f"{facts['tops']} {ru_plural(facts['tops'], 'верх', 'верха', 'верхов')}"
    bottoms = f"{facts['bottoms']} {ru_plural(facts['bottoms'], 'низ', 'низа', 'низов')}"
    zone = candidate["zone"]
    if zone == "Обувь":
        return f"На {tops} и {bottoms} сейчас {_pairs(facts['shoes'])} — новая пара сразу даст свежие сочетания."
    if zone == "Низ":
        return f"Сейчас {tops} и только {bottoms} — новый низ раскроет уже имеющийся верх."
    if zone == "Верх":
        return f"Сейчас {tops} на {bottoms} — ещё один верх освежит привычные комплекты."
    count = facts["outer"]
    if zone == "Верхняя одежда" and facts["cold_season"] and not facts["warm_outer"]:
        if not count:
            return "Верхней одежды в шкафу нет — без неё к зиме не собрать ни одного образа."
        return (f"Тёплой верхней одежды нет: на холод сейчас только {count} "
                f"{ru_plural(count, 'вещь', 'вещи', 'вещей')} без утепления.")
    if zone == "Верхняя одежда":
        return f"Верхней одежды сейчас {count} {ru_plural(count, 'вещь', 'вещи', 'вещей')} — новый слой добавит образов на прохладные дни."
    count = facts["accessories"]
    return f"Аксессуаров сейчас {count} — одна деталь соберёт уже имеющиеся комплекты."


def card(wardrobe, candidate, facts):
    """Данные карточки покупки: всё посчитано по текущему шкафу."""
    item = wardrobe_item(candidate)
    before = count_outfits(wardrobe)
    tip = candidate.get("tip") or ""
    if not tip or _TIP_FORBIDDEN_RE.search(tip):
        tip = _TIP_BY_ZONE.get(candidate["zone"], "")
    return {
        "name": candidate["item"],
        "why": _grounded_why(candidate.get("why"), wardrobe, candidate, facts) or _fallback_why(candidate, facts),
        "before": before,
        "after": count_outfits(_with_item(wardrobe, item)),
        "outfits": outfits_with_item(wardrobe, item, limit=3),
        "tip": tip,
    }


def ai_facts_text(facts, analysis_data):
    """Короткие факты для AI-промпта; числа берутся только отсюда."""
    return (
        f"вещей: {facts['total']}; верх: {facts['tops']}; низ: {facts['bottoms']}; "
        f"обувь: {facts['shoes']}; верхняя одежда: {facts['outer']}; аксессуары: {facts['accessories']}; "
        f"тёплая верхняя одежда: {'есть' if facts['warm_outer'] else 'нет'}; "
        f"ближайший сезон: {'холодный' if facts['cold_season'] else 'тёплый'}; "
        f"слабые места: {', '.join(analysis_data['weaknesses']) or 'нет'}"
    )
