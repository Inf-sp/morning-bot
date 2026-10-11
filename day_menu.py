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

# Вид страны над меню дня — узнаваемое место, не блюдо. Фото ищется через общий слой
# travel_photos (Unsplash, при сбое Pexels) по запросу CUISINE_SCENE и хранится 30 дней;
# CUISINE_IMAGE — проверенный запасной вид из Wikimedia Commons, если оба сервиса недоступны.
CUISINE_SCENE = {
    "italian": "Amalfi Coast Italy", "japanese": "Mount Fuji Japan", "thai": "Phi Phi Islands Thailand",
    "mexican": "Chichen Itza Mexico", "indian": "Taj Mahal India", "chinese": "Great Wall of China",
    "turkish": "Cappadocia balloons Turkey", "french": "Paris Eiffel Tower",
    "russian": "Moscow Kremlin", "georgian": "Gergeti Trinity Church Georgia",
}
_PHOTO_TTL = 30 * 86400
_PHOTO_RETRY = 86400  # оба сервиса недоступны — запасной вид, новая попытка через сутки

CUISINE_IMAGE = {
    "italian": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3d/Amalfi_Coast_%28Italy%2C_October_2020%29_-_75_%2850558355441%29.jpg/1280px-Amalfi_Coast_%28Italy%2C_October_2020%29_-_75_%2850558355441%29.jpg",  # Амальфитанское побережье
    "japanese": "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f8/View_of_Mount_Fuji_from_%C5%8Cwakudani_20211202.jpg/1280px-View_of_Mount_Fuji_from_%C5%8Cwakudani_20211202.jpg",  # Фудзи
    "thai": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/KohPhiPhi.JPG/1280px-KohPhiPhi.JPG",  # острова Пхи-Пхи
    "mexican": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/51/Chichen_Itza_3.jpg/1280px-Chichen_Itza_3.jpg",  # Чичен-Ица
    "indian": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1d/Taj_Mahal_%28Edited%29.jpeg/1280px-Taj_Mahal_%28Edited%29.jpeg",  # Тадж-Махал
    "chinese": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/The_Great_Wall_of_China_at_Jinshanling-edit.jpg/1280px-The_Great_Wall_of_China_at_Jinshanling-edit.jpg",  # Великая Китайская стена
    "turkish": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/Cappadocia_balloon_trip%2C_Ortahisar_Castle_%2811893715185%29.jpg/1280px-Cappadocia_balloon_trip%2C_Ortahisar_Castle_%2811893715185%29.jpg",  # Каппадокия
    "french": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/La_Tour_Eiffel_vue_de_la_Tour_Saint-Jacques%2C_Paris_ao%C3%BBt_2014_%282%29.jpg/1280px-La_Tour_Eiffel_vue_de_la_Tour_Saint-Jacques%2C_Paris_ao%C3%BBt_2014_%282%29.jpg",  # Париж
    "russian": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/46/Moscow_Kremlin_%288281675670%29.jpg/1280px-Moscow_Kremlin_%288281675670%29.jpg",  # Московский Кремль
    "georgian": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4c/Gergeti_Trinity_Church_09.23.jpg/1280px-Gergeti_Trinity_Church_09.23.jpg",  # Гергетская церковь
}

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
    "russian": "Домашние и сытные блюда: наваристые супы, каши, блины, квашеные овощи и сметана.",
    "georgian": "Травы, орехи и сыр: хачапури, хинкали, ореховые соусы, много зелени и специй.",
}


def _settings():
    import settings
    return settings


# Слова, по которым код проверяет блюдо на ограничения (название и пояснение).
_RESTRICTION_MARKERS = {
    "vegetarian": ("мяс", "кур", "птиц", "говяд", "свин", "телят", "баран", "утк", "индейк", "фарш", "копчён",
                   "котлет", "колбас", "бекон", "ветчин", "гуанчале", "рыб", "тунц", "лосос", "кревет",
                   "кебаб", "кёфте", "шашлык", "пельмен", "хинкали", "чахохбили", "бефстроганов",
                   "тонкацу", "кацудон", "якитори", "оякодон", "chicken", "beef", "pork", "pollo",
                   "saltimbocca", "carbonara", "bourguignon", "coq au vin", "poulet", "mole", "pozole",
                   "tacos", "enchiladas"),
    "no_pork": ("свин", "бекон", "ветчин", "гуанчале", "тонкацу", "кацудон", "pork", "carbonara",
                "croque-monsieur", "saltimbocca", "chorizo"),
    "no_lactose": ("сыр", "молок", "молоч", "сливк", "сливоч", "сметан", "йогурт", "творог", "панир",
                   "пармезан", "бешамел", "моцарел", "кефир", "caffellatte", "yogurt", "quesadilla",
                   "gratin", "croque", "хачапури", "чвиштари", "сырник", "lasagna", "parmigiana"),
}


def fits_restrictions(dish, restrictions) -> bool:
    text = f"{dish.get('name', '')} {dish.get('note', '')}".casefold()
    return not any(marker in text for key in restrictions for marker in _RESTRICTION_MARKERS.get(key, ()))


LOCAL_PREFIX = "local_"  # кухня страны отпуска: local_za


def cuisine_flag(cuisine) -> str:
    """Флаг кухни для заголовка меню; кухня страны отпуска — флаг страны."""
    from ui.constants import CUISINE_FLAG
    import util
    if str(cuisine).startswith(LOCAL_PREFIX):
        return util.flag_from_cc(str(cuisine)[len(LOCAL_PREFIX):]) or "🍳"
    return CUISINE_FLAG.get(str(cuisine or ""), "🍳")


def cuisine_label(cuisine) -> str:
    """«Итальянская» — подпись кухни без эмодзи."""
    label = next((label for key, label in _settings().CUISINE_OPTIONS if key == cuisine), cuisine)
    return label.split(" ", 1)[-1]


def day_cuisine(cid, day) -> str:
    """Кухня дня: в отпуске — кухня страны; дома по кругу из предпочтений, без них — из всех."""
    import vacation
    trip = vacation.cuisine(cid)
    if trip:
        return trip
    return home_cuisine(cid, day)


def home_cuisine(cid, day) -> str:
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
    meals = _settings().food_meals(cid)
    return menu if all((dishes.get(meal) or {}).get("name") for meal in meals) else None


def _history(profile, now) -> list[str]:
    entry = profile.get("cooking_day_menu_history") or {}
    month = _today(now).strftime("%Y-%m")
    return list(entry.get("names") or []) if entry.get("month") == month else []


def _fallback_dish(cuisine, meal, day, avoided, restrictions=()):
    """Типичное блюдо из встроенного списка: по кругу, сначала подходящие и не показанные.

    Если в этом приёме пищи нет блюда под ограничения — берём подходящее блюдо этой же
    кухни из другого приёма пищи, а не нарушаем ограничение.
    """
    by_meal = TYPICAL_DISHES.get(cuisine) or {}
    options = by_meal.get(meal) or (("", ""),)

    def fitting(items):
        return [item for item in items if fits_restrictions({"name": item[0], "note": item[1]}, restrictions)]

    other_meals = [item for key, items in by_meal.items() if key != meal for item in items]
    allowed = fitting(options) or fitting(other_meals) or list(options)
    fresh = [item for item in allowed if item[0].casefold() not in avoided] or allowed
    name, note = fresh[day.toordinal() % len(fresh)]
    return {"name": name, "note": note}


def _clean(value, limit) -> str:
    text = " ".join(str(value or "").split()).strip(" .")
    return text if 0 < len(text) <= limit else ""


def _prompt(cuisine, available, avoided_names, meals=MEALS, restrictions_text="", label="") -> str:
    lines = "\n".join(f"- {meal}: {typical_dish_line(cuisine, meal)}" for meal in meals)
    limits = (f"Обязательные ограничения в еде: {restrictions_text}. Блюдо, которое их нарушает, — ошибка.\n"
              if restrictions_text else "")
    json_shape = ",".join(f'"{meal}":{{"name":"","note":""}}' for meal in meals)
    names = {"breakfast": "завтрак", "lunch": "обед", "dinner": "ужин"}
    fridge = (f"По возможности опирайся на продукты из холодильника: "
              f"{secure.wrap_untrusted(', '.join(available), 'продукты в наличии')}.\n" if available else "")
    avoid = (f"Не повторяй блюда: {secure.wrap_untrusted(', '.join(avoided_names[-60:]), 'история меню')}.\n"
             if avoided_names else "")
    return (
        f"Составь меню на один день в одной кухне: {label or cuisine_label(cuisine)}.\n"
        f"Нужны типичные домашние блюда этой кухни на: {', '.join(names[meal] for meal in meals)} — то, что в "
        "этой стране действительно едят на этот приём пищи, без фьюжна и случайных комбинаций.\n"
        f"Типичные варианты:\n{lines}\n"
        "Выбери из них или столь же традиционные; все блюда разные.\n"
        f"{limits}{fridge}{avoid}"
        "name — общепринятое название блюда (для итальянской, французской и мексиканской кухни — "
        "оригинальное латиницей, для остальных — по-русски); note — что это за блюдо, по-русски, "
        "до 10 слов, без точки.\n"
        f"JSON без markdown: {{{json_shape}}}"
    )


