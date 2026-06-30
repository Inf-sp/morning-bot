import asyncio
import re
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
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

# ================= ТРЕНАЖЁР СЛОВ =================
TRAIN_FORMATS = ["gap", "tf", "card"]  # legacy — не используется в новом квизе

def _train_words(cid, language):
    """Только записи kind=word нужного языка из словаря с переводом: [(word, ru), ...]."""
    code = _code(language)
    out = []
    for w in _ensure_dict(cid):
        if _dict_lang(w) == code and _dict_kind(w) == "word":
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


def _same_len(options):
    lens = [len(str(x)) for x in options if str(x).strip()]
    return bool(lens) and max(lens) - min(lens) <= max(6, int(max(lens) * 0.45))


def _u16_len(text):
    return len((text or "").encode("utf-16-le")) // 2


def _train_question(word):
    prefix = "Переведи слово «"
    suffix = "»"
    text = f"{prefix}{word}{suffix}"
    return text, [MessageEntity(MessageEntity.BOLD, _u16_len(prefix), _u16_len(str(word)))]


def _clip_poll_explanation(text, limit=200):
    text = re.sub(r"\s+\n", "\n", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def _train_explanation(word, meaning, sentence="", sentence_ru="", mnemonic=""):
    lines = [f"{word} → {meaning}"]
    if sentence:
        context = f"{sentence}"
        if sentence_ru:
            context += f" → {sentence_ru}"
        lines.append(context)
    if mnemonic:
        lines.append(f"Зацепка: {mnemonic}")
    return _clip_poll_explanation("\n".join(lines), limit=160)


async def _gen_train_quiz_card(word, ru, language):
    """Smart LLM: context sentence + pedagogically useful distractors."""
    prompt = f"""
Ты методист тренажёра слов для языка: {language}.
Целевое слово: «{word}».
Базовый перевод: «{ru}».

Сделай quiz poll на перевод целевого слова. Контекстное предложение нужно только для последующего объяснения.

Жёсткие правила вариантов ответа:
1. Ровно 2 варианта на русском: один правильный и один неправильный.
2. Оба варианта — одна часть речи.
3. Варианты примерно одинаковой длины, без очевидно самого длинного ответа.
4. Неправильный вариант — похожая ловушка: частая ошибка, созвучие, близкое значение или ложный друг. Не случайное слово.
5. Контекстное предложение должно быть очень коротким, бытовым и понятным для человека с СДВГ: одна простая сцена, без лишних деталей.
6. Если пользователь выбрал неверный вариант, объяснение должно назвать, как этот неверный смысл выражается на {language}.

Верни JSON:
{{
  "sentence": "короткое предложение на {language} с целевым словом",
  "sentence_ru": "перевод предложения на русский",
  "correct": "правильный вариант на русском",
  "wrong": ["неверный вариант"],
  "wrong_map": {{"неверный вариант": "как это будет на {language}"}},
  "mnemonic": "очень короткая ассоциация для запоминания, до 8 слов",
  "meaning": "краткое значение целевого слова на русском, до 4 слов"
}}
"""
    try:
        d = await ai.allm_json(prompt, 900, tier="smart", route="claude", module="learning")
    except Exception:
        sentence, sentence_ru = await asyncio.to_thread(_gen_context, word, language)
        wrong = await asyncio.to_thread(_gen_distractors, word, ru, language, "fl_to_ru")
        return {
            "sentence": sentence or word,
            "sentence_ru": sentence_ru or ru,
            "correct": ru,
            "wrong": wrong[:1],
            "wrong_map": {},
            "mnemonic": "",
            "meaning": ru,
        }

    wrong = [str(x).strip() for x in (d.get("wrong") or []) if str(x).strip()][:1]
    correct = str(d.get("correct") or ru).strip()
    options = [correct] + wrong
    if len(wrong) < 1 or len(set(x.lower() for x in options)) < 2 or not _same_len(options):
        fallback_wrong = await asyncio.to_thread(_gen_distractors, word, ru, language, "fl_to_ru")
        for item in fallback_wrong:
            item = str(item).strip()
            if item and item.lower() not in {x.lower() for x in [correct] + wrong}:
                wrong.append(item)
            if len(wrong) >= 1:
                break
    return {
        "sentence": str(d.get("sentence") or word).strip(),
        "sentence_ru": str(d.get("sentence_ru") or "").strip(),
        "correct": correct,
        "wrong": wrong[:1],
        "wrong_map": d.get("wrong_map") if isinstance(d.get("wrong_map"), dict) else {},
        "mnemonic": str(d.get("mnemonic") or "").strip(),
        "meaning": str(d.get("meaning") or correct).strip(),
    }

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
        [InlineKeyboardButton("✨ Ещё слово", callback_data="train_next")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_learn")],
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
            text=f"{_flag(language)} В словаре нет отдельных слов с переводом. Добавь записи типа «слово» через словарь.",
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
        await bot.send_message(chat_id=cid, text="В словаре нет отдельных слов с переводом."); return

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

    card = await _gen_train_quiz_card(word, ru, language)
    correct_answer = card.get("correct") or ru
    wrong = list(card.get("wrong") or [])

    # Фолбэк: берём слово из словаря если LLM не сгенерировал дистрактор.
    if len(wrong) < 1:
        other = [(w, r) for w, r in words if w != word]
        _r.shuffle(other)
        for ow, oru in other[:1 - len(wrong)]:
            wrong.append(oru)

    clean_wrong = []
    seen = {str(correct_answer).lower()}
    for item in wrong:
        item = str(item).strip()
        if item and item.lower() not in seen:
            clean_wrong.append(item)
            seen.add(item.lower())
        if len(clean_wrong) >= 1:
            break
    options = [correct_answer] + clean_wrong[:1]
    if len(options) < 2:
        await bot.send_message(chat_id=cid, text="Не удалось собрать два хороших варианта. Попробуй ещё раз.", reply_markup=_train_again_kb())
        return
    _r.shuffle(options)
    correct_idx = options.index(correct_answer)

    st.update({
        "word": word,
        "ru": ru,
        "sentence": card.get("sentence") or word,
        "sentence_ru": card.get("sentence_ru") or "",
        "meaning": card.get("meaning") or correct_answer,
        "mnemonic": card.get("mnemonic") or "",
        "wrong_map": card.get("wrong_map") or {},
        "options": options,
        "correct_idx": correct_idx,
    })

    question, question_entities = _train_question(word)
    explanation = _train_explanation(
        word,
        st["meaning"],
        st.get("sentence", ""),
        st.get("sentence_ru", ""),
        st.get("mnemonic", ""),
    )

    msg = await bot.send_poll(
        chat_id=cid,
        question=question[:300],
        question_entities=question_entities,
        options=[str(x)[:100] for x in options[:10]],
        type="quiz",
        correct_option_id=correct_idx,
        is_anonymous=True,
        explanation=explanation,
        reply_markup=_train_again_kb(),
    )
    if getattr(msg, "poll", None):
        store.train_polls[msg.poll.id] = str(cid)


async def train_quiz_answer(bot, cid, idx):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    options = st.get("options", [])
    if idx >= len(options):
        return
    await _send_train_feedback(bot, cid, idx, st)


async def handle_train_poll_answer(bot, poll_answer):
    cid = store.train_polls.get(poll_answer.poll_id)
    if not cid:
        return
    st = store.train_state.get(str(cid))
    if not st:
        return
    option_ids = list(getattr(poll_answer, "option_ids", []) or [])
    if not option_ids:
        return
    await _send_train_feedback(bot, cid, int(option_ids[0]), st)


async def _send_train_feedback(bot, cid, idx, st):
    options = st.get("options", [])
    if idx >= len(options):
        return
    correct_idx = int(st.get("correct_idx", 0))
    word = st.get("word", "")
    lang = st.get("lang", "нидерландский")
    correct = str(options[correct_idx])
    chosen = str(options[idx])
    sentence = st.get("sentence", "")
    sentence_ru = st.get("sentence_ru", "")
    meaning = st.get("meaning") or correct
    wrong_map = st.get("wrong_map") or {}
    chosen_fl = wrong_map.get(chosen) or wrong_map.get(chosen.lower()) or ""
    mnemonic = st.get("mnemonic", "")

    if idx == correct_idx:
        lines = [
            "✅ <b>Верно.</b>",
            "",
            f"<b>{esc(word)}</b> → {esc(meaning)}",
        ]
    else:
        lines = [
            "❌ <b>Не совсем так.</b>",
            "",
            f"<b>{esc(word)}</b> → {esc(meaning)}",
            f"Твой ответ: «{esc(chosen)}»" + (f" — это <b>{esc(chosen_fl)}</b>." if chosen_fl else "."),
        ]

    if sentence:
        context = f"{esc(sentence)}"
        if sentence_ru:
            context += f" → {esc(sentence_ru)}"
        lines += ["", context]

    st["round"] = st.get("round", 0) + 1
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё слово", callback_data="train_next")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_learn")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=kb)


