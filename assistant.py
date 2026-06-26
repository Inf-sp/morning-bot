from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
import ai
import verify

# ---------- свободный чат ----------
_MED_WORDS = ("боль", "болит", "температур", "симптом", "врач", "таблет", "лекарств", "горло",
              "кашель", "тошнот", "давлен", "head", "сыпь", "простуд", "грипп", "живот")

_CATEGORY_TRIGGERS = [
    ("👕 Гардероб", "m_wardrobe", (
        "одежда", "надеть", "лук", "гардероб", "образ", "стиль",
        "куртка", "рубашка", "джинсы", "обувь", "пиджак", "свитер",
    )),
    ("👨‍🍳 Кулинарный радар", "m_food", (
        "рецепт", "приготовить", "ужин", "обед", "завтрак",
        "поесть", "холодильник", "блюдо", "готовить", "поужинать",
    )),
    ("🎯 Мотивация", "as_motiv", (
        "мотивац", "прокрастин", "не могу начать", "застрял",
        "лень", "не хочу делать", "не могу заставить",
    )),
    ("😌 Дневник тревоги", "as_daycheck", (
        "тревог", "тревож", "беспокоит", "не могу успокоиться",
        "крутится в голове", "переживаю", "нервничаю",
    )),
    ("📚 Обучение", "m_learn", (
        "нидерландск", "тренажёр", "пословица",
        "язык учить", "повторить слова", "учить слова", "учёба",
    )),
    ("✈️ Путешествия", "a_trav_go", (
        "путешест", "поездка", "куда поехать", "тур ",
        "перелёт", "виза", "маршрут", "страну посетить",
    )),
    ("🍿 Досуг", "m_leisure", (
        "фильм", "сериал", "книга", "музыка", "концерт",
        "послушать", "посмотреть", "почитать",
    )),
    ("☀️ Мой день", "a_w_today", (
        "погод", "дождь", "зонт", "похолодан", "потеплеет", "прогноз",
    )),
]


def _detect_categories(text: str) -> list[tuple[str, str]]:
    t = text.lower()
    return [
        (label, cb)
        for label, cb, keywords in _CATEGORY_TRIGGERS
        if any(kw in t for kw in keywords)
    ]


async def chat_reply(bot, cid, text):
    store.last_action[str(cid)] = None
    store.last_source[str(cid)] = "Ассистент"
    await bot.send_chat_action(chat_id=cid, action="typing")
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = await ai.achat_chain(hist)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await verify.safe_send(bot, cid, (answer or "").strip() or "Пусто, попробуй ещё раз.", surface="chat")
    store.last_answer[str(cid)] = answer
    store.last_surface[str(cid)] = "chat"
    if any(w in text.lower() for w in _MED_WORDS):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👩🏻‍⚕️ Вопрос врачу", callback_data="as_doctor")]])
        await bot.send_message(chat_id=cid,
            text="👩🏻‍⚕️ Похоже на вопрос о здоровье. В разделе 🧠 Баланс → «Вопрос врачу» дам подробный структурированный разбор.",
            reply_markup=kb)
    cats = _detect_categories(text)
    if cats:
        rows = [[InlineKeyboardButton(label, callback_data=cb)] for label, cb in cats]
        await bot.send_message(
            chat_id=cid,
            text="💡 Похоже, это связано с разделами бота:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
