import re
import ai

_WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]

def esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

async def send_long(bot, chat_id, text):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000])

def country_flag(name):
    try:
        out = ai.llm(f"Верни ТОЛЬКО эмодзи флага страны: {name}. Без текста.", 20, 0).strip()
        return out if out else "🏳"
    except Exception:
        return "🏳"

def flag_from_cc(cc):
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)

# --- markdown -> Telegram HTML (страховка поверх системного промпта) ---
_ALLOWED_TAGS = ("b", "i", "u", "s", "code", "pre", "a")

def tg_html(text):
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
    tag_re = re.compile(r"</?(?:" + "|".join(_ALLOWED_TAGS) + r")(?:\s[^>]*)?>", re.I)
    t = tag_re.sub(_stash, t)

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