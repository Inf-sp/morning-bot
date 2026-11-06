"""Жёсткие правила погоды и сочетаемость цветов для подбора образа.

Правила погоды — фильтр, а не бонус: вещь, которая их не проходит, не попадёт в
образ ни при каком стиле, предпочтениях или ответе AI. Работают и со старым
``weather_ctx`` без почасовых полей (тогда опираются на tmax/tmin).
"""
# ---------- погода ----------
_SHORTS = ("шорт", "shorts", "бермуд")
_OPEN_SHOES = ("сандал", "шлёп", "шлеп", "сланц", "вьетнамк", "эспадрил", "мюли", "slides")
_SLEEVELESS = ("майк", "борцовк", "tank")
_RAIN_BAD_SHOES = ("замш", "suede", "текстил", "канвас", "canvas", "сетк", "mesh", "нубук")
_LIGHT_BOTTOM = ("лён", "лен", "льнян", "linen")

SHORTS_FEELS_MIN = 20   # °C «ощущается» в самый холодный час окна
SWEATER_FEELS_MAX = 18  # °C: кофта поверх футболки — только если холоднее (или холодное утро)
SHORTS_GUST_MAX = 8     # м/с
WARM_LAYER_FEELS_MAX = 12  # °C «ощущается»: холоднее — кофта (свитер, худи) в образе обязательна, если есть в шкафу
WARM_OUTER_FEELS_MAX = 8   # °C: холоднее — не ветровка и не лёгкая куртка, если в шкафу есть теплее
_LIGHT_OUTER = ("ветровк", "дождевик", "windbreaker", "анорак", "overshirt", "оверши", "шакет", "shacket")


def _facts(item):
    return " ".join(str(item.get(key) or "") for key in (
        "name", "subcategory", "material", "style",
    )).casefold()


def _has(item, markers):
    facts = _facts(item)
    return any(marker in facts for marker in markers)


def feels_low(ctx):
    """«Ощущается» в самый холодный час; без почасовых данных — tmin, иначе tmax."""
    for key in ("feels_min", "tmin", "tmax"):
        if ctx.get(key) is not None:
            return float(ctx[key])
    return None


def feels_high(ctx):
    for key in ("feels_max", "tmax"):
        if ctx.get(key) is not None:
            return float(ctx[key])
    return None


def summer_weather(ctx):
    """Можно шорты, открытую обувь и майки: тепло весь день, сухо и без порывов."""
    low = feels_low(ctx)
    gust = ctx.get("gust_max", ctx.get("wind_ms"))
    warm_enough = low is not None and low >= SHORTS_FEELS_MIN
    if ctx.get("feels_min") is None and ctx.get("tmin") is None:
        # Старый контекст без минимума: только явная жара.
        warm_enough = bool(ctx.get("hot"))
    return bool(
        warm_enough and not ctx.get("has_rain") and not ctx.get("strong_wind")
        and (gust is None or float(gust) <= SHORTS_GUST_MAX)
    )


def allowed(item, ctx):
    """Проходит ли вещь жёсткие правила погоды (одна вещь, без учёта комплекта)."""
    zone = item.get("zone")
    summer_only = (
        (zone == "Низ" and _has(item, _SHORTS))
        or (zone == "Обувь" and _has(item, _OPEN_SHOES))
        or (zone == "Верх" and _has(item, _SLEEVELESS))
    )
    if summer_only and not summer_weather(ctx):
        return False
    low, high = feels_low(ctx), feels_high(ctx)
    warmth = item.get("warmth")
    if zone == "Кофты" and low is not None and low >= SWEATER_FEELS_MAX and not ctx.get("layering"):
        return False  # в тепло кофта не нужна
    if zone in ("Верх", "Кофты", "Низ", "Обувь", "Верхняя одежда"):
        if warmth == "тёплые" and (ctx.get("hot") or (high is not None and high >= 22)):
            return False
        if warmth == "лёгкие" and zone in ("Низ", "Обувь") and low is not None and low < 12:
            return False
    if zone == "Низ" and ctx.get("has_rain") and _has(item, _LIGHT_BOTTOM) and low is not None and low < 18:
        return False
    return True


_LIGHT_COLORS = ("бел", "молоч", "кремов", "экрю", "айвори", "светл", "беж", "песоч")


def is_light(item):
    """Светлая вещь: в дождь её лучше не выбирать для низа."""
    return any(marker in main_color(item).casefold() for marker in _LIGHT_COLORS)


