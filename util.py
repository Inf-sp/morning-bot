import asyncio
import contextlib
import logging
import random
import re
import time
from html import escape as _html_escape

_log = logging.getLogger(__name__)

_WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
_WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]
_TTL_CACHE = {}

LOADING_PHRASES = [
    "⏳ Готовлю ответ…",
    "🔍 Ищу нужную информацию…",
    "🔍 Собираю данные…",
    "⏳ Анализирую запрос…",
    "⏳ Формулирую ответ…",
]

def loading_phrase() -> str:
    return random.choice(LOADING_PHRASES)


class StatusManager:
    """Редактируемый индикатор ожидания для долгих операций."""

    STAGES = (
        (0, "⏳ Ищу ответ..."),
        (3, "🔍 Проверяю данные..."),
        (8, "🧩 Собираю лучший ответ..."),
        (15, "✨ Почти готово..."),
    )

    # Тематические фразы ожидания по разделу — вместо общего "Ищу ответ" на
    # экране конкретного раздела (гардероб, готовка и т.п.), см. TOPIC_STAGES.
    TOPIC_STAGES = {
        "wardrobe": (
            (0, "⏳ Разбираю шкаф..."),
            (3, "🧩 Собираю образ..."),
            (8, "✨ Почти готово..."),
        ),
        "food": (
            (0, "⏳ Ищу рецепт..."),
            (3, "🥕 Подбираю ингредиенты..."),
            (8, "✨ Почти готово..."),
        ),
        "learning": (
            (0, "⏳ Готовлю задание..."),
            (3, "📚 Подбираю слова..."),
            (8, "✨ Почти готово..."),
        ),
        "leisure": (
            (0, "⏳ Подбираю варианты..."),
            (3, "🎬 Сверяюсь со вкусом..."),
            (8, "✨ Почти готово..."),
        ),
        "travel": (
            (0, "⏳ Ищу маршрут..."),
            (3, "🗺️ Прикидываю план..."),
            (8, "✨ Почти готово..."),
        ),
        "health": (
            (0, "⏳ Собираю мысли..."),
            (3, "💬 Подбираю слова..."),
            (8, "✨ Почти готово..."),
        ),
    }

    def __init__(self, bot, cid=None, message=None, parse_mode=None, mode="message", stages=None):
        self.bot = bot
        self.cid = cid
        self.message = message
        self.parse_mode = parse_mode
        self.mode = mode
        self.stages = stages or self.STAGES
        self._inline_cleared = False
        self._task = None
        self._stopped = asyncio.Event()

    @classmethod
    async def start(cls, bot, cid=None, message=None, text=None, parse_mode=None, stages=None):
        manager = cls(bot, cid=cid, message=message, parse_mode=parse_mode, stages=stages)
        first_text = text or manager.stages[0][1]
        if manager.message is None:
            manager.message = await bot.send_message(chat_id=cid, text=first_text, parse_mode=parse_mode)
        else:
            await manager._edit(first_text)
        manager._task = asyncio.create_task(manager._run())
        return manager

    @classmethod
    async def start_inline(cls, q, bot=None, cid=None, text=None, stages=None):
        manager = cls(bot, cid=cid, message=q.message, mode="inline", stages=stages)
        await manager._edit(text or manager.stages[0][1])
        manager._task = asyncio.create_task(manager._run())
        return manager

    async def _run(self):
        started = time.monotonic()
        for delay, text in self.stages[1:]:
            timeout = max(0, delay - (time.monotonic() - started))
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=timeout)
                return
            except asyncio.TimeoutError:
                pass
            if self._stopped.is_set():
                return
            await self._edit(text)

    async def _edit(self, text, **kwargs):
        if self.message is None:
            return False
        if self.mode == "inline" and self._inline_cleared:
            return False
        try:
            if self.mode == "inline":
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data="noop")]])
                await self.message.edit_reply_markup(reply_markup=kb)
            else:
                await self.message.edit_text(text, **kwargs)
            return True
        except Exception:
            return False

    async def stop(self, delete=True):
        await self._cancel()
        if self.mode == "inline" and self.message is not None:
            await self._clear_inline_markup()
        elif delete and self.message is not None:
            _log.info("StatusManager.stop: deleting message_id=%s cid=%s",
                      getattr(self.message, "message_id", None), self.cid)
            try:
                await self.message.delete()
            except Exception as e:
                _log.warning("StatusManager.stop: delete failed: %r", e)

    async def _clear_inline_markup(self):
        self._inline_cleared = True
        ok = False
        try:
            await self.message.edit_reply_markup(reply_markup=None)
            ok = True
        except Exception as e:
            _log.info("StatusManager._clear_inline_markup: edit_reply_markup via message failed: %r", e)
        if ok or self.bot is None:
            return
        chat_id = self.cid
        if chat_id is None:
            chat = getattr(self.message, "chat", None)
            chat_id = getattr(chat, "id", None) or getattr(self.message, "chat_id", None)
        message_id = getattr(self.message, "message_id", None)
        if chat_id is not None and message_id is not None:
            try:
                await self.bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=None,
                )
            except Exception as e:
                _log.warning("StatusManager._clear_inline_markup: edit via bot failed chat_id=%s message_id=%s: %r",
                            chat_id, message_id, e)

    async def replace(self, text, **kwargs):
        await self._cancel()
        if self.mode == "inline":
            await self.stop(delete=False)
            if self.cid is not None and self.bot is not None:
                _log.info("StatusManager.replace(inline): sending new message cid=%s text_len=%s",
                          self.cid, len(text or ""))
                try:
                    msg = await self.bot.send_message(chat_id=self.cid, text=text, **kwargs)
                except Exception as e:
                    _log.error("StatusManager.replace(inline): send_message failed cid=%s: %r", self.cid, e, exc_info=True)
                    raise
                _log.info("StatusManager.replace(inline): sent message_id=%s cid=%s", msg.message_id, self.cid)
                return True
            _log.warning("StatusManager.replace(inline): no cid/bot, cid=%s bot=%s", self.cid, self.bot is not None)
            return False
        ok = await self._edit(text, **kwargs)
        if not ok and self.cid is not None:
            await self.bot.send_message(chat_id=self.cid, text=text, **kwargs)
            return True
        return ok

    async def _cancel(self):
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

