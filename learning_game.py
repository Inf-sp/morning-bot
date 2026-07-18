"""Языковая игра-детектив: состояние, генерация, ответы и подсказки."""

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import secure
import store
import verify
from ui import learning as learning_ui
from ui.navigation import back_menu_keyboard


LEVEL_LABELS = {"simple": "Простой", "medium": "Средний", "hard": "Сложный"}


def _code(language):
    if language in ("nl", "en"):
        return language
    return "nl" if language == "нидерландский" else "en"


def _language_for_code(code):
    return "английский" if code == "en" else "нидерландский"


def _active_language_code(cid):
    code = store.get_learning_language(cid)
    if code in ("nl", "en"):
        return code
    import settings
    return _code(settings.study_lang(cid))


def _flag(language):
    return "🇳🇱" if _code(language) == "nl" else "🇬🇧"


def _level_label(level):
    return LEVEL_LABELS.get(level, "Средний")
# ================= ИГРА-ДЕТЕКТИВ =================
# Служебные заголовки локализованы под язык игры (см. game_lang_kb/gamelang_*) —
# улики и служебный UI на одном языке, а не в смеси.
GAME_UI = {
    "русский": {
        "diff_q": "Выбери сложность:",
        "easy": "Лёгкая",
        "hard": "Тяжёлая",
        "title": "Игра-детектив",
        "who": "Кто это?",
        "hint": "💡 Подсказка",
        "reveal": "😞 Сдаюсь",
        "suspect": "Подозреваемый:",
        "found": "✅ Дело раскрыто!",
        "answer": "Ответ",
        "analyse": "Анализ:",
        "again": "✨ Ещё",
        "back": "⬅️ Назад",
        "nohint": "Подсказок больше нет.",
        "wrong": "❌ Не то",
        "retry": "Ещё попытка - напиши ответ или возьми подсказку.",
    },
    "английский": {
        "diff_q": "Choose difficulty:",
        "easy": "Easy",
        "hard": "Hard",
        "title": "Detective Game",
        "who": "Who am I?",
        "hint": "💡 Hint",
        "reveal": "😞 Give up",
        "suspect": "Suspect:",
        "found": "✅ Case solved!",
        "answer": "Answer",
        "analyse": "Analysis:",
        "again": "✨ Again",
        "back": "⬅️ Back",
        "nohint": "No more hints.",
        "wrong": "❌ Not quite",
        "retry": "One more try - write the answer or take a hint.",
    },
    "нидерландский": {
        "diff_q": "Kies de moeilijkheidsgraad:",
        "easy": "Makkelijk",
        "hard": "Moeilijk",
        "title": "Detectivespel",
        "who": "Wie ben ik?",
        "hint": "💡 Hint",
        "reveal": "😞 Opgeven",
        "suspect": "Verdachte:",
        "found": "✅ Zaak opgelost!",
        "answer": "Antwoord",
        "analyse": "Analyse:",
        "again": "✨ Nog een",
        "back": "⬅️ Terug",
        "nohint": "Geen hints meer.",
        "wrong": "❌ Niet juist",
        "retry": "Nog een poging - schrijf het antwoord of neem een hint.",
    },
}

def _game_ui(lang=None):
    return GAME_UI.get(lang) or GAME_UI["русский"]


def _dot(s):
    """Гарантирует точку в конце предложения/подсказки."""
    s = (s or "").strip()
    if s and s[-1] not in ".!?…:":
        s += "."
    return s


def _game_norm(s):
    return re.sub(r"[^0-9a-zа-яё]+", "", (s or "").lower())


def _game_same(a, b):
    a, b = _game_norm(a), _game_norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5 and (a in b or b in a):
        return True
    if abs(len(a) - len(b)) <= 2:
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        return diff <= 2
    return False


def _game_is_recent(d, recent):
    names = [d.get("answer", "")] + list(d.get("aliases") or [])
    return any(_game_same(name, old) for name in names for old in (recent or []))


def _game_recent(cid):
    prof = store.get_profile(cid)
    persisted = prof.get("game_recent", []) if isinstance(prof, dict) else []
    mem = store.game_recent.get(str(cid), [])
    out = []
    for name in list(persisted) + list(mem):
        name = (name or "").strip()
        if name and not any(_game_same(name, old) for old in out):
            out.append(name)
    out = out[-80:]
    store.game_recent[str(cid)] = out
    return out


def _set_game_recent(cid, rec):
    rec = [str(x).strip() for x in (rec or []) if str(x).strip()]
    rec = rec[-80:]
    store.game_recent[str(cid)] = rec
    prof = store.get_profile(cid)
    prof["game_recent"] = rec
    store.set_profile(cid, prof)


def _remember_game_answer(cid, d):
    names = [d.get("answer", "")] + list(d.get("aliases") or [])
    rec = _game_recent(cid)
    for name in names:
        name = (name or "").strip()
        if name and not any(_game_same(name, old) for old in rec):
            rec.append(name)
    _set_game_recent(cid, rec)


def game_data(clue_lang, difficulty, recent, attempt=0):
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
    avoid = ("Не загадывай ничего из этого списка и их переводы/синонимы: " + ", ".join(recent[-80:])) if recent else ""
    prompt = f"""Игра-детектив. Загадай: {subject}.
Сложность: {diff_desc}. ВЕСЬ текст на языке: {clue_lang}. {avoid}
Попытка генерации: {attempt + 1}. Если сомневаешься, выбирай менее очевидный вариант, которого не было в списке.
Каждая подсказка и каждое предложение заканчивается точкой.
Стиль: улики должны быть атмосферными и чуть кинематографичными, но короткими. Не сухой список фактов.
Добавь 1 деталь действия/сцены в каждой улике: след, привычка, жест, звук, место, предмет, последствия.
Не повторяй одинаковые формулировки между уликами.
Ответь строго, каждое поле с новой строки, без markdown:
CLUES: 4 улики на языке {clue_lang}, через | , от косвенной к более явной — конкретные детали (форма, цвет, происхождение, функция, ощущения), без имени/названия
ANSWER: название на языке {clue_lang}
ALIASES: то же название на русском, английском и нидерландском через |
HINT: ещё одна явная подсказка на языке {clue_lang}
HINT2: совсем простая, почти очевидная подсказка (но без названия), на языке {clue_lang}
EXPLAIN: 2 живых предложения — что это такое и почему улики вели именно к нему (на языке {clue_lang})"""
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
    msg = learning_ui.game_start()
    await bot.send_message(chat_id=cid, text=msg.text, reply_markup=game_lang_kb())

async def ask_difficulty(bot, cid, lang):
    ui = _game_ui(lang)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["easy"], callback_data="gamediff_easy")],
        [InlineKeyboardButton(ui["hard"], callback_data="gamediff_hard")],
    ])
    await bot.send_message(chat_id=cid, text=ui["diff_q"], reply_markup=kb)

async def send_game(bot, cid):
    store.challenge_state.pop(str(cid), None)   # фикс: чтобы перевод не перехватывал
    cfg = store.game_config.get(str(cid), {"lang": "английский", "difficulty": "easy"})
    lang = cfg["lang"]
    ui = _game_ui(lang)
    recent = _game_recent(cid)
    try:
        d = {}
        for attempt in range(5):
            cand = game_data(lang, cfg["difficulty"], recent, attempt=attempt)
            if cand.get("answer") and not _game_is_recent(cand, recent):
                d = cand
                break
            if cand.get("answer"):
                recent = recent + [cand.get("answer", "")] + list(cand.get("aliases") or [])
        if not d:
            await bot.send_message(
                chat_id=cid,
                text="Не смог загадать новое без повтора. Попробуй ещё раз через минуту.",
                reply_markup=back_menu_keyboard("m_learn"),
            )
            return
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_learn"); return
    _remember_game_answer(cid, d)
    hints = [_dot(h) for h in [d.get("hint"), d.get("hint2")] if (h or "").strip()]
    store.game_state[str(cid)] = {"answer": d.get("answer", ""), "aliases": d.get("aliases", []),
                                  "quote": d.get("quote", ""), "hints": hints, "hint_i": 0,
                                  "explain": _dot(d.get("explain", "")), "tries": 0}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
         InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="game_change"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    clues = "\n".join(f"• {c.strip()}" for c in d.get("clues", "").split("\n") if c.strip())
    msg = learning_ui.game_card(ui, clues)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)

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
    ui = _game_ui(cfg["lang"])
    guess = text.lower().strip()
    names = [st["answer"]] + st.get("aliases", [])
    pool = []
    for n in names:
        n = (n or "").lower().strip()
        pool += [n] + n.split()
    correct = any(_fuzzy(guess, p) for p in pool if p)
    if correct:
        store.game_state.pop(str(cid), None)
        _remember_game_answer(cid, st)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(ui["again"], callback_data="game_again")],
            [InlineKeyboardButton(ui["back"], callback_data="m_learn"),
             InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
        ])
        body = st.get("explain") or st.get("quote", "")
        msg = learning_ui.game_found(ui, st["answer"], body)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return True
    st["tries"] = st.get("tries", 0) + 1
    if st["tries"] >= 2:
        store.game_state.pop(str(cid), None)
        _remember_game_answer(cid, st)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["again"], callback_data="game_again")]])
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}. {st['answer']}.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
                                    InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")]])
        await bot.send_message(chat_id=cid, text=f"{ui['wrong']}. {ui['retry']}", reply_markup=kb)
    return True


async def game_hint(bot, cid, q):
    st = store.game_state.get(str(cid))
    ui = _game_ui(store.game_config.get(str(cid), {}).get("lang", "русский"))
    hints = (st or {}).get("hints") or []
    i = (st or {}).get("hint_i", 0)
    if st and i < len(hints):
        st["hint_i"] = i + 1
        msg = learning_ui.game_hint(ui, hints[i])
        await q.message.reply_text(msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
    else:
        await q.message.reply_text(ui["nohint"])


async def game_reveal(bot, cid, q):
    st = store.game_state.pop(str(cid), None)
    ui = _game_ui(store.game_config.get(str(cid), {}).get("lang", "русский"))
    if not st:
        return
    _remember_game_answer(cid, st)
    body = st.get("explain") or st.get("quote", "")
    msg = learning_ui.game_found(ui, st.get("answer", ""), body)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["again"], callback_data="game_again")],
        [InlineKeyboardButton(ui["back"], callback_data="m_learn"),
         InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    try:
        await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
