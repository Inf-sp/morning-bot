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

async def send_welcome(bot, cid):
    await bot.send_message(chat_id=cid, text=WELCOME)

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
    await send_long(bot, cid, answer)