async def train_next(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    store.pending_input.pop(str(cid), None)
    await _render_quiz(bot, cid)


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

def _u16_len(text):
    return len((text or "").encode("utf-16-le")) // 2

def _proverb_entities_card(flag, original, literal, meaning):
    chunks = []
    entities = []

    def add(text, entity_type=None):
        offset = _u16_len("".join(chunks))
        chunks.append(text)
        if entity_type:
            entities.append(MessageEntity(entity_type, offset, _u16_len(text)))

    header = f"💭{flag} Живой язык" if flag else "💭 Живой язык"
    add(header, MessageEntity.BOLD)
    add("\n\n")
    if original:
        quote = f"({original}"
        if literal:
            quote += f" → {literal}"
        quote += ")"
        add(quote, MessageEntity.BLOCKQUOTE)
    if meaning:
        add("\n\nКогда так говорят\n")
        add(meaning)
    return "".join(chunks).rstrip(), entities

async def send_proverb(bot, cid, language):
    flag = _flag(language)
    try:
        d = await ai.allm_json(
            "Ты эксперт по живому разговорному языку. "
            f"Твоя цель — научить говорить как местный житель. "
            f"Выдай одно полезное выражение на {language}: фразовый глагол, идиому или частую разговорную фразу.\n"
            'JSON: {"original":"выражение на ' + language + '",'
            '"type":"фразовый глагол / идиома / разговорная фраза",'
            '"literal":"дословный перевод на русский",'
            '"meaning":"значение + когда так говорят, 1-2 строки на русском"}',
            400, tier="cheap", module="learning")
        def _cap(s):
            s = (s or "").strip()
            return s[0].upper() + s[1:] if s else s

        txt, entities = _proverb_entities_card(
            flag,
            _cap(d.get("original", "")),
            _cap(d.get("literal", "")),
            _cap(d.get("meaning", "")),
        )
    except Exception:
        txt, entities = _proverb_entities_card(flag, "", "", "Не удалось получить выражение.\nПопробуй ещё раз чуть позже.")
    await bot.send_message(chat_id=cid, text=txt, entities=entities, reply_markup=_proverb_kb(_code(language)))


async def send_proverb_both(bot, cid, with_kb=True):
    """Живой язык NL + EN: фразовый глагол, идиома или разговорная фраза."""
    try:
        d = await ai.allm_json(
            "Ты эксперт по живому разговорному языку. "
            "Выдай одно выражение — фразовый глагол, идиому или частую разговорную фразу.\n"
            'JSON: {"nl":"выражение на нидерландском",'
            '"en":"живой английский эквивалент (не перевод, а аналог)",'
            '"ru":"дословный перевод на русский",'
            '"type":"фразовый глагол / идиома / разговорная фраза",'
            '"meaning":"значение + когда так говорят, 1-2 строки на русском"}',
            500, tier="cheap", module="learning")
        def _cap(s):
            s = (s or "").strip()
            return s[0].upper() + s[1:] if s else s

        original = _cap(d.get("nl", "")) or _cap(d.get("en", ""))
        literal = _cap(d.get("ru", ""))
        txt, entities = _proverb_entities_card(" ", original, literal, _cap(d.get("meaning", "")))
    except Exception:
        txt, entities = _proverb_entities_card(" ", "", "", "Не удалось получить выражение.\nПопробуй ещё раз чуть позже.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё вариант", callback_data="a_proverb")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_learn")],
    ]) if with_kb else None
    await bot.send_message(chat_id=cid, text=txt, entities=entities, reply_markup=kb)


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

_DICT_ADD_VERB_RE = re.compile(r"\b(добавь|добавить|занеси|запиши|сохрани|внеси)\b", re.I)
_DICT_WORD_RE = re.compile(r"\b(?:в\s+)?(?:мой\s+)?словар[ьяьею]*\b", re.I)
_DICT_LANG_RE = re.compile(r"\b(?:на\s+)?(нидерландском|голландском|dutch|nl|английском|english|en)\b", re.I)
_DICT_KIND_RE = re.compile(r"\b(слово|слова|фразу|выражение|термин)\b", re.I)

def _dict_lang_hint(text):
    t = (text or "").lower()
    if any(x in t for x in ("английск", "english", " en ")):
        return "en"
    if any(x in t for x in ("нидерланд", "голланд", "dutch", " nl ")):
        return "nl"
    return "nl"

def _extract_chat_dict_add(text):
    """Команда из свободного чата: «добавь в словарь слово ...» -> полезная часть."""
    if not _DICT_ADD_VERB_RE.search(text or "") or not _DICT_WORD_RE.search(text or ""):
        return None, None
    lang = _dict_lang_hint(f" {text} ")
    payload = _DICT_ADD_VERB_RE.sub(" ", text, count=1)
    payload = _DICT_WORD_RE.sub(" ", payload)
    payload = _DICT_KIND_RE.sub(" ", payload)
    payload = _DICT_LANG_RE.sub(" ", payload)
    payload = re.sub(r"\s+", " ", payload).strip(" \t\n\r:;,.-–—")
    if len(payload) < 2:
        return None, None
    return payload, lang

async def try_add_dict_from_chat(bot, cid, text):
    """Перехватывает явную просьбу добавить слово/фразу в словарь из обычного чата."""
    payload, lang = _extract_chat_dict_add(text)
    if not payload:
        return False
    await add_words_batch(bot, cid, payload, lang)
    return True

def _parse_simple_pairs(text, lang_hint):
    """Быстрый путь без LLM для строк вида «de aandacht - внимание»."""
    chunks = [x.strip() for x in re.split(r"[\n;]+", text or "") if x.strip()]
    if not chunks:
        return []
    items = []
    for chunk in chunks:
        term, ru = _split_term(chunk)
        if not term or not ru:
            return []
        items.append({"word": term, "ru": ru, "lang": lang_hint})
    return items

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
    items = _parse_simple_pairs(text, lang)
    if not items:
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
    """Добавляет слова или фразы — LLM сам определяет тип каждого элемента."""
    language = "нидерландский" if lang == "nl" else "английский"
    spec = (
        "Разбей текст на отдельные элементы. Для каждого определи тип:\n"
        "- 'word': одно иностранное слово (нидерландское существительное — с артиклем de/het)\n"
        "- 'phrase': выражение из нескольких слов на иностранном языке\n"
        f"Если язык элемента неочевиден, используй '{lang}'.\n"
        'Верни ТОЛЬКО JSON: {"items":[{"word":"иностранный термин или фраза","ru":"перевод","lang":"nl|en","kind":"word|phrase"}]}'
    )
    try:
        d = await ai.allm_json(f"{spec}\n\n{secure.wrap_untrusted(text, 'text')}", 1200, tier="cheap", module="learning")
        items = d.get("items", []) if isinstance(d, dict) else []
    except Exception:
        items = []
    if not items:
        raw = re.split(r"[\n;,]+", text)
        items = [{"word": x.strip(), "ru": "", "lang": lang, "kind": "word"} for x in raw if x.strip()]

    added = {"nl": {"word": 0, "phrase": 0}, "en": {"word": 0, "phrase": 0}}
    for it in items:
        kind = it.get("kind", "word")
        term = (it.get("word") or "").strip()
        if not term:
            continue
        term, extra_ru = _split_term(term)
        if not term:
            continue
        ru = (it.get("ru") or "").strip() or extra_ru
        lng = "en" if it.get("lang") == "en" else "nl"
        knd = "phrase" if kind == "phrase" else _kind_of(term)
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

async def send_dict_lang(bot, cid, lang, back="m_dict_settings"):
    c = _dict_counts(cid)[lang]
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    name = "Нидерландский" if lang == "nl" else "Английский"
    txt = (f"{flag} <b>Словарь · {name}</b>\n\n"
           f"Слов: {c['word']} · Фраз: {c['phrase']}")
    rows = [
        [
            InlineKeyboardButton("❌ Слово", callback_data=f"a_dictedit_{lang}_word"),
            InlineKeyboardButton("❌ Фраза", callback_data=f"a_dictedit_{lang}_phrase"),
        ],
        [InlineKeyboardButton("✏️ Добавить слово или фразу", callback_data=f"a_dictadd_smart_{lang}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=back)],
    ]
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))


def _dict_manage_kb(lang: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Словарь", callback_data=f"a_dictlang_{lang}")],
        [InlineKeyboardButton("✏️ Добавить", callback_data=f"a_dictadd_smart_{lang}")],
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


async def send_morning_word(bot, cid, language=None, with_kb=True):
    """11:00 - Daily Words: метод дня недели + порция (3 слова + 2 фразы) из словаря."""
    import random as _r
    from datetime import datetime
    import settings
    language = language or settings.study_lang(cid)
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


# ================= УРОВЕНЬ ЯЗЫКА =================
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


# ===== DE/HET =====

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

def _dehet_gen_words():
    prompt = (
        "Дай 7 нидерландских существительных уровня A1-A2 с правильным артиклем de или het.\n"
        "Разные категории: дом, природа, еда, тело, транспорт, вещи. Примерно 4 de и 3 het (или наоборот).\n"
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
    [("◀️ Стоп", "m_nl")],
])


async def send_dehet_trainer(bot, cid):
    try:
        words = _dehet_gen_words()
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
        await bot.send_message(chat_id=cid, text="Сессия устарела. Начни заново через «Артикли».")
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
    if data == "dh_start":
        await send_dehet_trainer(bot, cid)
    elif data in ("dh_de", "dh_het"):
        await dehet_answer(bot, cid, q, data[3:])
