import asyncio
import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
import ai
import util
import verify
import research
import rich_delivery
import menu
import myday
import secure
import assistant_context
from ui import assistant as assistant_ui
from ui.builder import MessageSpec


_log = logging.getLogger(__name__)

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

_THINK_OPEN_RE = re.compile(r"<think(?:\s[^>]*)?>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think\s*>", re.IGNORECASE)


def _visible_model_text(value, *, streaming=False):
    """Удаляет служебные рассуждения модели до попадания в Telegram или историю."""
    text = str(value or "")
    pieces = []
    position = 0
    while True:
        opening = _THINK_OPEN_RE.search(text, position)
        if opening is None:
            pieces.append(text[position:])
            break
        pieces.append(text[position:opening.start()])
        closing = _THINK_CLOSE_RE.search(text, opening.end())
        if closing is None:
            # Незавершённый reasoning-блок нельзя показывать даже в live preview.
            break
        position = closing.end()
    visible = "".join(pieces)
    if streaming:
        # Тег может прийти несколькими SSE-кусочками: «<th» + «ink>». Пока
        # он не собран, не даём его началу попасть в предварительный просмотр.
        last_open = visible.rfind("<")
        if last_open >= 0:
            tail = visible[last_open:].casefold()
            if any(marker.startswith(tail) for marker in ("<think", "</think")):
                visible = visible[:last_open]
    return visible


class _VisibleAssistantStream:
    """Накопитель потока, который выпускает только новые безопасные символы."""

    def __init__(self):
        self._source = ""
        self._visible = ""

    def add(self, delta):
        self._source += str(delta or "")
        visible = _visible_model_text(self._source, streaming=True)
        if not visible.startswith(self._visible):
            # Не возвращаем в черновик исходный текст при неожиданной разметке:
            # финальный ответ всё равно будет полностью очищен перед отправкой.
            return ""
        new_text = visible[len(self._visible):]
        self._visible = visible
        return new_text


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
    (("холодильник", "из холодильника", "что есть дома", "что есть в холодильнике", "остатк"),
     "fridge"),
    (("что посмотреть", "фильм", "сериал", "кино"), "movie"),
    (("что почитать", "почитать", "книгу", "книжку"), "book"),
    (("что послушать", "послушать", "музыку", "музыка", "плейлист"), "music"),
    (("концерт", "мероприят", "событи", "афиша", "выступлен"), "concerts"),
    (("нидерландск", "голландск", "dutch", "английск", "english", "phrasal", "де/хет", "de/het"), "learn"),
    (("словар", "лексик", "перевод", "какое слово", "слово дня"), "dictionary"),
    (("одеться", "что надеть", "образ дня", "образ на"), "outfit"),
    (("погода", "дождь", "температура", "зонт", "прогноз"), "weather"),
]

_RECIPE_INGREDIENT_RE = re.compile(
    r"\bприготовить\s+(?:из|с)\s+([^?!.…]+)", re.IGNORECASE,
)
_MEAL_WITH_INGREDIENT_RE = re.compile(
    r"\b(завтрак|обед|ужин)\s+(?:из|с)\s+([^?!.…]+)", re.IGNORECASE,
)
_GENERIC_RECIPE_INGREDIENTS = {"этого", "того", "них", "холодильника", "продуктов"}
# Слова общей просьбы «что приготовить?» — без них в тексте остаётся суть конкретного запроса.
_RECIPE_FILLER = {
    "есть", "рецепт", "рецепта", "рецепты", "рецептик", "дай", "подскажи", "посоветуй", "предложи", "скинь",
    "какой", "какие", "какой-нибудь", "что", "чего", "нибудь", "приготовить", "сготовить", "поесть", "покушать",
    "можно", "мне", "нам", "пожалуйста", "хочу", "хочется", "на", "в", "к", "и", "или", "а", "у", "тебя",
    "сегодня", "завтрак", "обед", "ужин", "завтрака", "обеда", "ужина", "быстро", "простой", "простое",
}

_LOVE_ADD_VERB_RE = re.compile(r"\b(добавь|добавить|занеси|запиши|сохрани|сохранить|закинь)\b", re.I)
_LOVE_WORD_RE = re.compile(r"\bв\s+(?:мои\s+|мой\s+)?любим(?:ые|ое|ых|ый|ую)\b", re.I)
# «Добавь сериал X» — глагол сразу перед категорией: слово «любимые» не обязательно.
_DIRECT_ADD_RE = re.compile(
    r"^\s*(?:добавь|добавить|занеси|запиши|сохрани|сохранить|закинь)\s+(?:мне\s+)?"
    r"(?:фильм|сериал|кино|книг|книжк|музык|музыкант|исполнител|артист|групп)",
    re.I,
)

# (regex категории, config-ключ хранилища, вкладка «Моей библиотеки» для подтверждения)
_LOVE_CATEGORIES = [
    (re.compile(r"\b(фильм[а-я]*|сериал[а-я]*|кино)\b", re.I), "movies", "Фильмы и сериалы"),
    (re.compile(r"\b(книг[а-я]*|книжк[а-я]*)\b", re.I), "books", "Книги"),
    (re.compile(r"\b(музык[а-я]*|музыкант[а-я]*|исполнител[а-я]*|артист[а-я]*|груп[а-я]*)\b", re.I),
     "artists", "Музыка"),
]

# Слово категории вместе с «фильмы или сериалы» убирается из названия целиком.
_MOVIE_WORD = r"(?:фильм[а-я]*|сериал[а-я]*|кино)"
_LOVE_CATEGORY_KEY_RE = re.compile(
    rf"\b(?:{_MOVIE_WORD}(?:\s+(?:или|и)\s+{_MOVIE_WORD})?|книг[а-я]*|книжк[а-я]*|"
    r"музык[а-я]*|музыкант[а-я]*|исполнител[а-я]*|артист[а-я]*|груп[а-я]*)\b",
    re.I,
)

def _detect_love_add(text: str):
    """«Добавь в любимые фильм X» -> (store_key, folder_label, title) | None.

    Триггер строго требует и глагол добавления, и слово «любим*» — иначе
    «люблю фильмы про космос» не должно случайно матчиться."""
    text = text or ""
    if re.search(r"словар", text, re.I):  # «добавь книгу в словарь» — это словарь, не библиотека
        return None
    loved = _LOVE_ADD_VERB_RE.search(text) and _LOVE_WORD_RE.search(text)
    if not loved and not _DIRECT_ADD_RE.search(text):
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
    payload = re.sub(r"[ \t]+", " ", payload).strip(" \t\n\r:;,.-–—")
    return store_key, folder_label, payload  # пустое название — открыть добавление этой вкладки


