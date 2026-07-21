"""Чистая схема гардероба и нормализация вещей."""

import re

ZONE_SUBCATS = {
    "Верх": ["Футболки", "Поло", "Рубашки", "Лонгсливы", "Свитеры", "Кардиганы", "Худи", "Пиджаки", "Другое"],
    "Верхняя одежда": ["Ветровки", "Куртки", "Пальто", "Пуховики", "Плащи", "Другое"],
    "Низ": ["Джинсы", "Брюки", "Чиносы", "Шорты", "Спортивные брюки", "Другое"],
    "Обувь": ["Кеды", "Кроссовки", "Лоферы", "Ботинки", "Сандалии", "Тапочки", "Другое"],
    "Аксессуары": ["Кепки", "Шапки", "Ремни", "Часы", "Очки", "Украшения", "Шарфы", "Перчатки", "Сумки", "Рюкзаки", "Носки", "Другое"],
    "Другое": ["Другое"],
}
ZONE_ORDER = ["Верх", "Низ", "Верхняя одежда", "Обувь", "Аксессуары", "Другое"]
ZONE_PUBLIC_LABELS = {
    "Верх": "Верх",
    "Низ": "Низ",
    "Верхняя одежда": "Верхняя одежда",
    "Обувь": "Обувь",
    "Аксессуары": "Аксессуары",
    "Другое": "Другое",
}
WARMTH_VALUES = ("лёгкие", "обычные", "тёплые")
ATTRIBUTE_SCHEMA_VERSION = 2
WARMTH_TEMP_RANGE = {"лёгкие": [15, 35], "обычные": [5, 26], "тёплые": [-20, 18]}
ZONE_COMPAT = {
    "Верх": ["Низ", "Обувь", "Верхняя одежда", "Аксессуары"],
    "Низ": ["Верх", "Обувь", "Верхняя одежда", "Аксессуары"],
    "Верхняя одежда": ["Верх", "Низ", "Обувь", "Аксессуары"],
    "Обувь": ["Верх", "Низ", "Верхняя одежда", "Аксессуары"],
    "Аксессуары": ["Верх", "Низ", "Верхняя одежда", "Обувь"],
    "Другое": [],
}

ZONES = [
    ("Верхняя одежда", ["верхняя одежд", "верхн", "куртк", "ветровк", "пиджак", "пальто", "плащ", "дождевик", "парк", "пуховик", "тренч", "анорак", "бомбер", "жилет"]),
    ("Верх", ["верх", "футбол", "рубаш", "свит", "толстов", "худи", "лонгслив", "поло", "майк", "кофт"]),
    ("Низ", ["низ", "джинс", "брюк", "штан", "шорт", "юбк"]),
    ("Обувь", ["обув", "кроссов", "ботин", "кед", "туфл", "сандал"]),
    ("Аксессуары", ["аксессуар", "часы", "кольц", "ремен", "шапк", "кепк", "очк", "шарф", "сумк", "цепоч", "носк", "украшен"]),
]


def public_zone_name(zone):
    return ZONE_PUBLIC_LABELS.get(str(zone or ""), str(zone or "Другие вещи"))

SUBCATEGORY_KEYWORDS = {
    "Футболки": ["футболк", "майк"], "Поло": ["поло"], "Рубашки": ["рубаш"], "Лонгсливы": ["лонгслив"],
    "Свитеры": ["свитер", "свитш", "джемпер"], "Кардиганы": ["кардиган"], "Худи": ["худи", "толстовк"], "Пиджаки": ["пиджак"],
    "Ветровки": ["ветровк"], "Куртки": ["куртк", "бомбер", "анорак", "жилет"], "Пальто": ["пальто"], "Пуховики": ["пуховик"], "Плащи": ["плащ", "тренч", "дождевик"],
    "Джинсы": ["джинс"], "Брюки": ["брюк", "штан"], "Чиносы": ["чино"], "Шорты": ["шорт"], "Спортивные брюки": ["спортивн"],
    "Кеды": ["кед"], "Кроссовки": ["кроссов"], "Лоферы": ["лофер"], "Ботинки": ["ботин"], "Сандалии": ["сандал"], "Тапочки": ["тапоч"],
    "Кепки": ["кепк"], "Шапки": ["шапк"], "Ремни": ["ремен", "ремн"], "Часы": ["час"], "Очки": ["очк"],
    "Украшения": ["украшен", "цепоч", "кольц"], "Шарфы": ["шарф"], "Перчатки": ["перчат"], "Сумки": ["сумк"], "Рюкзаки": ["рюкзак"], "Носки": ["носк"],
}

RAIN_OUTER_MARKERS = ("дождевик", "ветровк", "непромокаем", "мембран", "raincoat", "waterproof", "плащ", "тренч", "анорак")

_INTERNAL_TAG_MARKERS = (
    "летн", "зимн", "деми", "всесезон", "casual", "utility", "formal",
    "smart casual", "smart_casual", "streetwear", "sport", "город", "офис",
    "работ", "прогул", "путешеств", "вечер", "повседнев", "свободн",
    "прям", "притал", "оверсайз",
)

_WARM_MARKERS = ("тёпл", "тепл", "утепл", "толст", "плотн", "зимн")
_LIGHT_MARKERS = ("лёгк", "легк", "тонк", "летн")
_PHYSICAL_NAME_RE = re.compile(
    r"\b(?:очень\s+)?(?:т[ёе]пл\w*|утепл[ёе]н\w*|толст\w*|плотн\w*|"
    r"л[ёе]гк\w*|тонк\w*|летн\w*|зимн\w*|демисезонн\w*)\b",
    re.I,
)


