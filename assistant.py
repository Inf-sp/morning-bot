from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
import ai
import verify

# ---------- свободный чат ----------
_MED_WORDS = ("боль", "болит", "температур", "симптом", "врач", "таблет", "лекарств", "горло",
              "кашель", "тошнот", "давлен", "head", "сыпь", "простуд", "грипп", "живот")

async def chat_reply(bot, cid, text):
    store.last_action[str(cid)] = None
    store.last_source[str(cid)] = "Ассистент"
    await bot.send_chat_action(chat_id=cid, action="typing")
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = await ai.achat_chain(hist)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await verify.safe_send(bot, cid, (answer or "").strip() or "Пусто, попробуй ещё раз.", surface="chat")
    store.last_answer[str(cid)] = answer
    store.last_surface[str(cid)] = "chat"
    if any(w in text.lower() for w in _MED_WORDS):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👩🏻‍⚕️ Вопрос врачу", callback_data="as_doctor")]])
        await bot.send_message(chat_id=cid,
            text="👩🏻‍⚕️ Похоже на вопрос о здоровье. В разделе 🧠 Баланс → «Вопрос врачу» дам подробный структурированный разбор.",
            reply_markup=kb)
