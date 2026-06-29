import asyncio
import re
import uuid
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
from cleanup import open_cleanup, send_cleanup, handle_cleanup  # noqa: F401

_HERE = Path(__file__).parent
import store
import ai
from util import esc
import verify
import secure

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

_URL_RE = re.compile(r'\(?https?://\S+\)?')

def _strip_urls(s):
    return re.sub(r'\s{2,}', ' ', _URL_RE.sub('', s or '')).strip()

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
 "task": "предложение с ровно одним пропуском ____ на {language}; в предложении не должно быть ни одного из вариантов A или B кроме самого пропуска",
 "task_ru": "перевод задания на русский",
 "a": "вариант A",
 "b": "вариант B",
 "correct": "a или b",
 "rule": "почему именно этот вариант верный, 1-2 строки"
}}"""
    return ai.llm_json(prompt, 1400, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL, route="openrouter")

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
                                     "task": d.get("task", ""), "task_ru": d.get("task_ru", ""),
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
        [InlineKeyboardButton("✨ Ещё пример из этой темы", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("🎲 Случайная тема", callback_data=f"rand_gram_{code}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L2), parse_mode="HTML", reply_markup=kb)

async def next_grammar(bot, cid, language):
    """Полностью новая грамматика с объяснением."""
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
                                     "task": d.get("task", ""), "task_ru": d.get("task_ru", ""),
                                     "topic": d.get("title", topic), "lang": language}
    await _send_grammar_task(bot, cid, d, _code(language))

async def grammar_answer(bot, cid, chosen):
    st = store.grammar_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Задание устарело, запроси новое."); return
    code = _code(st.get("lang", "нидерландский"))
    right = st["a"] if st["correct"] == "a" else st["b"]
    task_parts = (st.get("task") or "").split("____", 1)
    completed = ""
    if len(task_parts) == 2:
        completed = esc(task_parts[0]) + f"<b>{esc(right)}</b>" + esc(task_parts[1])
    if chosen == st["correct"]:
        L = ["✅ <b>Верно!</b>"]
        if completed:
            L += ["", completed]
        if st.get("task_ru"):
            L += ["", esc(st["task_ru"])]
        if st.get("rule"):
            L += ["", f"💡 {esc(st['rule'])}"]
    else:
        L = [f"❌ <b>Неверно.</b> Правильно: <b>{esc(right)}</b>"]
        if completed:
            L += ["", completed]
        if st.get("task_ru"):
            L += ["", esc(st["task_ru"])]
        if st.get("rule"):
            L += ["", f"💡 {esc(st['rule'])}"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё пример из этой темы", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("🎲 Случайная тема", callback_data=f"rand_gram_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)


# ================= ТРЕНАЖЁР СЛОВ =================
TRAIN_FORMATS = ["gap", "tf", "card"]  # legacy — не используется в новом квизе

def _train_words(cid, language):
    """Слова нужного языка из словаря с переводом: [(word, ru), ...]."""
    code = _code(language)
    out = []
    for w in _ensure_dict(cid):
        if _dict_lang(w) == code:
            term = _cap(_w_field(w, "word", "nl", "en"))
            ru = _w_field(w, "ru")
            if term and ru:
                out.append((term, ru))
    return out


def _gen_distractors(word_fl, word_ru, lang, direction):
    """LLM: 2 неправильных, но реалистичных варианта ответа."""
    if direction == "fl_to_ru":
        prompt = (
            f"Слово на {lang}: «{word_fl}», перевод на русский: «{word_ru}».\n"
            "Дай 2 неправильных, но реалистичных варианта перевода на русский — не однокоренные, не абсурдные.\n"
            'JSON: {"wrong": ["вариант1", "вариант2"]}'
        )
    else:
        prompt = (
            f"Русское слово: «{word_ru}», перевод на {lang}: «{word_fl}».\n"
            f"Дай 2 неправильных, но реалистичных слова на {lang} той же части речи.\n"
            'JSON: {"wrong": ["вариант1", "вариант2"]}'
        )
    try:
        d = ai.llm_json(prompt, 150, tier="cheap")
        return [str(x).strip() for x in (d.get("wrong") or []) if str(x).strip()][:2]
    except Exception:
        return []


def _gen_context(word, lang):
    """LLM: короткое предложение с использованием слова + перевод."""
    prompt = (
        f"Составь одно короткое естественное предложение на {lang} со словом «{word}».\n"
        'JSON: {"sentence": "...", "ru": "перевод на русский"}'
    )
    try:
        d = ai.llm_json(prompt, 200, tier="cheap")
        return d.get("sentence", ""), d.get("ru", "")
    except Exception:
        return "", ""

def train_data(language, level, word, ru, fmt):
    """Задание тренажёра вокруг слова `word` (перевод `ru`) в формате fmt (gap/tf)."""
    base = (f"Ты преподаватель языка {language}, уровень ученика {level}. "
            f'Целевое слово: "{word}"' + (f" (перевод: {ru})" if ru else "") + ". ")
    if fmt == "gap":
        prompt = base + f"""Составь ОДНО предложение на {language} уровня {level} с этим словом, заменив его на ____.
Правила для вариантов ответа:
- Один вариант — целевое слово (верный).
- Второй вариант — ДРУГОЕ слово с другим значением (не форма того же слова и не однокоренное). Оно должно быть той же части речи, но явно не подходить по смыслу. Например: для глагола «eten» (есть) дистрактор «slapen» (спать), а НЕ «eet»/«at».
- Оба варианта внешне НЕПОХОЖИ: разные корни, не отличаются только окончанием.
JSON (без переносов строк внутри значений):
{{"sentence":"предложение с ____","a":"вариант A","b":"вариант B","correct":"a или b","ru":"перевод предложения на русский","rule":"почему правильный подходит, а второй — нет (1 строка)"}}"""
    else:  # tf
        prompt = base + f"""Составь ОДНО естественное предложение на {language} уровня {level}, где это слово - существительное, выделенное тегами <b></b>.
