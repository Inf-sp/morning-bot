from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import weather
import wardrobe

FONTS = ("Cuprum, Fira Sans, Lora, Montserrat, Neucha, Open Sans, Orbitron, "
         "Pacifico, Philosopher, PT Sans, PT Serif, Roboto, Rubik, Ubuntu, Loew")

HOME_TEXT = (
    "💬 Ассистент DM | Daily Manager\n\n"
    "Что делаем сегодня?\n\n"
    "Я помогаю с делами, языками, стилем, путешествиями и решениями на каждый день.\n\n"
    "Выбери направление или просто напиши вопрос 👇"
)
HOME_HINT = "💡 Не знаешь, с чего начать? Расскажи, что сейчас в голове - задача, идея, проблема или вопрос."

_RETRY_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Ещё раз (не нравится ответ)", callback_data="chat_retry")]])

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def _screen(key):
    if key == "as_home":
        return (HOME_TEXT + "\n\n" + HOME_HINT, _kb([
            [("👕 Стиль и гардероб", "as_style")],
            [("📚 Учёба и языки", "as_study")],
            [("✈️ Путешествия", "as_travel")],
            [("🍳 Еда и покупки", "as_food")],
            [("🧠 Разобраться в голове", "as_head")],
            [("🩺 Врач", "as_doctor"), ("🎨 Дизайнер", "as_designer")],
        ]))
    if key == "as_style":
        return ("👕 Стиль и гардероб", _kb([
            [("Что надеть сегодня?", "as_wear")],
            [("⬅️ Назад", "as_home")],
        ]))
    if key == "as_study":
        return ("📚 Учёба и языки", _kb([
            [("Объясни тему дня", "as_topic")],
            [("✍️ Помощь в написании письма", "as_letter")],
            [("⬅️ Назад", "as_home")],
        ]))
    if key == "as_travel":
        return ("✈️ Путешествия", _kb([
            [("Куда съездить", "as_trip")],
            [("⬅️ Назад", "as_home")],
        ]))
    if key == "as_food":
        return ("🍳 Еда и покупки", _kb([
            [("Составь меню на неделю", "as_menu")],
            [("⬅️ Назад", "as_home")],
        ]))
    if key == "as_head":
        return ("🧠 Разобраться в голове", _kb([
            [("🗺️ Карта развития", "as_map")],
            [("Мотивируй меня", "as_motivate")],
            [("⬅️ Назад", "as_home")],
        ]))
    return (HOME_TEXT, _screen("as_home")[1])


async def _send(bot, cid, text, retry=True):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for c in chunks[:-1]:
        await bot.send_message(chat_id=cid, text=c)
    await bot.send_message(chat_id=cid, text=chunks[-1], reply_markup=_RETRY_KB if retry else None)


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
    return ai.llm(
        "Объясни одну полезную тему дня (грамматика нидерландского/английского ИЛИ полезный навык). "
        "Просто, для СДВГ: суть, короткий пример, как применить. Короткие блоки, маркеры. Без воды.", 900, 0.8)

def _gen_trip(cid):
    s = store.get_settings(cid)
    return ai.llm(
        f"Куда съездить на выходные из города {s['city']} (Нидерланды/Европа)? "
        f"4-5 вариантов: место - чем добраться - почему стоит. Коротко, маркеры. Без воды.", 900, 0.8)

def _gen_menu(cid):
    return ai.llm(
        "Составь простое меню на неделю (завтрак/обед/ужин) для одного человека. "
        "Готовка на электрической плите. Коротко, по дням, маркеры. В конце - короткий список покупок.", 1200, 0.7)

def _gen_map(cid):
    return ai.llm(
        f"Сделай «Карту развития» для Дмитрия: дизайнер UI/UX и график, фотограф, в Нидерландах, "
        f"учит нидерландский и английский, у него СДВГ. Формат, без воды:\n\n"
        f"🗺️ Карта развития\n\n🎯 Цель (1 строка)\n\n📍 Сейчас (2-3 пункта)\n\n"
        f"🪜 Шаги на 3 месяца (3-4 пункта)\n\n💪 Сильные стороны\n\n⚠️ Ловушки (СДВГ)\n\n"
        f"Опирайся по духу на установки: {config.LAGOM}", 1200, 0.8)

def _gen_motivate(cid):
    return ai.llm(
        f"Мотивируй Дмитрия коротко и по-настоящему (не банально), 3-4 строки, опираясь на его установки: "
        f"{config.LAGOM}\nБез воды и пафоса.", 400, 0.95)

_ONESHOT = {
    "as_wear": _gen_wear, "as_topic": _gen_topic, "as_trip": _gen_trip,
    "as_menu": _gen_menu, "as_map": _gen_map, "as_motivate": _gen_motivate,
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
    if data in ("as_home", "as_style", "as_study", "as_travel", "as_food", "as_head"):
        text, kb = _screen(data)
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
            await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
            return
        store.last_action[str(cid)] = ("oneshot", data)
        await _send(bot, cid, out)
        return
    if data == "as_letter":
        store.pending_input[str(cid)] = "role_letter"
        await bot.send_message(chat_id=cid, text=LETTER_REF)
        return
    if data == "as_designer":
        store.pending_input[str(cid)] = "role_designer"
        await bot.send_message(chat_id=cid, text=ROLE_INTRO["designer"])
        return
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=ROLE_INTRO["doctor"])
        return


async def handle_role(bot, cid, role, text):
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        out = ai.llm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
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
        await bot.send_message(chat_id=cid, text=f"Ошибка чата: {e}")
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
            await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
            return
        await _send(bot, cid, out)
        return
    if la and la[0] == "role":
        await handle_role(bot, cid, la[1], la[2])
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
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer)