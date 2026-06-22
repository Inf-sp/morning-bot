import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
from util import esc, send_long

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
LO = ai.LEARN_ORDER

def _is_b1plus(level):
    try:
        return LEVELS.index(level) >= LEVELS.index("B1")
    except Exception:
        return False

def _code(language):
    return "nl" if language == "нидерландский" else "en"

def _flag(language):
    return "🇳🇱" if language == "нидерландский" else "🇬🇧"

def _adj(language):
    return "Нидерландская" if language == "нидерландский" else "Английская"


# ================= ГРАММАТИКА =================
def grammar_data(language, level, topic=None, study_topics=None):
    in_lang = _is_b1plus(level) and language == "нидерландский"
    lang_rule = ("Объяснение - на русском простыми словами; пример и задание на нидерландском с переводом." if in_lang
                 else "Объяснение простым русским, пример на изучаемом языке с переводом.")
    book = ("Ориентируйся на программу учебника TaalCompleet. " if language == "нидерландский" else "")
    if topic:
        topic_rule = f'Тема СТРОГО: "{topic}". Дай НОВЫЙ пример и новое задание по этой же теме.'
    elif study_topics:
        topic_rule = ("Выбери тему из списка тем, которые пользователь хочет изучать: "
                      + "; ".join(study_topics[:10]) + ". Бери одну из них.")
    else:
        topic_rule = f"Выбери одну тему уровня {level}, каждый раз НОВУЮ."
    prompt = f"""Грамматическое задание по языку {language}, уровень {level}. {book}
{topic_rule} {lang_rule}
Покажи тему в настоящем и прошедшем времени рядом.
JSON (без переносов строк внутри значений):
{{
 "title": "название темы",
 "explain": "краткое объяснение простыми словами, 2-3 строки",
 "present": "пример в настоящем времени на {language}",
 "present_ru": "перевод",
 "past": "пример в прошедшем времени на {language} (или 'N.v.t.' если неприменимо)",
 "past_ru": "перевод или пусто",
 "task": "предложение по теме с одним пропуском ____ на {language}",
 "task_ru": "перевод задания на русский",
 "a": "вариант A",
 "b": "вариант B",
 "correct": "a или b",
 "rule": "правило-объяснение почему так, 1-2 строки"
}}"""
    return ai.llm_json(prompt, 1000, LO)

async def send_grammar(bot, cid, language, flag=None, topic=None):
    level = store.get_level(cid, language)
    study_topics = [t.get("text", "") if isinstance(t, dict) else str(t)
                    for t in get_topics(cid, language)]
    try:
        d = grammar_data(language, level, topic, study_topics if not topic else None)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.grammar_state[str(cid)] = {"correct": d.get("correct", "a"), "rule": d.get("rule", ""),
                                     "a": d.get("a", ""), "b": d.get("b", ""),
                                     "task_ru": d.get("task_ru", ""),
                                     "topic": d.get("title", ""), "lang": language}
    code = _code(language)
    # Сообщение 1: грамматика (объяснение)
    L = [f"📖 {_flag(language)} <b>{_adj(language)} грамматика</b>", ""]
    L.append(f"<b>Тема:</b> {esc(d.get('title',''))}")
    if d.get("explain"):
        L += ["", esc(d["explain"])]
    L += ["", "<b>Пример:</b>"]
    if d.get("present"):
        L.append(f"Настоящее время - {esc(d.get('present',''))} → {esc(d.get('present_ru',''))}")
    if d.get("past") and d.get("past") != "N.v.t.":
        L.append(f"Прошедшее время - {esc(d.get('past',''))} → {esc(d.get('past_ru',''))}")
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
    # Сообщение 2: задание
    await _send_grammar_task(bot, cid, d, code)

async def _send_grammar_task(bot, cid, d, code):
    L2 = ["✍🏻 <b>Задание</b>", "", esc(d.get("task", "")), "", "Выбери вариант 👇"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(d.get("a", "A"), callback_data="gram_a"),
         InlineKeyboardButton(d.get("b", "B"), callback_data="gram_b")],
        [InlineKeyboardButton("🔄 Ещё пример", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L2), parse_mode="HTML", reply_markup=kb)

async def again_grammar(bot, cid, language):
    """Ещё пример: новое задание на ТУ ЖЕ тему, без повтора объяснения грамматики."""
    st = store.grammar_state.get(str(cid)) or {}
    topic = st.get("topic")
    level = store.get_level(cid, language)
    try:
        d = grammar_data(language, level, topic)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.grammar_state[str(cid)] = {"correct": d.get("correct", "a"), "rule": d.get("rule", ""),
                                     "a": d.get("a", ""), "b": d.get("b", ""),
                                     "task_ru": d.get("task_ru", ""),
                                     "topic": d.get("title", topic), "lang": language}
    await _send_grammar_task(bot, cid, d, _code(language))

async def grammar_answer(bot, cid, chosen):
    st = store.grammar_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Задание устарело, запроси новое."); return
    code = _code(st.get("lang", "нидерландский"))
    if chosen == st["correct"]:
        L = ["✅ <b>Верно!</b>"]
        if st.get("task_ru"):
            L += ["", f"<b>Перевод:</b> {esc(st['task_ru'])}"]
        if st.get("rule"):
            L += ["", f"💡 <b>Правило:</b> {esc(st['rule'])}"]
    else:
        right = st["a"] if st["correct"] == "a" else st["b"]
        L = [f"❌ <b>Неверно.</b> Правильно: {esc(right)}"]
        if st.get("task_ru"):
            L += ["", f"<b>Перевод:</b> {esc(st['task_ru'])}"]
        if st.get("rule"):
            L += ["", f"💡 <b>Правило:</b> {esc(st['rule'])}"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Ещё пример", callback_data=f"again_gram_{code}")]])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)


# ================= ОБРАТНЫЙ ПЕРЕВОД =================
def generate_challenge(language, level):
    return ai.llm(f"Дай ОДНУ фразу на русском для перевода на {language}. Уровень {level}, бытовая/рабочая ситуация. "
                  f"Только русская фраза, без кавычек.", 200, 1.0, LO).strip()

def check_translation(language, ru, answer):
    return ai.llm_json(f"""Ученик переводит с русского на {language}.
Русская фраза: {ru}
Перевод ученика: {answer}
JSON: {{"ok": true/false, "error": "ошибка коротко по-русски или пусто",
 "correct": "правильный естественный вариант на {language}", "note": "короткое правило/слово по-русски или пусто"}}""", 800, LO)

async def do_translate(bot, cid, lang):
    store.pending_input.pop(str(cid), None)
    store.game_state.pop(str(cid), None)   # фикс: чтобы ответ не уходил в игру
    level = store.get_level(cid, lang)
    try:
        ru = generate_challenge(lang, level)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.challenge_state[str(cid)] = {"ru": ru, "lang": lang}
    await bot.send_message(chat_id=cid,
        text=f"📝 <b>{_flag(lang)} Обратный перевод</b>\n\nФраза: «{esc(ru)}»\n\nНапиши перевод на {lang} следующим сообщением.",
        parse_mode="HTML")

async def translate_answer(bot, cid, text):
    st = store.challenge_state.pop(str(cid), None)
    if not st:
        return False
    await bot.send_message(chat_id=cid, text="Проверяю...")
    try:
        r = check_translation(st["lang"], st["ru"], text)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return True
    L = [f"📝 <b>{_flag(st['lang'])} Обратный перевод</b>", "", f"Твой ответ: {esc(text)}", ""]
    if r.get("ok"):
        L.append("✅ Верно")
        if r.get("correct"):
            L += ["", f"💡 Естественнее: {esc(r['correct'])}"]
    else:
        if r.get("error"):
            L += [f"❌ Ошибка: {esc(r['error'])}"]
        if r.get("correct"):
            L += ["", f"✅ Лучше: {esc(r['correct'])}"]
    if r.get("note"):
        L += ["", f"💡 {esc(r['note'])}"]
    code = _code(st["lang"])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё пример", callback_data=f"again_tr_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
    return True


# ================= ГЛАГОЛ ДНЯ / ПОСЛОВИЦА =================
def _proverb_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё пример", callback_data=f"a_proverb_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])

async def send_proverb(bot, cid, language):
    adj = "нидерландском" if language == "нидерландский" else "английском"
    try:
        d = ai.llm_json(
            f"Дай пословицу/поговорку или живое разговорное выражение строго на {language} языке.\n"
            'JSON: {"original":"оригинал на ' + language + '","literal":"дословный перевод на русский",'
            '"analog":"русский аналог по смыслу (1-2 варианта)","meaning":"значение, когда так говорят, 1-2 строки"}',
            500, LO)
        L = [f"💬 <b>Пословица на {adj} языке</b>", ""]
        L.append(f"\"{esc(d.get('original',''))}\" → \"{esc(d.get('literal',''))}\"")
        if d.get("analog"):
            L += ["", f"<b>Русский аналог:</b> {esc(d['analog'])}"]
        if d.get("meaning"):
            L += ["", f"<b>Значение:</b> {esc(d['meaning'])}"]
        txt = "\n".join(L)
    except Exception:
        txt = f"💬 <b>Пословица на {adj} языке</b>\n\nНе удалось получить, попробуй ещё раз."
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=_proverb_kb(_code(language)))


# ================= СЛОВАРЬ (раздельно NL / EN) =================
def _normalize_word(raw, lang="nl"):
    if lang == "en":
        spec = '{"word":"английское слово/фраза","ru":"русский перевод"}'
        lng = "английского"
    else:
        spec = '{"word":"нидерландское слово/фраза с артиклем (de/het)","ru":"русский перевод"}'
        lng = "нидерландского"
    try:
        d = ai.llm_json(f"Выдели главное слово/фразу {lng} языка из текста и переведи на русский.\n"
                        f"Текст: {raw}\nJSON: {spec}", 300, LO)
        return {"lang": lang, "word": d.get("word", "")[:80], "ru": d.get("ru", "")}
    except Exception:
        return {"lang": lang, "word": str(raw)[:60], "ru": ""}

async def add_word_manual(bot, cid, text, lang="nl"):
    d = _normalize_word(text, lang)
    store.add_to_list(config.DICT_KEY, cid, d)
    flag = "🇬🇧" if lang == "en" else "🇳🇱"
    await bot.send_message(chat_id=cid, text=f"📖 Добавлено: {flag} {d.get('word','')} - {d.get('ru','')}")
    await send_dict(bot, cid)

def _w_field(w, *keys):
    for k in keys:
        if isinstance(w, dict) and w.get(k):
            return w[k]
    return ""

def _ensure_dict(cid):
    """Возвращает словарь; если пусто - подгружает дефолтные NL-слова из dict_nl.json."""
    words = store.get_list(config.DICT_KEY, cid)
    if words:
        return words
    try:
        import json
        with open("dict_nl.json", encoding="utf-8") as f:
            seed = json.load(f)
        if seed:
            store.set_list(config.DICT_KEY, cid, seed)
            return seed
    except Exception:
        pass
    return words