def ttl_get(namespace: str, key: str, ttl: int):
    hit = _TTL_CACHE.get((namespace, key))
    if not hit:
        return None
    ts, value = hit
    if time.time() - ts > ttl:
        _TTL_CACHE.pop((namespace, key), None)
        return None
    return value

def ttl_set(namespace: str, key: str, value):
    _TTL_CACHE[(namespace, key)] = (time.time(), value)
    return value

def esc(t: str | None) -> str:
    return _html_escape(t or "", quote=False)

def cap_sentence(t: str | None) -> str:
    """Заглавная первая буква для коротких LLM-фраз, не меняя остальной текст."""
    s = (t or "").strip()
    return s[:1].upper() + s[1:] if s else s

async def ack_loading(q) -> None:
    """Меняет клавиатуру на статус ожидания пока идёт медленная LLM-операция. Ошибки игнорирует."""
    try:
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(loading_phrase(), callback_data="noop")]])
        await q.edit_message_reply_markup(reply_markup=kb)
    except Exception:
        pass

async def clear_loading(q) -> None:
    """Убирает клавиатуру-индикатор загрузки после того, как готовый ответ уже отправлен новым сообщением."""
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


# Имя страны (ru/en, нижний регистр) -> ISO-2 код. Офлайн, без LLM.
_COUNTRY_CC = {
    "нидерланды": "NL", "голландия": "NL", "netherlands": "NL", "holland": "NL",
    "бельгия": "BE", "belgium": "BE", "германия": "DE", "germany": "DE",
    "франция": "FR", "france": "FR", "испания": "ES", "spain": "ES",
    "италия": "IT", "italy": "IT", "португалия": "PT", "portugal": "PT",
    "великобритания": "GB", "англия": "GB", "соединённое королевство": "GB",
    "uk": "GB", "united kingdom": "GB", "england": "GB", "britain": "GB",
    "ирландия": "IE", "ireland": "IE", "австрия": "AT", "austria": "AT",
    "швейцария": "CH", "switzerland": "CH", "польша": "PL", "poland": "PL",
    "чехия": "CZ", "czechia": "CZ", "czech republic": "CZ", "словакия": "SK", "slovakia": "SK",
    "венгрия": "HU", "hungary": "HU", "швеция": "SE", "sweden": "SE",
    "норвегия": "NO", "norway": "NO", "дания": "DK", "denmark": "DK",
    "финляндия": "FI", "finland": "FI", "исландия": "IS", "iceland": "IS",
    "греция": "GR", "greece": "GR", "хорватия": "HR", "croatia": "HR",
    "словения": "SI", "slovenia": "SI", "румыния": "RO", "romania": "RO",
    "болгария": "BG", "bulgaria": "BG", "сербия": "RS", "serbia": "RS",
    "люксембург": "LU", "luxembourg": "LU", "эстония": "EE", "estonia": "EE",
    "латвия": "LV", "latvia": "LV", "литва": "LT", "lithuania": "LT",
    "россия": "RU", "russia": "RU", "украина": "UA", "ukraine": "UA",
    "сша": "US", "америка": "US", "usa": "US", "united states": "US", "us": "US",
    "канада": "CA", "canada": "CA", "мексика": "MX", "mexico": "MX",
    "бразилия": "BR", "brazil": "BR", "аргентина": "AR", "argentina": "AR",
    "чили": "CL", "chile": "CL",
    "япония": "JP", "japan": "JP", "китай": "CN", "china": "CN",
    "южная корея": "KR", "корея": "KR", "south korea": "KR", "korea": "KR",
    "таиланд": "TH", "thailand": "TH", "вьетнам": "VN", "vietnam": "VN",
    "индия": "IN", "india": "IN", "индонезия": "ID", "indonesia": "ID",
    "турция": "TR", "turkey": "TR", "türkiye": "TR", "оаэ": "AE",
    "uae": "AE", "эмираты": "AE", "united arab emirates": "AE",
    "египет": "EG", "egypt": "EG", "марокко": "MA", "morocco": "MA",
    "израиль": "IL", "israel": "IL", "грузия": "GE", "georgia": "GE",
    "австралия": "AU", "australia": "AU", "новая зеландия": "NZ", "new zealand": "NZ",
    "кипр": "CY", "cyprus": "CY", "мальта": "MT", "malta": "MT",
}

def cc_of(name):
    """ISO-2 код по названию страны (ru/en) или '' если неизвестно."""
    return _COUNTRY_CC.get((name or "").strip().lower(), "")


def country_name_from_cc(cc):
    """Русское название страны по ISO-2 без сетевого запроса."""
    code = str(cc or "").strip().upper()
    special = {"AE": "ОАЭ", "US": "США"}
    if code in special:
        return special[code]
    for name, value in _COUNTRY_CC.items():
        if value == code and re.search(r"[а-яё]", name, re.I):
            return name.capitalize()
    return ""

def country_flag(name):
    """Эмодзи флага по названию страны - офлайн, без LLM. Неизвестное -> ''."""
    from ui.constants import COUNTRY_EMOJI

    cc = cc_of(name)
    return COUNTRY_EMOJI.get(cc.lower(), "") if cc else ""

def flag_from_cc(cc: str) -> str:
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)

# --- markdown -> Telegram HTML (страховка поверх системного промпта) ---
_ALLOWED_TAGS = ("b", "i", "u", "s", "code", "pre", "a")
_TAG_RE = re.compile(r"</?(?:" + "|".join(_ALLOWED_TAGS) + r")(?:\s[^>]*)?>", re.I)
_PLAIN_LABEL_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<label>[^:<>{}\n]{1,64}):(?P<space>\s+)(?P<body>.+)$"
)
_CASE_SENSITIVE_LABELS = {
    "автор", "город", "дата", "имя", "место", "название", "ссылка",
    "страна", "фраза", "твой ответ",
}


def _lower_plain_initial(text: str) -> str:
    for index, char in enumerate(text):
        if char.isalpha():
            return text[:index] + char.lower() + text[index + 1:]
        if char.isdigit() or char == "<":
            break
        if not char.isspace() and char not in "«„\"'([{—–-":
            break
    return text


def _format_plain_label_line(line: str) -> str:
    """Страховка для свободного AI-текста: ``Подпись: текст`` -> Telegram HTML."""
    match = _PLAIN_LABEL_LINE_RE.match(line)
    if not match:
        return line
    label = match.group("label").strip()
    if not any(char.isalpha() for char in label) or len(label.split()) > 7:
        return line
    first_alpha = next((char for char in label if char.isalpha()), "")
    if not first_alpha.isupper():
        return line
    body = match.group("body").strip()
    if label.casefold() not in _CASE_SENSITIVE_LABELS:
        body = _lower_plain_initial(body)
    return f'{match.group("indent")}<b>{label}:</b> {body}'


def _lower_html_label_content(line: str) -> str:
    match = re.match(
        r"^(?P<prefix>\s*<b>(?P<label>[^<]{1,64}):</b>\s+)(?P<body>.+)$",
        line,
    )
    if not match:
        return line
    body = match.group("body")
    if match.group("label").strip().casefold() not in _CASE_SENSITIVE_LABELS:
        body = _lower_plain_initial(body)
    return match.group("prefix") + body

def tg_html(text: str | None) -> str:
    """Чистит ответ модели под Telegram HTML: убирает markdown, оставляет
    только разрешённые теги, приводит списки к «• ». Безопасно при любом вводе."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n")

    # 1) Спрятать уже существующие разрешённые теги, чтобы не экранировать их
    saved = []
    def _stash(m):
        saved.append(m.group(0))
        return f"\x00{len(saved)-1}\x00"
    t = _TAG_RE.sub(_stash, t)

    # 2) Экранировать всё остальное
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 3) markdown -> теги
    t = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", t, flags=re.S)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    t = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<i>\1</i>", t, flags=re.S)
    t = re.sub(r"(?<!\w)__(?!\s)(.+?)(?<!\s)__(?!\w)", r"<b>\1</b>", t, flags=re.S)

    # 4) построчная чистка: маркеры списков и заголовки
    out = []
    for line in t.split("\n"):
        s = line.lstrip()
        indent = line[:len(line) - len(s)]
        s = re.sub(r"^#{1,6}\s+", "", s)          # markdown-заголовки -> обычный текст
        s = re.sub(r"^[-*•]\s+", "• ", s)         # маркеры списка -> «• »
        out.append(_format_plain_label_line(indent + s))
    t = "\n".join(out)

    # 5) вернуть спрятанные теги
    t = re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], t)
    t = "\n".join(_lower_html_label_content(line) for line in t.split("\n"))

    # 6) убрать лишние пустые строки (макс одна подряд)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


_ENTITY_TYPE = {"b": "bold", "i": "italic", "u": "underline", "s": "strikethrough", "code": "code", "pre": "pre"}
_HTML_TOKEN_RE = re.compile(r'<(/?)(\w+)((?:\s+\w+="[^"]*")*)\s*>')
_HTML_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def u16_len(text: str) -> int:
    return len((text or "").encode("utf-16-le")) // 2


def html_to_entities(text: str | None):
    """Разбирает Telegram-HTML (только теги из tg_html) в (plain_text, [MessageEntity]).
    Нужен там, где раньше отправляли parse_mode='HTML', а теперь — entities."""
    from telegram import MessageEntity

    if not text:
        return "", []

    plain_parts = []
    entities = []
    open_stack = []  # [(tag, u16_offset_start, url_or_None)]
    pos = 0
    for m in _HTML_TOKEN_RE.finditer(text):
        chunk = _html_unescape(text[pos:m.start()])
        plain_parts.append(chunk)
        pos = m.end()

        is_close, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag not in _ENTITY_TYPE and tag != "a":
            continue
        offset_now = u16_len("".join(plain_parts))
        if not is_close:
            url = None
            if tag == "a":
                am = _HTML_ATTR_RE.search(attrs)
                url = am.group(2) if am else None
            open_stack.append((tag, offset_now, url))
        else:
            for i in range(len(open_stack) - 1, -1, -1):
                if open_stack[i][0] == tag:
                    open_tag, start, url = open_stack.pop(i)
                    length = offset_now - start
                    if length > 0:
                        if open_tag == "a" and url:
                            entities.append(MessageEntity(MessageEntity.TEXT_LINK, start, length, url=url))
                        elif open_tag in _ENTITY_TYPE:
                            entities.append(MessageEntity(_ENTITY_TYPE[open_tag], start, length))
                    break
    plain_parts.append(_html_unescape(text[pos:]))
    plain = "".join(plain_parts)
    entities.sort(key=lambda e: e.offset)
    return plain, entities


def _html_unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def entities_to_json(entities) -> list:
    """list[MessageEntity] -> список JSON-совместимых словарей, для хранения в JSONB (store.py).
    Обратная операция — entities_from_json()."""
    out = []
    for e in entities or []:
        item = {"type": str(e.type), "offset": e.offset, "length": e.length}
        url = getattr(e, "url", None)
        if url:
            item["url"] = url
        out.append(item)
    return out


def entities_from_json(data) -> list:
    """Обратная операция к entities_to_json() — список словарей из JSONB -> list[MessageEntity]."""
    from telegram import MessageEntity

    return [
        MessageEntity(item["type"], item["offset"], item["length"], url=item.get("url"))
        for item in (data or [])
    ]


def chunk_text_with_entities(text: str, entities, limit: int = 4000):
    """Режет text на части по limit UTF-16-юнитов, сохраняя форматирование: каждая entity
    либо целиком попадает в один чанк со сдвинутым offset, либо (если пересекает границу)
    обрезается по границе чанка — никогда не выходит за пределы своего чанка с невалидным
    offset/length. Возвращает [(chunk_text, chunk_entities), ...]."""
    entities = sorted(entities or [], key=lambda e: e.offset)
    u16 = (text or "").encode("utf-16-le")
    total = len(u16) // 2
    if total <= limit:
        return [(text or "", list(entities))]

    chunks = []
    start = 0
    while start < total:
        end = min(start + limit, total)
        chunk_text = u16[start * 2:end * 2].decode("utf-16-le")
        chunk_entities = []
        for e in entities:
            e_start, e_end = e.offset, e.offset + e.length
            if e_end <= start or e_start >= end:
                continue
            clipped_start = max(e_start, start) - start
            clipped_end = min(e_end, end) - start
            if clipped_end > clipped_start:
                from telegram import MessageEntity
                chunk_entities.append(
                    MessageEntity(e.type, clipped_start, clipped_end - clipped_start, url=getattr(e, "url", None))
                )
        chunks.append((chunk_text, chunk_entities))
        start = end
    return chunks
