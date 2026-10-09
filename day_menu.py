"""Меню на день: одна кухня, три типичных блюда — завтрак, обед, ужин.

Главный экран Готовки предлагает готовое решение на день, без рецептов. Кухня
дня идёт по кругу из предпочтений; блюда подбирает AI одним запросом из
типичных для кухни вариантов, ответ проверяет код. Без AI — встроенный список
типичных блюд. Полный рецепт блюда собирается отдельно по кнопке приёма пищи.
"""
import logging
from datetime import datetime

import ai
import config
import secure
import store
from recipe_generation import TYPICAL_DISHES, typical_dish_line

_log = logging.getLogger(__name__)

MEALS = ("breakfast", "lunch", "dinner")
MEAL_HOURS = {"breakfast": 8, "lunch": 13, "dinner": 18}
_NAME_MAX = 60
_NOTE_MAX = 100
_HISTORY_MAX = 93  # три блюда на день — месяц без повторов

# Вступление о кухне дня: характер и за что её любят.
CUISINE_INTRO = {
    "italian": "Свежие продукты и простые сочетания: томаты, оливковое масло, сыр, свежая зелень — "
               "и невероятная региональная вариативность.",
    "japanese": "Точность, умами и баланс: чистые вкусы, текстуры, бульоны и внимание к деталям.",
    "thai": "Острота, кислота, сладость и аромат: яркие контрасты, лайм, чили, кокосовое молоко "
            "и свежие травы.",
    "mexican": "Пряности, кукуруза и насыщенные соусы: чили, лайм, кукуруза, мясо и сложные соусы.",
    "indian": "Пряности и глубокие ароматы: сложные смеси специй, бобовые, разнообразие соусов "
              "и вегетарианских блюд.",
    "chinese": "Огромное региональное разнообразие: умами, хрустящие и нежные текстуры, "
               "ферментированные продукты и разные стили приготовления.",
    "turkish": "Мясо, овощи, специи и выпечка: сочное мясо, йогуртовые соусы, овощные блюда "
               "и богатая культура завтраков.",
    "french": "Соусы, техника и выпечка: сливочные соусы, масло, изысканные десерты "
              "и региональные специалитеты.",
    "russian": "Домашняя и сытная кухня: наваристые супы, каши, блины, квашеные овощи и сметана.",
    "georgian": "Травы, орехи и сыр: хачапури, хинкали, ореховые соусы, много зелени и специй.",
}


def _settings():
    import settings
    return settings


def cuisine_label(cuisine) -> str:
    """«Итальянская» — подпись кухни без эмодзи."""
    label = next((label for key, label in _settings().CUISINE_OPTIONS if key == cuisine), cuisine)
    return label.split(" ", 1)[-1]


def day_cuisine(cid, day) -> str:
    """Кухня дня: по кругу из предпочтений, без них — из всех кухонь."""
    pool = _settings().cuisines(cid) or [key for key, _label in _settings().CUISINE_OPTIONS]
    return pool[day.toordinal() % len(pool)]


def _today(now=None):
    return (now or datetime.now(config.TZ)).date()


def get_cached_day_menu(cid, now=None) -> dict | None:
    """Меню текущего дня из профиля или None — без AI и побочных эффектов."""
    menu = (store.get_profile(cid) or {}).get("cooking_day_menu")
    if not isinstance(menu, dict) or menu.get("date") != _today(now).isoformat():
        return None
    dishes = menu.get("dishes") or {}
    return menu if all((dishes.get(meal) or {}).get("name") for meal in MEALS) else None


def _history(profile, now) -> list[str]:
    entry = profile.get("cooking_day_menu_history") or {}
    month = _today(now).strftime("%Y-%m")
    return list(entry.get("names") or []) if entry.get("month") == month else []


def _fallback_dish(cuisine, meal, day, avoided):
    """Типичное блюдо из встроенного списка: по кругу, сначала не показанные."""
    options = (TYPICAL_DISHES.get(cuisine) or {}).get(meal) or (("", ""),)
    fresh = [item for item in options if item[0].casefold() not in avoided] or list(options)
    name, note = fresh[day.toordinal() % len(fresh)]
    return {"name": name, "note": note}


def _clean(value, limit) -> str:
    text = " ".join(str(value or "").split()).strip(" .")
    return text if 0 < len(text) <= limit else ""