def normalize_warmth(value="", source_text=""):
    """Возвращает одно из трёх стабильных значений физического тепла вещи."""
    text = f"{value or ''} {source_text or ''}".casefold()
    if any(marker in text for marker in _WARM_MARKERS):
        return "тёплые"
    if any(marker in text for marker in _LIGHT_MARKERS):
        return "лёгкие"
    return "обычные"


def clean_physical_name(value):
    """Убирает тепло/плотность/сезонность из имени — они хранятся полями."""
    text = _PHYSICAL_NAME_RE.sub(" ", str(value or ""))
    text = re.sub(r"\s*,\s*,+", ", ", text)
    text = re.sub(r"(?:\s*,\s*)+$", "", text)
    return re.sub(r"\s+", " ", text).strip(" ,;.-")


def _tag_values(item):
    if not isinstance(item, dict):
        return []
    values = []
    for key in ("color", "color_secondary", "material", "length", "style", "fit", "formality", "warmth"):
        if item.get(key):
            values.append(str(item[key]).strip().casefold())
    for key in ("colors", "season", "occasions"):
        values.extend(str(value).strip().casefold() for value in (item.get(key) or []) if str(value).strip())
    return values


def _is_internal_tag(value, item=None):
    value = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    if not value:
        return True
    if any(marker in value for marker in _INTERNAL_TAG_MARKERS):
        return True
    return any(value == known or value in known or known in value for known in _tag_values(item))


def strip_internal_tags(value, item=None):
    """Убирает служебные season/style/occasion/fit-теги из скобок.

    Значимые части вроде бренда сохраняются: ``(Nike, город)`` превращается в
    ``(Nike)``, а полностью служебная группа исчезает целиком.
    """
    text = str(value or "")

    def _replace(match):
        parts = [part.strip() for part in re.split(r"[,;·]", match.group(1)) if part.strip()]
        kept = [part for part in parts if not _is_internal_tag(part, item)]
        return f" ({', '.join(kept)})" if kept else ""

    return re.sub(r"\s*\(([^()]*)\)", _replace, text).strip()


def public_item_name(item):
    if not isinstance(item, dict):
        return strip_internal_tags(item)
    return strip_internal_tags(item.get("name") or "", item)


def zone_of(category):
    text = str(category or "").lower()
    for zone, keys in ZONES:
        if any(key in text for key in keys):
            return zone
    return "Другое"


def guess_subcategory(zone, name, fallback_text=""):
    valid = set(ZONE_SUBCATS.get(zone, ["Другое"]))
    for text in (str(name).lower(), str(fallback_text).lower()):
        for subcategory, keys in SUBCATEGORY_KEYWORDS.items():
            if subcategory in valid and any(key in text for key in keys):
                return subcategory
    return "Другое"


def normalize_parsed_item(raw):
    if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
        return None
    raw_name = str(raw["name"]).strip()
    name = clean_physical_name(raw_name) or raw_name
    zone = raw.get("zone") if raw.get("zone") in ZONE_SUBCATS else zone_of(name)
    subcategory = raw.get("subcategory")
    if subcategory not in ZONE_SUBCATS.get(zone, []):
        subcategory = guess_subcategory(zone, name)
    color = str(raw.get("color") or "").strip()
    color_secondary = (str(raw["color_secondary"]).strip() or None) if raw.get("color_secondary") else None
    warmth = normalize_warmth(raw.get("warmth"), f"{raw_name} {raw.get('_source_text') or ''}")
    colors = [str(value).strip() for value in (raw.get("colors") or []) if str(value).strip()]
    if not colors:
        colors = [value for value in (color, color_secondary) if value]
    item = {
        "zone": zone, "subcategory": subcategory, "name": name,
        "color": color,
        "color_secondary": color_secondary,
        "colors": colors,
        "material": (str(raw["material"]).strip() or None) if raw.get("material") else None,
        "length": (str(raw["length"]).strip() or None) if raw.get("length") else None,
        "style": str(raw.get("style") or "").strip() or None,
        "fit": str(raw.get("fit") or "").strip() or None,
        "formality": str(raw.get("formality") or "").strip() or None,
        "warmth": warmth,
        "temp_range": list(WARMTH_TEMP_RANGE[warmth]),
        "season": [str(x).strip() for x in (raw.get("season") or []) if str(x).strip()]
                  if isinstance(raw.get("season"), list) else [],
        "occasions": [str(x).strip() for x in (raw.get("occasions") or []) if str(x).strip()]
                     if isinstance(raw.get("occasions"), list) else [],
        "rain_ok": bool(raw.get("rain_ok")),
        "wind_ok": bool(raw.get("wind_ok")),
        "compatible_categories": list(ZONE_COMPAT.get(zone, [])),
        "last_used": None,
        "use_count": 0,
        "accepted_count": 0,
        "rejected_count": 0,
        "attribute_schema_version": ATTRIBUTE_SCHEMA_VERSION,
    }
    item["name"] = public_item_name(item)
    return item


def flat_items(wardrobe):
    return [(zone, subcategory, item)
            for zone, subcategories in (wardrobe or {}).get("zones", {}).items()
            for subcategory, items in subcategories.items() for item in items]


def wardrobe_stats(wardrobe):
    counts = {zone: 0 for zone in ZONE_ORDER}
    for zone, _subcategory, _item in flat_items(wardrobe):
        counts[zone if zone in counts else "Другое"] += 1
    return sum(counts.values()), counts


def has_rain_outerwear(wardrobe):
    text = " ".join(str(item.get("name") or "") for _zone, _subcategory, item in flat_items(wardrobe)).lower()
    return any(marker in text for marker in RAIN_OUTER_MARKERS)
