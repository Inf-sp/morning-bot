"""Чистая сборка данных упражнений языкового тренажёра.

Модуль не знает о Telegram, store и AI. Для разговорных форматов вызывающий
код передаёт уже подготовленную ситуацию через ``situation``.
"""

import random
import re

from trainer_engine import (
    EXERCISE_BUILD_SENTENCE,
    EXERCISE_CHOOSE_REACTION,
    EXERCISE_CHOOSE_TRANSLATION,
    EXERCISE_FILL_GAP,
    EXERCISE_FIND_ERROR,
    EXERCISE_RECALL,
)


def entry_term(entry):
    return str(entry.get("term") or entry.get("word") or entry.get("base_form") or "")


def entry_translation(entry):
    return str(entry.get("translation") or entry.get("ru") or "")


def _cap(value):
    value = str(value or "").strip()
    return value[:1].upper() + value[1:] if value else value


def _first_translation(entry):
    # Запятая может быть частью полноценного перевода: «Я думаю, это глупо».
    return next((part.strip() for part in entry_translation(entry).split(";") if part.strip()), "")


def _example(entry):
    examples = entry.get("examples") or []
    if not isinstance(examples, list):
        return {}
    return next((example for example in examples if isinstance(example, dict)), {})


def _tokens(text):
    return [m.group(0).lower() for m in re.finditer(
        r"[\wÀ-ÖØ-öø-ÿ'-]+", str(text or ""), flags=re.UNICODE)]


_POS_ALIASES = {
    "noun": "noun", "сущ": "noun", "существительное": "noun", "substantief": "noun",
    "verb": "verb", "глагол": "verb", "werkwoord": "verb",
    "adjective": "adjective", "adj": "adjective", "прилагательное": "adjective", "bijvoeglijk naamwoord": "adjective",
    "adverb": "adverb", "наречие": "adverb", "bijwoord": "adverb",
    "preposition": "preposition", "предлог": "preposition", "voorzetsel": "preposition",
    "pronoun": "pronoun", "местоимение": "pronoun", "voornaamwoord": "pronoun",
    "conjunction": "conjunction", "союз": "conjunction", "voegwoord": "conjunction",
}

# Отвлекающие варианты не должны зависеть от личного словаря: иначе длинная
# фраза может получить рядом случайное слово, которое пользователь добавил
# когда-то давно. Это короткий проверенный учебный пул, а не материал ученика.
_LOCAL_PHRASE_DISTRACTORS = {
    "ru": (
        "Мы встречаемся после работы в центре города",
        "Я жду автобус у следующей остановки",
        "Она покупает подарок для своего друга",
        "Сегодня мы готовим ужин дома вместе",
        "Он ищет тихое место для учебы",
    ),
    "nl": (
        "We spreken na het werk in het centrum af",
        "Ik wacht bij de volgende halte op de bus",
        "Zij koopt een cadeau voor haar vriend",
        "Vandaag koken we samen thuis het avondeten",
        "Hij zoekt een rustige plek om te studeren",
    ),
    "en": (
        "We are meeting in the city after work",
        "I am waiting for the bus at the stop",
        "She is buying a gift for her friend",
        "Today we are cooking dinner together at home",
        "He is looking for a quiet place to study",
    ),
}

_LOCAL_WORD_DISTRACTORS = {
    "ru": {
        "noun": ("Встреча", "Причина", "Вопрос", "Подарок"),
        "verb": ("Ждать", "Искать", "Покупать", "Объяснять"),
        "adjective": ("Новый", "Важный", "Тихий", "Свободный"),
        "adverb": ("Медленно", "Скоро", "Вместе", "Снова"),
        "preposition": ("Перед", "После", "Вместо", "Между"),
        "pronoun": ("Кто-то", "Никто", "Каждый", "Другой"),
        "conjunction": ("Потому что", "Хотя", "Если", "Когда"),
        "": ("Вопрос", "Ждать", "Новый", "Скоро"),
    },
    "nl": {
        "noun": ("huis", "boek", "vriend", "trein"),
        "verb": ("wachten", "zoeken", "kopen", "leren"),
        "adjective": ("nieuw", "groot", "klein", "rustig"),
        "adverb": ("langzaam", "snel", "samen", "opnieuw"),
        "preposition": ("voor", "zonder", "tegen", "tussen"),
        "pronoun": ("iemand", "niemand", "iedereen", "ander"),
        "conjunction": ("omdat", "hoewel", "als", "wanneer"),
        "": ("huis", "wachten", "nieuw", "snel"),
    },
    "en": {
        "noun": ("house", "book", "friend", "train"),
        "verb": ("wait", "search", "buy", "learn"),
        "adjective": ("new", "large", "quiet", "ready"),
        "adverb": ("slowly", "soon", "together", "again"),
        "preposition": ("before", "after", "without", "between"),
        "pronoun": ("someone", "nobody", "everyone", "another"),
        "conjunction": ("because", "although", "if", "when"),
        "": ("house", "wait", "new", "soon"),
    },
}


