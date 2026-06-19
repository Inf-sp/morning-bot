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

STATE_TEXT = (
    "🎯 Мотивация и состояние\n\n"
    "Помогу разобраться с внутренним состоянием, фокусом и мотивацией. "
    "Это не психотерапия и не замена специалиста - если становится тяжело, лучше подключить врача или психолога.\n\n"
    "Опиши, что сейчас происходит 👇"
)

DOCTOR_INTRO = (
    "👩🏻‍⚕️ Врач\n\n"
    "Дам общую справочную информацию о здоровье и лекарствах. Это не диагноз и не назначение - "
    "при тревожных симптомах обратись к специалисту.\n\n"
    "Опиши, что беспокоит, или спроси про лекарство 👇"
)

LETTER_REF = (
    "✍️ Помощь с письмом\n\n"
    "Помогу написать, исправить или перевести текст.\n\n"
    "Отправь черновик или расскажи задачу 👇"
)


def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

def home_kb():
    return _kb([
        [("👨‍🍳 Кулинарный радар", "as_food")],
        [("✍️ Письма и тексты", "as_letter")],
        [("💡 Идеи и проекты", "as_idea")],
        [("🎯 Мотивация и состояние", "as_state")],
        [("👩🏻‍⚕️ Вопрос врачу", "as_doctor")],
    ])

def state_kb():
    return _kb([
        [("⚡ Подбодри меня", "as_cheer")],
        [("🧠 СДВГ-фокус", "as_adhd")],
        [("😌 Проверить состояние", "as_daycheck")],
        [("📈 Карта развития", "as_map")],
        [("⬅️ Назад", "m_close")],
    ])

# универсальная клавиатура под ответом: [Продолжить][⭐][В меню]
def _ans_kb(cont_label="🔄 Продолжить", cont_cb="chat_retry"):
    rows = []
    if cont_label and cont_cb:
        rows.append([(cont_label, cont_cb)])
    rows.append([("⭐ Добавить в избранное", "as_fav")])
    rows.append([("⬅️ Назад", "m_close")])
    return _kb(rows)

def _recipe_kb():
    return _kb([
        [("📖 Полный рецепт", "as_food_full")],
        [("🔄 Ещё рецепт", "as_food")],
        [("🥕 Не выбрасывать продукты", "as_food_left")],
        [("⭐ Добавить в избранное", "as_fav")],
        [("⬅️ Назад", "m_close")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "m_close")]])


async def _send(bot, cid, text, kb=None):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Ассистент")
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for c in chunks[:-1]:
        await bot.send_message(chat_id=cid, text=c)
    await bot.send_message(chat_id=cid, text=chunks[-1], reply_markup=kb if kb is not None else _ans_kb())


async def send_home(bot, cid):
    await bot.send_message(chat_id=cid, text=HOME_TEXT)

send_welcome = send_home


# ---------- Кулинарный радар ----------
def _gen_recipe(constraint):
    return ai.llm_json(
        f"Предложи 1 рецепт ({constraint}), 1 человек, электрическая плита. Компактно.\n"
        'JSON: {"name":"название","time":"X мин","servings":"N порц.",'
        '"short":"2-3 коротких предложения как готовить","full":"полный рецепт: ингредиенты списком + шаги по пунктам"}', 900)

def _recipe_card(d):
    return (f"🥘 {d.get('name','')}\n\n"
            f"⏱️ {d.get('time','')} • 🍽️ {d.get('servings','')}\n\n"
            f"{d.get('short','')}")

async def send_recipe(bot, cid, constraint="обычное блюдо"):
    await bot.send_message(chat_id=cid, text="Подбираю...")
    try:
        d = _gen_recipe(constraint)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", constraint)
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
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
            "Каждый: 🥘 Название • ⏱️ время, затем 1-2 строки как готовить, с переносами. Компактно, эмодзи. Без воды.", 800, 0.9)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("leftovers", ingredients)
    await _send(bot, cid, out, kb=_recipe_kb())


# ---------- Идеи / СДВГ / Подбодрить / Карта ----------
def _gen_idea(cid):
    return ai.llm("Сгенерируй 1 свежую идею/мини-проект (дизайн, ИИ, фото, AR, путешествия). "
                  "Придумай ей короткое название. СТРОГО формат, без markdown:\n"
                  "💡 Идея дня\n\n{название проекта}\n\n{1-2 предложения описания}\n\n"
                  "Польза: {через запятую}\nНачать: {через запятую}\n\nПервый результат: {что получится}",
                  400, 1.0, ai.LEARN_ORDER)

