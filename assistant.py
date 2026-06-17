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
        [("👨‍🍳 Кулинарный радар", "as_food")],
        [("✍️ Письма и тексты", "as_letter")],
        [("💡 Генератор бизнес-идей", "as_idea")],
        [("🩺 Вопрос врачу и состояние", "as_health")],
    ])

def _screen(key):
    if key == "as_home":
        return (HOME_TEXT, home_kb())
    if key == "as_health":
        return ("🩺 Вопрос врачу и состояние\n\nВыбери:", _kb([
            [("🩺 Вопрос врачу", "as_doctor")],
            [("⚡ Мотивация и состояние", "as_motivate")],
            [("⬅️ В меню", "as_home")],
        ]))
    return (HOME_TEXT, home_kb())


# ---------- клавиатуры результата (один столбец) ----------
def _result_kb():
    return _kb([
        [("🔄 Ещё раз", "chat_retry")],
        [("⭐ В избранное", "as_fav")],
        [("⬅️ В меню", "as_home")],
    ])

def _recipe_kb():
    return _kb([
        [("📖 Полный рецепт", "as_food_full")],
        [("🔄 Ещё рецепт", "as_food")],
        [("⚡ До 15 минут", "as_food_quick")],
        [("🧊 Использовать остатки", "as_food_left")],
        [("⭐ В избранное", "as_fav")],
        [("⬅️ В меню", "as_home")],
    ])

def _motivation_kb():
    return _kb([
        [("🧠 СДВГ-фокус", "as_adhd")],
        [("😌 Дневник тревоги", "as_daycheck")],
        [("🎯 Новый ориентир", "as_motivate")],
        [("⭐ В избранное", "as_fav")],
        [("⬅️ В меню", "as_home")],
    ])

def _back_kb():
    return _kb([[("⬅️ В меню", "as_home")]])


async def _send(bot, cid, text, kb=None):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    store.last_answer[str(cid)] = text
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for c in chunks[:-1]:
        await bot.send_message(chat_id=cid, text=c)
    await bot.send_message(chat_id=cid, text=chunks[-1], reply_markup=kb if kb is not None else _result_kb())


async def send_home(bot, cid):
    text, kb = _screen("as_home")
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)

send_welcome = send_home


# ---------- Кулинарный радар ----------
def _gen_recipe(constraint):
    return ai.llm_json(
        f"Предложи 1 рецепт ({constraint}), 1 человек, электрическая плита. Компактно.\n"
        'JSON: {"name":"название","time":"X мин","servings":"N порц.",'
        '"short":"2-3 коротких предложения как готовить","full":"полный рецепт: ингредиенты списком + шаги"}', 900)

def _recipe_card(d):
    return (f"🥘 {d.get('name','')}\n"
            f"⏱️ {d.get('time','')} • 🍽️ {d.get('servings','')}\n"
            f"{d.get('short','')}")

async def send_recipe(bot, cid, constraint="обычное блюдо", label="recipe"):
    await bot.send_message(chat_id=cid, text="Подбираю...")
    try:
        d = _gen_recipe(constraint)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", constraint)
    card = _recipe_card(d)
    store.last_answer[str(cid)] = card
    await bot.send_message(chat_id=cid, text=card, reply_markup=_recipe_kb())

async def send_recipe_full(bot, cid):
    d = store.last_recipe.get(str(cid))
    if not d:
        await bot.send_message(chat_id=cid, text="Сначала выбери рецепт."); return
    txt = f"📖 {d.get('name','')}\n\n{d.get('full','')}"
    store.last_answer[str(cid)] = txt
    await bot.send_message(chat_id=cid, text=txt, reply_markup=_recipe_kb())

async def send_leftovers(bot, cid, ingredients):
    await bot.send_message(chat_id=cid, text="Смотрю, что можно приготовить...")
    try:
        out = ai.llm(
            f"Есть продукты: {ingredients}. Предложи 3 простых рецепта только из них (+ базовые специи). "
            "Каждый: 🥘 Название • ⏱️ время, 1-2 строки как готовить. Компактно, эмодзи. Без воды.", 800, 0.9)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("leftovers", ingredients)
    await _send(bot, cid, out)


# ---------- одноразовые ----------
def _gen_idea(cid):
    return ai.llm("Сгенерируй 1 свежую бизнес-идею (дизайн, ИИ, путешествия и т.п.). "
                  "1-2 предложения, конкретно, с эмодзи. Без воды.", 300, 1.0)

def _gen_adhd(cid):
    return ai.llm("Дай 1 короткую технику фокуса при СДВГ прямо сейчас (2-3 строки, эмодзи). "
                  "Выполнимо за минуту. Без воды.", 300, 0.9)

_ONESHOT = {"as_idea": _gen_idea, "as_adhd": _gen_adhd}

# ---------- Мотивация / состояние (карта развития внутри) ----------
def _gen_orient(cid):
    return ai.llm(
        "Сделай блок-ориентир для Дмитрия (дизайнер UI/UX, фотограф, в Нидерландах, СДВГ). СТРОГО формат:\n\n"
        "⚡ Сегодняшний ориентир\n\n🎯 Главный фокус\n{1 строка}\n\n"
        "💪 Сильные стороны\n• пункт\n• пункт\n• пункт\n\n"
        "⚠️ Ловушки\n• пункт\n• пункт\n\n"
        "➡️ Следующий шаг\n{1 конкретное действие на 15 минут}\n\n"
        f"Кратко, под действие. Опирайся по духу: {config.LAGOM}", 600, 0.85)

async def send_orient(bot, cid):
    await bot.send_message(chat_id=cid, text="Секунду...")
    try:
        out = _gen_orient(cid)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("orient",)
    store.last_answer[str(cid)] = out
    await _send(bot, cid, out, kb=_motivation_kb())


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
                "сырой текст - вежливо, чётко, структурно. Готовый текст с [плейсхолдерами]. Без воды.")
    if role == "doctor":
        return ("Ты помощник по здоровью. Дай разбор СТРОГО в формате, кратко, с эмодзи:\n"
                "🩺 Разбор симптомов\n\n📍 Основная жалоба:\n{коротко}\n\n🔎 На что похоже:\n{1-2 предложения}\n\n"
                "✅ Рекомендации:\n• пункт\n• пункт\n\n🚨 Срочно к врачу:\n{когда}\n\nИтог: {1-2 предложения}\n\n"
                "Не ставь диагноз, это общая информация и не замена врача.")
    return "Ты полезный ассистент."

def _doctor_candidates(symptoms):
    data = ai.llm_json(
        f"Пользователь описал: {symptoms}\nДай 6 коротких справочных тезисов (общая информация о возможных "
        "причинах/состояниях при таких симптомах; НЕ диагноз). JSON: {\"items\": [\"тезис\", ...]}", 900)
    return [x for x in data.get("items", []) if isinstance(x, str) and x.strip()]

async def doctor_answer(bot, cid, symptoms):
    await bot.send_chat_action(chat_id=cid, action="typing")
    passages = []
    try:
        cands = _doctor_candidates(symptoms)
        ranked = ze.rerank(symptoms, cands, top_n=3)
        passages = [t for t, _ in ranked]
    except Exception:
        passages = []
    base = _role_system("doctor")
    if passages:
        ctx = "\n".join(f"- {p}" for p in passages)
        prompt = f"{base}\n\nНаиболее релевантные справочные тезисы (по симптомам):\n{ctx}\n\nСимптомы: {symptoms}"
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
        await doctor_answer(bot, cid, text); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        out = ai.llm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("role", role, text)
    await _send(bot, cid, out)