def rain_shoe_ok(item):
    return not _has(item, _RAIN_BAD_SHOES)


def colder_than(ctx, limit) -> bool:
    low = feels_low(ctx)
    return low is not None and low < limit


def light_outerwear(item) -> bool:
    """Ветровка, дождевик, лёгкая куртка — не греют в холод."""
    return item.get("warmth") == "лёгкие" or _has(item, _LIGHT_OUTER)


def needs_outerwear(ctx):
    low = feels_low(ctx)
    return bool(ctx.get("has_rain") or ctx.get("strong_wind") or (low is not None and low < 16))


# ---------- цвет ----------
_FAMILIES = (
    ("navy", ("тёмно-син", "темно-син", "нави", "navy", "индиго", "деним", "джинс")),
    ("neutral", ("бел", "молоч", "кремов", "экрю", "айвори", "чёрн", "черн", "сер", "графит",
                 "антрацит", "беж", "песоч", "кэмел", "камел", "тауп", "слонов")),
    ("earth", ("корич", "шоколад", "коньяч", "табач", "хаки", "олив", "горчич", "терракот",
               "ржав", "кирпич", "охр", "мокко")),
    ("cool", ("голуб", "бирюз", "мят", "изумруд", "зелён", "зелен", "фиолет", "лаванд",
              "сирен", "син")),
    ("warm", ("бордов", "винн", "марсал", "красн", "оранж", "жёлт", "желт", "розов", "корал",
              "персик", "фукси", "малин")),
)
_BRIGHT = ("красн", "оранж", "жёлт", "желт", "фукси", "неон", "ярко", "салат", "малин", "лимон")
# Проверенные пары стилистов (семейство/оттенок + семейство/оттенок).
_GOOD_PAIRS = (
    ("navy", "earth"), ("navy", "neutral"), ("earth", "neutral"),
    ("olive", "beige"), ("grey", "burgundy"), ("navy", "camel"), ("olive", "navy"),
    ("brown", "cream"), ("burgundy", "navy"), ("green", "beige"),
)
_SHADE_MARKERS = {
    "olive": ("олив", "хаки"), "beige": ("беж", "песоч", "кремов", "экрю", "молоч"),
    "grey": ("сер", "графит"), "burgundy": ("бордов", "винн", "марсал"),
    "camel": ("кэмел", "камел", "коньяч"), "brown": ("корич", "шоколад", "мокко", "табач"),
    "cream": ("кремов", "молоч", "экрю", "айвори"), "green": ("зелён", "зелен", "изумруд"),
    "navy": ("тёмно-син", "темно-син", "нави", "navy", "индиго"),
}
_CLASH = (("красн", "розов"), ("оранж", "розов"), ("фиолет", "оранж"), ("красн", "оранж"),
          ("красн", "зелён"), ("красн", "зелен"))


def color_family(color):
    text = str(color or "").casefold()
    for family, markers in _FAMILIES:
        if any(marker in text for marker in markers):
            return family
    return "neutral" if not text else "other"


def main_color(item):
    colors = item.get("colors") or [item.get("color")]
    text = str(next((c for c in colors if c), "") or "")
    return text or str(item.get("name") or "")


def _shades(text):
    text = str(text or "").casefold()
    return {shade for shade, markers in _SHADE_MARKERS.items() if any(m in text for m in markers)}


def harmony_score(items):
    """Сочетаемость цветов комплекта: + за базу и проверенные пары, − за спорящие акценты."""
    colors = [main_color(item) for item in items if item.get("zone") != "Аксессуары"]
    if not colors:
        return 0.0
    families = [color_family(color) for color in colors]
    accents = [color for color, family in zip(colors, families) if family in ("cool", "warm", "other")]
    brights = [color for color in colors if any(marker in color.casefold() for marker in _BRIGHT)]
    score = 0.0
    if len(set(families)) == 1:
        score += 2  # тон в тон
    if not accents:
        score += 2  # спокойная нейтральная база
    elif len(accents) == 1:
        score += 3  # база плюс один акцент
    else:
        score -= 3 * (len(accents) - 1)
    if len(brights) >= 2:
        score -= 6
    texts = [color.casefold() for color in colors]
    for first, second in _CLASH:
        if any(first in a for a in texts) and any(second in b for b in texts):
            score -= 5
    family_set, shade_set = set(families), set().union(*(_shades(c) for c in colors))
    for first, second in _GOOD_PAIRS:
        pool = family_set | shade_set
        if first in pool and second in pool and first != second:
            score += 1.5
    score -= _MUDDY_PENALTY * _earth_neighbours(items)
    score -= _WARM_WITH_COLD_PENALTY * _warm_base_with_cold_accent(items)
    return score


