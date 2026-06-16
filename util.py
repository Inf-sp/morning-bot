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
