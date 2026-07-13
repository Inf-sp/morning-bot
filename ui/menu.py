from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .balance import finish_dot
from .builder import MessageBuilder, MessageSpec
from .constants import LANGUAGE_EMOJI, ui_label

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
    greeting = f"👋🏻 Привет, {name}! Я DM — помощник на каждый день." if name else "👋🏻 Привет! Я DM — помощник на каждый день."
    b = MessageBuilder()
    b.bold(greeting)
    b.newline()
    b.spacer()
    b.line("Помогу подобрать одежду, найти рецепт, потренировать язык, спланировать поездку или просто разобраться с вопросом.")
    b.spacer()
    b.line("Выбери раздел в меню или напиши мне напрямую.")
    b.spacer()
    b.line("Нажми ⭐ Сохранить, чтобы оставить на потом, или ❤️ В любимые, чтобы добавить в избранное.")
    return b.build()


_SCREENS = {
    "m_wardrobe": (
        UI_WARDROBE,
        "Гардероб",
        "Одежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. Чем полнее гардероб, тем точнее рекомендации.",
        [
            [(ui_label("recommendation", "Образ на сегодня"), "w_look")],
            [("✂️ Разбор шкафа", "w_improve"), (ui_label("find", "Оценка"), "w_check")],
            [("🎚️ Настройки гардероба", "set_wardrobe_settings")],
        ],
    ),
    "m_balance": (
        UI_HEALTH,
        "Здоровье",
        "Здоровье и эмоции. Разберу симптом, поддержу и помогу разгрузить голову.",
        [
            [(ui_label("doctor", "Спросить врача"), "as_doctor")],
            [("⚡ Заряд мотивации", "as_motiv"), (ui_label("worry_diary", "Дневник тревог"), "as_daycheck")],
            [(ui_label("settings", "Настройки здоровья"), "set_lagom")],
            [("⬅️ Назад", "m_menu"), ("🏠 Меню", "m_menu")],
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
            [("⬅️ Назад", "m_menu"), ("🏠 Меню", "m_menu")],
        ],
        False,
    ),
}


_MATERIAL_LABELS = {"word": "Слово дня", "phrase": "Фраза дня", "rule": "Правило дня"}


def _labeled_line(b: MessageBuilder, label: str, text: str):
    """Единый формат по всей карточке: '**Название:** текст' одной строкой,
    без переноса содержимого на следующую строку (см. docs/word-trainer.md)."""
    b.bold(f"{label}:")
    b.text_line(f" {text}")
    b.newline()
    return b


def learning_menu(home: dict):
    """Главный экран раздела 'Обучение' — сразу материал дня и переход в
    тренажёр, без описания возможностей и списка форматов (см. §27 CLAUDE.md
    и docs/word-trainer.md). `home` — результат learning.build_learning_home(cid);
    эта функция только рендерит готовые поля, не читает store и не выбирает
    материал сама."""
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
        label = _MATERIAL_LABELS.get(home.get("kind"), "Слово дня")
        _labeled_line(b, label, finish_dot(f"{home['term']} → {home['translation']}"))
        if home.get("example_text"):
            example = home["example_text"]
            if home.get("example_translation"):
                example += f" → {home['example_translation']}"
            _labeled_line(b, "Пример", finish_dot(example))
        if home.get("note"):
            _labeled_line(b, "Полезно", finish_dot(home["note"]))
        if home.get("focus"):
            _labeled_line(b, "Сегодня в фокусе", finish_dot(home["focus"]))

    return b.build_stripped(reply_markup=ikb([
        [(ui_label("word_trainer", "Тренажёр"), f"a_train_{code}")],
        [(ui_label("live_language", "Живой язык"), f"a_proverb_{code}"), (ui_label("game", "Игра-детектив"), f"gamelang_{code}")],
        [("📊 Прогресс", "a_train_progress")],
        [(ui_label("settings", "Настройки обучения"), "set_learning")],
        [("⬅️ Назад", "m_menu"), ("🏠 Меню", "m_menu")],
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


def food_menu(lifehacks=None):
    b = MessageBuilder()
    b.text_line(f"{UI_FOOD} ")
    b.bold("Готовка")
    b.newline()
    b.spacer()
    b.line("Еда без хаоса. Соберу понятное меню на день, разберу холодильник и честно скажу, что с ним не так.")
    if lifehacks:
        b.spacer()
        b.bold("Кухонный лайфхак:" if len(lifehacks) == 1 else "Кухонные лайфхаки:")
        b.newline()
        for tip in lifehacks:
            b.bullet(tip)
    _add_footer(b)
    rows = [
        [(ui_label("breakfast", "Завтрак"), "a_recipe_breakfast"), (ui_label("lunch", "Обед"), "a_recipe_lunch"), (ui_label("dinner", "Ужин"), "a_recipe_dinner")],
        [(ui_label("cook_from", "Из того что есть"), "as_fridge_cook")],
        [(ui_label("settings", "Настройки готовки"), "set_fridge_g")],
        [("⬅️ Назад", "m_menu"), ("🏠 Меню", "m_menu")],
    ]
    return b.build_stripped(reply_markup=ikb(rows))
