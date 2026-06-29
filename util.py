import re
from html import escape as _html_escape

_WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
_WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]

def esc(t: str | None) -> str:
    return _html_escape(t or "", quote=False)

def cap_sentence(t: str | None) -> str:
    """Заглавная первая буква для коротких LLM-фраз, не меняя остальной текст."""
    s = (t or "").strip()
    return s[:1].upper() + s[1:] if s else s

async def ack_loading(q) -> None:
    """Меняет клавиатуру на ⏳ пока идёт медленная LLM-операция. Ошибки игнорирует."""
    try:
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏳ Генерация…", callback_data="noop")]])
        await q.edit_message_reply_markup(reply_markup=kb)
    except Exception:
        pass

async def send_html(bot, cid, text: str | None, reply_markup=None) -> None:
    """Одиночное сообщение в Telegram HTML с чисткой markdown и откатом на plain."""
    from telegram.error import BadRequest
    html = tg_html(text or "")
    try:
        await bot.send_message(chat_id=cid, text=html, parse_mode="HTML", reply_markup=reply_markup)
    except BadRequest:
        await bot.send_message(chat_id=cid, text=html, reply_markup=reply_markup)

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

def country_flag(name):
    """Эмодзи флага по названию страны - офлайн, без LLM. Неизвестное -> 🏳."""
    cc = cc_of(name)
    return flag_from_cc(cc) if cc else "🏳"

def flag_from_cc(cc: str) -> str:
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)

# --- markdown -> Telegram HTML (страховка поверх системного промпта) ---
_ALLOWED_TAGS = ("b", "i", "u", "s", "code", "pre", "a")
_TAG_RE = re.compile(r"</?(?:" + "|".join(_ALLOWED_TAGS) + r")(?:\s[^>]*)?>", re.I)

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
        out.append(indent + s)
    t = "\n".join(out)

    # 5) вернуть спрятанные теги
    t = re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], t)

    # 6) убрать лишние пустые строки (макс одна подряд)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t
