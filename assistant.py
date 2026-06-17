from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import myday
import ze

HOME_TEXT = (
    "💬 Ассистент DM | Daily Manager\n\n"
    "Что делаем сегодня?\n\n"
    "Я помогаю с делами, языками, стилем, путешествиями и решениями на каждый день.\n\n"
    "Выбери направление или просто напиши вопрос 👇"
)

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def home_kb():
    return _kb([
        [("👨‍🍳 Еда", "as_food")],
        [("✍️ Письма и тексты", "as_letter")],
        [("💡 Генератор бизнес-идей", "as_idea")],
        [("🗺️ Карта развития", "as_map")],
        [("🧠 Состояние", "as_state")],
        [("🩺 Вопрос врачу", "as_doctor")],
        [("📝 Мои заметки", "as_notes")],
    ])

def _screen(key):
    if key == "as_home":
        return (HOME_TEXT, home_kb())
    if key == "as_food":
        return ("👨‍🍳 Еда\n\nЧто приготовить?", _kb([
            [("🍳 Завтрак", "as_food_b"), ("🥗 Обед", "as_food_l"), ("🍝 Ужин", "as_food_d")],
            [("⬅️ В меню", "as_home")],
        ]))
    if key == "as_map":
        return ("🗺️ Карта развития\n\nТвоя цель и следующие шаги.", _kb([
            [("🎯 Главная цель", "as_map_goal")],
            [("💪 Сильные стороны", "as_map_strengths")],
            [("⚠️ Ловушки", "as_map_traps")],
            [("📈 Следующий шаг", "as_map_next")],
            [("⬅️ В меню", "as_home")],
        ]))
    if key == "as_state":
        return ("🧠 Состояние\n\nДневник тревоги, мотивация и СДВГ-фокус.", _kb([
            [("🌙 Проверка дня", "as_daycheck"), ("📊 Дневник", "as_diary")],
            [("⚡ Мотивация", "as_motivate")],
            [("🧠 СДВГ-фокус", "as_adhd")],
            [("⬅️ В меню", "as_home")],
        ]))
    return (HOME_TEXT, home_kb())


def _result_kb():
    return _kb([
        [("🔄 Ещё раз", "chat_retry"), ("➕ Сохранить в заметки", "as_note_save")],
        [("⬅️ В меню", "as_home")],
    ])

def _back_kb():
    return _kb([[("⬅️ В меню", "as_home")]])


async def _send(bot, cid, text, retry=True):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    store.last_answer[str(cid)] = text
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for c in chunks[:-1]:
        await bot.send_message(chat_id=cid, text=c)
    await bot.send_message(chat_id=cid, text=chunks[-1], reply_markup=_result_kb() if retry else None)


async def send_home(bot, cid):
    text, kb = _screen("as_home")
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)

send_welcome = send_home


# ---------- генераторы (одноразовые) ----------
def _gen_idea(cid):
    return ai.llm("Сгенерируй 1 свежую бизнес-идею (можно на стыке дизайна, ИИ, путешествий). "
                  "1-2 предложения, конкретно, с эмодзи. Без воды.", 300, 1.0)

def _gen_food(meal):
    return ai.llm(f"Предложи что приготовить на {meal} (1 человек, электрическая плита 1-9). "
                  f"1 блюдо: название + 3-5 шагов кратко + время. Эмодзи уместны. Без воды.", 500, 0.9)

def _gen_map(part):
    base = ("Профиль: дизайнер UI/UX и фотограф в Нидерландах, учит нидерландский и английский, СДВГ. "
            "Кратко, по делу, для действий, не самоописание.")
    if part == "goal":
        return ai.llm(f"{base}\nНапиши блок:\n🎯 Главная цель\n1-2 предложения - куда движется.", 300, 0.8)
    if part == "strengths":
        return ai.llm(f"{base}\nНапиши блок:\n💪 Сильные стороны\n3-4 пункта маркерами.", 300, 0.8)
    if part == "traps":
        return ai.llm(f"{base}\nНапиши блок:\n⚠️ Ловушки (СДВГ)\n3-4 пункта маркерами, что мешает.", 300, 0.8)
    if part == "next":
        return ai.llm(f"{base}\nНапиши блок:\n📈 Следующий шаг\n1-3 конкретных действия на ближайшее время.", 300, 0.8)
    return ""