def _entry_pos(entry):
    raw = " ".join(str(entry.get("pos") or "").casefold().split())
    if raw in _POS_ALIASES:
        return _POS_ALIASES[raw]
    if raw.startswith("сущ"):
        return "noun"
    if raw.startswith("глаг"):
        return "verb"
    if raw.startswith("прилаг"):
        return "adjective"
    if entry.get("article"):
        return "noun"
    if entry.get("construction"):
        return "rule"
    return ""


def _value_kind(value):
    return "phrase" if len(_tokens(value)) > 1 else "word"


def _compatible_term(entry, candidate):
    """Отвлекающий вариант должен совпадать по типу материала и форме."""
    term = entry_term(entry)
    other_term = entry_term(candidate)
    if not term or not other_term or _value_kind(term) != _value_kind(other_term):
        return False
    pos = _entry_pos(entry)
    other_pos = _entry_pos(candidate)
    if pos and other_pos != pos:
        return False
    if pos == "verb":
        lang = str(entry.get("lang") or "nl").casefold()
        if _grammar_shape(term, lang) != _grammar_shape(other_term, lang):
            return False
    return True


def _compatible_translation(entry, candidate):
    correct = _first_translation(entry)
    alternative = _first_translation(candidate)
    if not correct or not alternative or _value_kind(correct) != _value_kind(alternative):
        return False
    pos = _entry_pos(entry)
    return not pos or _entry_pos(candidate) == pos


def clean_options(correct, candidates, needed=2):
    result = []
    seen = {str(correct).lower()}
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        lowered = candidate.lower()
        junk = (not candidate or "____" in candidate or len(candidate) > 100
                or not any(char.isalpha() for char in candidate)
                or bool(set(_tokens(lowered)) & {"todo", "n/a", "none", "null"}))
        if not junk and lowered not in seen:
            result.append(candidate)
            seen.add(lowered)
        if len(result) >= needed:
            break
    return result


def _local_distractors(entry, correct, language, rng):
    """Полные учебные варианты той же формы, не связанные с базой пользователя."""
    language = str(language or "nl").casefold()
    language = language if language in _LOCAL_WORD_DISTRACTORS else "nl"
    if _value_kind(correct) == "phrase":
        pool = list(_LOCAL_PHRASE_DISTRACTORS[language])
    else:
        pool = list(_LOCAL_WORD_DISTRACTORS[language].get(
            _entry_pos(entry), _LOCAL_WORD_DISTRACTORS[language][""],
        ))
    rng.shuffle(pool)
    return clean_options(correct, pool)


def _wrong_terms(entry, other_entries, rng):
    own_term = entry_term(entry).casefold()
    pool = [entry_term(other) for other in other_entries
            if entry_term(other) and entry_term(other).casefold() != own_term
            and _compatible_term(entry, other)]
    rng.shuffle(pool)
    return pool


def _blank_from_example(term, example_text):
    bare = re.sub(r"^(de|het|een|to|the|a|an)\s+", "", term.strip(), flags=re.I)
    for candidate in (term.strip(), bare.strip()):
        if candidate and 1 <= len(_tokens(candidate)) <= 3 and len(candidate) <= 32:
            pattern = re.compile(re.escape(candidate), re.I)
            match = pattern.search(example_text)
            if match:
                return pattern.sub("____", example_text, count=1), match.group(0)
    return "", ""


def _grammar_shape(value, lang):
    tokens = _tokens(value)
    if len(tokens) != 1:
        return (len(tokens), "phrase")
    word = tokens[0]
    if lang == "nl":
        if word.endswith("en"):
            form = "infinitive"
        elif word.endswith("t"):
            form = "finite_t"
        elif word.endswith("d"):
            form = "finite_d"
        else:
            form = "base"
    elif word.endswith("ing"):
        form = "ing"
    elif word.endswith("ed"):
        form = "past"
    elif word.endswith("s"):
        form = "finite_s"
    else:
        form = "base"
    return (1, form)


def _gap_wrong_terms(entry, correct, other_entries, rng):
    """Only plausible options with the same POS, length and surface form."""
    pos = _entry_pos(entry)
    if not pos:
        return []
    lang = str(entry.get("lang") or "nl").strip().casefold()
    shape = _grammar_shape(correct, lang)
    pool = [value for value in _local_distractors(entry, correct, lang, rng)
            if (1 <= len(_tokens(value)) <= 3 and len(value) <= 32
                and _grammar_shape(value, lang) == shape)]
    rng.shuffle(pool)
    return clean_options(correct, pool)


