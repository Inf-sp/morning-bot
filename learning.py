import logging
import random
import re
from datetime import datetime
from pathlib import Path
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
import config
from cleanup import open_cleanup, send_cleanup, handle_cleanup  # noqa: F401

_HERE = Path(__file__).parent
import store
import ai
import verify
import secure
from ui import dictionary as dict_ui
from ui import learning as learning_ui

_log = logging.getLogger(__name__)

LEVELS = ["simple", "medium", "hard"]
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
    import settings as _s
    return _code(_s.study_lang(cid))

def active_language(cid):
    return _language_for_code(_active_language_code(cid))

def _language_display(language):
    return f"{_flag(language)} {'Нидерландский' if _code(language) == 'nl' else 'Английский'}"

def _flag(language):
    return "🇳🇱" if _code(language) == "nl" else "🇬🇧"

def _level_label(level):
    return LEVEL_LABELS.get(level, "Средний")

# ================= ЕДИНЫЙ ТРЕНАЖЁР =================
TRAIN_FORMATS = ["gap", "tf", "card"]  # legacy — не используется в новом квизе

def _train_entries(cid, language):
    """Все записи словаря нужного языка с переводом — единый тренажёр, без деления
    на слова и фразы: [(term, ru), ...]."""
    code = _code(language)
    out = []
    for w in _ensure_dict(cid):
        if _dict_lang(w) != code:
            continue
        term = _entry_term(w)
        ru = _entry_translation(w)
        term, _grammar_note = _normalize_dict_term(code, _kind_of(term), term)
        if term and ru:
            out.append((str(term).strip(), str(ru).strip()))
    return out


def _train_full_entries(cid, language):
    """Полные записи словаря (term/translation/breakdown/examples), нужные для
    программной сборки карточки тренажёра без LLM."""
    code = _code(language)
    out = []
    for w in _ensure_dict(cid):
        if _dict_lang(w) != code:
            continue
        term = _entry_term(w)
        ru = _entry_translation(w)
        if term and ru:
            out.append(w)
    return out


def _clip_poll_explanation(text, limit=200):
    text = re.sub(r"\s+\n", "\n", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rstrip() + "…"


def _phrase_poll_question(blank_phrase, sentence_ru):
    msg = learning_ui.phrase_poll_question(blank_phrase, sentence_ru)
    return msg.text, msg.entities


def _phrase_poll_explanation(blank_phrase, correct, full_phrase, sentence_ru, extra=""):
    full_phrase = str(full_phrase or "").strip()
    sentence_ru = str(sentence_ru or "").strip()
    if full_phrase and sentence_ru:
        return _clip_poll_explanation(f"{full_phrase} → {sentence_ru}", limit=160)
    return _clip_poll_explanation(sentence_ru or full_phrase, limit=160)


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

Учебная карточка должна быстро объяснить выражение без повторов:
- исходная фраза;
- естественный русский перевод;
- одна строка разбора: intro_pattern — intro_explanation;
- один новый короткий пример с переводом.

Правила учебной карточки:
- intro_pattern — ключевое слово или всё устойчивое выражение. Если нужна грамматика, добавь её прямо в паттерн:
  "zin hebben om te + инфинитив", "beginnen met + существительное", "stoppen met + инфинитив",
  "kijken naar + существительное".
- intro_explanation — новая информация после заголовка "Разбор": значение конструкции, правило,
  особенность употребления или отличие от похожего выражения.
- Не делай intro_explanation простым повтором intro_pattern или перевода.
- Если выражение устойчивое, объясняй всё выражение, а не отдельные слова.
- card_example — новый короткий пример на {language}, не копия учебной фразы и не тестовая фраза.
- card_example_ru — естественный перевод card_example на русский.
- Не используй лингвистические термины без необходимости.

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
Новый пример обязан отличаться контекстом: другой субъект, другой объект и другое обстоятельство времени,
места или ситуации. Не повторяй целевое слово в видимом тексте test_blank_phrase вне пропуска.

Правила теста:
1. test_blank_phrase — новая фраза с ____ вместо целевого слова.
2. test_full_phrase — та же новая фраза полностью, с правильным словом.
3. correct — ровно пропущенное слово, без артиклей и лишних слов.
4. wrong — три правдоподобных неправильных варианта на {language}, той же части речи.
5. test_sentence_ru — перевод test_full_phrase на русский.
6. short_rule — короткая подсказка для результата теста, не дубль intro_pattern.
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
  "intro_pattern": "строка для разбора, например 'zin hebben om te + инфинитив'",
  "intro_explanation": "краткое объяснение значения/правила без повтора intro_pattern",
  "card_example": "новый короткий пример с той же конструкцией",
  "card_example_ru": "естественный перевод card_example на русский",
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

Для устойчивых выражений, фразовых конструкций и идиом focus_unit/construction должен быть смысловым блоком,
а не отдельным словом. Например:
- "Het is niet te doen" -> focus_unit/construction: "niet te doen", не "doen";
- "geen zin hebben in", "het maakt niet uit", "dat is de druppel", "ik heb er genoeg van",
  "zin hebben om", "bezig zijn met" — это цельные смысловые блоки.

Не возвращай placeholders, технические заглушки и фразы из промта в любых пользовательских полях.
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
    construction = str(d.get("focus_unit") or d.get("construction") or "").strip()
    construction_meaning = str(d.get("focus_explanation_ru") or d.get("construction_meaning") or "").strip()
    short_rule = str(d.get("rule_ru") or d.get("usage_note_ru") or d.get("short_rule") or "").strip()
    return {
        "blank_phrase": blank_phrase,
        "correct": correct,
        "target_token": str(d.get("target_token") or correct).strip(),
        "wrong": wrong,
        "sentence_ru": str(d.get("test_sentence_ru") or d.get("sentence_ru") or "").strip(),
        "test_full_phrase": full_phrase,
        "construction": construction,
        "construction_meaning": construction_meaning,
        "intro_pattern": str(d.get("intro_pattern") or construction).strip(),
        "intro_explanation": str(d.get("intro_explanation") or construction_meaning or short_rule).strip(),
        "card_example": str(d.get("card_example") or d.get("example") or "").strip(),
        "card_example_ru": str(d.get("card_example_ru") or d.get("example_ru") or "").strip(),
        "short_rule": short_rule,
        "detail": str(d.get("detail") or "").strip(),
        "other_forms": _filter_phrase_other_forms(other_forms, d),
        "explanation": str(d.get("rule_ru") or d.get("usage_note_ru") or d.get("short_rule") or d.get("explanation") or "").strip(),
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
    "verb", "глагол", "noun", "prep", "предлог", "infinitief", "infinitive",
    "инфинитив", "zelfstandig", "naamwoord",
}

_UI_PLACEHOLDER_PATTERNS = (
    "значение из учебной фразы в новом контексте",
    "смотри значение в переводе фразы",
    "смотри перевод фразы",
    "перевод зависит от контекста",
    "объяснение будет добавлено позже",
    "значение слова из фразы",
    "учебное значение",
    "новый контекст",
    "placeholder",
)

_UI_PLACEHOLDER_TOKENS = {"todo", "n/a", "none", "null"}

_PHRASE_FOCUS_FALLBACKS = {
    "niet te doen": {
        "focus": "niet te doen",
        "meaning": "устойчивое выражение: \"невозможно\", \"нереально\", \"слишком трудно\"",
        "rule": "Конструкция \"niet te + infinitief\" часто означает, что действие невозможно или почти невозможно выполнить.",
        "intro_pattern": "niet te + инфинитив",
        "intro_explanation": "действие невозможно или почти нереально выполнить.",
        "card_example": "Die drukte is niet te doen.",
        "card_example_ru": "Эта толпа невыносима.",
        "blank": "Deze opdracht is niet te ____.",
        "correct": "doen",
        "wrong": ["maken", "gaan", "zijn"],
        "ru": "Это задание невозможно выполнить.",
    },
    "geen zin": {
        "focus": "geen zin hebben in",
        "meaning": "не хотеть чего-то, не иметь желания что-то делать",
        "rule": "Конструкция \"geen zin hebben in\" значит, что у человека нет желания или настроения для действия или ситуации.",
        "intro_pattern": "geen zin hebben in + существительное",
        "intro_explanation": "не хотеть чего-то, не иметь настроения для ситуации.",
        "card_example": "We hebben geen zin in regen.",
        "card_example_ru": "Нам не хочется дождя.",
        "blank": "Zij heeft geen zin ____ die vergadering.",
        "correct": "in",
        "wrong": ["om", "met", "van"],
        "ru": "Ей не хочется на это собрание.",
    },
    "maakt niet uit": {
        "focus": "het maakt niet uit",
        "meaning": "это не важно, без разницы",
        "rule": "Выражение \"het maakt niet uit\" используют, когда выбор или деталь не имеет значения.",
        "intro_pattern": "het maakt niet uit",
        "intro_explanation": "используют, когда выбор или деталь не имеет значения.",
        "card_example": "Het maakt niet uit waar we zitten.",
        "card_example_ru": "Не важно, где мы сядем.",
        "blank": "Het maakt niet ____ welke trein we nemen.",
        "correct": "uit",
        "wrong": ["op", "mee", "af"],
        "ru": "Не важно, на какой поезд мы сядем.",
    },
    "dat is de druppel": {
        "focus": "dat is de druppel",
        "meaning": "это последняя капля",
        "rule": "Выражение \"dat is de druppel\" означает последнюю неприятность, после которой терпение заканчивается.",
        "intro_pattern": "dat is de druppel",
        "intro_explanation": "последняя неприятность, после которой терпение заканчивается.",
        "card_example": "Nog een boete, dat is de druppel.",
        "card_example_ru": "Ещё один штраф — это последняя капля.",
        "blank": "Nu is dat echt de ____.",
        "correct": "druppel",
        "wrong": ["regen", "dag", "vraag"],
        "ru": "Теперь это правда последняя капля.",
    },
    "genoeg van": {
        "focus": "ik heb er genoeg van",
        "meaning": "мне надоело, с меня хватит",
        "rule": "Выражение \"ergens genoeg van hebben\" значит, что человеку что-то надоело или он больше не хочет это терпеть.",
        "intro_pattern": "ergens genoeg van hebben",
        "intro_explanation": "говорят, когда что-то надоело и человек больше не хочет это терпеть.",
        "card_example": "Wij hebben genoeg van deze discussie.",
        "card_example_ru": "Нам надоела эта дискуссия.",
        "blank": "Zij heeft genoeg ____ het lawaai.",
        "correct": "van",
        "wrong": ["in", "op", "mee"],
        "ru": "Ей надоел шум.",
    },
    "zin om": {
        "focus": "zin hebben om",
        "meaning": "хотеть что-то сделать, иметь желание",
        "rule": "Конструкция \"zin hebben om te + infinitief\" говорит о желании сделать действие.",
        "intro_pattern": "zin hebben om te + инфинитив",
        "intro_explanation": "хотеть что-то сделать, иметь настроение или желание для действия.",
        "card_example": "Ik heb zin om te wandelen.",
        "card_example_ru": "Мне хочется пойти гулять.",
        "blank": "Wij hebben zin ____ te koken.",
        "correct": "om",
        "wrong": ["in", "van", "met"],
        "ru": "Нам хочется готовить.",
    },
    "bezig met": {
        "focus": "bezig zijn met",
        "meaning": "быть занятым чем-то, заниматься чем-то",
        "rule": "Конструкция \"bezig zijn met\" показывает, чем человек сейчас занят.",
        "intro_pattern": "bezig zijn met + существительное",
        "intro_explanation": "показывает, чем человек сейчас занят.",
        "card_example": "Ik ben bezig met mijn presentatie.",
        "card_example_ru": "Я занимаюсь своей презентацией.",
        "blank": "We zijn bezig ____ de planning.",
        "correct": "met",
        "wrong": ["om", "in", "van"],
        "ru": "Мы занимаемся планированием.",
    },
}


def _phrase_tokens(text):
    return [m.group(0).lower() for m in re.finditer(r"[\wÀ-ÖØ-öø-ÿ'-]+", str(text or ""), flags=re.UNICODE)]


def _normalize_phrase_for_compare(text):
    return " ".join(_phrase_tokens(text))


def _has_cyrillic(text):
    return bool(re.search(r"[А-Яа-яЁё]", str(text or "")))


def _looks_like_ui_placeholder(text):
    value = str(text or "").strip()
    if not value:
        return False
    low = value.lower()
    if any(pattern in low for pattern in _UI_PLACEHOLDER_PATTERNS):
        return True
    tokens = set(_phrase_tokens(low))
    if tokens & _UI_PLACEHOLDER_TOKENS:
        return True
    if re.search(r"\bN\s*/\s*A\b", value, flags=re.IGNORECASE):
        return True
    return False


def _phrase_card_has_placeholder(card):
    keys = (
        "phrase", "translation_ru", "focus_unit", "focus_explanation_ru", "rule_ru", "usage_note_ru",
        "blank_phrase", "test_blank_phrase", "test_sentence", "test_full_phrase", "correct",
        "correct_answer", "target_token", "sentence_ru", "test_sentence_ru", "construction",
        "construction_meaning", "intro_pattern", "intro_explanation", "card_example", "card_example_ru",
        "short_rule", "detail", "explanation",
    )
    for key in keys:
        if _looks_like_ui_placeholder(card.get(key)):
            return True
    for item in list(card.get("wrong") or []) + list(card.get("options") or []):
        if _looks_like_ui_placeholder(item):
            return True
    for item in card.get("other_forms") or []:
        if isinstance(item, dict) and any(_looks_like_ui_placeholder(v) for v in item.values()):
            return True
    return False


def _known_phrase_focus_unit(phrase):
    phrase_norm = _normalize_phrase_for_compare(phrase)
    if not phrase_norm:
        return ""
    for marker, spec in _PHRASE_FOCUS_FALLBACKS.items():
        marker_norm = _normalize_phrase_for_compare(marker)
        focus_norm = _normalize_phrase_for_compare(spec["focus"])
        if marker_norm in phrase_norm or focus_norm in phrase_norm:
            return spec["focus"]
    if "niet te" in phrase_norm:
        tokens = phrase_norm.split()
        for idx in range(len(tokens) - 2):
            if tokens[idx] == "niet" and tokens[idx + 1] == "te":
                return " ".join(tokens[idx:idx + 3])
    return ""


def _phrase_option_is_junk(option):
    option = str(option or "").strip()
    if not option:
        return True
    if _looks_like_ui_placeholder(option):
        return True
    if "____" in option or len(option) > 40:
        return True
    return False


def _phrase_pattern_token_present(token, learn_tokens):
    if token in learn_tokens:
        return True
    variants = {
        "hebben": {"heb", "hebt", "heeft", "hebben", "had", "hadden"},
        "zijn": {"ben", "bent", "is", "zijn", "was", "waren"},
    }
    return bool(variants.get(token, set()) & set(learn_tokens))


def _phrase_without_target_tokens(text, target):
    target_tokens = set(_phrase_tokens(target))
    return [t for t in _phrase_tokens(text) if t not in target_tokens]


def _phrase_repeats_source(learn_phrase, blank_phrase, correct):
    """Reject source-sentence clozes and near copies."""
    learn_norm = _normalize_phrase_for_compare(learn_phrase)
    full_norm = _normalize_phrase_for_compare(_phrase_full_from_blank(blank_phrase, correct))
    if not learn_norm or not full_norm:
        return True
    if learn_norm == full_norm:
        return True

    learn_tokens = _phrase_without_target_tokens(learn_phrase, correct)
    test_tokens = _phrase_without_target_tokens(full_norm, correct)
    if not learn_tokens or not test_tokens:
        return True
    test_counts = {}
    for token in test_tokens:
        test_counts[token] = test_counts.get(token, 0) + 1
    overlap = 0
    for token in learn_tokens:
        if test_counts.get(token, 0) > 0:
            overlap += 1
            test_counts[token] -= 1
    if len(learn_tokens) <= 4:
        learn_set = set(learn_tokens)
        new_tokens = [token for token in test_tokens if token not in learn_set]
        return len(new_tokens) < 2
    return (overlap / max(1, len(learn_tokens))) > 0.60


def _phrase_text_repeats_source(source, candidate):
    source_norm = _normalize_phrase_for_compare(source)
    candidate_norm = _normalize_phrase_for_compare(candidate)
    if not source_norm or not candidate_norm:
        return True
    if source_norm == candidate_norm:
        return True
    source_tokens = source_norm.split()
    candidate_tokens = candidate_norm.split()
    if not source_tokens or not candidate_tokens:
        return True
    candidate_counts = {}
    for token in candidate_tokens:
        candidate_counts[token] = candidate_counts.get(token, 0) + 1
    overlap = 0
    for token in source_tokens:
        if candidate_counts.get(token, 0) > 0:
            overlap += 1
            candidate_counts[token] -= 1
    if len(source_tokens) <= 4:
        source_set = set(source_tokens)
        new_tokens = [token for token in candidate_tokens if token not in source_set]
        return len(new_tokens) < 2
    source_set = set(source_tokens)
    new_tokens = [token for token in candidate_tokens if token not in source_set]
    return (overlap / max(1, len(source_tokens))) > 0.60 and len(new_tokens) < 2


def _phrase_blank_repeats_target(blank_phrase, correct):
    visible_tokens = _phrase_tokens(str(blank_phrase or "").replace("____", " "))
    target_tokens = set(_phrase_tokens(correct))
    return bool(target_tokens and any(t in target_tokens for t in visible_tokens))


def _filter_phrase_other_forms(other_forms, card):
    if not other_forms:
        return []
    main = " ".join(str(card.get(k) or "").lower() for k in (
        "construction", "construction_meaning", "intro_pattern", "intro_explanation", "short_rule",
    ))
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


def _phrase_card_consistency_check(learn_phrase, learn_ru, card):
    """Проверяет карточку и возвращает (ok, reason). reason — короткий код первой
    провалившейся проверки, для диагностики частых отказов тренажёра в логах."""
    learn_phrase = str(learn_phrase or "").strip()
    learn_ru = str(learn_ru or "").strip()
    if _phrase_card_has_placeholder(card):
        return False, "placeholder"
    blank = str(card.get("test_sentence") or card.get("test_blank_phrase") or card.get("blank_phrase") or "").strip()
    full = str(card.get("test_full_phrase") or "").strip()
    correct = str(card.get("correct_answer") or card.get("correct") or "").strip()
    target = str(card.get("target_token") or correct).strip()
    construction = str(card.get("focus_unit") or card.get("construction") or "").strip()
    construction_meaning = str(card.get("focus_explanation_ru") or card.get("construction_meaning") or "").strip()
    intro_pattern = str(card.get("intro_pattern") or construction).strip()
    intro_explanation = str(card.get("intro_explanation") or construction_meaning or card.get("short_rule") or "").strip()
    card_example = str(card.get("card_example") or "").strip()
    card_example_ru = str(card.get("card_example_ru") or "").strip()
    short_rule = str(card.get("rule_ru") or card.get("usage_note_ru") or card.get("short_rule") or "").strip()
    test_ru = str(card.get("test_sentence_ru") or card.get("sentence_ru") or "").strip()
    wrong = _clean_phrase_options(correct, list(card.get("wrong") or []), needed=3)
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
    failed_self_check = [k for k in required_checks if self_check.get(k) is not True]
    if failed_self_check:
        return False, f"self_check:{failed_self_check[0]}"
    missing = [
        name for name, value in [
            ("learn_phrase", learn_phrase), ("learn_ru", learn_ru), ("blank", blank), ("full", full),
            ("correct", correct), ("target", target), ("construction", construction),
            ("construction_meaning", construction_meaning), ("intro_pattern", intro_pattern),
            ("intro_explanation", intro_explanation), ("card_example", card_example),
            ("card_example_ru", card_example_ru), ("short_rule", short_rule), ("test_ru", test_ru),
        ] if not value
    ]
    if missing:
        return False, f"missing:{missing[0]}"
    if not _has_cyrillic(learn_ru):
        return False, "no_cyrillic:learn_ru"
    if not _has_cyrillic(construction_meaning):
        return False, "no_cyrillic:construction_meaning"
    if not _has_cyrillic(intro_explanation):
        return False, "no_cyrillic:intro_explanation"
    if not _has_cyrillic(card_example_ru):
        return False, "no_cyrillic:card_example_ru"
    if not _has_cyrillic(short_rule):
        return False, "no_cyrillic:short_rule"
    if _normalize_phrase_for_compare(intro_pattern) == _normalize_phrase_for_compare(intro_explanation):
        return False, "intro_pattern_equals_explanation"
    if "____" not in blank:
        return False, "no_blank_marker"
    if len(wrong) < 3:
        return False, "not_enough_wrong_options"
    if _normalize_phrase_for_compare(learn_phrase) == _normalize_phrase_for_compare(full):
        return False, "full_equals_learn_phrase"
    if _normalize_phrase_for_compare(learn_phrase) == _normalize_phrase_for_compare(blank):
        return False, "blank_equals_learn_phrase"
    if _phrase_repeats_source(learn_phrase, blank, correct):
        return False, "blank_repeats_source"
    if _phrase_blank_repeats_target(blank, correct):
        return False, "blank_repeats_target"
    if _phrase_text_repeats_source(learn_phrase, card_example):
        return False, "example_repeats_source"
    if _normalize_phrase_for_compare(card_example) == _normalize_phrase_for_compare(full):
        return False, "example_equals_full"

    known_focus = _known_phrase_focus_unit(learn_phrase)
    if known_focus and _normalize_phrase_for_compare(construction) != _normalize_phrase_for_compare(known_focus):
        return False, "construction_mismatch_known_focus"

    learn_tokens = set(_phrase_tokens(learn_phrase))
    learn_token_list = _phrase_tokens(learn_phrase)
    full_tokens = set(_phrase_tokens(full))
    target_low = target.lower()
    correct_low = correct.lower()
    if target_low not in learn_tokens or correct_low not in full_tokens:
        return False, "target_or_correct_not_in_tokens"

    pattern_tokens = [
        t for t in _phrase_tokens(construction)
        if t not in _PATTERN_PLACEHOLDERS and len(t) > 1
    ]
    if pattern_tokens and not all(_phrase_pattern_token_present(t, learn_tokens) for t in pattern_tokens):
        return False, "pattern_token_missing"
    if pattern_tokens and correct_low not in pattern_tokens and target_low not in pattern_tokens:
        return False, "correct_not_in_pattern"
    construction_low = construction.lower()
    if any(marker in construction_low for marker in ("+ прилагательное", "+ adjective", "+ adjectief")):
        positions = [i for i, t in enumerate(learn_token_list) if t == target_low]
        if not positions or all(i >= len(learn_token_list) - 1 for i in positions):
            return False, "adjective_position_invalid"
    return True, ""


def _phrase_card_is_consistent(learn_phrase, learn_ru, card):
    ok, _reason = _phrase_card_consistency_check(learn_phrase, learn_ru, card)
    return ok


async def _validate_phrase_card_semantics(phrase, ru, language, card):
    prompt = f"""
Проверь карточку фразового тренажёра для языка: {language}.

Учебная фраза: {phrase}
Русский перевод учебной фразы: {ru}
Паттерн: {card.get("construction") or ""}
Значение паттерна: {card.get("construction_meaning") or ""}
Строка разбора для пользователя: {card.get("intro_pattern") or ""}
Объяснение в разборе: {card.get("intro_explanation") or ""}
Целевой токен: {card.get("target_token") or card.get("correct") or ""}

Пример в учебной карточке: {card.get("card_example") or ""}
Перевод примера: {card.get("card_example_ru") or ""}

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
- разбор после заголовка повторяет паттерн или перевод и не добавляет новой информации;
- пример в учебной карточке не использует тот же паттерн;
- пример в учебной карточке копирует учебную фразу или тестовую фразу;
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
    focus = _known_phrase_focus_unit(phrase)
    if not focus:
        return {}
    spec = None
    focus_norm = _normalize_phrase_for_compare(focus)
    for item in _PHRASE_FOCUS_FALLBACKS.values():
        if _normalize_phrase_for_compare(item["focus"]) == focus_norm:
            spec = item
            break
    if not spec:
        return {}
    correct = spec["correct"]
    blank_phrase = spec["blank"]
    if _phrase_repeats_source(phrase, blank_phrase, correct) or _phrase_blank_repeats_target(blank_phrase, correct):
        return {}
    wrong = _clean_phrase_options(correct, spec["wrong"], needed=3)
    if len(wrong) < 3:
        return {}
    self_check = {
        "translation_matches_learning_phrase": True,
        "pattern_present_in_learning_phrase": True,
        "target_token_role_ok": True,
        "learning_phrase_natural": True,
        "test_checks_same_rule": True,
        "test_is_new_not_copy": True,
        "no_mixed_meanings": True,
    }
    return {
        "blank_phrase": blank_phrase,
        "correct": correct,
        "wrong": wrong,
        "sentence_ru": spec["ru"],
        "test_full_phrase": _phrase_full_from_blank(blank_phrase, correct),
        "construction": spec["focus"],
        "construction_meaning": spec["meaning"],
        "intro_pattern": spec.get("intro_pattern") or spec["focus"],
        "intro_explanation": spec.get("intro_explanation") or spec["meaning"],
        "card_example": spec.get("card_example") or "",
        "card_example_ru": spec.get("card_example_ru") or "",
        "short_rule": spec["rule"],
        "detail": spec["rule"],
        "other_forms": [],
        "explanation": spec["rule"],
        "self_check": self_check,
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
        if item and item.lower() not in seen and not _phrase_option_is_junk(item):
            clean_wrong.append(item)
            seen.add(item.lower())
        if len(clean_wrong) >= needed:
            break
    return clean_wrong


def _strip_article(lang, term):
    term = (term or "").strip()
    if lang == "nl":
        return re.sub(r"^(de|het|een)\s+", "", term, flags=re.I)
    if lang == "en":
        return re.sub(r"^(to|the|a|an)\s+", "", term, flags=re.I)
    return term


def _blank_from_example(term, example_text):
    """Вставляет пропуск на месте term в сохранённом примере — регистронезависимо.
    Сначала пробует полный term (с артиклем), затем term без артикля — чтобы
    сохранить в blank_phrase ровно то, что реально стоит в примере. Возвращает
    (blank_phrase, correct) или ("", "") если term не найден в тексте буквально."""
    if not example_text:
        return "", ""
    term = (term or "").strip()
    term_bare = _strip_article("nl", _strip_article("en", term)).strip()
    for candidate in (term, term_bare):
        if not candidate:
            continue
        pattern = re.compile(re.escape(candidate), re.I)
        m = pattern.search(example_text)
        if m:
            return pattern.sub("____", example_text, count=1), m.group(0)
    return "", ""


def _dict_distractors(entry, correct, other_entries, needed=3):
    """Дистракторы для wrong-варианта — термины других слов того же словаря,
    без LLM."""
    term_self = _entry_term(entry)
    pool = [
        _cap(_entry_term(w)) for w in other_entries
        if _entry_term(w) != term_self and _entry_term(w)
    ]
    random.shuffle(pool)
    return _clean_phrase_options(correct, pool, needed=needed)


def _build_programmatic_card(entry, other_entries, lang):
    """Собирает тест-карточку тренажёра из уже сохранённых term/translation/
    breakdown/examples записи словаря — без единого LLM-вызова. Формат
    результата совпадает с _gen_consistent_phrase_card, чтобы вся остальная
    логика тренажёра (интро/quiz/true-false/feedback) работала без изменений."""
    term = _cap(_entry_term(entry))
    translation = _entry_translation(entry)
    breakdown = entry.get("breakdown") or ""
    examples = entry.get("examples") or []
    example_text = examples[0].get("text") if examples else ""
    example_ru = examples[0].get("translation") if examples else ""

    blank_phrase, correct = _blank_from_example(term, example_text) if example_text else ("", "")
    sentence_ru = example_ru or translation
    if not blank_phrase:
        # Нет сохранённого примера, из которого можно честно вырезать слово —
        # раньше здесь была вырожденная фраза "____ — перевод" без контекста.
        # Лучше явно сказать вызывающему коду, что программная карточка
        # недоступна, чем показывать бессмысленный пропуск.
        return {}

    wrong = _dict_distractors(entry, correct, other_entries, needed=3)
    if len(wrong) < 3:
        return {}

    return {
        "blank_phrase": blank_phrase,
        "correct": correct,
        "wrong": wrong,
        "sentence_ru": sentence_ru,
        "test_full_phrase": _phrase_full_from_blank(blank_phrase, correct),
        "intro_pattern": term,
        "intro_explanation": breakdown or translation,
        "card_example": example_text or "",
        "card_example_ru": example_ru or "",
        "short_rule": breakdown or f"{term} — {translation}",
        "detail": breakdown or f"{term} — {translation}",
        "explanation": breakdown or f"{term} — {translation}",
        "programmatic": True,
    }


def _bump_train_shown_count(cid, entry):
    """Считает показы слова в тренажёре — определяет, когда изредка подмешать
    LLM-карточку для разнообразия (см. _TRAIN_LLM_REFRESH_EVERY). Не влияет на
    last_shown_at (используется в других местах для приоритизации утренней подборки)."""
    words = store.get_list(config.DICT_KEY, cid)
    term_self = _entry_term(entry)
    lang_self = _dict_lang(entry)
    for idx, w in enumerate(words):
        if _dict_lang(w) == lang_self and _entry_term(w) == term_self:
            words[idx]["train_shown_count"] = int(w.get("train_shown_count") or 0) + 1
            store.set_list(config.DICT_KEY, cid, words)
            return


_TRAIN_LLM_REFRESH_EVERY = 3  # раз в столько показов одного слова — новая LLM-карточка для разнообразия.
# Программный путь (см. _build_programmatic_card) берёт тестовую фразу из ЕДИНСТВЕННОГО
# сохранённого примера слова — того же, что уже показан на intro-карточке — и просто
# вырезает слово, поэтому воспринимается как повтор экрана. Каждые 3 показа вместо 5
# заметно чаще подключается настоящий новый контекст от LLM.


async def _gen_train_card(cid, entry, other_entries, language, lang_code, show_count=0):
    """Основной путь тренажёра: программная карточка без LLM. Изредка (не каждый
    показ) подмешивает LLM-карточку для разнообразия — результат не сохраняется
    обратно в словарь, это одноразовое разнообразие."""
    if show_count and show_count % _TRAIN_LLM_REFRESH_EVERY == 0:
        term = _cap(_entry_term(entry))
        ru = _entry_translation(entry)
        card = await _gen_consistent_phrase_card(term, ru, language)
        if card:
            return card
    return _build_programmatic_card(entry, other_entries, lang_code)


async def _gen_consistent_phrase_card(phrase, ru, language, avoid_tests=None, attempts=3):
    for attempt in range(max(1, attempts)):
        card = await _gen_phrase_quiz_card(phrase, ru, language, avoid_tests=avoid_tests)
        correct_answer = card.get("correct") or ""
        clean_wrong = _clean_phrase_options(correct_answer, list(card.get("wrong") or []), needed=3)
        blank_phrase = card.get("blank_phrase") or ""
        ok, reason = _phrase_card_consistency_check(phrase, ru, card)
        if (
            correct_answer
            and "____" in blank_phrase
            and len(clean_wrong) >= 3
            and not _phrase_repeats_source(phrase, blank_phrase, correct_answer)
            and not _phrase_blank_repeats_target(blank_phrase, correct_answer)
            and ok
            and await _validate_phrase_card_semantics(phrase, ru, language, card)
        ):
            card["wrong"] = clean_wrong[:3]
            return card
        if not ok:
            reason = reason or "unknown"
        elif not correct_answer or "____" not in blank_phrase or len(clean_wrong) < 3:
            reason = "malformed_card"
        else:
            reason = "semantic_validation_failed"
        _log.info("phrase_card_rejected phrase=%r language=%s attempt=%d reason=%s",
                   phrase, language, attempt + 1, reason)
    fallback = _fallback_phrase_quiz_card(phrase, ru, language)
    if fallback and _phrase_card_is_consistent(phrase, ru, fallback):
        return fallback
    _log.info("phrase_card_fallback_unavailable phrase=%r language=%s", phrase, language)
    return {}


def _train_back_target(language=None):
    return "m_learn"


def _train_again_kb(language=None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё", callback_data="train_next")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=_train_back_target(language))],
    ])


def _phrase_unavailable_kb(language=None):
    back = _train_back_target(language)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Следующая", callback_data="train_next")],
        [InlineKeyboardButton("Повторить позже", callback_data=back)],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
    ])


async def train_start(bot, cid, language, mode=None):
    """Единый тренажёр: без деления на режимы слов/фраз — все записи словаря
    учатся одинаково (карточка + тест с пропуском)."""
    store.challenge_state.pop(str(cid), None)
    store.game_state.pop(str(cid), None)
    store.pending_input.pop(str(cid), None)
    if not _train_entries(cid, language):
        code = _code(language)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "📖 Открыть словарь", callback_data=f"a_dictlang_{code}_from_menu")]])
        await bot.send_message(chat_id=cid,
            text=f"{_flag(language)} В словаре нет слов или фраз с переводом. Добавь записи через словарь.",
            reply_markup=kb)
        return
    store.train_state[str(cid)] = {
        "lang": language,
        "round": 0,
        "used_entries": [],
    }
    await _render_next_train_quiz(bot, cid)



_MISTAKE_EVERY_N_ROUNDS = 3


async def _render_train_quiz(bot, cid):
    """Единая карточка тренажёра: интро (термин + перевод + пример) и отдельный тест
    с пропуском — см. phrase_intro_continue(). Один формат для всех записей словаря.

    Каждый _MISTAKE_EVERY_N_ROUNDS-й раунд, если есть открытая ошибка на активном
    языке — показывает её вместо случайного слова (§ record_mistake/mistake_review_card),
    без отдельного раздела «Повторение ошибок»."""
    import random as _r
    store.pending_input.pop(str(cid), None)
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    language = st["lang"]
    lang_code = _code(language)
    round_n = st.get("round", 0)
    if round_n > 0 and round_n % _MISTAKE_EVERY_N_ROUNDS == 0:
        mistake = next_open_mistake(cid, lang_code)
        if mistake:
            msg = learning_ui.mistake_review_card(mistake)
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
            return
    full_entries = _train_full_entries(cid, language)
    if not full_entries:
        await bot.send_message(chat_id=cid, text="В словаре нет записей с переводом."); return

    used = st.get("used_entries", [])
    available = [(i, e) for i, e in enumerate(full_entries) if i not in used]
    if not available:
        used = []
        available = list(enumerate(full_entries))
        st["used_entries"] = used
    idx, entry = _r.choice(available)
    used.append(idx)
    st["used_entries"] = used

    phrase, _note = _normalize_dict_term(lang_code, _kind_of(_entry_term(entry)), _entry_term(entry))
    ru = _entry_translation(entry)
    show_count = int(entry.get("train_shown_count") or 0)
    card = await _gen_train_card(cid, entry, full_entries, language, lang_code, show_count=show_count)
    _bump_train_shown_count(cid, entry)
    correct_answer = card.get("correct") or ""
    wrong = list(card.get("wrong") or [])
    clean_wrong = _clean_phrase_options(correct_answer, wrong, needed=3)
    blank_phrase = card.get("blank_phrase") or ""
    is_programmatic = bool(card.get("programmatic"))
    if (
        not correct_answer
        or "____" not in blank_phrase
        or len(clean_wrong) < 3
        or (not is_programmatic and not _phrase_card_is_consistent(phrase, ru, card))
    ):
        await bot.send_message(
            chat_id=cid,
            text="Не получилось собрать хорошую карточку.\nПопробуй следующую.",
            reply_markup=_phrase_unavailable_kb(language),
        )
        return

    options = [correct_answer] + clean_wrong[:3]
    _r.shuffle(options)
    correct_idx = options.index(correct_answer)
    st.update({
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

    st["intro_pattern"] = card.get("intro_pattern") or card.get("construction") or ""
    st["intro_explanation"] = card.get("intro_explanation") or card.get("construction_meaning") or card.get("short_rule") or ""

    msg = learning_ui.phrase_intro_card(
        phrase,
        ru,
        st["intro_pattern"],
        st["intro_explanation"],
        card.get("card_example") or "",
        card.get("card_example_ru") or "",
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧩 Тест", callback_data="phrase_intro_test")],
        [InlineKeyboardButton("✅ Выучил", callback_data="phrase_intro_mastered")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=_train_back_target(language))],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def phrase_intro_continue(bot, cid):
    """Реакция на «Тест» после учебной карточки — случайно выбирает формат теста
    (quiz poll с 4 вариантами или короткое утверждение да/нет), чтобы форматы
    чередовались и тренажёр не приедался."""
    st = store.train_state.get(str(cid))
    if not st or not st.get("phrase_pending_quiz"):
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    st["phrase_pending_quiz"] = False
    st["phrase_stage"] = "quiz"

    roll = random.random()
    if roll < 0.34:
        await _send_phrase_truefalse(bot, cid, st)
        return
    if roll < 0.67:
        await _send_phrase_smart_reveal(bot, cid, st)
        return

    language = st["lang"]
    phrase = st.get("phrase_full", "")
    blank_phrase = st.get("sentence", "")
    correct_answer = st.get("meaning", "")
    options = st.get("options", [])
    correct_idx = st.get("correct_idx", 0)

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
        reply_markup=_train_again_kb(language),
    )
    if getattr(msg, "poll", None):
        store.train_polls[msg.poll.id] = str(cid)


def _truefalse_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да", callback_data="phrase_tf_yes"),
         InlineKeyboardButton("❌ Нет", callback_data="phrase_tf_no")],
    ])


async def _send_phrase_smart_reveal(bot, cid, st):
    """Третий формат теста: «умное раскрытие» — сначала вопрос на перевод текущего
    слова/фразы, подсказка по кнопке, ответ текстом, результат с Понял/Повторить
    позже. Строится из уже собранной карточки intro-этапа, без нового LLM-запроса."""
    lang = st["lang"]
    ru = st.get("sentence_ru") or st.get("ru", "")
    correct = st.get("phrase_full") or st.get("word", "")
    hint = st.get("intro_pattern") or st.get("intro_explanation", "")
    explanation = st.get("phrase_short_rule") or st.get("phrase_explanation", "")
    if not ru or not correct:
        await _send_phrase_truefalse(bot, cid, st)
        return
    store.smart_reveal_state[str(cid)] = {
        "ru": ru, "hint": hint, "lang": lang, "correct": correct, "explanation": explanation,
    }
    msg = learning_ui.smart_reveal_question(_flag(lang), ru)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                            reply_markup=learning_ui.smart_reveal_kb(show_hint=bool(hint)))


async def _send_phrase_truefalse(bot, cid, st):
    """Второй формат теста: утверждение «в этой фразе пропущено слово X» — да или нет.
    Строится из уже провалидированной карточки, без нового LLM-запроса."""
    blank_phrase = st.get("sentence", "")
    correct_answer = st.get("meaning", "")
    options = [o for o in st.get("options", []) if o]
    wrong_options = [o for o in options if o.lower() != str(correct_answer).lower()]

    is_true = random.random() < 0.5 or not wrong_options
    shown_word = correct_answer if is_true else random.choice(wrong_options)
    st["truefalse_is_true"] = is_true

    statement = blank_phrase.replace("____", f"«{shown_word}»") if "____" in blank_phrase else blank_phrase
    msg = learning_ui.phrase_truefalse_question(statement)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_truefalse_kb())


async def phrase_truefalse_answer(bot, cid, answered_yes):
    st = store.train_state.get(str(cid))
    if not st or "truefalse_is_true" not in st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    is_true = st.pop("truefalse_is_true")
    correct_idx = int(st.get("correct_idx", 0))
    options = st.get("options") or []
    is_correct = answered_yes == is_true
    idx = correct_idx if is_correct else next((i for i in range(len(options)) if i != correct_idx), correct_idx)
    await _send_train_feedback(bot, cid, idx, st)


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
            reply_markup=_train_again_kb(language),
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
        [InlineKeyboardButton("⬅️ Назад", callback_data=_train_back_target(st.get("lang", "")))],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


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
    lang = st.get("lang", "нидерландский")

    is_correct = idx == correct_idx
    if is_correct:
        msg = learning_ui.phrase_quiz_result(st, True)
        st["round"] = st.get("round", 0) + 1
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Следующая", callback_data="train_next")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=_train_back_target(lang))],
        ])
    else:
        st["phrase_error_count"] = int(st.get("phrase_error_count", 0)) + 1
        repeated_error = st["phrase_error_count"] >= 2
        if repeated_error:
            st["needs_review"] = True
            record_mistake(
                cid, _code(lang), st.get("word", ""),
                wrong=options[idx] if idx < len(options) else "",
                correct=st.get("meaning", "") or options[correct_idx],
                explanation=st.get("phrase_short_rule", "") or st.get("phrase_explanation", ""),
            )
        msg = learning_ui.phrase_quiz_result(st, False, repeated_error=repeated_error)
        if repeated_error:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Разобрать", callback_data="phrase_explain"),
                 InlineKeyboardButton("Новый пример", callback_data="phrase_new_example")],
                [InlineKeyboardButton("Дальше", callback_data="train_next"),
                 InlineKeyboardButton("⬅️ Назад", callback_data=_train_back_target(lang))],
            ])
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("Новый пример", callback_data="phrase_new_example"),
                 InlineKeyboardButton("Дальше", callback_data="train_next")],
                [InlineKeyboardButton("⬅️ Назад", callback_data=_train_back_target(lang))],
            ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def _render_next_train_quiz(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    await _render_train_quiz(bot, cid)


async def train_next(bot, cid):
    st = store.train_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Тренажёр устарел, открой заново."); return
    store.pending_input.pop(str(cid), None)
    await _render_next_train_quiz(bot, cid)


async def send_train_lang_select(bot, cid):
    language = active_language(cid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"▶️ {_language_display(language)}", callback_data=f"a_train_{_code(language)}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_learn")],
    ])
    msg = learning_ui.train_lang_select()
    await bot.send_message(chat_id=cid,
        text=msg.text,
        entities=msg.entities, reply_markup=kb)


# ================= ОБРАТНЫЙ ПЕРЕВОД =================
def generate_challenge(language, level):
    level_label = LEVEL_LABELS.get(level, "средний")
    return ai.llm(f"Дай ОДНУ фразу на русском для перевода на {language}. Уровень сложности: {level_label.lower()}, "
                  f"бытовая/рабочая ситуация. Только русская фраза, без кавычек.", 200, 1.0, tier="cheap").strip()


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
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_learn")],
    ])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
    return True


# ================= УМНОЕ РАСКРЫТИЕ ОТВЕТА =================
async def smart_reveal_show_hint(bot, cid, q=None):
    st = store.smart_reveal_state.get(str(cid))
    if not st:
        return
    st["hint_shown"] = True
    msg = learning_ui.smart_reveal_question(_flag(st["lang"]), st["ru"], st.get("hint"))
    kb = learning_ui.smart_reveal_kb(show_hint=False)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def smart_reveal_ask_answer(bot, cid):
    st = store.smart_reveal_state.get(str(cid))
    if not st:
        return
    store.pending_input[str(cid)] = "smart_reveal_answer"
    await bot.send_message(chat_id=cid, text="Напиши свой ответ следующим сообщением.")


async def smart_reveal_answer(bot, cid, text):
    st = store.smart_reveal_state.pop(str(cid), None)
    if not st:
        return False
    store.pending_input.pop(str(cid), None)
    await _smart_reveal_finish(bot, cid, st, text)
    return True


async def smart_reveal_skip(bot, cid):
    st = store.smart_reveal_state.pop(str(cid), None)
    if not st:
        return
    await _smart_reveal_finish(bot, cid, st, None)


async def _smart_reveal_finish(bot, cid, st, answer):
    lang = st["lang"]
    known_correct = st.get("correct")
    if known_correct:
        # Вопрос построен из уже известного слова словаря (_send_phrase_smart_reveal) —
        # сравниваем текст напрямую, без нового LLM-запроса.
        correct = known_correct
        explanation = st.get("explanation", "")
        is_wrong = bool(answer) and not _fuzzy(answer, correct)
    elif answer:
        try:
            r = check_translation(lang, st["ru"], answer)
        except Exception as e:
            await verify.safe_error(bot, cid, e); return
        correct = str(r.get("correct") or "").strip() or answer
        explanation = str(r.get("note") or r.get("error") or "").strip()
        is_wrong = not r.get("ok")
    else:
        # Пропустил без ответа и без готового правильного варианта — проверять нечего.
        await bot.send_message(chat_id=cid, text="Хорошо, идём дальше.")
        return
    store.smart_reveal_result_state[str(cid)] = {
        "lang": lang, "term": st["ru"], "wrong": answer or "", "correct": correct, "explanation": explanation,
    }
    if is_wrong:
        record_mistake(cid, _code(lang), st["ru"], wrong=answer, correct=correct, explanation=explanation)
    msg = learning_ui.smart_reveal_result(_flag(lang), lang, correct, explanation)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup)


async def smart_reveal_understood(bot, cid):
    store.smart_reveal_result_state.pop(str(cid), None)
    await bot.send_message(chat_id=cid, text="Отлично, идём дальше.")


async def smart_reveal_later(bot, cid):
    r = store.smart_reveal_result_state.pop(str(cid), None)
    if r:
        record_mistake(cid, r["lang"], r["term"], wrong=r.get("wrong", ""),
                        correct=r["correct"], explanation=r.get("explanation", ""))
    await bot.send_message(chat_id=cid, text="Хорошо, вернёмся к этому в «Повторении ошибок».")


# ================= ГЛАГОЛ ДНЯ / ПОСЛОВИЦА =================
def _proverb_kb(code):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Ещё вариант", callback_data=f"a_proverb_{code}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_learn")],
    ])

def _proverb_entities_card(flag, original, analogs=None, meaning="", examples=None, example_ru=""):
    msg = learning_ui.proverb_card(flag, original, analogs, meaning, examples, example_ru)
    return msg.text, msg.entities


_PROVERB_FALLBACKS = {
    "nl": [
        {
            "nl": "Dat is de druppel!",
            "en": "",
            "analogs": ["это последняя капля"],
            "type": "идиома",
            "meaning": "Когда мелкие неприятности копятся, и очередная мелочь окончательно добивает.",
            "example": "Eerst was mijn trein te laat, toen morste ik koffie over mijn shirt... En nu dit?! Dat is de druppel!",
            "example_ru": "Сначала поезд опоздал, потом я залил кофе рубашку... А теперь еще и это?! Ну всё, это последняя капля!",
        },
        {
            "nl": "Geen probleem.",
            "en": "",
            "analogs": ["без проблем"],
            "type": "разговорная фраза",
            "meaning": "Когда спокойно соглашаются помочь или показывают, что всё нормально.",
            "example": "Kun je me straks even bellen? Geen probleem.",
            "example_ru": "Можешь потом мне позвонить? Без проблем.",
        },
        {
            "nl": "Komt goed.",
            "en": "",
            "analogs": ["всё будет нормально"],
            "type": "разговорная фраза",
            "meaning": "Когда хотят коротко успокоить человека или показать, что вопрос решится.",
            "example": "Maak je geen zorgen, ik regel het morgen. Komt goed.",
            "example_ru": "Не переживай, завтра я всё улажу. Всё будет нормально.",
        },
        {
            "nl": "Doe maar rustig aan.",
            "en": "",
            "analogs": ["не торопись"],
            "type": "разговорная фраза",
            "meaning": "Когда человеку предлагают не спешить и действовать спокойнее.",
            "example": "Je hoeft niet te rennen. Doe maar rustig aan.",
            "example_ru": "Тебе не нужно бежать. Не торопись.",
        },
        {
            "nl": "Ik zie wel.",
            "en": "",
            "analogs": ["посмотрим"],
            "type": "разговорная фраза",
            "meaning": "Когда пока не принимают решение и оставляют всё открытым.",
            "example": "Misschien ga ik mee, maar ik zie wel.",
            "example_ru": "Может, я пойду с вами, но пока посмотрим.",
        },
        {
            "nl": "Laat maar.",
            "en": "",
            "analogs": ["забей"],
            "type": "разговорная фраза",
            "meaning": "Когда больше не хотят объяснять, спорить или продолжать тему.",
            "example": "Nee, het lukt niet meer. Laat maar.",
            "example_ru": "Нет, уже не получится. Забей.",
        },
        {
            "nl": "Het valt mee.",
            "en": "",
            "analogs": ["всё не так плохо"],
            "type": "разговорная фраза",
            "meaning": "Когда ситуация оказалась легче или приятнее, чем ожидалось.",
            "example": "Ik dacht dat het examen moeilijk zou zijn, maar het valt mee.",
            "example_ru": "Я думал, экзамен будет сложным, но всё оказалось не так плохо.",
        },
        {
            "nl": "Ik ben er klaar mee.",
            "en": "",
            "analogs": ["с меня хватит"],
            "type": "разговорная фраза",
            "meaning": "Когда человек устал от ситуации и больше не хочет с ней мириться.",
            "example": "Elke week hetzelfde gedoe. Ik ben er klaar mee.",
            "example_ru": "Каждую неделю одна и та же возня. С меня хватит.",
        },
        {
            "nl": "Dat komt goed uit.",
            "en": "",
            "analogs": ["это как раз кстати"],
            "type": "разговорная фраза",
            "meaning": "Когда что-то удобно совпало с планами или ситуацией.",
            "example": "Je bent morgen vrij? Dat komt goed uit.",
            "example_ru": "Ты завтра свободен? Это как раз кстати.",
        },
        {
            "nl": "Daar heb ik geen zin in.",
            "en": "",
            "analogs": ["мне совсем не хочется"],
            "type": "разговорная фраза",
            "meaning": "Когда прямо говорят, что нет желания что-то делать.",
            "example": "Nog een vergadering van twee uur? Daar heb ik geen zin in.",
            "example_ru": "Еще одно двухчасовое совещание? Мне совсем не хочется.",
        },
    ],
    "en": [
        {
            "nl": "",
            "en": "No worries.",
            "analogs": ["не переживай"],
            "type": "разговорная фраза",
            "meaning": "Когда хотят показать, что всё нормально и проблемы нет.",
            "example": "Sorry, I forgot to reply yesterday. No worries.",
            "example_ru": "Прости, я вчера забыл ответить. Не переживай.",
        },
        {
            "nl": "",
            "en": "That makes sense.",
            "analogs": ["логично"],
            "type": "разговорная фраза",
            "meaning": "Когда объяснение звучит понятно и разумно.",
            "example": "You took the earlier train to avoid the rain? That makes sense.",
            "example_ru": "Ты сел на поезд пораньше, чтобы не попасть под дождь? Логично.",
        },
        {
            "nl": "",
            "en": "I'm in.",
            "analogs": ["я с вами"],
            "type": "разговорная фраза",
            "meaning": "Когда человек соглашается участвовать в плане.",
            "example": "Pizza after work? I'm in.",
            "example_ru": "Пицца после работы? Я с вами.",
        },
        {
            "nl": "",
            "en": "Fair enough.",
            "analogs": ["справедливо"],
            "type": "разговорная фраза",
            "meaning": "Когда принимают чужой аргумент, даже если не спорят дальше.",
            "example": "I need more time before I decide. Fair enough.",
            "example_ru": "Мне нужно больше времени, прежде чем решить. Справедливо.",
        },
        {
            "nl": "",
            "en": "It slipped my mind.",
            "analogs": ["я совсем забыл"],
            "type": "разговорная фраза",
            "meaning": "Когда человек забыл что-то не специально.",
            "example": "I meant to call you back, but it slipped my mind.",
            "example_ru": "Я собирался тебе перезвонить, но совсем забыл.",
        },
        {
            "nl": "",
            "en": "Give me a sec.",
            "analogs": ["дай секунду"],
            "type": "разговорная фраза",
            "meaning": "Когда просят немного подождать.",
            "example": "Give me a sec, I'm just finding the address.",
            "example_ru": "Дай секунду, я как раз ищу адрес.",
        },
        {
            "nl": "",
            "en": "That was close.",
            "analogs": ["чуть не случилось"],
            "type": "разговорная фраза",
            "meaning": "Когда неприятность почти произошла, но её удалось избежать.",
            "example": "The cup almost fell off the table. That was close.",
            "example_ru": "Чашка почти упала со стола. Чуть не случилось.",
        },
        {
            "nl": "",
            "en": "I'm not feeling it.",
            "analogs": ["мне не заходит"],
            "type": "разговорная фраза",
            "meaning": "Когда что-то не нравится или не подходит по настроению.",
            "example": "Everyone likes this song, but I'm not feeling it.",
            "example_ru": "Всем нравится эта песня, но мне не заходит.",
        },
        {
            "nl": "",
            "en": "Let's call it a day.",
            "analogs": ["давай на сегодня закончим"],
            "type": "разговорная фраза",
            "meaning": "Когда предлагают закончить работу или дело на сегодня.",
            "example": "We've been fixing this for hours. Let's call it a day.",
            "example_ru": "Мы чиним это уже несколько часов. Давай на сегодня закончим.",
        },
        {
            "nl": "",
            "en": "I'm running late.",
            "analogs": ["я опаздываю"],
            "type": "разговорная фраза",
            "meaning": "Когда человек сообщает, что не успевает прийти вовремя.",
            "example": "I'm running late, but I'll be there in ten minutes.",
            "example_ru": "Я опаздываю, но буду через десять минут.",
        },
    ],
}


def _proverb_fallback(language):
    return dict(random.choice(_PROVERB_FALLBACKS[_code(language)]))


def _proverb_prompt(language):
    code = _code(language)
    target = "нидерландский" if code == "nl" else "английский"
    field = "nl" if code == "nl" else "en"
    other = "en" if code == "nl" else "nl"
    language_rule = (
        "Для Dutch выбирай выражения, которые реально звучат в Нидерландах. "
        if code == "nl"
        else "Для English выбирай живой разговорный аналог, а не буквальный перевод. "
    )
    return (
        "Ты эксперт по живой разговорной речи. "
        "Выдай одно естественное выражение для короткой карточки Telegram-бота. "
        "Это может быть идиома, фразовый глагол или частая разговорная фраза. "
        "Выражение должно реально использоваться в живой речи. "
        "Не придумывай кальки. Не используй редкие выражения. "
        "Русский перевод должен передавать смысл, а не буквальный перевод. "
        "Пример должен звучать как обычная жизненная ситуация. "
        "Пиши коротко. Без учебникового стиля. "
        f"Целевой язык карточки: {target}. Заполни поле {field}; поле {other} можно оставить пустым. "
        f"{language_rule}"
        "analogs[0] — главный русский перевод; другие варианты не нужны. "
        "meaning максимум 1 короткое предложение. "
        "example максимум 1-2 предложения. "
        "example_ru должен переводить смысл, а не слово в слово. "
        'JSON: {'
        '"nl":"NL expression or empty string",'
        '"en":"English expression or empty string",'
        '"analogs":["главный русский перевод"],'
        '"type":"идиома / разговорная фраза / фразовый глагол",'
        '"meaning":"когда так говорят, коротко по-русски",'
        '"example":"короткий пример на языке выражения",'
        '"example_ru":"естественный перевод примера на русский"'
        '}'
    )


def _split_proverb_example(value):
    if isinstance(value, list):
        value = value[0] if value else ""
    value = str(value or "").strip()
    if "→" not in value:
        return value, ""
    example, example_ru = value.split("→", 1)
    return example.strip(), example_ru.strip()


def _proverb_normalized(raw, language):
    raw = raw if isinstance(raw, dict) else {}
    code = _code(language)
    original = str(raw.get(code) or raw.get("original") or "").strip()
    analogs = raw.get("analogs") or raw.get("literal") or raw.get("ru") or []
    if isinstance(analogs, str):
        analogs = [analogs]
    analogs = [str(x).strip() for x in analogs if str(x).strip()][:1]
    example, parsed_example_ru = _split_proverb_example(raw.get("example") or raw.get("examples") or "")
    return {
        "original": _cap(original),
        "analogs": analogs,
        "meaning": str(raw.get("meaning") or "").strip(),
        "example": example,
        "example_ru": str(raw.get("example_ru") or parsed_example_ru or "").strip(),
    }


def _plain_for_match(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(text or "").lower(), flags=re.UNICODE)).strip()


def _example_mentions_original(original, example):
    original_plain = _plain_for_match(original)
    example_plain = _plain_for_match(example)
    if not original_plain or not example_plain:
        return False
    if original_plain in example_plain:
        return True
    tokens = [token for token in original_plain.split() if len(token) > 2]
    return bool(tokens) and all(token in example_plain.split() for token in tokens)


def _valid_proverb(data):
    return (
        bool(data.get("original"))
        and bool(data.get("analogs"))
        and bool(data.get("example"))
        and _example_mentions_original(data.get("original"), data.get("example"))
        and len(data.get("meaning") or "") <= 160
        and len(data.get("example") or "") <= 240
        and len(data.get("example_ru") or "") <= 240
    )


async def _generate_proverb(language):
    try:
        raw = await ai.allm_json(_proverb_prompt(language), 500, tier="cheap", route="gemini", module="learning")
    except Exception:
        raw = _proverb_fallback(language)
    data = _proverb_normalized(raw, language)
    if not _valid_proverb(data):
        data = _proverb_normalized(_proverb_fallback(language), language)
    return data


async def send_proverb(bot, cid, language=None, with_kb=True):
    language = language or active_language(cid)
    data = await _generate_proverb(language)
    txt, entities = _proverb_entities_card(
        _flag(language),
        data["original"],
        data["analogs"],
        _cap(data["meaning"]),
        data["example"],
        data["example_ru"],
    )
    reply_markup = _proverb_kb(_code(language)) if with_kb else None
    await bot.send_message(chat_id=cid, text=txt, entities=entities, reply_markup=reply_markup)


async def send_proverb_both(bot, cid, with_kb=True, language=None):
    """Compatibility wrapper: live language uses the single active learning language."""
    await send_proverb(bot, cid, language or active_language(cid), with_kb=with_kb)


# ================= СЛОВАРЬ (раздельно NL / EN) =================
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

_NL_IK_INFINITIVE_FIXES = {
    "begrijpen": "begrijp",
    "beginnen": "begin",
    "behalen": "behaal",
    "beïnvloeden": "beïnvloed",
    "bekijken": "bekijk",
    "benadrukken": "benadruk",
    "beoordelen": "beoordeel",
    "beperken": "beperk",
    "bereiken": "bereik",
    "beschouwen": "beschouw",
    "beschrijven": "beschrijf",
    "beslissen": "beslis",
    "bespreken": "bespreek",
    "betalen": "betaal",
    "betekenen": "beteken",
    "bevorderen": "bevorder",
    "bewijzen": "bewijs",
    "blijven": "blijf",
    "denken": "denk",
    "doen": "doe",
    "eisen": "eis",
    "gaan": "ga",
    "gebruiken": "gebruik",
    "geven": "geef",
    "halen": "haal",
    "handhaven": "handhaaf",
    "hebben": "heb",
    "helpen": "help",
    "herhalen": "herhaal",
    "herkennen": "herken",
    "hoeven": "hoef",
    "houden": "houd",
    "kiezen": "kies",
    "kijken": "kijk",
    "kloppen": "klop",
    "komen": "kom",
    "kopen": "koop",
    "kunnen": "kan",
    "leren": "leer",
    "lezen": "lees",
    "liggen": "lig",
    "lopen": "loop",
    "luisteren": "luister",
    "maken": "maak",
    "mogen": "mag",
    "moeten": "moet",
    "nemen": "neem",
    "onderbouwen": "onderbouw",
    "onderzoeken": "onderzoek",
    "onderscheiden": "onderscheid",
    "ontmoeten": "ontmoet",
    "ontwikkelen": "ontwikkel",
    "overtuigen": "overtuig",
    "overwegen": "overweeg",
    "praten": "praat",
    "proberen": "probeer",
    "reageren": "reageer",
    "rechtvaardigen": "rechtvaardig",
    "reizen": "reis",
    "schatten": "schat",
    "slapen": "slaap",
    "spreken": "spreek",
    "staan": "sta",
    "streven": "streef",
    "veranderen": "verander",
    "verbeteren": "verbeter",
    "vergeten": "vergeet",
    "vermijden": "vermijd",
    "veronderstellen": "veronderstel",
    "voorkomen": "voorkom",
    "vragen": "vraag",
    "wachten": "wacht",
    "werken": "werk",
    "weten": "weet",
    "willen": "wil",
    "zeggen": "zeg",
    "zien": "zie",
    "zijn": "ben",
    "zitten": "zit",
    "zoeken": "zoek",
    "zullen": "zal",
}

_NL_IK_INFINITIVE_RE = re.compile(r"^(\s*ik\s+)([A-Za-zÀ-ÖØ-öø-ÿ]+)(\b.*)$", re.I)

def _normalize_dutch_phrase(term):
    """Correct the high-confidence learner error "Ik + infinitive"."""
    phrase = re.sub(r"\s+", " ", (term or "").strip())
    m = _NL_IK_INFINITIVE_RE.match(phrase)
    if not m:
        return phrase, ""
    fixed_verb = _NL_IK_INFINITIVE_FIXES.get(m.group(2).casefold())
    if not fixed_verb:
        return phrase, ""
    fixed = f"{m.group(1)}{fixed_verb}{m.group(3)}"
    return _cap(fixed.strip()), "После ik нужен личный глагол, а не инфинитив."

def _normalize_dict_term(lang, kind, term):
    term = re.sub(r"\s+", " ", (term or "").strip())
    if lang == "nl" and kind == "phrase":
        return _normalize_dutch_phrase(term)
    return term, ""

_DICT_ADD_VERB_RE = re.compile(
    r"\b(добавь|добавить|занеси|запиши|сохрани|сохранить|запомни|запомнить|внеси|закинь|"
    r"add|save|remember)\b", re.I)
_DICT_WORD_RE = re.compile(r"\b(?:в\s+)?(?:мой\s+)?(?:словар[ьяьею]*|обучени[еяю]|тренировк[ауиах]*)\b", re.I)
_DICT_LEADING_RE = re.compile(r"^\s*в\s+(?:мой\s+)?словар[ьяьею]*\b", re.I)
_DICT_LANG_RE = re.compile(
    r"\b(?:на\s+)?("
    r"нидерландск(?:ом|ое|ого|ий|ую|ая|ие|их)|голландск(?:ом|ое|ого|ий|ую|ая|ие|их)|dutch|nl|"
    r"английск(?:ом|ое|ого|ий|ую|ая|ие|их)|english|en"
    r")\b",
    re.I,
)
_DICT_KIND_RE = re.compile(r"\b(слово|слова|фразу|фраза|выражение|выражения|термин)\b", re.I)
_DICT_QUESTION_PAYLOAD_RE = re.compile(r"^(?:како(?:е|й|ую)|что|что-то)\b", re.I)
_DICT_PAYLOAD_PREFIX_RE = re.compile(
    r"^(?:(?:ну|пожалуйста|плиз|нужно|надо|можешь|можно|мне|нам|хочу|давай|нов(?:ое|ый|ую|ая|ые)|эту|это|его|её|ее)\s+)+",
    re.I,
)
_DICT_EMPTY_PAYLOAD = {"", "в", "на", "для", "туда", "это", "эту", "его", "её", "ее"}

def _dict_lang_hint_explicit(text):
    """Язык, явно названный в самой команде («на английском», «dutch» и т.п.).
    None, если язык явно не назван — тогда решение принимает вызывающий код
    по активному языку обучения, признакам de/het или сам LLM."""
    t = (text or "").lower()
    if any(x in t for x in ("английск", "english", " en ")):
        return "en"
    if any(x in t for x in ("нидерланд", "голланд", "dutch", " nl ")):
        return "nl"
    return None


_DUTCH_ARTICLE_RE = re.compile(r"\b(de|het)\s+\w+", re.I)


def _dict_lang_hint(text, cid=None):
    """Порядок определения языка (без безусловного fallback на nl):
    1. Язык, явно указанный в самой команде.
    2. Признаки de/het (нидерландский артикль) в тексте — прямое доказательство
       в самих словах, сильнее предположения по активному языку обучения.
    3. Активный язык обучения пользователя.
    4. Иначе — не подсказываем язык явно, финальное решение остаётся за LLM
       (промпт _normalize_dict_entry_full сам определяет lang по слову)."""
    explicit = _dict_lang_hint_explicit(text)
    if explicit:
        return explicit
    if _DUTCH_ARTICLE_RE.search(text or ""):
        return "nl"
    if cid is not None:
        try:
            return _active_language_code(cid)
        except Exception:
            pass
    return None


def _clean_chat_dict_payload(text):
    payload = _DICT_ADD_VERB_RE.sub(" ", text or "", count=1)
    payload = _DICT_WORD_RE.sub(" ", payload)
    payload = _DICT_KIND_RE.sub(" ", payload)
    payload = _DICT_LANG_RE.sub(" ", payload)
    payload = re.sub(r"\b(?:эту|это|его|её|ее)\b", " ", payload, flags=re.I)
    payload = re.sub(r"\s+", " ", payload).strip(" \t\n\r:;,.-–—")
    payload = _DICT_PAYLOAD_PREFIX_RE.sub("", payload).strip(" \t\n\r:;,.-–—")
    return payload


def _extract_chat_dict_add(text, cid=None):
    """Команда из свободного чата: «добавь в словарь слово ...» -> полезная часть."""
    text = text or ""
    if _DICT_LEADING_RE.search(text):
        lang = _dict_lang_hint(f" {text} ", cid)
        payload = _clean_chat_dict_payload(_DICT_LEADING_RE.sub(" ", text, count=1))
        if payload.casefold() in _DICT_EMPTY_PAYLOAD:
            return "", lang
        return payload, lang
    has_add_verb = bool(_DICT_ADD_VERB_RE.search(text))
    has_dict_word = bool(_DICT_WORD_RE.search(text))
    has_kind_word = bool(_DICT_KIND_RE.search(text))
    if not has_add_verb:
        return None, None
    lang = _dict_lang_hint(f" {text} ", cid)
    payload = _clean_chat_dict_payload(text)
    has_foreign_payload = bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", payload)) and not _CYRILLIC_RE.search(payload)
    if not (has_dict_word or has_kind_word or has_foreign_payload):
        return None, None
    if _DICT_QUESTION_PAYLOAD_RE.search(payload):
        return None, None
    if payload.casefold() in _DICT_EMPTY_PAYLOAD:
        return "", lang
    return payload, lang

async def try_add_dict_from_chat(bot, cid, text):
    """Перехватывает явную просьбу добавить слово/фразу в словарь из обычного чата.
    Явная команда («добавь в словарь ...») — чёткое намерение добавить именно эту
    фразу целиком, даже если она длинная или заканчивается на «?»/«!» — поэтому
    здесь НЕ проверяем payload на «похоже на связный текст» (в отличие от
    add_words_batch, куда текст мог попасть без явной команды на конкретную фразу).
    _normalize_dict_entry_full сам исправит опечатки и приведёт фразу к
    естественной форме, а единый confirm-экран покажет итог на подтверждение."""
    payload, lang = _extract_chat_dict_add(text, cid)
    if payload is None:
        return False
    if not payload:
        await bot.send_message(
            chat_id=cid,
            text="Пришли само слово или фразу: например «добавь в словарь de kater».",
        )
        return True
    await add_dict_entry_from_chat(bot, cid, payload, lang, source_text=text)
    return True


def _lang_title(lang):
    return "нидерландский" if lang == "nl" else "английский"


def _lang_loc_title(lang):
    return "нидерландском" if lang == "nl" else "английском"


def _add_term_run(b, term):
    """Термин жирным курсивом (правило проекта: термин выделен, перевод — через жирную стрелку)."""
    from ui.builder import u16_len
    offset = u16_len(b.text)
    b.add(term)
    length = u16_len(term)
    b._entities.append(MessageEntity(MessageEntity.BOLD, offset, length))
    b._entities.append(MessageEntity(MessageEntity.ITALIC, offset, length))


def _dict_entry_message(entry, status="added"):
    """Карточка после добавления/обновления/поиска: термин жирным курсивом с большой буквы,
    перевод одной строкой через жирную стрелку "→", разбор, примеры."""
    from ui.builder import MessageBuilder

    b = MessageBuilder()
    term = entry.get("term") or ""
    if entry.get("article") and not term.lower().startswith(entry["article"].lower() + " "):
        term = f"{entry['article']} {term}"
    term = _cap(term)

    if status == "duplicate":
        b.text_line("📚 ")
        b.bold(f"Уже есть в {_lang_loc_title(entry.get('lang'))} словаре")
        b.newline()
        b.spacer()
        _add_term_run(b, term)
        if entry.get("translation"):
            b.text_line(" ")
            b.bold("→")
            b.text_line(f" {entry['translation']}")
        b.newline()
        return b.build_stripped()

    titles = {"updated": "Обновлено", "found": "Найдено"}
    emoji = "✅" if status in ("added", "updated") else "📚"
    b.text_line(f"{emoji} ")
    b.bold(titles.get(status, "Добавлено"))
    b.text_line(": ")
    _add_term_run(b, term)
    b.newline()
    b.spacer()
    if entry.get("translation"):
        b.bold("→")
        b.text_line(f" {entry['translation']}")
        b.newline()
    if entry.get("breakdown"):
        b.line(f"Разбор: {entry['breakdown']}")
    examples = entry.get("examples") or []
    if examples:
        b.spacer()
        b.line("Пример:" if len(examples) == 1 else "Примеры:")
        for ex in examples:
            b.line(f"{ex.get('text', '')} — {ex.get('translation', '')}")
    return b.build_stripped()


def _dict_loose_key(lang, entry_type, word):
    base = re.sub(r"\s+", " ", (word or "").strip()).casefold()
    if lang == "nl":
        base = re.sub(r"^(de|het|een)\s+", "", base)
    if lang == "en":
        base = re.sub(r"^(to|the|a|an)\s+", "", base)
    return lang, entry_type or "word", base


def _dict_loose_text(lang, word):
    return _dict_loose_key(lang, "word", word)[2]


async def _normalize_dict_entry_full(payload, lang_hint=None, source_text="", avoid_translations=None):
    """Единая точка добавления: нормализация + перевод + короткий разбор + 1-2 примера.
    Один AI-вызов на запись, кэшируется в ai.py по input_hash (module="learning_dict_add",
    TTL 30 дней) — повторное добавление того же слова не тратит лимит повторно.
    lang_hint — nl/en/None. None означает, что язык не определён ни явной командой,
    ни активным языком обучения, ни признаками de/het — LLM определяет его сам,
    без принудительного fallback на nl.
    avoid_translations — уже показанные пользователю варианты (кнопка «Другой перевод»);
    меняет текст промпта, чтобы не попасть в тот же кэш и получить другой вариант."""
    if lang_hint in ("nl", "en"):
        language_line = f"Подсказка языка: {_lang_title(lang_hint)} ({lang_hint})."
    else:
        language_line = "Язык не подсказан — определи его сам по слову/фразе."
    avoid_line = ""
    if avoid_translations:
        avoid_line = (
            "\nПользователь уже видел эти варианты перевода и просит другой — "
            "НЕ повторяй их, предложи следующее по точности значение: "
            + "; ".join(avoid_translations) + "."
        )
    prompt = f"""
Ты лексикограф для учебного словаря Telegram-бота. Всё учится как фраза: короткая
запись (одно слово) и длинная (выражение/предложение) хранятся одинаково.

Пользователь хочет добавить: {secure.wrap_untrusted(payload, 'запись')}
Полное сообщение пользователя: {secure.wrap_untrusted(source_text or payload, 'сообщение')}
{language_line}{avoid_line}

Определи и нормализуй РОВНО ОДНУ учебную запись.

Правила:
- lang: nl или en.
- term: правильная учебная форма (без перевода).
  - Нидерландские существительные — с артиклем de/het.
  - Глаголы — в инфинитиве; английские глаголы словарной формой — с to.
  - Прилагательные — в базовой форме.
  - Устойчивые выражения — целиком в базовой форме.
  - Фразы/предложения — естественно и грамматически правильно, без изменения смысла.
  - Для нидерландских фраз проверяй согласование подлежащего и сказуемого:
    "Ik bereiken mijn doel" нельзя; правильно "Ik bereik mijn doel".
  - Если во фразе явная опечатка (например лишняя/пропущенная буква, не меняющая
    смысл: "wat doc je daar" → "wat doe je daar"), исправь её молча — term должен
    быть уже исправленной, естественной формой, а не сырым вводом с ошибкой.
- article: артикль "de"/"het" ТОЛЬКО для нидерландских существительных. У глаголов, прилагательных,
  фраз и предложений артикля нет и не может быть — всегда пусто.
- translation: 1-2 самых точных значения на русском, через "; ".
- breakdown: короткий разбор — часть речи, род/артикль, особенность формы (одна строка,
  без пояснений сверх необходимого).
- examples: 1-2 примера предложений на изучаемом языке с переводом на русский, естественных
  и коротких.
- Не выдумывай значение. Если слово многозначное, редкое, написано с ошибкой, не хватает
  артикля для нидерландского существительного или есть риск неверного перевода, поставь
  needs_confirmation=true и дай наиболее вероятную трактовку.

Верни JSON:
{{
  "ok": true,
  "lang": "nl|en",
  "term": "правильная учебная форма",
  "article": "de|het|",
  "translation": "перевод",
  "breakdown": "короткий разбор",
  "examples": [{{"text": "...", "translation": "..."}}],
  "needs_confirmation": false,
  "reason": "короткая причина уточнения или пусто"
}}
Если это не похоже на нидерландскую или английскую учебную запись, верни {{"ok": false, "reason": "коротко почему"}}.
"""
    d = await ai.allm_json(prompt, 900, module="learning_dict_add")
    if not isinstance(d, dict) or not d.get("ok"):
        return None
    lang = "en" if d.get("lang") == "en" else "nl"
    term = re.sub(r"\s+", " ", str(d.get("term") or "").strip())
    translation = re.sub(r"\s+", " ", str(d.get("translation") or "").strip())
    term, _grammar_note = _normalize_dict_term(lang, _kind_of(term), term)
    if not term or not translation or _is_bad_dict_item(term, translation):
        return None
    examples = []
    for ex in (d.get("examples") or [])[:2]:
        if not isinstance(ex, dict):
            continue
        text = re.sub(r"\s+", " ", str(ex.get("text") or "").strip())
        ex_ru = re.sub(r"\s+", " ", str(ex.get("translation") or "").strip())
        if text and ex_ru:
            examples.append({"text": text[:200], "translation": ex_ru[:200]})
    breakdown = re.sub(r"\s+", " ", str(d.get("breakdown") or "").strip())[:180]
    article = str(d.get("article") or "").strip() if lang == "nl" else ""
    if article and "глагол" in breakdown.lower():
        # У глаголов нет артикля de/het — модель иногда всё равно его возвращает.
        article = ""
    return {
        "lang": lang,
        "term": term[:120],
        "article": article,
        "translation": translation[:180],
        "breakdown": breakdown,
        "examples": examples,
        "source_text": source_text or payload,
        "added_at": datetime.now(config.TZ).isoformat(),
        "status": "new",
        "last_shown_at": None,
        "needs_confirmation": bool(d.get("needs_confirmation")),
        "reason": str(d.get("reason") or "").strip(),
    }


def _save_normalized_dict_entry(cid, entry):
    """Сохраняет запись единого словаря (структура из спеки: term/article/translation/
    breakdown/examples/status). Возвращает (status, saved_entry) где status —
    added/updated/duplicate."""
    entry = dict(entry)
    words = store.get_list(config.DICT_KEY, cid)
    loose_text = _dict_loose_text(entry["lang"], entry["term"])
    for idx, item in enumerate(words):
        existing_term = _entry_term(item)
        if _dict_lang(item) != entry["lang"]:
            continue
        if existing_term.casefold() == entry["term"].casefold():
            duplicate = dict(item)
            return "duplicate", duplicate
        if _dict_loose_text(entry["lang"], existing_term) == loose_text:
            updated = dict(item)
            updated.update({
                "lang": entry["lang"],
                "term": entry["term"],
                "article": entry.get("article", ""),
                "translation": entry["translation"],
                "breakdown": entry.get("breakdown", ""),
                "examples": entry.get("examples", []),
                "source_text": entry.get("source_text", ""),
                "added_at": item.get("added_at") or entry["added_at"],
                "status": item.get("status") or "new",
                "last_shown_at": item.get("last_shown_at"),
                "updated_at": datetime.now(config.TZ).isoformat(),
            })
            words[idx] = updated
            store.set_list(config.DICT_KEY, cid, words)
            return "updated", updated
    saved = {
        "lang": entry["lang"],
        "term": entry["term"],
        "article": entry.get("article", ""),
        "translation": entry["translation"],
        "breakdown": entry.get("breakdown", ""),
        "examples": entry.get("examples", []),
        "source_text": entry.get("source_text", ""),
        "added_at": entry["added_at"],
        "status": entry.get("status") or "new",
        "last_shown_at": entry.get("last_shown_at"),
    }
    store.add_to_list(config.DICT_KEY, cid, saved)
    return "added", saved


def _entry_term(item):
    """Термин записи с фолбэком на legacy-поля (word/base_form) для старых записей."""
    if not isinstance(item, dict):
        return str(item)
    return item.get("term") or item.get("word") or item.get("base_form") or ""


def _entry_translation(item):
    if not isinstance(item, dict):
        return ""
    return item.get("translation") or item.get("ru") or ""


def _entry_needs_ai_refresh(item):
    """Старая запись без разбора/примеров — донасытим при первом обращении (ленивая миграция)."""
    if not isinstance(item, dict):
        return False
    return not item.get("breakdown") or not item.get("examples")


async def _refresh_dict_entry(cid, item):
    """Ленивая миграция одной старой записи в новый формат при первом обращении.
    Обновляет запись на месте по индексу — не через _save_normalized_dict_entry,
    т.к. та считает совпадение термина дубликатом и не заменит поля."""
    term = _entry_term(item)
    lang = _dict_lang(item)
    try:
        entry = await _normalize_dict_entry_full(term, lang, source_text=term)
    except Exception:
        return item
    if not entry or entry.get("needs_confirmation"):
        return item
    words = store.get_list(config.DICT_KEY, cid)
    for idx, w in enumerate(words):
        if w is item or (_dict_lang(w) == lang and _entry_term(w) == term):
            updated = dict(w)
            updated.update({
                "lang": entry["lang"],
                "term": entry["term"],
                "article": entry.get("article", ""),
                "translation": entry["translation"],
                "breakdown": entry.get("breakdown", ""),
                "examples": entry.get("examples", []),
                "status": w.get("status") or "new",
                "last_shown_at": w.get("last_shown_at"),
                "updated_at": datetime.now(config.TZ).isoformat(),
            })
            words[idx] = updated
            store.set_list(config.DICT_KEY, cid, words)
            return updated
    return item


def _dict_saved_kb(lang, term_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Другой перевод", callback_data="a_dictconfirm_retry")],
        [InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictdelok_{lang}_{term_key}")],
    ])


def _overwrite_dict_entry_fields(cid, lang, term, fields):
    """Обновляет уже сохранённую запись на месте по точному совпадению term
    (используется "Другим переводом" после мгновенного сохранения)."""
    words = store.get_list(config.DICT_KEY, cid)
    for idx, item in enumerate(words):
        if _dict_lang(item) == lang and _entry_term(item).casefold() == term.casefold():
            updated = dict(item)
            updated.update(fields)
            updated["updated_at"] = datetime.now(config.TZ).isoformat()
            words[idx] = updated
            store.set_list(config.DICT_KEY, cid, words)
            return updated
    return None


async def add_dict_entry_from_chat(bot, cid, payload, lang=None, source_text=""):
    """Сохраняет запись в словарь сразу, без ожидания кнопки "Добавить" - если разбор
    ошибся, запись можно удалить одной кнопкой, а не потерять, забыв подтвердить."""
    try:
        entry = await _normalize_dict_entry_full(payload, lang, source_text=source_text)
    except Exception:
        await bot.send_message(chat_id=cid, text="⚠️ Не получилось разобрать слово. Попробуй ещё раз.")
        return
    if not entry:
        await bot.send_message(
            chat_id=cid,
            text="Не уверена в форме или переводе. Пришли так: de kater → похмелье.",
        )
        return
    status, saved = _save_normalized_dict_entry(cid, entry)
    saved["_payload"] = payload
    saved["_source_text"] = source_text
    saved["_seen_translations"] = [entry["translation"]]
    store.dict_pending_add[str(cid)] = saved
    msg = _dict_entry_message(saved, status=status)
    term_key = _dict_item_key(saved["lang"], "", _entry_term(saved))[2]
    if status == "duplicate":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictdelok_{saved['lang']}_{term_key}"),
             InlineKeyboardButton("✅ Оставить", callback_data="noop")],
        ])
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return
    kb = _dict_saved_kb(saved["lang"], term_key)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def retry_pending_dict_add(bot, cid):
    """Кнопка «Другой перевод»: перегенерирует перевод, исключая уже показанные
    варианты, и обновляет уже сохранённую запись на месте (слово уже в словаре)."""
    entry = store.dict_pending_add.get(str(cid))
    if not entry:
        await bot.send_message(chat_id=cid, text="Уточнение устарело. Пришли слово ещё раз.")
        return
    seen = entry.get("_seen_translations") or [entry.get("translation", "")]
    try:
        new_entry = await _normalize_dict_entry_full(
            entry.get("_payload", entry.get("term", "")), entry.get("lang", "nl"),
            source_text=entry.get("_source_text", ""), avoid_translations=seen,
        )
    except Exception:
        await bot.send_message(chat_id=cid, text="⚠️ Не получилось получить другой вариант. Попробуй ещё раз.")
        return
    if not new_entry or new_entry["translation"] in seen:
        await bot.send_message(chat_id=cid, text="Больше вариантов перевода не нашлось.")
        return
    updated = _overwrite_dict_entry_fields(cid, entry["lang"], entry["term"], {
        "translation": new_entry["translation"],
        "breakdown": new_entry.get("breakdown", ""),
        "examples": new_entry.get("examples", []),
    }) or new_entry
    updated["_payload"] = entry.get("_payload", "")
    updated["_source_text"] = entry.get("_source_text", "")
    updated["_seen_translations"] = seen + [new_entry["translation"]]
    store.dict_pending_add[str(cid)] = updated
    msg = _dict_entry_message(updated, status="updated")
    term_key = _dict_item_key(updated["lang"], "", _entry_term(updated))[2]
    kb = _dict_saved_kb(updated["lang"], term_key)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def cancel_pending_dict_add(bot, cid):
    store.dict_pending_add.pop(str(cid), None)
    await bot.send_message(chat_id=cid, text="Отменено.")


async def confirm_pending_dict_add(bot, cid):
    entry = store.dict_pending_add.pop(str(cid), None)
    if not entry:
        await bot.send_message(chat_id=cid, text="Уточнение устарело. Пришли слово ещё раз.")
        return
    status, saved = _save_normalized_dict_entry(cid, entry)
    msg = _dict_entry_message(saved, status=status)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)


def _dict_item_key(lang, kind, word):
    normalized = re.sub(r"\s+", " ", (word or "").strip()).casefold()
    return lang, kind, normalized

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

_BATCH_CARD_LIMIT = 5  # больше строк — не спамим карточками, шлём короткую сводку
_DICT_TOPIC_LIMIT = 5  # сколько кандидатов максимум предлагать из свободного текста

_SENTENCE_LINE_RE = re.compile(r"[.!?…]\s*$")


def _looks_like_free_text(lines):
    """True, если ввод похож на связный текст (предложения), а не на список
    отдельных слов/фраз — тогда нельзя добавлять построчно без разбора темы."""
    if len(lines) == 1:
        words = lines[0].split()
        return len(words) > 6 or bool(_SENTENCE_LINE_RE.search(lines[0]))
    sentence_like = sum(
        1 for ln in lines
        if len(ln.split()) > 6 or _SENTENCE_LINE_RE.search(ln)
    )
    return sentence_like >= max(2, len(lines) // 2)


async def _extract_dict_topics(text, lang="nl"):
    """LLM выбирает до _DICT_TOPIC_LIMIT ключевых слов/фраз из свободного текста
    вместо добавления всего подряд построчно — см. правило превью+подтверждение."""
    language_hint = _lang_title(lang)
    prompt = f"""
Пользователь прислал текст в Telegram-бот с изучением языков. Подсказка языка
изучения: {language_hint} ({lang}).
Текст: {secure.wrap_untrusted(text, 'текст')}

Найди основную тему текста и выбери не больше {_DICT_TOPIC_LIMIT} самых полезных
для учебного словаря слов или коротких фраз на языке {language_hint}, которые
встречаются в тексте по смыслу (переведи на {language_hint}, если текст на русском).
Не включай случайные малополезные слова — только те, что реально стоит выучить.
Если в тексте нет ничего подходящего для словаря, верни пустой список.

Верни JSON:
{{"items": [{{"term": "...", "translation": "..."}}]}}
"""
    try:
        d = await ai.allm_json(prompt, 500, module="learning")
    except Exception:
        d = {}
    items = (d or {}).get("items") or []
    out = []
    for item in items[:_DICT_TOPIC_LIMIT]:
        if not isinstance(item, dict):
            continue
        term = re.sub(r"\s+", " ", str(item.get("term") or "").strip())
        translation = re.sub(r"\s+", " ", str(item.get("translation") or "").strip())
        if term and translation:
            out.append({"term": term, "translation": translation})
    return out


def _dict_batch_preview_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Добавить всё", callback_data="a_dictbatch_add"),
        InlineKeyboardButton("❌ Не добавлять", callback_data="a_dictbatch_cancel"),
    ]])


async def offer_dict_topics_from_text(bot, cid, text, lang="nl"):
    """Свободный текст (несколько предложений) — не добавляем слепо: LLM находит
    тему, показываем превью до 5 кандидатов и добавляем только по подтверждению."""
    topics = await _extract_dict_topics(text, lang)
    if not topics:
        await bot.send_message(
            chat_id=cid,
            text="Не нашла в тексте ничего подходящего для словаря.",
        )
        return
    store.dict_pending_batch[str(cid)] = {"lang": lang, "items": topics, "source_text": text}
    lines = "\n".join(f"• {it['term']} — {it['translation']}" for it in topics)
    await bot.send_message(
        chat_id=cid,
        text=f"📚 Добавить в словарь?\n\n{lines}",
        reply_markup=_dict_batch_preview_kb(),
    )


async def confirm_dict_batch(bot, cid):
    pending = store.dict_pending_batch.pop(str(cid), None)
    if not pending:
        await bot.send_message(chat_id=cid, text="Подборка устарела. Пришли текст ещё раз.")
        return
    lang = pending.get("lang", "nl")
    text = "\n".join(it["term"] for it in pending.get("items") or [])
    await add_words_batch(bot, cid, text, lang, detailed_confirmation=True)


async def cancel_dict_batch(bot, cid):
    store.dict_pending_batch.pop(str(cid), None)
    await bot.send_message(chat_id=cid, text="Хорошо, не добавляю.")


async def _offer_manual_batch_preview(bot, cid, lines, lang):
    """Явный список слов/фраз пользователя (2+ строки, каждая — отдельная запись):
    показываем превью как есть и просим общее подтверждение перед AI-разбором и
    сохранением — единый стиль добавления, без исключений для «очевидных» слов."""
    store.dict_pending_batch[str(cid)] = {"lang": lang, "items": [{"term": ln} for ln in lines], "source_text": "\n".join(lines)}
    preview = "\n".join(f"• {ln}" for ln in lines)
    await bot.send_message(
        chat_id=cid,
        text=f"📚 Добавить в словарь?\n\n{preview}",
        reply_markup=_dict_batch_preview_kb(),
    )


async def add_words_batch(bot, cid, text, lang="nl", detailed_confirmation=False):
    """Добавляет одну или несколько записей: каждая строка проходит полный AI-разбор
    (нормализация + перевод + разбор + пример), см. _normalize_dict_entry_full.
    При <= 5 строках — карточка на каждую запись; иначе короткая сводка.

    Единый стиль подтверждения: одиночное слово — карточка «Ты имеешь в виду X — Y?»
    (см. add_dict_entry_from_chat), несколько строк — превью списка с общим
    подтверждением (см. _offer_manual_batch_preview). detailed_confirmation=True —
    это уже подтверждённый список, идём сразу к AI-разбору и сохранению."""
    lines = [x.strip() for x in re.split(r"[\n;]+", text or "") if x.strip()]
    if not lines:
        await bot.send_message(chat_id=cid, text="Не удалось распознать слова. Попробуй ещё раз.")
        return
    if not detailed_confirmation and _looks_like_free_text(lines):
        await offer_dict_topics_from_text(bot, cid, text, lang)
        return
    if not detailed_confirmation and len(lines) == 1:
        await add_dict_entry_from_chat(bot, cid, lines[0], lang, source_text=lines[0])
        return
    if not detailed_confirmation and len(lines) > 1:
        await _offer_manual_batch_preview(bot, cid, lines, lang)
        return

    added_entries = []
    duplicate_entries = []
    unrecognized_lines = []
    for line in lines:
        try:
            entry = await _normalize_dict_entry_full(line, lang, source_text=line)
        except Exception:
            entry = None
        if not entry:
            unrecognized_lines.append(line[:60])
            continue
        status, saved = _save_normalized_dict_entry(cid, entry)
        if status == "duplicate":
            duplicate_entries.append(saved)
        else:
            added_entries.append(saved)

    if not added_entries:
        if duplicate_entries:
            await bot.send_message(chat_id=cid, text="Эти слова или фразы уже есть в словаре."); return
        if unrecognized_lines:
            await bot.send_message(chat_id=cid,
                text="Не уверена в форме или переводе: " + ", ".join(unrecognized_lines[:10]) +
                     ". Пришли так: de kater → похмелье.")
            return
        await bot.send_message(chat_id=cid, text="Не удалось распознать слова. Попробуй ещё раз."); return

    if len(added_entries) <= _BATCH_CARD_LIMIT:
        for saved in added_entries:
            msg = _dict_entry_message(saved, status="added")
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
    else:
        terms = ", ".join(e.get("term", "") for e in added_entries[:10])
        more = f" и ещё {len(added_entries) - 10}" if len(added_entries) > 10 else ""
        await bot.send_message(chat_id=cid,
            text=f"📚 Добавлено {len(added_entries)}: {terms}{more}")
    if unrecognized_lines:
        await bot.send_message(chat_id=cid,
            text="⚠️ Не удалось распознать: " + ", ".join(unrecognized_lines[:10]))
    await send_dict_lang(bot, cid, lang)


async def add_smart_batch(bot, cid, text, lang="nl"):
    """Алиас для единого пути добавления (сохранён для совместимости вызовов)."""
    await add_words_batch(bot, cid, text, lang, detailed_confirmation=False)


def _w_field(w, *keys):
    for k in keys:
        if isinstance(w, dict) and w.get(k):
            return w[k]
    return ""

def _ensure_dict(cid):
    """Возвращает словарь пользователя (без авто-сида)."""
    return store.get_list(config.DICT_KEY, cid)


_DICT_SEED_PROFILE_KEY = "_dict_seed"
_DICT_SEED_SEEN_PROFILE_KEY = "_dict_seed_seen"
_DICT_SEED_PAGE_SIZE = 5
_DICT_SEED_LIMIT = 30
_SEED_LEVELS = ["simple", "medium", "hard"]
_DICT_SEED_SOURCE_NOTE = (
    "Списки собраны как частотный старт: Oxford 3000/5000, Cambridge/English "
    "Vocabulary Profile и частотные разговорные списки; редкие книжные слова исключены."
)

_EN_SEED_WORDS = {
    "simple": [
        ("about", "о, про", ""), ("always", "всегда", ""), ("because", "потому что", ""),
        ("before", "до, перед", ""), ("between", "между", ""), ("bring", "приносить", ""),
        ("city", "город", ""), ("clean", "чистый; убирать", ""), ("different", "разный", ""),
        ("enough", "достаточно", ""), ("family", "семья", ""), ("friend", "друг", ""),
        ("important", "важный", ""), ("learn", "учить", ""), ("listen", "слушать", ""),
        ("maybe", "может быть", ""), ("morning", "утро", ""), ("often", "часто", ""),
        ("place", "место", ""), ("question", "вопрос", ""), ("remember", "помнить", ""),
        ("something", "что-то", ""), ("sometimes", "иногда", ""), ("together", "вместе", ""),
        ("understand", "понимать", ""), ("usually", "обычно", ""), ("want", "хотеть", ""),
        ("water", "вода", ""), ("week", "неделя", ""), ("work", "работать; работа", ""),
        ("almost", "почти", ""), ("already", "уже", ""), ("arrive", "прибывать", ""),
        ("believe", "верить", ""), ("borrow", "занимать", ""), ("change", "менять; изменение", ""),
        ("comfortable", "удобный", ""), ("continue", "продолжать", ""), ("decide", "решать", ""),
        ("during", "во время", ""), ("explain", "объяснять", ""), ("finally", "наконец", ""),
        ("follow", "следовать", ""), ("happen", "случаться", ""), ("include", "включать", ""),
        ("instead", "вместо этого", ""), ("invite", "приглашать", ""), ("journey", "поездка", ""),
        ("later", "позже", ""), ("necessary", "необходимый", ""), ("opinion", "мнение", ""),
        ("perhaps", "возможно", ""), ("prepare", "готовить; подготавливать", ""), ("quite", "довольно", ""),
        ("receive", "получать", ""), ("reason", "причина", ""), ("return", "возвращаться", ""),
        ("several", "несколько", ""), ("spend", "тратить; проводить время", ""), ("without", "без", ""),
    ],
    "medium": [
        ("achieve", "достигать", ""), ("although", "хотя", ""), ("avoid", "избегать", ""),
        ("challenge", "вызов; трудная задача", ""), ("compare", "сравнивать", ""), ("consider", "считать; рассматривать", ""),
        ("create", "создавать", ""), ("depend", "зависеть", ""), ("develop", "развивать", ""),
        ("effort", "усилие", ""), ("especially", "особенно", ""), ("experience", "опыт; переживать", ""),
        ("focus", "фокусироваться", ""), ("improve", "улучшать", ""), ("increase", "увеличивать", ""),
        ("involve", "включать; вовлекать", ""), ("knowledge", "знание", ""), ("likely", "вероятный", ""),
        ("manage", "справляться; управлять", ""), ("notice", "замечать", ""), ("opportunity", "возможность", ""),
        ("provide", "предоставлять", ""), ("purpose", "цель", ""), ("reduce", "снижать", ""),
        ("require", "требовать", ""), ("result", "результат", ""), ("similar", "похожий", ""),
        ("support", "поддерживать; поддержка", ""), ("therefore", "поэтому", ""), ("whether", "ли", ""),
    ],
    "hard": [
        ("accurate", "точный", ""), ("approach", "подход", ""), ("assume", "предполагать", ""),
        ("benefit", "польза; приносить пользу", ""), ("complex", "сложный", ""), ("concern", "беспокойство; касаться", ""),
        ("consistent", "последовательный", ""), ("define", "определять", ""), ("demand", "требование; требовать", ""),
        ("encourage", "поощрять", ""), ("evidence", "доказательство", ""), ("expand", "расширять", ""),
        ("feature", "особенность", ""), ("impact", "влияние", ""), ("indicate", "указывать", ""),
        ("maintain", "поддерживать", ""), ("method", "метод", ""), ("obvious", "очевидный", ""),
        ("participate", "участвовать", ""), ("perspective", "точка зрения", ""), ("predict", "предсказывать", ""),
        ("previous", "предыдущий", ""), ("principle", "принцип", ""), ("range", "диапазон", ""),
        ("reliable", "надёжный", ""), ("respond", "отвечать; реагировать", ""), ("significant", "значительный", ""),
        ("specific", "конкретный", ""), ("strategy", "стратегия", ""), ("task", "задача", ""),
        ("acknowledge", "признавать", ""), ("adapt", "адаптироваться; адаптировать", ""),
        ("adequate", "достаточный", ""), ("advocate", "выступать за", ""), ("allocate", "распределять", ""),
        ("anticipate", "предвидеть", ""), ("apparent", "очевидный; кажущийся", ""), ("attribute", "приписывать", ""),
        ("clarify", "прояснять", ""), ("constraint", "ограничение", ""), ("contribute", "вносить вклад", ""),
        ("derive", "получать; происходить", ""), ("emphasis", "акцент", ""), ("enhance", "улучшать", ""),
        ("evaluate", "оценивать", ""), ("framework", "структура; рамка", ""), ("imply", "подразумевать", ""),
        ("incentive", "стимул", ""), ("inevitable", "неизбежный", ""), ("insight", "понимание; инсайт", ""),
        ("justify", "обосновывать", ""), ("prioritize", "расставлять приоритеты", ""), ("prohibit", "запрещать", ""),
        ("resolve", "решать; разрешать", ""), ("retain", "сохранять", ""), ("shift", "сдвиг; менять", ""),
        ("subtle", "тонкий; едва заметный", ""), ("sustain", "поддерживать длительно", ""), ("undergo", "претерпевать", ""),
        ("whereas", "тогда как", ""),
    ],
}

_NL_SEED_WORDS = {
    "simple": [
        ("altijd", "всегда", ""), ("begrijpen", "понимать", ""), ("betalen", "платить", ""),
        ("blijven", "оставаться", ""), ("boodschap", "покупка; сообщение", ""), ("buiten", "снаружи", ""),
        ("denken", "думать", ""), ("dichtbij", "рядом", ""), ("familie", "семья", ""),
        ("genoeg", "достаточно", ""), ("graag", "охотно; с удовольствием", ""), ("helpen", "помогать", ""),
        ("kiezen", "выбирать", ""), ("kijken", "смотреть", ""), ("kopen", "покупать", ""),
        ("leren", "учить", ""), ("luisteren", "слушать", ""), ("misschien", "может быть", ""),
        ("nodig", "нужный", ""), ("plaats", "место", ""), ("praten", "говорить", ""),
        ("samen", "вместе", ""), ("schoon", "чистый", ""), ("soms", "иногда", ""),
        ("vragen", "спрашивать", ""), ("vriend", "друг", ""), ("wachten", "ждать", ""),
        ("werken", "работать", ""), ("weten", "знать", ""), ("zoeken", "искать", ""),
        ("aanbieden", "предлагать", ""), ("afspraak", "встреча; запись", ""), ("beginnen", "начинать", ""),
        ("beslissen", "решать", ""), ("bereiken", "достигать", ""), ("beschrijven", "описывать", ""),
        ("betekenen", "значить", ""), ("bijna", "почти", ""), ("daarom", "поэтому", ""),
        ("duidelijk", "понятный", ""), ("eigenlijk", "вообще-то", ""), ("ervaring", "опыт", ""),
        ("gebruiken", "использовать", ""), ("gebeuren", "случаться", ""), ("gezellig", "уютный; приятный", ""),
        ("halen", "забирать; доставать", ""), ("herhalen", "повторять", ""), ("hoeven", "быть должным", "часто с niet/geen"),
        ("kloppen", "быть верным; стучать", ""), ("makkelijk", "лёгкий", ""), ("mening", "мнение", ""),
        ("mogelijk", "возможный", ""), ("ontmoeten", "встречать", ""), ("proberen", "пробовать", ""),
        ("reizen", "путешествовать", ""), ("rustig", "спокойный", ""), ("terug", "назад", ""),
        ("uitleggen", "объяснять", ""), ("vergeten", "забывать", ""), ("veranderen", "менять", ""),
    ],
    "medium": [
        ("aanpassen", "адаптировать; подстраивать", ""), ("aanraden", "советовать", ""),
        ("afhankelijk", "зависимый", ""), ("behalen", "достигать", ""), ("beïnvloeden", "влиять", ""),
        ("belangrijk", "важный", ""), ("bespreken", "обсуждать", ""), ("betrouwbaar", "надёжный", ""),
        ("bewijzen", "доказывать", ""), ("bijdragen", "вносить вклад", ""), ("doel", "цель", ""),
        ("gevolg", "последствие", ""), ("herkennen", "узнавать; распознавать", ""), ("inmiddels", "тем временем; уже", ""),
        ("kans", "шанс; возможность", ""), ("kennis", "знание", ""), ("namelijk", "а именно; ведь", ""),
        ("onderzoeken", "исследовать", ""), ("ontwikkelen", "развивать", ""), ("opletten", "внимательно следить", ""),
        ("oplossen", "решать проблему", ""), ("overwegen", "обдумывать", ""), ("rekening houden met", "учитывать", ""),
        ("resultaat", "результат", ""), ("samenwerken", "сотрудничать", ""), ("toestaan", "разрешать", ""),
        ("uitdaging", "вызов; трудность", ""), ("vermijden", "избегать", ""), ("verbeteren", "улучшать", ""),
        ("waarschijnlijk", "вероятно", ""),
    ],
    "hard": [
        ("aantonen", "показывать; доказывать", ""), ("benadering", "подход", ""), ("beperken", "ограничивать", ""),
        ("bevorderen", "способствовать", ""), ("complex", "сложный", ""), ("consequent", "последовательный", ""),
        ("daadwerkelijk", "действительно", ""), ("desondanks", "несмотря на это", ""), ("doeltreffend", "эффективный", ""),
        ("eisen", "требовать", ""), ("ernstig", "серьёзный", ""), ("gedrag", "поведение", ""),
        ("geschikt", "подходящий", ""), ("inschatten", "оценивать", ""), ("maatregel", "мера", ""),
        ("nadruk", "акцент", ""), ("ondersteunen", "поддерживать", ""), ("ontbreken", "отсутствовать", ""),
        ("overtuigen", "убеждать", ""), ("perspectief", "перспектива", ""), ("principe", "принцип", ""),
        ("reageren", "реагировать", ""), ("relevant", "релевантный", ""), ("schatten", "оценивать", ""),
        ("specifiek", "конкретный", ""), ("strategie", "стратегия", ""), ("toepassen", "применять", ""),
        ("uitbreiden", "расширять", ""), ("voorkomen", "предотвращать; случаться", ""), ("zorgvuldig", "тщательный", ""),
        ("aanscherpen", "уточнять; усиливать", ""), ("aanzienlijk", "значительный", ""), ("benadrukken", "подчёркивать", ""),
        ("beoordelen", "оценивать", ""), ("belemmeren", "препятствовать", ""), ("beschouwen", "рассматривать", ""),
        ("bewustwording", "осознание", ""), ("daarentegen", "напротив", ""), ("doorslaggevend", "решающий", ""),
        ("duurzaam", "устойчивый", ""), ("genuanceerd", "нюансированный", ""), ("grondig", "основательный", ""),
        ("handhaven", "поддерживать; обеспечивать соблюдение", ""), ("in aanmerking komen", "подходить; иметь право", ""),
        ("inzicht", "понимание", ""), ("kenmerk", "характерная черта", ""), ("noodzakelijk", "необходимый", ""),
        ("onderbouwen", "обосновывать", ""), ("onderscheiden", "различать", ""), ("onvermijdelijk", "неизбежный", ""),
        ("overeenkomen", "соответствовать; договариваться", ""), ("prioriteit", "приоритет", ""), ("rechtvaardigen", "оправдывать", ""),
        ("streven naar", "стремиться к", ""), ("subtiel", "тонкий; едва заметный", ""), ("toereikend", "достаточный", ""),
        ("uitgangspunt", "исходная точка", ""), ("veronderstellen", "предполагать", ""), ("voortvloeien uit", "следовать из", ""),
        ("wezenlijk", "существенный", ""),
    ],
}

_EN_SEED_PHRASES = {
    "simple": [("How are you?", "Как дела?", ""), ("I don't understand.", "Я не понимаю.", ""), ("Can you help me?", "Можете помочь?", ""), ("How much is it?", "Сколько это стоит?", ""), ("See you later.", "Увидимся позже.", ""), ("I would like...", "Я бы хотел...", ""), ("Where is the station?", "Где вокзал?", ""), ("I am sorry.", "Извините.", ""), ("No problem.", "Без проблем.", ""), ("What does it mean?", "Что это значит?", ""),
        ("Could you repeat that?", "Не могли бы повторить?", ""), ("I am looking for...", "Я ищу...", ""), ("It depends on...", "Это зависит от...", ""), ("I have already done it.", "Я уже это сделал.", ""), ("What do you think?", "Что ты думаешь?", ""), ("I need to change it.", "Мне нужно это изменить.", ""), ("Can I borrow this?", "Можно это одолжить?", ""), ("Let me know.", "Дай знать.", ""), ("I am on my way.", "Я уже в пути.", ""), ("That sounds good.", "Звучит хорошо.", "")],
    "medium": [("I see your point.", "Я понимаю твою мысль.", ""), ("It is worth trying.", "Это стоит попробовать.", ""), ("I need to improve this.", "Мне нужно это улучшить.", ""), ("Although it is difficult, it is useful.", "Хотя это сложно, это полезно.", ""), ("What is the main challenge?", "В чём главная сложность?", ""), ("I would rather avoid it.", "Я бы предпочёл этого избежать.", ""), ("It depends on the situation.", "Это зависит от ситуации.", ""), ("That is a good opportunity.", "Это хорошая возможность.", ""), ("Could you explain it briefly?", "Можешь кратко объяснить?", ""), ("I have noticed that...", "Я заметил, что...", "")],
    "hard": [("From my perspective...", "С моей точки зрения...", ""), ("The evidence suggests that...", "Данные указывают на то, что...", ""), ("We need a reliable method.", "Нам нужен надёжный метод.", ""), ("It has a significant impact.", "Это оказывает значительное влияние.", ""), ("Let me clarify one point.", "Позволь уточнить один момент.", ""), ("The previous approach did not work.", "Предыдущий подход не сработал.", ""), ("This strategy is more consistent.", "Эта стратегия более последовательна.", ""), ("What are the main concerns?", "Какие главные опасения?", ""), ("It is not that obvious.", "Это не так очевидно.", ""), ("We should define the task first.", "Сначала нужно определить задачу.", ""),
        ("I acknowledge the concern.", "Я признаю эту обеспокоенность.", ""), ("That implies a different approach.", "Это подразумевает другой подход.", ""), ("We need to prioritize the issue.", "Нужно расставить приоритеты в вопросе.", ""), ("The outcome was inevitable.", "Исход был неизбежен.", ""), ("Let me justify this decision.", "Позволь обосновать это решение.", ""), ("This framework is too narrow.", "Эта рамка слишком узкая.", ""), ("It requires a subtle shift.", "Это требует тонкого сдвига.", ""), ("The incentive is not clear.", "Стимул неясен.", ""), ("We should evaluate the impact.", "Нужно оценить влияние.", ""), ("Whereas the first option is faster...", "Тогда как первый вариант быстрее...", "")],
}

_NL_SEED_PHRASES = {
    "simple": [("Hoe gaat het?", "Как дела?", ""), ("Ik begrijp het niet.", "Я не понимаю.", ""), ("Kunt u mij helpen?", "Можете мне помочь?", ""), ("Hoeveel kost het?", "Сколько это стоит?", ""), ("Tot later.", "До встречи.", ""), ("Ik wil graag...", "Я хотел бы...", ""), ("Waar is het station?", "Где вокзал?", ""), ("Het spijt me.", "Мне жаль.", ""), ("Geen probleem.", "Без проблем.", ""), ("Wat betekent dat?", "Что это значит?", ""),
        ("Kunt u dat herhalen?", "Можете это повторить?", ""), ("Ik ben op zoek naar...", "Я ищу...", ""), ("Het hangt af van...", "Это зависит от...", ""), ("Ik heb het al gedaan.", "Я уже это сделал.", ""), ("Wat vind je ervan?", "Что ты об этом думаешь?", ""), ("Ik moet het veranderen.", "Мне нужно это изменить.", ""), ("Mag ik dit lenen?", "Можно это одолжить?", ""), ("Laat het me weten.", "Дай мне знать.", ""), ("Ik ben onderweg.", "Я в пути.", ""), ("Dat klinkt goed.", "Звучит хорошо.", "")],
    "medium": [("Ik begrijp je punt.", "Я понимаю твою мысль.", ""), ("Het is de moeite waard.", "Это того стоит.", ""), ("Ik wil dit verbeteren.", "Я хочу это улучшить.", ""), ("Hoewel het moeilijk is, is het nuttig.", "Хотя это сложно, это полезно.", ""), ("Wat is de grootste uitdaging?", "В чём главная трудность?", ""), ("Ik wil dat liever vermijden.", "Я предпочёл бы этого избежать.", ""), ("Het hangt van de situatie af.", "Это зависит от ситуации.", ""), ("Dat is een goede kans.", "Это хорошая возможность.", ""), ("Kun je het kort uitleggen?", "Можешь кратко объяснить?", ""), ("Ik heb gemerkt dat...", "Я заметил, что...", "")],
    "hard": [("Vanuit mijn perspectief...", "С моей точки зрения...", ""), ("Dat toont aan dat...", "Это показывает, что...", ""), ("We hebben een betrouwbare methode nodig.", "Нам нужен надёжный метод.", ""), ("Het heeft een grote invloed.", "Это оказывает большое влияние.", ""), ("Laat me één punt verduidelijken.", "Позволь уточнить один момент.", ""), ("De vorige aanpak werkte niet.", "Предыдущий подход не сработал.", ""), ("Deze strategie is consequenter.", "Эта стратегия более последовательна.", ""), ("Wat zijn de belangrijkste zorgen?", "Какие основные опасения?", ""), ("Dat is niet zo vanzelfsprekend.", "Это не так очевидно.", ""), ("We moeten eerst de taak bepalen.", "Сначала нужно определить задачу.", ""),
        ("Ik erken die zorg.", "Я признаю это опасение.", ""), ("Dat veronderstelt een andere aanpak.", "Это предполагает другой подход.", ""), ("We moeten dit prioriteit geven.", "Нужно дать этому приоритет.", ""), ("De uitkomst was onvermijdelijk.", "Исход был неизбежен.", ""), ("Laat me deze beslissing onderbouwen.", "Позволь обосновать это решение.", ""), ("Dit uitgangspunt is te beperkt.", "Эта исходная рамка слишком ограничена.", ""), ("Dat vraagt om een subtiele verschuiving.", "Это требует тонкого сдвига.", ""), ("De prikkel is niet duidelijk.", "Стимул неясен.", ""), ("We moeten de impact beoordelen.", "Нужно оценить влияние.", ""), ("Daarentegen is de eerste optie sneller.", "Напротив, первый вариант быстрее.", "")],
}


def _seed_dataset(lang, kind):
    if kind == "phrase":
        return _NL_SEED_PHRASES if lang == "nl" else _EN_SEED_PHRASES
    return _NL_SEED_WORDS if lang == "nl" else _EN_SEED_WORDS


def _seed_language(cid, lang=None):
    if lang in ("nl", "en"):
        code = lang
    else:
        import settings as _s
        code = _code(_s.study_lang(cid))
    language = "нидерландский" if code == "nl" else "английский"
    level = store.get_level(cid, language)
    if level not in LEVELS:
        level = "medium"
    return code, language, level


def _seed_existing_keys(cid):
    return {
        _dict_item_key(_dict_lang(w), _dict_kind(w), _w_field(w, "word", "nl", "en"))
        for w in _ensure_dict(cid)
    }


def _seed_seen_keys(cid):
    prof = store.get_profile(cid)
    raw = prof.get(_DICT_SEED_SEEN_PROFILE_KEY) or []
    return {tuple(x) for x in raw if isinstance(x, (list, tuple)) and len(x) == 3}


def _seed_mark_seen(cid, items):
    if not items:
        return
    prof = store.get_profile(cid)
    seen = _seed_seen_keys(cid)
    for item in items:
        seen.add(_dict_item_key(item.get("lang"), item.get("kind"), item.get("word")))
    prof[_DICT_SEED_SEEN_PROFILE_KEY] = [list(x) for x in sorted(seen)]
    store.set_profile(cid, prof)


def _seed_candidates(cid, lang, level, kind="word"):
    blocked = _seed_existing_keys(cid) | _seed_seen_keys(cid)
    out = []
    for word, ru, note in _seed_dataset(lang, kind).get(level, []):
        item = {"lang": lang, "word": _cap(word), "ru": ru, "kind": kind, "note": note}
        key = _dict_item_key(lang, kind, item["word"])
        if key not in blocked:
            out.append(item)
        if len(out) >= _DICT_SEED_LIMIT:
            break
    return out


def _seed_state_get(cid):
    prof = store.get_profile(cid)
    st = prof.get(_DICT_SEED_PROFILE_KEY)
    return st if isinstance(st, dict) else {}


def _seed_state_set(cid, st):
    prof = store.get_profile(cid)
    prof[_DICT_SEED_PROFILE_KEY] = st
    store.set_profile(cid, prof)


def _seed_state_clear(cid):
    prof = store.get_profile(cid)
    prof.pop(_DICT_SEED_PROFILE_KEY, None)
    store.set_profile(cid, prof)


def _seed_item_line(item):
    text = f"{item.get('word')} — {item.get('ru')}"
    if item.get("note"):
        text += f" ({item['note']})"
    return text


def _seed_render_text(st):
    level = st.get("level", "medium")
    kind = st.get("kind", "word")
    items = st.get("items") or []
    selected = set(st.get("selected") or [])
    page = int(st.get("page") or 0)
    total_pages = max(1, (len(items) + _DICT_SEED_PAGE_SIZE - 1) // _DICT_SEED_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _DICT_SEED_PAGE_SIZE
    chunk = items[start:start + _DICT_SEED_PAGE_SIZE]
    level_label = LEVEL_LABELS.get(level, level)
    header = f"🧩 Стартовые фразы · {level_label}" if kind == "phrase" else f"📚 Популярные слова · {level_label}"
    lines = [
        header,
        f"Страница {page + 1} из {total_pages}",
        "",
        "Отметьте слова, которые хотите добавить в словарь:" if kind == "word" else "Отметьте фразы, которые хотите добавить в словарь:",
        "",
    ]
    for offset, item in enumerate(chunk):
        idx = start + offset
        mark = "✅" if idx in selected else "□"
        lines.append(f"{mark} {_seed_item_line(item)}")
    lines.extend(["", _DICT_SEED_SOURCE_NOTE])
    return "\n".join(lines)


def _seed_render_kb(st):
    items = st.get("items") or []
    selected = set(st.get("selected") or [])
    page = int(st.get("page") or 0)
    total_pages = max(1, (len(items) + _DICT_SEED_PAGE_SIZE - 1) // _DICT_SEED_PAGE_SIZE)
    start = page * _DICT_SEED_PAGE_SIZE
    chunk = items[start:start + _DICT_SEED_PAGE_SIZE]
    rows = []
    for offset, item in enumerate(chunk):
        idx = start + offset
        mark = "✅" if idx in selected else "□"
        rows.append([InlineKeyboardButton(f"{mark} {item.get('word')[:38]}", callback_data=f"a_dictseed_toggle_{idx}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"a_dictseed_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️ Далее", callback_data=f"a_dictseed_page_{page + 1}"))
    if nav:
        rows.append(nav)
    level_label = LEVEL_LABELS.get(st.get("level"), "Средний")
    rows.append([InlineKeyboardButton(f"📶 Другой уровень ({level_label})", callback_data="a_dictseed_level")])
    add_label = f"✅ Добавить отмеченные ({len(selected)})" if selected else "✅ Добавить отмеченные"
    rows.append([InlineKeyboardButton(add_label, callback_data="a_dictseed_add")])
    return InlineKeyboardMarkup(rows)


async def send_seed_intro(bot, cid, lang=None):
    code, language, level = _seed_language(cid, lang)
    items = _seed_candidates(cid, code, level, "word")
    if not items:
        await send_dict_lang(bot, cid, code)
        return
    text = (
        "Для эффективного обучения сначала наполним ваш словарь.\n\n"
        f"Я подобрал слова уровня «{LEVEL_LABELS.get(level, level)}». Просмотрите список и отметьте те, "
        "которые хотите добавить."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Наполнить словарь", callback_data=f"a_dictseed_start_{code}")],
        [InlineKeyboardButton("✏️ Добавить свои слова", callback_data=f"a_dictadd_smart_{code}")],
    ])
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def offer_seed_for_level_change(bot, cid, language, level):
    code = _code(language)
    items = _seed_candidates(cid, code, level, "word")
    if not items:
        return
    level_label = LEVEL_LABELS.get(level, level)
    text = (
        f"📚 Уровень обновлён до «{level_label}»\n\n"
        f"Хотите добавить стартовые слова уровня «{level_label}»?\n"
        "Я покажу список, а вы отметите те, которые хотите добавить."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✨ Добавить слова ({level_label})", callback_data=f"a_dictseed_start_{code}")],
        [InlineKeyboardButton("Позже", callback_data="a_dictseed_later")],
    ])
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_later(bot, cid):
    _seed_state_clear(cid)
    await send_dict(bot, cid)


async def seed_start(bot, cid, lang=None, kind="word", q=None):
    code, _language, level = _seed_language(cid, lang)
    items = _seed_candidates(cid, code, level, kind)
    if not items:
        text = (
            "📚 Словарь уже заполнен\n\n"
            "Для вашего уровня пока нет новых стартовых слов.\n"
            "Можно добавить свои слова вручную или перейти к фразам."
        )
        if q is not None:
            try:
                await q.message.edit_text(text)
                return
            except Exception:
                pass
        await bot.send_message(chat_id=cid, text=text)
        return
    st = {
        "lang": code,
        "level": level,
        "kind": kind,
        "items": items,
        "selected": [],
        "page": 0,
        "created_at": datetime.now(config.TZ).isoformat(),
        "confirmed": False,
    }
    _seed_state_set(cid, st)
    text = _seed_render_text(st)
    kb = _seed_render_kb(st)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_toggle(bot, cid, idx, q=None):
    st = _seed_state_get(cid)
    items = st.get("items") or []
    if not (0 <= idx < len(items)):
        return
    selected = set(st.get("selected") or [])
    if idx in selected:
        selected.remove(idx)
    else:
        selected.add(idx)
    st["selected"] = sorted(selected)
    _seed_state_set(cid, st)
    if q is not None:
        try:
            await q.message.edit_text(_seed_render_text(st), reply_markup=_seed_render_kb(st))
        except Exception:
            await bot.send_message(chat_id=cid, text=_seed_render_text(st), reply_markup=_seed_render_kb(st))


async def seed_page(bot, cid, page, q=None):
    st = _seed_state_get(cid)
    if not st:
        return
    st["page"] = max(0, int(page))
    _seed_state_set(cid, st)
    if q is not None:
        try:
            await q.message.edit_text(_seed_render_text(st), reply_markup=_seed_render_kb(st))
        except Exception:
            await bot.send_message(chat_id=cid, text=_seed_render_text(st), reply_markup=_seed_render_kb(st))


def _seed_level_kb(cid, code):
    _l, _language, current = _seed_language(cid, code)
    row = []
    for level in _SEED_LEVELS:
        mark = "✅ " if level == current else ""
        row.append(InlineKeyboardButton(f"{mark}{LEVEL_LABELS[level]}", callback_data=f"a_dictseedlvl_{code}_{level}"))
    rows = [row, [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictseed_start_{code}")]]
    return InlineKeyboardMarkup(rows)


async def seed_choose_level(bot, cid, q=None):
    st = _seed_state_get(cid)
    code = st.get("lang") if st else None
    code = code or _seed_language(cid)[0]
    text = "📶 Выбери уровень слов для добавления."
    kb = _seed_level_kb(cid, code)
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


async def seed_set_level(bot, cid, lang, level, q=None):
    if level not in _SEED_LEVELS:
        return
    st = _seed_state_get(cid)
    kind = st.get("kind", "word") if st else "word"
    language = "нидерландский" if lang == "nl" else "английский"
    store.set_level(cid, language, level)
    await seed_start(bot, cid, lang, kind=kind, q=q)


async def seed_add_selected(bot, cid, q=None):
    st = _seed_state_get(cid)
    if not st:
        await bot.send_message(chat_id=cid, text="Подборка устарела. Открой словарь заново.")
        return
    if st.get("confirmed"):
        await bot.send_message(chat_id=cid, text="Эта подборка уже обработана.")
        return
    st["confirmed"] = True
    _seed_state_set(cid, st)
    selected = set(st.get("selected") or [])
    existing = _seed_existing_keys(cid)
    added = []
    for idx, item in enumerate(st.get("items") or []):
        if idx not in selected:
            continue
        key = _dict_item_key(item["lang"], item["kind"], item["word"])
        if key in existing:
            continue
        legacy = {k: item[k] for k in ("lang", "word", "ru", "kind") if item.get(k)}
        store.add_to_list(config.DICT_KEY, cid, legacy)
        existing.add(key)
        added.append(legacy)
    kind = st.get("kind", "word")
    lang = st.get("lang", "en")
    _seed_mark_seen(cid, added)
    _seed_state_clear(cid)
    # Сразу генерируем пример/разбор для тренажёра — та же ленивая миграция,
    # что при первом обращении к старой записи, но выполненная сейчас, а не
    # отложенная до первого показа в тренажёре.
    for legacy in added:
        await _refresh_dict_entry(cid, legacy)
    noun = "фраз" if kind == "phrase" else "слов"
    if added:
        terms = ", ".join(a.get("word", "") for a in added[:10])
        more = f" и ещё {len(added) - 10}" if len(added) > 10 else ""
        text = f"✅ Добавлено {len(added)} {noun}: {terms}{more}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 Начать обучение", callback_data=f"a_train_{lang}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}")],
        ])
    else:
        text = "Ничего не отмечено — словарь не изменился."
        kb = None
    if q is not None:
        try:
            await q.message.edit_text(text, reply_markup=kb)
        except Exception:
            await bot.send_message(chat_id=cid, text=text, reply_markup=kb)
    else:
        await bot.send_message(chat_id=cid, text=text, reply_markup=kb)


def _dict_kind(w):
    if isinstance(w, dict) and w.get("kind"):
        return w["kind"]
    word = w.get("word", "") if isinstance(w, dict) else str(w)
    return "phrase" if " " in word.strip() else "word"

def _dict_lang(w):
    return w.get("lang", "nl") if isinstance(w, dict) else "nl"

def _dict_counts(cid):
    """Количество записей словаря по языку — единый счётчик, без деления
    на слова и фразы."""
    words = _ensure_dict(cid)
    out = {"nl": 0, "en": 0}
    for w in words:
        lang = "en" if _dict_lang(w) == "en" else "nl"
        out[lang] += 1
    return out

async def _show_screen(bot, cid, text, entities=None, reply_markup=None, q=None):
    """Навигация внутри словаря: редактирует текущее сообщение, если есть callback
    query, иначе (первый вход, текстовая команда) шлёт новое."""
    if q is not None:
        try:
            await q.message.edit_text(text, entities=entities, reply_markup=reply_markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=reply_markup)


_DICT_ORIGIN_TO_BACK = {
    "notes": "m_notes",
    "menu": "m_learn",
    "mydata": "set_home",
    "learnset": "set_learning",
}
_DICT_BACK_TO_ORIGIN = {v: k for k, v in _DICT_ORIGIN_TO_BACK.items()}


async def send_dict(bot, cid, back="m_notes", q=None):
    c = _dict_counts(cid)
    nl_total = c["nl"]
    en_total = c["en"]
    msg = dict_ui.dict_overview(nl_total, en_total)
    origin = _DICT_BACK_TO_ORIGIN.get(back, "notes")
    rows = [
        [InlineKeyboardButton(f"🇳🇱 Нидерландский ({nl_total})", callback_data=f"a_dictlang_nl_from_{origin}")],
        [InlineKeyboardButton(f"🇬🇧 Английский ({en_total})", callback_data=f"a_dictlang_en_from_{origin}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
    ]
    await _show_screen(bot, cid, msg.text, msg.entities, InlineKeyboardMarkup(rows), q=q)

async def send_dict_lang(bot, cid, lang, back="m_learn", q=None, page=0):
    """Главный экран словаря — короткое меню без списка слов: Найти/Добавить-удалить
    (список слов теперь внутри этой вкладки)/Сгенерировать, «⬅️ Назад» ведёт туда,
    откуда открыли словарь (раздел «Обучение»)."""
    count = len(_dict_lang_entries(cid, lang))
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    rows = [
        [InlineKeyboardButton("🔍 Найти в словаре", callback_data=f"a_dictsearch_{lang}")],
        [InlineKeyboardButton("✏️ Добавить или удалить слово", callback_data=f"a_dictadd_smart_{lang}")],
        [InlineKeyboardButton("✨ Сгенерировать набор слов", callback_data=f"a_dictseed_start_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
    ]
    text = f"{flag} Мой словарь · {count} слов и фраз"
    await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)


async def send_dict_manage(bot, cid, lang, back="m_learn", q=None, page=0):
    """Вкладка «Добавить или удалить слово»: список слов (тап открывает карточку
    с удалением) + приглашение написать слово текстом, чтобы добавить его."""
    store.pending_input[str(cid)] = f"dictadd_smart_{lang}"
    entries = _dict_lang_entries(cid, lang)
    flag = "🇳🇱" if lang == "nl" else "🇬🇧"
    lang_title = "нидерландского" if lang == "nl" else "английского"
    add_hint = (
        "Пришли слово или фразу для изучения — можно сразу несколько, каждую с новой строки.\n"
        "Я сам приведу в правильную форму, переведу и разберу."
    )
    if not entries:
        rows = [[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}")]]
        text = f"{flag} Словарь {lang_title} языка пока пуст.\n\n{add_hint}"
        await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)
        return
    total_pages = max(1, (len(entries) + _DICT_LIST_PAGE_SIZE - 1) // _DICT_LIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _DICT_LIST_PAGE_SIZE
    chunk = entries[start:start + _DICT_LIST_PAGE_SIZE]
    word_buttons = []
    for item in chunk:
        term_key = _dict_item_key(lang, "", _entry_term(item))[2]
        word_buttons.append(InlineKeyboardButton(
            _cap(_entry_term(item))[:20],
            callback_data=f"a_dictview_{lang}_{page}_{term_key}",
        ))
    word_rows = [word_buttons[i:i + 2] for i in range(0, len(word_buttons), 2)]
    nav_rows = []
    if total_pages > 1:
        next_page = page + 1 if page < total_pages - 1 else 0
        nav_rows.append([InlineKeyboardButton("Следующее слово", callback_data=f"a_dicteditpage_{lang}_{next_page}")])
    rows = word_rows + nav_rows + [[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}")]]
    text = (
        f"{flag} Показаны {start + 1}–{start + len(chunk)} из {len(entries)}. "
        "Нажми на слово, чтобы посмотреть перевод, пример и удалить его.\n\n"
        f"{add_hint}"
    )
    await _show_screen(bot, cid, text, None, InlineKeyboardMarkup(rows), q=q)


def _dict_manage_kb(lang: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Мой словарь", callback_data=f"a_dictlang_{lang}")],
        [InlineKeyboardButton("✏️ Добавить или удалить слово", callback_data=f"a_dictadd_smart_{lang}")],
    ])


async def send_dict_search_prompt(bot, cid, lang, q=None):
    store.pending_input[str(cid)] = f"dictsearch_{lang}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}")]])
    await _show_screen(bot, cid, "🔍 Введи слово или фразу для поиска.", None, kb, q=q)


def _dict_search_kb(lang, term_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictdel_{lang}_{term_key}")],
        [InlineKeyboardButton("🔍 Искать ещё", callback_data=f"a_dictsearch_{lang}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}")],
    ])


async def handle_dict_search(bot, cid, lang, query):
    """Ищет по подстроке термина в словаре, показывает карточку с кнопкой удаления."""
    query_norm = re.sub(r"\s+", " ", (query or "").strip()).casefold()
    if not query_norm:
        await bot.send_message(chat_id=cid, text="Пришли слово или часть фразы для поиска.")
        return
    words = _ensure_dict(cid)
    match = None
    for item in words:
        if _dict_lang(item) != lang:
            continue
        term = _entry_term(item)
        if query_norm in term.casefold():
            match = item
            break
    if not match:
        await bot.send_message(
            chat_id=cid,
            text="Не нашла в словаре. Попробуй другое слово или посмотри весь список.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Мои слова и фразы", callback_data=f"a_dictedit_{lang}")],
            ]),
        )
        return
    if _entry_needs_ai_refresh(match):
        match = await _refresh_dict_entry(cid, match)
    msg = _dict_entry_message(match, status="found")
    term_key = _dict_item_key(lang, "", _entry_term(match))[2]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                            reply_markup=_dict_search_kb(lang, term_key))


async def confirm_delete_dict_entry(bot, cid, lang, term_key, q=None):
    await _show_screen(
        bot, cid, "Точно удалить это из словаря?", None,
        InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"a_dictdelok_{lang}_{term_key}"),
            InlineKeyboardButton("Отмена", callback_data=f"a_dictlang_{lang}"),
        ]]),
        q=q,
    )


async def del_dict_entry_by_term(bot, cid, lang, term_key, page=None, q=None):
    words = store.get_list(config.DICT_KEY, cid)
    removed = ""
    kept = []
    for item in words:
        if _dict_lang(item) == lang and _dict_item_key(lang, "", _entry_term(item))[2] == term_key and not removed:
            removed = _entry_term(item)
            continue
        kept.append(item)
    if removed:
        store.set_list(config.DICT_KEY, cid, kept)
    msg = dict_ui.dict_deleted(removed or "")
    if page is not None:
        await _show_screen(
            bot, cid, msg.text, msg.entities,
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад к списку", callback_data=f"a_dictedit_{lang}_{page}")]]),
            q=q,
        )
        return
    await _show_screen(bot, cid, msg.text, msg.entities, _dict_manage_kb(lang), q=q)


_DICT_LIST_PAGE_SIZE = 10


def _dict_lang_entries(cid, lang):
    """Слова языка, отсортированные по алфавиту — стабильный порядок для
    постраничного списка «Мои слова и фразы»."""
    entries = [w for w in _ensure_dict(cid) if _dict_lang(w) == lang]
    return sorted(entries, key=lambda w: _cap(_entry_term(w)).casefold())


def _dict_entry_view_kb(lang, page, term_key):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Удалить", callback_data=f"a_dictviewdel_{lang}_{page}_{term_key}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictedit_{lang}_{page}")],
    ])


async def send_dict_entry_view(bot, cid, lang, page, term_key, q=None):
    """Карточка слова из списка — тот же вид, что при добавлении, плюс удаление."""
    entries = _dict_lang_entries(cid, lang)
    match = next((w for w in entries if _dict_item_key(lang, "", _entry_term(w))[2] == term_key), None)
    if not match:
        await send_dict_lang(bot, cid, lang, page=page, q=q)
        return
    if _entry_needs_ai_refresh(match):
        match = await _refresh_dict_entry(cid, match)
    msg = _dict_entry_message(match, status="found")
    await _show_screen(bot, cid, msg.text, msg.entities, _dict_entry_view_kb(lang, page, term_key), q=q)


async def del_word(bot, cid, i):
    words = store.get_list(config.DICT_KEY, cid)
    removed = ""
    if i < len(words):
        removed_item = words.pop(i)
        removed = _cap(_entry_term(removed_item))
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


def _morning_method_line(method, entries):
    if not entries:
        return "В словаре пока нет записей на этом языке. Сегодня можно добавить что-то через словарь."
    return method


# ================= БАЗА ОШИБОК (mistakeReview) =================

def _mistakes(cid):
    return store.get_list(config.MISTAKES_KEY, cid)


def _find_open_mistake(mistakes, lang, term):
    term_key = term.strip().casefold()
    for m in mistakes:
        if (not m.get("resolved") and m.get("lang") == lang
                and str(m.get("term", "")).strip().casefold() == term_key):
            return m
    return None


def record_mistake(cid, lang, term, wrong, correct, explanation=""):
    """Записывает ошибку тренажёра в персистентную базу (mistakes.json). Если по
    этому же слову уже есть нерешённая ошибка — обновляет её вместо дубля."""
    import uuid
    term = (term or "").strip()
    wrong = (wrong or "").strip()
    correct = (correct or "").strip()
    if not term or not correct:
        return
    mistakes = _mistakes(cid)
    existing = _find_open_mistake(mistakes, lang, term)
    if existing:
        existing["wrong"] = wrong or existing.get("wrong", "")
        existing["correct"] = correct
        existing["explanation"] = explanation or existing.get("explanation", "")
        existing["last_reviewed_at"] = None
    else:
        mistakes.append({
            "id": uuid.uuid4().hex[:8],
            "lang": lang,
            "term": term,
            "wrong": wrong,
            "correct": correct,
            "explanation": explanation,
            "created_at": datetime.now(config.TZ).isoformat(),
            "review_count": 0,
            "last_reviewed_at": None,
            "resolved": False,
        })
    store.set_list(config.MISTAKES_KEY, cid, mistakes)


def resolve_mistake(cid, mistake_id):
    mistakes = _mistakes(cid)
    for m in mistakes:
        if m.get("id") == mistake_id:
            m["resolved"] = True
            store.set_list(config.MISTAKES_KEY, cid, mistakes)
            return True
    return False


def mark_mistake_reviewed(cid, mistake_id):
    mistakes = _mistakes(cid)
    for m in mistakes:
        if m.get("id") == mistake_id:
            m["review_count"] = int(m.get("review_count") or 0) + 1
            m["last_reviewed_at"] = datetime.now(config.TZ).isoformat()
            store.set_list(config.MISTAKES_KEY, cid, mistakes)
            return m
    return None


def next_open_mistake(cid, lang=None):
    """Следующая нерешённая ошибка для повторения: сначала никогда не
    повторённые, потом давно не повторявшиеся."""
    mistakes = [m for m in _mistakes(cid) if not m.get("resolved")]
    if lang:
        mistakes = [m for m in mistakes if m.get("lang") == lang]
    if not mistakes:
        return None
    def _key(m):
        reviewed = m.get("last_reviewed_at")
        return (0 if not reviewed else 1, reviewed or "")
    return sorted(mistakes, key=_key)[0]


def _mistake_review_kb(back="m_learn"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data=back)]])


async def send_mistake_review(bot, cid, language=None, back="m_learn"):
    """Показывает одну открытую ошибку на повторение. Если ошибок нет —
    короткое сообщение вместо пустого экрана."""
    lang_code = _code(language) if language else _active_language_code(cid)
    mistake = next_open_mistake(cid, lang_code)
    if not mistake:
        msg = learning_ui.no_open_mistakes_card()
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                                reply_markup=_mistake_review_kb(back))
        return
    msg = learning_ui.mistake_review_card(mistake)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup)


async def mistake_retry(bot, cid, mistake_id):
    """«Попробовать снова»: помечает ошибку как повторённую и продолжает
    тренажёр (следующая карточка по обычной логике)."""
    mark_mistake_reviewed(cid, mistake_id)
    if store.train_state.get(str(cid)):
        await _render_next_train_quiz(bot, cid)
    else:
        await train_start(bot, cid, active_language(cid))


async def mistake_understood(bot, cid, mistake_id):
    """«Уже понял»: ошибка закрыта, продолжаем тренажёр той же карточкой
    выбора (следующее слово по обычной логике)."""
    resolve_mistake(cid, mistake_id)
    if store.train_state.get(str(cid)):
        await _render_next_train_quiz(bot, cid)
    else:
        await send_mistake_review(bot, cid)


# ================= ДИАЛОГОВЫЙ ТРЕНАЖЁР =================
# Ситуация → реплика собеседника → варианты ответа кнопками → фидбек. Весь
# диалог (ситуация + 3-4 реплики + варианты + пометка лучшего варианта)
# генерируется одним LLM-запросом, дальше только листаем уже готовые шаги.

def generate_dialogue(language, level):
    level_label = LEVEL_LABELS.get(level, "средний")
    d = ai.llm_json(
        f"Придумай короткий бытовой диалог на {language} для тренировки речи. "
        f"Уровень: {level_label.lower()}. 3-4 реплики собеседника, на каждую — "
        "2-3 варианта ответа ученика на том же языке, из которых один самый "
        "естественный, остальные — тоже понятные, но менее уместные (не грубые "
        "ошибки, а стилистически слабее). Перемешивай позицию самого естественного "
        "варианта в списке options от шага к шагу — не ставь его всегда первым.\n"
        'JSON: {"topic": "тема диалога по-русски", "steps": ['
        '{"line": "реплика собеседника", "options": ["вариант 1", "вариант 2", "вариант 3"], '
        '"best": "индекс (0, 1 или 2) самого естественного варианта в options — определи заново для каждого шага, '
        'не копируй одно и то же число", '
        '"note": "короткое пояснение по-русски, почему вариант под индексом best лучше остальных"}'
        "]}",
        900, tier="cheap", module="learning",
    )
    return d or {}


async def dialogue_start(bot, cid):
    store.pending_input.pop(str(cid), None)
    store.game_state.pop(str(cid), None)
    language = active_language(cid)
    level = store.get_level(cid, language)
    try:
        d = generate_dialogue(language, level)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    steps = [s for s in (d.get("steps") or []) if s.get("line") and s.get("options")]
    if not steps:
        await bot.send_message(chat_id=cid, text="Не получилось собрать диалог. Попробуй ещё раз."); return
    store.dialogue_state[str(cid)] = {
        "lang": language, "topic": d.get("topic", ""), "steps": steps, "step": 0,
    }
    await _render_dialogue_step(bot, cid)


async def _render_dialogue_step(bot, cid):
    st = store.dialogue_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Диалог устарел, открой заново."); return
    idx = st["step"]
    steps = st["steps"]
    if idx >= len(steps):
        msg = learning_ui.dialogue_summary_card(st.get("topic", ""))
        store.dialogue_state.pop(str(cid), None)
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup)
        return
    step = steps[idx]
    msg = learning_ui.dialogue_step_card(
        _flag(st["lang"]), st.get("topic", ""), step.get("line", ""),
        step.get("options", []), idx + 1, len(steps),
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup)


async def dialogue_pick(bot, cid, option_idx):
    st = store.dialogue_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Диалог устарел, открой заново."); return
    steps = st["steps"]
    idx = st["step"]
    if idx >= len(steps):
        return
    step = steps[idx]
    options = step.get("options", [])
    if option_idx >= len(options):
        return
    picked = options[option_idx]
    is_good = option_idx == int(step.get("best", 0))
    msg = learning_ui.dialogue_feedback_card(picked, is_good, step.get("note", "") if not is_good else "")
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=msg.reply_markup)


async def dialogue_next(bot, cid):
    st = store.dialogue_state.get(str(cid))
    if not st:
        await bot.send_message(chat_id=cid, text="Диалог устарел, открой заново."); return
    st["step"] += 1
    await _render_dialogue_step(bot, cid)


def _entries_priority_sorted(pool):
    """Сортировка по приоритету: сначала никогда не показанные, потом давно
    показанные, потом невыученные — используется и для утренней подборки."""
    def _key(w):
        shown = w.get("last_shown_at")
        never_shown = 0 if not shown else 1
        not_known = 0 if w.get("status") != "known" else 1
        return (never_shown, not_known, shown or "")
    return sorted(pool, key=_key)


def _build_morning_word(cid, language):
    """Собирает карточку слова дня (без отправки) -> (MessageSpec, del_row[InlineKeyboardButton])."""
    import random as _r
    from datetime import datetime
    lang_code = _code(language)
    flag = _flag(language)
    wd = datetime.now(config.TZ).weekday()
    _title, _phase, method = WEEK_TRACK[wd]
    words = _ensure_dict(cid)
    pool = [w for w in words if _dict_lang(w) == lang_code and _entry_term(w) and _entry_translation(w)]
    if wd >= 5 or not pool:
        msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"), empty_hint=True)
        return msg, []
    method = _morning_method_line(method, pool)
    ranked = _entries_priority_sorted(pool)
    top_n = ranked[:max(5, len(ranked) // 2)]
    chosen = _r.sample(top_n, min(5, len(top_n)))
    if not chosen:
        msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"))
        return msg, []

    now_iso = datetime.now(config.TZ).isoformat()
    del_row = []
    lines = []
    for w in chosen:
        term = _cap(_entry_term(w))
        ru = _entry_translation(w)
        lines.append((term, ru))
        try:
            idx = words.index(w)
            words[idx]["last_shown_at"] = now_iso
            del_row.append(InlineKeyboardButton(f"❌ {term[:20]}", callback_data=f"worddel_{idx}"))
        except ValueError:
            pass
    try:
        store.set_list(config.DICT_KEY, cid, words)
    except Exception:
        pass

    msg = learning_ui.morning_words(flag, method, is_read_aloud=method.startswith("Прочитай вслух"), words=lines)
    return msg, del_row


async def send_morning_word(bot, cid, language=None, with_kb=True):
    """11:00 - Daily Words: метод дня недели + порция из 5 записей словаря,
    без деления на слова и фразы — приоритет давно не показанным."""
    import settings
    language = language or settings.study_lang(cid)
    msg, del_row = _build_morning_word(cid, language)
    rows = _chunks(del_row, 3) if with_kb else []
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows) if rows else None,
    )


async def send_daily_practice(bot, cid):
    """11:00 - "Практика языка": слово дня и живая фраза активного языка одним сообщением."""
    import settings
    from ui.builder import MessageBuilder
    language = settings.study_lang(cid)
    word_msg, _del_row = _build_morning_word(cid, language)
    proverb_data = await _generate_proverb(language)
    proverb_msg = learning_ui.proverb_card(
        _flag(language), proverb_data["original"], proverb_data["analogs"],
        _cap(proverb_data["meaning"]), proverb_data["example"], proverb_data["example_ru"],
    )
    combined = MessageBuilder()
    combined.embed(word_msg)
    combined.embed(proverb_msg)
    msg = combined.build_stripped()
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)


# ================= ИГРА-ДЕТЕКТИВ =================
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
        "again": "✨ Ещё",
        "back": "⬅️ Назад",
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
        [InlineKeyboardButton("⬅️ Назад", callback_data="game_change")],
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
        [InlineKeyboardButton(ui["back"], callback_data="m_learn")],
    ])
    try:
        await q.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= НАСТРОЙКИ ОБУЧЕНИЯ =================
def learning_settings_kb(active_lang, active_level, back="set_home"):
    row = []
    for level in LEVELS:
        mark = "✅ " if level == active_level else ""
        row.append(InlineKeyboardButton(f"{mark}{LEVEL_LABELS[level]}", callback_data=f"set_learning_level_{level}"))
    code = _code(active_lang)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📚 Язык: {_language_display(active_lang)}", callback_data="toggle_learning_language")],
        row,
        [InlineKeyboardButton("📖 Мой словарь", callback_data=f"a_dictlang_{code}_from_learnset")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=back)],
    ])


async def send_learning_settings(bot, cid, q=None, back="set_home"):
    active_lang = active_language(cid)
    active_level = store.get_level(cid, active_lang)
    msg = learning_ui.learning_settings(_language_display(active_lang), _level_label(active_level))
    kb = learning_settings_kb(active_lang, active_level, back)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_levels(bot, cid, q=None, back="set_home"):
    await send_learning_settings(bot, cid, q=q, back=back)


async def handle_learning_settings_callback(bot, cid, q, data):
    back = "m_learn"
    if data == "set_learning":
        await send_learning_settings(bot, cid, q=q, back=back)
        return
    if data == "toggle_learning_language":
        old_code = _active_language_code(cid)
        new_code = "en" if old_code == "nl" else "nl"
        store.set_learning_language(cid, new_code)
        store.ensure_level(cid, _language_for_code(old_code), "medium")
        store.ensure_level(cid, _language_for_code(new_code), "medium")
        prof = store.get_profile(cid)
        prof.pop("_myday_seed_prompted", None)
        store.set_profile(cid, prof)
        await send_learning_settings(bot, cid, q=q, back=back)
        return
    if data.startswith("set_learning_level_"):
        level = data[len("set_learning_level_"):]
        if level in LEVELS:
            language = active_language(cid)
            old_level = store.get_level(cid, language)
            store.set_level(cid, language, level)
            await send_learning_settings(bot, cid, q=q, back=back)
            if old_level != level:
                await offer_seed_for_level_change(bot, cid, language, level)
            return
        await send_learning_settings(bot, cid, q=q, back=back)


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
