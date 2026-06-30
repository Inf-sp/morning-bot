from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

WELCOME = (
    "👋 <b>Привет! Я DM — твой помощник на каждый день.</b>\n\n"
    "<b>Помогаю с погодой, одеждой, языками, рецептами, досугом и полезными привычками.</b>\n\n"
    "<b>Разделы</b>\n"
    "☀️ <b>Мой день</b> — погода, сводка и советы.\n"
    "👕 <b>Гардероб</b> — что надеть и покупки.\n"
    "🧬 <b>Здоровье</b> — мотивация, тревоги и здоровье.\n"
    "📚 <b>Обучение</b> — языки, игра и практика.\n"
    "🍿 <b>Досуг</b> — фильмы, книги, музыка и поездки.\n"
    "🥣 <b>Готовка</b> — рецепты и идеи из продуктов.\n\n"
    "Просто напиши вопрос в чат и я помогу 💬\n\n"
    "Изменить параметры или посмотреть сохранённую информацию можно в 🎚️ <b>Настройках</b>."
)

def main_kb(cid=None):
    rows = [
        ["☀️ Мой день"],
        ["👕 Гардероб", "🚑 Здоровье"],
        ["📚 Обучение", "🍿 Досуг"],
        ["🥣 Готовка", "🎚️ Настройки"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# Нижнее меню: Мой день широкий первый + категории в два столбца
MAIN_LABELS = [
    "☀️ Мой день",
    "👕 Гардероб",
    "🧬 Здоровье",
    "📚 Обучение",
    "🍿 Досуг",
    "🥣 Готовка",
    "🎚️ Настройки",
]

# Reply-ярлык -> ключ инлайн-подменю
LABEL_TO_KEY = {
    "👕 Гардероб": "m_wardrobe",
    "🚑 Здоровье": "m_balance",
    "📚 Обучение": "m_learn",
    "🍿 Досуг": "m_leisure",
    "🥣 Готовка": "m_food",
    "🎚️ Настройки": "m_notes",
}

_MENU_FOOTER = (
    "\n\n<b>Выбери действие 👇</b>"
)

def _ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def _back(parent="m_close"):
    return [("◀️ Назад", parent)]

def menu_screen(key):
    if key == "m_wardrobe":
        return (
            "👕 <b>Гардероб</b>\n\n"
            "Одежда без хаоса. Соберу тебе актуальный образ, разберу шкаф и честно скажу, что с ним не так."
            + _MENU_FOOTER,
            _ikb([
                [("✨ Образ на сегодня", "w_look")],
                [("🧥 Разбор гардероба", "w_improve")],
                [("🔎 Проверка покупки", "w_check")],
                [("🎚️ Настройки гардероба", "set_wardrobe_g")],
            ])
        )
    if key == "m_balance":
        return (
            "🚑 <b>Здоровье</b>\n\n"
            "Здоровье и эмоции. Разберу симптом, поддержу и помогу разгрузить голову."
            + _MENU_FOOTER,
            _ikb([
                [("👩🏻‍⚕️ Вопрос врачу", "as_doctor")],
                [("☕️ Мотивация", "as_motiv"), ("📓 Дневник тревог", "as_daycheck")],
                [("🎚️ Настройки здоровья", "set_lagom")],

            ])
        )
    if key == "m_food_gen":
        return (
            "👨‍🍳 <b>Сгенерировать рецепт</b>\n\nВыбери приём пищи 👇",
            _ikb([
                [("🍳 Завтрак", "a_recipe_breakfast")],
                [("🥗 Обед", "a_recipe_lunch")],
                [("🍽️ Ужин", "a_recipe_dinner")],
                [("◀️ Назад", "m_food")],
            ])
        )
    if key == "m_learn":
        return (
            "📚 <b>Обучение</b>\n\n"
            "Выбери язык — и вперёд!"
            + _MENU_FOOTER,
            _ikb([
                [("🇳🇱 Нидерландский", "m_nl"), ("🇬🇧 Английский", "m_en")],
                [("🎚️ Настройки обучения", "m_dict_settings")],
            ])
        )
    if key == "m_dict_settings":
        return (
            "📖 <b>Словари и языки</b>\n\n"
            "Управляй словарём и уровнем языка."
            + _MENU_FOOTER,
            _ikb([
                [("🎚️ Нидерландский", "a_dictlang_nl"), ("🎚️ Английский", "a_dictlang_en")],
                [("🎚️ Уровень языка (настройка)", "a_levels")],
                [("◀️ Назад", "m_learn")],
            ])
        )
    if key == "m_nl":
        return (
            "🇳🇱 <b>Нидерландский</b>\n\n"
            "Практика языка: слова, грамматика, живые выражения и игры."
            + _MENU_FOOTER,
            _ikb([
                [("🧠 Тренажёр", "a_train_nl"), ("📘 Грамматика", "gm_lang_nl")],
                [("💭 Живой язык", "a_proverb_nl"), ("🕵️ Игра-детектив", "gamelang_nl")],
                [("🧩 Артикли", "dh_start")],
                [("◀️ Назад", "m_learn")],
            ])
        )
    if key == "m_en":
        return (
            "🇬🇧 <b>Английский</b>\n\n"
            "Практика языка: слова, грамматика, живые выражения и игры."
            + _MENU_FOOTER,
            _ikb([
                [("🧠 Тренажёр", "a_train_en"), ("📘 Грамматика", "gm_lang_en")],
                [("💭 Живой язык", "a_proverb_en"), ("🕵️ Игра-детектив", "gamelang_en")],
                [("◀️ Назад", "m_learn")],
            ])
        )
    if key == "m_leisure":
        return (
            "🍿 <b>Досуг</b>\n\n"
            "Фильмы, музыка, книги и путешествия — под твой вкус."
            + _MENU_FOOTER,
            _ikb([
                [("✈️ Путешествия", "a_trav_go"), ("🎬 Кино", "a_watch")],
                [("📖 Книги", "a_read"), ("🎸 Музыка", "a_listen"), ("🎫 Концерты", "a_concerts_find")],
                [("🎚️ Досуг (настройки)", "m_leisure_settings")],
            ])
        )
    if key == "m_leisure_settings":
        return (
            "🎚️ <b>Настройки досуга</b>\n\n"
            "Списки, которые бот использует для рекомендаций фильмов, поездок, музыки и книг."
            + _MENU_FOOTER,
            _ikb([
                [("🎚️ Кино", "ls_love_movies"), ("🎚️ Страны", "ls_love_countries")],
                [("🎚️ Артисты", "ls_love_artists"), ("🎚️ Книги", "ls_love_books")],
                [("◀️ Назад", "m_leisure")],
            ])
        )
    return ("Меню снизу 👇", None)


async def send_food_menu(bot, cid):
    kb = _ikb([
        [("🍳 Завтрак", "a_recipe_breakfast"), ("🥗 Обед", "a_recipe_lunch"), ("🍽️ Ужин", "a_recipe_dinner")],
        [("🥕 Из того что есть", "as_fridge_cook")],
        [("🎚️ Холодильник (настройки)", "set_fridge_g")],
    ])
    await bot.send_message(
        chat_id=cid,
        text=(
            "🥣 <b>Готовка</b>\n\n"
            "Еда без хаоса. Соберу понятное меню на день, разберу холодильник и честно скажу, что с ним не так."
            + _MENU_FOOTER
        ),
        parse_mode="HTML",
        reply_markup=kb,
    )
