import html
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .builder import MessageBuilder, MessageSpec
from .constants import CUISINE_FLAG, LANGUAGE_EMOJI, ui_label
from .food import CUISINE_RU
from .navigation import nav_row
from .text import ru_plural

UI_MYDAY = ui_label("myday", "").strip()
UI_WARDROBE = ui_label("wardrobe", "").strip()
UI_FOOD = ui_label("food", "").strip()
UI_SETTINGS = ui_label("settings", "").strip()


def _add_footer(b: MessageBuilder):
    """Общий футер для экранов меню: подсказка про Настройки, bold только на слове."""
    b.spacer()
    b.text_line(f"Изменить параметры или посмотреть сохранённую информацию можно в {UI_SETTINGS} ")
    b.bold("Настройках")
    b.text_line(".")
    return b


def _screen_message(emoji: str, title: str, description, rows, show_footer: bool = True) -> MessageSpec:
    """Строит экран меню: 'emoji жирный_заголовок' + описание + общий футер настроек."""
    b = MessageBuilder()
    b.text_line(f"{emoji} ")
    b.bold(title)
    b.newline()
    b.spacer()
    if isinstance(description, (list, tuple)):
        for line in description:
            b.line(line)
    else:
        b.line(description)
    if show_footer:
        _add_footer(b)
    return b.build_stripped(reply_markup=ikb(rows))


def ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])


def main_menu_rows():
    return [
        [(ui_label("myday", "Мой день"), "m_myday")],
        [(ui_label("wardrobe", "Гардероб"), "m_wardrobe"), (ui_label("food", "Кухня"), "m_food")],
        [(ui_label("learning", "Обучение"), "m_learn"), (ui_label("leisure", "Досуг"), "m_leisure")],
        [(ui_label("settings", "Настройки"), "m_settings")],
    ]


def main_menu_kb():
    return ikb(main_menu_rows())


def welcome(name: str = ""):
    name = str(name or "").strip()
    greeting = f"👋🏻 Привет, {name}! Я DM — твой помощник на каждый день." if name else "👋🏻 Привет! Я DM — твой помощник на каждый день."
    b = MessageBuilder()
    b.bold(greeting)
    b.newline()
    b.spacer()
    b.line("Подберу образ по погоде, найду рецепт из продуктов дома, помогу с языком или подскажу, что посмотреть.")
    b.spacer()
    b.line("Выбирай раздел в меню или просто пиши мне здесь 💬")
    return b.build()


def inactivity_reminder():
    b = MessageBuilder()
    b.section("🫪 Давно не виделись")
    b.spacer()
    b.line("Загляни - подберу образ на сегодня, помогу с планами или предложу что-то полезное.")
    b.spacer()
    b.line("Выбери раздел или просто напиши, что нужно.")
    return b.build_stripped(reply_markup=main_menu_kb())


