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

# Короткие реплики-реакции: разбираются правилами, без похода в основной AI-промпт
# (см. classify_short_reply). Ключи — точное совпадение реплики целиком (после
# lower/strip), не подстрока — иначе "спасибо, что помог с холодильником" тоже
# попал бы сюда и потерял содержательный запрос.
_ACKNOWLEDGEMENT_WORDS = {"ок", "окей", "ok", "okay", "хорошо", "понял", "поняла", "ясно",
                          "ладно", "договорились", "принято", "принял"}
_THANKS_WORDS = {"спасибо", "спс", "благодарю", "спасибо большое", "thanks", "thank you"}
_POSITIVE_WORDS = {"отлично", "супер", "класс", "идеально", "круто", "здорово", "прекрасно"}
_CONFIRM_WORDS = {"да", "ага", "угу", "верно", "точно", "именно", "конечно", "yes", "yep"}
_REJECT_WORDS = {"нет", "не", "не надо", "не хочу", "отмена", "неа", "no"}

ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
THANKS = "THANKS"
POSITIVE_REACTION = "POSITIVE_REACTION"
CONFIRMATION = "CONFIRMATION"
REJECTION = "REJECTION"

_ACK_REPLIES = ("Хорошо.", "Понял.", "Принято.")
_THANKS_REPLIES = ("Пожалуйста.", "Рад помочь.")
_POSITIVE_REPLIES = ("Отлично.", "Супер.")


def classify_short_reply(text: str):
    """Короткая реплика-реакция (ок/спасибо/да/нет и т.п.) -> тип, иначе None.

    None означает «не короткая реакция» — вызывающий код должен продолжить
    обычным путём (intent-роутинг, затем основной AI-промпт). Работает по
    точному совпадению всей реплики, поэтому длинные содержательные сообщения
    ("печень", "Амстердам", "манник", "устал") сюда не попадают."""
    t = (text or "").strip().lower().strip(".!?…")
    if not t or len(t.split()) > 3:
        return None
    if t in _ACKNOWLEDGEMENT_WORDS:
        return ACKNOWLEDGEMENT
    if t in _THANKS_WORDS:
        return THANKS
    if t in _POSITIVE_WORDS:
        return POSITIVE_REACTION
    if t in _CONFIRM_WORDS:
        return CONFIRMATION
    if t in _REJECT_WORDS:
        return REJECTION
    return None


def _short_reply_answer(kind: str) -> str:
    import random
    if kind == ACKNOWLEDGEMENT:
        return random.choice(_ACK_REPLIES)
    if kind == THANKS:
        return random.choice(_THANKS_REPLIES)
    if kind == POSITIVE_REACTION:
        return random.choice(_POSITIVE_REPLIES)
    if kind == REJECTION:
        return "Хорошо, не буду."
    return "Понял."  # CONFIRMATION и фолбэк


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

_RECIPE_INGREDIENT_RE = re.compile(
    r"\bприготовить\s+(?:из|с)\s+([^?!.…]+)", re.IGNORECASE,
)
_GENERIC_RECIPE_INGREDIENTS = {"этого", "того", "них", "холодильника", "продуктов"}

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
    if store_key == "countries":
        import travel
        await travel.add_visited_country(bot, cid, title)
        return True
    key_map = {
        "movies": config.WATCHLIST_KEY,
        "books": config.BOOKS_KEY,
        "artists": config.ARTISTS_KEY,
        "countries": config.FAVCOUNTRIES_KEY,
    }
    existing = {
        (x.get("value", "") if isinstance(x, dict) else str(x)).strip().lower()
        for x in _store.get_list(key_map[store_key], cid)
    }
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


def _recipe_ingredients_from_chat(text: str):
    """Return an explicitly named ingredient for a direct recipe card."""
    matched = _RECIPE_INGREDIENT_RE.search(text or "")
    if not matched:
        return None
    ingredients = re.sub(r"\s+", " ", matched.group(1)).strip(" ,;:—-.")
    if not ingredients or ingredients.casefold() in _GENERIC_RECIPE_INGREDIENTS:
        return None
    return ingredients[:160]


