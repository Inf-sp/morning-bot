from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

# Нижнее меню всегда видно - только основные категории
MAIN_LABELS = ["💬 Ассистент", "📋 Мой день", "👕 Гардероб", "📚 Обучение",
               "🌤 Погода", "✈️ Путешествия", "🍿 Досуг"]

MAIN_KB = ReplyKeyboardMarkup([
    ["💬 Ассистент"],
    ["📋 Мой день", "👕 Гардероб"],
    ["📚 Обучение", "🌤 Погода"],
    ["✈️ Путешествия", "🍿 Досуг"],
], resize_keyboard=True)

# Reply-ярлык -> ключ инлайн-подменю (или действие)
LABEL_TO_KEY = {
    "💬 Ассистент": "assist",
    "📚 Обучение": "m_lang",
    "🌤 Погода": "m_weather",
    "✈️ Путешествия": "m_travel",
    "🍿 Досуг": "m_content",
}

def _ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

# Низ каждого инлайн-подменю
def _back(parent="m_close"):
    return [("⬅️ Назад", parent)]

def menu_screen(key):
    if key == "m_lang":
        return ("📚 Обучение + игра", _ikb([
            [("🇳🇱 Нидерландский", "m_nl"), ("🇬🇧 Английский", "m_en")],
            [("🕵️ Игра-детектив", "a_game")],
            [("⚙️ Уровень языка", "a_levels")],
            _back(),
        ]))
    if key == "m_nl":
        return ("🇳🇱 Нидерландский", _ikb([
            [("📖 Грамматика", "a_gram_nl"), ("⚡ Тренировка", "a_tr_nl")],
            [("⬅️ Назад", "m_lang")],
        ]))
    if key == "m_en":
        return ("🇬🇧 Английский", _ikb([
            [("📖 Грамматика", "a_gram_en"), ("⚡ Тренировка", "a_tr_en")],
            [("⬅️ Назад", "m_lang")],
        ]))
    if key == "m_weather":
        return ("🌤 Погода", _ikb([
            [("🌤 Сегодня", "a_w_today")],
            [("📅 На 3 дня", "a_w_3")],
            [("📍 Сменить город", "a_setcity")],
            _back(),
        ]))
    if key == "m_travel":
        return ("✈️ Путешествия", _ikb([
            [("🗺 Куда поехать", "a_trav_go"), ("🏳 Мои страны", "a_trav_my")],
            _back(),
        ]))
    if key == "m_content":
        return ("🍿 Досуг", _ikb([
            [("🎬 Что посмотреть", "a_watch"), ("📖 Что почитать", "a_read")],
            [("🍿 Список просмотра", "a_watchlist"), ("📚 Список чтения", "a_readlist")],
            [("❤️ Любимое", "a_fav"), ("🎤 Концерты", "m_concerts")],
            _back(),
        ]))
    if key == "m_concerts":
        return ("🎤 Концерты", _ikb([
            [("🎤 Мои артисты", "a_artists"), ("➕ Добавить", "a_artadd")],
            [("⬅️ Назад", "m_content")],
        ]))
    return ("Меню снизу 👇", None)