async def try_add_love_from_chat(bot, cid, text):
    """«Добавь книги / музыку / фильмы или сериалы X, Y» из чата — в нужную вкладку «Моей библиотеки».

    Несколько названий — через запятую или с новой строки. Без названия — обычное добавление вкладки.
    """
    import config
    import personal_collections
    import store as _store
    detected = _detect_love_add(text)
    if not detected:
        return False
    store_key, folder_label, payload = detected
    if not payload:
        await personal_collections.love_add_start(bot, cid, store_key, origin="leisure")
        return True
    from leisure_collection import (
        _resolve_movie_label, canonical_movie_label, movie_title_for_lookup, plain_label,
    )
    list_key = {"movies": config.FAVORITE_MOVIES_KEY, "books": config.FAVORITE_BOOKS_KEY,
                "artists": config.FAVORITE_ARTISTS_KEY}[store_key]

    def comparable(value):
        value = value.get("value", "") if isinstance(value, dict) else str(value)
        return (movie_title_for_lookup(value) if store_key == "movies" else value).strip().lower()

    existing = {comparable(item) for item in _store.get_list(list_key, cid)}
    added, already, missing = [], [], []
    for raw in dict.fromkeys(part.strip() for part in re.split(r"[,;\n]+", payload) if part.strip()):
        title = plain_label(raw)
        if store_key == "movies":
            # Тот же строгий поиск, что при ручном добавлении: чужой фильм не сохраняется.
            try:
                metadata = await asyncio.wait_for(
                    asyncio.to_thread(_resolve_movie_label, movie_title_for_lookup(raw), allow_ai=True),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                metadata = None
            if not metadata:
                missing.append(title)
                continue
            title = canonical_movie_label(raw, metadata)
        if comparable(title) in existing:
            already.append(title)
            continue
        _store.add_to_list(list_key, cid, title)
        existing.add(comparable(title))
        added.append(title)
    quoted = lambda titles: ", ".join(f"«{title}»" for title in titles)  # noqa: E731
    lines = []
    if added:
        lines.append(f"❤️ Добавил в библиотеку · {folder_label}: {quoted(added)}.")
    if already:
        lines.append(f"Уже в библиотеке: {quoted(already)}.")
    if missing:
        lines.append(f"Не нашёл {quoted(missing)} среди фильмов и сериалов. Уточни название — можно оригинальное.")
    await bot.send_message(chat_id=cid, text="\n".join(lines))
    return True


def _detect_intent(text: str):
    t = text.lower()
    informational_markers = (
        "как запомнить", "как различать", "в чём разница", "в чем разница",
        "чем отличается", "чем отличаются", "различие между", "разница между",
        "что значит", "что означает", "объясни", "объясните",
        # Вопрос о конкретном фильме или человеке — ответ ассистента, не подборка.
        "что за ", "кто такой", "кто такая", "кто такие", "расскажи", "о чём", "о чем",
        "про что", "сколько сезонов", "чем закончил",
    )
    if any(marker in t for marker in informational_markers):
        return None
    for keywords, action in _INTENT_MAP:
        if any(re.search(rf"(?<!\w){re.escape(kw)}", t) for kw in keywords):
            return action
    return None


def _specific_recipe_request(text: str):
    """«есть рецепт быстрой выпечки в духовке к чаю из гранолы» → сама просьба для рецепта; общий
    вопрос («что приготовить на ужин?», «дай рецепт») → None — тогда выбор «Завтрак / Обед / Ужин»."""
    words = re.findall(r"[а-яёa-z-]+", str(text or "").casefold())
    meaningful = [word for word in words if word not in _RECIPE_FILLER and len(word) > 2]
    if len(meaningful) < 2:
        return None
    return " ".join(str(text).split()).strip(" ?!.")[:200]


def _recipe_ingredients_from_chat(text: str):
    """Return an explicitly named ingredient for a direct recipe card."""
    meal_matched = _MEAL_WITH_INGREDIENT_RE.search(text or "")
    if meal_matched:
        meal = meal_matched.group(1).casefold()
        ingredients = re.sub(r"\s+", " ", meal_matched.group(2)).strip(" ,;:—-.")
        if ingredients and ingredients.casefold() not in _GENERIC_RECIPE_INGREDIENTS:
            return f"{meal} с {ingredients}"[:160]
    matched = _RECIPE_INGREDIENT_RE.search(text or "")
    if not matched:
        return None
    ingredients = re.sub(r"\s+", " ", matched.group(1)).strip(" ,;:—-.")
    if not ingredients or ingredients.casefold() in _GENERIC_RECIPE_INGREDIENTS:
        return None
    return ingredients[:160]


async def _run_intent(bot, cid, action, recipe_ingredients=None):
    import cooking
    import leisure_movies
    import myday
    import settings
    import wardrobe
    import fridge
    import leisure_concerts
    import leisure_music
    import learning_dictionary as dictionary
    import weather as wx
    # Ответы ассистента на свободный текст не должны нести кнопку «⬅️ Назад» -
    # пользователь не открывал раздел через меню, и вести её было бы некуда.
    no_kb_bot = settings._NoKbBot(bot)
    if action == "meal_request":
        # Конкретная просьба — рецепт именно под неё, а не общий выбор приёма пищи.
        await cooking.send_recipe(bot, cid, f"по просьбе пользователя: {recipe_ingredients}")
    elif action == "meal_recipe":
        is_meal_request = str(recipe_ingredients or "").casefold().startswith(
            ("завтрак ", "обед ", "ужин ")
        )
        constraint = recipe_ingredients if is_meal_request else f"блюдо из {recipe_ingredients}"
        await cooking.send_recipe(bot, cid, constraint)
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
    elif action == "concerts":
        await leisure_concerts.find_concerts(no_kb_bot, cid, "home")
    elif action == "learn":
        text, entities, kb = menu.menu_screen("m_learn", cid)
        await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb)
    elif action == "dictionary":
        await dictionary.send_dict(no_kb_bot, cid)
    elif action == "outfit":
        await wardrobe.send_looks(no_kb_bot, cid)
    elif action == "weather":
        await wx.send_weather(no_kb_bot, cid, "today")


