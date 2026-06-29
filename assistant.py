from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
import ai
import verify

_MED_WORDS = ("боль", "болит", "температур", "симптом", "врач", "таблет", "лекарств", "горло",
              "кашель", "тошнот", "давлен", "head", "сыпь", "простуд", "грипп", "живот")

# (ключевые слова, action)
_INTENT_MAP = [
    (("что сегодня", "что на сегодня", "план на день", "дела на день", "расписан", "планировать день", "мой день"),
     "day_plan"),
    (("завтрак", "обед", "ужин", "поесть", "приготовить", "рецепт",
      "покушать", "голоден", "голодна", "что поесть", "что покушать",
      "что приготовить", "чего поесть"), "meal_picker"),
    (("холодильник", "из холодильника", "что есть дома", "что есть в холодильнике", "остатки"),
     "fridge"),
    (("что посмотреть", "фильм", "сериал", "кино"), "movie"),
    (("что почитать", "почитать", "книгу", "книжку"), "book"),
    (("что послушать", "послушать", "музыку", "музыка", "плейлист"), "music"),
    (("куда поехать", "путешест", "поездка", "отпуск", "маршрут"), "travel"),
    (("концерт", "мероприят", "событи", "афиша", "выступлен"), "concerts"),
    (("мотивац", "прокрастин", "лень", "грустн", "грустно",
      "не могу начать", "застрял", "настроени"), "motivation"),
    (("нидерландск", "голландск", "dutch", "де/хет", "de/het"), "grammar_nl"),
    (("английск", "english", "phrasal"), "grammar_en"),
    (("словар", "лексик", "перевод", "какое слово", "слово дня"), "dictionary"),
    (("одеться", "что надеть", "образ дня", "образ на"), "outfit"),
    (("погода", "дождь", "температура", "зонт", "прогноз"), "weather"),
    (("тревог", "тревож", "беспокоюсь", "стресс", "переживаю", "нервничаю"), "worry"),
    (("заметк", "сохран", "запомни это", "мои заметки", "база"), "notes"),
]

_FALLBACK_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("☀️ Мой день",   callback_data="a_w_today"),
     InlineKeyboardButton("👕 Гардероб",   callback_data="m_wardrobe")],
    [InlineKeyboardButton("🍃 Самозабота", callback_data="m_balance"),
     InlineKeyboardButton("📚 Обучение",   callback_data="m_learn")],
    [InlineKeyboardButton("🍿 Досуг",      callback_data="m_leisure"),
     InlineKeyboardButton("🥣 Готовка",    callback_data="m_food")],
])


def _detect_intent(text: str):
    t = text.lower()
    for keywords, action in _INTENT_MAP:
        if any(kw in t for kw in keywords):
            return action
    return None


async def _run_intent(bot, cid, action):
    import balance, leisure, learning, wardrobe, myday, settings
    import weather as wx
    if action == "meal_picker":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌅 Завтрак", callback_data="a_recipe_breakfast"),
            InlineKeyboardButton("☀️ Обед",    callback_data="a_recipe_lunch"),
            InlineKeyboardButton("🌙 Ужин",    callback_data="a_recipe_dinner"),
        ]])
        await bot.send_message(chat_id=cid, text="🍽 <b>Что готовим?</b>",
                               parse_mode="HTML", reply_markup=kb)
    elif action == "day_plan":
        await myday.send_plany(bot, cid)
    elif action == "fridge":
        await balance.send_fridge_recipe(bot, cid)
    elif action == "movie":
        await leisure.send_recos(bot, cid, "movie")
    elif action == "book":
        await leisure.send_recos(bot, cid, "book")
    elif action == "music":
        await leisure.send_listen(bot, cid)
    elif action == "travel":
        await leisure.send_go(bot, cid)
    elif action == "concerts":
        await leisure.find_concerts(bot, cid, "home")
    elif action == "motivation":
        await balance.send_motiv_push(bot, cid)
    elif action == "grammar_nl":
        await learning.gm_send_lang(bot, cid, "nl")
    elif action == "grammar_en":
        await learning.gm_send_lang(bot, cid, "en")
    elif action == "dictionary":
        await learning.send_dict(bot, cid)
    elif action == "outfit":
        await wardrobe.send_looks(bot, cid)
    elif action == "weather":
        await wx.send_weather(bot, cid, "today")
    elif action == "worry":
        await balance.send_daycheck(bot, cid)
    elif action == "notes":
        await settings.send_notes(bot, cid)


async def chat_reply(bot, cid, text):
    store.last_action[str(cid)] = None
    store.last_source[str(cid)] = "Ассистент"

    # Медицинские слова — подсказка, но не прерываем роутинг
    if any(w in text.lower() for w in _MED_WORDS):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👩🏻‍⚕️ Вопрос врачу", callback_data="as_doctor")]])
        await bot.send_message(
            chat_id=cid,
            text="👩🏻‍⚕️ <b>Похоже на вопрос о здоровье</b>\n\n«Вопрос врачу» даст структурированный разбор.",
            parse_mode="HTML", reply_markup=kb)
        return

    await bot.send_chat_action(chat_id=cid, action="typing")

    # Intent-роутинг — сразу запускаем нужную функцию
    intent = _detect_intent(text)
    if intent:
        await _run_intent(bot, cid, intent)
        store.last_surface[str(cid)] = "chat"
        return

    # Фолбэк — LLM-ответ + кнопки главного меню
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = await ai.achat_chain(hist, cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    store.last_answer[str(cid)] = answer
    store.last_surface[str(cid)] = "chat"
    await verify.safe_send(bot, cid, (answer or "").strip() or "Пусто, попробуй ещё раз.",
                           surface="chat", reply_markup=_FALLBACK_KB)
