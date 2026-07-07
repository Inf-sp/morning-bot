import asyncio
import re
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
from cleanup import open_cleanup, send_cleanup, handle_cleanup  # noqa: F401

_HERE = Path(__file__).parent
import store
import ai
import verify
import secure
from ui import dictionary as dict_ui
from ui import learning as learning_ui

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


def _train_phrases(cid, language):
    """Только записи kind=phrase нужного языка из словаря с переводом: [(phrase, ru), ...]."""
    code = _code(language)
    out = []
    for w in _ensure_dict(cid):
        if _dict_lang(w) == code and _dict_kind(w) == "phrase":
            phrase = _w_field(w, "word", "nl", "en")
            ru = _w_field(w, "ru")
            if phrase and ru:
                out.append((str(phrase).strip(), str(ru).strip()))
    return out


def _should_train_new_word(round_no):
    """30% новых слов: 3, 6 и 9 раунды в каждом блоке из 10."""
    try:
        return int(round_no) % 10 in {2, 5, 8}
    except Exception:
        return False


async def _gen_train_new_word(cid, language, words, used_new):
    """Новое частотное слово выше B1, близкое к уже добавленному словарю."""
    existing = {str(w).strip().lower() for w, _ in words}
    blocked = existing | {str(w).strip().lower() for w in (used_new or [])}
    anchors = ", ".join(w for w, _ in words[:40])
    lang_code = _code(language)
    prompt = (
        f"Язык: {language} ({lang_code}).\n"
        f"Слова из словаря пользователя: {anchors}.\n"
        "Подбери РОВНО ОДНО новое слово для тренажёра.\n"
        "Требования:\n"
        "1. Слово должно быть частотным в реальной живой речи.\n"
        "2. Уровень выше B1: B2 или аккуратный C1, но без книжной редкости.\n"
        "3. Оно должно быть тематически или семантически связано с уже добавленными словами.\n"
        "4. Не возвращай фразы, имена, бренды, артикли и формы спряжения.\n"
        f"5. Не повторяй эти слова: {', '.join(sorted(blocked))[:700]}.\n"
        'JSON: {"word": "слово", "ru": "короткий перевод на русский"}'
    )
    try:
        d = await ai.allm_json(prompt, 350, tier="smart", route="gemini", module="learning")
    except Exception:
        return None
    word = _cap(str(d.get("word") or "").strip())
    ru = str(d.get("ru") or "").strip()
    if not word or not ru or " " in word or word.lower() in blocked:
        return None
    return word, ru


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


def _train_question(word):
    msg = learning_ui.train_question(word)
    return msg.text, msg.entities


def _clip_poll_explanation(text, limit=200):
    text = re.sub(r"\s+\n", "\n", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def _train_explanation(sentence="", sentence_ru=""):
    sentence = str(sentence or "").strip()
    sentence_ru = str(sentence_ru or "").strip()
    if sentence and sentence_ru:
        return _clip_poll_explanation(f"{sentence} → {sentence_ru}", limit=160)
    return _clip_poll_explanation(sentence_ru or sentence, limit=160)


def _phrase_poll_question(blank_phrase, sentence_ru):
    msg = learning_ui.phrase_poll_question(blank_phrase, sentence_ru)
    return msg.text, msg.entities


def _phrase_poll_explanation(blank_phrase, correct, full_phrase, sentence_ru, extra=""):
    full_phrase = str(full_phrase or "").strip()
    sentence_ru = str(sentence_ru or "").strip()
    if full_phrase and sentence_ru:
        return _clip_poll_explanation(f"{full_phrase} → {sentence_ru}", limit=160)
    return _clip_poll_explanation(sentence_ru or full_phrase, limit=160)


async def _gen_train_quiz_card(word, ru, language):
    """Smart LLM: context sentence + pedagogically useful distractors."""
    prompt = f"""
Ты методист тренажёра слов для языка: {language}.
Целевое слово: «{word}».
Базовый перевод: «{ru}».

Сделай quiz poll на перевод целевого слова. Контекстное предложение нужно только для последующего объяснения.

Жёсткие правила вариантов ответа:
1. Ровно 3 варианта на русском: один правильный и два неправильных.
2. Все варианты — одна часть речи.
3. Все варианты примерно одинаковой длины, без очевидно самого длинного ответа.
4. Неправильный вариант — похожая ловушка: частая ошибка, созвучие, близкое значение или ложный друг. Не случайное слово.
5. Контекстное предложение должно быть очень коротким, бытовым и понятным для человека с СДВГ: одна простая сцена, без лишних деталей.
6. Если пользователь выбрал неверный вариант, объяснение должно назвать, как этот неверный смысл выражается на {language}.

Верни JSON:
{{
  "sentence": "короткое предложение на {language} с целевым словом",
  "sentence_ru": "перевод предложения на русский",
  "correct": "правильный вариант на русском",
  "wrong": ["неверный вариант 1", "неверный вариант 2"],
  "wrong_map": {{"неверный вариант": "как это будет на {language}"}},
  "meaning": "краткое значение целевого слова на русском, до 4 слов"
}}
"""
    try:
        d = await ai.allm_json(prompt, 900, tier="smart", route="gemini", module="learning")
    except Exception:
        sentence, sentence_ru = await asyncio.to_thread(_gen_context, word, language)
        wrong = await asyncio.to_thread(_gen_distractors, word, ru, language, "fl_to_ru")
        return {
            "sentence": sentence or word,
            "sentence_ru": sentence_ru or ru,
            "correct": ru,
            "wrong": wrong[:2],
            "wrong_map": {},
            "meaning": ru,
        }

    wrong = [str(x).strip() for x in (d.get("wrong") or []) if str(x).strip()][:2]
    correct = str(d.get("correct") or ru).strip()
    options = [correct] + wrong
    if len(wrong) < 2 or len(set(x.lower() for x in options)) < 3 or not _same_len(options):
        fallback_wrong = await asyncio.to_thread(_gen_distractors, word, ru, language, "fl_to_ru")
        for item in fallback_wrong:
            item = str(item).strip()
            if item and item.lower() not in {x.lower() for x in [correct] + wrong}:
                wrong.append(item)
            if len(wrong) >= 2:
                break
    return {
        "sentence": str(d.get("sentence") or word).strip(),
        "sentence_ru": str(d.get("sentence_ru") or "").strip(),
        "correct": correct,
        "wrong": wrong[:2],
        "wrong_map": d.get("wrong_map") if isinstance(d.get("wrong_map"), dict) else {},
        "meaning": str(d.get("meaning") or correct).strip(),
    }


async def _gen_phrase_quiz_card(phrase, ru, language, avoid_tests=None):
    """Учебная карточка фразы и отдельный тест на применение правила в новом контексте."""
    avoid_tests = [str(x).strip() for x in (avoid_tests or []) if str(x).strip()]
    avoid_note = ""
    if avoid_tests:
        avoid_note = "\nНе повторяй эти тестовые фразы:\n" + "\n".join(f"- {x}" for x in avoid_tests[-5:])
    prompt = f"""
Ты методист тренажёра фраз для языка: {language}.
Учебная фраза: «{phrase}».
Перевод на русский: «{ru}».
{avoid_note}

Сделай учебную карточку и ОТДЕЛЬНЫЙ тест на применение того же правила.

Учебная карточка показывает исходную фразу, перевод и короткое правило.

Перед ответом проверь согласованность:
- перевод относится именно к учебной фразе;
- construction реально присутствует в учебной фразе;
- target_token используется в учебной фразе именно в роли из правила;
- нельзя смешивать разные значения одного слова в одной карточке.

Пример: для "Dat is bijzonder." нельзя давать перевод "Эта машина необычно дорогая" и правило
"bijzonder + прилагательное". Это другая карточка: "Deze auto is bijzonder duur."

Тестовая фраза должна быть НОВОЙ, не копией учебной фразы. Она проверяет применение правила, а не память
исходного предложения. Нельзя повторять одновременно тот же глагол, то же существительное, тот же перевод
и ту же структуру предложения. Сохрани только целевое слово, грамматическую конструкцию и смысл правила.

Правила теста:
1. test_blank_phrase — новая фраза с ____ вместо целевого слова.
2. test_full_phrase — та же новая фраза полностью, с правильным словом.
3. correct — ровно пропущенное слово, без артиклей и лишних слов.
4. wrong — три правдоподобных неправильных варианта на {language}, той же части речи.
5. test_sentence_ru — перевод test_full_phrase на русский.
6. short_rule — короткая подсказка вида "door = из-за, по причине чего-то".
7. detail — разбор 350-450 символов простыми словами, только про test_full_phrase.
8. target_token — слово, правило которого проверяем; обычно совпадает с correct.
9. self_check — все поля true только если карточка полностью согласована.

Верни JSON:
{{
  "test_blank_phrase": "новая тестовая фраза с ____",
  "test_full_phrase": "новая тестовая фраза полностью",
  "correct": "пропущенное слово",
  "target_token": "целевое слово правила",
  "wrong": ["неверный вариант 1", "неверный вариант 2", "неверный вариант 3"],
  "test_sentence_ru": "перевод test_full_phrase на русский",
  "construction": "название конструкции, например 'ziek door iets'",
  "construction_meaning": "что значит конструкция целиком, коротко по-русски",
  "short_rule": "короткая подсказка",
  "detail": "короткий разбор по тестовой фразе",
  "other_forms": [
    {{"word": "слово", "meaning": "другое значение, только если оно не конфликтует с правилом"}}
  ],
  "self_check": {{
    "translation_matches_learning_phrase": true,
    "pattern_present_in_learning_phrase": true,
    "target_token_role_ok": true,
    "learning_phrase_natural": true,
    "test_checks_same_rule": true,
    "test_is_new_not_copy": true,
    "no_mixed_meanings": true
  }}
}}

Поле other_forms заполняй максимум одним пунктом и только если оно не повторяет главное правило, не создаёт
конфликтующее значение и реально помогает. Если сомневаешься — верни пустой список.
"""
    try:
        d = await ai.allm_json(prompt, 1400, tier="smart", route="gemini", module="learning")
    except Exception:
        return {}
    wrong = [str(x).strip() for x in (d.get("wrong") or []) if str(x).strip()][:3]
    other_forms = []
    for item in (d.get("other_forms") or [])[:3]:
        if not isinstance(item, dict):
            continue
        pos = str(item.get("word") or item.get("pos") or "").strip()
        meaning = str(item.get("meaning") or "").strip()
        if pos and meaning:
            other_forms.append({"pos": pos, "meaning": meaning})
    blank_phrase = str(d.get("test_blank_phrase") or d.get("blank_phrase") or "").strip()
    correct = str(d.get("correct") or "").strip()
    full_phrase = str(d.get("test_full_phrase") or "").strip()
    if not full_phrase and blank_phrase and correct:
        full_phrase = blank_phrase.replace("____", correct, 1)
    return {
        "blank_phrase": blank_phrase,
        "correct": correct,
        "target_token": str(d.get("target_token") or correct).strip(),
        "wrong": wrong,
        "sentence_ru": str(d.get("test_sentence_ru") or d.get("sentence_ru") or "").strip(),
        "test_full_phrase": full_phrase,
        "construction": str(d.get("construction") or "").strip(),
        "construction_meaning": str(d.get("construction_meaning") or "").strip(),
        "short_rule": str(d.get("short_rule") or "").strip(),
        "detail": str(d.get("detail") or "").strip(),
        "other_forms": _filter_phrase_other_forms(other_forms, d),
        "explanation": str(d.get("short_rule") or d.get("explanation") or "").strip(),
        "self_check": d.get("self_check") if isinstance(d.get("self_check"), dict) else {},
    }


_PHRASE_SKIP = {
    "a", "an", "the", "to", "of", "in", "on", "at", "for", "with", "and", "or", "but",
    "i", "you", "he", "she", "it", "we", "they", "me", "my", "your", "his", "her",
    "de", "het", "een", "ik", "je", "jij", "hij", "zij", "ze", "we", "wij", "mijn",
    "jouw", "zijn", "haar", "en", "of", "maar", "op", "in", "aan", "van", "voor",
}

_PHRASE_DISTRACTORS = {
    "нидерландский": ["maken", "denken", "werken", "vragen", "kijken", "nodig", "samen", "later"],
    "английский": ["make", "think", "work", "ask", "look", "need", "together", "later"],
}

_PATTERN_PLACEHOLDERS = {
    "iets", "iemand", "someone", "something", "somebody", "sth", "sb",
    "adjective", "adjectief", "прилагательное", "сущ", "существительное",
    "verb", "глагол", "noun", "prep", "предлог",
}


def _phrase_tokens(text):
    return [m.group(0).lower() for m in re.finditer(r"[\wÀ-ÖØ-öø-ÿ'-]+", str(text or ""), flags=re.UNICODE)]


def _normalize_phrase_for_compare(text):
    return " ".join(_phrase_tokens(text))


def _filter_phrase_other_forms(other_forms, card):
    if not other_forms:
        return []
    main = " ".join(str(card.get(k) or "").lower() for k in ("construction", "construction_meaning", "short_rule"))
    filtered = []
    for item in other_forms:
        pos = str(item.get("pos") or "").strip()
        meaning = str(item.get("meaning") or "").strip()
        if not pos or not meaning:
            continue
        combined = f"{pos} {meaning}".lower()
        if combined in main or meaning.lower() in main:
            continue
        filtered.append({"pos": pos, "meaning": meaning})
        break
    return filtered


def _phrase_card_is_consistent(learn_phrase, learn_ru, card):
    learn_phrase = str(learn_phrase or "").strip()
    learn_ru = str(learn_ru or "").strip()
    blank = str(card.get("blank_phrase") or "").strip()
    full = str(card.get("test_full_phrase") or "").strip()
    correct = str(card.get("correct") or "").strip()
    target = str(card.get("target_token") or correct).strip()
    construction = str(card.get("construction") or "").strip()
    construction_meaning = str(card.get("construction_meaning") or "").strip()
    test_ru = str(card.get("sentence_ru") or "").strip()
    self_check = card.get("self_check") if isinstance(card.get("self_check"), dict) else {}
    required_checks = (
        "translation_matches_learning_phrase",
        "pattern_present_in_learning_phrase",
        "target_token_role_ok",
        "learning_phrase_natural",
        "test_checks_same_rule",
        "test_is_new_not_copy",
        "no_mixed_meanings",
    )
    if any(self_check.get(k) is not True for k in required_checks):
        return False
    if not all([learn_phrase, learn_ru, blank, full, correct, target, construction, construction_meaning, test_ru]):
        return False
    if "____" not in blank:
        return False
    if _normalize_phrase_for_compare(learn_phrase) == _normalize_phrase_for_compare(full):
        return False
    if _normalize_phrase_for_compare(learn_phrase) == _normalize_phrase_for_compare(blank):
        return False

    learn_tokens = set(_phrase_tokens(learn_phrase))
    learn_token_list = _phrase_tokens(learn_phrase)
    full_tokens = set(_phrase_tokens(full))
    target_low = target.lower()
    correct_low = correct.lower()
    if target_low not in learn_tokens or correct_low not in full_tokens:
        return False

    pattern_tokens = [
        t for t in _phrase_tokens(construction)
        if t not in _PATTERN_PLACEHOLDERS and len(t) > 1
    ]
    if pattern_tokens and not all(t in learn_tokens for t in pattern_tokens):
        return False
    if pattern_tokens and correct_low not in pattern_tokens and target_low not in pattern_tokens:
        return False
    construction_low = construction.lower()
    if any(marker in construction_low for marker in ("+ прилагательное", "+ adjective", "+ adjectief")):
        positions = [i for i, t in enumerate(learn_token_list) if t == target_low]
        if not positions or all(i >= len(learn_token_list) - 1 for i in positions):
            return False
    return True


async def _validate_phrase_card_semantics(phrase, ru, language, card):
    prompt = f"""
Проверь карточку фразового тренажёра для языка: {language}.

Учебная фраза: {phrase}
Русский перевод учебной фразы: {ru}
Паттерн: {card.get("construction") or ""}
Значение паттерна: {card.get("construction_meaning") or ""}
Целевой токен: {card.get("target_token") or card.get("correct") or ""}

Тестовая фраза с пропуском: {card.get("blank_phrase") or ""}
Полная тестовая фраза: {card.get("test_full_phrase") or ""}
Перевод тестовой фразы: {card.get("sentence_ru") or ""}
Правильный ответ: {card.get("correct") or ""}

Ответь строго JSON:
{{
  "ok": true,
  "reason": ""
}}

Поставь ok=false, если:
- русский перевод не относится именно к учебной фразе;
- паттерн не присутствует в учебной фразе;
- target_token используется не в той роли;
- смешаны разные значения одного слова;
- тестовая фраза проверяет другое правило;
- тестовая фраза копирует учебную.
"""
    try:
        d = await ai.allm_json(prompt, 350, tier="cheap", route="gemini", module="learning")
    except Exception:
        return False
    return bool(isinstance(d, dict) and d.get("ok") is True)


def _fallback_phrase_quiz_card(phrase, ru, language):
    phrase = str(phrase or "").strip()
    tokens = list(re.finditer(r"[\wÀ-ÖØ-öø-ÿ'-]+", phrase, flags=re.UNICODE))
    candidates = []
    for match in tokens:
        word = match.group(0).strip("'’")
        low = word.lower()
        if len(word) >= 3 and low not in _PHRASE_SKIP and not word[:1].isupper():
            candidates.append(match)
    if not candidates:
        candidates = [m for m in tokens if len(m.group(0).strip("'’")) >= 2]
    if not candidates:
        return {}

    match = candidates[-1]
    correct = match.group(0).strip("'’")
    blank_phrase = phrase[:match.start()] + "____" + phrase[match.end():]
    seen = {correct.lower()}
    wrong = []
    for item in _PHRASE_DISTRACTORS.get(language, _PHRASE_DISTRACTORS["английский"]):
        if item.lower() not in seen:
            wrong.append(item)
            seen.add(item.lower())
        if len(wrong) == 3:
            break
    if len(wrong) < 3:
        return {}
    return {
        "blank_phrase": blank_phrase,
        "correct": correct,
        "wrong": wrong,
        "sentence_ru": str(ru or "").strip(),
        "test_full_phrase": blank_phrase.replace("____", correct, 1),
        "construction": correct,
        "construction_meaning": "смотри значение в переводе фразы",
        "short_rule": f"{correct} = смотри перевод фразы",
        "detail": f"В этой фразе подходит «{correct}». Сравни полный пример с переводом и запомни конструкцию целиком.",
        "other_forms": [],
        "explanation": f"В этой фразе пропущено слово «{correct}».",
    }


def _phrase_full_from_blank(blank_phrase, correct):
    blank_phrase = str(blank_phrase or "").strip()
    correct = str(correct or "").strip()
    if blank_phrase and correct and "____" in blank_phrase:
        return blank_phrase.replace("____", correct, 1)
    return blank_phrase


def _clean_phrase_options(correct_answer, wrong, needed=3):
    clean_wrong = []
    seen = {str(correct_answer).lower()}
    for item in wrong:
        item = str(item).strip()
        if item and item.lower() not in seen:
            clean_wrong.append(item)
            seen.add(item.lower())
        if len(clean_wrong) >= needed:
            break
    return clean_wrong


async def _gen_consistent_phrase_card(phrase, ru, language, avoid_tests=None, attempts=2):
    for _ in range(max(1, attempts)):
        card = await _gen_phrase_quiz_card(phrase, ru, language, avoid_tests=avoid_tests)
        correct_answer = card.get("correct") or ""
        clean_wrong = _clean_phrase_options(correct_answer, list(card.get("wrong") or []), needed=3)
        blank_phrase = card.get("blank_phrase") or ""
        if (
            correct_answer
            and "____" in blank_phrase
            and len(clean_wrong) >= 3
            and _phrase_card_is_consistent(phrase, ru, card)
            and await _validate_phrase_card_semantics(phrase, ru, language, card)
        ):
            card["wrong"] = clean_wrong[:3]
            return card
    return {}


def _phrase_start_card_or_fallback(card, phrase, ru, language):
    """Use a generated phrase card, or a local cloze fallback for the first training card."""
    correct_answer = card.get("correct") or ""
    clean_wrong = _clean_phrase_options(correct_answer, list(card.get("wrong") or []), needed=3)
    blank_phrase = card.get("blank_phrase") or ""
    if correct_answer and "____" in blank_phrase and len(clean_wrong) >= 3:
        card["wrong"] = clean_wrong[:3]
        return card
    return _fallback_phrase_quiz_card(phrase, ru, language)


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


def _train_back_target(language=None):
    return "m_nl" if _code(language or "нидерландский") == "nl" else "m_en"


def _train_again_kb(language=None, mode="word"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё", callback_data="train_next")],
        [InlineKeyboardButton("◀️ Назад", callback_data=_train_back_target(language))],
    ])

def _train_available_modes(cid, language):
    modes = []
    if _train_phrases(cid, language):
        modes.append("phrase")
    if _train_words(cid, language):
        modes.append("word")
    return modes


async def train_start(bot, cid, language, mode=None):
    store.challenge_state.pop(str(cid), None)
    store.game_state.pop(str(cid), None)
    store.pending_input.pop(str(cid), None)
    available_modes = _train_available_modes(cid, language)
    if not available_modes:
        code = _code(language)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📖 Открыть словарь", callback_data=f"a_dictlang_{code}_from_lang")]])
        await bot.send_message(chat_id=cid,
            text=f"{_flag(language)} В словаре нет слов или фраз с переводом. Добавь записи через словарь.",
            reply_markup=kb)
        return
    start_mode = mode if mode in available_modes else available_modes[0]
    store.train_state[str(cid)] = {
        "lang": language,
        "mode": start_mode,
        "next_mode": start_mode,
        "locked_mode": mode if mode in ("word", "phrase") else None,
        "round": 0,
        "used_words": [],
        "used_phrases": [],
    }
    await _render_next_train_quiz(bot, cid)


async def _render_quiz(bot, cid):
    import random as _r
    store.pending_input.pop(str(cid), None)
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    language = st["lang"]
    words = _train_words(cid, language)
    if not words:
        if _train_phrases(cid, language):
            st["next_mode"] = "phrase"
            await _render_phrase_quiz(bot, cid)
            return
        await bot.send_message(chat_id=cid, text="В словаре нет отдельных слов с переводом."); return

    # В 30% раундов пробуем новое частотное слово выше B1, близкое к словарю.
    word_source = "dict"
    used_new = st.get("used_new", [])
    new_word = None
    if _should_train_new_word(st.get("round", 0)):
        new_word = await _gen_train_new_word(cid, language, words, used_new)
    if new_word:
        word, ru = new_word
        used_new.append(word)
        st["used_new"] = used_new[-50:]
        word_source = "new"
    else:
        # Выбираем слово (без повторов пока не исчерпаем весь список)
        used = st.get("used_words", [])
        available = [(i, w) for i, w in enumerate(words) if i not in used]
        if not available:
            used = []
            available = list(enumerate(words))
            st["used_words"] = used
        idx, (word, ru) = _r.choice(available)
        used.append(idx)
        st["used_words"] = used

    card = await _gen_train_quiz_card(word, ru, language)
    correct_answer = card.get("correct") or ru
    wrong = list(card.get("wrong") or [])

    # Фолбэк: берём слово из словаря если LLM не сгенерировал дистрактор.
    if len(wrong) < 2:
        other = [(w, r) for w, r in words if w != word]
        _r.shuffle(other)
        for ow, oru in other[:2 - len(wrong)]:
            wrong.append(oru)

    clean_wrong = []
    seen = {str(correct_answer).lower()}
    for item in wrong:
        item = str(item).strip()
        if item and item.lower() not in seen:
            clean_wrong.append(item)
            seen.add(item.lower())
        if len(clean_wrong) >= 2:
            break
    options = [correct_answer] + clean_wrong[:2]
    if len(options) < 3:
        await bot.send_message(
            chat_id=cid,
            text="Не удалось собрать три хороших варианта. Попробуй ещё раз.",
            reply_markup=_train_again_kb(language),
        )
        return
    _r.shuffle(options)
    correct_idx = options.index(correct_answer)

    locked = st.get("locked_mode")
    next_mode = locked if locked else ("phrase" if _train_phrases(cid, language) else "word")
    st.update({
        "mode": "word",
        "next_mode": next_mode,
        "word": word,
        "ru": ru,
        "sentence": card.get("sentence") or word,
        "sentence_ru": card.get("sentence_ru") or "",
        "meaning": card.get("meaning") or correct_answer,
        "wrong_map": card.get("wrong_map") or {},
        "options": options,
        "correct_idx": correct_idx,
        "word_source": word_source,
    })

    question, question_entities = _train_question(word)
    explanation = _train_explanation(
        st.get("sentence", ""),
        st.get("sentence_ru", ""),
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
        reply_markup=_train_again_kb(language),
    )
    if getattr(msg, "poll", None):
        store.train_polls[msg.poll.id] = str(cid)


async def _render_phrase_quiz(bot, cid):
    """Этап 1 тренажёра фраз: карточка с фразой целиком и разбором конструкции. Quiz (этап 2)
    отправляется отдельно по нажатию кнопки — см. phrase_intro_continue()."""
    import random as _r
    store.pending_input.pop(str(cid), None)
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    language = st["lang"]
    phrases = _train_phrases(cid, language)
    if not phrases:
        if _train_words(cid, language):
            st["next_mode"] = "word"
            await _render_quiz(bot, cid)
            return
        await bot.send_message(chat_id=cid, text="В словаре нет фраз с переводом."); return

    used = st.get("used_phrases", [])
    available = [(i, p) for i, p in enumerate(phrases) if i not in used]
    if not available:
        used = []
        available = list(enumerate(phrases))
        st["used_phrases"] = used
    idx, (phrase, ru) = _r.choice(available)
    used.append(idx)
    st["used_phrases"] = used

    card = _phrase_start_card_or_fallback(
        await _gen_consistent_phrase_card(phrase, ru, language),
        phrase,
        ru,
        language,
    )
    correct_answer = card.get("correct") or ""
    wrong = list(card.get("wrong") or [])
    clean_wrong = _clean_phrase_options(correct_answer, wrong, needed=3)
    blank_phrase = card.get("blank_phrase") or ""
    if not correct_answer or "____" not in blank_phrase or len(clean_wrong) < 3:
        await bot.send_message(
            chat_id=cid,
            text="Не удалось собрать согласованную карточку по фразе. Попробуй ещё раз.",
            reply_markup=_train_again_kb(language, mode="phrase"),
        )
        return

    options = [correct_answer] + clean_wrong[:3]
    _r.shuffle(options)
    correct_idx = options.index(correct_answer)
    locked = st.get("locked_mode")
    next_mode = locked if locked else ("word" if _train_words(cid, language) else "phrase")
    st.update({
        "mode": "phrase",
        "next_mode": next_mode,
        "word": phrase,
        "ru": ru,
        "sentence": blank_phrase,
        "sentence_ru": card.get("sentence_ru") or ru,
        "meaning": correct_answer,
        "phrase_explanation": card.get("explanation") or "",
        "phrase_short_rule": card.get("short_rule") or card.get("explanation") or "",
        "phrase_detail": card.get("detail") or "",
        "wrong_map": {},
        "options": options,
        "correct_idx": correct_idx,
        "phrase_full": phrase,
        "phrase_test_full": card.get("test_full_phrase") or _phrase_full_from_blank(blank_phrase, correct_answer),
        "phrase_seen_tests": [blank_phrase],
        "phrase_error_count": 0,
        "phrase_pending_quiz": True,
        "phrase_stage": "intro",
    })

    msg = learning_ui.phrase_intro_card(
        phrase,
        ru,
        card.get("construction") or "",
        card.get("construction_meaning") or "",
        card.get("other_forms") or [],
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧩 Тест", callback_data="phrase_intro_test"),
            InlineKeyboardButton("✅ Выучил", callback_data="phrase_intro_mastered"),
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data=_train_back_target(language))],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def phrase_intro_continue(bot, cid):
    """Реакция на «Тест» после учебной карточки — отправляет quiz poll."""
    st = store.train_state.get(str(cid))
    if not st or not st.get("phrase_pending_quiz"):
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    language = st["lang"]
    phrase = st.get("phrase_full", "")
    blank_phrase = st.get("sentence", "")
    correct_answer = st.get("meaning", "")
    options = st.get("options", [])
    correct_idx = st.get("correct_idx", 0)
    st["phrase_pending_quiz"] = False
    st["phrase_stage"] = "quiz"

    question, question_entities = _phrase_poll_question(blank_phrase, st.get("sentence_ru", ""))
    explanation = _phrase_poll_explanation(
        blank_phrase,
        correct_answer,
        st.get("phrase_test_full", "") or phrase,
        st.get("sentence_ru", ""),
        "",
    )
    msg = await bot.send_poll(
        chat_id=cid,
        question=question,
        question_entities=question_entities,
        options=[str(x)[:100] for x in options[:10]],
        type="quiz",
        correct_option_id=correct_idx,
        is_anonymous=True,
        explanation=explanation,
        reply_markup=_train_again_kb(language, mode="phrase"),
    )
    if getattr(msg, "poll", None):
        store.train_polls[msg.poll.id] = str(cid)


async def phrase_intro_mastered(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    await _render_next_train_quiz(bot, cid)


async def phrase_new_example(bot, cid):
    import random as _r
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    phrase = st.get("phrase_full") or st.get("word", "")
    ru = st.get("ru", "")
    language = st.get("lang", "нидерландский")
    seen_tests = list(st.get("phrase_seen_tests") or [])
    card = await _gen_consistent_phrase_card(phrase, ru, language, avoid_tests=seen_tests)
    correct_answer = card.get("correct") or st.get("meaning", "")
    clean_wrong = _clean_phrase_options(correct_answer, list(card.get("wrong") or []), needed=3)
    blank_phrase = card.get("blank_phrase") or ""
    if not correct_answer or "____" not in blank_phrase or len(clean_wrong) < 3 or blank_phrase in seen_tests:
        await bot.send_message(
            chat_id=cid,
            text="Не удалось собрать новый пример. Попробуй дальше.",
            reply_markup=_train_again_kb(language, mode="phrase"),
        )
        return

    options = [correct_answer] + clean_wrong[:3]
    _r.shuffle(options)
    st.update({
        "sentence": blank_phrase,
        "sentence_ru": card.get("sentence_ru") or "",
        "meaning": correct_answer,
        "phrase_explanation": card.get("explanation") or "",
        "phrase_short_rule": card.get("short_rule") or card.get("explanation") or "",
        "phrase_detail": card.get("detail") or "",
        "options": options,
        "correct_idx": options.index(correct_answer),
        "phrase_test_full": card.get("test_full_phrase") or _phrase_full_from_blank(blank_phrase, correct_answer),
        "phrase_seen_tests": (seen_tests + [blank_phrase])[-8:],
        "phrase_pending_quiz": True,
        "phrase_stage": "intro",
    })
    await phrase_intro_continue(bot, cid)


async def phrase_explain(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    msg = learning_ui.phrase_rule_breakdown(st)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Новый пример", callback_data="phrase_new_example"),
         InlineKeyboardButton("Дальше", callback_data="train_next")],
        [InlineKeyboardButton("◀️ Назад", callback_data=_train_back_target(st.get("lang", "")))],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


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
    mode = st.get("mode", "word")

    if mode == "phrase" and st.get("phrase_stage") == "quiz":
        is_correct = idx == correct_idx
        if is_correct:
            msg = learning_ui.phrase_quiz_result(st, True)
            st["round"] = st.get("round", 0) + 1
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Следующая фраза", callback_data="train_next")],
                [InlineKeyboardButton("◀️ Назад", callback_data=_train_back_target(lang))],
            ])
        else:
            st["phrase_error_count"] = int(st.get("phrase_error_count", 0)) + 1
            repeated_error = st["phrase_error_count"] >= 2
            if repeated_error:
                st["needs_review"] = True
            msg = learning_ui.phrase_quiz_result(st, False, repeated_error=repeated_error)
            if repeated_error:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔎 Разобрать", callback_data="phrase_explain"),
                     InlineKeyboardButton("Новый пример", callback_data="phrase_new_example")],
                    [InlineKeyboardButton("Дальше", callback_data="train_next"),
                     InlineKeyboardButton("◀️ Назад", callback_data=_train_back_target(lang))],
                ])
            else:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Новый пример", callback_data="phrase_new_example"),
                     InlineKeyboardButton("Дальше", callback_data="train_next")],
                    [InlineKeyboardButton("◀️ Назад", callback_data=_train_back_target(lang))],
                ])
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return

    msg = learning_ui.train_result(st, idx, correct_idx, options, chosen_fl=chosen_fl)

    st["round"] = st.get("round", 0) + 1
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё", callback_data="train_next")],
        [InlineKeyboardButton("◀️ Назад", callback_data=_train_back_target(lang))],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def _render_next_train_quiz(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    mode = st.get("next_mode") or st.get("mode") or "phrase"
    if mode == "phrase":
        await _render_phrase_quiz(bot, cid)
    else:
        await _render_quiz(bot, cid)


async def train_next(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    store.pending_input.pop(str(cid), None)
    await _render_next_train_quiz(bot, cid)


async def send_train_kind_select(bot, cid, language):
    await train_start(bot, cid, language)


async def send_train_lang_select(bot, cid):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇳🇱 Нидерландский", callback_data="a_train_nl")],
        [InlineKeyboardButton("🇬🇧 Английский", callback_data="a_train_en")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_learn")],
    ])
    msg = learning_ui.train_lang_select()
    await bot.send_message(chat_id=cid,
        text=msg.text,
        entities=msg.entities, reply_markup=kb)


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
    msg = learning_ui.translate_prompt(_flag(lang), ru, lang)
    await bot.send_message(chat_id=cid,
        text=msg.text,
        entities=msg.entities)

async def translate_answer(bot, cid, text):
    st = store.challenge_state.pop(str(cid), None)
    if not st:
        return False
    try:
        r = check_translation(st["lang"], st["ru"], text)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return True
    msg = learning_ui.translate_result(_flag(st["lang"]), st["lang"], st["ru"], text, r)
    code = _code(st["lang"])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё пример", callback_data=f"again_tr_{code}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"m_{code}")],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
    return True


# ================= ГЛАГОЛ ДНЯ / ПОСЛОВИЦА =================
def _proverb_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё вариант", callback_data=f"a_proverb_{code}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"m_{code}")],
    ])

def _proverb_entities_card(flag, original, analogs=None, meaning="", examples=None):
    msg = learning_ui.proverb_card(flag, original, analogs, meaning, examples)
    return msg.text, msg.entities


def _proverb_fallback(language):
    if language == "английский":
        return {
            "original": "Cut corners",
            "analogs": ["сделать спустя рукава", "сэкономить на качестве", "срезать углы"],
            "meaning": "делать быстрее или дешевле, жертвуя качеством",
            "examples": ["Don’t cut corners on this report. → Не делай этот отчёт спустя рукава."],
        }
    return {
        "original": "Geen gedoe",
        "analogs": ["без лишней возни", "без заморочек", "без шума"],
        "meaning": "когда хочется сделать что-то просто и без усложнений",
        "examples": ["Ik wil gewoon geen gedoe. → Я просто хочу без лишней возни."],
    }


async def send_proverb(bot, cid, language):
    flag = _flag(language)
    try:
        d = await ai.allm_json(
            "Ты эксперт по живому разговорному языку. "
            f"Твоя цель — научить говорить как местный житель. "
            f"Пиши только проверенные, естественные выражения на языке: {language}. "
            "Перевод на русский должен передавать реальный смысл, не буквальную кальку. "
            f"Выдай одно полезное выражение на {language}: фразовый глагол, идиому или частую разговорную фразу.\n"
            'JSON: {"original":"выражение на ' + language + '",'
            '"type":"фразовый глагол / идиома / разговорная фраза",'
            '"analogs":["русский аналог 1","русский аналог 2","русский аналог 3","русский аналог 4"],'
            '"meaning":"контекст употребления на русском, коротко; пустая строка если не нужен",'
            '"examples":["один пример на ' + language + ' → перевод на русский"]}',
            400, tier="cheap", route="gemini", module="learning")
        def _cap(s):
            s = (s or "").strip()
            return s[0].upper() + s[1:] if s else s

        original = _cap(d.get("original", ""))
        if not original:
            d = _proverb_fallback(language)
            original = d["original"]
        txt, entities = _proverb_entities_card(
            flag,
            original,
            d.get("analogs") or d.get("literal") or d.get("ru") or [],
            _cap(d.get("meaning", "")),
            d.get("examples") or [],
        )
    except Exception:
        d = _proverb_fallback(language)
        txt, entities = _proverb_entities_card(
            flag,
            d["original"],
            d["analogs"],
            d["meaning"],
            d["examples"],
        )
    await bot.send_message(chat_id=cid, text=txt, entities=entities, reply_markup=_proverb_kb(_code(language)))


async def send_proverb_both(bot, cid, with_kb=True):
    """Живой язык NL + EN: фразовый глагол, идиома или разговорная фраза."""
    try:
        d = await ai.allm_json(
            "Ты эксперт по живому разговорному языку. "
            "Пиши только проверенные, естественные выражения. "
            "Перевод на русский должен передавать реальный смысл, не буквальную кальку. "
            "Выдай одно выражение — фразовый глагол, идиому или частую разговорную фразу.\n"
            'JSON: {"nl":"выражение на нидерландском",'
            '"en":"живой английский эквивалент (не перевод, а аналог)",'
            '"analogs":["русский аналог 1","русский аналог 2","русский аналог 3","русский аналог 4"],'
            '"type":"фразовый глагол / идиома / разговорная фраза",'
            '"meaning":"контекст употребления на русском, коротко; пустая строка если не нужен",'
            '"examples":["один пример на нидерландском или английском → перевод на русский"]}',
            500, tier="cheap", route="gemini", module="learning")
        def _cap(s):
            s = (s or "").strip()
            return s[0].upper() + s[1:] if s else s

        original = _cap(d.get("nl", "")) or _cap(d.get("en", ""))
        if not original:
            d = _proverb_fallback("английский")
            original = d["original"]
        txt, entities = _proverb_entities_card(" ", original, d.get("analogs") or d.get("ru") or [], _cap(d.get("meaning", "")), d.get("examples") or [])
    except Exception:
        d = _proverb_fallback("английский")
        txt, entities = _proverb_entities_card(" ", d["original"], d["analogs"], d["meaning"], d["examples"])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё вариант", callback_data="a_proverb")],
        [InlineKeyboardButton("◀️ Назад", callback_data="m_learn")],
    ]) if with_kb else None
    await bot.send_message(chat_id=cid, text=txt, entities=entities, reply_markup=kb)


# ================= СЛОВАРЬ (раздельно NL / EN) =================
_BULLET_RE = re.compile(r"^[\s\-\*•·–—>»\d\.\)\(]+")
_TERM_SEP_RE = re.compile(r"\s+[-–—=:]\s+|\t+")
_PAREN_TRANSLATION_RE = re.compile(r"^(.+?)\s*[\(\[]\s*([^()\[\]]{1,160})\s*[\)\]]\s*$")

def _split_term(s):
    """Убирает маркеры списка и отделяет перевод, если он на той же строке (через - – — : =)."""
    s = _BULLET_RE.sub("", (s or "").strip()).strip()
    parts = _TERM_SEP_RE.split(s, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    m = _PAREN_TRANSLATION_RE.match(s)
    if m:
        term, ru = m.group(1).strip(), m.group(2).strip()
        if re.search(r"[А-Яа-яЁё]", ru):
            return term, ru
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
_DICT_LEADING_RE = re.compile(r"^\s*в\s+(?:мой\s+)?словар[ьяьею]*\b", re.I)
_DICT_LANG_RE = re.compile(
    r"\b(?:на\s+)?("
    r"нидерландск(?:ом|ое|ого|ий|ую|ая|ие|их)|голландск(?:ом|ое|ого|ий|ую|ая|ие|их)|dutch|nl|"
    r"английск(?:ом|ое|ого|ий|ую|ая|ие|их)|english|en"
    r")\b",
    re.I,
)
_DICT_KIND_RE = re.compile(r"\b(слово|слова|фразу|выражение|термин)\b", re.I)
_DICT_QUESTION_PAYLOAD_RE = re.compile(r"^(?:како(?:е|й|ую)|что|что-то)\b", re.I)
_DICT_PAYLOAD_PREFIX_RE = re.compile(
    r"^(?:(?:ну|пожалуйста|плиз|нужно|надо|можешь|можно|мне|нам|хочу|давай|нов(?:ое|ый|ую|ая|ые))\s+)+",
    re.I,
)

def _dict_lang_hint(text):
    t = (text or "").lower()
    if any(x in t for x in ("английск", "english", " en ")):
        return "en"
    if any(x in t for x in ("нидерланд", "голланд", "dutch", " nl ")):
        return "nl"
    return "nl"

def _extract_chat_dict_add(text):
    """Команда из свободного чата: «добавь в словарь слово ...» -> полезная часть."""
    text = text or ""
    if _DICT_LEADING_RE.search(text):
        lang = _dict_lang_hint(f" {text} ")
        payload = _DICT_LEADING_RE.sub(" ", text, count=1)
        payload = _DICT_KIND_RE.sub(" ", payload)
        payload = _DICT_LANG_RE.sub(" ", payload)
        payload = re.sub(r"\s+", " ", payload).strip(" \t\n\r:;,.-–—")
        payload = _DICT_PAYLOAD_PREFIX_RE.sub("", payload).strip(" \t\n\r:;,.-–—")
        if len(payload) < 2:
            return None, None
        return payload, lang
    has_add_verb = bool(_DICT_ADD_VERB_RE.search(text))
    has_dict_word = bool(_DICT_WORD_RE.search(text))
    has_kind_word = bool(_DICT_KIND_RE.search(text))
    if not has_add_verb or not (has_dict_word or has_kind_word):
        return None, None
    lang = _dict_lang_hint(f" {text} ")
    payload = _DICT_ADD_VERB_RE.sub(" ", text, count=1)
    payload = _DICT_WORD_RE.sub(" ", payload)
    payload = _DICT_KIND_RE.sub(" ", payload)
    payload = _DICT_LANG_RE.sub(" ", payload)
    payload = re.sub(r"\s+", " ", payload).strip(" \t\n\r:;,.-–—")
    payload = _DICT_PAYLOAD_PREFIX_RE.sub("", payload).strip(" \t\n\r:;,.-–—")
    if len(payload) < 2 or _DICT_QUESTION_PAYLOAD_RE.search(payload):
        return None, None
    return payload, lang

async def try_add_dict_from_chat(bot, cid, text):
    """Перехватывает явную просьбу добавить слово/фразу в словарь из обычного чата."""
    payload, lang = _extract_chat_dict_add(text)
    if not payload:
        return False
    await add_words_batch(bot, cid, payload, lang, detailed_confirmation=True)
    return True

def _parse_simple_pairs(text, lang_hint):
    """Быстрый путь без LLM для строк вида «de aandacht → внимание»."""
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
            "Основной формат: «термин → перевод»; также понимай старый ввод через -, —, : или =. В word клади ТОЛЬКО иностранный термин, "
            "перевод клади в ru. Для КАЖДОГО элемента определи: lang (nl - нидерландский или en - английский), "
            "kind (word - одно слово, в т.ч. существительное с артиклем de/het/the; phrase - выражение из нескольких слов), "
            "и перевод ru на русский. Нидерландские существительные - с артиклем de/het. "
            f"Если язык элемента неочевиден, ставь \"{lang_hint}\". "
            'Верни ТОЛЬКО JSON: {"items":[{"word":"иностранный термин без перевода","ru":"перевод","lang":"nl|en","kind":"word|phrase"}]}')
    d = ai.llm_json(f"{spec}\n\n{secure.wrap_untrusted(text, 'текст для разбора')}", 1500, tier="cheap")
    return d.get("items", []) if isinstance(d, dict) else []

def _dict_add_confirmation_card(added_items):
    msg = dict_ui.dict_add_confirmation(added_items)
    return msg.text, msg.entities

def _dict_item_key(lang, kind, word):
    normalized = re.sub(r"\s+", " ", (word or "").strip()).casefold()
    return lang, kind, normalized

def _dict_duplicate_confirmation_card(duplicate_items):
    msg = dict_ui.dict_duplicate_confirmation(duplicate_items)
    return msg.text, msg.entities

_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_PLACEHOLDER_RU_RE = re.compile(r"^\??\.?\.?\.?\??$")

def _is_bad_dict_item(word, ru):
    """True, если перевод отсутствует/заглушка, или word перепутан с ru (кириллица вместо иностранного слова)."""
    word = (word or "").strip()
    ru = (ru or "").strip()
    if not ru or _PLACEHOLDER_RU_RE.match(ru):
        return True
    if ru.casefold() == word.casefold():
        return True
    if _CYRILLIC_RE.search(word):
        return True
    return False

async def _translate_to_ru(term, lang):
    """Попытка через LLM перевести term (nl/en) на русский, когда парсер не нашёл перевод."""
    language = "нидерландский" if lang == "nl" else "английский"
    prompt = (
        f"Переведи термин с {language} языка на русский одним словом или короткой фразой.\n"
        f"Термин: «{term}».\n"
        'Если это не похоже на реальное слово/фразу на этом языке — верни пустую строку.\n'
        'Верни ТОЛЬКО JSON: {"ru": "перевод или пустая строка"}'
    )
    try:
        d = await ai.allm_json(prompt, 200, tier="cheap", module="learning")
    except Exception:
        return ""
    ru = str(d.get("ru") or "").strip() if isinstance(d, dict) else ""
    if not ru or _PLACEHOLDER_RU_RE.match(ru) or ru.casefold() == term.casefold():
        return ""
    return ru

async def add_words_batch(bot, cid, text, lang="nl", detailed_confirmation=False):
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
    added_items = []
    duplicate_items = []
    unrecognized_items = []
    existing_keys = {
        _dict_item_key(_dict_lang(w), _dict_kind(w), _w_field(w, "word", "nl", "en"))
        for w in _ensure_dict(cid)
    }
    for it in items:
        # чистим маркеры списка и отделяем перевод, прилипший к слову
        term, extra_ru = _split_term(it.get("word") or "")
        if not term:
            continue
        ru = (it.get("ru") or "").strip() or extra_ru
        lng = "en" if it.get("lang") == "en" else "nl"
        # LLM/пользователь могли перепутать стороны (термин на русском, перевод — иностранный)
        if _CYRILLIC_RE.search(term) and ru and not _CYRILLIC_RE.search(ru):
            term, ru = ru, term
        if _is_bad_dict_item(term, ru):
            translated = await _translate_to_ru(term, lng)
            if translated:
                ru = translated
        knd = _kind_of(term)   # тип по самому термину (одно слово = слово)
        word = _cap(term)[:80]
        if _is_bad_dict_item(word, ru):
            unrecognized_items.append(word)
            continue
        key = _dict_item_key(lng, knd, word)
        if key in existing_keys:
            duplicate_items.append({"lang": lng, "word": word, "ru": ru, "kind": knd})
            continue
        store.add_to_list(config.DICT_KEY, cid, {"lang": lng, "word": word, "ru": ru, "kind": knd})
        existing_keys.add(key)
        added[lng][knd] += 1
        added_items.append({"lang": lng, "word": word, "ru": ru, "kind": knd})
    if not any(added[l][k] for l in added for k in added[l]):
        if detailed_confirmation and duplicate_items:
            msg = dict_ui.dict_duplicate_confirmation(duplicate_items)
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_dict_manage_kb(lang))
            return
        if duplicate_items:
            await bot.send_message(chat_id=cid, text="Эти слова или фразы уже есть в словаре."); return
        if unrecognized_items:
            await bot.send_message(chat_id=cid,
                text="Не удалось найти перевод: " + ", ".join(unrecognized_items[:10]) +
                     ". Пришли в формате «термин → перевод».")
            return
        await bot.send_message(chat_id=cid, text="Не удалось распознать слова. Попробуй ещё раз."); return
    if unrecognized_items:
        await bot.send_message(chat_id=cid,
            text="⚠️ Без перевода, не добавлено: " + ", ".join(unrecognized_items[:10]) +
                 ". Пришли их в формате «термин → перевод».")
    if detailed_confirmation:
        msg = dict_ui.dict_add_confirmation(added_items)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_dict_manage_kb(lang))
        return
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
    unrecognized_items = []
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
        if _CYRILLIC_RE.search(term) and ru and not _CYRILLIC_RE.search(ru):
            term, ru = ru, term
        if _is_bad_dict_item(term, ru):
            translated = await _translate_to_ru(term, lng)
            if translated:
                ru = translated
        knd = "phrase" if kind == "phrase" else _kind_of(term)
        word = _cap(term)[:80]
        if _is_bad_dict_item(word, ru):
            unrecognized_items.append(word)
            continue
        store.add_to_list(config.DICT_KEY, cid, {"lang": lng, "word": word, "ru": ru, "kind": knd})
        added[lng][knd] += 1

    if unrecognized_items and not any(added[l][k] for l in added for k in added[l]):
        await bot.send_message(chat_id=cid,
            text="Не удалось найти перевод: " + ", ".join(unrecognized_items[:10]) +
                 ". Пришли в формате «термин → перевод».")
        return

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
    if unrecognized_items:
        await bot.send_message(chat_id=cid,
            text="⚠️ Без перевода, не добавлено: " + ", ".join(unrecognized_items[:10]) +
                 ". Пришли их в формате «термин → перевод».")
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
    msg = dict_ui.dict_overview(nl_total, en_total)
    origin = {"m_notes": "notes", "m_learn": "learn", "m_dict_settings": "settings"}.get(back, "notes")
    rows = [
        [InlineKeyboardButton(f"🇳🇱 Нидерландский ({nl_total})", callback_data=f"a_dictlang_nl_from_{origin}")],
        [InlineKeyboardButton(f"🇬🇧 Английский ({en_total})", callback_data=f"a_dictlang_en_from_{origin}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=back)],
    ]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))

async def send_dict_lang(bot, cid, lang, back="m_dict_settings"):
    c = _dict_counts(cid)[lang]
    msg = dict_ui.dict_language(lang, c)
    rows = [
        [
            InlineKeyboardButton("❌ Слово", callback_data=f"a_dictedit_{lang}_word"),
            InlineKeyboardButton("❌ Фраза", callback_data=f"a_dictedit_{lang}_phrase"),
        ],
        [InlineKeyboardButton("✏️ Добавить слово или фразу", callback_data=f"a_dictadd_smart_{lang}")],
        [InlineKeyboardButton("🩹 Проверить словарь", callback_data=f"a_dictcheck_{lang}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=back)],
    ]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))


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
    msg = dict_ui.dict_deleted(removed or "")
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
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


def _morning_method_line(method, word_items, phrase_items):
    if not phrase_items and "фраз" in method.lower():
        return "В словаре пока нет фраз. Сегодня повтори слова, а фразы можно добавить через словарь."
    if not word_items and "слов" in method.lower():
        return "В словаре пока нет отдельных слов. Сегодня повтори фразы или добавь новые слова через словарь."
    return method


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
    if wd >= 5 or not pool:
        msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"), empty_hint=True)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
        return
    word_items = [w for w in pool if _dict_kind(w) == "word"]
    phrase_items = [w for w in pool if _dict_kind(w) == "phrase"]
    method = _morning_method_line(method, word_items, phrase_items)
    chosen_phrases = _r.sample(phrase_items, min(2, len(phrase_items)))
    chosen_words = _r.sample(word_items, min(3, len(word_items)))
    if not chosen_phrases and not chosen_words:
        msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"))
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
        return

    phrase_del_row = []
    phrase_lines = []
    if chosen_phrases:
        for w in chosen_phrases:
            word = _cap(_w_field(w, "word", "nl", "en"))
            ru = _w_field(w, "ru")
            phrase_lines.append((word, ru))
            try:
                idx = words.index(w)
                phrase_del_row.append(InlineKeyboardButton(f"❌ {word[:30]}", callback_data=f"worddel_{idx}"))
            except ValueError:
                pass

    word_del_row = []
    word_lines = []
    if chosen_words:
        for w in chosen_words:
            word = _cap(_w_field(w, "word", "nl", "en"))
            ru = _w_field(w, "ru")
            word_lines.append((word, ru))
            try:
                idx = words.index(w)
                word_del_row.append(InlineKeyboardButton(f"❌ {word[:14]}", callback_data=f"worddel_{idx}"))
            except ValueError:
                pass

    msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"), phrases=phrase_lines, words=word_lines)

    rows = []
    if with_kb:
        rows.extend([[btn] for btn in phrase_del_row])
        rows.extend(_chunks(word_del_row, 3))

    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )


# ================= ИГРА-ДЕТЕКТИВ =================
GAME_UI = {
    "русский": {
        "diff_q": "Выбери сложность:",
        "easy": "Лёгкая",
        "hard": "Тяжёлая",
        "title": "🕵️ Игра-детектив",
        "who": "Кто это?",
        "hint": "💡 Подсказка",
        "reveal": "😞 Сдаюсь",
        "suspect": "Подозреваемый:",
        "found": "✅ Дело раскрыто!",
        "answer": "Ответ",
        "again": "🕵️ Загадать ещё",
        "back": "◀️ Назад",
        "nohint": "Подсказок больше нет.",
        "wrong": "❌ Не то",
        "retry": "Ещё попытка - напиши ответ или возьми подсказку.",
    },
}

def _game_ui(_lang=None):
    return GAME_UI["русский"]


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
            await bot.send_message(chat_id=cid, text="Не смог загадать новое без повтора. Попробуй ещё раз через минуту.")
            return
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    _remember_game_answer(cid, d)
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
            [InlineKeyboardButton(ui["back"], callback_data="m_learn")],
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
        await q.message.reply_text(msg.text, entities=msg.entities)
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
        [InlineKeyboardButton(ui["back"], callback_data="m_learn")],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


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
    msg = learning_ui.levels(nl_label, en_label)
    kb = _levels_kb(nl_lvl, en_lvl, back)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


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