async def send_dict(bot, cid):
    words = _ensure_dict(cid)
    lines = ["🗂️ <b>Мой словарь</b>", ""]
    rows = [[InlineKeyboardButton("🇳🇱 Добавить нидерландское", callback_data="a_dictadd_nl")],
            [InlineKeyboardButton("🇬🇧 Добавить английское", callback_data="a_dictadd_en")]]
    if not words:
        lines.append("Пока пусто. Добавляй слова кнопками ниже.")
    nl_words = [(i, w) for i, w in enumerate(words) if (w.get("lang") if isinstance(w, dict) else "nl") != "en"]
    en_words = [(i, w) for i, w in enumerate(words) if isinstance(w, dict) and w.get("lang") == "en"]
    if nl_words:
        lines.append("🇳🇱 <b>Нидерландские</b>")
        for i, w in nl_words[-25:]:
            lines.append(f"• {esc(_w_field(w,'word','nl'))} - {esc(_w_field(w,'ru'))}")
            rows.append([InlineKeyboardButton(f"❌ 🇳🇱 {_w_field(w,'word','nl')[:18]}", callback_data=f"worddel_{i}")])
    if en_words:
        lines.append("")
        lines.append("🇬🇧 <b>Английские</b>")
        for i, w in en_words[-25:]:
            lines.append(f"• {esc(_w_field(w,'word','en'))} - {esc(_w_field(w,'ru'))}")
            rows.append([InlineKeyboardButton(f"❌ 🇬🇧 {_w_field(w,'word','en')[:18]}", callback_data=f"worddel_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_learn")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def del_word(bot, cid, i):
    words = store.get_list(config.DICT_KEY, cid)
    if i < len(words):
        words.pop(i)
        store.set_list(config.DICT_KEY, cid, words)
    await send_dict(bot, cid)

WEEK_TRACK = {
    0: ("Свежая кровь", "Загрузка",
        "Берём 5 новых слов и 2 фразы. Прочитай вслух, покрути в голове. Больше ничего."),
    1: ("Первый повтор", "Эффект генерации",
        "Повтори вчерашнее. Посмотри на русский - вспомни перевод. Придумай ОДНО смешное предложение."),
    2: ("День разгрузки", "Микро-доза",
        "Повтори только фразы за понедельник. Слова не трогай. Есть силы - добавь 2 новых слова."),
    3: ("Проверка боем", "Активное вспоминание",
        "Повторяем всё за Пн и Ср. Закрой перевод рукой, вспоминай. Ошибся - отметь крестиком."),
    4: ("Финал недели", "Зачистка хвостов",
        "Повтори только слова, где вчера были крестики. Короткий спринт."),
    5: ("Легальный отдых", "Полный оффлайн",
        "Никакой учёбы. Мозгу нужен чистый отдых для переноса в долговременную память."),
    6: ("Легальный отдых", "Полный оффлайн",
        "Никакой учёбы. Дай мозгу отдохнуть - это часть процесса."),
}

async def send_morning_word(bot, cid):
    """11:00 - Daily Words: метод дня недели + порция слов из словаря."""
    import random as _r
    from datetime import datetime
    wd = datetime.now(config.TZ).weekday()
    title, phase, method = WEEK_TRACK[wd]
    words = _ensure_dict(cid)
    L = ["📚 <b>Daily Words | Повторение</b>", "", f"<b>{title}</b> - {phase}", esc(method)]
    # выходные - отдых, слова не шлём
    if wd >= 5 or not words:
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
        return
    portion = _r.sample(words, min(5, len(words)))
    L += ["", "🗂 <b>Порция на сегодня:</b>"]
    rows = []
    for w in portion:
        word = _w_field(w, "word", "nl", "en")
        ru = _w_field(w, "ru")
        L.append(f"• {esc(word)} → {esc(ru)}")
        # индекс для удаления
        try:
            idx = words.index(w)
            rows.append([InlineKeyboardButton(f"❌ {word[:24]}", callback_data=f"worddel_{idx}")])
        except ValueError:
            pass
    L += ["", "💡 <b>Контекст:</b> применяй эти слова сразу, когда думаешь о рутине."]
    rows.append([InlineKeyboardButton("🗂️ Мой словарь", callback_data="a_dict")])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))


# ================= ИЗУЧАЕМЫЕ ТЕМЫ (раздельно NL / EN) =================
def _topics_key(language):
    return config.TOPICS_NL_KEY if language == "нидерландский" else config.TOPICS_EN_KEY

