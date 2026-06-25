from datetime import datetime
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
import rerank
import util
from util import esc
import verify
import secure
import memory

TZ = config.TZ

DOCTOR_INTRO = (
    "👩🏻‍⚕️ Врач\n\n"
    "Дам общую справочную информацию о здоровье и лекарствах. Это не диагноз и не назначение - "
    "при тревожных симптомах обратись к специалисту.\n\n"
    "Опиши, что беспокоит, или спроси про лекарство 👇"
)

def _kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=c) for t, c in row] for row in rows])

# универсальная клавиатура под ответом: [Продолжить][Короче|Глубже][⭐][В меню]
def _ans_kb(cont_label="🔄 Продолжить", cont_cb="chat_retry", depth=True):
    rows = []
    if cont_label and cont_cb:
        rows.append([(cont_label, cont_cb)])
    if depth:
        rows.append([("✂️ Короче", "ans_short"), ("🔬 Глубже", "ans_deep")])
    rows.append([("⭐ В закладки", "as_fav")])
    rows.append([("⬅️ Назад", "m_close")])
    return _kb(rows)

def _recipe_kb():
    return _kb([
        [("🗒️ Полный рецепт", "as_food_full")],
        [("❤️ Сохранить рецепт", "as_recipe_save")],
        [("✨ Ещё рецепт", "as_food")],
        [("⭐ В закладки", "as_fav")],
        [("⬅️ Назад", "m_close")],
    ])

def _back_kb():
    return _kb([[("⬅️ Назад", "m_close")]])


async def _send(bot, cid, text, kb=None, surface="card"):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    text, _w = verify.grade_text(text, surface)   # health->дисклеймер, chat->≤1 эмодзи
    for w in _w:
        print(f"[verify] {surface}: {w}")
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Ассистент")
    store.last_surface[str(cid)] = surface       # для «Короче/Глубже»
    html = util.tg_html(text)
    chunks = [html[i:i+4000] for i in range(0, len(html), 4000)]
    for i, c in enumerate(chunks):
        markup = (kb if kb is not None else _ans_kb()) if i == len(chunks) - 1 else None
        try:
            await bot.send_message(chat_id=cid, text=c, parse_mode="HTML", reply_markup=markup)
        except Exception:
            # если HTML невалиден - отправляем как обычный текст, без падения
            await bot.send_message(chat_id=cid, text=c, reply_markup=markup)


# ---------- Кулинарный радар ----------
def _my_recipe_pref(cid):
    """Контекст из базы рецептов для промпта (первые 5 названий)."""
    if not cid:
        return ""
    saved = store.get_list(config.MY_RECIPES_KEY, str(cid))[:5]
    names = ", ".join(r.get("name", "") for r in saved if r.get("name"))
    return f"Пользователь любит готовить: {names}. Похожий стиль приветствуется.\n" if names else ""


def _gen_recipe(constraint, cid=None):
    pref = _my_recipe_pref(cid)
    return ai.llm_json(
        f"{pref}Предложи 1 рецепт ({constraint}), 1 человек, электрическая плита, духовка SAGE. Компактно.\n"
        "Оформление полей в Telegram HTML: подзаголовки тегом <b>...</b>, пункты с маркера «• ». "
        "НИКАКОГО markdown - запрещены *, **, #, `. Заголовки <b>Ингредиенты</b> и <b>Приготовление</b>, пункты с новой строки «• ».\n"
        'JSON: {"name":"название","time":"X мин","servings":"N порц.",'
        '"short":"3-4 коротких предложения как готовить","full":"полный рецепт в Telegram HTML: блок <b>Ингредиенты</b> со списком пунктов «• », затем <b>Приготовление</b> с пунктами «• »"}', 900, tier="cheap")

def _recipe_card(d):
    return (f"🥘 <b>{util.esc(d.get('name',''))}</b>\n\n"
            f"⏱️ {util.esc(d.get('time',''))} • 🍽️ {util.esc(d.get('servings',''))}\n\n"
            f"{d.get('short','')}")

async def send_recipe(bot, cid, constraint="обычное блюдо"):
    await bot.send_message(chat_id=cid, text="Подбираю...")
    try:
        d = _gen_recipe(constraint, cid=cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("recipe", constraint)
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Рецепт"
    store.last_answer[str(cid)] = card
    await util.send_html(bot, cid, card, reply_markup=_recipe_kb())

async def send_recipe_full(bot, cid):
    d = store.last_recipe.get(str(cid))
    if not d:
        await bot.send_message(chat_id=cid, text="Сначала выбери рецепт."); return
    txt = f"📖 <b>{util.esc(d.get('name',''))}</b>\n\n{d.get('full','')}"
    store.last_answer[str(cid)] = txt
    await util.send_html(bot, cid, txt, reply_markup=_recipe_kb())

def _gen_leftovers_recipe(ingredients):
    return ai.llm_json(
        f"Есть продукты: {secure.wrap_untrusted(ingredients, 'продукты')}. "
        "Предложи 1 простой рецепт только из них (+ базовые специи, максимум 1 доп продукт). 1 человек.\n"
        "Telegram HTML в полях: <b>теги</b> для заголовков, пункты с «• ». Без markdown.\n"
        'JSON: {"name":"название","time":"X мин","servings":"1 порц.",'
        '"short":"3-4 коротких предложения как готовить","full":"полный рецепт: <b>Ингредиенты</b> со списком «• », '
        'затем <b>Приготовление</b> с пунктами «• »"}', 900, tier="cheap")

async def send_leftovers(bot, cid, ingredients):
    await bot.send_message(chat_id=cid, text="Смотрю, что можно приготовить...")
    try:
        d = _gen_leftovers_recipe(ingredients)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_recipe[str(cid)] = d
    store.last_action[str(cid)] = ("leftovers", ingredients)
    card = _recipe_card(d)
    store.last_source[str(cid)] = "Питание · Остатки"
    store.last_answer[str(cid)] = card
    await util.send_html(bot, cid, card, reply_markup=_recipe_kb())


# ---------- Мой холодильник ----------
async def send_fridge(bot, cid):
    cid_s = str(cid)
    items = store.get_list(config.FRIDGE_KEY, cid_s)
    if items:
        lines = "\n".join(f"• {util.esc(it)}" for it in items)
        txt = f"🧊 <b>Мой холодильник</b>\n\n{lines}"
    else:
        txt = "🧊 <b>Мой холодильник</b>\n\nСписок пуст. Добавь продукты, которые обычно есть дома."
    rows = []
    for i, it in enumerate(items):
        rows.append([InlineKeyboardButton(f"❌ {it[:32]}", callback_data=f"as_fridge_del_{i}")])
    rows.append([InlineKeyboardButton("➕ Добавить продукты", callback_data="as_fridge_add")])
    if items:
        rows.append([InlineKeyboardButton("🍳 Что приготовить", callback_data="as_fridge_cook")])
        rows.append([InlineKeyboardButton("🗑 Очистить список", callback_data="as_fridge_clear")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_food")])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))


async def fridge_add_done(bot, cid, text):
    cid_s = str(cid)
    parts = re.split(r"[,\n;]+", text)
    items_new = [p.strip().lower() for p in parts if p.strip()]
    existing = {x.lower() for x in store.get_list(config.FRIDGE_KEY, cid_s)}
    added = []
    for it in items_new:
        if it and it not in existing:
            store.add_to_list(config.FRIDGE_KEY, cid_s, it)
            existing.add(it)
            added.append(it)
    if added:
        await bot.send_message(chat_id=cid, text=f"➕ Добавлено: {', '.join(added)}")
    else:
        await bot.send_message(chat_id=cid, text="Все эти продукты уже есть в списке.")
    await send_fridge(bot, cid)


async def fridge_del(bot, cid, idx):
    cid_s = str(cid)
    items = store.get_list(config.FRIDGE_KEY, cid_s)
    if idx < len(items):
        items.pop(idx)
        store.set_list(config.FRIDGE_KEY, cid_s, items)
    await send_fridge(bot, cid)


async def fridge_clear(bot, cid):
    store.set_list(config.FRIDGE_KEY, str(cid), [])
    await bot.send_message(chat_id=cid, text="🗑 Список продуктов очищен.")
    await send_fridge(bot, cid)


async def send_fridge_recipe(bot, cid):
    items = store.get_list(config.FRIDGE_KEY, str(cid))
    if not items:
        await bot.send_message(chat_id=cid, text="Холодильник пуст. Сначала добавь продукты."); return
    await send_leftovers(bot, cid, ", ".join(items))


# ---------- База рецептов ----------
async def save_my_recipe(bot, cid):
    cid_s = str(cid)
    d = store.last_recipe.get(cid_s)
    if not d or not d.get("name"):
        await bot.send_message(chat_id=cid, text="Нет рецепта для сохранения."); return
    saved = store.get_list(config.MY_RECIPES_KEY, cid_s)
    names_lower = [r.get("name", "").lower() for r in saved]
    if d["name"].lower() in names_lower:
        await bot.send_message(chat_id=cid, text=f"«{util.esc(d['name'])}» уже есть в твоих рецептах."); return
    store.add_to_list(config.MY_RECIPES_KEY, cid_s, d)
    await bot.send_message(chat_id=cid, text=f"❤️ «{util.esc(d['name'])}» сохранён в базе рецептов.")


async def send_my_recipes(bot, cid):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if not recipes:
        txt = ("📚 <b>Мои рецепты</b>\n\nПусто. Сохраняй рецепты кнопкой "
               "«❤️ Сохранить рецепт» под любым рецептом.")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="m_food")]])
    else:
        txt = "📚 <b>Мои рецепты</b> — {}\n\n".format(len(recipes))
        txt += "\n".join(f"• {util.esc(r.get('name', '?'))}" for r in recipes)
        rows = []
        for i, r in enumerate(recipes):
            name = r.get("name", f"Рецепт {i+1}")[:30]
            rows.append([InlineKeyboardButton(f"📖 {name}", callback_data=f"as_my_recipe_{i}")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_food")])
        kb = InlineKeyboardMarkup(rows)
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)


async def send_my_recipe_full(bot, cid, idx):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if idx >= len(recipes):
        await bot.send_message(chat_id=cid, text="Рецепт не найден."); return
    d = recipes[idx]
    store.last_recipe[cid_s] = d
    txt = f"📖 <b>{util.esc(d.get('name',''))}</b>\n\n{d.get('full','')}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить из базы", callback_data=f"as_my_recipe_del_{idx}")],
        [InlineKeyboardButton("⬅️ Назад к списку", callback_data="as_my_recipes")],
    ])
    await util.send_html(bot, cid, txt, reply_markup=kb)


async def my_recipe_del(bot, cid, idx):
    cid_s = str(cid)
    recipes = store.get_list(config.MY_RECIPES_KEY, cid_s)
    if idx < len(recipes):
        name = recipes[idx].get("name", "рецепт")
        recipes.pop(idx)
        store.set_list(config.MY_RECIPES_KEY, cid_s, recipes)
        await bot.send_message(chat_id=cid, text=f"🗑 «{util.esc(name)}» удалён из базы рецептов.")
    await send_my_recipes(bot, cid)


# ---------- СДВГ / Следующий шаг ----------
def _gen_motiv(cid):
    return ai.llm(
        "Человеку с СДВГ трудно начать. НЕ давай мотивацию и НЕ хвали - дай конкретный "
        "ФИЗИЧЕСКИЙ следующий шаг, который запускает тело, а не настроение "
        "(открой нужный файл, налей воды, поставь таймер на 7 минут, надень кроссовки, "
        "напиши первое плохое предложение, вынеси одну вещь со стола). "
        "Тёплый живой тон, как друг рядом. Без воды, канцелярита и клише («верь в себя», «ты сможешь»). "
        "ВАЖНО: пиши СТРОГО на русском - ни одного иностранного слова. "
        "Каждый раз бери РАЗНЫЕ шаги (не повторяйся). "
        "СТРОГО формат, без markdown, жирные заголовки:\n\n"
        "🎯 Следующий шаг\n\n"
        "{1 короткая фраза-разрешение начать с малого, не банальная}\n\n"
        "Прямо сейчас:\n"
        "• {ОДНО предельно мелкое физическое действие на 1 минуту, максимально конкретно}\n\n"
        "Потом:\n"
        "• {следующее такое же мелкое физическое действие, как продолжение}\n\n"
        "Если застрял:\n"
        "• {как снизить порог ещё сильнее - сделать шаг вдвое меньше}",
        500, 0.9, ai.LEARN_ORDER)


# ---------- роли ----------
def _role_system(role):
    if role == "state":
        return ("Ты спокойный помощник по состоянию, фокусу и мотивации ( психотерапевт). "
                "Выслушай, разложи ситуацию на 1-3 конкретных шага, поддержи коротко. Без воды, с эмодзи. "
        )
    if role == "doctor":
        return ("Ты помощник по здоровью. Дай разбор СТРОГО в формате, кратко, с эмодзи:\n"
                "👩🏻‍⚕️ Разбор симптомов\n\n📍 Основная жалоба:\n{коротко}\n\n🔎 На что похоже:\n{1-2 предложения}\n\n"
                "✅ Рекомендации:\n• пункт\n• пункт\n\n🚨 Срочно к врачу:\n{когда}\n\nИтог: {одно короткое предложение}\n\n"
                )
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
        "причинах/состояниях при таких симптомах; НЕ диагноз). JSON: {\"items\": [\"тезис\", ...]}", 900, tier="cheap")
    return [x for x in data.get("items", []) if isinstance(x, str) and x.strip()]

async def doctor_answer(bot, cid, symptoms):
    if secure.is_dangerous_med(symptoms):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health")
        return
    await bot.send_chat_action(chat_id=cid, action="typing")
    if _is_med_question(symptoms):
        prompt = f"{_med_system()}\n\nВопрос про лекарство: {symptoms}"
        try:
            out = ai.llm(prompt, 900, 0.4)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        store.last_source[str(cid)] = "Здоровье · Лекарство"
        store.last_action[str(cid)] = ("role", "doctor", symptoms)
        await _send(bot, cid, out, kb=_ans_kb(None, None, depth=False), surface="health")
        return
    passages = []
    try:
        cands = _doctor_candidates(symptoms)
        ranked = rerank.rerank(symptoms, cands, top_n=3)
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
        await verify.safe_error(bot, cid, e); return
    store.last_source[str(cid)] = "Здоровье · Врач"
    store.last_action[str(cid)] = ("role", "doctor", symptoms)
    await _send(bot, cid, out, kb=_ans_kb(None, None, depth=False), surface="health")

async def handle_role(bot, cid, role, text):
    if role == "doctor":
        await doctor_answer(bot, cid, text); return
    if secure.is_dangerous_med(text):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health"); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        out = ai.llm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_action[str(cid)] = ("role", role, text)
    cont = ("🔄 Ещё совет", "chat_retry") if role == "state" else ("🔄 Продолжить", "chat_retry")
    await _send(bot, cid, out, kb=_ans_kb(*cont), surface="chat" if role == "state" else "card")


# ---------- Дневник тревоги ----------
async def send_daycheck(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)   # фикс: ответ не уйдёт в Обратный перевод
    store.game_state.pop(cid, None)
    worries = store.get_list(config.WORRIES_KEY, cid)
    lines = ["😌 <b>Дневник тревоги</b>", "",
             "Сюда выгружай всё, что крутится в голове. Не анализируй - просто запиши.",
             "Каждую тревогу с новой строки. Вечером проверим, что было фактами, а что шумом.", ""]
    if worries:
        lines.append("<b>Тревоги за сегодня:</b>")
        for w in worries:
            lines.append(f"• {esc(w['text'])}")
        lines.append("")
        lines.append("Напиши новые мысли сообщением или разбери текущие 👇")
    else:
        lines.append("Пока пусто. Напиши тревоги одним сообщением.")
    store.pending_input[cid] = "worry"
    rows = [[InlineKeyboardButton("🧠 Разобрать тревоги", callback_data="as_worryreview")]] if worries else []
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_close")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_evening_review(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)
    store.game_state.pop(cid, None)
    worries = store.get_list(config.WORRIES_KEY, cid)
    if not worries:
        await bot.send_message(chat_id=cid, parse_mode="HTML",
            text="🥸 <b>Вечерний разбор</b>\n\nСегодня тревог не записано. Если что-то крутится - выгрузи сейчас, каждую с новой строки.")
        store.pending_input[cid] = "worry"
        return
    wlist = "\n".join(f"- {w['text']}" for w in worries)
    focus = ""
    try:
        analysis = ai.llm(
            "Ты спокойный психолог. Разбери тревоги человека с СДВГ по-доброму, на русском.\n"
            "Для КАЖДОЙ тревоги дай блок строго в формате (Telegram HTML, без markdown, без звёздочек *):\n"
            "📌 <b>{текст тревоги}</b>\n"
            "Факт: {что реально известно}\n"
            "Предположение: {что пока лишь догадка}\n\n"
            "В конце добавь блок:\n"
            "🧠 <b>Итог дня</b>\n{1-2 строки: где факты, а где шум и неопределённость}\n\n"
            "🌿 {тёплая короткая мысль на ночь}\n\n"
            "В самом конце с новой строки добавь ровно одну строку вида:\n"
            "FOCUS: <одно конкретное дело или намерение на завтра, коротко, без кавычек>\n\n"
            f"Тревоги:\n{wlist}", 800, 0.6)
        analysis = analysis.replace("**", "").replace("* ", "").strip()
        m = re.search(r"FOCUS:\s*(.+)", analysis)
        if m:
            focus = m.group(1).strip().strip('"«»')
            analysis = analysis[:m.start()].strip()
    except Exception:
        analysis = ""
    if focus:
        memory.set_focus(cid, focus)
    L = ["🥸 <b>Вечерний разбор</b>", "", "<b>Сегодня тебя беспокоили:</b>"]
    for w in worries:
        L.append(f"• {esc(w['text'])}")
    if analysis:
        L += ["", analysis]
    if focus:
        L += ["", f"🎯 <b>Фокус на завтра:</b> {esc(focus)}"]
    rows = [
        [InlineKeyboardButton("🧹 Очистить все тревоги", callback_data="worry_clearall")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_close")],
    ]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def worry_clear_all(bot, cid):
    cid = str(cid)
    worries = store.get_list(config.WORRIES_KEY, cid)
    if worries:
        summary = f"Разобрано тревог: {len(worries)}"
        store.add_to_list(config.DIARY_KEY, cid, {"date": datetime.now(TZ).strftime("%d.%m"), "text": summary})
    store.set_list(config.WORRIES_KEY, cid, [])
    await bot.send_message(chat_id=cid, text="🧹 Дневник тревог очищен. Лёгкой ночи.")

async def save_worries(bot, cid, text):
    cid = str(cid)
    new = [{"text": w.strip(), "status": "pending"} for w in text.split("\n") if w.strip()]
    existing = store.get_list(config.WORRIES_KEY, cid)
    store.set_list(config.WORRIES_KEY, cid, existing + new)
    await bot.send_message(chat_id=cid, text=f"📝 Записал в дневник тревоги: +{len(new)}. Вечером проверим, что реально случилось.")


_ONESHOT = {
    "as_motiv": (_gen_motiv, "🔄 Ещё", "as_motiv"),
}


# ---------- роутер кнопок Баланса ----------
async def handle_callback(bot, cid, q, data):
    # Кулинарный радар
    if data == "as_food":
        await send_recipe(bot, cid, "обычное блюдо"); return
    if data == "as_food_full":
        await send_recipe_full(bot, cid); return
    if data == "as_food_left":
        store.pending_input[str(cid)] = "leftovers"
        await bot.send_message(chat_id=cid, text="🥕 Напиши продукты, что есть дома (через запятую) — предложу рецепт.",
                               reply_markup=_back_kb()); return
    # дневник тревоги
    if data == "as_daycheck":
        await send_daycheck(bot, cid); return
    if data == "as_worryreview":
        await send_evening_review(bot, cid); return
    # мотивация (одноразовая генерация)
    if data in _ONESHOT:
        gen, lbl, cb = _ONESHOT[data]
        await bot.send_message(chat_id=cid, text="Секунду...")
        try:
            out = gen(cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        store.last_action[str(cid)] = ("oneshot", data)
        store.last_source[str(cid)] = {"as_motiv": "Здоровье · Мотивация"}.get(data, "Ассистент")
        await _send(bot, cid, out, kb=_ans_kb(lbl, cb))
        return
    # врач
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=DOCTOR_INTRO, reply_markup=_back_kb()); return
    # холодильник
    if data == "as_fridge":
        await send_fridge(bot, cid); return
    if data == "as_fridge_add":
        store.pending_input[str(cid)] = "fridge_add"
        await bot.send_message(chat_id=cid,
            text="➕ Напиши продукты через запятую или с новой строки — добавлю в список.",
            reply_markup=_back_kb()); return
    if data == "as_fridge_cook":
        await send_fridge_recipe(bot, cid); return
    if data == "as_fridge_clear":
        await fridge_clear(bot, cid); return
    if data.startswith("as_fridge_del_"):
        try:
            await fridge_del(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return
    # база рецептов
    if data == "as_recipe_save":
        await save_my_recipe(bot, cid); return
    if data == "as_my_recipes":
        await send_my_recipes(bot, cid); return
    if data.startswith("as_my_recipe_del_"):
        try:
            await my_recipe_del(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return
    if data.startswith("as_my_recipe_"):
        try:
            await send_my_recipe_full(bot, cid, int(data.split("_")[-1]))
        except (ValueError, IndexError):
            pass
        return


# ---------- «Продолжить» / «Ещё раз» ----------
async def retry(bot, cid):
    la = store.last_action.get(str(cid))
    if la and la[0] == "oneshot":
        gen, lbl, cb = _ONESHOT[la[1]]
        await bot.send_message(chat_id=cid, text="Ещё вариант...")
        try:
            out = gen(cid)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
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
        await verify.safe_error(bot, cid, e); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer, surface="chat")


# ---------- «Короче / Глубже» (переписать последний ответ) ----------
async def reword(bot, cid, mode):
    prev = (store.last_answer.get(str(cid)) or "").strip()
    if not prev:
        await bot.send_message(chat_id=cid, text="Нет ответа, который можно переписать."); return
    surface = store.last_surface.get(str(cid), "card")
    if mode == "short":
        how, tier = "короче и без воды, оставь только суть", "cheap"
    else:
        how, tier = "подробнее и глубже, добавь полезные детали и нюансы", "smart"
    await bot.send_chat_action(chat_id=cid, action="typing")
    prompt = (f"Перепиши этот ответ {how}. Сохрани смысл и тот же язык. "
              "Формат - Telegram HTML: подзаголовки <b>...</b>, пункты с «• », без markdown (без *, #, `).\n\n"
              f"Текст:\n{secure.wrap_untrusted(prev, 'предыдущий ответ')}")
    try:
        out = ai.llm(prompt, 1200, 0.6, tier=tier)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    await _send(bot, cid, out, surface=surface)