Затем дай утверждение на русском о значении/роли выделенного существительного - иногда ВЕРНОЕ, иногда ЛОЖНОЕ (выбирай случайно).
JSON (без переносов строк внутри значений):
{{"sentence":"предложение со словом в <b></b>","claim":"утверждение о выделенном слове на русском","correct":true или false,"explain":"короткое пояснение на русском, 1 строка","ru":"перевод предложения"}}"""
    return ai.llm_json(prompt, 700, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)

def _word_meanings(word: str, language: str) -> list:
    """Все значения слова (tier=cheap). Пустой список если значение одно."""
    try:
        d = ai.llm_json(
            f"Слово на языке {language}: «{word}». "
            "Перечисли ВСЕ его значения на русском. "
            "Если значение одно — верни пустой массив. "
            'JSON: {"meanings": ["значение 1", "значение 2"]}',
            200, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL, route="openai"
        )
        meanings = d.get("meanings", []) if isinstance(d, dict) else []
        return [str(m).strip() for m in meanings if str(m).strip()]
    except Exception:
        return []


def _train_again_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё задание", callback_data="train_next")],
    ])

async def train_start(bot, cid, language):
    store.challenge_state.pop(str(cid), None)
    store.game_state.pop(str(cid), None)
    store.pending_input.pop(str(cid), None)
    if not _train_words(cid, language):
        code = _code(language)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📖 Открыть словарь", callback_data=f"a_dictlang_{code}")]])
        await bot.send_message(chat_id=cid,
            text=f"{_flag(language)} В словаре нет слов с переводом. Добавь слова через словарь.",
            reply_markup=kb)
        return
    store.train_state[str(cid)] = {"lang": language, "round": 0, "used": []}
    await _render_quiz(bot, cid)


async def _render_quiz(bot, cid):
    import random as _r
    store.pending_input.pop(str(cid), None)
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    language = st["lang"]
    words = _train_words(cid, language)
    if not words:
        await bot.send_message(chat_id=cid, text="В словаре нет слов с переводом."); return

    # Выбираем слово (без повторов пока не исчерпаем весь список)
    used = st.get("used", [])
    available = [(i, w) for i, w in enumerate(words) if i not in used]
    if not available:
        used = []
        available = list(enumerate(words))
        st["used"] = used
    idx, (word, ru) = _r.choice(available)
    used.append(idx)
    st["used"] = used

    # Чётный раунд: FL→RU, нечётный: RU→FL
    round_num = st.get("round", 0)
    direction = "fl_to_ru" if round_num % 2 == 0 else "ru_to_fl"

    correct_answer = ru if direction == "fl_to_ru" else word
    question_word = word if direction == "fl_to_ru" else ru

    try:
        wrong = await asyncio.to_thread(_gen_distractors, word, ru, language, direction)
    except Exception:
        wrong = []

    # Фолбэк: берём слова из словаря если LLM не сгенерировал
    if len(wrong) < 2:
        other = [(w, r) for w, r in words if w != word]
        _r.shuffle(other)
        for ow, oru in other[:2 - len(wrong)]:
            wrong.append(oru if direction == "fl_to_ru" else ow)

    options = [correct_answer] + wrong[:2]
    _r.shuffle(options)
    correct_idx = options.index(correct_answer)

    st.update({"word": word, "ru": ru, "direction": direction,
               "options": options, "correct_idx": correct_idx})

    flag = _flag(language)
    if direction == "fl_to_ru":
        lang_label = "по-нидерландски" if _code(language) == "nl" else "по-английски"
        question = f"<b>{esc(word)}</b>\n\nКакой правильный перевод?"
    else:
        lang_label = "нидерландском" if _code(language) == "nl" else "английском"
        question = f"<b>{esc(ru)}</b>\n\nКак это на {lang_label}?"

    L = [f"🧠 {flag} <b>Тренажёр</b>", "", question]
    buttons = [[InlineKeyboardButton(esc(opt), callback_data=f"train_ans_{i}")]
               for i, opt in enumerate(options)]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(buttons))


async def train_quiz_answer(bot, cid, idx):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    options = st.get("options", [])
    if idx >= len(options):
        return
    correct_idx = st.get("correct_idx", 0)
    word = st.get("word", "")
    ru = st.get("ru", "")
    lang = st.get("lang", "нидерландский")
    is_correct = idx == correct_idx

    try:
        sentence, sentence_ru = await asyncio.to_thread(_gen_context, word, lang)
    except Exception:
        sentence, sentence_ru = "", ""

    L = []
    if is_correct:
        L.append(f"✅ <b>Верно!</b>")
    else:
        L.append(f"❌ Нет. Правильно: <b>{esc(options[correct_idx])}</b>")
    L += ["", f"{_flag(lang)} <b>{esc(word)}</b> — {esc(ru)}"]
    if sentence:
        L += ["", f"📝 {esc(sentence)}"]
        if sentence_ru:
            L.append(f"<i>{esc(sentence_ru)}</i>")

    st["round"] = st.get("round", 0) + 1
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Следующее", callback_data="train_next")]])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)


async def train_next(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    store.pending_input.pop(str(cid), None)
    await _render_quiz(bot, cid)
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=_train_again_kb())


async def send_train_lang_select(bot, cid):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇳🇱 Нидерландский", callback_data="a_train_nl")],
        [InlineKeyboardButton("🇬🇧 Английский", callback_data="a_train_en")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_learn")],
    ])
    await bot.send_message(chat_id=cid,
        text="🧠 <b>Тренажёр слов</b>\n\nСлова и фразы для тренировки добавляются в разделе <b>Словарь</b>.\n\n<b>Выбери язык для тренировки 👇</b>",
        parse_mode="HTML", reply_markup=kb)


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
        [InlineKeyboardButton("✨ Ещё пример", callback_data=f"again_tr_{code}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
    return True


# ================= ГЛАГОЛ ДНЯ / ПОСЛОВИЦА =================
def _proverb_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё вариант", callback_data=f"a_proverb_{code}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"m_{code}")],
    ])

async def send_proverb(bot, cid, language):
    flag = _flag(language)
    try:
        d = ai.llm_json(
            "Ты эксперт по живому разговорному языку. "
            f"Твоя цель — научить говорить как местный житель. "
            f"Выдай одно полезное выражение на {language}: фразовый глагол, идиому или частую разговорную фразу.\n"
            'JSON: {"original":"выражение на ' + language + '",'
            '"type":"фразовый глагол / идиома / разговорная фраза",'
            '"literal":"дословный перевод на русский",'
            '"meaning":"значение + когда так говорят, 1-2 строки на русском"}',
            400, tier="cheap")
        def _cap(s):
            s = (s or "").strip()
            return s[0].upper() + s[1:] if s else s

        header = f"💬{flag} <b>Живой язык</b>"
        L = [header, ""]
        L.append("<b>Сегодняшнее выражение:</b>")
        if d.get("original"):
            L.append(f"• <b>{esc(_cap(d.get('original', '')))}</b>")
        if d.get("literal"):
            L.append(f"• 🇷🇺 {esc(_cap(d['literal']))}")
        if d.get("meaning"):
            L += ["", f"<b>Когда так говорят:</b>", f"• {esc(_cap(d['meaning']))}"]
        txt = "\n".join(L)
    except Exception:
        txt = f"💬{flag} <b>Живой язык</b>\n\n• Не удалось получить выражение.\n• Попробуй ещё раз чуть позже."
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=_proverb_kb(_code(language)))


async def send_proverb_both(bot, cid, with_kb=True):
    """Живой язык NL + EN: фразовый глагол, идиома или разговорная фраза."""
    try:
        d = ai.llm_json(
            "Ты эксперт по живому разговорному языку. "
            "Выдай одно выражение — фразовый глагол, идиому или частую разговорную фразу.\n"
            'JSON: {"nl":"выражение на нидерландском",'
            '"en":"живой английский эквивалент (не перевод, а аналог)",'
            '"ru":"дословный перевод на русский",'
            '"type":"фразовый глагол / идиома / разговорная фраза",'
            '"meaning":"значение + когда так говорят, 1-2 строки на русском"}',
            500, tier="cheap")
        def _cap(s):
            s = (s or "").strip()
            return s[0].upper() + s[1:] if s else s

        header = "💬 <b>Живой язык</b>"
        L = [header, ""]
        L.append("<b>Сегодняшнее выражение:</b>")
        if d.get("nl"):
            L.append(f"• 🇳🇱 {esc(_cap(d['nl']))}")
        if d.get("en"):
            L.append(f"• 🇬🇧 {esc(_cap(d['en']))}")
        if d.get("ru"):
            L.append(f"• 🇷🇺 {esc(_cap(d['ru']))}")
        if d.get("meaning"):
            L += ["", "<b>Когда так говорят:</b>", f"• {esc(_cap(d['meaning']))}"]
        txt = "\n".join(L)
    except Exception:
        txt = "💬 <b>Живой язык</b>\n\n• Не удалось получить выражение.\n• Попробуй ещё раз чуть позже."
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё вариант", callback_data="a_proverb")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_learn")],
    ]) if with_kb else None
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)


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

async def add_smart_batch(bot, cid, text, lang="nl"):
    """Добавляет слова, фразы или темы — LLM сам определяет тип каждого элемента."""
    language = "нидерландский" if lang == "nl" else "английский"
    spec = (
        "Разбей текст на отдельные элементы. Для каждого определи тип:\n"
        "- 'word': одно иностранное слово (нидерландское существительное — с артиклем de/het)\n"
        "- 'phrase': выражение из нескольких слов на иностранном языке\n"
        "- 'topic': грамматическая или лексическая тема для изучения (русское название, например 'прошедшее время')\n"
        f"Если язык элемента неочевиден, используй '{lang}'.\n"
        'Верни ТОЛЬКО JSON: {"items":[{"word":"термин или название темы","ru":"перевод (пустой для тем)","lang":"nl|en","kind":"word|phrase|topic"}]}'
    )
    try:
        d = ai.llm_json(f"{spec}\n\n{secure.wrap_untrusted(text, 'text')}", 1200, tier="cheap")
        items = d.get("items", []) if isinstance(d, dict) else []
    except Exception:
        items = []
    if not items:
        raw = re.split(r"[\n;,]+", text)
        items = [{"word": x.strip(), "ru": "", "lang": lang, "kind": "word"} for x in raw if x.strip()]

    added = {"nl": {"word": 0, "phrase": 0}, "en": {"word": 0, "phrase": 0}}
    topics_added = 0
    for it in items:
        kind = it.get("kind", "word")
        term = (it.get("word") or "").strip()
        if not term:
            continue
        if kind == "topic":
            store.add_to_list(_topics_key(language), cid, {"text": term})
            topics_added += 1
        else:
            term, extra_ru = _split_term(term)
            if not term:
                continue
            ru = (it.get("ru") or "").strip() or extra_ru
            lng = "en" if it.get("lang") == "en" else "nl"
            knd = _kind_of(term)
            store.add_to_list(config.DICT_KEY, cid, {"lang": lng, "word": _cap(term)[:80], "ru": ru, "kind": knd})
            added[lng][knd] += 1

    parts = []
    for lng, flag in (("nl", "🇳🇱"), ("en", "🇬🇧")):
        seg = []
        if added[lng]["word"]:
            seg.append(f"слов: {added[lng]['word']}")
        if added[lng]["phrase"]:
            seg.append(f"фраз: {added[lng]['phrase']}")
        if seg:
            parts.append(f"{flag} " + ", ".join(seg))
    if topics_added:
        parts.append(f"тем: {topics_added}")
    if not parts:
        await bot.send_message(chat_id=cid, text="Не удалось распознать. Попробуй ещё раз."); return
    await bot.send_message(chat_id=cid, text="✅ Добавлено — " + "; ".join(parts))
    await send_dict_lang(bot, cid, lang)


def _w_field(w, *keys):
    for k in keys:
        if isinstance(w, dict) and w.get(k):
            return w[k]
    return ""

def _ensure_dict(cid):
    """Возвращает словарь пользователя (без авто-сида)."""
    return store.get_list(config.DICT_KEY, cid)

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

async def send_dict(bot, cid, back="m_notes"):
    c = _dict_counts(cid)
    nl_total = c["nl"]["word"] + c["nl"]["phrase"]
    en_total = c["en"]["word"] + c["en"]["phrase"]
    txt = (f"🗂️ <b>Мой словарь</b>\n\nВсего: {nl_total + en_total} "
           f"(🇳🇱 {nl_total} · 🇬🇧 {en_total})\n\nВыбери язык 👇")
    rows = [
        [InlineKeyboardButton(f"🇳🇱 Нидерландский ({nl_total})", callback_data="a_dictlang_nl")],
        [InlineKeyboardButton(f"🇬🇧 Английский ({en_total})", callback_data="a_dictlang_en")],
        [InlineKeyboardButton("◀️ Назад", callback_data=back)],
    ]
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def send_dict_lang(bot, cid, lang):
    c = _dict_counts(cid)[lang]
    topics = get_topics(cid, "нидерландский" if lang == "nl" else "английский")
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    name = "Нидерландский" if lang == "nl" else "Английский"
    txt = (f"{flag} <b>Словарь · {name}</b>\n\n"
           f"Слов: {c['word']} · Фраз: {c['phrase']} · Тем: {len(topics)}")
    rows = [
        [InlineKeyboardButton("✏️ Добавить слово, фразу или тему", callback_data=f"a_dictadd_smart_{lang}")],
        [
            InlineKeyboardButton("❌ Слово", callback_data=f"a_dictedit_{lang}_word"),
            InlineKeyboardButton("❌ Фраза", callback_data=f"a_dictedit_{lang}_phrase"),
            InlineKeyboardButton("❌ Тема", callback_data=f"a_topicclean_{lang}"),
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_dict_settings")],
    ]
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


def _dict_manage_kb(lang: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Словарь", callback_data=f"a_dictlang_{lang}"),
            InlineKeyboardButton("✏️ Добавить", callback_data=f"a_dictadd_smart_{lang}"),
        ]
    ])

async def send_dict_edit(bot, cid, lang, kind):
    """Редактирование списка = режим чистки (пагинация + мультивыбор)."""
    await open_cleanup(bot, cid, f"d_{lang}_{kind}")

async def del_word(bot, cid, i):
    words = store.get_list(config.DICT_KEY, cid)
    removed = ""
    if i < len(words):
        removed_item = words.pop(i)
        removed = _cap(_w_field(removed_item, "word", "nl", "en"))
        store.set_list(config.DICT_KEY, cid, words)
    import settings as _s
    lang = _code(_s.study_lang(cid))
    label = f" <b>{esc(removed)}</b>" if removed else ""
    await bot.send_message(
        chat_id=cid,
        text=f"✅ Слово{label} удалено из текущего списка.\n\nЕсли хочешь, можно сразу открыть словарь или добавить новое.",
        parse_mode="HTML",
        reply_markup=_dict_manage_kb(lang),
    )

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

def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


async def send_morning_word(bot, cid, with_kb=True):
    """11:00 - Daily Words: метод дня недели + порция (3 слова + 2 фразы) из словаря."""
    import random as _r
    from datetime import datetime
    import settings
    language = settings.study_lang(cid)
    lang_code = _code(language)
    flag = _flag(language)
    wd = datetime.now(config.TZ).weekday()
    _title, _phase, method = WEEK_TRACK[wd]
    words = _ensure_dict(cid)
    pool = [w for w in words if _dict_lang(w) == lang_code]
    L = [f"📚{flag} <b>Слова и фразы дня</b>", "", esc(method)]
    if wd >= 5 or not pool:
        L += ["", "📖 Открой словарь, если хочешь добавить что-то новое или быстро повторить текущее."]
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
        return
    word_items = [w for w in pool if _dict_kind(w) == "word"]
    phrase_items = [w for w in pool if _dict_kind(w) == "phrase"]
    chosen_phrases = _r.sample(phrase_items, min(2, len(phrase_items)))
    chosen_words = _r.sample(word_items, min(3, len(word_items)))
    if not chosen_phrases and not chosen_words:
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
        return

    phrase_del_row = []
    if chosen_phrases:
        L += ["", "💬 <b>Фразы</b>"]
        for w in chosen_phrases:
            word = _cap(_w_field(w, "word", "nl", "en"))
            ru = _w_field(w, "ru")
            L.append(f"• {esc(word)} → {esc(ru)}")
            try:
                idx = words.index(w)
                phrase_del_row.append(InlineKeyboardButton(f"❌ {word[:30]}", callback_data=f"worddel_{idx}"))
            except ValueError:
                pass

    word_del_row = []
    if chosen_words:
        L += ["", "📖 <b>Слова</b>"]
        for w in chosen_words:
            word = _cap(_w_field(w, "word", "nl", "en"))
            ru = _w_field(w, "ru")
            L.append(f"• {esc(word)} → {esc(ru)}")
            try:
                idx = words.index(w)
                word_del_row.append(InlineKeyboardButton(f"❌ {word[:14]}", callback_data=f"worddel_{idx}"))
            except ValueError:
                pass

    L += ["", "💡 Попробуй использовать 1-2 элемента сегодня в сообщениях, мыслях или разговоре."]

    rows = []
    if with_kb:
        rows.extend([[btn] for btn in phrase_del_row])
        rows.extend(_chunks(word_del_row, 3))

    await bot.send_message(
        chat_id=cid,
        text="\n".join(L),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )


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
    rows = [[InlineKeyboardButton("✏️ Добавить тему", callback_data=f"a_topicadd_{code}")]]
    if topics:
        rows.append([InlineKeyboardButton("❌ Очистить выученное", callback_data=f"a_topicclean_{code}")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=f"m_{code}")])
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
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(" К темам", callback_data=f"a_topics_{code}")]])
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
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(" К темам", callback_data=f"a_topics_{code}")]])
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
                "title": "🕵️ Игра-детектив", "who": "Кто это?", "hint": "💡 Подсказка", "reveal": "😞 Сдаюсь", "suspect": "Подозреваемый:", "analyse": "Анализ", "found": "✅ Дело раскрыто!", "answer": "Ответ", "give": "Знаешь ответ? Напиши его или нажми «😞 Сдаюсь»",
                "again": "🕵️ Загадать ещё", "chdiff": "🎚 Сложность", "chlang": "🌐 Язык", "back": " Обучение", "nohint": "Подсказок больше нет.",
                "correct": "✅ Верно!", "wrong": "❌ Не то", "retry": "Ещё попытка - напиши ответ или возьми подсказку."},
    "английский": {"diff_q": "Choose difficulty:", "easy": "Easy", "med": "Medium", "hard": "Hard",
                "title": "🕵️ Detective Game", "who": "Who am I?", "hint": "💡 Hint", "reveal": "😞 Give up", "suspect": "Suspect:", "analyse": "Analysis", "found": "✅ Case solved!", "answer": "Answer", "give": "Know it? Type the name or tap «😞 Give up»",
                "again": "🕵️ New character", "chdiff": "🎚 Difficulty", "chlang": "🌐 Language", "back": " Learning", "nohint": "No more hints.",
                "correct": "✅ Correct!", "wrong": "❌ Not quite", "retry": "Try again - type a name or take a hint."},
    "нидерландский": {"diff_q": "Kies niveau:", "easy": "Makkelijk", "med": "Gemiddeld", "hard": "Moeilijk",
                "title": "🕵️ Detectivespel", "who": "Wie ben ik?", "hint": "💡 Hint", "reveal": "😞 Opgeven", "suspect": "Verdachte:", "analyse": "Analyse", "found": "✅ Opgelost!", "answer": "Antwoord", "give": "Weet je het? Typ de naam of tik «😞 Opgeven»",
                "again": "🕵️ Nog een", "chdiff": "🎚 Niveau", "chlang": "🌐 Taal", "back": " Leren", "nohint": "Geen hints meer.",
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
CLUES: 4 улики на языке {clue_lang}, через | , от косвенной к более явной — конкретные детали (форма, цвет, происхождение, функция, ощущения), без имени/названия
ANSWER: название на языке {clue_lang}
ALIASES: то же название на русском, английском и нидерландском через |
HINT: ещё одна явная подсказка на языке {clue_lang}
HINT2: совсем простая, почти очевидная подсказка (но без названия), на языке {clue_lang}
EXPLAIN: 1-2 предложения — что это такое (на языке {clue_lang})"""
    raw = ai.llm(prompt, 900, 1.0, tier="cheap")
    out = {}
    for key, field in (("CLUES", "clues"), ("ANSWER", "answer"), ("ALIASES", "aliases"),
                       ("HINT", "hint"), ("HINT2", "hint2"), ("EXPLAIN", "explain")):
        m = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z]+\d*:|\Z)", raw, re.S)
        out[field] = m.group(1).strip() if m else ""
    out["clues"] = out.get("clues", "").replace(" | ", "\n").replace("|", "\n")
    out["aliases"] = [x.strip() for x in out.get("aliases", "").split("|") if x.strip()]
    return out