# ---------- избранное (бывшие заметки) ----------
def _shorten(text):
    try:
        return ai.llm("Сожми до 1-3 строк, сохрани суть и важное, без воды:\n\n" + text, 200, 0.3).strip() or text[:300]
    except Exception:
        return text[:300]

async def save_fav(bot, cid):
    txt = store.last_answer.get(str(cid))
    if not txt:
        await bot.send_message(chat_id=cid, text="Нечего сохранять."); return
    short = _shorten(txt)
    store.add_to_list(config.NOTES_KEY, cid, {"date": datetime.now(config.TZ).strftime("%d.%m"), "text": short})
    await bot.send_message(chat_id=cid, text="⭐ Сохранено в избранное.")

async def send_notes(bot, cid):
    notes = store.get_list(config.NOTES_KEY, cid)
    if not notes:
        await bot.send_message(chat_id=cid, text="⭐ Избранное пусто. Жми «⭐ В избранное» под ответами."); return
    rows = []
    lines = ["⭐ Избранное", ""]
    for i, n in enumerate(notes[-20:]):
        t = n.get("text", "") if isinstance(n, dict) else str(n)
        d = n.get("date", "") if isinstance(n, dict) else ""
        lines.append(f"{i+1}. {d} {t.strip()}")
        rows.append([InlineKeyboardButton(f"❌ {i+1}", callback_data=f"as_notedel_{i}")])
    await bot.send_message(chat_id=cid, text="\n\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))


# ---------- роутер инлайн-кнопок ассистента ----------
async def handle_callback(bot, cid, q, data):
    if data in ("as_home", "as_health"):
        if data == "as_home":
            store.pending_input.pop(str(cid), None)
        text, kb = _screen(data)
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
        return
    # Кулинарный радар
    if data == "as_food":
        await send_recipe(bot, cid, "обычное блюдо"); return
    if data == "as_food_quick":
        await send_recipe(bot, cid, "очень быстро, до 15 минут"); return
    if data == "as_food_full":
        await send_recipe_full(bot, cid); return
    if data == "as_food_left":
        store.pending_input[str(cid)] = "leftovers"
        await bot.send_message(chat_id=cid, text="🧊 Напиши продукты, что есть дома (через запятую) - предложу 3 рецепта.",
                               reply_markup=_back_kb())
        return
    # Состояние
    if data == "as_motivate":
        await send_orient(bot, cid); return
    if data == "as_daycheck":
        await myday.send_daycheck(bot, cid); return
    # Избранное
    if data == "as_fav":
        await save_fav(bot, cid); return
    if data == "as_notes":
        await send_notes(bot, cid); return
    if data.startswith("as_notedel_"):
        i = int(data.split("_")[-1])
        notes = store.get_list(config.NOTES_KEY, cid)
        if i < len(notes):
            notes.pop(i); store.set_list(config.NOTES_KEY, cid, notes)
        await send_notes(bot, cid); return
    # одноразовые
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
        await bot.send_message(chat_id=cid, text=LETTER_REF, reply_markup=_back_kb()); return
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=DOCTOR_INTRO, reply_markup=_back_kb()); return


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
        await _send(bot, cid, out); return
    if la and la[0] == "recipe":
        await send_recipe(bot, cid, la[1]); return
    if la and la[0] == "orient":
        await send_orient(bot, cid); return
    if la and la[0] == "leftovers":
        await send_leftovers(bot, cid, la[1]); return
    if la and la[0] == "role":
        await handle_role(bot, cid, la[1], la[2]); return
    hist = list(store.chat_history.get(str(cid), []))
    if not hist:
        await bot.send_message(chat_id=cid, text="Нет предыдущего запроса."); return
    if hist[-1]["role"] == "assistant":
        hist = hist[:-1]
    await bot.send_chat_action(chat_id=cid, action="typing")
    nudge = hist + [{"role": "user", "content": "Дай другой, более чёткий вариант ответа."}]
    try:
        answer = ai.chat_chain(nudge)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer)