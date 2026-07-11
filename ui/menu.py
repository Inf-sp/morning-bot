from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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


def welcome():
    b = MessageBuilder()
    b.bold("Привет! Я DM — твой помощник на каждый день.")
    b.newline()
    b.spacer()
    b.line("Помогаю с погодой, одеждой, языками, рецептами, досугом и полезными привычками.")
    b.section("Разделы:")
    b.bold(ui_label("myday", "Мой день"))
    b.line(" — погода, сводка и советы.")

    b.bold(ui_label("wardrobe", "Гардероб"))
    b.line(" — что надеть и покупки.")

    b.bold(ui_label("food", "Готовка"))
    b.line(" — рецепты и идеи из продуктов.")

    b.bold(ui_label("learning", "Обучение"))
    b.line(" — языки, игра и практика.")

    b.bold(ui_label("health", "Здоровье"))
    b.line(" — мотивация, тревоги и здоровье.")

    b.bold(ui_label("travel", "Путешествия"))
    b.line(" — новые страны и планы поездок.")

    b.bold(ui_label("leisure", "Досуг"))
    b.line(" — фильмы, книги и музыка.")
    
    b.spacer()
    b.line("Просто напиши вопрос в чат, и я помогу.")
    b.spacer()
    b.text_line(f"Изменить параметры или посмотреть сохранённую информацию можно в {UI_SETTINGS} ")
    b.bold("Настройках")
    b.text_line(".")
    return b.build()


_SCREENS = {
    "m_wardrobe": (
        UI_WARDROBE,
        "Гардероб",
        "Одежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. Чем полнее гардероб, тем точнее рекомендации.",
        [
            [(ui_label("recommendation", "Образ на сегодня"), "w_look")],
            [("👕 Разбор гардероба", "w_improve")],
            [(ui_label("find", "Проверка покупки"), "w_check")],
            [("👔 Мой гардероб", "set_wardrobe_g")],
        ],
    ),
    "m_balance": (
        UI_HEALTH,
        "Здоровье",
        "Здоровье и эмоции. Разберу симптом, поддержу и помогу разгрузить голову.",
        [
            [(ui_label("doctor", "Спросить врача"), "as_doctor")],
            [(ui_label("recommendation", "Заряд мотивации"), "as_motiv")],
            [(ui_label("worry_diary", "Дневник тревог"), "as_daycheck")],
            [(ui_label("settings", "Настройки здоровья"), "set_lagom")],
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
            [(ui_label("concerts", "Концерты"), "a_concerts_find")],
            [(ui_label("leisure", "Кино"), "a_watch")],
            [(ui_label("music", "Музыка"), "a_listen")],
            [(ui_label("books", "Книги"), "a_read")],
            [(ui_label("settings", "Настройки досуга"), "set_mydata_leisure")],
        ],
        False,
    ),
}


def learning_menu(active_code="nl"):
    """Единое меню обучения — без промежуточного экрана выбора языка. Показывает
    сразу тренажёр/живой язык/игру/словарь для текущего активного языка."""
    is_en = active_code == "en"
    flag = LANGUAGE_EMOJI["en"] if is_en else LANGUAGE_EMOJI["nl"]
    title = "Английский" if is_en else "Нидерландский"
    code = "en" if is_en else "nl"

    b = MessageBuilder()
    b.text_line(f"{UI_LEARNING} ")
    b.bold("Обучение")
    b.newline()
    b.spacer()
    b.text_line("Сейчас учим: ")
    b.text_line(f"{flag} ")
    b.bold(title)
    b.newline()
    b.spacer()
    b.line("Тренажёр, живой язык, игра и личный словарь — для этого языка.")
    b.spacer()
    b.quote("добавь слово ... / добавь фразу ...")
    b.spacer()
    b.line("Напиши так в чат — бот сам сохранит, переведёт и разберёт.")

    return b.build_stripped(reply_markup=ikb([
        [("⚡ Быстрая практика · 3 минуты", "session3_start")],
        [(ui_label("word_trainer", "Тренажёр"), f"a_train_{code}")],
        [("💬 Диалог", "dlg_start")],
        [(ui_label("live_language", "Живой язык"), f"a_proverb_{code}")],
        [(ui_label("game", "Игра-детектив"), f"gamelang_{code}")],
        [(ui_label("dictionary", "Мой словарь"), f"a_dictlang_{code}_from_menu")],
        [("🧠 Повторение ошибок", "mistake_review")],
        [(ui_label("settings", "Настройки обучения"), "set_learning")],
    ]))


def menu_screen(key):
    if key not in _SCREENS:
        return MessageSpec(text="Выбери раздел в нижнем меню.")
    screen = _SCREENS[key]
    if len(screen) == 4:
        emoji, title, description, rows = screen
        show_footer = True
    else:
        emoji, title, description, rows, show_footer = screen
    return _screen_message(emoji, title, description, rows, show_footer=show_footer)


def food_menu():
    return _screen_message(
        UI_FOOD,
        "Готовка",
        "Еда без хаоса. Соберу понятное меню на день, разберу холодильник и честно скажу, что с ним не так.",
        [
            [(ui_label("breakfast", "Завтрак"), "a_recipe_breakfast"), (ui_label("lunch", "Обед"), "a_recipe_lunch"), (ui_label("dinner", "Ужин"), "a_recipe_dinner")],
            [(ui_label("cook_from", "Из того что есть"), "as_fridge_cook")],
            [(ui_label("settings", "Настройки холодильника"), "set_fridge_g")],
        ],
    )
