import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
from util import esc

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
    prompt = f"""Ты опытный преподаватель языка {language}, объясняешь грамматику взрослому ученику уровня {level}. {book}
{topic_rule} {lang_rule}
Цель: чтобы после прочтения ученик понял ОДНО правило и смог применить его сам.
Объясняй живо и по сути: дай чёткое правило, покажи тему в настоящем и прошедшем времени рядом, предупреди о типичной ошибке.
JSON (без переносов строк внутри значений):
{{
 "title": "название темы",
 "explain": "объяснение правила простыми словами, 2-4 коротких предложения - суть, когда применяется, как образуется",
 "present": "пример в настоящем времени на {language}",
 "present_ru": "перевод",
 "past": "пример в прошедшем времени на {language} (или 'N.v.t.' если неприменимо)",
 "past_ru": "перевод или пусто",
 "mistake": "типичная ошибка изучающих по этой теме и как правильно, 1 предложение",
 "task": "предложение по теме с одним пропуском ____ на {language}",
 "task_ru": "перевод задания на русский",
 "a": "вариант A",
 "b": "вариант B",
 "correct": "a или b",
 "rule": "почему именно этот вариант верный, 1-2 строки"
}}"""
    return ai.llm_json(prompt, 1400, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)

async def send_grammar(bot, cid, language, flag=None, topic=None, random=False):
    level = store.get_level(cid, language)
    study_topics = [t.get("text", "") if isinstance(t, dict) else str(t)
                    for t in get_topics(cid, language)]
    try:
        d = grammar_data(language, level, topic, None if random or topic else study_topics)
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
    if d.get("mistake"):
        L += ["", f"⚠️ <b>Частая ошибка:</b> {esc(d['mistake'])}"]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
    # Сообщение 2: задание
    await _send_grammar_task(bot, cid, d, code)

async def _send_grammar_task(bot, cid, d, code):
    L2 = ["✍🏻 <b>Задание</b>", "", esc(d.get("task", "")), "", "Выбери вариант 👇"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(d.get("a", "A"), callback_data="gram_a"),
         InlineKeyboardButton(d.get("b", "B"), callback_data="gram_b")],
        [InlineKeyboardButton("🔄 Ещё пример из этой темы", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("➡️ Следующая тема", callback_data=f"next_gram_{code}")],
        [InlineKeyboardButton("🎲 Случайная тема", callback_data=f"rand_gram_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L2), parse_mode="HTML", reply_markup=kb)

async def next_grammar(bot, cid, language):
    """Следующая тема: полностью новая грамматика с объяснением."""
    await send_grammar(bot, cid, language)

async def random_grammar(bot, cid, language):
    """Случайная тема: новая грамматика уровня, игнорируя список изучаемых тем."""
    await send_grammar(bot, cid, language, random=True)

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
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё пример из этой темы", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("➡️ Следующая тема", callback_data=f"next_gram_{code}")],
        [InlineKeyboardButton("🎲 Случайная тема", callback_data=f"rand_gram_{code}")],
    ])
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
    flag = _flag(language)
    try:
        d = ai.llm_json(
            f"Дай пословицу/поговорку или живое разговорное выражение строго на {language} языке.\n"
            'JSON: {"original":"оригинал на ' + language + '","literal":"дословный перевод на русский",'
            '"analog":["русский аналог 1","аналог 2","аналог 3"],"meaning":"значение, когда так говорят, 1-2 строки"}',
            500, LO)
        L = [f"💬{flag} <b>Пословица на {adj}</b>", ""]
        L.append(f"<b>{esc(d.get('original',''))}</b> → {esc(d.get('literal',''))}")
        analog = d.get("analog", "")
        if analog:
            if isinstance(analog, list):
                L += ["", "<b>Русский аналог:</b>"] + [esc(str(a)) for a in analog]
            else:
                L += ["", "<b>Русский аналог:</b>", esc(analog)]
        if d.get("meaning"):
            L += ["", "<b>Значение:</b>", esc(d["meaning"])]
        txt = "\n".join(L)
    except Exception:
        txt = f"💬{flag} <b>Пословица на {adj}</b>\n\nНе удалось получить, попробуй ещё раз."
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=_proverb_kb(_code(language)))


# ================= СЛОВАРЬ (раздельно NL / EN) =================
_BULLET_RE = re.compile(r"^[\s\-\*•·–—>»\d\.\)\(]+")
_TERM_SEP_RE = re.compile(r"\s+[-–—=:]\s+|\t+")

def _split_term(s):
    """Убирает маркеры списка и отделяет перевод, если он на той же строке (через - – — : =)."""
    s = _BULLET_RE.sub("", (s or "").strip()).strip()
    parts = _TERM_SEP_RE.split(s, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return s, ""

def _kind_of(term):
    """Слово или фраза: считаем по термину без ведущего артикля (de/het/een/the/a/an)."""
    t = re.sub(r"^(de|het|een|the|a|an)\s+", "", (term or "").strip().lower())
    return "word" if len(t.split()) <= 1 else "phrase"

def _parse_batch(text, lang_hint):
    """Разбирает присланный текст на отдельные слова/фразы с авто-определением языка и типа."""
    spec = ("Раздели текст на отдельные единицы (разделители: новые строки, запятые, точки с запятой, маркеры списка, нумерация). "
            "Строки часто в формате «термин - перевод» или «термин — перевод»: в word клади ТОЛЬКО иностранный термин, "
            "перевод - в ru. Для КАЖДОГО элемента определи: lang (nl - нидерландский или en - английский), "
            "kind (word - одно слово, в т.ч. существительное с артиклем de/het/the; phrase - выражение из нескольких слов), "
            "и перевод ru на русский. Нидерландские существительные - с артиклем de/het. "
            f"Если язык элемента неочевиден, ставь \"{lang_hint}\". "
            'Верни ТОЛЬКО JSON: {"items":[{"word":"иностранный термин без перевода","ru":"перевод","lang":"nl|en","kind":"word|phrase"}]}')
    d = ai.llm_json(f"{spec}\n\nТекст:\n{text}", 1500, LO)
    return d.get("items", []) if isinstance(d, dict) else []

async def add_words_batch(bot, cid, text, lang="nl"):
    """Добавляет много слов/фраз разом: каждое отдельной записью, авто-тип (слово/фраза) и язык."""
    try:
        items = _parse_batch(text, lang)
    except Exception:
        items = []
    if not items:
        # фолбэк: бьём по строкам/запятым, язык = текущий, без перевода
        raw = re.split(r"[\n;,]+", text)
        items = [{"word": x.strip(), "ru": "", "lang": lang} for x in raw if x.strip()]
    added = {"nl": {"word": 0, "phrase": 0}, "en": {"word": 0, "phrase": 0}}
    for it in items:
        # чистим маркеры списка и отделяем перевод, прилипший к слову
        term, extra_ru = _split_term(it.get("word") or "")
        if not term:
            continue
        ru = (it.get("ru") or "").strip() or extra_ru
        lng = "en" if it.get("lang") == "en" else "nl"
        knd = _kind_of(term)   # тип по самому термину (одно слово = слово)
        store.add_to_list(config.DICT_KEY, cid, {"lang": lng, "word": term[:80], "ru": ru, "kind": knd})
        added[lng][knd] += 1
    if not any(added[l][k] for l in added for k in added[l]):
        await bot.send_message(chat_id=cid, text="Не удалось распознать слова. Попробуй ещё раз."); return
    parts = []
    for lng, flag in (("nl", "🇳🇱"), ("en", "🇬🇧")):
        seg = []
        if added[lng]["word"]:
            seg.append(f"слов: {added[lng]['word']}")
        if added[lng]["phrase"]:
            seg.append(f"фраз: {added[lng]['phrase']}")
        if seg:
            parts.append(f"{flag} " + ", ".join(seg))
    await bot.send_message(chat_id=cid, text="📖 Добавлено - " + "; ".join(parts))
    await send_dict_lang(bot, cid, lang)

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

def _dict_kind(w):
    if isinstance(w, dict) and w.get("kind"):
        return w["kind"]
    word = w.get("word", "") if isinstance(w, dict) else str(w)
    return "phrase" if " " in word.strip() else "word"

def _dict_lang(w):
    return w.get("lang", "nl") if isinstance(w, dict) else "nl"

def _dict_counts(cid):
    words = _ensure_dict(cid)
    out = {"nl": {"word": 0, "phrase": 0}, "en": {"word": 0, "phrase": 0}}
    for w in words:
        lang = "en" if _dict_lang(w) == "en" else "nl"
        out[lang][_dict_kind(w)] += 1
    return out

async def send_dict(bot, cid):
    c = _dict_counts(cid)
    nl_total = c["nl"]["word"] + c["nl"]["phrase"]
    en_total = c["en"]["word"] + c["en"]["phrase"]
    txt = (f"🗂️ <b>Мой словарь</b>\n\nВсего: {nl_total + en_total} "
           f"(🇳🇱 {nl_total} · 🇬🇧 {en_total})\n\nВыбери язык 👇")
    rows = [
        [InlineKeyboardButton(f"🇳🇱 Нидерландский ({nl_total})", callback_data="a_dictlang_nl")],
        [InlineKeyboardButton(f"🇬🇧 Английский ({en_total})", callback_data="a_dictlang_en")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_learn")],
    ]
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def send_dict_lang(bot, cid, lang):
    c = _dict_counts(cid)[lang]
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    name = "Нидерландский" if lang == "nl" else "Английский"
    txt = (f"{flag} <b>Словарь · {name}</b>\n\n"
           f"Слов: {c['word']} · Фраз: {c['phrase']}\n\nВыбери действие 👇")
    rows = [
        [InlineKeyboardButton("➕ Добавить новое слово или фразу", callback_data=f"a_dictadd_{lang}")],
        [InlineKeyboardButton("✏️ Редактировать список слов", callback_data=f"a_dictedit_{lang}_word")],
        [InlineKeyboardButton("✏️ Редактировать список фраз", callback_data=f"a_dictedit_{lang}_phrase")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="a_dict")],
    ]
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def send_dict_edit(bot, cid, lang, kind):
    words = _ensure_dict(cid)
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    label = "слов" if kind == "word" else "фраз"
    items = [(i, w) for i, w in enumerate(words) if _dict_lang(w) == lang and _dict_kind(w) == kind]
    lines = [f"{flag} <b>Список {label}</b>", ""]
    rows = []
    if not items:
        lines.append("Пусто.")
    for i, w in items[-40:]:
        lines.append(f"• {esc(_w_field(w,'word','nl','en'))} - {esc(_w_field(w,'ru'))}")
        rows.append([InlineKeyboardButton(f"❌ {_w_field(w,'word','nl','en')[:22]}", callback_data=f"worddel_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def del_word(bot, cid, i):
    words = store.get_list(config.DICT_KEY, cid)
    if i < len(words):
        words.pop(i)
        store.set_list(config.DICT_KEY, cid, words)
    await send_dict(bot, cid)

WEEK_TRACK = {
    0: ("Свежая кровь", "Загрузка",
        "Прочитай вслух, покрути в голове. Больше ничего."),
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
    """11:00 - Daily Words: метод дня недели + порция (3 слова + 2 фразы) из словаря."""
    import random as _r
    from datetime import datetime
    import settings
    language = settings.study_lang(cid)          # язык утреннего слова из /setup
    lang_code = _code(language)                  # "nl" / "en"
    flag = _flag(language)
    lang_gen = "нидерландского" if language == "нидерландский" else "английского"
    wd = datetime.now(config.TZ).weekday()
    title, phase, method = WEEK_TRACK[wd]
    words = _ensure_dict(cid)                    # полный список - для индексов удаления
    pool = [w for w in words if _dict_lang(w) == lang_code]
    L = [f"📚{flag} <b>Daily Words | Повторение {lang_gen} языка</b>", "", esc(method)]
    # выходные - отдых, слова не шлём
    if wd >= 5 or not pool:
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
        return
    # 3 слова + 2 фразы (раздельно по типу из словаря)
    word_items = [w for w in pool if _dict_kind(w) == "word"]
    phrase_items = [w for w in pool if _dict_kind(w) == "phrase"]
    portion = (_r.sample(word_items, min(3, len(word_items)))
               + _r.sample(phrase_items, min(2, len(phrase_items))))
    if not portion:
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
        return
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
    L += ["", "💡 Применяй эти слова сразу, когда думаешь о рутине."]
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

def _split_topics(text):
    """Дробит сообщение на отдельные темы по строкам/«;», убирая маркеры списка.
    Тема может быть из нескольких слов, поэтому по пробелам/запятым НЕ режем."""
    items = []
    for line in re.split(r"[\n;]+", text or ""):
        t = _BULLET_RE.sub("", line.strip()).strip()
        if t:
            items.append(t)
    return items

async def _add_one_topic(bot, cid, text, language):
    """Сохраняет одну тему и показывает грамматический разбор."""
    store.add_to_list(_topics_key(language), cid, {"text": text})
    flag = _flag(language)
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

def _topics_overview(topics, language):
    """Одним запросом - краткая суть по каждой теме (тема -> суть)."""
    joined = "\n".join(f"- {t}" for t in topics)
    try:
        d = ai.llm_json(
            f"Пользователь учит {language}. Он добавил темы для изучения:\n{joined}\n"
            "Для КАЖДОЙ темы дай очень короткую суть на русском (1 строка - главное правило/смысл).\n"
            'Верни ТОЛЬКО JSON: {"items":[{"topic":"тема как есть","tip":"короткая суть"}]}',
            1000, LO)
        return {(i.get("topic") or "").strip(): (i.get("tip") or "").strip()
                for i in d.get("items", [])} if isinstance(d, dict) else {}
    except Exception:
        return {}

async def add_topic(bot, cid, text, language):
    """Добавляет тему(ы). Если в сообщении несколько - дробит и добавляет каждую отдельно."""
    topics = _split_topics(text)
    if not topics:
        return
    if len(topics) == 1:
        await _add_one_topic(bot, cid, topics[0], language)
        return
    for t in topics:
        store.add_to_list(_topics_key(language), cid, {"text": t})
    tips = _topics_overview(topics, language)
    flag, code = _flag(language), _code(language)
    L = [f"Добавил в 🤓 Изучаемые темы {flag}: {len(topics)}", ""]
    for t in topics:
        tip = tips.get(t, "")
        L.append(f"• <b>{esc(t)}</b>" + (f" — {esc(tip)}" if tip else ""))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К темам", callback_data=f"a_topics_{code}")]])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)

async def del_topic(bot, cid, code, i):
    language = "нидерландский" if code == "nl" else "английский"
    topics = store.get_list(_topics_key(language), cid)
    if i < len(topics):
        topics.pop(i)
        store.set_list(_topics_key(language), cid, topics)
    await send_topics(bot, cid, language)


# ================= ИГРА-ДЕТЕКТИВ =================
GAME_UI = {
    "русский": {"diff_q": "Выбери сложность:", "easy": "Лёгкая", "med": "Средняя", "hard": "Тяжёлая",
                "title": "🕵️ Игра-детектив", "who": "Кто это?", "hint": "💡 Подсказка", "reveal": "😞 Сдаюсь", "suspect": "Подозреваемый:", "found": "✅ Дело раскрыто!", "answer": "Ответ", "give": "Знаешь ответ? Напиши его или нажми «😞 Сдаюсь»",
                "again": "🕵️ Загадать ещё", "chdiff": "🎚 Сложность", "chlang": "🌐 Язык", "back": "⬅️ Обучение", "nohint": "Подсказок больше нет.",
                "correct": "✅ Верно!", "wrong": "❌ Не то", "retry": "Ещё попытка - напиши ответ или возьми подсказку."},
    "английский": {"diff_q": "Choose difficulty:", "easy": "Easy", "med": "Medium", "hard": "Hard",
                "title": "🕵️ Detective Game", "who": "Who am I?", "hint": "💡 Hint", "reveal": "😞 Give up", "suspect": "Suspect:", "found": "✅ Case solved!", "answer": "Answer", "give": "Know it? Type the name or tap «😞 Give up»",
                "again": "🕵️ New character", "chdiff": "🎚 Difficulty", "chlang": "🌐 Language", "back": "⬅️ Learning", "nohint": "No more hints.",
                "correct": "✅ Correct!", "wrong": "❌ Not quite", "retry": "Try again - type a name or take a hint."},
    "нидерландский": {"diff_q": "Kies niveau:", "easy": "Makkelijk", "med": "Gemiddeld", "hard": "Moeilijk",
                "title": "🕵️ Detectivespel", "who": "Wie ben ik?", "hint": "💡 Hint", "reveal": "😞 Opgeven", "suspect": "Verdachte:", "found": "✅ Opgelost!", "answer": "Antwoord", "give": "Weet je het? Typ de naam of tik «😞 Opgeven»",
                "again": "🕵️ Nog een", "chdiff": "🎚 Niveau", "chlang": "🌐 Taal", "back": "⬅️ Leren", "nohint": "Geen hints meer.",
                "correct": "✅ Goed!", "wrong": "❌ Niet juist", "retry": "Nog een poging - typ een naam of neem een hint."},
}

def _dot(s):
    """Гарантирует точку в конце предложения/подсказки."""
    s = (s or "").strip()
    if s and s[-1] not in ".!?…:":
        s += "."
    return s

def game_data(clue_lang, difficulty, recent):
    diff_map = {"easy": "ОЧЕНЬ известный и популярный персонаж, которого знают почти все "
                        "(герои Disney/Pixar/Marvel-уровня узнаваемости, мировые звёзды, классика кино и мультфильмов). "
                        "Подсказки простые и явные, угадывается легко",
                "med": "сложнее: исторические личности, актёры, более тонкие подсказки",
                "hard": "редкие персонажи или абстрактные понятия, специфичная лексика, хитрые подсказки"}
    avoid = ("Не загадывай: " + ", ".join(recent[-30:])) if recent else ""
    prompt = f"""Игра-детектив. Загадай персонажа/личность (кино, мультфильмы, наука, история, музыка, литература).
Сложность: {diff_map.get(difficulty, diff_map['med'])}. ВЕСЬ текст на языке: {clue_lang}. {avoid}
Каждая подсказка и каждое предложение заканчивается точкой.
Ответь строго, каждое поле с новой строки, без markdown:
CLUES: 4 подсказки на языке {clue_lang}, через | , от непрямой к явной, без имени
ANSWER: имя на языке {clue_lang}
ALIASES: то же имя на русском, английском и нидерландском через |
HINT: ещё одна явная подсказка на языке {clue_lang}
HINT2: совсем простая, почти очевидная подсказка про того же персонажа (но без имени), на языке {clue_lang}
QUOTE: короткая фраза в духе персонажа на языке {clue_lang}
EXPLAIN: 1-2 предложения почему это он (на языке {clue_lang})"""
    raw = ai.llm(prompt, 900, 1.0, LO)
    out = {}
    for key, field in (("CLUES", "clues"), ("ANSWER", "answer"), ("ALIASES", "aliases"),
                       ("HINT", "hint"), ("HINT2", "hint2"), ("QUOTE", "quote"), ("EXPLAIN", "explain")):
        m = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z]+\d*:|\Z)", raw, re.S)
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
    hints = [_dot(h) for h in [d.get("hint"), d.get("hint2")] if (h or "").strip()]
    store.game_state[str(cid)] = {"answer": d.get("answer", ""), "aliases": d.get("aliases", []),
                                  "quote": d.get("quote", ""), "hints": hints, "hint_i": 0,
                                  "explain": _dot(d.get("explain", "")), "tries": 0}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
         InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")],
        [InlineKeyboardButton(ui["chdiff"], callback_data="game_change_diff"),
         InlineKeyboardButton(ui["chlang"], callback_data="game_change")],
        [InlineKeyboardButton(ui["back"], callback_data="m_learn")],
    ])
    clues = "\n".join(f"• {_dot(c)}" for c in d.get("clues", "").split("\n") if c.strip())
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
            [InlineKeyboardButton(ui["back"], callback_data="m_learn")],
        ])
        body = st.get("explain") or st.get("quote", "")
        txt = f"<b>{ui['found']}</b>\n\n{ui['answer']}:\n<b>{esc(st['answer'])}</b>"
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