def _gen_motivate(cid):
    return ai.llm(f"Мотивируй Дмитрия коротко (3-4 строки), не банально, с эмодзи. Структура:\n"
                  f"⚡ Мотивация\nРазберёмся быстро - помогу превратить хаос в следующий шаг.\n"
                  f"Спроси, что стопорит, и предложи 1-3 действия. Опирайся по духу: {config.LAGOM}", 400, 0.95)

def _gen_adhd(cid):
    return ai.llm("Дай 1 короткую технику фокуса при СДВГ прямо сейчас (2-3 строки, с эмодзи). "
                  "Конкретно, выполнимо за минуту. Без воды.", 300, 0.9)

_ONESHOT = {
    "as_idea": lambda cid: _gen_idea(cid),
    "as_food_b": lambda cid: _gen_food("завтрак"),
    "as_food_l": lambda cid: _gen_food("обед"),
    "as_food_d": lambda cid: _gen_food("ужин"),
    "as_map_goal": lambda cid: _gen_map("goal"),
    "as_map_strengths": lambda cid: _gen_map("strengths"),
    "as_map_traps": lambda cid: _gen_map("traps"),
    "as_map_next": lambda cid: _gen_map("next"),
    "as_motivate": lambda cid: _gen_motivate(cid),
    "as_adhd": lambda cid: _gen_adhd(cid),
}


# ---------- роли ----------
LETTER_REF = (
    "✍️ Помощь с письмом\n\n"
    "Помогу написать, исправить или перевести текст.\n\n"
    "Отправь черновик или расскажи задачу 👇"
)
DOCTOR_INTRO = (
    "🩺 Врач\n\n"
    "Справочная информация о здоровье и симптомах.\n"
    "Не заменяет консультацию врача.\n\n"
    "Опиши, что беспокоит 👇"
)

def _role_system(role):
    if role == "letter":
        return ("Ты помощник по текстам и переписке. Пиши/исправляй/переводи: официальные письма, деловые сообщения, "
                "сырой текст - вежливо, чётко, структурно, без ошибок. Готовый текст с [плейсхолдерами] для замены. Без воды.")
    if role == "doctor":
        return ("Ты помощник по здоровью. Дай разбор СТРОГО в этом формате, кратко, с эмодзи:\n"
                "🩺 Разбор симптомов\n\n"
                "📍 Основная жалоба:\n{коротко}\n\n"
                "🔎 На что похоже:\n{1-2 предложения}\n\n"
                "✅ Рекомендации:\n• пункт\n• пункт\n\n"
                "🚨 Срочно к врачу:\n{когда обращаться}\n\n"
                "Итог: {1-2 предложения}\n\n"
                "Не ставь диагноз, это общая информация и не замена врача.")
    return "Ты полезный ассистент."


