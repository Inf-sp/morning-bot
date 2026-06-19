from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

# Нижнее меню: Ассистент во всю ширину + 6 категорий в два ряда по три
MAIN_LABELS = ["💬 Ассистент DM | Daily Manager", "☀️ Мой день", "👕 Гардероб", "📋 Здоровье",
               "📚 Обучение", "🍽️ Питание", "🍿 Досуг"]

MAIN_KB = ReplyKeyboardMarkup([
    ["☀️ Мой день", "👕 Гардероб"],
    ["📋 Здоровье", "📚 Обучение"],
    ["🍽️ Питание", "🍿 Досуг"],
    ["💬 Ассистент DM | Daily Manager"],
], resize_keyboard=True)

# Reply-ярлык -> ключ инлайн-подменю
LABEL_TO_KEY = {
    "📋 Здоровье": "m_health",
    "📚 Обучение": "m_learn",
    "🍽️ Питание": "m_food",
    "🍿 Досуг": "m_leisure",
}

def _ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def _back(parent="m_close"):
    return [("⬅️ Назад", parent)]

def day_weather_kb():
    # инлайн под сводкой «Мой день»
    return _ikb([
        [("📅 Погода на завтра", "a_w_tomorrow")],
        [("🗓 Погода на неделю", "a_w_week")],
        [("📍 Сменить город", "a_setcity")],
        _back(),
    ])

def menu_screen(key):
    if key == "m_health":
        return ("📋 Здоровье", _ikb([
            [("🩺 Вопрос врачу", "as_doctor")],
            [("⚡ Мотивация", "as_cheer")],
            [("🧠 СДВГ-фокус", "as_adhd")],
            [("😌 Дневник тревоги", "as_daycheck")],
            _back(),
        ]))
    if key == "m_learn":
        return ("📚 <b>Обучение</b>\n\nОбъясню любую тему простыми словами и без воды.\n\n"
                "Уровень языка меняется в настройках: /setup\n\n"
                "Напиши вопрос или выбери 👇", _ikb([
            [("🇳🇱 Нидерландский", "m_nl"), ("🇬🇧 Английский", "m_en")],
            [("🕵️ Игра-детектив", "a_game"), ("🗂️ Словарь", "a_dict")],
            [("✍️ Тексты", "as_letter"), ("💡 Идея", "as_idea")],
            _back(),
        ]))
    if key == "m_nl":
        return ("🇳🇱 Нидерландский", _ikb([
            [("📖 Грамматика", "a_gram_nl"), ("📝 Обратный перевод", "a_tr_nl")],
            [("🔤 Глагол дня", "a_verb_nl"), ("💬 Пословица", "a_proverb_nl")],
            [("🎯 Подготовка к экзамену", "a_exam")],
            [("⬅️ Назад", "m_learn")],
        ]))
    if key == "m_en":
        return ("🇬🇧 Английский", _ikb([
            [("📖 Грамматика", "a_gram_en"), ("📝 Обратный перевод", "a_tr_en")],
            [("🔤 Глагол дня", "a_verb_en"), ("💬 Пословица", "a_proverb_en")],
            [("⬅️ Назад", "m_learn")],
        ]))
    if key == "m_food":
        return ("🍽️ Питание", _ikb([
            [("👨‍🍳 Кулинарный радар", "as_food")],
            [("🥕 Не выбрасывать продукты", "as_food_left")],
            _back(),
        ]))
    if key == "m_leisure":
        return ("🍿 <b>Досуг</b>", _ikb([
            [("✈️ Путешествия", "m_travel")],
            [("🎬 Что посмотреть", "a_watch")],
            [("📖 Что почитать", "a_read")],
            [("🎵 Что послушать", "a_listen")],
            [("🎤 Концерты", "m_concerts")],
            _back(),
        ]))
    if key == "m_travel":
        return ("✈️ Путешествия", _ikb([
            [("🗺 Куда поехать", "a_trav_go")],
            [("🏳 Мои страны", "a_trav_my")],
            [("⬅️ Назад", "m_leisure")],
        ]))
    if key == "m_concerts":
        return ("🎤 Концерты", _ikb([
            [("🔎 Найти концерты", "a_concerts_find")],
            [("🎤 Мои артисты", "a_artists")],
            [("➕ Добавить артиста", "a_artadd")],
            [("⬅️ Назад", "m_leisure")],
        ]))
    return ("Меню снизу 👇", None)