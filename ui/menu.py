from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .builder import MessageBuilder, MessageSpec


MENU_FOOTER = (
    "\n\nИзменить параметры или посмотреть сохранённую информацию можно в 🎚️ <b>Настройках</b>."
)


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
    b.line("🍿 Досуг — фильмы, книги, музыка и поездки.")
    b.line("🥣 Готовка — рецепты и идеи из продуктов.")
    b.spacer()
    b.line("Просто напиши вопрос в чат и я помогу 💬")
    b.spacer()
    b.text_line("Изменить параметры или посмотреть сохранённую информацию можно в 🎚️ ")
    b.bold("Настройках")
    b.text_line(".")
    return b.build()


def menu_screen(key):
    screens = {
        "m_wardrobe": (
            "👕 <b>Гардероб</b>\n\nОдежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. Чем полнее гардероб, тем точнее рекомендации.",
            [
                [("✨ Образ на сегодня", "w_look")],
                [("🧥 Разбор гардероба", "w_improve")],
                [("🔎 Проверка покупки", "w_check")],
                [("🎚️ Настройки гардероба", "set_wardrobe_g")],
            ],
        ),
        "m_balance": (
            "🚑 <b>Здоровье</b>\n\nЗдоровье и эмоции. Разберу симптом, поддержу и помогу разгрузить голову.",
            [
                [("👩🏻‍⚕️ Спросить врача", "as_doctor")],
                [("⚡️ Заряд мотивации", "as_motiv")],
                [("📓 Дневник тревог", "as_daycheck")],
                [("🎚️ Настройки здоровья", "set_lagom")],
            ],
        ),
        "m_learn": (
            "📚 <b>Обучение</b>\n\nВыбери язык — и вперёд!",
            [
                [("🇳🇱 Нидерландский язык", "m_nl")],
                [("🇬🇧 Английский язык", "m_en")],
                [("🎚️ Настройки обучения", "m_dict_settings")],
            ],
        ),
        "m_dict_settings": (
            "🎚️ <b>Словари и языки</b>\n\nУправляй словарём и уровнем языка.",
            [
                [("🎚️ Нидерландский словарь", "a_dictlang_nl")],
                [("🎚️ Английский словарь", "a_dictlang_en")],
                [("🎚️ Уровень языка", "a_levels")],
                [("◀️ Назад", "m_learn")],
            ],
        ),
        "m_nl": (
            "🇳🇱 <b>Нидерландский</b>\n\nПрактика языка: слова, живые выражения и игры.",
            [
                [("🧠 Тренажёр", "a_train_nl")],
                [("💭 Живой язык", "a_proverb_nl")],
                [("🕵️ Игра-детектив", "gamelang_nl")],
                [("◀️ Назад", "m_learn")],
            ],
        ),
        "m_en": (
            "🇬🇧 <b>Английский</b>\n\nПрактика языка: слова, живые выражения и игры.",
            [
                [("🧠 Тренажёр", "a_train_en")],
                [("💭 Живой язык", "a_proverb_en")],
                [("🕵️ Игра-детектив", "gamelang_en")],
                [("◀️ Назад", "m_learn")],
            ],
        ),
        "m_leisure": (
            "🍿 <b>Досуг</b>\n\nФильмы, музыка, книги и путешествия — под твой вкус.",
            [
                [("✈️ Планирование путешествий", "a_trav_go")],
                [("🎫 Поиск по концертам", "a_concerts_find")],
                [("🎸 Подбор музыкантов", "a_listen")],
                [("🎬 Подбор кино", "a_watch")],
                [("📖 Подбор книг", "a_read")],
                [("🎚️ Настройки досуга", "m_leisure_settings")],
            ],
        ),
        "m_leisure_settings": (
            "🎚️ <b>Настройки досуга</b>\n\nСписки, которые бот использует для рекомендаций фильмов, поездок, музыки и книг.",
            [
                [("🎚️ Кино", "ls_love_movies"), ("🎚️ Страны", "ls_love_countries")],
                [("🎚️ Музыканты", "ls_love_artists"), ("🎚️ Книги", "ls_love_books")],
                [("◀️ Назад", "m_leisure")],
            ],
        ),
    }
    if key not in screens:
        return MessageSpec(text="Выбери раздел в нижнем меню.", reply_markup=None, parse_mode="HTML")
    text, rows = screens[key]
    return MessageSpec(text=text + MENU_FOOTER, reply_markup=ikb(rows), parse_mode="HTML")


def food_menu():
    return MessageSpec(
        text=(
            "🥣 <b>Готовка</b>\n\n"
            "Еда без хаоса. Соберу понятное меню на день, разберу холодильник и честно скажу, что с ним не так."
            + MENU_FOOTER
        ),
        parse_mode="HTML",
        reply_markup=ikb([
            [("🍳 Завтрак", "a_recipe_breakfast"), ("🥗 Обед", "a_recipe_lunch"), ("🍽️ Ужин", "a_recipe_dinner")],
            [("🥕 Из того что есть", "as_fridge_cook")],
            [("🎚️ Настройки холодильника", "set_fridge_g")],
        ]),
    )