def game_lang_kb():
    return InlineKeyboardMarkup([
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
        [InlineKeyboardButton(ui["hard"], callback_data="gamediff_hard")],
    ])
    await bot.send_message(chat_id=cid, text=ui["diff_q"], reply_markup=kb)

async def send_game(bot, cid):
    store.challenge_state.pop(str(cid), None)   # фикс: чтобы перевод не перехватывал
    cfg = store.game_config.get(str(cid), {"lang": "английский", "difficulty": "easy"})
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
        [InlineKeyboardButton("◀️ Назад", callback_data="game_change")],
    ])
    clues = "\n".join(f"•{c.strip()}" for c in d.get("clues", "").split("\n") if c.strip())
    L = [f"<b>{ui['title']}</b>", "", f"<b>{ui['suspect']}</b>", clues, "", f"<b>{ui['who']} 🤔</b>"]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)

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
def _levels_kb(nl_lvl, en_lvl, back="set_home"):
    def _row(code, cur):
        hard = _is_b1plus(cur)
        flag = "🇳🇱" if code == "nl" else "🇬🇧"
        return [
            InlineKeyboardButton(("✅ " if not hard else "") + f"{flag} Лёгкий", callback_data=f"lvl_{code}_A2"),
            InlineKeyboardButton(("✅ " if hard else "") + f"{flag} Сложный", callback_data=f"lvl_{code}_B1"),
        ]
    return InlineKeyboardMarkup([
        _row("nl", nl_lvl),
        _row("en", en_lvl),
        [InlineKeyboardButton("◀️ Назад", callback_data=back)],
    ])

