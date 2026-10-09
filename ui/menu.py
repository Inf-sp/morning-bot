import html
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .builder import MessageBuilder, MessageSpec
from .constants import LANGUAGE_EMOJI, ui_label
from .food import CUISINE_RU
from .navigation import nav_row
from .news import append_weekly_news

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
        [(ui_label("wardrobe", "Гардероб"), "m_wardrobe"), (ui_label("food", "Готовка"), "m_food")],
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
        "Одежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. Чем полнее гардероб, тем точнее рекомендации.",
        [
            [("🎚️ Настроить", "w_closet"), ("#️⃣ Главная", "m_menu")],
        ],
    ),
    "m_food": (
        UI_FOOD,
        "Готовка",
        "Подберу блюдо из того, что есть дома, и покажу короткий понятный рецепт.",
        [
            [("🍳 Что приготовить", "m_food")],
            [("🎚️ Настроить", "as_fridge_home"), ("#️⃣ Главная", "m_menu")],
        ],
    ),
}


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
    code = home.get("lang_code", "nl")
    flag = LANGUAGE_EMOJI.get(code, LANGUAGE_EMOJI["nl"])
    title = "Английский" if code == "en" else "Нидерландский"

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

    # Одна мысль: фраза → пример с ней → правило из примера → одно действие.
    phrase = home.get("live_language") or {}
    if phrase.get("text") and phrase.get("translation"):
        b.spacer()
        # Фраза и перевод — одной цитатой: «Dat is de druppel! — Это последняя капля.»
        b.quote(f"{str(phrase['text']).strip()} — {str(phrase['translation']).strip()}")
        b.newline()
        if phrase.get("example"):
            b.spacer()
            b.italic(str(phrase["example"]).strip())
            b.newline()
        if phrase.get("rule"):
            b.spacer()
            b.bold("Грамматика:")
            b.newline()
            # _слово_ — пример на изучаемом языке, выделяется курсивом.
            for index, part in enumerate(re.split(r"_([^_]+)_", str(phrase["rule"]).strip())):
                (b.italic if index % 2 else b.text_line)(part)
            b.newline()
        if phrase.get("tip"):
            b.spacer()
            b.bold("Полезно:")
            b.text_line(f" {str(phrase['tip']).strip()}")

    return b.build_stripped(reply_markup=ikb([
        [("🎯 Запустить тренировку", f"a_train_{code}")],
        [(ui_label("game", "Угадать персонажа"), "a_game")],
        [("🎚️ Настроить", f"a_dictlang_{code}_from_menu"), ("#️⃣ Главная", "m_menu")],
    ]))


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


def food_menu(idea=None, *, meal="", news=None):
    """Главный экран Готовки: рецепт из холодильника на текущий приём пищи."""
    idea = idea or {}
    b = MessageBuilder()
    cuisine_code = str(idea.get("cuisine") or "").strip().lower()
    cuisine_name = CUISINE_RU.get(cuisine_code, CUISINE_RU["international"])
    title = " ".join(part for part in ("🍳 Что приготовить", _MEAL_TITLES.get(meal, "")) if part)
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
    missing = [str(item).strip() for item in missing if str(item).strip()]
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
        b.labeled_line("Полезно", tip)

    append_weekly_news(b, news)
    return b.build_stripped(reply_markup=food_card_kb())


def food_card_kb():
    """Кнопки рецепта: «Новый рецепт» открывает выбор приёма пищи и кухни под рецептом."""
    return ikb([
        [("✨ Новый рецепт", "food_pick")],
        [("🎚️ Настроить", "as_fridge_home"), ("#️⃣ Главная", "m_menu")],
    ])


_FOOD_MEALS = (("breakfast", "Завтрак"), ("lunch", "Обед"), ("dinner", "Ужин"))


def food_meal_kb():
    """Шаг 1 «Нового рецепта»: приём пищи."""
    rows = [[InlineKeyboardButton(label, callback_data=f"food_meal_{key}")]
            for key, label in _FOOD_MEALS]
    rows.append(nav_row("food_card"))
    return InlineKeyboardMarkup(rows)


def food_cuisine_kb(meal, cuisines):
    """Шаг 2: кухня для выбранного приёма пищи; cuisines — [(код, подпись)]."""
    rows = [[InlineKeyboardButton("Любая кухня", callback_data=f"food_go_{meal}_any")]]
    rows.extend([InlineKeyboardButton(label, callback_data=f"food_go_{meal}_{key}")]
                for key, label in cuisines)
    rows.append(nav_row("food_pick"))
    return InlineKeyboardMarkup(rows)


def food_empty_menu():
    b = MessageBuilder()
    b.section("🥣 Готовка")
    b.spacer()
    b.line("Добавь продукты, которые обычно есть дома.")
    b.spacer()
    b.line("Я буду подбирать простые рецепты из них и показывать, чего не хватает.")
    return b.build_stripped(reply_markup=ikb([
        [("✅ Добавить продукт", "as_fridge_add")],
        [("#️⃣ Главная", "m_menu")],
    ]))