def _ai_dishes(cid, cuisine, available, avoided_names, meals=MEALS, restrictions_text="", label="") -> dict:
    try:
        data = ai.llm_json(
            _prompt(cuisine, available, avoided_names, meals, restrictions_text, label), 500,
            tier="cheap", module="food",
            fallback_allowed=True, privacy_level="personal", allow_personal_openrouter=True,
            cache_context={
                "scenario": "food_day_menu", "cuisine": cuisine, "available": available,
                "avoid": avoided_names[-60:], "meals": list(meals), "restrictions": restrictions_text,
                "language": "ru", "schema_version": 2,
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
    meals = _settings().food_meals(cid)
    restrictions = _settings().food_restrictions(cid)
    label = ""
    if cuisine.startswith(LOCAL_PREFIX):
        import vacation
        label = vacation.local_cuisine_label(cid)
    data = _ai_dishes(cid, cuisine, available, avoided_names, meals, _settings().food_restrictions_prompt(cid), label)
    if label and not all(isinstance(data.get(meal), dict) and _clean(data[meal].get("name"), _NAME_MAX)
                         for meal in meals):
        # Кухни страны отпуска нет во встроенном списке блюд: без ответа AI — обычная кухня дня.
        cuisine, label = home_cuisine(cid, day), ""
        data = _ai_dishes(cid, cuisine, available, avoided_names, meals, _settings().food_restrictions_prompt(cid))
    dishes, used = {}, set()
    for meal in meals:
        raw = data.get(meal) if isinstance(data.get(meal), dict) else {}
        name, note = _clean(raw.get("name"), _NAME_MAX), _clean(raw.get("note"), _NOTE_MAX)
        if (not name or name.casefold() in avoided or name.casefold() in used
                or not fits_restrictions({"name": name, "note": note}, restrictions)):
            dish = _fallback_dish(cuisine, meal, day, avoided | used, restrictions)
        else:
            dish = {"name": name, "note": note}
        used.add(dish["name"].casefold())
        dishes[meal] = dish
    menu = {"date": day.isoformat(), "cuisine": cuisine, "dishes": dishes}
    return {**menu, "cuisine_label": label} if label else menu


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


def cuisine_photo(cuisine, cid=None) -> dict:
    """{url, source} — вид страны кухни; {} — фото нет (подписи автора под меню нет).

    Unsplash/Pexels через travel_photos, кэш 30 дней на кухню; без них — запасной вид Wikimedia.
    """
    import time
    import travel_photos
    cuisine = str(cuisine or "")
    query = CUISINE_SCENE.get(cuisine)
    if not query and cuisine.startswith(LOCAL_PREFIX):
        import vacation
        name = vacation.country_name_en(cid) if cid is not None else ""
        query = f"{name} landscape" if name else ""
    if not query:
        return {}
    saved = (store._load(config.CUISINE_PHOTOS_KEY) or {}).get(cuisine) or {}
    if isinstance(saved, dict) and float(saved.get("expires") or 0) > time.time():
        return dict(saved.get("value") or {})
    found = travel_photos.find_landscape(query) or {}
    if found.get("url"):
        value = {"url": found["url"], "source": found.get("provider", "")}
        ttl = _PHOTO_TTL
    else:
        value = {"url": CUISINE_IMAGE[cuisine]} if cuisine in CUISINE_IMAGE else {}
        ttl = _PHOTO_RETRY

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[cuisine] = {"value": value, "expires": time.time() + ttl}
        return data, None

    store.mutate_kv(config.CUISINE_PHOTOS_KEY, mutate)
    return value


def warm_day_menu(cid, now=None) -> bool:
    """Ночная подготовка меню дня (и фото кухни) без сообщений пользователю."""
    try:
        menu = get_day_menu(cid, now=now)
        try:
            cuisine_photo(menu.get("cuisine"), cid)
        except Exception:
            _log.debug("day menu photo warm failed", exc_info=True)
        return bool(menu)
    except Exception as error:
        _log.warning("day menu warm failed cid=%s: %r", cid, error)
        return False


def dish_recipe(cid, meal, now=None) -> dict:
    """Полный рецепт блюда из меню дня для этого приёма пищи (кэш до конца дня)."""
    import recipe_generation
    menu = get_day_menu(cid, now=now)
    dish = menu["dishes"].get(meal)
    if not dish:
        raise ValueError(f"meal {meal} is not in today's menu")
    moment = (now or datetime.now(config.TZ)).replace(
        hour=MEAL_HOURS[meal], minute=0, second=0, microsecond=0)
    # Кухни страны отпуска нет среди кухонь рецептов: блюдо задано, кухня — международная.
    cuisine = "international" if str(menu["cuisine"]).startswith(LOCAL_PREFIX) else menu["cuisine"]
    return recipe_generation.get_cooking_home_idea(cid, now=moment, cuisine=cuisine, dish=dish)
