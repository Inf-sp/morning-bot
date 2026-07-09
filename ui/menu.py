from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .builder import MessageBuilder, MessageSpec
from .constants import LANGUAGE_EMOJI, ui_label, language_label

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
    b.section("Разделы")
    b.line(f"{ui_label('myday', 'Мой день')} — погода, сводка и советы.")
    b.line(f"{ui_label('wardrobe', 'Гардероб')} — что надеть и покупки.")
    b.line(f"{ui_label('health', 'Здоровье')} — мотивация, тревоги и здоровье.")
    b.line(f"{ui_label('learning', 'Обучение')} — языки, игра и практика.")
    b.line(f"{ui_label('travel', 'Путешествия')} — новые страны и планы поездок.")
    b.line(f"{ui_label('leisure', 'Досуг')} — фильмы, книги и музыка.")
    b.line(f"{ui_label('food', 'Готовка')} — рецепты и идеи из продуктов.")
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
            [("Разбор гардероба", "w_improve")],
            [(ui_label("find", "Проверка покупки"), "w_check")],
            [(ui_label("settings", "Настройки гардероба"), "set_wardrobe_g")],
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
            [(ui_label("health_history", "История самочувствия"), "as_diary")],
            [(ui_label("settings", "Настройки здоровья"), "set_lagom")],
        ],
    ),
    "m_learn": (
        UI_LEARNING,
        "Обучение",
        [
            f"Активный язык: {language_label('nl', 'Нидерландский')}.",
            "Слова и фразы из словаря автоматически попадают в тренировки.",
        ],
        [
            [(language_label("nl", "Нидерландский"), "m_nl")],
            [(ui_label("settings", "Настройки обучения"), "set_learning")],
        ],
    ),
    "m_dict_settings": (
        UI_SETTINGS,
        "Словарь и обучение",
        f"Активный язык: {language_label('nl', 'Нидерландский')}.",
        [
            [(ui_label("dictionary", "Нидерландский словарь"), "a_dictlang_nl")],
            [(ui_label("settings", "Настройки обучения"), "set_learning")],
            [("⬅️ Назад", "m_learn")],
        ],
    ),
    "m_nl": (
        LANGUAGE_EMOJI["nl"],
        "Нидерландский",
        "Практика языка: слова, живые выражения и игры.",
        [
            [(ui_label("word_trainer", "Тренажёр слов"), "a_train_words_nl")],
            [(ui_label("phrases", "Тренажёр фраз"), "a_train_phrases_nl")],
            [(ui_label("live_language", "Живой язык"), "a_proverb_nl")],
            [("Игра-детектив", "gamelang_nl")],
            [("⬅️ Назад", "m_learn")],
        ],
    ),
    "m_en": (
        LANGUAGE_EMOJI["en"],
        "Английский",
        "Практика языка: слова, живые выражения и игры.",
        [
            [(ui_label("word_trainer", "Тренажёр слов"), "a_train_words_en")],
            [(ui_label("phrases", "Тренажёр фраз"), "a_train_phrases_en")],
            [(ui_label("live_language", "Живой язык"), "a_proverb_en")],
            [("Игра-детектив", "gamelang_en")],
            [("⬅️ Назад", "m_learn")],
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
            [(ui_label("cinema", "Сейчас в кино"), "a_now_playing")],
            [(ui_label("leisure", "Посмотреть дома"), "a_watch")],
            [(ui_label("music", "Музыка"), "a_listen")],
            [(ui_label("books", "Книги"), "a_read")],
            [(ui_label("news", "Новости"), "a_news_home")],
            [(ui_label("settings", "Настройки досуга"), "m_leisure_settings")],
        ],
        False,
    ),
    "m_leisure_settings": (
        UI_SETTINGS,
        "Настройки досуга",
        "Любимые фильмы, страны, исполнители и книги для рекомендаций.",
        [
            [(ui_label("cinema", "Кино"), "ls_love_movies"), (ui_label("countries", "Страны"), "ls_love_countries")],
            [(ui_label("music", "Музыканты"), "ls_love_artists"), (ui_label("books", "Книги"), "ls_love_books")],
            [("⬅️ Назад", "m_leisure")],
        ],
    ),
}


def learning_menu(active_code="nl"):
    is_en = active_code == "en"
    flag = LANGUAGE_EMOJI["en"] if is_en else LANGUAGE_EMOJI["nl"]
    title = "Английский" if is_en else "Нидерландский"
    code = "en" if is_en else "nl"
    return _screen_message(
        UI_LEARNING,
        "Обучение",
        [
            f"Активный язык: {flag} {title}.",
            "Слова и фразы из словаря автоматически попадают в тренировки.",
        ],
        [
            [(f"{flag} {title}", f"m_{code}")],
            [(ui_label("settings", "Настройки обучения"), "set_learning")],
        ],
    )


def learning_settings_menu(active_code="nl"):
    is_en = active_code == "en"
    flag = LANGUAGE_EMOJI["en"] if is_en else LANGUAGE_EMOJI["nl"]
    title = "Английский" if is_en else "Нидерландский"
    code = "en" if is_en else "nl"
    return _screen_message(
        UI_SETTINGS,
        "Словарь и обучение",
        f"Активный язык: {flag} {title}.",
        [
            [(ui_label("dictionary", f"{title} словарь"), f"a_dictlang_{code}")],
            [(ui_label("settings", "Настройки обучения"), "set_learning")],
            [("⬅️ Назад", "m_learn")],
        ],
    )


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