async def send_levels(bot, cid, q=None, back="set_home"):
    nl_lvl = store.get_level(cid, "нидерландский")
    en_lvl = store.get_level(cid, "английский")
    nl_label = "Сложный (B1+)" if _is_b1plus(nl_lvl) else "Лёгкий (A1–A2)"
    en_label = "Сложный (B1+)" if _is_b1plus(en_lvl) else "Лёгкий (A1–A2)"
    text = (
        "🎚 <b>Уровень языков</b>\n\n"
        f"🇳🇱 Нидерландский: <b>{nl_label}</b>\n"
        f"🇬🇧 Английский: <b>{en_label}</b>\n\n"
        "Нажми уровень чтобы изменить:"
    )
    kb = _levels_kb(nl_lvl, en_lvl, back)
    if q is not None:
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=kb)


# ===== МИКРО-ГРАММАТИКА (grammar_micro) =====

_ikb = lambda rows: InlineKeyboardMarkup(
    [[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows]
)

SYSTEM_TOPICS = {
    "нидерландский": {
        "A1": [
            "Порядок слов (SVO)",
            "Артикли de/het",
            "Спряжение глаголов в настоящем",
            "Отрицание niet/geen",
            "Вопросительные предложения",
            "Личные местоимения",
            "Множественное число существительных",
            "Числительные и время",
            "Притяжательные местоимения",
            "Предлоги места",
        ],
        "A2": [
            "Perfectum (voltooide tijd)",
            "Инверсия",
            "Разделяемые глаголы",
            "Er-конструкции",
            "Степени сравнения прилагательных",
            "Imperfectum (onvoltooid verleden)",
            "Придаточные с dat/omdat",
            "Возвратные глаголы (zich)",
            "Предлоги времени",
            "Сочинительные союзы",
        ],
        "B1": [
            "Страдательный залог (passief)",
            "Косвенная речь",
            "Придаточные с omdat/want",
            "Модальные глаголы (moeten/mogen/kunnen)",
            "Относительные местоимения (die/dat/wie/wat)",
            "Futurum (zullen/gaan)",
            "Условные предложения с als",
            "Отделяемые и неотделяемые приставки",
            "Плюсквамперфект",
            "Инфинитивные обороты с te",
        ],
    },
    "английский": {
        "A1": [
            "Present Simple",
            "Артикли a/an/the",
            "Вопросы с do/does",
            "Отрицание don't/doesn't",
            "There is/are",
            "Личные и притяжательные местоимения",
            "Множественное число существительных",
            "Предлоги места (in/on/at/under)",
            "Числительные и время",
            "Глагол to be",
        ],
        "A2": [
            "Present Continuous",
            "Past Simple",
            "Going to (планы)",
            "Модальные can/must/should",
            "Степени сравнения прилагательных",
            "Past Continuous",
            "Future Simple (will)",
            "Предлоги времени (in/on/at/since/for)",
            "Союзы but/because/so/although",
            "Вопросительные слова (who/what/where/when/why/how)",
        ],
        "B1": [
            "Present Perfect",
            "Passive Voice",
            "Reported Speech",
            "Conditionals 1 & 2",
            "Придаточные времени и условия",
            "Past Perfect",
            "Модальные could/would/might",
            "Герундий и инфинитив",
            "Относительные придаточные (who/which/that)",
            "Фразовые глаголы (phrasal verbs)",
        ],
    },
}

_LANG_NAME = {"nl": "нидерландский", "en": "английский"}
_LANG_CODE = {"нидерландский": "nl", "английский": "en"}
_LANG_FLAG = {"нидерландский": "🇳🇱", "английский": "🇬🇧"}
_LEVEL_EMOJI = {"A1": "📘", "A2": "📙", "B1": "📗"}


def _gm_lang(code):
    return _LANG_NAME.get(code, "нидерландский")


def _gm_topics(cid, lang):
    raw = store.get_list(config.MICRO_TOPICS_KEY, cid)
    return raw.get(lang, []) if isinstance(raw, dict) else []


def _gm_save_topics(cid, lang, topics):
    raw = store.get_list(config.MICRO_TOPICS_KEY, cid)
    d = raw if isinstance(raw, dict) else {}
    d[lang] = topics
    store.set_list(config.MICRO_TOPICS_KEY, cid, d)


def _gm_progress(cid):
    raw = store.get_list(config.MICRO_PROGRESS_KEY, cid)
    return raw if isinstance(raw, dict) else {}


def _gm_save_progress(cid, prog):
    store.set_list(config.MICRO_PROGRESS_KEY, cid, prog)


def _gm_lesson(topic_id):
    raw = store.get_list(config.MICRO_LESSONS_KEY, topic_id)
    return raw if isinstance(raw, dict) and raw else None


def _gm_save_lesson(topic_id, lesson):
    store.set_list(config.MICRO_LESSONS_KEY, topic_id, lesson)


def _gm_ensure_system_topics(cid, lang):
    topics = _gm_topics(cid, lang)
    existing = {t["title"] for t in topics if t.get("system")}
    changed = False
    for level, titles in SYSTEM_TOPICS.get(lang, {}).items():
        for title in titles:
            if title not in existing:
                topics.append({
                    "id": uuid.uuid4().hex[:12],
                    "level": level,
                    "title": title,
                    "system": True,
                })
                changed = True
    if changed:
        _gm_save_topics(cid, lang, topics)
    return _gm_topics(cid, lang)


def _gm_find_topic(cid, topic_id):
    for lang in ("нидерландский", "английский"):
        for t in _gm_topics(cid, lang):
            if t["id"] == topic_id:
                return t, lang
    return None, None


def _gm_gen_lesson(lang, title):
    prompt = (
        f"Создай микро-урок по грамматике ({lang}), тема: «{title}».\n"
        "JSON (без переносов внутри строк):\n"
        "{\n"
        ' "pattern": "шаблон предложения [Субъект + Глагол + ...], коротко",\n'
        ' "rule": "правило 1-2 предложения: когда применяется и как строится",\n'
        ' "examples": [\n'
        f'   {{"foreign": "пример на изучаемом языке", "ru": "перевод"}},\n'
        f'   {{"foreign": "второй пример на изучаемом языке", "ru": "перевод"}}\n'
        " ],\n"
        ' "hint": "шаблон с заменителями слов для составления своего предложения"\n'
        "}"
    )
    return ai.llm_json(prompt, 600, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)


def _gm_check(lang, title, pattern, sentence):
    prompt = (
        f"Пользователь изучает грамматику ({lang}), тема «{title}», паттерн: {pattern}.\n"
        f"Его предложение: {sentence}\n"
        "Проверь ТОЛЬКО применение паттерна (не орфографию, не стиль).\n"
        'JSON: {"ok": true/false, "feedback": "фидбек 1-2 строки на русском"}'
    )
    return ai.llm_json(prompt, 200, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)


def _gm_gen_dehet_words():
    prompt = (
        "Дай 7 нидерландских существительных уровня A1-A2 с правильным артиклем de или het.\n"
        "Разные темы: дом, природа, еда, тело, транспорт, вещи. Примерно 4 de и 3 het (или наоборот).\n"
        'JSON (только массив): [{"word": "huis", "article": "het"}, ...]'
    )
    return ai.llm_json(prompt, 300, ai.GRAMMAR_ORDER, claude_model=config.GRAMMAR_MODEL)


def _dehet_card(st):
    idx = st["idx"]
    total = len(st["words"])
    word = st["words"][idx]["word"]
    return f"🧩 <b>de / het</b>  ·  {idx + 1} из {total}\n\n<b>      {esc(word)}</b>"


_DEHET_KB = _ikb([
    [("de", "dh_de"), ("het", "dh_het")],
    [("◀️ Стоп", "gm_lang_nl")],
])


async def gm_send_home(bot, cid):
    kb = _ikb([
        [("🇳🇱 Нидерландский", "gm_lang_nl"), ("🇬🇧 Английский", "gm_lang_en")],
        [("◀️ Назад", "m_learn")],
    ])
    await bot.send_message(
        chat_id=cid,
        text="📘 <b>Микро-грамматика</b>\n\nОдин шаблон — один урок. Читаешь, пробуешь, идёшь дальше.",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def gm_send_lang(bot, cid, code):
    lang = _gm_lang(code)
    flag = _LANG_FLAG[lang]
    rows = [
        [("📗 Лёгкий · A1–A2", f"gm_level_{code}_easy")],
        [("📘 Сложный · B1+", f"gm_level_{code}_hard")],
        [("◀️ Назад", f"m_{code}")],
    ]
    await bot.send_message(
        chat_id=cid,
        text=f"📘 <b>Грамматика · {flag} {lang.capitalize()}</b>\n\nВыбери курс:",
        parse_mode="HTML",
        reply_markup=_ikb(rows),
    )


async def gm_send_level(bot, cid, code, level):
    lang = _gm_lang(code)
    flag = _LANG_FLAG[lang]
    topics = _gm_ensure_system_topics(cid, lang)
    prog = _gm_progress(cid)

    if level == "easy":
        level_topics = [t for t in topics if t.get("level") in ("A1", "A2") and t.get("system")]
        title = "📗 Лёгкий · A1–A2"
    else:  # hard
        level_topics = [t for t in topics if t.get("level") == "B1" and t.get("system")]
        title = "📘 Сложный · B1+"

    rows = []
    for t in level_topics:
        status = prog.get(t["id"], "new")
        icon = "✅" if status == "done" else ("📍" if status == "current" else "▸")
        rows.append([(f"{icon} {t['title']}", f"gm_topic_{t['id']}")])
    rows.append([("◀️ Назад", f"gm_lang_{code}")])

    done_count = sum(1 for t in level_topics if prog.get(t["id"]) == "done")
    await bot.send_message(
        chat_id=cid,
        text=f"{title} · {flag} {lang.capitalize()}\n\n{done_count}/{len(level_topics)} пройдено",
        parse_mode="HTML",
        reply_markup=_ikb(rows),
    )


async def gm_send_topic(bot, cid, topic_id):
    topic, lang = _gm_find_topic(cid, topic_id)
    if not topic:
        await bot.send_message(chat_id=cid, text="Тема не найдена.")
        return

    flag = _LANG_FLAG[lang]
    title = topic["title"]
    level = topic.get("level", "A1")
    code = _LANG_CODE[lang]

    lesson = _gm_lesson(topic_id)
    if not lesson:
        await bot.send_message(chat_id=cid, text="⏳ Генерирую урок...")
        try:
            lesson = _gm_gen_lesson(lang, title)
        except Exception as e:
            await verify.safe_error(bot, cid, e)
            return
        _gm_save_lesson(topic_id, lesson)

    prog = _gm_progress(cid)
    if prog.get(topic_id) != "done":
        prog[topic_id] = "current"
        _gm_save_progress(cid, prog)

    pattern = lesson.get("pattern", "")
    rule = lesson.get("rule", "")
    examples = lesson.get("examples", [])
    hint = lesson.get("hint", pattern)

    L = [f"📘 {flag} <b>{esc(title)}</b>", ""]
    L.append(f"<b>Шаблон:</b> {esc(pattern)}")
    L += ["", f"<b>Правило:</b> {esc(rule)}"]
    if examples:
        L += ["", "<b>Примеры:</b>"]
        for i, ex in enumerate(examples[:3], 1):
            L.append(f"{i}. {esc(ex.get('foreign', ''))} — <i>{esc(ex.get('ru', ''))}</i>")
    L += ["", "<i>Прочитай вслух. Покрути в голове. Всё.</i>"]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")

    store.micro_state[cid] = {
        "topic_id": topic_id,
        "lang": lang,
        "title": title,
        "pattern": pattern,
        "level": level,
        "code": code,
        "awaiting_sentence": True,
    }
    kb = _ikb([
        [("✅ Усвоил", f"gm_done_{topic_id}"), ("◀️ К темам", f"gm_level_{code}_{level}")],
    ])
    await bot.send_message(
        chat_id=cid,
        text=f"✍️ <b>Твоя очередь!</b>\n\nНапиши ОДНО предложение по шаблону:\n<code>{esc(hint)}</code>",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def check_sentence(bot, cid, text):
    st = store.micro_state.get(cid, {})
    if not st.get("awaiting_sentence"):
        return False
    store.micro_state[cid] = {**st, "awaiting_sentence": False}

    topic_id = st["topic_id"]
    lang = st["lang"]
    title = st["title"]
    pattern = st["pattern"]
    level = st.get("level", "A1")
    code = st.get("code", _LANG_CODE.get(lang, "nl"))

    try:
        result = _gm_check(lang, title, pattern, secure.wrap_untrusted(text, "предложение"))
    except Exception as e:
        await verify.safe_error(bot, cid, e)
        return True

    ok = result.get("ok", False)
    feedback = result.get("feedback", "")
    icon = "✅" if ok else "🤔"

    kb = _ikb([
        [("✨ Ещё раз", f"gm_topic_{topic_id}"), ("◀️ К темам", f"gm_level_{code}_{level}")],
    ])
    L = [f"{icon} <i>{esc(text)}</i>", "", esc(feedback)]
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
    return True


async def gm_mark_done(bot, cid, topic_id):
    store.micro_state.pop(cid, None)
    topic, lang = _gm_find_topic(cid, topic_id)

    prog = _gm_progress(cid)
    prog[topic_id] = "done"
    _gm_save_progress(cid, prog)

    if not lang:
        await bot.send_message(chat_id=cid, text="✅ Тема пройдена.")
        return

    level = topic.get("level", "A1")
    code = _LANG_CODE[lang]
    level_topics = [t for t in _gm_topics(cid, lang) if t.get("level") == level and t.get("system")]

    next_topic = None
    found = False
    for t in level_topics:
        if found and prog.get(t["id"]) != "done":
            next_topic = t
            break
        if t["id"] == topic_id:
            found = True

    emoji = _LEVEL_EMOJI.get(level, "📘")
    kb = _ikb([
        [("📋 К списку тем", f"gm_level_{code}_{level}")],
        [("✨ Ещё раз", f"gm_level_{code}_{level}"), ("◀️ К языку", f"gm_lang_{code}")],
    ])
    if next_topic:
        done_text = "✅ Тема пройдена!\n\nОткрой список тем, когда будешь готов."
    else:
        done_text = f"{emoji} <b>Курс {level} завершён!</b>\n\nВсе темы пройдены. Отличная работа!"
    await bot.send_message(
        chat_id=cid,
        text=done_text,
        parse_mode="HTML",
        reply_markup=kb,
    )


async def gm_send_custom(bot, cid, code):
    lang = _gm_lang(code)
    flag = _LANG_FLAG[lang]
    topics = [t for t in _gm_topics(cid, lang) if not t.get("system")]

    rows = []
    for t in topics:
        rows.append([
            (f"📝 {t['title'][:28]}", f"gm_topic_{t['id']}"),
            ("❌", f"gm_deltopic_{t['id']}"),
        ])
    rows.append([("✏️ Добавить тему", f"gm_addtopic_{code}")])
    rows.append([("◀️ Назад", f"a_dictlang_{code}")])

    header = f"📝 <b>Мои темы · {flag} {lang.capitalize()}</b>"
    body = "\n\nСвоих тем пока нет. Добавь первую!" if not topics else ""
    await bot.send_message(
        chat_id=cid, text=header + body, parse_mode="HTML", reply_markup=_ikb(rows)
    )


async def gm_delete_topic(bot, cid, topic_id):
    topic, lang = _gm_find_topic(cid, topic_id)
    if not lang:
        await bot.send_message(chat_id=cid, text="Тема не найдена.")
        return
    code = _LANG_CODE[lang]
    topics = [t for t in _gm_topics(cid, lang) if t["id"] != topic_id]
    _gm_save_topics(cid, lang, topics)
    _gm_save_lesson(topic_id, {})
    prog = _gm_progress(cid)
    prog.pop(topic_id, None)
    _gm_save_progress(cid, prog)
    await bot.send_message(chat_id=cid, text="✅ Тема удалена.")
    await gm_send_custom(bot, cid, code)


async def add_topic_done(bot, cid, code, name):
    lang = _gm_lang(code)
    name = name.strip()
    if not name:
        await bot.send_message(chat_id=cid, text="Название не может быть пустым.")
        return
    topics = _gm_topics(cid, lang)
    topics.append({"id": uuid.uuid4().hex[:12], "level": "custom", "title": name, "system": False})
    _gm_save_topics(cid, lang, topics)
    await bot.send_message(chat_id=cid, text=f"✅ Тема «{esc(name)}» добавлена.", parse_mode="HTML")
    await gm_send_custom(bot, cid, code)


async def send_dehet_trainer(bot, cid):
    try:
        words = _gm_gen_dehet_words()
    except Exception as e:
        await verify.safe_error(bot, cid, e)
        return
    if not isinstance(words, list) or not words:
        await bot.send_message(chat_id=cid, text="Не удалось сгенерировать слова, попробуй ещё.")
        return
    store.dehet_state[cid] = {"words": words, "idx": 0, "score": 0, "results": []}
    await bot.send_message(
        chat_id=cid,
        text=_dehet_card(store.dehet_state[cid]),
        parse_mode="HTML",
        reply_markup=_DEHET_KB,
    )


async def dehet_answer(bot, cid, q, chosen):
    st = store.dehet_state.get(cid)
    if not st:
        await bot.send_message(chat_id=cid, text="Сессия устарела. Начни заново через меню Грамматика.")
        return
    words = st["words"]
    idx = st["idx"]
    word_data = words[idx]
    correct = word_data["article"]
    ok = chosen == correct
    if ok:
        st["score"] += 1
    st["results"].append({"word": word_data["word"], "article": correct, "ok": ok})
    st["idx"] += 1

    feedback = f"{'✅' if ok else f'❌ (верно: {correct})'} <b>{esc(word_data['word'])}</b>\n\n"

    if st["idx"] >= len(words):
        score = st["score"]
        total = len(words)
        lines = [f"🎯 <b>Результат: {score}/{total}</b>", ""]
        for r in st["results"]:
            mark = "✅" if r["ok"] else f"❌ ({r['article']})"
            lines.append(f"{mark} <b>{esc(r['word'])}</b> — {r['article']}")
        store.dehet_state.pop(cid, None)
        kb = _ikb([[("✨ Ещё раз", "dh_start"), ("◀️ Назад", "m_nl")]])
        try:
            await q.edit_message_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=kb)
    else:
        text = feedback + _dehet_card(st)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=_DEHET_KB)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=_DEHET_KB)


async def handle_callback(bot, cid, q, data):
    if data == "gm_home":
        await gm_send_home(bot, cid)
    elif data.startswith("gm_lang_"):
        await gm_send_lang(bot, cid, data[8:])
    elif data.startswith("gm_level_"):
        rest = data[len("gm_level_"):]
        code, level = rest.split("_", 1)
        await gm_send_level(bot, cid, code, level)
    elif data.startswith("gm_topic_"):
        await gm_send_topic(bot, cid, data[len("gm_topic_"):])
    elif data.startswith("gm_done_"):
        await gm_mark_done(bot, cid, data[len("gm_done_"):])
    elif data.startswith("gm_custom_"):
        await gm_send_custom(bot, cid, data[len("gm_custom_"):])
    elif data.startswith("gm_addtopic_"):
        code = data[len("gm_addtopic_"):]
        lang = _gm_lang(code)
        store.pending_input[cid] = f"gm_addtopic_{code}"
        flag = _LANG_FLAG[lang]
        await bot.send_message(
            chat_id=cid, text=f"✍️ Введи название темы для {flag} {lang}:"
        )
    elif data.startswith("gm_deltopic_"):
        await gm_delete_topic(bot, cid, data[len("gm_deltopic_"):])
    elif data == "dh_start":
        await send_dehet_trainer(bot, cid)
    elif data in ("dh_de", "dh_het"):
        await dehet_answer(bot, cid, q, data[3:])
