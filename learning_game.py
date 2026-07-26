"""Языковая игра-детектив: состояние, генерация, ответы и подсказки."""

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import secure
import store
import verify
from ui import learning as learning_ui
from ui.navigation import back_menu_keyboard


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


# ================= ИГРА-ДЕТЕКТИВ =================
# Служебные заголовки локализованы под активный язык обучения —
# улики и служебный UI на одном языке, а не в смеси.
GAME_UI = {
    "русский": {
        "title": "Детектив",
        "who": "Кто это?",
        "hint": "💡 Подсказка",
        "hint_title": "💡 Подсказка",
        "reveal": "😞 Сдаюсь",
        "suspect": "Подозреваемый:",
        "found": "✅ Дело раскрыто!",
        "answer": "Ответ",
        "explain": "Почему:",
        "again": "✨ Ещё",
        "back": "⬅️ Назад",
        "home": "#️⃣ Главная",
        "nohint": "Подсказок больше нет.",
        "wrong": "❌ Не то",
        "retry": "Ещё попытка - напиши ответ или возьми подсказку.",
    },
    "английский": {
        "title": "Detective",
        "who": "Who am I?",
        "hint": "💡 Подсказка",
        "hint_title": "💡 Hint",
        "reveal": "😞 Сдаюсь",
        "suspect": "Suspect:",
        "found": "✅ Case solved!",
        "answer": "Answer",
        "explain": "Why:",
        "again": "✨ Ещё",
        "back": "⬅️ Назад",
        "home": "#️⃣ Главная",
        "nohint": "No more hints.",
        "wrong": "❌ Not quite",
        "retry": "One more try - write the answer or take a hint.",
    },
    "нидерландский": {
        "title": "Detective",
        "who": "Wie ben ik?",
        "hint": "💡 Подсказка",
        "hint_title": "💡 Hint",
        "reveal": "😞 Сдаюсь",
        "suspect": "Verdachte:",
        "found": "✅ Zaak opgelost!",
        "answer": "Antwoord",
        "explain": "Waarom:",
        "again": "✨ Ещё",
        "back": "⬅️ Назад",
        "home": "#️⃣ Главная",
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


_GAME_SIGNATURE_MARKERS = (
    "ус", "whisker", "snorhaar", "snorhar", "муз", "mouse", "mice", "muis", "хобот", "trunk", "slurf", "грива", "mane", "manen",
    "паутина", "web", "spinnen", "волшебн", "wand", "toverstaf", "зелён", "зелен", "green", "groen",
    "болот", "swamp", "moeras", "крыл", "wing", "vleugel", "чёрно-бел", "черно-бел", "black and white", "zwart-wit",
    "лёд", "лед", "ice", "ijs", "мёд", "мед", "honey", "honing", "маск", "mask", "gotham", "готэм",
    "hogwarts", "хогвартс", "arendelle", "аренд", "fiona", "фиона", "minnie", "минни", "mufasa", "муфаса",
    "дональд", "donald", "красн.*шорт", "red shorts", "rode broek", "осёл", "осел", "donkey",
)
_GAME_VAGUE_CLUE_MARKERS = (
    "активен ночью", "active at night", "'s nachts actief", "большие глаза", "big eyes", "grote ogen",
    "любит рыбу", "likes fish", "houdt van vis", "известн.*друг", "известн.*подруг", "known friend",
    "bekend vriend", "bekend vriendinnetje",
)


def _clues_are_guessable(data):
    """Отбрасывает наборы, где нет отличительного признака ответа."""
    clues = [str(clue or "").strip() for clue in str(data.get("clues") or "").splitlines() if str(clue or "").strip()]
    if len(clues) < 4:
        return False
    text = " ".join(clues).casefold()
    vague_count = sum(bool(re.search(marker, text)) for marker in _GAME_VAGUE_CLUE_MARKERS)
    signature_count = sum(bool(re.search(marker, text)) for marker in _GAME_SIGNATURE_MARKERS)
    if signature_count < 1:
        return False
    # Две и более универсальные характеристики без уникальной концовки делают
    # задачу угадыванием наугад. Последняя улика обязана нести отличительный факт.
    last = clues[-1].casefold()
    last_has_signature = any(re.search(marker, last) for marker in _GAME_SIGNATURE_MARKERS)
    return vague_count < 2 or last_has_signature


def game_data(clue_lang, recent, attempt=0):
    subject = ("только очень известное животное (например, кошка, собака, лев, слон, жираф, "
               "обезьяна, пингвин, акула или дельфин) ИЛИ очень известного героя мультфильма или кино "
               "(например, Микки Маус, Гарри Поттер, Человек-паук, Бэтмен, Шрек, Симба, "
               "Эльза или Винни-Пух). Выбирай только героя, которого узнает почти любой человек. "
               "Не загадывай предметы, растения, знаменитостей, исторических людей, редких персонажей "
               "или абстрактные понятия.")
    diff_desc = ("уровень языка A1–A2: только короткие простые предложения и самые частые слова. "
                 "Каждая улика должна прямо описывать цвет, размер, звук, место, действие или известную роль. "
                 "Загадка должна быть очень лёгкой: ответ можно уверенно угадать по третьей или четвёртой улике. "
                 "Не используй метафоры, идиомы, редкие слова, сложные времена или длинные описания")
    avoid = ("Не загадывай ничего из этого списка и их переводы/синонимы: " + ", ".join(recent[-80:])) if recent else ""
    prompt = f"""Игра-детектив. Загадай: {subject}.
Сложность: {diff_desc}. ВЕСЬ текст на языке: {clue_lang}. {avoid}
Для лёгкого режима особенно важно: не усложняй загадку ради интриги, не выбирай редкий вариант и не скрывай ответ сложными словами.
Попытка генерации: {attempt + 1}. Если сомневаешься, выбирай менее очевидный вариант, которого не было в списке.
Каждая подсказка и каждое предложение заканчивается точкой.
Стиль: улики должны быть короткими, конкретными и честными, а не туманными.
Порядок обязателен: 1) простая сцена или действие, 2) заметный внешний признак, 3) отличительная привычка/способность/роль, 4) почти очевидная уникальная деталь.
Минимум одна из последних двух улик обязана содержать узнаваемый признак: особый предмет, место, имя друга, способность, костюм, родственника или известную роль.
Не пиши «активен ночью», «у него большие глаза», «любит рыбу», «у него есть известный друг/подруга» без дополнительной уникальной детали — это слишком расплывчато.
Последняя улика должна позволить догадаться почти сразу, но не должна содержать само название ответа.
Не повторяй одинаковые формулировки между уликами.
Ответь строго, каждое поле с новой строки, без markdown:
CLUES: 4 улики на языке {clue_lang}, через | , от косвенной к более явной — конкретные детали (форма, цвет, происхождение, функция, ощущения), без имени/названия
ANSWER: название на языке {clue_lang}
ALIASES: то же название на русском, английском и нидерландском через |
ENGLISH: название ответа на английском, только 1–4 слова
HINT: ещё одна явная подсказка на языке {clue_lang}
HINT2: совсем простая, почти очевидная подсказка (но без названия), на языке {clue_lang}
EXPLAIN: 2 живых предложения — что это такое и почему улики вели именно к нему (на языке {clue_lang})"""
    raw = ai.llm(prompt, 900, 1.0, tier="cheap")
    out = {}
    for key, field in (("CLUES", "clues"), ("ANSWER", "answer"), ("ALIASES", "aliases"),
                       ("ENGLISH", "answer_en"),
                       ("HINT", "hint"), ("HINT2", "hint2"), ("EXPLAIN", "explain")):
        m = re.search(rf"{key}:\s*(.+?)(?=\n[A-Z]+\d*:|\Z)", raw, re.S)
        out[field] = m.group(1).strip() if m else ""
    out["clues"] = out.get("clues", "").replace(" | ", "\n").replace("|", "\n")
    out["aliases"] = [x.strip() for x in out.get("aliases", "").split("|") if x.strip()]
    return out

async def start(bot, cid, status=None):
    store.challenge_state.pop(str(cid), None)
    lang = _language_for_code(_active_language_code(cid))
    store.game_config[str(cid)] = {"lang": lang}
    await send_game(bot, cid, status=status)

async def send_game(bot, cid, status=None):
    store.challenge_state.pop(str(cid), None)   # фикс: чтобы перевод не перехватывал
    cfg = store.game_config.get(str(cid), {"lang": "английский"})
    lang = cfg.get("lang", "английский")
    ui = _game_ui(lang)
    recent = _game_recent(cid)
    try:
        d = {}
        for attempt in range(5):
            cand = game_data(lang, recent, attempt=attempt)
            if cand.get("answer") and _clues_are_guessable(cand) and not _game_is_recent(cand, recent):
                d = cand
                break
            if cand.get("answer"):
                recent = recent + [cand.get("answer", "")] + list(cand.get("aliases") or [])
        if not d:
            text = "Не смог загадать новое без повтора. Попробуй ещё раз через минуту."
            kb = back_menu_keyboard("m_learn")
            if status is not None:
                await status.replace(text, reply_markup=kb)
            else:
                await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
            return
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_learn"); return
    _remember_game_answer(cid, d)
    hints = [_dot(h) for h in [d.get("hint"), d.get("hint2")] if (h or "").strip()]
    store.game_state[str(cid)] = {"answer": d.get("answer", ""), "answer_en": d.get("answer_en", ""),
                                  "aliases": d.get("aliases", []),
                                  "quote": d.get("quote", ""), "hints": hints, "hint_i": 0,
                                  "explain": _dot(d.get("explain", "")), "tries": 0}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["hint"], callback_data="game_hint"),
         InlineKeyboardButton(ui["reveal"], callback_data="game_reveal")],
        [InlineKeyboardButton(ui["back"], callback_data="m_learn"), InlineKeyboardButton(ui["home"], callback_data="m_menu")],
    ])
    clues = "\n".join(f"• {c.strip()}" for c in d.get("clues", "").split("\n") if c.strip())
    msg = learning_ui.game_card(ui, clues)
    if status is not None:
        await status.replace(msg.text, entities=msg.entities, reply_markup=kb)
    else:
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


def _get_english_query(st):
    """Возвращает только английское название ответа для поиска фото."""
    explicit = str(st.get("answer_en") or "").strip()
    if explicit and re.search(r"[A-Za-z]", explicit):
        return explicit

    # Старые состояния не имели отдельного answer_en. По контракту генератора
    # второй alias — английский: русский | английский | нидерландский.
    aliases = [str(alias or "").strip() for alias in (st.get("aliases") or [])]
    if len(aliases) > 1 and re.search(r"[A-Za-z]", aliases[1]):
        return aliases[1]
    if re.search(r"[A-Za-z]", str(st.get("answer") or "")):
        return str(st.get("answer") or "").strip()
    for alias in aliases:
        if re.search(r"[A-Za-z]", alias):
            return alias
    return ""


async def _send_game_result(bot, cid, st, ui, kb):
    import travel_photos
    body = st.get("explain") or st.get("quote", "")
    msg = learning_ui.game_found(ui, st.get("answer", ""), body)
    query = _get_english_query(st)
    photo = None
    if query:
        try:
            photo = travel_photos.find_illustration(query)
        except Exception:
            pass
    if (_is_landscape_photo(photo)):
        try:
            await bot.send_photo(
                chat_id=cid,
                photo=photo["url"],
                caption=msg.text,
                caption_entities=msg.entities,
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def _is_landscape_photo(photo):
    """Детектив показывает только горизонтальный кадр с одним объектом."""
    if not isinstance(photo, dict) or not photo.get("url"):
        return False
    try:
        return int(photo.get("width") or 0) > int(photo.get("height") or 0) > 0
    except (TypeError, ValueError):
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
             InlineKeyboardButton(ui["home"], callback_data="m_menu")],
        ])
        await _send_game_result(bot, cid, st, ui, kb)
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
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(ui["again"], callback_data="game_again")],
        [InlineKeyboardButton(ui["back"], callback_data="m_learn"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    try:
        await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await _send_game_result(bot, cid, st, ui, kb)
