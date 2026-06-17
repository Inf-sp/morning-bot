from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import weather
import wardrobe
import myday
import ze

FONTS = ("Cuprum, Fira Sans, Lora, Montserrat, Neucha, Open Sans, Orbitron, "
         "Pacifico, Philosopher, PT Sans, PT Serif, Roboto, Rubik, Ubuntu, Loew")

HOME_TEXT = (
    "💬 Ассистент DM | Daily Manager\n\n"
    "Что делаем сегодня?\n\n"
    "Я помогаю с делами, языками, стилем, путешествиями и решениями на каждый день.\n\n"
    "Выбери направление или просто напиши вопрос 👇"
)
HOME_HINT = "💡 Не знаешь, с чего начать? Расскажи, что сейчас в голове - задача, идея, проблема или вопрос."

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def home_kb():
    # один столбец, одинаковая ширина
    return _kb([
        [("👕 Что надеть сегодня?", "as_wear")],
        [("📚 Объясни тему дня", "as_topic")],
        [("✍️ Помощь с письмом", "as_letter")],
        [("✈️ Куда съездить", "as_trip")],
        [("🗺️ Карта развития", "as_map")],
        [("⚡ Мотивируй меня", "as_motivate")],
        [("🌙 Проверка дня", "as_daycheck")],
        [("📊 Дневник", "as_diary")],
        [("🌿 Фраза дня", "as_phrase")],
        [("🩺 Врач", "as_doctor")],
        [("🔎 Поиск по записям", "as_search")],
    ])

def _screen(key):
    return (HOME_TEXT + "\n\n" + HOME_HINT, home_kb())

# Клавиатура под результатом: «Ещё раз» + «В меню» (один столбец)
def _result_kb():
    return _kb([
        [("🔄 Ещё раз (не нравится ответ)", "chat_retry")],
        [("⬅️ В меню", "as_home")],
    ])

# Клавиатура под подсказкой роли: только «Назад»
def _back_kb():
    return _kb([[("⬅️ В меню", "as_home")]])


async def _send(bot, cid, text, retry=True):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for c in chunks[:-1]:
        await bot.send_message(chat_id=cid, text=c)
    await bot.send_message(chat_id=cid, text=chunks[-1], reply_markup=_result_kb() if retry else None)


async def send_home(bot, cid):
    text, kb = _screen("as_home")
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)

send_welcome = send_home


def _gen_wear(cid):
    s = store.get_settings(cid)
    data = weather.fetch_weather(s["lat"], s["lon"], 2)
    wblock = weather.weather_block(data, 0, s["city"])
    of = wardrobe.build_outfit_focus(wblock, "сегодня")
    return "👕 Что надеть сегодня\n\n" + ", ".join(of.get("outfit", [])) + "\n\n" + of.get("why", "")

def _gen_topic(cid):
    nl = store.get_level(cid, "нидерландский")
    return ai.llm(
        f"Объясни одну тему дня по нидерландскому языку (уровень {nl}), каждый раз новую. "
        f"Коротко для СДВГ: правило в 1-2 строки, 1 пример с переводом, как применить. Эмодзи уместны. Без воды.", 600, 0.8)

def _gen_trip(cid):
    s = store.get_settings(cid)
    return ai.llm(
        f"Куда съездить на выходные из города {s['city']}? 4 варианта: место - чем добраться - почему. "
        f"Очень коротко, маркеры, эмодзи. Без воды.", 700, 0.8)

def _gen_map(cid):
    return ai.llm(
        f"«Карта развития» для Дмитрия: дизайнер UI/UX, фотограф, в Нидерландах, учит нидерландский и английский, СДВГ. "
        f"Кратко, формат:\n🗺️ Карта развития\n🎯 Цель\n📍 Сейчас\n🪜 3 шага на месяц\n💪 Сильные стороны\n⚠️ Ловушка СДВГ\n"
        f"По 1 строке на пункт. Опирайся по духу: {config.LAGOM}", 700, 0.8)

def _gen_motivate(cid):
    return ai.llm(
        f"Мотивируй Дмитрия коротко (2-3 строки), не банально, с эмодзи, опираясь на установки: {config.LAGOM}\nБез пафоса.", 300, 0.95)

_ONESHOT = {
    "as_wear": _gen_wear, "as_topic": _gen_topic, "as_trip": _gen_trip,
    "as_map": _gen_map, "as_motivate": _gen_motivate,
}

LETTER_REF = (
    "✍️ Помощь с письмами\n\n"
    "Оформлю официальные письма, деловые сообщения и перепишу сырой текст - понятно и без ошибок.\n\n"
    "Как обращаться:\n"
    "• «Напиши официальное письмо в [орган] о [проблема]»\n"
    "• «Сделай деловое сообщение [кому] с просьбой [что]»\n"
    "• «Перепиши вежливо и чётко: [текст]»\n"
    "• «Составь жалобу на [что], факты: [...]»\n"
    "• «Мягкий отказ на [предложение], причина: [...]»\n\n"
    "Опиши задачу следующим сообщением - напишу. Данные потом подставишь под себя."
)

def _role_system(role):
    if role == "letter":
        return ("Ты помощник по деловой переписке. Пиши официальные/деловые письма и переписывай сырой текст: "
                "вежливо, чётко, структурно, без ошибок. Давай готовый текст с [плейсхолдерами] для замены. "
                "Без воды. Если не хватает данных - укажи, что подставить.")
    if role == "designer":
        return ("Ты дизайн-ассистент Дмитрия (UI/UX и график). Помогай с типографикой, сетками, композицией, айдентикой. "
                f"Любимые шрифты, предлагай из них где уместно: {FONTS}. "
                "Конкретно, короткие блоки. Если не уверен - скажи честно.")
    if role == "doctor":
        return ("Ты помощник по общим вопросам здоровья. Даёшь ОБЩУЮ справочную информацию, не ставишь диагноз и не назначаешь лечение. "
                "Мягко напоминай, что это не заменяет врача, при тревожных симптомах советуй обратиться к специалисту. Коротко, без паники.")
    return "Ты полезный ассистент."

