from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ReplyKeyboardMarkup


def _u16_len(text):
    return len((text or "").encode("utf-16-le")) // 2


def _build_welcome():
    chunks = []
    entities = []

    def add(text, entity_type=None):
        offset = _u16_len("".join(chunks))
        chunks.append(text)
        if entity_type:
            entities.append(MessageEntity(entity_type, offset, _u16_len(text)))

    add("👋 Привет! Я DM — твой помощник на каждый день.", MessageEntity.BOLD)
    add("\n\n")
    add("Помогаю с погодой, одеждой, языками, рецептами, досугом и полезными привычками.")
    add("\n\n")
    add("Разделы", MessageEntity.BOLD)
    add("\n")
    add("☀️ Мой день — погода, сводка и советы.\n")
    add("👕 Гардероб — что надеть и покупки.\n")
    add("🚑 Здоровье — мотивация, тревоги и здоровье.\n")
    add("📚 Обучение — языки, игра и практика.\n")
    add("🍿 Досуг — фильмы, книги, музыка и поездки.\n")
    add("🥣 Готовка — рецепты и идеи из продуктов.\n")
    add("\n")
    add("Просто напиши вопрос в чат и я помогу 💬")
    add("\n\n")
    add("Изменить параметры или посмотреть сохранённую информацию можно в 🎚️ ")
    add("Настройках", MessageEntity.BOLD)
    add(".")
    return "".join(chunks), entities


WELCOME, WELCOME_ENTITIES = _build_welcome()

def main_kb(cid=None):
    rows = [
        ["☀️ Мой день"],
        ["👕 Гардероб", "🚑 Здоровье"],
        ["📚 Обучение", "🍿 Досуг"],
        ["🥣 Готовка", "🎚️ Настройки"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)

# Нижнее меню: Мой день широкий первый + категории в два столбца
MAIN_LABELS = [
    "☀️ Мой день",
    "👕 Гардероб",
    "🚑 Здоровье",
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
    "\n\nИзменить параметры или посмотреть сохранённую информацию можно в 🎚️ <b>Настройках</b>."
)

def _ikb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def _back(parent="m_close"):
    return [("◀️ Назад", parent)]

def menu_screen(key):
    if key == "m_wardrobe":
        return (
            "👕 <b>Гардероб</b>\n\nОдежда без хаоса. Подберу образ, помогу разобрать шкаф и выбрать, что стоит докупить. Чем полнее гардероб, тем точнее рекомендации."
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
                [("👩🏻‍⚕️ Спросить врача", "as_doctor")],
                [("⚡️ Заряд мотивации", "as_motiv")], 
                [ ("📓 Дневник тревог", "as_daycheck")],
                [("🎚️ Настройки здоровья", "set_lagom")],

            ])
        )
    if key == "m_learn":
        return (
            "📚 <b>Обучение</b>\n\n"
            "Выбери язык — и вперёд!"
            + _MENU_FOOTER,
            _ikb([
                [("🇳🇱 Нидерландский язык", "m_nl")], 
                [("🇬🇧 Английский язык", "m_en")],
                [("🎚️ Настройки обучения", "m_dict_settings")],
            ])
        )
    if key == "m_dict_settings":
        return (
            "🎚️ <b>Словари и языки</b>\n\n"
            "Управляй словарём и уровнем языка."
            + _MENU_FOOTER,
            _ikb([
                [("🎚️ Нидерландский словарь", "a_dictlang_nl")], 
                [("🎚️ Английский словарь", "a_dictlang_en")],
                [("🎚️ Уровень языка", "a_levels")],
                [("◀️ Назад", "m_learn")],
            ])
        )
    if key == "m_nl":
        return (
            "🇳🇱 <b>Нидерландский</b>\n\n"
            "Практика языка: слова, живые выражения и игры."
            + _MENU_FOOTER,
            _ikb([
                [("🧠 Тренажёр", "a_train_nl")], 
                [("💭 Живой язык", "a_proverb_nl")], 
                [("🕵️ Игра-детектив", "gamelang_nl")],
                [("◀️ Назад", "m_learn")],
            ])
        )
    if key == "m_en":
        return (
            "🇬🇧 <b>Английский</b>\n\n"
            "Практика языка: слова, живые выражения и игры."
            + _MENU_FOOTER,
            _ikb([
                [("🧠 Тренажёр", "a_train_en")], 
                [("💭 Живой язык", "a_proverb_en")], 
                [("🕵️ Игра-детектив", "gamelang_en")],
                [("◀️ Назад", "m_learn")],
            ])
        )
    if key == "m_leisure":
        return (
            "🍿 <b>Досуг</b>\n\n"
            "Фильмы, музыка, книги и путешествия — под твой вкус."
            + _MENU_FOOTER,
            _ikb([
                [("✈️ Путешествия", "a_trav_go")], 
                [("🎬 Кино", "a_watch")],
                [("📖 Книги", "a_read")], 
                [("🎸 Музыка", "a_listen")], 
                [("🎫 Концерты", "a_concerts_find")],
                [("🎚️ Настройки досуга", "m_leisure_settings")],
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
    return ("Выбери раздел в нижнем меню.", None)


async def send_food_menu(bot, cid):
    kb = _ikb([
        [("🍳 Завтрак", "a_recipe_breakfast"), ("🥗 Обед", "a_recipe_lunch"), ("🍽️ Ужин", "a_recipe_dinner")],
        [("🥕 Из того что есть", "as_fridge_cook")],
        [("🎚️ Настройки холодильника", "set_fridge_g")],
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