def _looks_medical(text: str) -> bool:
    t = text.lower()
    if any(kw in t for kw in _MEDICINE_WORDS):
        return True
    if any(kw in t for kw in _BODY_TEMP_HINTS):
        return True
    if "температур" in t and any(kw in t for kw in _WEATHER_HINTS):
        return False
    return any(kw in t for kw in _MED_WORDS)


async def _run_intent(bot, cid, action, recipe_ingredients=None):
    import balance, cooking, leisure_movies, wardrobe, myday, settings, travel
    import fridge
    import leisure_concerts
    import leisure_music
    import learning_dictionary as dictionary
    import saved_items
    import weather as wx
    # Ответы ассистента на свободный текст не должны нести кнопку «⬅️ Назад» -
    # пользователь не открывал раздел через меню, и вести её было бы некуда.
    no_kb_bot = settings._NoKbBot(bot)
    if action == "meal_recipe":
        await cooking.send_recipe(bot, cid, f"блюдо из {recipe_ingredients}")
    elif action == "meal_picker":
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🥐 Завтрак", callback_data="a_recipe_breakfast"),
            InlineKeyboardButton("🥗 Обед",    callback_data="a_recipe_lunch"),
            InlineKeyboardButton("🍲 Ужин",    callback_data="a_recipe_dinner"),
        ]])
        await bot.send_message(chat_id=cid, text="🍽 <b>Что готовим?</b>",
                               parse_mode="HTML", reply_markup=kb)
    elif action == "day_plan":
        await myday.send_plany(no_kb_bot, cid, force=True)
    elif action == "fridge":
        await fridge.send_fridge_recipe(no_kb_bot, cid)
    elif action == "movie":
        await leisure_movies.send_recos(no_kb_bot, cid, "movie")
    elif action == "book":
        await leisure_movies.send_recos(no_kb_bot, cid, "book")
    elif action == "music":
        await leisure_music.send_listen(no_kb_bot, cid)
    elif action == "travel":
        await travel.send_go(no_kb_bot, cid)
    elif action == "concerts":
        await leisure_concerts.find_concerts(no_kb_bot, cid, "home")
    elif action == "motivation":
        await balance.send_motiv_push(no_kb_bot, cid)
    elif action == "learn":
        text, entities, kb = __import__("menu").menu_screen("m_learn", cid)
        await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb)
    elif action == "dictionary":
        await dictionary.send_dict(no_kb_bot, cid)
    elif action == "outfit":
        await wardrobe.send_looks(no_kb_bot, cid)
    elif action == "weather":
        await wx.send_weather(no_kb_bot, cid, "today")
    elif action == "worry":
        await balance.send_daycheck(no_kb_bot, cid)
    elif action == "notes":
        await saved_items.send_notes(no_kb_bot, cid)


async def chat_reply(bot, cid, text):
    store.last_action[str(cid)] = None
    store.last_source[str(cid)] = "Ассистент"

    # Короткие реплики-реакции (ок/спасибо/да/нет) — разбираются правилами,
    # без похода в AI: там нет содержательного запроса, только реакция на
    # предыдущий ответ бота.
    short_kind = classify_short_reply(text)
    if short_kind:
        reply = _short_reply_answer(short_kind)
        await bot.send_message(chat_id=cid, text=reply)
        hist = store.chat_history.get(str(cid), [])
        hist.append({"role": "user", "content": text})
        hist.append({"role": "assistant", "content": reply})
        store.chat_history[str(cid)] = hist[-10:]
        store.last_surface[str(cid)] = "chat"
        return

    # Явные вопросы о здоровье сразу идут в медицинский сценарий.
    if _looks_medical(text):
        import doctor
        await doctor.answer(bot, cid, text)
        return

    await bot.send_chat_action(chat_id=cid, action="typing")

    # Intent-роутинг — сразу запускаем нужную функцию
    recipe_ingredients = _recipe_ingredients_from_chat(text)
    intent = "meal_recipe" if recipe_ingredients else _detect_intent(text)
    if intent:
        await _run_intent(bot, cid, intent, recipe_ingredients)
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
