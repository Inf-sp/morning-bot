import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
import ai
from util import esc, send_long

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

# --- Грамматика ---
def grammar_data(language, level="B1"):
    prompt = f"""Грамматическое задание по языку {language} для ученика уровня {level}.
Выбери одну тему уровня {level} (каждый раз разную). Предложение с ОДНИМ пропуском.
JSON:
{{
 "rule_title": "короткое название темы по-русски",
 "rule": "объяснение простым языком, 2-3 строки, по-русски",
 "sentence": "предложение на {language} с пропуском ____",
 "a": "вариант A (одно слово)",
 "b": "вариант B (одно слово)",
 "correct": "a или b",
 "why": "одна строка почему, по-русски"
}}"""
    return ai.llm_json(prompt, 800)

async def send_grammar(bot, cid, language, flag):
    level = store.get_level(cid, language)
    d = grammar_data(language, level)
    store.grammar_state[str(cid)] = {"correct": d.get("correct", "a"), "why": d.get("why", ""),
                                     "a": d.get("a", ""), "b": d.get("b", "")}
    code = "nl" if language == "нидерландский" else "en"
    text = (f"📖 {flag} Грамматика ({level})\n\n{d.get('rule_title','')}\n{d.get('rule','')}\n\n"
            f"Заполни пропуск:\n{d.get('sentence','')}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(d.get("a", "A"), callback_data="gram_a"),
         InlineKeyboardButton(d.get("b", "B"), callback_data="gram_b")],
        [InlineKeyboardButton("➕ Ещё пример", callback_data=f"again_gram_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)

async def grammar_answer(bot, cid, chosen):
    st = store.grammar_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Задание устарело, запроси новое.")
        return
    if chosen == st["correct"]:
        await bot.send_message(chat_id=cid, text=f"✅ Верно! {st['why']}")
    else:
        right = st["a"] if st["correct"] == "a" else st["b"]
        await bot.send_message(chat_id=cid, text=f"❌ Нет. Правильно: {right}\n{st['why']}")

# --- Тренировка (перевод) ---
def generate_challenge(language, level="B1"):
    prompt = f"""Дай ОДНУ фразу на русском для перевода на {language}.
Уровень {level}, бытовая или рабочая ситуация. С заглавной буквы и точкой в конце.
Выведи ТОЛЬКО русскую фразу, без кавычек."""
    return ai.llm(prompt, 200, 1.0).strip()

def check_translation(language, ru, answer):
    prompt = f"""Ученик переводит с русского на {language}.
Русская фраза: {ru}
Перевод ученика: {answer}
JSON:
{{
 "ok": true/false,
 "error": "в чём ошибка коротко по-русски (иначе пусто)",
 "correct": "правильный естественный вариант на {language}",
 "simple": ["1-3 коротких пункта по-русски"],
 "easier": "более простой вариант на {language} (иначе пусто)"
}}"""
    return ai.llm_json(prompt, 800)

async def do_translate(bot, cid, lang):
    level = store.get_level(cid, lang)
    try:
        ru = generate_challenge(lang, level)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    store.challenge_state[str(cid)] = {"ru": ru, "lang": lang}
    flag = "🇳🇱" if lang == "нидерландский" else "🇬🇧"
    await bot.send_message(chat_id=cid,
        text=f"{flag} Тренировка ({level})\n\nПереведи на {lang}:\n«{ru}»\n\nНапиши перевод на {lang} следующим сообщением.")

async def translate_answer(bot, cid, text):
    st = store.challenge_state.pop(str(cid), None)
    if not st:
        return False
    await bot.send_message(chat_id=cid, text="Проверяю...")
    try:
        r = check_translation(st["lang"], st["ru"], text)
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка проверки: {e}")
        return True
    flag = "🇳🇱" if st["lang"] == "нидерландский" else "🇬🇧"
    L = [f"{flag} Перевод"]
    if r.get("ok"):
        L += ["", "✅ Верно!"]
        if r.get("correct"):
            L += ["", "💡 Естественнее", r["correct"]]
    else:
        if r.get("error"):
            L += ["", "❌ Ошибка", r["error"]]
        if r.get("correct"):
            L += ["", "💡 Правильно", r["correct"]]
    simple = r.get("simple") or []
    if simple:
        L += ["", "🧠 Просто"] + [f"• {x}" for x in simple]
    if r.get("easier"):
        L += ["", "✔️ Можно проще", r["easier"]]
    arg = "tr_en" if st["lang"] == "английский" else "tr_nl"
    code = "en" if st["lang"] == "английский" else "nl"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Ещё фраза", callback_data=f"again_{arg}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text="\n".join(L), reply_markup=kb)
    return True

# --- Игра-детектив ---
def game_data(clue_lang, difficulty):
    diff_map = {"easy": "очень известный персонаж, подсказки простые",
                "med": "известный персонаж, подсказки средней сложности",
                "hard": "менее очевидный персонаж, подсказки хитрые и непрямые"}
    diff = diff_map.get(difficulty, diff_map["med"])
    prompt = f"""Игра-детектив: загадай персонажа или личность (кино, наука, история, музыка, литература).
Сложность: {diff}. Язык подсказок: {clue_lang}.
Ответь строго в формате, каждое поле с новой строки, без markdown, без кавычек:

CLUES: 3-4 подсказки на языке {clue_lang}, через знак | , от непрямой к явной, без имени
ANSWER: имя
HINT: ещё одна явная подсказка на языке {clue_lang}
QUOTE: короткая дерзкая или смешная фраза в духе персонажа на языке {clue_lang}
QUOTE_RU: перевод фразы на русский"""
    raw = ai.llm(prompt, 800, 0.9)
    out = {}
    for key, field in (("CLUES", "clues"), ("ANSWER", "answer"), ("HINT", "hint"),
                       ("QUOTE", "quote"), ("QUOTE_RU", "quote_ru")):
        m = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z_]+:|\Z)", raw, re.S)
        out[field] = m.group(1).strip() if m else ""
    out["clues"] = out.get("clues", "").replace(" | ", "\n").replace("|", "\n")
    return out

def game_lang_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇷🇺 Русский", callback_data="gamelang_ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="gamelang_en"),
        InlineKeyboardButton("🇳🇱 Nederlands", callback_data="gamelang_nl"),
    ]])

async def game_start(bot, cid):
    await bot.send_message(chat_id=cid, text="🕵️ Игра-детектив. На каком языке подсказки?",
                           reply_markup=game_lang_kb())

async def send_game(bot, cid):
    cfg = store.game_config.get(str(cid), {"lang": "нидерландский", "difficulty": "med"})
    try:
        d = game_data(cfg["lang"], cfg["difficulty"])
    except Exception as e:
        await bot.send_message(chat_id=cid, text=f"Ошибка: {e}")
        return
    store.game_state[str(cid)] = {"answer": d.get("answer", ""), "quote": d.get("quote", ""),
                                  "quote_ru": d.get("quote_ru", ""), "hint": d.get("hint", ""), "tries": 0}
    diff_ru = {"easy": "лёгкая", "med": "средняя", "hard": "тяжёлая"}.get(cfg["difficulty"], "средняя")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💡 Подсказка", callback_data="game_hint"),
         InlineKeyboardButton("👁 Ответ", callback_data="game_reveal")],
        [InlineKeyboardButton("🔁 Сменить", callback_data="game_change"),
         InlineKeyboardButton("⬅️ Назад", callback_data="m_lang")],
    ])
    await bot.send_message(chat_id=cid,
        text=f"🕵️ Детектив ({cfg['lang']}, {diff_ru})\n\n{d.get('clues','')}\n\nНапиши имя. Можно на любом языке, опечатка ок.",
        reply_markup=kb)

async def game_answer(bot, cid, text):
    st = store.game_state.get(str(cid))
    if not st:
        return False
    ans = st["answer"].lower().strip()
    guess = text.lower().strip()
    def close(a, b):
        if a in b or b in a:
            return True
        if abs(len(a) - len(b)) <= 1:
            diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
            return diff <= 1
        return False
    correct = any(close(guess, part) for part in [ans] + ans.split())
    if correct:
        store.game_state.pop(str(cid), None)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕵️ Загадать ещё", callback_data="game_again")]])
        L = ["✅ Верно!", "", f"💬 {st.get('quote','')}"]
        if st.get("quote_ru"):
            L.append(f"<i>{esc(st['quote_ru'])}</i>")
        await bot.send_message(chat_id=cid, text="\n".join(L), parse_mode="HTML", reply_markup=kb)
        return True
    st["tries"] = st.get("tries", 0) + 1
    if st["tries"] >= 2:
        store.game_state.pop(str(cid), None)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🕵️ Загадать ещё", callback_data="game_again")]])
        await bot.send_message(chat_id=cid, text=f"❌ Не угадал. Это {st['answer']}.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💡 Подсказка", callback_data="game_hint"),
             InlineKeyboardButton("👁 Ответ", callback_data="game_reveal")]])
        await bot.send_message(chat_id=cid, text="❌ Не то. Ещё попытка - напиши имя или возьми подсказку.", reply_markup=kb)
    return True

async def send_levels(bot, cid):
    nl_lvl, en_lvl = store.get_level(cid, "нидерландский"), store.get_level(cid, "английский")
    kb_nl = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_nl_{l}") for l in LEVELS]])
    kb_en = InlineKeyboardMarkup([[InlineKeyboardButton(l, callback_data=f"lvl_en_{l}") for l in LEVELS]])
    await bot.send_message(chat_id=cid, text=f"🇳🇱 Уровень нидерландского (сейчас {nl_lvl}):", reply_markup=kb_nl)
    await bot.send_message(chat_id=cid, text=f"🇬🇧 Уровень английского (сейчас {en_lvl}):", reply_markup=kb_en)