_MUDDY_PENALTY = 5  # сильнее бонусов «база» и «проверенная пара»: такой комплект проигрывает контрастному
# Соседние слои: верх и низ, верх и кофта, кофта и куртка (без кофты — верх и куртка).
_NEIGHBOUR_ZONES = (("Верх", "Низ"), ("Верх", "Кофты"), ("Кофты", "Верхняя одежда"))


def _earth_neighbours(items) -> int:
    """Сколько пар соседних слоёв обе землистых тонов (коричневая рубашка + оливковые брюки, коричневое худи +
    оливковый флис): тёплые приглушённые цвета рядом сливаются. Землистый хорош с нейтральным или тёмно-синим."""
    earth = {}
    for item in items:
        earth[item.get("zone")] = earth.get(item.get("zone")) or color_family(main_color(item)) == "earth"
    pairs = [*_NEIGHBOUR_ZONES, *([("Верх", "Верхняя одежда")] if "Кофты" not in earth else [])]
    return sum(1 for first, second in pairs if earth.get(first) and earth.get(second))


# Холодные цветные акценты, к которым нужна холодная база (серый, чёрный, белый, тёмно-синий, деним). Синий,
# голубой и зелёный с бежевым — классика, они не в счёт.
_COLD_ACCENTS = ("фиолет", "сирен", "лаванд", "мят", "бирюз")
_WARM_BASE = ("беж", "кремов", "молоч", "экрю", "айвори", "песоч", "кэмел", "камел", "слонов")
_WARM_WITH_COLD_PENALTY = 4  # бежевая футболка под фиолетовый флис проигрывает серой и чёрной


def _warm_base_with_cold_accent(items) -> int:
    """Сколько вещей тёплой базы (бежевый, кремовый, землистые) рядом с холодным акцентом (фиолетовый, мятный…)."""
    colors = [main_color(item).casefold() for item in items if item.get("zone") != "Аксессуары"]
    if not any(marker in color for color in colors for marker in _COLD_ACCENTS):
        return 0
    return sum(1 for color in colors if not any(marker in color for marker in _COLD_ACCENTS)
               and (color_family(color) == "earth" or any(marker in color for marker in _WARM_BASE)))


_PATTERNS = ("рисун", "принт", "надпис", "логотип", "клет", "полос", "камуфл", "цветоч", "горош", "орнамент",
             "узор", "леопард", "графич")


def _accent(item) -> str:
    """Цветной акцент вещи — корень цвета («фиолет», «бордов»), чтобы тон в тон считался одним; база — ""."""
    color = main_color(item).casefold()
    family = color_family(color)
    if family not in ("cool", "warm"):
        return ""
    return next((marker for marker in dict(_FAMILIES)[family] if marker in color), family)


def statement_count(items) -> int:
    """Заметные элементы комплекта: каждая вещь с рисунком или узором и каждый разный цветной акцент (тон в тон —
    один). Больше одного — образ перегружен: бежевая футболка с рисунком под фиолетовым флисом."""
    clothes = [item for item in items if item.get("zone") != "Аксессуары"]
    patterned = [item for item in clothes if _has(item, _PATTERNS)]
    accents = {_accent(item) for item in clothes if not any(item is other for other in patterned)} - {""}
    return len(patterned) + len(accents)


# ---------- строка-причина ----------
FROST_FEELS = 0    # °C «ощущается»: шапка и перчатки
COLD_FEELS = 6     # °C: шарф
WINDY_COLD_FEELS = 12


# Аксессуар по погоде (винительный падеж) → как его узнать в шкафу.
_ACCESSORY_KINDS = (
    ("шапку", ("шапк", "бини", "beanie")),
    ("перчатки", ("перчат", "варежк")),
    ("шарф", ("шарф", "снуд")),
    ("солнечные очки", ("солнцезащит", "солнечн", "очки от солнца", "sunglasses")),
)


def _accessory_kind(item):
    if item.get("zone") != "Аксессуары":
        return ""
    name = str(item.get("name") or "").casefold()
    return next((kind for kind, markers in _ACCESSORY_KINDS if any(marker in name for marker in markers)), "")