_SCREENS = {
    "m_myday": (
        UI_MYDAY,
        "Мой день",
        "Соберу короткую сводку: погоду, планы, образ и одну полезную мысль на сегодня.",
        [
            [("☀️ Открыть мой день", "a_plany")],
            [("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_wardrobe": (
        UI_WARDROBE,
        "Гардероб",
        "Одежда без хаоса. Подберу образ, помогу разобрать шкаф и собрать чемодан в поездку. Чем полнее гардероб, тем точнее рекомендации.",
        [
            [("🎚️ Настроить шкаф", "w_closet"), ("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_food": (
        UI_FOOD,
        "Кухня",
        "Подберу блюдо из того, что есть дома, и покажу короткий понятный рецепт.",
        [
            [("🍳 Что приготовить", "m_food")],
            [("🎚️ Настроить холодильник", "as_fridge_home"), ("#️⃣ Главная", "m_menu")],
        ],
    ),
}


def _without_period(value) -> str:
    """«Geen probleem.» → «Geen probleem»; «!» и «?» остаются."""
    return str(value or "").strip().rstrip(".").strip()


def _with_period(value) -> str:
    value = str(value or "").strip()
    return value if not value or value[-1] in ".!?…" else value + "."


def _grammar_parts(rule) -> tuple:
    """«_Hoeven … te_ → нужно, обычно с _niet_…» → («Hoeven … te», «Нужно, обычно с niet…»)."""
    rule = re.sub(r"_([^_]+)_", r"\1", str(rule or "")).strip()
    # Тема отделена «→» (старые и AI-правила — «—»): берём первый разделитель.
    parts = re.split(r"\s(?:→|—)\s", rule, maxsplit=1)
    topic, explanation = (parts + [""])[:2]
    separator = len(parts) > 1
    if not separator:
        return "", _with_period(rule) if rule else ""
    explanation = explanation.strip()
    return topic.strip(), _with_period(explanation[:1].upper() + explanation[1:])


def learning_menu(home: dict):
    """Главный экран обучения: материал дня, прогресс и следующий шаг."""
    if home.get("disabled"):
        b = MessageBuilder()
        b.section("🧠 Обучение")
        b.spacer()
        b.line("Изучение языка выключено.")
        b.spacer()
        b.line("Выбери нидерландский или английский в предпочтениях, когда захочешь вернуться к практике.")
        return b.build_stripped(reply_markup=ikb([
            [("🎚️ Язык обучения", "set_learning")],
            [("#️⃣ Главная", "m_menu")],
        ]))
    if home.get("vacation"):
        return _trip_learning(home)
    code = home.get("lang_code", "nl")
    flag = home.get("flag") or LANGUAGE_EMOJI.get(code, LANGUAGE_EMOJI["nl"])
    title = home.get("language") or ("Английский" if code == "en" else "Нидерландский")

    b = MessageBuilder()
    if not home.get("has_material"):
        b.section("🧠 Обучение")
        b.spacer()
        b.line("Добавляй сюда слова, которые хочешь запомнить.")
        b.spacer()
        b.line("Можно просто написать мне в чате:")
        b.line("«добавь в словарь afspraak»")
        b.spacer()
        b.line("Я буду использовать их в практике и повторении.")
        return b.build_stripped(reply_markup=ikb([
            [("✅ Добавить слова", f"a_dictadd_smart_{code}")],
            [("✨ Сгенерировать набор слов", f"a_dictseed_start_{code}")],
        ]))

    b.bold(f"{flag} Изучаем сегодня · {title}")
    b.newline()

    # Грамматика: тема → значение, объяснение пунктами в цитате, пример → перевод
    # (фраза дня — в «Моём дне», строка «Фраза дня»).
    phrase = home.get("live_language") or {}
    if phrase.get("topic") and phrase.get("explain"):
        # Подробное объяснение — пунктами через пустую строку в одной цитате; без пунктов — краткое.
        details = [_with_period(point) for point in phrase.get("details") or [] if str(point).strip()]
        topic, explanation = phrase["topic"], "\n\n".join(details) or _with_period(phrase["explain"])
        if phrase.get("meaning"):
            topic += f" → {phrase['meaning']}"
    else:  # прежний краткий формат правила (уроки страны отпуска)
        topic, explanation = _grammar_parts(phrase.get("rule"))
    if topic:
        b.spacer()
        b.bold("Грамматика:")
        b.text_line(f" {topic}")
        b.newline()
    if explanation:
        b.spacer()
        b.quote(explanation)
        b.newline()
    if phrase.get("more") and phrase.get("more_translation"):
        b.spacer()
        b.line(f"{_without_period(phrase['more'])} → {_with_period(phrase['more_translation'])}")
    stats = home.get("stats")
    if stats:  # статистика — последней строкой курсивом
        b.spacer()
        b.italic(learning_stats_line(title, stats.get("words"), stats.get("topics")))
        b.newline()

    return b.build_stripped(reply_markup=ikb([
        [("🎯 Запустить тренировку", f"a_train_{code}")],
        [(ui_label("game", "Угадай слово"), "a_game")],
        [("🎚️ Настроить словарь", f"a_dictlang_{code}_from_menu"), ("#️⃣ Главная", "m_menu")],
    ]))


def _trip_learning(home: dict):
    """«🇿🇦 Обучение в отпуске · Африкаанс»: «Ситуация: транспорт», ниже три фразы обычным текстом строка к строке —
    фраза жирным → перевод (произношение); курсивом факт дня о языке. Грамматики и строки «Произношение» нет."""
    b = MessageBuilder()
    b.title(f"{home.get('flag') or '🌍'} Обучение в отпуске · {home.get('language') or 'Местный язык'}")
    situation = home.get("situation") or {}
    if situation.get("phrases"):
        b.labeled_line("Ситуация", str(situation.get("title") or "").strip())  # «Ситуация: транспорт»
        for phrase in situation["phrases"]:  # обычным текстом, строка к строке: фраза жирным → перевод (произношение)
            b.bold(str(phrase["text"]).strip())
            b.line(f" → {phrase['ru']}" + (f" ({phrase['say']})" if phrase.get("say") else ""))
    else:
        b.line("Готовлю фразы для поездки — загляни чуть позже.")
    if str(home.get("language_fact") or "").strip():
        b.spacer()
        b.italic(_with_period(home["language_fact"]))
        b.newline()
    # В отпуске: тренировка по фразам страны, свой словарь — после возвращения.
    return b.build_stripped(reply_markup=ikb([
        [("🎯 Запустить тренировку", "vac_train")],
        [(ui_label("game", "Угадай слово"), "a_game")],
        [("#️⃣ Главная", "m_menu")],
    ]))


def learning_stats_line(language, words, topics) -> str:
    words, topics = int(words or 0), int(topics or 0)
    return (f"Твой {str(language).lower()} · {words} {ru_plural(words, 'слово', 'слова', 'слов')}"
            f" · {topics} {ru_plural(topics, 'изученная тема', 'изученные темы', 'изученных тем')}")


def menu_screen(key):
    if key not in _SCREENS:
        return MessageSpec(text="Выбери раздел через /menu.")
    screen = _SCREENS[key]
    if len(screen) == 4:
        emoji, title, description, rows = screen
        show_footer = True
    else:
        emoji, title, description, rows, show_footer = screen
    return _screen_message(emoji, title, description, rows, show_footer=show_footer)


_COOKING_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    r"\u2190-\u21FF\u2300-\u23FF\u2B00-\u2BFF\ufe0f\u200d\U0001F3FB-\U0001F3FF]+"
)


def _cooking_text(value) -> str:
    value = html.unescape(str(value or ""))
    value = value.replace("**", "").replace("\\-", "-")
    value = _COOKING_EMOJI_RE.sub("", value)
    return " ".join(value.split()).strip(" -•")


def _cooking_sentence(value) -> str:
    value = _cooking_text(value)
    if value and value[-1] not in ".!?…":
        value += "."
    return value


_MEAL_TITLES = {"breakfast": "на завтрак", "lunch": "на обед", "dinner": "на ужин"}


_MEAL_NAMES = (("breakfast", "Завтрак"), ("lunch", "Обед"), ("dinner", "Ужин"))


# «Южноафриканская кухня — это …» / «Кухня Южной Африки — это …»: название уже в заголовке — не повторяем.
_INTRO_NAME_RE = re.compile(r"^[^.!?—]{0,60}?кухн[яиеу]\b[^—.!?]{0,40}?\s*[—–-]\s*(?:это\s+)?", re.IGNORECASE)


def _short_intro(text) -> str:
    """Описание кухни в одно короткое предложение без повтора её названия."""
    first = re.split(r"(?<=[.!?…])\s+", " ".join(str(text or "").split()))[0]
    first = _INTRO_NAME_RE.sub("", first, count=1).strip()
    return _with_period(first[:1].upper() + first[1:]) if first else ""


def trip_food_stats(stats) -> str:
    """«День 4 из 14» — запасная строка внизу Кухни в отпуске, когда нет чаевых и курса; нет данных — ""."""
    stats = stats or {}
    day, days = int(stats.get("day") or 0), int(stats.get("days") or 0)
    if day <= 0:
        return ""
    return f"День {day} из {days}" if days >= day else f"День {day}"


def _trip_menu(dish, *, cuisine_label, intro, flag, fact, trip_stats=None, eating_out=""):
    """«🇿🇦 Пробуем в отпуске · Южноафриканская кухня»: описание кухни одним коротким предложением без повтора
    названия, одно блюдо жирным — «🥩 Билтонг · Biltong» (эмодзи и местное название, если есть) — с описанием, курсивом
    что пригодится в кафе — «Чаевые: 10–15 % в кафе · 1 € = 18,51 R» (eating_out); нет — день поездки;
    кнопки «Другое блюдо» и «Главная» — в поездке не готовим, рецептов нет."""
    b = MessageBuilder()
    b.section(f"{flag} Пробуем в отпуске · {cuisine_label} кухня")
    # Описания кухни нет — каждый день одно и то же; главное — блюдо: название и местное название без эмодзи.
    if (dish or {}).get("name"):
        b.spacer()
        b.bold(" · ".join(part for part in (dish["name"], dish.get("original")) if part))
        b.newline()
        note = _cooking_text(dish.get("note"))
        if note:
            b.line(_with_period(note[:1].upper() + note[1:]))
    else:  # AI не дал блюда кухни страны — честно, без подмены домашней кухней
        b.spacer()
        b.line("Блюдо дня сейчас недоступно — загляни чуть позже.")
    footer = str(eating_out or "").strip() or trip_food_stats(trip_stats)
    if footer:
        b.spacer()
        b.italic(footer)
        b.newline()
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Другое блюдо", callback_data="food_trip_next")],
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ]))


def day_menu(menu, *, cuisine_label, intro="", flag="", show_mine=True, stats=None, trip=False, trip_dish=None,
             fact="", trip_stats=None, eating_out=""):
    """Главный экран Кухни: кухня дня, тон меню под заголовком, блюда без рецептов, статистика внизу.

    show_mine=False — в отпуске: кнопки «Из моих продуктов» нет (холодильник остался дома).
    trip — в отпуске: «Пробуем в отпуске» — одно блюдо trip_dish и факт о кухне fact (_trip_menu).
    stats — {"products": N, "meals": M}: курсивная строка «Твой холодильник · …» внизу.
    """
    flag = flag or CUISINE_FLAG.get(str((menu or {}).get("cuisine") or ""), "🍳")
    if trip:
        return _trip_menu(trip_dish, cuisine_label=cuisine_label, intro=intro, flag=flag, fact=fact,
                          trip_stats=trip_stats, eating_out=eating_out)
    b = MessageBuilder()
    b.section(f"{flag} Меню на сегодня · {cuisine_label} кухня")
    if intro:  # тон и настроение меню — сразу под заголовком, обычным текстом
        b.spacer()
        b.line(intro)
    dishes = (menu or {}).get("dishes") or {}
    rows = [(label, dishes.get(meal) or {}) for meal, label in _MEAL_NAMES]
    rows = [(label, dish) for label, dish in rows if dish.get("name")]
    for label, dish in rows:
        b.spacer()  # пустая строка перед каждым блюдом
        b.bold(f"{label}:")
        note = _cooking_text(dish.get("note"))
        b.line(f" {dish['name']}" + (f" — {note[:1].lower()}{note[1:]}" if note else ""))
    if stats:
        b.spacer()
        b.italic(fridge_stats_line(stats.get("products"), stats.get("meals")))
        b.newline()
    return b.build_stripped(reply_markup=food_card_kb([meal for meal, _label in _MEAL_NAMES if meal in dishes],
                                                      show_mine=show_mine))


def fridge_stats_line(products, meals) -> str:
    products, meals = int(products or 0), int(meals or 0)
    return (f"Твой холодильник · {products} {ru_plural(products, 'продукт', 'продукта', 'продуктов')}"
            f" · {meals} {ru_plural(meals, 'приём', 'приёма', 'приёмов')} пищи")


def food_menu(idea=None, *, meal="", from_fridge=False):
    """Полный рецепт блюда из меню дня для выбранного приёма пищи.

    from_fridge — рецепт «Из моих продуктов»: строка «Не хватает» и кнопка «Другой рецепт»;
    у блюда меню дня (из любых продуктов) их нет.
    """
    idea = idea or {}
    b = MessageBuilder()
    cuisine_code = str(idea.get("cuisine") or "").strip().lower()
    cuisine_name = CUISINE_RU.get(cuisine_code, CUISINE_RU["international"])
    # Эмодзи кухни, как в заголовке меню дня: «🇮🇹 Что приготовить на завтрак · Итальянская кухня».
    flag = CUISINE_FLAG.get(cuisine_code, "🍳")
    title = " ".join(part for part in (f"{flag} Что приготовить", _MEAL_TITLES.get(meal, "")) if part)
    b.section(f"{title} · {cuisine_name}")

    name = _cooking_text(idea.get("name"))
    if name:
        b.spacer()
        b.bold(name)
        b.newline()

    ingredients = [_cooking_text(item) for item in (idea.get("ingredients") or [])]
    ingredients = [item for item in ingredients if item]
    if ingredients:
        b.spacer()
        b.labeled_line("Ингредиенты", ", ".join(ingredients))
    missing = idea.get("missing_ingredients") or idea.get("missing") or []
    if isinstance(missing, str):
        missing = [missing]
    missing = [str(item).strip() for item in missing if str(item).strip()] if from_fridge else []
    if missing:
        b.spacer()
        b.bold("Не хватает:")
        b.newline()
        b.line(", ".join(missing))

    steps = []
    for raw_step in (idea.get("steps") or [])[:3]:
        step = raw_step if isinstance(raw_step, dict) else {"text": raw_step}
        text = _cooking_text(step.get("text"))
        minutes = step.get("minutes")
        if text and minutes and not re.search(r"\d+(?:\s*[–-]\s*\d+)?\s*мин", text, re.I):
            text = f"{text.rstrip('.!?…')} — {int(minutes)} мин"
        text = _cooking_sentence(text)
        if text:
            steps.append(text)
    if steps:
        b.spacer()
        b.bold("Приготовление:")
        b.newline()
        for step in steps:
            b.bullet(step)
    tip = _cooking_sentence(idea.get("tip"))
    if tip:
        b.spacer()
        b.italic(tip)  # финальная подсказка экрана — курсивом
        b.newline()

    return b.build_stripped(reply_markup=mine_recipe_kb() if from_fridge else InlineKeyboardMarkup([nav_row("m_food")]))


def mine_recipe_kb():
    """Под рецептом «Из моих продуктов»: «Другой рецепт» открывает выбор кухни (food_mine_pick)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Другой рецепт", callback_data="food_mine_pick")],
        nav_row("m_food"),
    ])


def fridge_empty_for_mine():
    """«Из моих продуктов» при пустом холодильнике: добавить продукты."""
    b = MessageBuilder()
    b.section("🧊 Из моих продуктов")
    b.spacer()
    b.line("В холодильнике нет отмеченных продуктов. Добавь, что есть дома, — и я соберу рецепт из них.")
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Добавить продукт", callback_data="as_fridge_add")],
        nav_row("m_food"),
    ]))


def food_card_kb(meals=None, show_mine=True):
    """Кнопки меню дня: рецепт каждого блюда (только выбранные приёмы пищи), «Из моих продуктов» (кроме отпуска)."""
    meals = meals or [meal for meal, _label in _MEAL_NAMES]
    rows = [
        [InlineKeyboardButton(label, callback_data=f"a_recipe_{meal}") for meal, label in _MEAL_NAMES if meal in meals],
    ]
    if show_mine:
        # Рецепт из отмеченных продуктов холодильника под время суток (меню дня — из любых продуктов).
        rows.append([InlineKeyboardButton("🧊 Из моих продуктов", callback_data="food_mine")])
    rows.append([InlineKeyboardButton("🎚️ Настроить холодильник", callback_data="as_fridge_home"),
                 InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")])
    return InlineKeyboardMarkup(rows)


def food_cuisine_kb(cuisines, prefix="food_go_day_", back="food_card"):
    """Выбор кухни; cuisines — [(код, подпись)]. По умолчанию — старая «Другое меню на сегодня» из истории
    чата; «Другой рецепт» из моих продуктов — prefix food_mine_go_, back food_mine_card."""
    rows = [[InlineKeyboardButton(label, callback_data=f"{prefix}{key}")]
            for key, label in cuisines]
    rows.append(nav_row(back))
    return InlineKeyboardMarkup(rows)

