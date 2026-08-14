"""Координатор повторов и изменения длины обычных ответов."""

import ai
import cooking
import secure
import store
import verify
from response_delivery import send_response


async def _retry_chat(bot, cid):
    hist = list(store.chat_history.get(str(cid), []))
    if not hist:
        await bot.send_message(chat_id=cid, text="Нет предыдущего запроса.")
        return
    if hist[-1]["role"] == "assistant":
        hist = hist[:-1]
    await bot.send_chat_action(chat_id=cid, action="typing")
    nudge = hist + [{"role": "user", "content": "Продолжи мысль или дай более полезный вариант."}]
    try:
        answer = await ai.achat_chain(nudge, cid)
    except Exception as error:
        await verify.safe_error(bot, cid, error)
        return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await send_response(bot, cid, answer, surface="chat")


async def retry_last_response(bot, cid, status=None):
    if await cooking.retry_last_action(bot, cid, status=status):
        return
    await _retry_chat(bot, cid)


async def reword_last_response(bot, cid, mode):
    prev = (store.last_answer.get(str(cid)) or "").strip()
    if not prev:
        await bot.send_message(chat_id=cid, text="Нет ответа, который можно переписать.")
        return
    surface = store.last_surface.get(str(cid), "card")
    if mode == "short":
        how, tier = "короче и без воды, оставь только суть", "cheap"
    else:
        how, tier = "подробнее и глубже, добавь полезные детали и нюансы", "smart"
    await bot.send_chat_action(chat_id=cid, action="typing")
    prompt = (
        f"Перепиши этот ответ {how}. Сохрани смысл и тот же язык. "
        "Формат - Telegram HTML: подзаголовки <b>...</b>, пункты с «• », "
        "без markdown (без *, #, `).\n\n"
        f"Текст:\n{secure.wrap_untrusted(prev, 'предыдущий ответ')}"
    )
    try:
        out = await ai.allm(prompt, 1200, 0.6, tier=tier)
    except Exception as error:
        await verify.safe_error(bot, cid, error)
        return
    await send_response(bot, cid, out, surface=surface)