ROLE_INTRO = {
    "designer": ("🎨 Дизайнер\n\nПомогу с типографикой, сетками, композицией, айдентикой. "
                 f"Знаю твои шрифты: {FONTS}.\n\nОпиши задачу следующим сообщением."),
    "doctor": ("🩺 Врач\n\nДам общую справочную информацию о здоровье. Это не диагноз и не замена врача - "
               "при тревожных симптомах обратись к специалисту.\n(Полная интеграция с Vera Health требует API-ключа.)\n\n"
               "Опиши, что беспокоит."),
}


async def handle_callback(bot, cid, q, data):
    if data == "as_home":
        store.pending_input.pop(str(cid), None)
        text, kb = _screen("as_home")
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
        return
    if data in _ONESHOT:
        await bot.send_message(chat_id=cid, text="Секунду...")
        try:
            out = _ONESHOT[data](cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e))
            return
        store.last_action[str(cid)] = ("oneshot", data)
        await _send(bot, cid, out)
        return
    if data == "as_daycheck":
        await myday.send_daycheck(bot, cid)
        return
    if data == "as_diary":
        await myday.send_diary(bot, cid)
        return
    if data == "as_phrase":
        await myday.send_phrase(bot, cid)
        return
    if data == "as_letter":
        store.pending_input[str(cid)] = "role_letter"
        await bot.send_message(chat_id=cid, text=LETTER_REF, reply_markup=_back_kb())
        return
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=ROLE_INTRO["doctor"], reply_markup=_back_kb())
        return
    if data == "as_search":
        store.pending_input[str(cid)] = "search"
        await bot.send_message(chat_id=cid,
            text="🔎 Поиск по твоим записям (дневник, списки, гардероб, недавний чат).\n\nЧто найти? Напиши запрос.",
            reply_markup=_back_kb())
        return


def _personal_docs(cid):
    cid = str(cid)
    docs = []
    for e in store.get_list(config.DIARY_KEY, cid):
        if isinstance(e, dict):
            docs.append(f"Дневник {e.get('date','')}: {e.get('text','')}")
    for key, label in ((config.FAVORITES_KEY, "Любимое"), (config.WATCHLIST_KEY, "Посмотреть"),
                       (config.READLIST_KEY, "Почитать"), (config.ARTISTS_KEY, "Артист")):
        for it in store.get_list(key, cid):
            docs.append(f"{label}: {it}")
    for c in store.get_list(config.FAVCOUNTRIES_KEY, cid):
        if isinstance(c, dict):
            docs.append(f"Любимая страна: {c.get('name','')}")
    try:
        w = store.load_wardrobe()
        for cat, items in w.items():
            for it in items:
                docs.append(f"Гардероб ({cat}): {it}")
    except Exception:
        pass
    for m in store.chat_history.get(cid, []):
        who = "Я" if m.get("role") == "user" else "Ассистент"
        docs.append(f"{who}: {m.get('content','')[:300]}")
    return docs


async def do_search(bot, cid, query):
    docs = _personal_docs(cid)
    if not docs:
        await bot.send_message(chat_id=cid, text="Пока нечего искать - записей нет.")
        return
    await bot.send_message(chat_id=cid, text="Ищу...")
    try:
        ranked = ze.rerank(query, docs, top_n=5)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка поиска: {e}")
        return
    if not ranked:
        await bot.send_message(chat_id=cid, text="Ничего похожего не нашёл.")
        return
    lines = ["🔎 Нашёл по теме:", ""]
    for txt, _ in ranked:
        lines.append(f"• {txt}")
    # короткий ответ строго по найденному (без выдумок)
    context_text = "\n".join(f"- {t}" for t, _ in ranked)
    try:
        ans = ai.llm(
            f"Вопрос: {query}\n\nМои записи (отвечай ТОЛЬКО по ним, ничего не выдумывай):\n{context_text}\n\n"
            f"Коротко ответь по записям. Если в них нет ответа - честно скажи.", 500, 0.3)
        if ans and ans.strip():
            lines += ["", "🧠 Кратко:", ans.strip()]
    except Exception:
        pass
    store.last_action[str(cid)] = ("search", query)
    await _send(bot, cid, "\n".join(lines))


async def handle_role(bot, cid, role, text):
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        out = ai.llm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e))
        return
    store.last_action[str(cid)] = ("role", role, text)
    await _send(bot, cid, out)


async def chat_reply(bot, cid, text):
    store.last_action[str(cid)] = None
    await bot.send_chat_action(chat_id=cid, action="typing")
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = ai.chat_chain(hist)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e))
        return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer)


async def retry(bot, cid):
    la = store.last_action.get(str(cid))
    if la and la[0] == "oneshot":
        await bot.send_message(chat_id=cid, text="Ещё вариант...")
        try:
            out = _ONESHOT[la[1]](cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e))
            return
        await _send(bot, cid, out)
        return
    if la and la[0] == "role":
        await handle_role(bot, cid, la[1], la[2])
        return
    if la and la[0] == "search":
        await do_search(bot, cid, la[1])
        return
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
        await bot.send_message(chat_id=cid, text=str(e))
        return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer)