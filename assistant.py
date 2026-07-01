import re
from html import unescape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
import store
import ai
import verify

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

_FALLBACK_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("☀️ Мой день",   callback_data="a_w_today"),
     InlineKeyboardButton("👕 Гардероб",   callback_data="m_wardrobe")],
    [InlineKeyboardButton("🚑 Здоровье", callback_data="m_balance"),
     InlineKeyboardButton("📚 Обучение",   callback_data="m_learn")],
    [InlineKeyboardButton("🍿 Досуг",      callback_data="m_leisure"),
     InlineKeyboardButton("🥣 Готовка",    callback_data="m_food")],
])


_LEADING_EMOJI_RE = re.compile(
    r"^[\s\U0001F1E6-\U0001FAFF\u2600-\u27BF\uFE0F]+"
)


def _u16_len(text: str) -> int:
    return len((text or "").encode("utf-16-le")) // 2


def _clean_assistant_line(line: str) -> str:
    line = unescape(line or "").strip()
    line = re.sub(r"</?(?:b|strong|i|em|code)>", "", line, flags=re.I)
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = re.sub(r"__(.*?)__", r"\1", line)
    return line.strip()


def _strip_title_emoji(line: str) -> str:
    return _LEADING_EMOJI_RE.sub("", line or "").strip()


def _assistant_entities_card(answer: str):
    raw_lines = [_clean_assistant_line(line) for line in (answer or "").splitlines()]
    lines = [line for line in raw_lines if line]
    if not lines:
        lines = ["Пусто", "Попробуй ещё раз."]

    title = _strip_title_emoji(lines[0]).rstrip(".:") or "Ответ"
    body = lines[1:]
    chunks = []
    entities = []

    def add(text: str, entity_type=None):
        offset = _u16_len("".join(chunks))
        chunks.append(text)
        if entity_type and text:
            entities.append(MessageEntity(entity_type, offset, _u16_len(text)))

    add(title, MessageEntity.BOLD)
    if body:
        add("\n\n")

    for idx, line in enumerate(body):
        normalized = line.strip()
        is_quote = normalized.startswith((">", "»"))
        if is_quote:
            normalized = normalized.lstrip(">» ").strip()

        if normalized.lower().startswith(("это значит", "значит:")):
            normalized = "Это значит:"

        entity_type = MessageEntity.BLOCKQUOTE if is_quote else None
        add(normalized, entity_type)
        if idx != len(body) - 1:
            add("\n" if normalized.startswith("- ") else "\n\n")

    # Если модель дала короткий ответ без явной цитаты, не выдумываем цитируемый блок.
    return "".join(chunks).rstrip(), entities


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
    elif action == "learn":
        text, kb = __import__("menu").menu_screen("m_learn")
        await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)
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

    # Фолбэк — LLM-ответ + кнопки главного меню
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    pending = await bot.send_message(chat_id=cid, text="⏳ <b>Генерация…</b>", parse_mode="HTML")
    try:
        answer = await ai.achat_chain(hist, cid)
    except Exception as e:
        try:
            await pending.delete()
        except Exception:
            pass
        await verify.safe_error(bot, cid, e); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    store.last_answer[str(cid)] = answer
    store.last_surface[str(cid)] = "chat"
    out_text, entities = _assistant_entities_card((answer or "").strip() or "Пусто, попробуй ещё раз.")
    ok = False
    try:
        await pending.edit_text(out_text, entities=entities, reply_markup=_FALLBACK_KB)
        ok = True
    except Exception:
        ok = False
    if not ok:
        try:
            await bot.send_message(chat_id=cid, text=out_text, entities=entities, reply_markup=_FALLBACK_KB)
        except Exception:
            await verify.safe_send(bot, cid, out_text, surface="chat", reply_markup=_FALLBACK_KB)
