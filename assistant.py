import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
import ai
import util
import verify
from ui import assistant as assistant_ui

_MED_WORDS = ("боль", "болит", "симптом", "врач", "горло", "кашель", "тошнот", "давлен",
              "сыпь", "простуд", "грипп", "живот", "голова", "мигрень", "насморк")
_MEDICINE_WORDS = ("лекарств", "таблет", "препарат", "доз", "мг ", " мг", "капл", "сироп",
                   "мазь", "антибиотик", "парацетамол", "ибупрофен", "риталин", "concerta")
_BODY_TEMP_HINTS = ("у меня температур", "температура тела", "высокая температура",
                    "температура 37", "температура 38", "температура 39", "температура 40")
_WEATHER_HINTS = ("погода", "на улице", "прогноз", "зонт", "ветер", "дождь")

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
    (("нидерландск", "голландск", "dutch", "английск", "english", "phrasal", "де/хет", "de/het"), "learn"),
    (("словар", "лексик", "перевод", "какое слово", "слово дня"), "dictionary"),
    (("одеться", "что надеть", "образ дня", "образ на"), "outfit"),
    (("погода", "дождь", "температура", "зонт", "прогноз"), "weather"),
    (("тревог", "тревож", "беспокоюсь", "стресс", "переживаю", "нервничаю"), "worry"),
    (("заметк", "сохран", "запомни это", "мои заметки", "база"), "notes"),
]

_LOVE_ADD_VERB_RE = re.compile(r"\b(добавь|добавить|занеси|запиши|сохрани|сохранить|закинь)\b", re.I)
_LOVE_WORD_RE = re.compile(r"\bв\s+(?:мои\s+|мой\s+)?любим(?:ые|ое|ых|ый|ую)\b", re.I)

# (regex категории, config-ключ хранилища, человекочитаемая папка для подтверждения)
_LOVE_CATEGORIES = [
    (re.compile(r"\b(фильм|сериал|кино)\b", re.I), "movies", "Кино"),
    (re.compile(r"\b(книг[ауи]?|книжк[ауи]?)\b", re.I), "books", "Мои книги"),
    (re.compile(r"\b(музыкант[а-я]*|исполнител[а-я]*|артист[а-я]*|груп[а-я]*)\b", re.I), "artists", "Мои музыканты"),
    (re.compile(r"\b(стран[ауы]?)\b", re.I), "countries", "Мои страны"),
]

_LOVE_CATEGORY_KEY_RE = re.compile(
    r"\b(?:фильм[а-я]*|сериал[а-я]*|кино|книг[а-я]*|книжк[а-я]*|"
    r"музыкант[а-я]*|исполнител[а-я]*|артист[а-я]*|груп[а-я]*|стран[а-я]*)\b",
    re.I,
)


def _detect_love_add(text: str):
    """«Добавь в любимые фильм X» -> (store_key, folder_label, title) | None.

    Триггер строго требует и глагол добавления, и слово «любим*» — иначе
    «люблю фильмы про космос» не должно случайно матчиться."""
    text = text or ""
    if not _LOVE_ADD_VERB_RE.search(text) or not _LOVE_WORD_RE.search(text):
        return None
    category = next(
        ((key, label) for pattern, key, label in _LOVE_CATEGORIES if pattern.search(text)),
        None,
    )
    if not category:
        return None
    store_key, folder_label = category
    payload = _LOVE_ADD_VERB_RE.sub(" ", text, count=1)
    payload = _LOVE_WORD_RE.sub(" ", payload, count=1)
    payload = _LOVE_CATEGORY_KEY_RE.sub(" ", payload, count=1)
    payload = re.sub(r"\s+", " ", payload).strip(" \t\n\r:;,.-–—")
    if not payload:
        return None
    return store_key, folder_label, payload


async def try_add_love_from_chat(bot, cid, text):
    """Перехватывает «добавь в любимые фильм/книгу/музыканта/страну X» из чата."""
    import config
    import store as _store
    detected = _detect_love_add(text)
    if not detected:
        return False
    store_key, folder_label, title = detected
    key_map = {
        "movies": config.WATCHLIST_KEY,
        "books": config.BOOKS_KEY,
        "artists": config.ARTISTS_KEY,
        "countries": config.FAVCOUNTRIES_KEY,
    }
    existing = {str(x).strip().lower() for x in _store.get_list(key_map[store_key], cid)}
    if title.strip().lower() in existing:
        await bot.send_message(chat_id=cid, text=f"❤️ «{title}» уже в любимых ({folder_label}).")
        return True
    _store.add_to_list(key_map[store_key], cid, title)
    await bot.send_message(chat_id=cid, text=f"❤️ «{title}» — добавил в любимые ({folder_label}).")
    return True


def _detect_intent(text: str):
    t = text.lower()
    for keywords, action in _INTENT_MAP:
        if any(kw in t for kw in keywords):
            return action
    return None


def _looks_medical(text: str) -> bool:
    t = text.lower()
    if any(kw in t for kw in _MEDICINE_WORDS):
        return True
    if any(kw in t for kw in _BODY_TEMP_HINTS):
        return True
    if "температур" in t and any(kw in t for kw in _WEATHER_HINTS):
        return False
    return any(kw in t for kw in _MED_WORDS)


async def _run_intent(bot, cid, action):
    import balance, leisure, learning, wardrobe, myday, settings, travel
    import weather as wx
    # Ответы ассистента на свободный текст не должны нести кнопку «⬅️ Назад» -
    # пользователь не открывал раздел через меню, и вести её было бы некуда.
    no_kb_bot = settings._NoKbBot(bot)
    if action == "meal_picker":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🥐 Завтрак", callback_data="a_recipe_breakfast"),
            InlineKeyboardButton("🥗 Обед",    callback_data="a_recipe_lunch"),
            InlineKeyboardButton("🍲 Ужин",    callback_data="a_recipe_dinner"),
        ]])
        await bot.send_message(chat_id=cid, text="🍽 <b>Что готовим?</b>",
                               parse_mode="HTML", reply_markup=kb)
    elif action == "day_plan":
        await myday.send_plany(no_kb_bot, cid)
    elif action == "fridge":
        await balance.send_fridge_recipe(no_kb_bot, cid)
    elif action == "movie":
        await leisure.send_recos(no_kb_bot, cid, "movie")
    elif action == "book":
        await leisure.send_recos(no_kb_bot, cid, "book")
    elif action == "music":
        await leisure.send_listen(no_kb_bot, cid)
    elif action == "travel":
        await travel.send_go(no_kb_bot, cid)
    elif action == "concerts":
        await leisure.find_concerts(no_kb_bot, cid, "home")
    elif action == "motivation":
        await balance.send_motiv_push(no_kb_bot, cid)
    elif action == "learn":
        text, entities, kb = __import__("menu").menu_screen("m_learn", cid)
        await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb)
    elif action == "dictionary":
        await learning.send_dict(no_kb_bot, cid)
    elif action == "outfit":
        await wardrobe.send_looks(no_kb_bot, cid)
    elif action == "weather":
        await wx.send_weather(no_kb_bot, cid, "today")
    elif action == "worry":
        await balance.send_daycheck(no_kb_bot, cid)
    elif action == "notes":
        await settings.send_notes(no_kb_bot, cid)


async def chat_reply(bot, cid, text):
    store.last_action[str(cid)] = None
    store.last_source[str(cid)] = "Ассистент"

    # Явные вопросы о здоровье сразу идут в медицинский сценарий.
    if _looks_medical(text):
        import balance
        await balance.doctor_answer(bot, cid, text)
        return

    await bot.send_chat_action(chat_id=cid, action="typing")

    # Intent-роутинг — сразу запускаем нужную функцию
    intent = _detect_intent(text)
    if intent:
        await _run_intent(bot, cid, intent)
        store.last_surface[str(cid)] = "chat"
        return

    # Фолбэк - LLM-ответ без прикрепленного главного меню
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    status = await util.StatusManager.start(bot, cid)
    try:
        answer = await ai.achat_chain(hist, cid)
    except Exception as e:
        await status.stop(delete=True)
        await verify.safe_error(bot, cid, e); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    store.last_answer[str(cid)] = answer
    store.last_surface[str(cid)] = "chat"
    msg = assistant_ui.assistant_answer((answer or "").strip() or "Пусто, попробуй ещё раз.")
    ok = await status.replace(msg.text, entities=msg.entities)
    if not ok:
        try:
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
        except Exception:
            await verify.safe_send(bot, cid, msg.text, surface="chat")