def _choose_translation(entry, other_entries, rng):
    correct = _first_translation(entry)
    wrong = _local_distractors(entry, correct, "ru", rng)
    return (
        {"term": entry_term(entry), "correct": correct, "wrong": wrong}
        if correct and len(wrong) >= 2 else None
    )


def _recall(entry, other_entries, rng):
    correct = entry_term(entry)
    wrong = _local_distractors(entry, correct, entry.get("lang", "nl"), rng)
    translation = _first_translation(entry)
    if not correct or not translation or len(wrong) < 2:
        return None
    acceptable = entry.get("acceptable_answers") or entry.get("forms") or []
    acceptable = [str(value).strip() for value in acceptable if str(value).strip()]
    return {
        "ru": translation,
        "correct": correct,
        "wrong": wrong,
        "acceptable_answers": acceptable,
    }


def _build_sentence(entry, _other_entries, rng):
    tokens = entry_term(entry).split()
    if len(tokens) < 3 or len({token.casefold() for token in tokens}) < 2:
        return None
    shuffled = list(tokens)
    for _ in range(5):
        rng.shuffle(shuffled)
        if shuffled != tokens:
            break
    else:
        return None
    return {"ru": _first_translation(entry), "correct": _cap(entry_term(entry)),
            "tokens": tokens, "shuffled": shuffled}


def _find_error(entry, _other_entries, _rng):
    # Без сохранённого проверенного правила нельзя надёжно отличить
    # прилагательное от существительного после een.
    if entry.get("verified_error_rule") != "een_de_adjective":
        return None
    example = _example(entry)
    text = str(example.get("text") or "").strip()
    term = entry_term(entry).casefold()
    tokens = text.split()
    if not term or term not in text.casefold():
        return None
    # Строим только настоящую ошибку: у нидерландского прилагательного после
    # een перед de-существительным убираем обязательное окончание -e.
    error_idx = next((i for i in range(1, min(len(tokens) - 1, 6))
                      if tokens[i - 1].casefold() == "een"
                      and tokens[i].casefold().rstrip(".,!?").endswith("e")), None)
    if error_idx is None:
        return None
    correct_word = tokens[error_idx]
    punctuation = correct_word[len(correct_word.rstrip(".,!?")):]
    wrong_word = correct_word.rstrip(".,!?")[:-1] + punctuation
    broken = list(tokens)
    broken[error_idx] = wrong_word
    return {
        "tokens": broken,
        "broken_idx": error_idx,
        "correct_text": text,
        "ru": str(example.get("translation") or entry_translation(entry)).split(";")[0].strip(),
        "note": entry.get("breakdown") or f"правильно: «{correct_word}»",
    }


def _fill_gap(entry, other_entries, rng):
    example = _example(entry)
    blank, correct = _blank_from_example(entry_term(entry), str(example.get("text") or ""))
    if not blank:
        return None
    wrong = _gap_wrong_terms(entry, correct, other_entries, rng)
    if len(wrong) < 2:
        return None
    translation = str(example.get("translation") or entry_translation(entry)).strip()
    hint = _first_translation(entry)
    if not hint or len(hint) > 60:
        return None
    return {"blank_phrase": blank, "correct": correct, "wrong": wrong,
            "hint": hint,
            "result_sentence": blank.replace("____", correct, 1),
            "ru": translation,
            "note": entry.get("breakdown") or ""}


def _conversation(entry, other_entries, rng, situation):
    if not situation or not situation.get("line"):
        return None
    correct = _cap(entry_term(entry))
    wrong = _local_distractors(entry, correct, entry.get("lang", "nl"), rng)
    if len(wrong) < 2:
        return None
    return {"situation": situation["line"], "situation_ru": situation.get("line_ru", ""),
            "correct": correct, "wrong": wrong}


_BUILDERS = {
    EXERCISE_CHOOSE_TRANSLATION: _choose_translation,
    EXERCISE_RECALL: _recall,
    EXERCISE_BUILD_SENTENCE: _build_sentence,
    EXERCISE_FIND_ERROR: _find_error,
    EXERCISE_FILL_GAP: _fill_gap,
}


def build_exercise(entry, other_entries, exercise_type, *, situation=None, rng=None):
    """Возвращает полные данные одного из семи форматов или ``None``."""
    rng = rng or random
    if exercise_type == EXERCISE_CHOOSE_REACTION:
        data = _conversation(entry, other_entries, rng, situation)
    else:
        builder = _BUILDERS.get(exercise_type)
        data = builder(entry, other_entries, rng) if builder else None
    if data is None:
        return None
    return {**data, "exercise_type": exercise_type, "term": entry_term(entry),
            "lang": entry.get("lang", "nl"), "entry": dict(entry)}