def get_topics(cid, language):
    return store.get_list(_topics_key(language), cid)

async def send_topics(bot, cid, language):
    code = _code(language)
    topics = get_topics(cid, language)
    flag = _flag(language)
    lines = [f"🤓 <b>{flag} Изучаемые темы</b>", ""]
    if topics:
        for t in topics:
            txt = t.get("text", "") if isinstance(t, dict) else str(t)
            lines.append(f"• {esc(txt)}")
    else:
        lines.append("Пока пусто. Добавь тему, которую хочешь разобрать.")
    rows = []
    for i, t in enumerate(topics[:30]):
        txt = (t.get("text", "") if isinstance(t, dict) else str(t))
        rows.append([InlineKeyboardButton(f"❌ {txt[:26]}", callback_data=f"topicdel_{code}_{i}")])
    rows.append([InlineKeyboardButton("✍🏻 Добавить тему", callback_data=f"a_topicadd_{code}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(rows))

async def add_topic(bot, cid, text, language):
    """Сохраняет тему и показывает грамматический разбор."""
    text = text.strip()
    store.add_to_list(_topics_key(language), cid, {"text": text})
    flag = _flag(language)
    # разбор темы
    try:
        breakdown = ai.llm(
            f"Пользователь учит {language}. Он добавил тему/фразу для изучения: \"{text}\".\n"
            "Дай короткий разбор простыми словами на русском (Telegram HTML, теги <b>):\n"
            "- если это фраза/конструкция: разбери по частям (что значит каждое слово), "
            "выдели грамматическое правило одним предложением.\n"
            "- если это грамматическая тема: объясни суть в 2-3 строки с мини-примером.\n"
            "Без markdown, компактно, по делу.", 500, 0.6, LO).strip()
    except Exception:
        breakdown = ""
    L = [f"Добавил в 🤓 Изучаемая тема {flag}", "", f"<b>Будем изучать:</b> {esc(text)}"]
    if breakdown:
        L += ["", breakdown]
    code = _code(language)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К темам", callback_data=f"a_topics_{code}")]])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)

async def del_topic(bot, cid, code, i):
    language = "нидерландский" if code == "nl" else "английский"
    topics = store.get_list(_topics_key(language), cid)
    if i < len(topics):
        topics.pop(i)
        store.set_list(_topics_key(language), cid, topics)
    await send_topics(bot, cid, language)


# ===== Воскресная рассылка: интервальные повторения словаря =====
async def send_vocab_cards(bot, cid):
    words = store.get_list(config.DICT_KEY, cid)
    if not words:
        return
    import random as _r
    pick = _r.sample(words, k=min(5, len(words)))
    lines = ["📚 <b>Повторение словаря</b>", "", "Вспомни перевод, потом проверь 👇", ""]
    for w in pick:
        flag = "🇬🇧" if (isinstance(w, dict) and w.get("lang") == "en") else "🇳🇱"
        word = _w_field(w, "word", "nl", "en")
        ru = _w_field(w, "ru")
        lines.append(f"{flag} <b>{esc(word)}</b> — <tg-spoiler>{esc(ru)}</tg-spoiler>")
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML")


# ================= ИГРА-ДЕТЕКТИВ =================
GAME_UI = {
    "русский": {"diff_q": "Выбери сложность:", "easy": "Лёгкая", "med": "Средняя", "hard": "Тяжёлая",
                "title": "🕵️ Игра-детектив", "who": "Кто это?", "hint": "💡 Подсказка", "reveal": "😞 Сдаюсь", "suspect": "Подозреваемый:", "found": "✅ Дело раскрыто!", "answer": "Ответ", "give": "Знаешь ответ? Напиши его или нажми «😞 Сдаюсь»",
                "again": "🕵️ Загадать ещё", "chdiff": "🎚 Сложность", "chlang": "🌐 Язык",
                "correct": "✅ Верно!", "wrong": "❌ Не то", "retry": "Ещё попытка - напиши ответ или возьми подсказку."},
    "английский": {"diff_q": "Choose difficulty:", "easy": "Easy", "med": "Medium", "hard": "Hard",
                "title": "🕵️ Detective Game", "who": "Who am I?", "hint": "💡 Hint", "reveal": "😞 Give up", "suspect": "Suspect:", "found": "✅ Case solved!", "answer": "Answer", "give": "Know it? Type the name or tap «😞 Give up»",
                "again": "🕵️ New character", "chdiff": "🎚 Difficulty", "chlang": "🌐 Language",
                "correct": "✅ Correct!", "wrong": "❌ Not quite", "retry": "Try again - type a name or take a hint."},
    "нидерландский": {"diff_q": "Kies niveau:", "easy": "Makkelijk", "med": "Gemiddeld", "hard": "Moeilijk",
                "title": "🕵️ Detectivespel", "who": "Wie ben ik?", "hint": "💡 Hint", "reveal": "😞 Opgeven", "suspect": "Verdachte:", "found": "✅ Opgelost!", "answer": "Antwoord", "give": "Weet je het? Typ de naam of tik «😞 Opgeven»",
                "again": "🕵️ Nog een", "chdiff": "🎚 Niveau", "chlang": "🌐 Taal",
                "correct": "✅ Goed!", "wrong": "❌ Niet juist", "retry": "Nog een poging - typ een naam of neem een hint."},
}

def game_data(clue_lang, difficulty, recent):
    diff_map = {"easy": "ОЧЕНЬ известный и популярный персонаж, которого знают почти все "
                        "(герои Disney/Pixar/Marvel-уровня узнаваемости, мировые звёзды, классика кино и мультфильмов). "
                        "Подсказки простые и явные, угадывается легко",
                "med": "сложнее: исторические личности, актёры, более тонкие подсказки",
                "hard": "редкие персонажи или абстрактные понятия, специфичная лексика, хитрые подсказки"}
    avoid = ("Не загадывай: " + ", ".join(recent[-30:])) if recent else ""
    prompt = f"""Игра-детектив. Загадай персонажа/личность (кино, мультфильмы, наука, история, музыка, литература).
Сложность: {diff_map.get(difficulty, diff_map['med'])}. ВЕСЬ текст на языке: {clue_lang}. {avoid}
Ответь строго, каждое поле с новой строки, без markdown:
CLUES: 4 подсказки на языке {clue_lang}, через | , от непрямой к явной, без имени
ANSWER: имя на языке {clue_lang}
ALIASES: то же имя на русском, английском и нидерландском через |
HINT: ещё одна явная подсказка на языке {clue_lang}
QUOTE: короткая фраза в духе персонажа на языке {clue_lang}
EXPLAIN: 1-2 предложения почему это он (на языке {clue_lang})"""
    raw = ai.llm(prompt, 800, 1.0, LO)
    out = {}
    for key, field in (("CLUES", "clues"), ("ANSWER", "answer"), ("ALIASES", "aliases"),
                       ("HINT", "hint"), ("QUOTE", "quote"), ("EXPLAIN", "explain")):
        m = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z]+:|\Z)", raw, re.S)
        out[field] = m.group(1).strip() if m else ""
    out["clues"] = out.get("clues", "").replace(" | ", "\n").replace("|", "\n")
    out["aliases"] = [x.strip() for x in out.get("aliases", "").split("|") if x.strip()]
    return out

def game_lang_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="gamelang_ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="gamelang_en")],
        [InlineKeyboardButton("🇳🇱 Nederlands", callback_data="gamelang_nl")],
    ])

async def game_start(bot, cid):
    store.challenge_state.pop(str(cid), None)
    await bot.send_message(chat_id=cid, text="🕵️ Игра-детектив. На каком языке играем?", reply_markup=game_lang_kb())

async def ask_difficulty(bot, cid, lang):
    ui = GAME_UI.get(lang, GAME_UI["русский"])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["easy"], callback_data="gamediff_easy")],
        [InlineKeyboardButton(ui["med"], callback_data="gamediff_med")],
        [InlineKeyboardButton(ui["hard"], callback_data="gamediff_hard")],
    ])
    await bot.send_message(chat_id=cid, text=ui["diff_q"], reply_markup=kb)

async def send_game(bot, cid):
    store.challenge_state.pop(str(cid), None)   # фикс: чтобы перевод не перехватывал
    cfg = store.game_config.get(str(cid), {"lang": "русский", "difficulty": "med"})
    lang = cfg["lang"]
    ui = GAME_UI.get(lang, GAME_UI["русский"])
    recent = store.game_recent.get(str(cid), [])
    await bot.send_message(chat_id=cid, text="...")
    try:
        d = game_data(lang, cfg["difficulty"], recent)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.game_state[str(cid)] = {"answer": d.get("answer", ""), "aliases": d.get("aliases", []),
                                  "quote": d.get("quote", ""), "hint": d.get("hint", ""),
                                  "explain": d.get("explain", ""), "tries": 0}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
         InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")],
        [InlineKeyboardButton(ui["chdiff"], callback_data="game_change_diff"),
         InlineKeyboardButton(ui["chlang"], callback_data="game_change")],
        [InlineKeyboardButton("⬅️ Обучение", callback_data="m_learn")],
    ])
    clues = "\n".join(f"• {c.strip()}" for c in d.get("clues", "").split("\n") if c.strip())
    txt = f"<b>{ui['title']}</b>\n\n<b>{ui['suspect']}</b>\n{clues}\n\n{ui['who']} 🤔"
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)

def _fuzzy(a, b):
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if abs(len(a) - len(b)) <= 3:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        return diff <= 3
    return False

async def game_answer(bot, cid, text):
    st = store.game_state.get(str(cid))
    if not st:
        return False
    cfg = store.game_config.get(str(cid), {"lang": "русский"})
    ui = GAME_UI.get(cfg["lang"], GAME_UI["русский"])
    guess = text.lower().strip()
    names = [st["answer"]] + st.get("aliases", [])
    pool = []
    for n in names:
        n = (n or "").lower().strip()
        pool += [n] + n.split()
    correct = any(_fuzzy(guess, p) for p in pool if p)
    if correct:
        store.game_state.pop(str(cid), None)
        rec = store.game_recent.get(str(cid), []); rec.append(st["answer"]); store.game_recent[str(cid)] = rec[-30:]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(ui["again"], callback_data="game_again")],
            [InlineKeyboardButton("⬅️ Обучение", callback_data="m_learn")],
        ])
        body = st.get("explain") or st.get("quote", "")
        txt = f"✅ <b>Дело раскрыто!</b>\n\n{ui['answer']}: <b>{esc(st['answer'])}</b>"
        if body:
            txt += f"\n\n{esc(body)}"
        await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)
        return True
    st["tries"] = st.get("tries", 0) + 1
    if st["tries"] >= 2:
        store.game_state.pop(str(cid), None)
        rec = store.game_recent.get(str(cid), []); rec.append(st["answer"]); store.game_recent[str(cid)] = rec[-30:]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["again"], callback_data="game_again")]])
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}. {st['answer']}.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
                                    InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")]])
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}. {ui['retry']}", reply_markup=kb)
    return True


# ================= УРОВЕНЬ (/setup) =================
async def send_levels(bot, cid):
    nl_lvl, en_lvl = store.get_level(cid, "нидерландский"), store.get_level(cid, "английский")
    kb_nl = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_nl_{l}") for l in LEVELS]])
    kb_en = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_en_{l}") for l in LEVELS]])
    await bot.send_message(chat_id=cid, text=f"🇳🇱 Уровень нидерландского (сейчас {nl_lvl}):", reply_markup=kb_nl)
    await bot.send_message(chat_id=cid, text=f"🇬🇧 Уровень английского (сейчас {en_lvl}):", reply_markup=kb_en)