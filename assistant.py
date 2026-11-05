import asyncio
import logging
import re
import time

import store
import ai
import util
import verify
import research
import rich_delivery
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


_LOVE_ADD_VERB_RE = re.compile(r"\b(добавь|добавить|занеси|запиши|сохрани|сохранить|закинь)\b", re.I)
_LOVE_WORD_RE = re.compile(r"\bв\s+(?:мои\s+|мой\s+)?любим(?:ые|ое|ых|ый|ую)\b", re.I)
# «Добавь сериал X» — глагол сразу перед категорией: слово «любимые» не обязательно.
_DIRECT_ADD_RE = re.compile(
    r"^\s*(?:добавь|добавить|занеси|запиши|сохрани|сохранить|закинь)\s+(?:мне\s+)?"
    r"(?:фильм|сериал|кино|музык|музыкант|исполнител|артист|групп)",
    re.I,
)

# (regex категории, config-ключ хранилища, вкладка «Моей библиотеки» для подтверждения)
_LOVE_CATEGORIES = [
    (re.compile(r"\b(фильм[а-я]*|сериал[а-я]*|кино)\b", re.I), "movies", "Фильмы и сериалы"),
    (re.compile(r"\b(музык[а-я]*|музыкант[а-я]*|исполнител[а-я]*|артист[а-я]*|груп[а-я]*)\b", re.I),
     "artists", "Музыка"),
]

# Слово категории вместе с «фильмы или сериалы» убирается из названия целиком.
_MOVIE_WORD = r"(?:фильм[а-я]*|сериал[а-я]*|кино)"
_LOVE_CATEGORY_KEY_RE = re.compile(
    rf"\b(?:{_MOVIE_WORD}(?:\s+(?:или|и)\s+{_MOVIE_WORD})?|"
    r"музык[а-я]*|музыкант[а-я]*|исполнител[а-я]*|артист[а-я]*|груп[а-я]*)\b",
    re.I,
)

def _detect_love_add(text: str):
    """«Добавь в любимые фильм X» -> (store_key, folder_label, title) | None.

    Триггер строго требует и глагол добавления, и слово «любим*» — иначе
    «люблю фильмы про космос» не должно случайно матчиться."""
    text = text or ""
    if re.search(r"словар", text, re.I):  # «добавь фильм в словарь» — это словарь, не библиотека
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


_WARDROBE_ADD_VERB = r"(?:добавь|добавить|занеси|запиши|сохрани|сохранить|закинь)"
_WARDROBE_PLACE = r"(?:в\s+(?:мой\s+)?(?:шкаф|гардероб))"
_WARDROBE_ADD_RES = (
    # «добавь в шкаф: чёрная футболка», «добавь мне в гардероб белые кеды»
    re.compile(rf"^\s*{_WARDROBE_ADD_VERB}\s+(?:мне\s+)?{_WARDROBE_PLACE}\s*[:\-—]?\s*(.*)$", re.I | re.S),
    # «добавь чёрную футболку в шкаф»
    re.compile(rf"^\s*{_WARDROBE_ADD_VERB}\s+(?:мне\s+)?(.+?)\s+{_WARDROBE_PLACE}\s*[.!]?\s*$", re.I | re.S),
    # «добавь вещь синие джинсы», «добавь одежду: …»
    re.compile(rf"^\s*{_WARDROBE_ADD_VERB}\s+(?:мне\s+)?(?:одежду|вещь|вещи)\s*[:\-—]?\s*(.*)$", re.I | re.S),
)


def detect_wardrobe_add(text: str):
    """«Добавь в шкаф чёрную футболку» → «чёрную футболку»; команда без вещи → ""; не команда шкафа → None.
    Без этого словарь принимал «в шкаф чёрную футболку» за новое слово."""
    for pattern in _WARDROBE_ADD_RES:
        matched = pattern.search(str(text or ""))
        if matched:
            payload = re.sub(r"\s+", " ", matched.group(1)).strip(" \t\n\r:;,.-–—")
            return re.sub(rf"\s*{_WARDROBE_PLACE}$", "", payload, flags=re.I).strip()
    return None


async def try_add_wardrobe_from_chat(bot, cid, text) -> bool:
    """Вещь из чата — в шкаф (тот же разбор, что у «Добавить вещь»); без вещи — вопрос, что добавить."""
    payload = detect_wardrobe_add(text)
    if payload is None:
        return False
    import wardrobe
    if payload:
        await wardrobe.add_item(bot, cid, payload)
    else:
        import wardrobe_router
        await wardrobe_router.handle_callback(bot, cid, None, "w_add")
    return True


async def try_add_love_from_chat(bot, cid, text):
    """«Добавь музыку / фильмы или сериалы X, Y» из чата — в нужную вкладку «Моей библиотеки».

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
    list_key = {"movies": config.FAVORITE_MOVIES_KEY, "artists": config.FAVORITE_ARTISTS_KEY}[store_key]

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
    shown = added + already
    if store_key == "movies" and len(shown) == 1:
        # Один фильм — сразу его карточка в «Моём кино», без лишнего нажатия.
        import leisure_movies
        await leisure_movies.open_favorite_movie(bot, cid, shown[0])
    return True


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

    # Любой вопрос или просьба в чате — ответ ИИ с данными пользователя по теме (шкаф, продукты, любимое, концерты,
    # погода). Экраны разделов чат не открывает: «когда концерт Tove Lo?» — ответ, а не список концертов. Команды
    # добавления (слово, вещь, продукт, фильм, музыка) разбирает bot_text раньше.
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
