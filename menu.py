from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

# Нижнее меню: Мой день широкий первый + категории в два столбца
MAIN_LABELS = ["☀️ Мой день", "👕 Гардероб", "🧠 Баланс", "📚 Обучение", "🍿 Досуг"]

MAIN_KB = ReplyKeyboardMarkup([
    ["☀️ Мой день"],
    ["👕 Гардероб", "🧠 Баланс"],
    ["📚 Обучение", "🍿 Досуг"],
], resize_keyboard=True)

# Reply-ярлык -> ключ инлайн-подменю
LABEL_TO_KEY = {
    "👕 Гардероб": "m_wardrobe",
    "🧠 Баланс": "m_balance",
    "📚 Обучение": "m_learn",
    "🍿 Досуг": "m_leisure",
}

_MENU_FOOTER = (
    "\n\n<b>Команды:</b>\n"
    "/setup — настройки\n"
    "/notes — сохранённые закладки\n\n"
    "Сохраняй полезное через ⭐ В закладки или ❤️ В любимые.\n\n"
    "Выбери 👇"
)

def _ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def _back(parent="m_close"):
    return [("⬅️ Назад", parent)]

def menu_screen(key):
    if key == "m_wardrobe":
        return (
            "👕 <b>Гардероб</b>\n\n"
            "Одежда без хаоса. Соберу тебе актуальный образ, разберу шкаф и честно скажу, что с ним не так."
            + _MENU_FOOTER,
            _ikb([
                [("✨ Сгенерировать образ", "w_look")],
                [("💡 Улучшить гардероб", "w_improve")],
                [("🛒 Проверка покупки", "w_check")],
            ])
        )
    if key == "m_balance":
        return (
            "🧠 <b>Баланс</b>\n\n"
            "Здоровье, эмоции и питание в одном месте.\n"
            "Разберу симптом, поддержу, помогу разгрузить голову и подскажу, что приготовить."
            + _MENU_FOOTER,
            _ikb([
                [("👩🏻‍⚕️ Вопрос врачу", "as_doctor")],
                [("👨‍🍳 Кулинарный радар", "m_food")],
                [("🎯 Личная мотивация", "as_motiv")],
                [("😌 Дневник тревоги", "as_daycheck")],
            ])
        )
    if key == "m_food":
        return (
            "👨‍🍳 <b>Кулинарный радар</b>\n\n"
            "Подберу рецепт под приём пищи или помогу с остатками."
            + _MENU_FOOTER,
            _ikb([
                [("🍳 Завтрак", "a_food_breakfast")],
                [("🥗 Обед", "a_food_lunch")],
                [("🍽️ Ужин", "a_food_dinner")],
                [("🧊 Из холодильника", "as_fridge_cook")],
                [("🧊 Мой холодильник", "as_fridge")],
                [("⬅️ Назад", "m_balance")],
            ])
        )
    if key in ("m_learn", "m_nl", "m_en"):
        return (
            "📚 <b>Обучение</b>\n\n"
            "Тренируй слова и обучайся — играя!"
            + _MENU_FOOTER,
            _ikb([
                [("🧠 Тренажёр", "a_train")],
                [("💬 Пословица", "a_proverb")],
                [("🕵️ Игра-детектив", "a_game")],
            ])
        )
    if key == "m_leisure":
        return (
            "🍿 <b>Досуг</b>\n\n"
            "Фильмы, музыка, книги и путешествия — под твой вкус."
            + _MENU_FOOTER,
            _ikb([
                [("✈️ Путешествия", "a_trav_go")],
                [("🎬 Фильмы и сериалы", "a_watch")],
                [("📖 Книги", "a_read")],
                [("🎸 Музыка", "a_listen")],
                [("🎫 Поиск по мероприятиям", "a_concerts_find")],
            ])
        )
    return ("Меню снизу 👇", None)
