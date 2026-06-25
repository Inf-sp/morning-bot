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
    "🧠 Баланс": "m_balance",
    "📚 Обучение": "m_learn",
    "🍿 Досуг": "m_leisure",
}

def _ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def _back(parent="m_close"):
    return [("⬅️ Назад", parent)]

def menu_screen(key):
    if key == "m_balance":
        return ("🧠 <b>Баланс</b>\n\nЗдоровье, эмоции и питание в одном месте.\n"
                "Разберу симптом, поддержу, помогу разгрузить голову и подскажу, что приготовить.\n\nВыбери 👇", _ikb([
            [("👩🏻‍⚕️ Вопрос врачу", "as_doctor")],
            [("👨‍🍳 Кулинарный радар", "m_food")],
            [("🎯 Личная мотивация", "as_motiv")],
            [("😌 Дневник тревоги", "as_daycheck")],
        ]))
    if key == "m_food":
        return ("👨‍🍳 <b>Кулинарный радар</b>\n\nПодберу рецепт под приём пищи или помогу с остатками.\n\nВыбери 👇", _ikb([
            [("🍳 Завтрак", "a_food_breakfast")],
            [("🥗 Обед", "a_food_lunch")],
            [("🍽️ Ужин", "a_food_dinner")],
            [("🧊 Мой холодильник", "as_fridge")],
            [("⬅️ Назад", "m_balance")],
        ]))
    if key in ("m_learn", "m_nl", "m_en"):
        return ("📚 <b>Обучение</b>\n\nВыбери действие и язык 👇\n"
                "<i>Уровень языка: /setup</i>", _ikb([
            [("📖 Грамматика 🇳🇱", "a_gram_nl"), ("📖 Грамматика 🇬🇧", "a_gram_en")],
            [("🧠 Тренажёр 🇳🇱", "a_train_nl"), ("🧠 Тренажёр 🇬🇧", "a_train_en")],
            [("📝 Перевод 🇳🇱", "a_tr_nl"), ("📝 Перевод 🇬🇧", "a_tr_en")],
            [("💬 Пословица 🇳🇱", "a_proverb_nl"), ("💬 Пословица 🇬🇧", "a_proverb_en")],
            [("🕵️ Игра-детектив", "a_game")],
            [("🗂️ Словарь и темы", "m_dict")],
        ]))
    if key == "m_dict":
        return ("🗂️ <b>Словарь и темы</b>\n\nМои слова и изучаемые темы по языкам.\n\nВыбери 👇", _ikb([
            [("🇳🇱 Слова NL", "a_dictlang_nl"), ("🇬🇧 Слова EN", "a_dictlang_en")],
            [("🤓 Темы NL", "a_topics_nl"), ("🤓 Темы EN", "a_topics_en")],
            _back("m_learn"),
        ]))
    if key == "m_leisure":
        return ("🍿 <b>Досуг</b>\n\nФильмы, музыка, книги и путешествия - под твой вкус.\n\nВыбери 👇", _ikb([
            [("✈️ Путешествия", "a_trav_go")],
            [("🎬 Фильмы и сериалы", "a_watch")],
            [("📖 Книги", "a_read")],
            [("🎸 Музыка", "a_listen")],
            [("🎫 Поиск по мероприятиям", "a_concerts_find")],
        ]))
    return ("Меню снизу 👇", None)