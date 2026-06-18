import re
import random
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


# ================= ГРАММАТИКА =================
def grammar_data(language, level):
    in_lang = _is_b1plus(level) and language == "нидерландский"
    lang_rule = ("Объяснение темы, пример и задание — ПОЛНОСТЬЮ на нидерландском (уровень требует погружения), "
                 "но добавь короткий перевод примера на русский." if in_lang
                 else "Объяснение простым русским, пример на изучаемом языке с переводом.")
    book = ("Ориентируйся на программу учебника TaalCompleet для нидерландского. " if language == "нидерландский" else "")
    prompt = f"""Грамматическое задание по языку {language}, уровень {level}. {book}
Выбери одну тему уровня {level}, каждый раз НОВУЮ. {lang_rule}
Покажи тему в настоящем и прошедшем времени рядом.
JSON:
{{
 "title": "название темы",
 "explain": "краткое объяснение простым языком, 2-3 строки",
 "example": "пример по теме на {language}",
 "example_ru": "перевод примера на русский",
 "present": "пример в настоящем времени на {language}",
 "present_ru": "перевод",
 "past": "тот же пример в прошедшем времени на {language}",
 "past_ru": "перевод",
 "task": "предложение по теме с одним пропуском ____ на {language}",
 "a": "вариант A",
 "b": "вариант B",
 "correct": "a или b",
 "hint": "подсказка-правило, 1 строка"
}}"""
    return ai.llm_json(prompt, 900, LO)

async def send_grammar(bot, cid, language, flag=None):
    level = store.get_level(cid, language)
    flag = flag or _flag(language)
    try:
        d = grammar_data(language, level)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.grammar_state[str(cid)] = {"correct": d.get("correct", "a"), "hint": d.get("hint", ""),
                                     "a": d.get("a", ""), "b": d.get("b", "")}
    code = _code(language)
    L = [f"📝 <b>Грамматика ({flag} {level})</b>", ""]
    L.append(f"<b>Тема:</b> {esc(d.get('title',''))}")
    if d.get("explain"):
        L.append(esc(d["explain"]))
    L.append("")
    if d.get("present"):
        L.append(f"<b>Настоящее время</b> — {esc(d.get('present',''))}")
        if d.get("present_ru"):
            L.append(esc(d["present_ru"]))
    if d.get("past"):
        L.append(f"<b>Прошедшее время</b> — {esc(d.get('past',''))}")
        if d.get("past_ru"):
            L.append(esc(d["past_ru"]))
    if d.get("example") and not d.get("present"):
        L.append(f"<b>Пример:</b> {esc(d.get('example',''))}")
        if d.get("example_ru"):
            L.append(esc(d["example_ru"]))
    L += ["", "<b>Задание:</b>", esc(d.get("task", "")), "", "Выбери вариант 👇"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(d.get("a", "A"), callback_data="gram_a"),
         InlineKeyboardButton(d.get("b", "B"), callback_data="gram_b")],
        [InlineKeyboardButton("🔄 Ещё пример", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("🆕 Новая тема", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)

async def grammar_answer(bot, cid, chosen):
    st = store.grammar_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Задание устарело, запроси новое."); return
    if chosen == st["correct"]:
        await bot.send_message(chat_id=cid, text=f"✅ Верно!\n💡 {st.get('hint','')}")
    else:
        right = st["a"] if st["correct"] == "a" else st["b"]
        await bot.send_message(chat_id=cid, text=f"❌ Неверно. Правильно: {right}\n💡 {st.get('hint','')}")


# ================= ПЕРЕВЕДИ ПРЕДЛОЖЕНИЕ =================
def generate_challenge(language, level):
    return ai.llm(f"Дай ОДНУ фразу на русском для перевода на {language}. Уровень {level}, бытовая/рабочая ситуация. "
                  f"Только русская фраза, без кавычек.", 200, 1.0, LO).strip()

def check_translation(language, ru, answer):
    return ai.llm_json(f"""Ученик переводит с русского на {language}.
Русская фраза: {ru}
Перевод ученика: {answer}
JSON:
{{"ok": true/false,
 "error": "в чём ошибка коротко по-русски (иначе пусто)",
 "correct": "правильный естественный вариант на {language}",
 "note": "короткое полезное правило/слово по-русски (иначе пусто)"}}""", 800, LO)

async def do_translate(bot, cid, lang):
    store.pending_input.pop(str(cid), None)  # снять чужой pending (фикс: ответ не уходит в дневник)
    level = store.get_level(cid, lang)
    try:
        ru = generate_challenge(lang, level)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.challenge_state[str(cid)] = {"ru": ru, "lang": lang}
    flag = _flag(lang)
    await bot.send_message(chat_id=cid,
        text=f"📝 <b>Переведи предложение ({flag} {level})</b>\n\nФраза: «{esc(ru)}»\n\nНапиши перевод на {lang} следующим сообщением.",
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
    flag = _flag(st["lang"])
    L = [f"📝 <b>Перевод ({flag})</b>", "", f"Твой ответ: {esc(text)}", ""]
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


# ================= ГЛАГОЛ ДНЯ / ПОСЛОВИЦА / ВЫРАЖЕНИЕ =================
def _word_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Добавить слово", callback_data="a_addword")],
        [InlineKeyboardButton("⭐ Добавить в избранное", callback_data="as_fav")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])

async def send_verb(bot, cid, language):
    out = ai.llm(f"Дай фразовый глагол дня для языка {language} (уровень {store.get_level(cid, language)}). "
                 f"Формат: глагол — перевод. Затем 1 пример с переводом. Коротко, эмодзи.", 300, 0.9, LO)
    store.last_word[str(cid)] = out
    await bot.send_message(chat_id=cid, text=f"🔤 Глагол дня\n\n{out}", reply_markup=_word_kb(_code(language)))

async def send_proverb(bot, cid, language):
    out = ai.llm(f"Дай пословицу/поговорку на языке {language} + дословный перевод + русский аналог по смыслу. "
                 f"Коротко, эмодзи.", 300, 0.95, LO)
    store.last_word[str(cid)] = out
    await bot.send_message(chat_id=cid, text=f"💬 Пословица\n\n{out}", reply_markup=_word_kb(_code(language)))

async def send_expression(bot, cid, language):
    out = ai.llm(f"Дай разговорное выражение дня на языке {language} + аналог/перевод на русский + 1 пример. Коротко.", 300, 0.95, LO)
    store.last_word[str(cid)] = out
    await bot.send_message(chat_id=cid, text=f"🗣 Выражение дня\n\n{out}", reply_markup=_word_kb(_code(language)))


# ================= СЛОВАРЬ =================
async def add_word(bot, cid):
    raw = store.last_word.get(str(cid))
    if not raw:
        await bot.send_message(chat_id=cid, text="Сначала открой слово/выражение, потом «Добавить слово»."); return
    try:
        d = ai.llm_json(f"Выдели главное слово/фразу из текста и переведи.\nТекст: {raw}\n"
                        'JSON: {"nl":"нидерландский с артиклем","ru":"русский","en":"английский"}', 300, LO)
    except Exception:
        d = {"nl": raw[:60], "ru": "", "en": ""}
    store.add_to_list(config.DICT_KEY, cid, d)
    await bot.send_message(chat_id=cid, text=f"📖 Добавлено в словарь: {d.get('nl','')} — {d.get('ru','')}")

async def send_dict(bot, cid):
    words = store.get_list(config.DICT_KEY, cid)
    if not words:
        await bot.send_message(chat_id=cid, text="🗂️ Словарь пуст. Открой слово дня и нажми «📖 Добавить слово».")
        return
    lines = ["🗂️ <b>Мой словарь</b>", ""]
    rows = []
    for i, w in enumerate(words[-30:]):
        nl = w.get("nl", "") if isinstance(w, dict) else str(w)
        ru = w.get("ru", "") if isinstance(w, dict) else ""
        lines.append(f"• {esc(nl)} — {esc(ru)}")
        rows.append([InlineKeyboardButton(f"❌ {i+1}", callback_data=f"worddel_{i}")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_learn")])
    await bot.send_message(chat_id=cid, text="\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))

async def del_word(bot, cid, i):
    words = store.get_list(config.DICT_KEY, cid)
    if i < len(words):
        words.pop(i)
        store.set_list(config.DICT_KEY, cid, words)
    await send_dict(bot, cid)


# ================= ПОДГОТОВКА К ЭКЗАМЕНУ =================
async def send_exam(bot, cid):
    await bot.send_message(chat_id=cid, text="Готовлю 20-минутный урок...")
    level = store.get_level(cid, "нидерландский")
    dict_words = store.get_list(config.DICT_KEY, cid)
    extra = ("Включи мои слова: " + ", ".join(w.get("nl", "") for w in dict_words[-5:] if isinstance(w, dict))) if dict_words else ""
    prompt = f"""Сделай 20-минутный урок нидерландского под экзамен Inburgering/B1 (стиль DUO). Уровень {level}.
Адаптация под СДВГ: микро-порции, динамика. {extra}
Формат, без markdown:
🎯 Урок на 20 минут

🗂 Карточки слов (3-5):
- слово — перевод
...
📌 Короткое правило:
{{1-2 строки}}
🧪 Мини-тест (3 вопроса с вариантами):
1) ... 
2) ...
3) ...
✅ Ответы: ..."""
    try:
        out = ai.llm(prompt, 1100, 0.7, LO)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=str(e)); return
    store.last_answer[str(cid)] = out
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Ещё урок", callback_data="a_exam")],
        [InlineKeyboardButton("⭐ Добавить в избранное", callback_data="as_fav")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_learn")],
    ])
    await send_long(bot, cid, out)
    await bot.send_message(chat_id=cid, text="Дальше 👇", reply_markup=kb)


# ================= ИГРА-ДЕТЕКТИВ =================
GAME_UI = {
    "русский": {"diff_q": "Выбери сложность:", "easy": "Лёгкая", "med": "Средняя", "hard": "Тяжёлая",
                "title": "🕵️ Игра-детектив", "who": "Кто это?", "hint": "💡 Подсказка", "reveal": "👁 Ответ",
                "again": "🕵️ Загадать ещё", "chdiff": "🎚 Сложность", "chlang": "🌐 Язык",
                "write": "Напиши ответ (любой язык, опечатка ок).", "correct": "✅ Верно!",
                "wrong": "❌ Не то", "retry": "Ещё попытка - напиши ответ или возьми подсказку.", "diffname": {"easy":"лёгкая","med":"средняя","hard":"тяжёлая"}},
    "английский": {"diff_q": "Choose difficulty:", "easy": "Easy", "med": "Medium", "hard": "Hard",
                "title": "🕵️ Detective Game", "who": "Who am I?", "hint": "💡 Hint", "reveal": "👁 Answer",
                "again": "🕵️ New character", "chdiff": "🎚 Difficulty", "chlang": "🌐 Language",
                "write": "Write your answer (any language, typo ok).", "correct": "✅ Correct!",
                "wrong": "❌ Not quite", "retry": "Try again - type a name or take a hint.", "diffname": {"easy":"easy","med":"medium","hard":"hard"}},
    "нидерландский": {"diff_q": "Kies moeilijkheid:", "easy": "Makkelijk", "med": "Gemiddeld", "hard": "Moeilijk",
                "title": "🕵️ Detectivespel", "who": "Wie ben ik?", "hint": "💡 Hint", "reveal": "👁 Antwoord",
                "again": "🕵️ Nog een", "chdiff": "🎚 Niveau", "chlang": "🌐 Taal",
                "write": "Schrijf je antwoord (elke taal, typefout ok).", "correct": "✅ Goed!",
                "wrong": "❌ Niet juist", "retry": "Nog een poging - typ een naam of neem een hint.", "diffname": {"easy":"makkelijk","med":"gemiddeld","hard":"moeilijk"}},
}

def game_data(clue_lang, difficulty, recent):
    diff_map = {"easy": "очень известный персонаж (можно из мультфильмов/фильмов), простые подсказки",
                "med": "сложнее: исторические личности, актёры, более тонкие подсказки",
                "hard": "редкие персонажи или абстрактные понятия, специфичная лексика, хитрые подсказки"}
    avoid = ("Не загадывай: " + ", ".join(recent[-30:])) if recent else ""
    prompt = f"""Игра-детектив. Загадай персонажа/личность (кино, мультфильмы, наука, история, музыка, литература).
Сложность: {diff_map.get(difficulty, diff_map['med'])}. ВЕСЬ текст загадки на языке: {clue_lang}. {avoid}
Ответь строго, каждое поле с новой строки, без markdown:
CLUES: 4 подсказки на языке {clue_lang}, через | , от непрямой к явной, без имени
ANSWER: имя (на языке {clue_lang})
ALIASES: то же имя на русском, английском и нидерландском через |
HINT: ещё одна явная подсказка на языке {clue_lang}
QUOTE: короткая фраза в духе персонажа на языке {clue_lang}"""
    raw = ai.llm(prompt, 800, 1.0, LO)
    out = {}
    for key, field in (("CLUES", "clues"), ("ANSWER", "answer"), ("ALIASES", "aliases"),
                       ("HINT", "hint"), ("QUOTE", "quote")):
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
                                  "quote": d.get("quote", ""), "hint": d.get("hint", ""), "tries": 0}
    diffname = ui["diffname"].get(cfg["difficulty"], "")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
         InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")],
        [InlineKeyboardButton(ui["chdiff"], callback_data="game_change_diff"),
         InlineKeyboardButton(ui["chlang"], callback_data="game_change")],
    ])
    await bot.send_message(chat_id=cid,
        text=f"{ui['title']}\n\n{diffname}\n\n{d.get('clues','')}\n\n{ui['who']}\n{ui['write']}",
        reply_markup=kb)

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
        rec = store.game_recent.get(str(cid), [])
        rec.append(st["answer"])
        store.game_recent[str(cid)] = rec[-30:]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["again"], callback_data="game_again")]])
        L = [ui["correct"], "", f"💬 {st.get('quote','')}", "", f"({st['answer']})"]
        await bot.send_message(chat_id=cid, text="\n".join(L), reply_markup=kb)
        return True
    st["tries"] = st.get("tries", 0) + 1
    if st["tries"] >= 2:
        store.game_state.pop(str(cid), None)
        rec = store.game_recent.get(str(cid), [])
        rec.append(st["answer"])
        store.game_recent[str(cid)] = rec[-30:]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["again"], callback_data="game_again")]])
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}. {st['answer']}.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
                                    InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")]])
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}. {ui['retry']}", reply_markup=kb)
    return True


# ================= УРОВЕНЬ =================
async def send_levels(bot, cid):
    nl_lvl, en_lvl = store.get_level(cid, "нидерландский"), store.get_level(cid, "английский")
    kb_nl = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_nl_{l}") for l in LEVELS]])
    kb_en = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_en_{l}") for l in LEVELS]])
    await bot.send_message(chat_id=cid, text=f"🇳🇱 Уровень нидерландского (сейчас {nl_lvl}):", reply_markup=kb_nl)
    await bot.send_message(chat_id=cid, text=f"🇬🇧 Уровень английского (сейчас {en_lvl}):", reply_markup=kb_en)