from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

WELCOME = (
    "👋 <b>Привет! Я DM</b> - твой помощник на каждый день. "
    "Погода, учеба, идеи и планы в одном месте.\n\n"
    "<b>Что я умею:</b>\n"
    "☀️ <b>Мой день</b> - погода, образ, слово дня, идея и факты.\n"
    "👕 <b>Гардероб</b> - одежда по погоде, разбор шкафа и оценка покупок.\n"
    "🍃 <b>Баланс</b> - советы врача, мотивация и рецепты.\n"
    "📚 <b>Обучение</b> - языки (NL/EN), игры, словарь и тесты.\n"
    "🍿 <b>Досуг</b> - фильмы, книги, музыка, концерты и поездки.\n\n"
    "Просто напиши свой вопрос в чат - я на него отвечу 💬\n\n"
    "<b>Команды:</b>\n"
    "/start - главное меню\n"
    "/setup - настройки (язык, город, уведомления)\n\n"
    "<b>Как сохранять:</b>\n"
    "Жми «⏳ <b>Позже</b>» под ответами, чтобы не потерять их. "
    "Топовые треки и фильмы кидай в «❤️ <b>В любимые</b>». "
    "Всё сохраненное лежит тут: /notes."
)

# Нижнее меню: Мой день широкий первый + категории в два столбца
MAIN_LABELS = ["☀️ Мой день", "👕 Гардероб", "🍃 Самозабота", "📚 Обучение", "🍿 Досуг"]

MAIN_KB = ReplyKeyboardMarkup([
    ["☀️ Мой день"],
    ["👕 Гардероб", "🍃 Самозабота"],
    ["📚 Обучение", "🍿 Досуг"],
], resize_keyboard=True)

# Reply-ярлык -> ключ инлайн-подменю
LABEL_TO_KEY = {
    "👕 Гардероб": "m_wardrobe",
    "🍃 Самозабота": "m_balance",
    "📚 Обучение": "m_learn",
    "🍿 Досуг": "m_leisure",
}

_MENU_FOOTER = (
    "\n\n<b>Команды:</b>\n"
    "/setup — настройки\n"
    "/notes — временные закладки\n\n"
    "Сохраняй полезное через ⏳ <b>Позже</b> или ❤️ <b>В любимые</b>.\n\n"
    "<b>Выбери 👇</b>"
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
                [("✨ Сгенерировать образ", "w_look")],
                [("💡 Улучшить гардероб", "w_improve")],
                [("🛒 Проверка покупки", "w_check")],
            ])
        )
    if key == "m_balance":
        return (
            "🍃 <b>Самозабота</b>\n\n"
            "Здоровье, эмоции и питание в одном месте. Разберу симптом, поддержу, помогу разгрузить голову и подскажу, что приготовить."
            + _MENU_FOOTER,
            _ikb([
                [("👨‍🍳 Кулинарный радар", "m_food")],
                [("👩🏻‍⚕️ Вопрос врачу", "as_doctor")],
                [("☕️ Личная мотивация", "as_motiv")],
                [("📓 Дневник тревоги", "as_daycheck")],
            ])
        )
    if key == "m_food_gen":
        return (
            "👨‍🍳 <b>Сгенерировать рецепт</b>\n\nВыбери приём пищи 👇",
            _ikb([
                [("🍳 Завтрак", "a_food_breakfast")],
                [("🥗 Обед", "a_food_lunch")],
                [("🍽️ Ужин", "a_food_dinner")],
                [("◀️ Назад", "m_food")],
            ])
        )
    if key in ("m_learn", "m_nl", "m_en"):
        return (
            "📚 <b>Обучение</b>\n\n"
            "Тренируй слова и обучайся — играя!\n\n"
            "<b>Доступны языки:</b>\n🇬🇧 Английский\n🇳🇱 Нидерландский"
            + _MENU_FOOTER,
            _ikb([
                [("🧠 Тренажёр", "a_train")],
                [("📘 Грамматика", "gm_home")],
                [("🧩 Артикли De / Het", "dh_start")],
                [("💭 Живой язык", "a_proverb")],
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
                [("🎬 Кино", "a_watch")],
                [("📖 Книги", "a_read")],
                [("🎸 Музыка", "a_listen")],
                [("🎫 Поиск по мероприятиям", "a_concerts_find")],
            ])
        )
    return ("Меню снизу 👇", None)


async def send_food_menu(bot, cid):
    import asyncio
    import balance
    tip = await asyncio.to_thread(balance.fetch_food_tip, cid)
    header = "👨‍🍳 <b>Кулинарный радар</b>"
    body = f"\n\n{tip}" if tip else ""
    kb = _ikb([
        [("🍳 Завтрак", "a_food_breakfast"), ("🥗 Обед", "a_food_lunch"), ("🍽️ Ужин", "a_food_dinner")],
        [("🧊 Из холодильника", "as_fridge_cook")],
        [("◀️ Назад", "m_balance")],
    ])
    await bot.send_message(chat_id=cid, text=header + body,
                           parse_mode="HTML", reply_markup=kb)
