from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .builder import MessageBuilder, MessageSpec


def _add_footer(b: MessageBuilder):
    """Общий футер для экранов меню: подсказка про Настройки, bold только на слове."""
    b.spacer()
    b.text_line("Изменить параметры или посмотреть сохранённую информацию можно в 🎚️ ")
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
    b.bold("👋 Привет! Я DM — твой помощник на каждый день.")
    b.newline()
    b.spacer()
    b.line("Помогаю с погодой, одеждой, языками, рецептами, досугом и полезными привычками.")
    b.section("Разделы")
    b.line("☀️ Мой день — погода, сводка и советы.")
    b.line("👕 Гардероб — что надеть и покупки.")
    b.line("🚑 Здоровье — мотивация, тревоги и здоровье.")
    b.line("📚 Обучение — языки, игра и практика.")
    b.line("✈️ Путешествия — новые страны и планы поездок.")
    b.line("🍿 Досуг — фильмы, книги и музыка.")
    b.line("🥣 Готовка — рецепты и идеи из продуктов.")
    b.spacer()
    b.line("Просто напиши вопрос в чат и я помогу 💬")
    b.spacer()
    b.text_line("Изменить параметры или посмотреть сохранённую информацию можно в 🎚️ ")
    b.bold("Настройках")
    b.text_line(".")
    return b.build()


_SCREENS = {
    "m_wardrobe": (
        "👕",
        "Гардероб",
        "Одежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. Чем полнее гардероб, тем точнее рекомендации.",
        [
            [("✨ Образ на сегодня", "w_look")],
            [("🧥 Разбор гардероба", "w_improve")],
            [("🔎 Проверка покупки", "w_check")],
            [("🎚️ Настройки гардероба", "set_wardrobe_g")],
        ],
    ),
    "m_balance": (
        "🚑",
        "Здоровье",
        "Здоровье и эмоции. Разберу симптом, поддержу и помогу разгрузить голову.",
        [
            [("👩🏻‍⚕️ Спросить врача", "as_doctor")],
            [("⚡️ Заряд мотивации", "as_motiv")],
            [("📓 Дневник тревог", "as_daycheck")],
            [("📝 История самочувствия", "as_diary")],
            [("🎚️ Настройки здоровья", "set_lagom")],
        ],
    ),
    "m_learn": (
        "📚",
        "Обучение",
        "Выбери язык — и вперёд!",
        [
            [("🇳🇱 Нидерландский язык", "m_nl")],
            [("🇬🇧 Английский язык", "m_en")],
            [("🎚️ Настройки обучения", "m_dict_settings")],
        ],
    ),
    "m_dict_settings": (
        "🎚️",
        "Словари и языки",
        "Управляй словарём и уровнем языка.",
        [
            [("🎚️ Нидерландский словарь", "a_dictlang_nl")],
            [("🎚️ Английский словарь", "a_dictlang_en")],
            [("🎚️ Уровень языка", "a_levels")],
            [("◀️ Назад", "m_learn")],
        ],
    ),
    "m_nl": (
        "🇳🇱",
        "Нидерландский",
        "Практика языка: слова, живые выражения и игры.",
        [
            [("🧠 Тренажёр слов", "a_train_words_nl")],
            [("🧩 Тренажёр фраз", "a_train_phrases_nl")],
            [("💭 Живой язык", "a_proverb_nl")],
            [("🕵️ Игра-детектив", "gamelang_nl")],
            [("◀️ Назад", "m_learn")],
        ],
    ),
    "m_en": (
        "🇬🇧",
        "Английский",
        "Практика языка: слова, живые выражения и игры.",
        [
            [("🧠 Тренажёр слов", "a_train_words_en")],
            [("🧩 Тренажёр фраз", "a_train_phrases_en")],
            [("💭 Живой язык", "a_proverb_en")],
            [("🕵️ Игра-детектив", "gamelang_en")],
            [("◀️ Назад", "m_learn")],
        ],
    ),
    "m_leisure": (
        "🍿",
        "Досуг",
        [
            "Фильмы, музыка и книги - под твой вкус.",
            "Предпочтения и сохранённое - в настройках.",
        ],
        [
            [("🎤 Концерты", "a_concerts_find"), ("🎬 Что в кино", "a_now_playing")],
            [("🍿 Что посмотреть", "a_watch")],
            [("🎧 Музыка для тебя", "a_listen")],
            [("📚 Книги для тебя", "a_read")],
            [("⚙️ Настройки досуга", "m_leisure_settings")],
        ],
        False,
    ),
    "m_leisure_settings": (
        "⚙️",
        "Настройки досуга",
        "Любимые фильмы, страны, исполнители и книги для рекомендаций.",
        [
            [("🎚️ Кино", "ls_love_movies"), ("🎚️ Страны", "ls_love_countries")],
            [("🎚️ Музыканты", "ls_love_artists"), ("🎚️ Книги", "ls_love_books")],
            [("◀️ Назад", "m_leisure")],
        ],
    ),
}


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
        "🥣",
        "Готовка",
        "Еда без хаоса. Соберу понятное меню на день, разберу холодильник и честно скажу, что с ним не так.",
        [
            [("🥐 Завтрак", "a_recipe_breakfast"), ("🥗 Обед", "a_recipe_lunch"), ("🍲 Ужин", "a_recipe_dinner")],
            [("🥕 Из того что есть", "as_fridge_cook")],
            [("🎚️ Настройки холодильника", "set_fridge_g")],
        ],
    )
