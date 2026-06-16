from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
import ai
from util import send_long

WELCOME = (
    "💬 Ассистент\n\n"
    "Что будем делать сегодня?\n"
    "Помогу с делами, языками, гардеробом, путешествиями и поиском информации.\n\n"
    "Попробуй спросить:\n"
    "👕 Что надеть сегодня?\n"
    "🇳🇱 Объясни разницу между die и dat\n"
    "✈️ Куда поехать на выходные из Нидерландов?\n"
    "📚 Посоветуй книгу как «Цветы для Элджернона»\n"
    "🎬 Найди сериал похожий на The Last of Us\n"
    "🌤 Какая погода на выходных?\n\n"
    "💡 Или просто расскажи, что сейчас в голове - задача, идея, проблема или вопрос."
)

_RETRY_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Ещё раз (не нравится ответ)", callback_data="chat_retry")]])

async def send_welcome(bot, cid):
    await bot.send_message(chat_id=cid, text=WELCOME)

async def _send_with_retry(bot, cid, text):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    # длинный текст бьём, кнопку вешаем на последнее сообщение
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for c in chunks[:-1]:
        await bot.send_message(chat_id=cid, text=c)
    await bot.send_message(chat_id=cid, text=chunks[-1], reply_markup=_RETRY_KB)

async def chat_reply(bot, cid, text):
    await bot.send_chat_action(chat_id=cid, action="typing")
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = ai.chat_chain(hist)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка чата: {e}")
        return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send_with_retry(bot, cid, answer)

async def chat_retry(bot, cid):
    hist = list(store.chat_history.get(str(cid), []))
    if not hist:
        await bot.send_message(chat_id=cid, text="Нет предыдущего запроса.")
        return
    if hist[-1]["role"] == "assistant":
        hist = hist[:-1]
    await bot.send_chat_action(chat_id=cid, action="typing")
    nudge = hist + [{"role": "user", "content": "Дай другой, более чёткий и полезный вариант ответа на мой последний вопрос."}]
    try:
        answer = ai.chat_chain(nudge)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send_with_retry(bot, cid, answer)