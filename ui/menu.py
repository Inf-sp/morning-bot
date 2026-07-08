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
        [
            "Активный язык: 🇳🇱 Нидерландский.",
            "Слова и фразы из словаря автоматически попадают в тренировки.",
        ],
        [
            [("🇳🇱 Нидерландский", "m_nl")],
            [("🎚️ Настройки обучения", "set_learning")],
        ],
    ),
    "m_dict_settings": (
        "🎚️",
        "Словарь и обучение",
        "Активный язык: 🇳🇱 Нидерландский.",
        [
            [("🎚️ Нидерландский словарь", "a_dictlang_nl")],
            [("🎚️ Настройки обучения", "set_learning")],
            [("⬅️ Назад", "m_learn")],
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
            [("⬅️ Назад", "m_learn")],
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
            [("⬅️ Назад", "m_learn")],
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
            [("🎤 Концерты", "a_concerts_find")], 
            [("🎬 Сейчас в кино", "a_now_playing")],
            [("🍿 Посмотреть дома", "a_watch")],
            [("🎧 Музыка", "a_listen")],
            [("📚 Книги", "a_read")],
            [("📰 Новости", "a_news_home")],
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
            [("⬅️ Назад", "m_leisure")],
        ],
    ),
}


def learning_menu(active_code="nl"):
    is_en = active_code == "en"
    flag = "🇬🇧" if is_en else "🇳🇱"
    title = "Английский" if is_en else "Нидерландский"
    code = "en" if is_en else "nl"
    return _screen_message(
        "📚",
        "Обучение",
        [
            f"Активный язык: {flag} {title}.",
            "Слова и фразы из словаря автоматически попадают в тренировки.",
        ],
        [
            [(f"{flag} {title}", f"m_{code}")],
            [("🎚️ Настройки обучения", "set_learning")],
        ],
    )


def learning_settings_menu(active_code="nl"):
    is_en = active_code == "en"
    flag = "🇬🇧" if is_en else "🇳🇱"
    title = "Английский" if is_en else "Нидерландский"
    code = "en" if is_en else "nl"
    return _screen_message(
        "🎚️",
        "Словарь и обучение",
        f"Активный язык: {flag} {title}.",
        [
            [(f"🎚️ {title} словарь", f"a_dictlang_{code}")],
            [("🎚️ Настройки обучения", "set_learning")],
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
        "🥣",
        "Готовка",
        "Еда без хаоса. Соберу понятное меню на день, разберу холодильник и честно скажу, что с ним не так.",
        [
            [("🥐 Завтрак", "a_recipe_breakfast"), ("🥗 Обед", "a_recipe_lunch"), ("🍲 Ужин", "a_recipe_dinner")],
            [("🥕 Из того что есть", "as_fridge_cook")],
            [("🎚️ Настройки холодильника", "set_fridge_g")],
        ],
    )
