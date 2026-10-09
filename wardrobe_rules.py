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
SHORTS_GUST_MAX = 8     # м/с


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
    if zone in ("Верх", "Низ", "Обувь", "Верхняя одежда"):
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
    return score


# ---------- строка-причина ----------
FROST_FEELS = 0    # °C «ощущается»: шапка и перчатки
COLD_FEELS = 6     # °C: шарф
WINDY_COLD_FEELS = 12


def accessory_advice(ctx):
    """Аксессуары по погоде — советом, а не вещами из шкафа: зонт, очки, шарф, шапка и перчатки."""
    low, high = feels_low(ctx), feels_high(ctx)
    advice = []
    if ctx.get("has_rain"):
        advice.append("зонт")
    if low is not None and low <= FROST_FEELS:
        advice.append("шапка и перчатки")
    elif low is not None and (low <= COLD_FEELS or (ctx.get("strong_wind") and low < WINDY_COLD_FEELS)):
        advice.append("шарф")
    if not ctx.get("has_rain") and (ctx.get("sunny") or (summer_weather(ctx) and high is not None and high >= 24)):
        advice.append("солнечные очки")
    return advice


def weather_reason(ctx, items):
    """Одна строка с точкой в конце, только когда погода повлияла на выбор; иначе ""."""
    parts, consequences = [], []
    if ctx.get("has_rain"):
        hour = ctx.get("rain_from")
        parts.append(f"Дождь с {hour:02d}:00" if isinstance(hour, int) and hour > (ctx.get("window") or (0,))[0]
                     else "Дождь")
        outer = next((item for item in items if item.get("zone") == "Верхняя одежда"), None)
        if outer and outer.get("rain_ok"):
            consequences.append("непромокаемая верхняя одежда")
        if any(item.get("zone") == "Обувь" for item in items):
            consequences.append("закрытая обувь")
    gust = ctx.get("gust_max")
    if ctx.get("strong_wind"):
        parts.append(f"порывы до {gust} м/с" if gust else "сильный ветер")
        if not ctx.get("has_rain") and any(item.get("zone") == "Верхняя одежда" for item in items):
            consequences.append("ветрозащитный слой")
    if ctx.get("layering") and ctx.get("feels_min") is not None:
        parts.append(f"утром ощущается {ctx['feels_min']:+d}°, днём до {ctx['feels_max']:+d}°")
        consequences.append("слой, который можно снять")
    if not parts and summer_weather(ctx) and feels_high(ctx) is not None and feels_high(ctx) >= 24:
        parts.append(f"Тепло до {round(feels_high(ctx)):+d}° и сухо")
        consequences.append("лёгкие вещи")
    low = feels_low(ctx)
    if not parts and low is not None and low <= COLD_FEELS:
        parts.append(f"Холодно, ощущается {round(low):+d}°")
    consequences.extend(accessory_advice(ctx))
    if not parts:
        return ""
    text = ", ".join(parts)
    text = text[:1].upper() + text[1:]
    if consequences:
        text += " — " + ", ".join(dict.fromkeys(consequences))
    return text + "."

