import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .balance import finish_dot
from .builder import MessageBuilder, MessageSpec
from .constants import LANGUAGE_EMOJI, choose_label, ui_label
from .food import compact_step_lines, pairing_text

UI_MYDAY = ui_label("myday", "").strip()
UI_WARDROBE = ui_label("wardrobe", "").strip()
UI_FOOD = ui_label("food", "").strip()
UI_LEARNING = ui_label("learning", "").strip()
UI_HEALTH = ui_label("health", "").strip()
UI_TRAVEL = ui_label("travel", "").strip()
UI_LEISURE = ui_label("leisure", "").strip()
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
        [(ui_label("learning", "Обучение"), "m_learn"), (ui_label("health", "Здоровье"), "m_balance")],
        [(ui_label("travel", "Поездки"), "m_travel"), (ui_label("leisure", "Досуг"), "m_leisure")],
        [(ui_label("settings", "Настройки"), "m_notes")],
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
    b.line("Подберу образ по погоде, найду рецепт из продуктов дома, помогу с языком или спланирую поездку.")
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
    "m_wardrobe": (
        UI_WARDROBE,
        "Гардероб",
        "Одежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. Чем полнее гардероб, тем точнее рекомендации.",
        [
            [(ui_label("recommendation", "Образ на сегодня"), "w_look")],
            [(ui_label("assessment", "Проверить покупку"), "w_check")],
            [("✂️ Разбор шкафа", "w_improve")],
            [(choose_label("Выбрать стили"), "set_wardrobe_style")],
            [("⬅️ Назад", "m_menu"), ("#️⃣ Меню", "m_menu")],
        ],
    ),
    "m_balance": (
        UI_HEALTH,
        "Здоровье",
        "Здоровье и эмоции. Разберу симптом, поддержу и помогу разгрузить голову.",
        [
            [(ui_label("doctor", "Спросить врача"), "as_doctor")],
            [("⚡ Мотивация", "as_motiv"), (ui_label("worry_diary", "Мысли"), "as_daycheck")],
            [("Мой лагом", "set_lagom")],
            [(choose_label("Выбрать принципы"), "as_health_principles")],
            [("⬅️ Назад", "m_menu"), ("#️⃣ Меню", "m_menu")],
        ],
    ),
    "m_leisure": (
        UI_LEISURE,
        "Досуг",
        [
            "Фильмы, музыка и книги - под твой вкус.",
            "Предпочтения и сохранённое - в настройках.",
        ],
        [
            [(ui_label("concerts", "Концерты"), "a_concerts_find"), (ui_label("leisure", "Кино"), "a_watch")],
            [(ui_label("music", "Музыка"), "a_listen"), (ui_label("books", "Книги"), "a_read")],
            [(ui_label("settings", "Настройки досуга"), "set_mydata_leisure")],
            [("⬅️ Назад", "m_menu"), ("#️⃣ Меню", "m_menu")],
        ],
        False,
    ),
}


def learning_menu(home: dict):
    """Главный экран обучения: материал дня, прогресс и следующий шаг."""
    code = home.get("lang_code", "nl")
    flag = LANGUAGE_EMOJI.get(code, LANGUAGE_EMOJI["nl"])
    title = "Английский" if code == "en" else "Нидерландский"

    b = MessageBuilder()
    b.text_line(f"{UI_LEARNING} ")
    b.bold(f"Обучение · {title} {flag}")
    b.newline()
    b.spacer()

    if not home.get("has_material"):
        b.line("В словаре пока нет слов с переводом — начни с тренажёра, он поможет добавить первые.")
    else:
        b.bold("Конструкция дня:")
        b.text_line(" ")
        b.italic(home["term"])
        b.text_line(f" → {finish_dot(home['translation'])}")
        b.newline()
        b.line("Сегодня тренажёр поможет запомнить перевод и пример. Новые типы заданий откроются после закрепления слов.")

    progress = home.get("progress") or {}
    b.spacer()
    b.bold("Прогресс:")
    b.text_line(" Слов и фраз ")
    b.bold(str(progress.get("total", 0)))
    b.text_line(" изучаю · ")
    b.bold(str(progress.get("due_count", 0)))
    b.text_line(" повторить · ")
    b.bold(f"{progress.get('no_hint_pct', 0)}%")
    b.text_line(" без подсказок")
    b.newline()
    b.spacer()
    b.bold("Следующая цель:")
    b.text_line(" Перевод и понимание → самостоятельное вспоминание.")

    return b.build_stripped(reply_markup=ikb([
        [(ui_label("word_trainer", "Тренажёр"), f"a_train_{code}")],
        [(ui_label("live_language", "Живой язык"), f"a_proverb_{code}"), (ui_label("game", "Игра-детектив"), f"gamelang_{code}")],
        [("📖 Мой словарь", f"a_dictlang_{code}_from_menu")],
        [(choose_label("Выбрать язык"), "set_learning")],
        [("⬅️ Назад", "m_menu"), ("#️⃣ Меню", "m_menu")],
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
    value = _COOKING_EMOJI_RE.sub("", str(value or ""))
    return " ".join(value.split()).strip(" -•")


def _cooking_sentence(value) -> str:
    value = _cooking_text(value)
    if value and value[-1] not in ".!?…":
        value += "."
    return value


def food_menu(idea=None):
    """Главный экран Готовки: один полный рецепт из холодильника."""
    idea = idea or {}
    b = MessageBuilder()
    b.section(f"{UI_FOOD} Готовка · Идея на сегодня")

    reason = _cooking_sentence(idea.get("reason"))
    if reason:
        b.spacer()
        b.italic(reason)
        b.newline()

    name = _cooking_text(idea.get("name"))
    if name:
        b.spacer()
        b.bold(name)

    servings = _cooking_text(idea.get("servings"))
    if servings:
        b.newline()
        b.line(f"👤 {servings}")

    ingredients = [_cooking_text(item) for item in (idea.get("ingredients") or [])]
    ingredients = [item for item in ingredients if item]
    if ingredients:
        b.spacer()
        b.bold("Ингредиенты:")
        b.newline()
        b.line(", ".join(ingredients))

    missing = [_cooking_text(item) for item in (idea.get("missing_ingredients") or [])]
    missing = [item for item in missing if item]
    if missing:
        b.spacer()
        b.bold("Не хватает:")
        b.newline()
        b.line(", ".join(missing))

    steps = compact_step_lines(idea.get("steps") or [])
    if steps:
        b.spacer()
        b.bold("Приготовление:")
        b.newline()
        for step in steps:
            b.bullet(step)

    pairing = pairing_text({
        "pairing_wine": _cooking_text(idea.get("pairing_wine")),
        "pairing_drink": _cooking_text(idea.get("pairing_drink")),
    })
    if pairing:
        b.spacer()
        b.line(f"К блюду подойдет: {pairing}")

    tip = _cooking_sentence(idea.get("tip"))
    if tip:
        b.spacer()
        b.text_line("💡 ")
        b.labeled_line("Полезно", tip)

    rows = [
        [("✨ Другой рецепт", "m_food_next")],
        [(ui_label("breakfast", "Завтрак"), "a_recipe_breakfast"), (ui_label("lunch", "Обед"), "a_recipe_lunch"), (ui_label("dinner", "Ужин"), "a_recipe_dinner")],
        [("🧊 Мой холодильник", "as_fridge_home")],
        [(choose_label("Выбрать кухни"), "set_cuisines")],
        [("⬅️ Назад", "m_menu"), ("#️⃣ Меню", "m_menu")],
    ]
    return b.build_stripped(reply_markup=ikb(rows))