def _gen_adhd(cid):
    return ai.llm("Дай 1 короткую технику фокуса при СДВГ прямо сейчас (2-3 строки, эмодзи). "
                  "Выполнимо за минуту. Без воды.", 300, 0.9)

def _gen_cheer(cid):
    return ai.llm(f"Подбодри коротко (2-3 строки), тепло и не банально, с эмодзи. "
                  f"Опирайся по духу: {config.LAGOM}", 300, 0.95)

def _gen_map(cid):
    return ai.llm(
        "Сделай блок-ориентир (для дизайнера UI/UX и фотографа в Нидерландах, с СДВГ). СТРОГО формат:\n\n"
        "📈 Карта развития\n\n🎯 Главный фокус\n{1 строка}\n\n"
        "💪 Сильные стороны\n• пункт\n• пункт\n• пункт\n\n"
        "⚠️ Ловушки\n• пункт\n• пункт\n\n"
        "➡️ Следующий шаг\n{1 конкретное действие на 15 минут}\n\n"
        f"Кратко, под действие. Опирайся: {config.LAGOM}", 600, 0.85)


# ---------- роли ----------
def _role_system(role):
    if role == "letter":
        return ("Ты помощник по текстам и переписке. Пиши/исправляй/переводи: официальные письма, деловые сообщения, "
                "сырой текст - вежливо, чётко, структурно. Готовый текст с [плейсхолдерами]. Без воды.")
    if role == "state":
        return ("Ты спокойный помощник по состоянию, фокусу и мотивации (не психотерапевт). "
                "Выслушай, разложи ситуацию на 1-3 конкретных шага, поддержи коротко. Без воды, с эмодзи. "
                "Если звучит тяжело - мягко предложи специалиста.")
    if role == "doctor":
        return ("Ты помощник по здоровью. Дай разбор СТРОГО в формате, кратко, с эмодзи:\n"
                "👩🏻‍⚕️ Разбор симптомов\n\n📍 Основная жалоба:\n{коротко}\n\n🔎 На что похоже:\n{1-2 предложения}\n\n"
                "✅ Рекомендации:\n• пункт\n• пункт\n\n🚨 Срочно к врачу:\n{когда}\n\nИтог: {одно короткое предложение}\n\n"
                "Не ставь диагноз, это общая информация и не замена врача.")
    return "Ты полезный ассистент."

_MED_RE = ("лекарств", "таблет", "препарат", "доз", "мг ", " мг", "метилфенидат", "ибупрофен",
           "парацетамол", "антибиотик", "капл", "сироп", "мазь", "витамин", "пилюл", "concerta",
           "ritalin", "риталин", "медикамент", "побочк", "побочн", "как принимать")

def _is_med_question(text):
    t = (text or "").lower()
    return any(k in t for k in _MED_RE)

def _med_system():
    return ("Ты помощник по лекарствам. Дай СПРАВОЧНУЮ информацию о препарате СТРОГО в формате, кратко, с эмодзи:\n"
            "💊 {название и доза если есть}\n\n"
            "📍 Зачем:\n{коротко}\n\n"
            "⏱️ Когда работает:\n{через сколько и сколько держится}\n\n"
            "⚠️ Часто бывает:\n• побочка\n• побочка\n\n"
            "💡 Важно:\n• пункт\n• пункт\n\n"
            "🚨 К врачу если:\n• симптом\n• симптом\n\n"
            "Итог: {одно короткое предложение}\n\n"
            "Это общая справочная информация, не назначение. Дозы и схему определяет врач.")

def _doctor_candidates(symptoms):
    data = ai.llm_json(
        f"Пользователь описал: {symptoms}\nДай 6 коротких справочных тезисов (общая информация о возможных "
        "причинах/состояниях при таких симптомах; НЕ диагноз). JSON: {\"items\": [\"тезис\", ...]}", 900)
    return [x for x in data.get("items", []) if isinstance(x, str) and x.strip()]

async def doctor_answer(bot, cid, symptoms):
    await bot.send_chat_action(chat_id=cid, action="typing")
    if _is_med_question(symptoms):
        prompt = f"{_med_system()}\n\nВопрос про лекарство: {symptoms}"
        try:
            out = ai.llm(prompt, 900, 0.4)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        store.last_source[str(cid)] = "Здоровье · Лекарство"
        store.last_action[str(cid)] = ("role", "doctor", symptoms)
        await _send(bot, cid, out, kb=_ans_kb(None, None))
        return
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
        prompt = f"{base}\n\nНаиболее релевантные тезисы (по симптомам):\n{ctx}\n\nСимптомы: {symptoms}"
    else:
        prompt = f"{base}\n\nСимптомы: {symptoms}"
    try:
        out = ai.llm(prompt, 900, 0.5)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_source[str(cid)] = "Здоровье · Врач"
    store.last_action[str(cid)] = ("role", "doctor", symptoms)
    await _send(bot, cid, out, kb=_ans_kb(None, None))

async def handle_role(bot, cid, role, text):
    if role == "doctor":
        await doctor_answer(bot, cid, text); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        out = ai.llm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_action[str(cid)] = ("role", role, text)
    cont = ("🔄 Ещё совет", "chat_retry") if role == "state" else ("🔄 Продолжить", "chat_retry")
    await _send(bot, cid, out, kb=_ans_kb(*cont))


# ---------- избранное ----------
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
    source = store.last_source.get(str(cid), "Прочее")
    store.add_to_list(config.NOTES_KEY, cid, {"date": datetime.now(config.TZ).strftime("%d.%m"),
                                              "text": short, "source": source})
    await bot.send_message(chat_id=cid, text="⭐ Сохранено в избранное.")

def _top_cat(source):
    return (source or "Прочее").split(" · ")[0]

async def export_notes(bot, cid):
    import io
    notes = store.get_list(config.NOTES_KEY, cid)
    if not notes:
        await bot.send_message(chat_id=cid, text="Избранное пусто."); return
    by_cat = {}
    for n in notes:
        src = n.get("source", "Прочее") if isinstance(n, dict) else "Прочее"
        by_cat.setdefault(_top_cat(src), []).append(n)
    lines = ["Моё избранное (DM)", ""]
    for cat, items in by_cat.items():
        lines.append(f"== {cat} ==")
        for n in items:
            t = n.get("text", "") if isinstance(n, dict) else str(n)
            d = n.get("date", "") if isinstance(n, dict) else ""
            lines.append(f"- [{d}] {t.strip()}")
        lines.append("")
    buf = io.BytesIO("\n".join(lines).encode("utf-8"))
    buf.name = "izbrannoe.txt"
    await bot.send_document(chat_id=cid, document=buf, filename="izbrannoe.txt",
                            caption="📤 Готово. Текст можно вставить в Заметки/Напоминания Apple.")

async def send_notes(bot, cid):
    notes = store.get_list(config.NOTES_KEY, cid)
    if not notes:
        await bot.send_message(chat_id=cid, text="⭐ Избранное пусто. Жми «⭐ Добавить в избранное» под ответами."); return
    cats = []
    for n in notes:
        c = _top_cat(n.get("source", "Прочее") if isinstance(n, dict) else "Прочее")
        if c not in cats:
            cats.append(c)
    rows = [[InlineKeyboardButton(f"📂 {c} ({sum(1 for n in notes if _top_cat(n.get('source','Прочее') if isinstance(n,dict) else 'Прочее')==c)})",
                                  callback_data=f"as_notecat_{i}")] for i, c in enumerate(cats)]
    rows.append([InlineKeyboardButton("📤 Экспорт в файл", callback_data="as_export")])
    await bot.send_message(chat_id=cid, text="⭐ Избранное - выбери категорию:", reply_markup=InlineKeyboardMarkup(rows))

async def send_notes_cat(bot, cid, cat_index):
    notes = store.get_list(config.NOTES_KEY, cid)
    cats = []
    for n in notes:
        c = _top_cat(n.get("source", "Прочее") if isinstance(n, dict) else "Прочее")
        if c not in cats:
            cats.append(c)
    if cat_index >= len(cats):
        await send_notes(bot, cid); return
    cat = cats[cat_index]
    lines = [f"⭐ <b>{cat}</b>", ""]
    rows = []
    for i, n in enumerate(notes):
        src = n.get("source", "Прочее") if isinstance(n, dict) else "Прочее"
        if _top_cat(src) != cat:
            continue
        t = n.get("text", "") if isinstance(n, dict) else str(n)
        d = n.get("date", "") if isinstance(n, dict) else ""
        sub = (" · " + src.split(" · ", 1)[1]) if " · " in src else ""
        lines.append(f"• {d}{sub}: {t.strip()}")
        rows.append([InlineKeyboardButton(f"❌ {d} {t.strip()[:24]}", callback_data=f"as_notedel_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_notes")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


_ONESHOT = {
    "as_idea": (_gen_idea, "🔁 Новая идея", "as_idea"),
    "as_adhd": (_gen_adhd, "🔄 Ещё приём", "as_adhd"),
    "as_cheer": (_gen_cheer, "🔄 Ещё совет", "as_cheer"),
    "as_map": (_gen_map, "🔄 Обновить", "as_map"),
}


# ---------- роутер кнопок ассистента ----------
async def handle_callback(bot, cid, q, data):
    if data == "as_home":
        store.pending_input.pop(str(cid), None)
        try:
            await q.message.edit_text(HOME_TEXT, reply_markup=home_kb())
        except Exception:
            await bot.send_message(chat_id=cid, text=HOME_TEXT, reply_markup=home_kb())
        return
    if data == "as_state":
        store.pending_input[str(cid)] = "role_state"
        try:
            await q.message.edit_text(STATE_TEXT, reply_markup=state_kb())
        except Exception:
            await bot.send_message(chat_id=cid, text=STATE_TEXT, reply_markup=state_kb())
        return
    # Кулинарный радар
    if data == "as_food":
        await send_recipe(bot, cid, "обычное блюдо"); return
    if data == "as_food_full":
        await send_recipe_full(bot, cid); return
    if data == "as_food_left":
        store.pending_input[str(cid)] = "leftovers"
        await bot.send_message(chat_id=cid, text="🥕 Напиши продукты, что есть дома (через запятую) - предложу 3 рецепта.",
                               reply_markup=_back_kb()); return
    # состояние-кнопки
    if data == "as_daycheck":
        await myday.send_daycheck(bot, cid); return
    # избранное
    if data == "as_fav":
        await save_fav(bot, cid); return
    if data == "as_notes":
        await send_notes(bot, cid); return
    if data == "as_notecat_export" or data == "as_export":
        await export_notes(bot, cid); return
    if data.startswith("as_notecat_"):
        await send_notes_cat(bot, cid, int(data.split("_")[-1])); return
    if data.startswith("as_notedel_"):
        i = int(data.split("_")[-1])
        notes = store.get_list(config.NOTES_KEY, cid)
        if i < len(notes):
            notes.pop(i); store.set_list(config.NOTES_KEY, cid, notes)
        await send_notes(bot, cid); return
    # одноразовые
    if data in _ONESHOT:
        gen, lbl, cb = _ONESHOT[data]
        await bot.send_message(chat_id=cid, text="Секунду...")
        try:
            out = gen(cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        store.last_action[str(cid)] = ("oneshot", data)
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb))
        return
    # роли
    if data == "as_letter":
        store.pending_input[str(cid)] = "role_letter"
        kb = _kb([
            [("📄 Официальный ответ", "as_draft_official")],
            [("🎂 Поздравление с ДР", "as_draft_bday")],
            [("💬 Ответ на личное сообщение", "as_draft_dm")],
            [("⬅️ Назад", "m_close")],
        ])
        await bot.send_message(chat_id=cid, text=LETTER_REF, reply_markup=kb); return
    if data.startswith("as_draft_"):
        kind = data[len("as_draft_"):]
        presets = {
            "official": "Напиши официальный ответ. Уточни у меня детали, если нужно. Тон вежливый, формальный.",
            "bday": "Напиши тёплое поздравление с днём рождения. Спроси, кому, если нужно.",
            "dm": "Помоги ответить на личное сообщение - вежливо и по-человечески.",
        }
        await handle_role(bot, cid, "letter", presets.get(kind, "Помоги с текстом."))
        return
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=DOCTOR_INTRO, reply_markup=_back_kb()); return


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
        answer = ai.chat_chain(hist)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await bot.send_message(chat_id=cid, text=(answer or "").strip() or "Пусто, попробуй ещё раз.")
    store.last_answer[str(cid)] = answer
    if any(w in text.lower() for w in _MED_WORDS):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👩🏻‍⚕️ Вопрос врачу", callback_data="as_doctor")]])
        await bot.send_message(chat_id=cid,
            text="👩🏻‍⚕️ Похоже на вопрос о здоровье. В разделе 🧠 Баланс → «Вопрос врачу» дам подробный структурированный разбор.",
            reply_markup=kb)


# ---------- «Продолжить» / «Ещё раз» ----------
async def retry(bot, cid):
    la = store.last_action.get(str(cid))
    if la and la[0] == "oneshot":
        gen, lbl, cb = _ONESHOT[la[1]]
        await bot.send_message(chat_id=cid, text="Ещё вариант...")
        try:
            out = gen(cid)
        except Exception as e:
            await bot.send_message(chat_id=cid, text=str(e)); return
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb)); return
    if la and la[0] == "recipe":
        await send_recipe(bot, cid, la[1]); return
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
    nudge = hist + [{"role": "user", "content": "Продолжи мысль или дай более полезный вариант."}]
    try:
        answer = ai.chat_chain(nudge)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer)