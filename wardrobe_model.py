"""Чистая схема гардероба и нормализация вещей."""

ZONE_SUBCATS = {
    "Верх": ["Футболки", "Поло", "Рубашки", "Лонгсливы", "Свитеры", "Кардиганы", "Худи", "Пиджаки", "Другое"],
    "Верхняя одежда": ["Ветровки", "Куртки", "Пальто", "Пуховики", "Плащи", "Другое"],
    "Низ": ["Джинсы", "Брюки", "Чиносы", "Шорты", "Спортивные брюки", "Другое"],
    "Обувь": ["Кеды", "Кроссовки", "Лоферы", "Ботинки", "Сандалии", "Тапочки", "Другое"],
    "Аксессуары": ["Кепки", "Шапки", "Ремни", "Часы", "Очки", "Украшения", "Шарфы", "Перчатки", "Сумки", "Рюкзаки", "Носки", "Другое"],
    "Другое": ["Другое"],
}
ZONE_ORDER = ["Верх", "Низ", "Верхняя одежда", "Обувь", "Аксессуары", "Другое"]

ZONES = [
    ("Верхняя одежда", ["верхняя одежд", "верхн", "куртк", "ветровк", "пиджак", "пальто", "плащ", "дождевик", "парк", "пуховик", "тренч", "анорак", "бомбер", "жилет"]),
    ("Верх", ["верх", "футбол", "рубаш", "свит", "толстов", "худи", "лонгслив", "поло", "майк", "кофт"]),
    ("Низ", ["низ", "джинс", "брюк", "штан", "шорт", "юбк"]),
    ("Обувь", ["обув", "кроссов", "ботин", "кед", "туфл", "сандал"]),
    ("Аксессуары", ["аксессуар", "часы", "кольц", "ремен", "шапк", "кепк", "очк", "шарф", "сумк", "цепоч", "носк", "украшен"]),
]

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
    name = str(raw["name"]).strip()
    zone = raw.get("zone") if raw.get("zone") in ZONE_SUBCATS else zone_of(name)
    subcategory = raw.get("subcategory")
    if subcategory not in ZONE_SUBCATS.get(zone, []):
        subcategory = guess_subcategory(zone, name)
    return {
        "zone": zone, "subcategory": subcategory, "name": name,
        "color": str(raw.get("color") or "").strip(),
        "color_secondary": (str(raw["color_secondary"]).strip() or None) if raw.get("color_secondary") else None,
        "material": (str(raw["material"]).strip() or None) if raw.get("material") else None,
        "style": str(raw.get("style") or "").strip() or None,
        "fit": str(raw.get("fit") or "").strip() or None,
        "season": [str(x).strip() for x in (raw.get("season") or []) if str(x).strip()]
                  if isinstance(raw.get("season"), list) else [],
        "occasions": [str(x).strip() for x in (raw.get("occasions") or []) if str(x).strip()]
                     if isinstance(raw.get("occasions"), list) else [],
    }


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