def accessory_advice(ctx, items=()):
    """Аксессуары по погоде (винительный падеж): шапка и перчатки, шарф, очки. Что уже есть в образе (items), —
    не советуем: оно в списке вещей."""
    low, high = feels_low(ctx), feels_high(ctx)
    advice = []
    if low is not None and low <= FROST_FEELS:
        advice += ["шапку", "перчатки"]
    elif low is not None and (low <= COLD_FEELS or (ctx.get("strong_wind") and low < WINDY_COLD_FEELS)):
        advice.append("шарф")
    if not ctx.get("has_rain") and (ctx.get("sunny") or (summer_weather(ctx) and high is not None and high >= 24)):
        advice.append("солнечные очки")
    worn = {_accessory_kind(item) for item in items}
    return [kind for kind in advice if kind not in worn]


def weather_accessories(ctx, accessories):
    """Аксессуары из шкафа под погоду — в список образа: в мороз шапка и перчатки, в холод шарф, в солнце очки;
    по одному каждого вида. Чего в шкафу нет — остаётся советом в строке о погоде."""
    wanted = accessory_advice(ctx)
    picked = {}
    for item in accessories:
        kind = _accessory_kind(item)
        if kind in wanted and kind not in picked:
            picked[kind] = item
    return [picked[kind] for kind, _markers in _ACCESSORY_KINDS if kind in picked]


def _and_list(values):
    """«a», «a и b», «a, b и c»; после придаточного — запятая: «слой, который можно снять, и шарф»."""
    values = list(dict.fromkeys(values))
    if len(values) == 1:
        return values[0]
    head = ", ".join(values[:-1])
    return f"{head}, и {values[-1]}" if len(values) == 2 and "," in values[0] else f"{head} и {values[-1]}"


def weather_reason(ctx, items):
    """«Дождь и порывы ветра до 16 м/с. Надень непромокаемую верхнюю одежду и закрытую обувь.»

    Погода, затем что надеть — отдельным предложением, без тире. В спокойную погоду — сводка «Сухо, до +14°,
    ветер до 5 м/с.»; без прогноза — "". Аксессуар, который уже в образе (items), не советуется.
    """
    weather, consequences = [], []
    if ctx.get("has_rain"):
        hour = ctx.get("rain_from")
        weather.append(f"дождь с {hour:02d}:00" if isinstance(hour, int) and hour > (ctx.get("window") or (0,))[0]
                       else "дождь")
        outer = next((item for item in items if item.get("zone") == "Верхняя одежда"), None)
        if outer and outer.get("rain_ok"):
            consequences.append("непромокаемую верхнюю одежду")
        if any(item.get("zone") == "Обувь" for item in items):
            consequences.append("закрытую обувь")
    gust = ctx.get("gust_max")
    if ctx.get("strong_wind"):
        weather.append(f"порывы ветра до {gust} м/с" if gust else "сильный ветер")
        if not ctx.get("has_rain") and any(item.get("zone") == "Верхняя одежда" for item in items):
            consequences.append("ветрозащитный слой")
    parts = [" и ".join(weather)] if weather else []
    if ctx.get("layering") and ctx.get("feels_min") is not None:
        parts.append(f"утром ощущается {ctx['feels_min']:+d}°, днём до {ctx['feels_max']:+d}°")
        consequences.append("слой, который можно снять")
    if not parts and summer_weather(ctx) and feels_high(ctx) is not None and feels_high(ctx) >= 24:
        parts.append(f"тепло до {round(feels_high(ctx)):+d}° и сухо")
        consequences.append("лёгкие вещи")
    low = feels_low(ctx)
    if not parts and low is not None and low <= COLD_FEELS:
        parts.append(f"холодно, ощущается {round(low):+d}°")
    consequences.extend(accessory_advice(ctx, items))
    if not parts and ctx.get("tmax") is not None:
        # Погода ни на что не повлияла — всё равно коротко говорим, какая она.
        parts.append(f"сухо, до {round(float(ctx['tmax'])):+d}°")
        if ctx.get("wind_ms"):
            parts.append(f"ветер до {round(float(ctx['wind_ms']))} м/с")
    if not parts:
        return ""
    text = ", ".join(parts)
    text = text[:1].upper() + text[1:]
    if consequences:
        return f"{text}. Надень {_and_list(consequences)}."
    return text + "."
