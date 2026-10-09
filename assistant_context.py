"""Личные данные для ответа ассистента: только раздел, о котором вопрос.

«Какие носки мне купить?» → вещи «Моего шкафа» с цветами и стиль; «что приготовить
без мяса» → продукты в наличии, кухни и ограничения; «что посмотреть похожего» →
«Моё кино», «Мои книги», «Мои артисты». Раздел ограничен по длине, чтобы не
раздувать запрос; без подходящей темы ничего не добавляется.
"""
import logging
import re

import config
import store

_log = logging.getLogger(__name__)

SECTION_MAX_CHARS = 1500
_LIST_MAX = 30

_TOPICS = (
    ("wardrobe", re.compile(
        r"(одежд|носк|обув|кед|кроссов|ботин|лофер|куртк|пальто|ветровк|футболк|рубаш|брюк|джинс|шорт|"
        r"свитер|худи|кардиган|пиджак|надеть|наряд|образ|гардероб|шкаф|сочета|подойд|носить|стил|"
        r"ремен|ремн|шапк|шарф|перчат|сумк|аксессуар)", re.I)),
    ("food", re.compile(
        r"(рецепт|приготов|готов(?:ить|лю|им)|ужин|обед|завтрак|продукт|холодильник|\bеда\b|еды|блюд|"
        r"кухн|перекус|ингредиент|вегетариан|лактоз|свинин)", re.I)),
    ("leisure", re.compile(
        r"(фильм|кино|сериал|книг|почитать|посмотреть|музык|артист|исполнител|альбом|песн|трек|"
        r"концерт|послушать)", re.I)),
    ("weather", re.compile(r"(погод|дожд|зонт|холодн|жарк|ветер|ветр|мороз)", re.I)),
)


def topics(text) -> list[str]:
    """Разделы данных, о которых вопрос; одежда тянет за собой погоду."""
    found = [name for name, pattern in _TOPICS if pattern.search(str(text or ""))]
    if "wardrobe" in found and "weather" not in found:
        found.append("weather")
    return found


def _clip(text):
    return text if len(text) <= SECTION_MAX_CHARS else text[:SECTION_MAX_CHARS].rsplit("\n", 1)[0] + "\n…"


def _wardrobe(cid):
    import settings
    from wardrobe_model import flat_items, public_item_name, public_zone_name
    by_zone = {}
    for zone, _subcategory, item in flat_items(store.load_wardrobe(cid)):
        name = public_item_name(item)
        colors = ", ".join(str(color) for color in item.get("colors") or [] if color)
        # Цвет в скобках, только если его нет в названии («Белые кеды» уже белые).
        stems = [str(color).casefold()[:4] for color in item.get("colors") or [] if color]
        label = name if not colors or all(stem in name.casefold() for stem in stems) else f"{name} ({colors})"
        by_zone.setdefault(public_zone_name(zone), []).append(label)
    if not by_zone:
        return "Шкаф пользователя пуст."
    lines = [f"{zone}: {', '.join(items[:_LIST_MAX])}" for zone, items in by_zone.items()]
    styles = settings.wardrobe_styles(cid)
    if styles:
        lines.append(f"Любимый стиль: {', '.join(styles)}")
    return "Шкаф пользователя («Мой шкаф»):\n" + "\n".join(lines)


def _food(cid):
    import settings
    from fridge_model import _fridge_available
    available = [str(name) for name in _fridge_available(store.get_list(config.FRIDGE_KEY, str(cid)))]
    lines = [f"Продукты в наличии: {', '.join(name for name in available[:60] if name) or 'нет отметок'}"]
    cuisines = settings.cuisine_labels(cid)
    if cuisines:
        lines.append(f"Любимые кухни: {', '.join(label.split(' ', 1)[-1] for label in cuisines)}")
    restrictions = settings.food_restrictions_prompt(cid)
    if restrictions:
        lines.append(f"Ограничения в еде (обязательные): {restrictions}")
    return "Еда пользователя:\n" + "\n".join(lines)


def _titles(key, cid, *, russian=False):
    titles = []
    for item in store.get_list(key, cid):
        if isinstance(item, dict):
            value = (item.get("title_ru") if russian else "") or item.get("value") or item.get("title") or item.get("name")
        else:
            value = item
        value = " ".join(str(value or "").split())
        if value:
            titles.append(value)
    return titles[:_LIST_MAX]


def _leisure(cid):
    lines = []
    for label, key, russian in (("Моё кино", config.FAVORITE_MOVIES_KEY, False),
                                ("Мои книги", config.FAVORITE_BOOKS_KEY, True),
                                ("Мои артисты", config.FAVORITE_ARTISTS_KEY, False)):
        titles = _titles(key, cid, russian=russian)
        if titles:
            lines.append(f"{label}: {', '.join(titles)}")
    return ("Любимое пользователя:\n" + "\n".join(lines)) if lines else ""


def _weather(cid):
    import weather
    settings_data = store.get_settings(cid) or {}
    city = str(settings_data.get("city") or "").strip()
    try:
        daily = (weather.fetch_weather(settings_data["lat"], settings_data["lon"], 1) or {}).get("daily") or {}
        tmax = round(daily["temperature_2m_max"][0])
        rain = int(daily["precipitation_probability_max"][0] or 0)
        wind = round(daily["windspeed_10m_max"][0] or 0)
        today = f"сегодня до {tmax:+d}°, вероятность дождя {rain}%, ветер до {wind} м/с"
    except Exception:
        today = ""
    parts = [part for part in (f"Город: {city}" if city else "", today) if part]
    return ("Погода: " + ", ".join(parts)) if parts else ""


def build(cid, text) -> str:
    """Блоки данных пользователя для вопроса или ""; сбой одного раздела не мешает остальным."""
    blocks = []
    for name in topics(text):
        try:
            block = globals()[f"_{name}"](cid)
        except Exception:
            _log.warning("assistant context %s failed cid=%s", name, cid, exc_info=True)
            continue
        if block:
            blocks.append(_clip(block))
    return "\n\n".join(blocks)