async def handle_callback(bot, cid, q, data):
    # навигация
    if data in ("as_home", "as_food", "as_map", "as_state"):
        if data == "as_home":
            store.pending_input.pop(str(cid), None)
        text, kb = _screen(data)
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
        return
    # состояние -> мотивация/дневник/проверка/сдвг
    if data == "as_daycheck":
        await myday.send_daycheck(bot, cid); return
    if data == "as_diary":
        await myday.send_diary(bot, cid); return
    # заметки
    if data == "as_notes":
        await send_notes(bot, cid); return
    if data == "as_note_save":
        txt = store.last_answer.get(str(cid))
        if not txt:
            await bot.send_message(chat_id=cid, text="Нечего сохранять.")
        else:
            store.add_to_list(config.NOTES_KEY, cid, {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": txt[:1500]})
            await bot.send_message(chat_id=cid, text="✅ Сохранено в заметки.")
        return
    if data.startswith("as_notedel_"):
        i = int(data.split("_")[-1])
        notes = store.get_list(config.NOTES_KEY, cid)
        if i < len(notes):
            notes.pop(i)
            store.set_list(config.NOTES_KEY, cid, notes)
        await send_notes(bot, cid)
        return
    # одноразовые действия
    if data in _ONESHOT:
        await bot.send_message(chat_id=cid, text="Секунду...")
        try:
            out = _ONESHOT[data](cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        store.last_action[str(cid)] = ("oneshot", data)
        await _send(bot, cid, out)
        return
    # роли
    if data == "as_letter":
        store.pending_input[str(cid)] = "role_letter"
        await bot.send_message(chat_id=cid, text=LETTER_REF, reply_markup=_back_kb())
        return
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=DOCTOR_INTRO, reply_markup=_back_kb())
        return


async def send_notes(bot, cid):
    notes = store.get_list(config.NOTES_KEY, cid)
    if not notes:
        await bot.send_message(chat_id=cid, text="📝 Заметки пусты. Жми «➕ Сохранить в заметки» под ответами.")
        return
    rows = []
    lines = ["📝 Мои заметки", ""]
    for i, n in enumerate(notes[-20:]):
        t = n.get("text", "") if isinstance(n, dict) else str(n)
        d = n.get("date", "") if isinstance(n, dict) else ""
        preview = t.replace("\n", " ")[:60]
        lines.append(f"{i+1}. {d} {preview}")
        rows.append([InlineKeyboardButton(f"❌ {i+1}", callback_data=f"as_notedel_{i}")])
    rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="as_home")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


def _doctor_candidates(symptoms):
    data = ai.llm_json(
        f"Пользователь описал: {symptoms}\n"
        "Дай 6 коротких справочных тезисов (общая информация о возможных причинах/состояниях при таких "
        "симптомах; НЕ диагноз). JSON: {\"items\": [\"тезис\", ...]}", 900)
    return [x for x in data.get("items", []) if isinstance(x, str) and x.strip()]

async def doctor_answer(bot, cid, symptoms):
    await bot.send_chat_action(chat_id=cid, action="typing")
    passages = []
    # ZeroEntropy: ранжируем сгенерированные тезисы по релевантности симптомам
    try:
        cands = _doctor_candidates(symptoms)
        ranked = ze.rerank(symptoms, cands, top_n=3)
        passages = [t for t, _ in ranked]
    except Exception:
        passages = []
    base = _role_system("doctor")
    if passages:
        ctx = "\n".join(f"- {p}" for p in passages)
        prompt = f"{base}\n\nНаиболее релевантные справочные тезисы (отобраны по симптомам):\n{ctx}\n\nСимптомы: {symptoms}"
    else:
        prompt = f"{base}\n\nСимптомы: {symptoms}"
    try:
        out = ai.llm(prompt, 900, 0.5)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("role", "doctor", symptoms)
    await _send(bot, cid, out)


async def handle_role(bot, cid, role, text):
    if role == "doctor":
        await doctor_answer(bot, cid, text)
        return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        out = ai.llm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("role", role, text)
    await _send(bot, cid, out)


# ---------- свободный чат ----------
async def chat_reply(bot, cid, text):
    store.last_action[str(cid)] = None
    await bot.send_chat_action(chat_id=cid, action="typing")
    hist = store.chat_history.get(str(cid), [])
    hist.append({"role": "user", "content": text})
    hist = hist[-10:]
    try:
        answer = ai.chat_chain(hist)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer)


# ---------- «Ещё раз» ----------
async def retry(bot, cid):
    la = store.last_action.get(str(cid))
    if la and la[0] == "oneshot":
        await bot.send_message(chat_id=cid, text="Ещё вариант...")
        try:
            out = _ONESHOT[la[1]](cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        await _send(bot, cid, out)
        return
    if la and la[0] == "role":
        await handle_role(bot, cid, la[1], la[2])
        return
    hist = list(store.chat_history.get(str(cid), []))
    if not hist:
        await bot.send_message(chat_id=cid, text="Нет предыдущего запроса."); return
    if hist[-1]["role"] == "assistant":
        hist = hist[:-1]
    await bot.send_chat_action(chat_id=cid, action="typing")
    nudge = hist + [{"role": "user", "content": "Дай другой, более чёткий и полезный вариант ответа."}]
    try:
        answer = ai.chat_chain(nudge)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer)