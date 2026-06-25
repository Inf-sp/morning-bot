import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store
import ai
from util import esc
import verify
import secure

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

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
def grammar_data(language, level, topic=None, study_topics=None, study_words=None):
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
    words_rule = ("Если уместно, построй пример и задание вокруг одного из слов пользователя: "
                  + ", ".join(study_words[:8]) + " - используй слово естественно. " if study_words else "")
    prompt = f"""Ты опытный преподаватель языка {language}, объясняешь грамматику взрослому ученику уровня {level}. {book}
{topic_rule} {words_rule}{lang_rule}
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

def _study_words(cid, language):
    """До 8 слов нужного языка из словаря - для примеров грамматики/тренажёра."""
    code = _code(language)
    words = [_cap(_w_field(w, "word", "nl", "en"))
             for w in _ensure_dict(cid)
             if _dict_lang(w) == code and _dict_kind(w) == "word"]
    return [w for w in words if w][:8]

async def send_grammar(bot, cid, language, flag=None, topic=None, random=False):
    level = store.get_level(cid, language)
    study_topics = [t.get("text", "") if isinstance(t, dict) else str(t)
                    for t in get_topics(cid, language)]
    study_words = _study_words(cid, language)
    try:
        d = grammar_data(language, level, topic, None if random or topic else study_topics, study_words)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
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
        d = grammar_data(language, level, topic, study_words=_study_words(cid, language))
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
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


# ================= ТРЕНАЖЁР СЛОВ =================
TRAIN_FORMATS = ["gap", "tf", "card"]

def _train_words(cid, language):
    """Слова (kind=word) нужного языка из словаря: [(word, ru), ...]."""
    code = _code(language)
    out = []
    for w in _ensure_dict(cid):
        if _dict_lang(w) == code and _dict_kind(w) == "word":
            term = _cap(_w_field(w, "word", "nl", "en"))
            if term:
                out.append((term, _w_field(w, "ru")))
    return out

def train_data(language, level, word, ru, fmt):
    """Задание тренажёра вокруг слова `word` (перевод `ru`) в формате fmt (gap/tf)."""
    base = (f"Ты преподаватель языка {language}, уровень ученика {level}. "
            f'Целевое слово: "{word}"' + (f" (перевод: {ru})" if ru else "") + ". ")
    if fmt == "gap":
        prompt = base + f"""Составь ОДНО предложение на {language} уровня {level} с этим словом, заменив его на ____.
Задание должно иметь РОВНО ОДИН верный ответ. Неверный вариант — это слово в неправильной форме (другое время/число/падеж), или слово другой части речи, или синоним с другим управлением, который грамматически НЕЛЬЗЯ вставить в данное предложение. Не выбирай синонимы, которые оба подходят в этом контексте.
JSON (без переносов строк внутри значений):
{{"sentence":"предложение с ____","a":"вариант A","b":"вариант B","correct":"a или b","ru":"перевод предложения на русский","rule":"почему правильный верен, а неверный — нет (1 строка)"}}"""
    else:  # tf
        prompt = base + f"""Составь ОДНО естественное предложение на {language} уровня {level}, где это слово - существительное, выделенное тегами <b></b>.
Затем дай утверждение на русском о значении/роли выделенного существительного - иногда ВЕРНОЕ, иногда ЛОЖНОЕ (выбирай случайно).
JSON (без переносов строк внутри значений):
{{"sentence":"предложение со словом в <b></b>","claim":"утверждение о выделенном слове на русском","correct":true или false,"explain":"короткое пояснение на русском, 1 строка","ru":"перевод предложения"}}"""
    return ai.llm_json(prompt, 700, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)

def _train_again_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Ещё задание", callback_data="train_next")],
        [InlineKeyboardButton("🗂️ Мой словарь", callback_data="a_dict")],
    ])

async def train_start(bot, cid, language):
    store.challenge_state.pop(str(cid), None)
    store.game_state.pop(str(cid), None)
    if not _train_words(cid, language):
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗂️ Мой словарь", callback_data="a_dict")]])
        await bot.send_message(chat_id=cid,
            text=f"{_flag(language)} В словаре пока нет слов для тренировки. Добавь слова в 🗂️ Словарь.",
            reply_markup=kb)
        return
    store.train_state[str(cid)] = {"lang": language, "fmt_i": 0}
    await _render_train(bot, cid)

async def _render_train(bot, cid):
    import random as _r
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    language = st["lang"]
    words = _train_words(cid, language)
    if not words:
        await bot.send_message(chat_id=cid, text="В словаре нет слов для тренировки."); return
    word, ru = _r.choice(words)
    fmt = TRAIN_FORMATS[st.get("fmt_i", 0) % len(TRAIN_FORMATS)]
    head = f"🧠 {_flag(language)} <b>Тренажёр слов</b>"
    if fmt == "card":
        st.update({"fmt": "card", "word": word, "ru": ru})
        L = [head, "", "Вспомни перевод:", "", f"<b>{esc(word)}</b>"]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👁 Показать перевод", callback_data="train_reveal")]])
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
        return
    try:
        d = train_data(language, store.get_level(cid, language), word, ru, fmt)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    if fmt == "gap":
        st.update({"fmt": "gap", "word": word, "ru": ru, "correct": d.get("correct", "a"),
                   "a": d.get("a", ""), "b": d.get("b", ""),
                   "task_ru": d.get("ru", ""), "rule": d.get("rule", "")})
        L = [head, "", "Вставь пропущенное слово:", "", esc(d.get("sentence", "")), "", "Выбери вариант 👇"]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(d.get("a", "A"), callback_data="train_a"),
                                    InlineKeyboardButton(d.get("b", "B"), callback_data="train_b")]])
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
        return
    # tf - в предложении нужно сохранить <b></b> от модели, но обезопасить остальной HTML
    st.update({"fmt": "tf", "word": word, "ru": ru, "correct": bool(d.get("correct")),
               "explain": d.get("explain", ""), "task_ru": d.get("ru", "")})
    sentence = esc(d.get("sentence", "")).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
    L = [head, "", sentence, "", f"❓ {esc(d.get('claim', ''))}", "", "Верно или неверно? 👇"]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Верно", callback_data="train_true"),
                                InlineKeyboardButton("❌ Неверно", callback_data="train_false")]])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)

async def train_reveal(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    L = [f"🧠 {_flag(st['lang'])} <b>{esc(st.get('word', ''))}</b>", "",
         f"Перевод: {esc(st.get('ru', '') or '—')}"]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=_train_again_kb())

async def train_answer(bot, cid, chosen):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    fmt = st.get("fmt")
    if fmt == "gap":
        right = st.get("a") if st.get("correct") == "a" else st.get("b")
        L = ["✅ <b>Верно!</b>"] if chosen == st.get("correct") else [f"❌ <b>Неверно.</b> Правильно: {esc(right)}"]
        if st.get("task_ru"):
            L += ["", f"<b>Перевод:</b> {esc(st['task_ru'])}"]
        if st.get("rule"):
            L += ["", f"💡 {esc(st['rule'])}"]
    elif fmt == "tf":
        L = ["✅ <b>Верно!</b>"] if chosen == st.get("correct") else ["❌ <b>Неверно.</b>"]
        if st.get("explain"):
            L += ["", f"💡 {esc(st['explain'])}"]
        if st.get("task_ru"):
            L += ["", f"<b>Перевод:</b> {esc(st['task_ru'])}"]
    else:
        await bot.send_message(chat_id=cid, text="Это задание без выбора ответа."); return
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=_train_again_kb())

async def train_next(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    st["fmt_i"] = (st.get("fmt_i", 0) + 1) % len(TRAIN_FORMATS)
    await _render_train(bot, cid)


# ================= ОБРАТНЫЙ ПЕРЕВОД =================
def generate_challenge(language, level):
    return ai.llm(f"Дай ОДНУ фразу на русском для перевода на {language}. Уровень {level}, бытовая/рабочая ситуация. "
                  f"Только русская фраза, без кавычек.", 200, 1.0, tier="cheap").strip()

def check_translation(language, ru, answer):
    return ai.llm_json(f"""Ученик переводит с русского на {language}.
Русская фраза: {ru}
Перевод ученика: {answer}
JSON: {{"ok": true/false, "error": "ошибка коротко по-русски или пусто",
 "correct": "правильный естественный вариант на {language}", "note": "короткое правило/слово по-русски или пусто"}}""", 800, tier="cheap")

async def do_translate(bot, cid, lang):
    store.pending_input.pop(str(cid), None)
    store.game_state.pop(str(cid), None)   # фикс: чтобы ответ не уходил в игру
    level = store.get_level(cid, lang)
    try:
        ru = generate_challenge(lang, level)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
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
        await verify.safe_error(bot, cid, e); return True
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
            500, tier="cheap")
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

def _cap(s):
    """Первая буква термина - заглавная (с учётом орфографии), остальное не трогаем."""
    s = (s or "").strip()
    return s[:1].upper() + s[1:] if s else s

def migrate_dict_caps():
    """Разовая миграция: приводит уже сохранённые слова словаря к виду с заглавной буквы."""
    data = store._load(config.DICT_KEY)
    changed = False
    for cid, words in (data or {}).items():
        if not isinstance(words, list):
            continue
        for w in words:
            if isinstance(w, dict) and w.get("word"):
                capped = _cap(w["word"])
                if capped != w["word"]:
                    w["word"] = capped
                    changed = True
    if changed:
        store._save(config.DICT_KEY, data)
    return changed

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
    d = ai.llm_json(f"{spec}\n\n{secure.wrap_untrusted(text, 'текст для разбора')}", 1500, tier="cheap")
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
        store.add_to_list(config.DICT_KEY, cid, {"lang": lng, "word": _cap(term)[:80], "ru": ru, "kind": knd})
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
        [InlineKeyboardButton("📝 Добавить новое слово или фразу", callback_data=f"a_dictadd_{lang}")],
        [InlineKeyboardButton("✏️ Редактировать список слов", callback_data=f"a_dictedit_{lang}_word")],
        [InlineKeyboardButton("✏️ Редактировать список фраз", callback_data=f"a_dictedit_{lang}_phrase")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="a_dict")],
    ]
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def send_dict_edit(bot, cid, lang, kind):
    """Редактирование списка = режим чистки (пагинация + мультивыбор)."""
    await open_cleanup(bot, cid, f"d_{lang}_{kind}")

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
        word = _cap(_w_field(w, "word", "nl", "en"))
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
    lines = [f"🤓 <b>{flag} Изучаемые темы</b>", f"Всего: {len(topics)}", ""]
    if topics:
        for i, t in enumerate(topics, 1):
            txt = t.get("text", "") if isinstance(t, dict) else str(t)
            lines.append(f"{i}. {esc(txt)}")
    else:
        lines.append("Пока пусто. Добавь тему, которую хочешь разобрать.")
    rows = [[InlineKeyboardButton("✍🏻 Добавить тему", callback_data=f"a_topicadd_{code}")]]
    if topics:
        rows.append([InlineKeyboardButton("🧹 Убрать выученные", callback_data=f"a_topicclean_{code}")])
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
            f"Пользователь учит {language}. Он добавил тему/фразу для изучения: "
            f"{secure.wrap_untrusted(text, 'тема')}\n"
            "Дай короткий разбор простыми словами на русском (Telegram HTML, теги <b>):\n"
            "- если это фраза/конструкция: разбери по частям (что значит каждое слово), "
            "выдели грамматическое правило одним предложением.\n"
            "- если это грамматическая тема: объясни суть в 2-3 строки с мини-примером.\n"
            "Без markdown, компактно, по делу.", 500, 0.6, tier="cheap").strip()
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
            1000, tier="cheap")
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


# ================= ЧИСТКА СПИСКОВ (пагинация + мультивыбор) =================
CLEAN_PAGE = 8

def _sel(cid, ctx):
    return store.list_sel.setdefault(f"{cid}:{ctx}", set())

def _list_label(it):
    """Подпись для элемента простого списка (строка или {name})."""
    return it.get("name", "") if isinstance(it, dict) else str(it)

def _ctx_items(cid, ctx):
    """(заголовок, items=[(global_id, label)], back_callback) для контекста чистки."""
    if ctx.startswith("d_"):
        _, lang, kind = ctx.split("_")
        flag = "🇳🇱" if lang == "nl" else "🇬🇧"
        label = "слов" if kind == "word" else "фраз"
        words = _ensure_dict(cid)
        items = []
        for i, w in enumerate(words):
            if _dict_lang(w) == lang and _dict_kind(w) == kind:
                term = _w_field(w, "word", "nl", "en")
                ru = _w_field(w, "ru")
                items.append((i, f"{term} — {ru}".strip(" —")))
        return f"{flag} Чистка: {label}", items, f"a_dictlang_{lang}"
    if ctx.startswith("t_"):
        _, lang = ctx.split("_")
        language = "нидерландский" if lang == "nl" else "английский"
        topics = get_topics(cid, language)
        items = [(i, (t.get("text", "") if isinstance(t, dict) else str(t))) for i, t in enumerate(topics)]
        return f"{_flag(language)} Чистка: темы", items, f"a_topics_{lang}"
    if ctx == "nb":                       # временные закладки (NOTES, bucket=fav)
        notes = store.get_list(config.NOTES_KEY, cid)
        items = [(i, (n.get("text", "") if isinstance(n, dict) else str(n)).strip())
                 for i, n in enumerate(notes)
                 if (n.get("bucket", "fav") if isinstance(n, dict) else "fav") == "fav"]
        return "⭐ Чистка: закладки", items, "as_bucket_fav"
    if ctx in ("wl", "rl"):               # watchlist / readlist
        key = config.WATCHLIST_KEY if ctx == "wl" else config.READLIST_KEY
        title = "🍿 Чистка: посмотреть" if ctx == "wl" else "📚 Чистка: почитать"
        back = "a_watchlist" if ctx == "wl" else "a_readlist"
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(key, cid))]
        return title, items, back
    if ctx == "kast":                     # шкаф (плоский список (cat,item))
        flat = _wardrobe_flat(cid)
        items = [(i, it) for i, (cat, it) in enumerate(flat)]
        return "🗄 Чистка: шкаф", items, "w_closet"
    if ctx.startswith("lv_"):             # любимые: страны/артисты/книги
        key = ctx[len("lv_"):]
        store_key = {"countries": config.COUNTRIES_KEY, "artists": config.ARTISTS_KEY,
                     "books": config.BOOKS_KEY}.get(key)
        title = {"countries": "🧳 Чистка: страны", "artists": "🎸 Чистка: артисты",
                 "books": "📖 Чистка: книги"}.get(key, "Чистка")
        items = [(i, _list_label(it)) for i, it in enumerate(store.get_list(store_key, cid))] if store_key else []
        return title, items, f"as_love_{key}"
    if ctx == "fridge":
        items = [(i, it) for i, it in enumerate(store.get_list(config.FRIDGE_KEY, cid))]
        return "🧊 Чистка: холодильник", items, "as_fridge"
    if ctx == "recipes":
        recipes = store.get_list(config.MY_RECIPES_KEY, cid)
        items = [(i, r.get("name", f"Рецепт {i+1}")) for i, r in enumerate(recipes)]
        return "🍳 Чистка: рецепты", items, "as_my_recipes"
    return "Чистка", [], "m_learn"

def _wardrobe_flat(cid):
    """Плоский стабильный список (категория, вещь) шкафа - для чистки/удаления."""
    flat = []
    for cat, items in store.load_wardrobe().items():
        if cat == "_v" or not isinstance(items, list):
            continue
        for it in items:
            flat.append((cat, it))
    return flat

async def send_cleanup(bot, cid, ctx, page=0, q=None):
    title, items, back = _ctx_items(cid, ctx)
    sel = _sel(cid, ctx)
    sel &= {i for i, _ in items}          # отбрасываем устаревшие индексы
    total = len(items)
    pages = max(1, (total + CLEAN_PAGE - 1) // CLEAN_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * CLEAN_PAGE:(page + 1) * CLEAN_PAGE]
    lines = [f"🧹 <b>{esc(title)}</b>", f"Всего: {total} · отмечено: {len(sel)}", "",
             "Отметь выученное ✅ и нажми «Удалить отмеченные»."]
    rows = []
    for idx, lbl in chunk:
        mark = "✅" if idx in sel else "▫️"
        rows.append([InlineKeyboardButton(f"{mark} {lbl[:36]}", callback_data=f"clt_{ctx}_{idx}_{page}")])
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"clp_{ctx}_{(page - 1) % pages}"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"clp_{ctx}_{(page + 1) % pages}"),
        ])
    rows.append([InlineKeyboardButton("☑️ Отметить всё на странице", callback_data=f"cla_{ctx}_{page}")])
    if sel:
        rows.append([InlineKeyboardButton(f"🗑 Удалить отмеченные ({len(sel)})", callback_data=f"cld_{ctx}_{page}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data=back)])
    kb = InlineKeyboardMarkup(rows)
    text = "\n".join(lines)
    if q is not None:
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb); return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)

def _cleanup_delete(cid, ctx):
    sel = _sel(cid, ctx)
    if not sel:
        return
    if ctx.startswith("d_"):
        words = [w for i, w in enumerate(_ensure_dict(cid)) if i not in sel]
        store.set_list(config.DICT_KEY, cid, words)
    elif ctx.startswith("t_"):
        _, lang = ctx.split("_")
        language = "нидерландский" if lang == "nl" else "английский"
        topics = [t for i, t in enumerate(get_topics(cid, language)) if i not in sel]
        store.set_list(_topics_key(language), cid, topics)
    elif ctx == "nb":
        notes = [n for i, n in enumerate(store.get_list(config.NOTES_KEY, cid)) if i not in sel]
        store.set_list(config.NOTES_KEY, cid, notes)
    elif ctx in ("wl", "rl"):
        key = config.WATCHLIST_KEY if ctx == "wl" else config.READLIST_KEY
        store.set_list(key, cid, [it for i, it in enumerate(store.get_list(key, cid)) if i not in sel])
    elif ctx == "kast":
        flat = _wardrobe_flat(cid)
        drop = {flat[i] for i in sel if i < len(flat)}   # (cat, item) пары на удаление
        w = store.load_wardrobe()
        for cat, it in drop:
            if cat in w and it in w[cat]:
                w[cat].remove(it)
                if not w[cat]:
                    del w[cat]
        store.save_wardrobe(w)
    elif ctx.startswith("lv_"):
        key = ctx[len("lv_"):]
        store_key = {"countries": config.COUNTRIES_KEY, "artists": config.ARTISTS_KEY,
                     "books": config.BOOKS_KEY}.get(key)
        if store_key:
            store.set_list(store_key, cid, [it for i, it in enumerate(store.get_list(store_key, cid)) if i not in sel])
    elif ctx == "fridge":
        store.set_list(config.FRIDGE_KEY, cid, [it for i, it in enumerate(store.get_list(config.FRIDGE_KEY, cid)) if i not in sel])
    elif ctx == "recipes":
        store.set_list(config.MY_RECIPES_KEY, cid, [r for i, r in enumerate(store.get_list(config.MY_RECIPES_KEY, cid)) if i not in sel])
    store.list_sel[f"{cid}:{ctx}"] = set()

async def open_cleanup(bot, cid, ctx):
    """Свежий вход в режим чистки - сбрасываем выбор."""
    store.list_sel[f"{cid}:{ctx}"] = set()
    await send_cleanup(bot, cid, ctx, 0)

async def handle_cleanup(bot, cid, data, q=None):
    parts = data.split("_")
    op = parts[0]
    if op == "clt":                       # переключить галочку
        page, idx, ctx = int(parts[-1]), int(parts[-2]), "_".join(parts[1:-2])
        _sel(cid, ctx).symmetric_difference_update({idx})
        await send_cleanup(bot, cid, ctx, page, q=q); return
    page, ctx = int(parts[-1]), "_".join(parts[1:-1])
    if op == "clp":                       # листать страницы
        await send_cleanup(bot, cid, ctx, page, q=q); return
    if op == "cla":                       # отметить/снять всю страницу
        _, items, _ = _ctx_items(cid, ctx)
        page_ids = {i for i, _ in items[page * CLEAN_PAGE:(page + 1) * CLEAN_PAGE]}
        sel = _sel(cid, ctx)
        if page_ids <= sel:               # уже все отмечены - снимаем
            sel -= page_ids
        else:                             # иначе отмечаем всю страницу
            sel |= page_ids
        await send_cleanup(bot, cid, ctx, page, q=q); return
    if op == "cld":                       # удалить отмеченные
        _cleanup_delete(cid, ctx)
        await send_cleanup(bot, cid, ctx, 0, q=q); return


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
    if difficulty == "easy":
        subject = ("животное, птицу, рыбу, насекомое, фрукт, овощ, бытовой предмет или транспортное средство "
                   "(примеры: слон, орёл, акула, яблоко, велосипед, холодильник). "
                   "НЕ загадывай людей, знаменитостей или абстрактные понятия.")
        diff_desc = ("подсказки через внешность, размер, цвет, звук, поведение, где живёт или для чего используется. "
                     "Очень простые и конкретные, угадывается легко")
    elif difficulty == "hard":
        subject = "персонажа, историческую личность или абстрактное понятие"
        diff_desc = "редкие персонажи или абстрактные понятия, специфичная лексика, хитрые подсказки"
    else:
        subject = "известного персонажа или историческую личность (кино, наука, история, музыка, литература)"
        diff_desc = "исторические личности, актёры, более тонкие подсказки"
    avoid = ("Не загадывай: " + ", ".join(recent[-30:])) if recent else ""
    prompt = f"""Игра-детектив. Загадай: {subject}.
Сложность: {diff_desc}. ВЕСЬ текст на языке: {clue_lang}. {avoid}
Каждая подсказка и каждое предложение заканчивается точкой.
Ответь строго, каждое поле с новой строки, без markdown:
CLUES: 4 подсказки на языке {clue_lang}, через | , от непрямой к явной, без имени/названия
ANSWER: название на языке {clue_lang}
ALIASES: то же название на русском, английском и нидерландском через |
HINT: ещё одна явная подсказка на языке {clue_lang}
HINT2: совсем простая, почти очевидная подсказка (но без названия), на языке {clue_lang}
QUOTE: короткая фраза или звук в духе загаданного на языке {clue_lang}
EXPLAIN: 1-2 предложения — что это такое (на языке {clue_lang})"""
    raw = ai.llm(prompt, 900, 1.0, tier="cheap")
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
        await verify.safe_error(bot, cid, e); return
    hints = [_dot(h) for h in [d.get("hint"), d.get("hint2")] if (h or "").strip()]
    store.game_state[str(cid)] = {"answer": d.get("answer", ""), "aliases": d.get("aliases", []),
                                  "quote": d.get("quote", ""), "hints": hints, "hint_i": 0,
                                  "explain": _dot(d.get("explain", "")), "tries": 0}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
         InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")],
        [InlineKeyboardButton(ui["chdiff"], callback_data="game_change_diff"),
         InlineKeyboardButton(ui["chlang"], callback_data="game_change")],
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


async def game_hint(bot, cid, q):
    st = store.game_state.get(str(cid))
    ui = GAME_UI.get(store.game_config.get(str(cid), {}).get("lang", "русский"), GAME_UI["русский"])
    hints = (st or {}).get("hints") or []
    i = (st or {}).get("hint_i", 0)
    if st and i < len(hints):
        st["hint_i"] = i + 1
        await q.message.reply_text(
            f"<b>{ui['hint']}</b>\n\n<b>{esc(hints[i])}</b>\n\n{ui['who']}",
            parse_mode="HTML")
    else:
        await q.message.reply_text(ui["nohint"])


async def game_reveal(bot, cid, q):
    st = store.game_state.pop(str(cid), None)
    ui = GAME_UI.get(store.game_config.get(str(cid), {}).get("lang", "русский"), GAME_UI["русский"])
    if not st:
        return
    body = st.get("explain") or st.get("quote", "")
    txt = f"<b>{ui['found']}</b>\n\n{ui['answer']}:\n<b>{esc(st.get('answer', ''))}</b>"
    if body:
        txt += f"\n\n{esc(body)}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["again"], callback_data="game_again")],
        [InlineKeyboardButton(ui["back"], callback_data="m_learn")],
    ])
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)


# ================= УРОВЕНЬ (/setup) =================
async def send_levels(bot, cid):
    nl_lvl, en_lvl = store.get_level(cid, "нидерландский"), store.get_level(cid, "английский")
    kb_nl = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_nl_{l}") for l in LEVELS]])
    kb_en = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_en_{l}") for l in LEVELS]])
    await bot.send_message(chat_id=cid, text=f"🇳🇱 Уровень нидерландского (сейчас {nl_lvl}):", reply_markup=kb_nl)
    await bot.send_message(chat_id=cid, text=f"🇬🇧 Уровень английского (сейчас {en_lvl}):", reply_markup=kb_en)