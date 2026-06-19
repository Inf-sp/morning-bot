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
def grammar_data(language, level, topic=None):
    in_lang = _is_b1plus(level) and language == "нидерландский"
    lang_rule = ("Объяснение - на русском простыми словами; пример и задание на нидерландском с переводом." if in_lang
                 else "Объяснение простым русским, пример на изучаемом языке с переводом.")
    book = ("Ориентируйся на программу учебника TaalCompleet. " if language == "нидерландский" else "")
    topic_rule = (f'Тема СТРОГО: "{topic}". Дай НОВЫЙ пример и новое задание по этой же теме.'
                  if topic else f"Выбери одну тему уровня {level}, каждый раз НОВУЮ.")
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
 "a": "вариант A",
 "b": "вариант B",
 "correct": "a или b",
 "hint": "подсказка-правило, 1 строка"
}}"""
    return ai.llm_json(prompt, 900, LO)

async def send_grammar(bot, cid, language, flag=None, topic=None):
    level = store.get_level(cid, language)
    try:
        d = grammar_data(language, level, topic)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.grammar_state[str(cid)] = {"correct": d.get("correct", "a"), "hint": d.get("hint", ""),
                                     "a": d.get("a", ""), "b": d.get("b", ""),
                                     "topic": d.get("title", ""), "lang": language}
    code = _code(language)
    # Сообщение 1: объяснение
    L = [f"📝 <b>{_flag(language)} {_adj(language)} грамматика</b>", ""]
    L.append(f"<b>Тема:</b> {esc(d.get('title',''))}")
    if d.get("explain"):
        L.append(f"<i>{esc(d['explain'])}</i>")
    L.append("")
    L.append("<b>Пример:</b>")
    if d.get("present"):
        L.append(f"Настоящее время - {esc(d.get('present',''))}")
        if d.get("present_ru"):
            L.append(esc(d["present_ru"]))
    if d.get("past"):
        L.append(f"Прошедшее время - {esc(d.get('past',''))}")
        if d.get("past_ru"):
            L.append(esc(d["past_ru"]))
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML")
    # Сообщение 2: задание + кнопки
    L2 = ["<b>Задание:</b>", esc(d.get("task", "")), "", "Выбери вариант 👇"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(d.get("a", "A"), callback_data="gram_a"),
         InlineKeyboardButton(d.get("b", "B"), callback_data="gram_b")],
        [InlineKeyboardButton("🔄 Ещё пример", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L2), parse_mode="HTML", reply_markup=kb)

async def again_grammar(bot, cid, language):
    st = store.grammar_state.get(str(cid)) or {}
    await send_grammar(bot, cid, language, topic=st.get("topic"))

async def grammar_answer(bot, cid, chosen):
    st = store.grammar_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Задание устарело, запроси новое."); return
    if chosen == st["correct"]:
        await bot.send_message(chat_id=cid, text=f"✅ Верно!\n💡 {st.get('hint','')}")
    else:
        right = st["a"] if st["correct"] == "a" else st["b"]
        await bot.send_message(chat_id=cid, text=f"❌ Неверно. Правильно: {right}\n💡 {st.get('hint','')}")


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
def _verb_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё пример", callback_data=f"a_verb_{code}")],
        [InlineKeyboardButton("📖 Добавить в словарь", callback_data="a_addword")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])

def _proverb_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё пример", callback_data=f"a_proverb_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])

async def send_verb(bot, cid, language):
    out = ai.llm(f"Дай фразовый глагол дня для языка {language}. Формат: глагол - перевод, затем 1 пример с переводом. "
                 f"Коротко, эмодзи.", 300, 0.9, LO)
    store.last_word[str(cid)] = {"text": out, "lang": _code(language)}
    await bot.send_message(chat_id=cid, text=f"🔤 Глагол дня\n\n{out}", reply_markup=_verb_kb(_code(language)))

async def send_proverb(bot, cid, language):
    out = ai.llm(f"Дай пословицу/поговорку или разговорное выражение на языке {language}: оригинал + дословный перевод "
                 f"+ русский аналог по смыслу + 1 пример. Коротко, эмодзи.", 350, 0.95, LO)
    await bot.send_message(chat_id=cid, text=f"💬 Пословица\n\n{out}", reply_markup=_proverb_kb(_code(language)))


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

async def add_word(bot, cid):
    lw = store.last_word.get(str(cid))
    if not lw:
        await bot.send_message(chat_id=cid, text="Сначала открой «Глагол дня», потом «Добавить в словарь»."); return
    raw = lw["text"] if isinstance(lw, dict) else lw
    lang = lw.get("lang", "nl") if isinstance(lw, dict) else "nl"
    d = _normalize_word(raw, lang)
    store.add_to_list(config.DICT_KEY, cid, d)
    flag = "🇬🇧" if lang == "en" else "🇳🇱"
    await bot.send_message(chat_id=cid, text=f"📖 Добавлено в словарь: {flag} {d.get('word','')} - {d.get('ru','')}")

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

async def send_dict(bot, cid):
    words = store.get_list(config.DICT_KEY, cid)
    lines = ["🗂️ <b>Мой словарь</b>", ""]
    rows = [[InlineKeyboardButton("🇳🇱 Добавить нидерландское", callback_data="a_dictadd_nl")],
            [InlineKeyboardButton("🇬🇧 Добавить английское", callback_data="a_dictadd_en")]]
    if not words:
        lines.append("Пока пусто. Добавляй слова или сохраняй из «Глагол дня».")
    nl_words = [(i, w) for i, w in enumerate(words) if (w.get("lang") if isinstance(w, dict) else "nl") != "en"]
    en_words = [(i, w) for i, w in enumerate(words) if isinstance(w, dict) and w.get("lang") == "en"]
    if nl_words:
        lines.append("🇳🇱 <b>Нидерландские</b>")
        for i, w in nl_words[-20:]:
            lines.append(f"• {esc(_w_field(w,'word','nl'))} - {esc(_w_field(w,'ru'))}")
            rows.append([InlineKeyboardButton(f"❌ 🇳🇱 {_w_field(w,'word','nl')[:18]}", callback_data=f"worddel_{i}")])
    if en_words:
        lines.append("")
        lines.append("🇬🇧 <b>Английские</b>")
        for i, w in en_words[-20:]:
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
    diff_map = {"easy": "очень известный персонаж (можно из мультфильмов/фильмов), простые подсказки",
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
    ])
    clues = "\n".join(f"• {c.strip()}" for c in d.get("clues", "").split("\n") if c.strip())
    txt = f"<b>{ui['title']}</b>\n\n<b>{ui['suspect']}</b>\n{clues}\n\n{ui['who']} 🤔"
    await bot.send_message(chat_id=cid, text=txt, parse_mode="HTML", reply_markup=kb)

def _fuzzy(a, b):
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if abs(len(a) - len(b)) <= 2:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        return diff <= 2
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
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["again"], callback_data="game_again")]])
        body = st.get("explain") or st.get("quote", "")
        txt = f"{ui['found']}\n\n{ui['answer']}: <b>{esc(st['answer'])}</b>"
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