def _prompt(cuisine, available, avoided_names) -> str:
    lines = "\n".join(f"- {meal}: {typical_dish_line(cuisine, meal)}" for meal in MEALS)
    fridge = (f"По возможности опирайся на продукты из холодильника: "
              f"{secure.wrap_untrusted(', '.join(available), 'продукты в наличии')}.\n" if available else "")
    avoid = (f"Не повторяй блюда: {secure.wrap_untrusted(', '.join(avoided_names[-60:]), 'история меню')}.\n"
             if avoided_names else "")
    return (
        f"Составь меню на один день в одной кухне: {cuisine_label(cuisine)}.\n"
        "Нужны три типичных домашних блюда этой кухни — завтрак, обед и ужин: то, что в этой стране "
        "действительно едят на этот приём пищи, без фьюжна и случайных комбинаций.\n"
        f"Типичные варианты:\n{lines}\n"
        "Выбери из них или столь же традиционные; все три блюда разные.\n"
        f"{fridge}{avoid}"
        "name — общепринятое название блюда (для итальянской, французской и мексиканской кухни — "
        "оригинальное латиницей, для остальных — по-русски); note — что это за блюдо, по-русски, "
        "до 10 слов, без точки.\n"
        'JSON без markdown: {"breakfast":{"name":"","note":""},"lunch":{"name":"","note":""},'
        '"dinner":{"name":"","note":""}}'
    )


def _ai_dishes(cid, cuisine, available, avoided_names) -> dict:
    try:
        data = ai.llm_json(
            _prompt(cuisine, available, avoided_names), 500, tier="cheap", module="food",
            fallback_allowed=True, privacy_level="personal", allow_personal_openrouter=True,
            cache_context={
                "scenario": "food_day_menu", "cuisine": cuisine, "available": available,
                "avoid": avoided_names[-60:], "language": "ru", "schema_version": 1,
            },
        )
    except Exception as error:
        _log.warning("day menu AI unavailable, using typical dishes: %s", type(error).__name__)
        return {}
    return data if isinstance(data, dict) else {}


def build_day_menu(cid, now=None, cuisine=None, avoid=()) -> dict:
    """Новое меню на день: блюда AI, прошедшие проверку, иначе типичные из списка."""
    import recipe_generation
    day = _today(now)
    cuisine = cuisine or day_cuisine(cid, day)
    profile = store.get_profile(cid) or {}
    avoided_names = list(dict.fromkeys([*_history(profile, now), *avoid]))
    avoided = {name.casefold() for name in avoided_names}
    available = recipe_generation._home_idea_context(cid, now=now).get("available") or []
    data = _ai_dishes(cid, cuisine, available, avoided_names)
    dishes, used = {}, set()
    for meal in MEALS:
        raw = data.get(meal) if isinstance(data.get(meal), dict) else {}
        name, note = _clean(raw.get("name"), _NAME_MAX), _clean(raw.get("note"), _NOTE_MAX)
        if not name or name.casefold() in avoided or name.casefold() in used:
            dish = _fallback_dish(cuisine, meal, day, avoided | used)
        else:
            dish = {"name": name, "note": note}
        used.add(dish["name"].casefold())
        dishes[meal] = dish
    return {"date": day.isoformat(), "cuisine": cuisine, "dishes": dishes}


def get_day_menu(cid, now=None, refresh=False, cuisine=None) -> dict:
    """Меню дня из кэша или новое; refresh — «Новое меню» (не повторяет текущее)."""
    cached = get_cached_day_menu(cid, now)
    if cached and not refresh:
        return cached
    current = [dish["name"] for dish in ((cached or {}).get("dishes") or {}).values()]
    menu = build_day_menu(cid, now=now, cuisine=cuisine, avoid=current)
    month = _today(now).strftime("%Y-%m")

    def save(profile):
        history = _history(profile, now)
        names = [*history, *(dish["name"] for dish in menu["dishes"].values())]
        profile["cooking_day_menu"] = menu
        profile["cooking_day_menu_history"] = {"month": month, "names": names[-_HISTORY_MAX:]}
        return profile, None

    store.mutate_profile(cid, save)
    return menu


def warm_day_menu(cid, now=None) -> bool:
    """Ночная подготовка меню дня без сообщений пользователю."""
    try:
        return bool(get_day_menu(cid, now=now))
    except Exception as error:
        _log.warning("day menu warm failed cid=%s: %r", cid, error)
        return False


def dish_recipe(cid, meal, now=None) -> dict:
    """Полный рецепт блюда из меню дня для этого приёма пищи (кэш до конца дня)."""
    import recipe_generation
    menu = get_day_menu(cid, now=now)
    dish = menu["dishes"][meal]
    moment = (now or datetime.now(config.TZ)).replace(
        hour=MEAL_HOURS[meal], minute=0, second=0, microsecond=0)
    return recipe_generation.get_cooking_home_idea(
        cid, now=moment, cuisine=menu["cuisine"], dish=dish)