# Плавная печать целого ответа: до ~12 шагов по ~0.12 с (≈1.5 с на ответ).
_TYPE_CHUNK_CHARS = 80
_TYPE_MAX_STEPS = 12
_TYPE_STEP_SECONDS = 0.12


def _typing_pieces(text):
    """Делит текст на 3–12 кусков по границам слов для эффекта печати."""
    words = re.split(r"(?<=\s)", text)
    size = max(_TYPE_CHUNK_CHARS // 2, len(text) // _TYPE_MAX_STEPS + 1)
    pieces, current = [], ""
    for word in words:
        current += word
        if len(current) >= size:
            pieces.append(current)
            current = ""
    if current:
        pieces.append(current)
    return pieces


def _with_final_period(text: str) -> str:
    """Ответ модели иногда обрывается без точки — дописываем её после слова, цифры,
    кавычки или скобки; знаки препинания, эмодзи и код не трогаем."""
    text = (text or "").rstrip()
    if text and not text.endswith("```") and (text[-1].isalnum() or text[-1] in "»\")'"):
        return text + "."
    return text


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

    # Базовая кризисная защита остаётся частью общего ассистента, даже без
    # отдельного медицинского раздела.
    if secure.is_dangerous_med(text):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG)
        return

    await bot.send_chat_action(chat_id=cid, action="typing")

    # Intent-роутинг — сразу запускаем нужную функцию
    recipe_ingredients = _recipe_ingredients_from_chat(text)
    intent = "meal_recipe" if recipe_ingredients else _detect_intent(text)
    if intent == "meal_picker":
        request = _specific_recipe_request(text)
        if request:
            intent, recipe_ingredients = "meal_request", request
    if intent:
        await _run_intent(bot, cid, intent, recipe_ingredients)
        store.last_surface[str(cid)] = "chat"
        return

    # Фолбэк - LLM-ответ без прикрепленного главного меню
    hist = store.chat_history.get(str(cid), [])
    prompt_text = text
    if research.requires_explicit_web_search(text):
        rows = await asyncio.to_thread(
            research.web_search, text, 3, scenario="explicit_research",
            allow_tavily=True, search_priority="tavily",
        )
        sources = [" ".join(str(row.get("content") or "").split())[:700] for row in rows]
        sources = [source for source in sources if source]
        if sources:
            prompt_text += "\n\nСвежие фрагменты из открытых источников:\n" + "\n---\n".join(sources)
    hist.append({"role": "user", "content": prompt_text})
    hist = hist[-10:]
    # Свои данные — только в текущий запрос к AI (не в сохраняемую историю):
    # вещи шкафа, продукты, любимое — по теме вопроса.
    personal = await asyncio.to_thread(assistant_context.build, cid, text)
    call_hist = hist
    if personal:
        call_hist = [*hist[:-1], {"role": "user", "content": (
            f"{prompt_text}\n\nДанные пользователя — опирайся на них, называй его конкретные вещи, "
            "продукты и любимое; не выдумывай того, чего в списке нет:\n"
            f"{secure.wrap_untrusted(personal, 'данные пользователя')}"
        )}]
    # Rich drafts are Telegram's native live preview surface. Classic bots and
    # older delivery paths keep the existing editable status message instead.
    draft = await rich_delivery.start_draft(bot, cid)
    status = None if draft is not None else await util.StatusManager.start(bot, cid)
    streamed_text = ""
    visible_stream = _VisibleAssistantStream()
    last_draft_at = time.monotonic()
    last_draft_length = 0

    async def update_draft(delta):
        """Coalesce model tokens into a readable Telegram live preview."""
        nonlocal streamed_text, last_draft_at, last_draft_length
        visible_delta = visible_stream.add(delta)
        if not visible_delta:
            return
        if len(visible_delta) > _TYPE_CHUNK_CHARS:
            # Провайдер без потока (Gemini, Cloudflare) отдал ответ целиком —
            # черновик всё равно допечатывает его плавно, по словам.
            for piece in _typing_pieces(visible_delta):
                streamed_text += piece
                await draft.text(assistant_ui.preview_text(streamed_text))
                await asyncio.sleep(_TYPE_STEP_SECONDS)
            last_draft_at = time.monotonic()
            last_draft_length = len(streamed_text)
            return
        streamed_text += visible_delta
        now = time.monotonic()
        if (
            last_draft_length > 0
            and
            len(streamed_text) - last_draft_length < 80
            and now - last_draft_at < 0.45
        ):
            return
        await draft.text(assistant_ui.preview_text(streamed_text))
        last_draft_at = now
        last_draft_length = len(streamed_text)

    try:
        answer = (
            await ai.achat_chain_stream(call_hist, cid, on_delta=update_draft)
            if draft is not None else await ai.achat_chain(call_hist, cid)
        )
    except Exception as e:
        if status is not None:
            await status.stop(delete=True)
        if draft is not None and isinstance(e, ai.StreamOutputInterrupted):
            interrupted = MessageSpec(
                text=str(e),
                rich_message={"blocks": [{"type": "paragraph", "text": str(e)}]},
            )
            try:
                await rich_delivery.send(bot, cid, interrupted)
            except Exception as error:
                # The API may have accepted the rich message before a network
                # error reached us. A classic retry here could duplicate the
                # final answer in the chat, so only validation errors fall
                # back inside rich_delivery itself.
                _log.warning(
                    "Rich interrupted-answer delivery unconfirmed; skip duplicate fallback: %s",
                    type(error).__name__,
                )
            return
        await verify.safe_error(bot, cid, e); return
    answer = _with_final_period(_visible_model_text(answer).strip())
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    store.last_answer[str(cid)] = answer
    store.last_surface[str(cid)] = "chat"
    msg = assistant_ui.assistant_answer((answer or "").strip() or "Пусто, попробуй ещё раз.")
    if draft is not None:
        try:
            await rich_delivery.send(bot, cid, msg)
        except Exception as error:
            # rich_delivery already falls back to classic text for known Bot
            # API validation errors. For a timeout/network error delivery is
            # uncertain, and a second send can create a duplicate response.
            _log.warning(
                "Rich assistant-answer delivery unconfirmed; skip duplicate fallback: %s",
                type(error).__name__,
            )
        return
    ok = await status.replace(msg.text, entities=msg.entities)
    if not ok:
        try:
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
        except Exception:
            await verify.safe_send(bot, cid, msg.text, surface="chat")
