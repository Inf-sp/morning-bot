"""Чистая сборка данных упражнений языкового тренажёра.

Модуль не знает о Telegram, store и AI. Для разговорных форматов вызывающий
код передаёт уже подготовленную ситуацию через ``situation``.
"""

import random
import re

from trainer_engine import (
    EXERCISE_BUILD_SENTENCE,
    EXERCISE_CHOOSE_NATURAL,
    EXERCISE_CHOOSE_REACTION,
    EXERCISE_CHOOSE_TRANSLATION,
    EXERCISE_CONTINUE_DIALOGUE,
    EXERCISE_FILL_GAP,
    EXERCISE_FIND_ERROR,
    EXERCISE_RECALL_FREE,
    EXERCISE_TRANSLATE_CONTEXT,
)


def entry_term(entry):
    return str(entry.get("term") or entry.get("word") or entry.get("base_form") or "")


def entry_translation(entry):
    return str(entry.get("translation") or entry.get("ru") or "")


def _cap(value):
    value = str(value or "").strip()
    return value[:1].upper() + value[1:] if value else value


def _first_translation(entry):
    return entry_translation(entry).split(";")[0].split(",")[0].strip()


def _example(entry):
    examples = entry.get("examples") or []
    if not isinstance(examples, list):
        return {}
    return next((example for example in examples if isinstance(example, dict)), {})


def _tokens(text):
    return [m.group(0).lower() for m in re.finditer(
        r"[\wÀ-ÖØ-öø-ÿ'-]+", str(text or ""), flags=re.UNICODE)]


def clean_options(correct, candidates, needed=2):
    result = []
    seen = {str(correct).lower()}
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        lowered = candidate.lower()
        junk = (not candidate or "____" in candidate or len(candidate) > 40
                or bool(set(_tokens(lowered)) & {"todo", "n/a", "none", "null"}))
        if not junk and lowered not in seen:
            result.append(candidate)
            seen.add(lowered)
        if len(result) >= needed:
            break
    return result


def _wrong_terms(entry, other_entries, rng):
    own_term = entry_term(entry)
    pool = [entry_term(other).lower() for other in other_entries
            if entry_term(other) and entry_term(other) != own_term]
    rng.shuffle(pool)
    return pool


def _blank_from_example(term, example_text):
    bare = re.sub(r"^(de|het|een|to|the|a|an)\s+", "", term.strip(), flags=re.I)
    for candidate in (term.strip(), bare.strip()):
        if candidate:
            pattern = re.compile(re.escape(candidate), re.I)
            match = pattern.search(example_text)
            if match:
                return pattern.sub("____", example_text, count=1), match.group(0)
    return "", ""


def _choose_translation(entry, other_entries, rng):
    correct = _first_translation(entry)
    pool = [_first_translation(other) for other in other_entries
            if entry_term(other) != entry_term(entry)]
    wrong = clean_options(correct, pool)
    return {"term": _cap(entry_term(entry)), "correct": correct, "wrong": wrong} if wrong else None


def _recall_free(entry, _other_entries, _rng):
    correct = _cap(entry_term(entry))
    hint = entry.get("construction") or entry.get("pos") or f"Начинается на «{correct[:1]}»"
    return {"ru": _first_translation(entry), "correct": correct, "hint": hint}


def _build_sentence(entry, _other_entries, rng):
    tokens = entry_term(entry).split()
    if len(tokens) < 3:
        return None
    shuffled = list(tokens)
    rng.shuffle(shuffled)
    return {"ru": _first_translation(entry), "correct": _cap(entry_term(entry)),
            "tokens": tokens, "shuffled": shuffled}


def _find_error(entry, _other_entries, rng):
    example = _example(entry)
    text = str(example.get("text") or "").strip()
    tokens = text.split()
    droppable = {"de", "het", "een", "the", "a", "an"}
    indices = [i for i, token in enumerate(tokens[:6])
               if token.lower().strip(".,!?") in droppable]
    if len(tokens) < 3 or not indices:
        return None
    drop_idx = rng.choice(indices)
    broken = tokens[:drop_idx] + tokens[drop_idx + 1:]
    return {
        "tokens": broken,
        "broken_idx": min(drop_idx, len(broken) - 1),
        "correct_text": text,
        "ru": str(example.get("translation") or entry_translation(entry)).split(";")[0].strip(),
        "note": entry.get("breakdown") or f"пропущен артикль «{tokens[drop_idx]}»",
    }


def _choose_natural(entry, other_entries, rng):
    correct = _cap(entry_term(entry))
    wrong = clean_options(correct, _wrong_terms(entry, other_entries, rng))
    return {"ru": _first_translation(entry), "correct": correct, "wrong": wrong} if len(wrong) >= 2 else None


def _fill_gap(entry, other_entries, rng):
    example = _example(entry)
    blank, correct = _blank_from_example(entry_term(entry), str(example.get("text") or ""))
    if not blank:
        return None
    wrong = clean_options(correct, _wrong_terms(entry, other_entries, rng))
    if not wrong:
        return None
    return {"blank_phrase": blank, "correct": correct, "wrong": wrong,
            "ru": str(example.get("translation") or entry_translation(entry)).strip(),
            "note": entry.get("breakdown") or ""}


def _translate_context(entry, _other_entries, _rng):
    alternatives = entry.get("alt_translations") or []
    if not isinstance(alternatives, list):
        alternatives = []
    return {"ru": _first_translation(entry), "correct": _cap(entry_term(entry)),
            "alt": alternatives,
            "situation": entry.get("situation_type") or ""}


def _conversation(entry, other_entries, rng, situation, dialogue=False):
    if not situation or not situation.get("line"):
        return None
    correct = _cap(entry_term(entry))
    wrong = clean_options(correct, _wrong_terms(entry, other_entries, rng))
    if len(wrong) < 2:
        return None
    key = "line" if dialogue else "situation"
    ru_key = "line_ru" if dialogue else "situation_ru"
    return {key: situation["line"], ru_key: situation.get("line_ru", ""),
            "correct": correct, "wrong": wrong}


_BUILDERS = {
    EXERCISE_CHOOSE_TRANSLATION: _choose_translation,
    EXERCISE_RECALL_FREE: _recall_free,
    EXERCISE_BUILD_SENTENCE: _build_sentence,
    EXERCISE_FIND_ERROR: _find_error,
    EXERCISE_CHOOSE_NATURAL: _choose_natural,
    EXERCISE_FILL_GAP: _fill_gap,
    EXERCISE_TRANSLATE_CONTEXT: _translate_context,
}


def build_exercise(entry, other_entries, exercise_type, *, situation=None, rng=None):
    """Возвращает полные данные одного из девяти форматов или ``None``."""
    rng = rng or random
    if exercise_type == EXERCISE_CHOOSE_REACTION:
        data = _conversation(entry, other_entries, rng, situation, dialogue=False)
    elif exercise_type == EXERCISE_CONTINUE_DIALOGUE:
        data = _conversation(entry, other_entries, rng, situation, dialogue=True)
    else:
        builder = _BUILDERS.get(exercise_type)
        data = builder(entry, other_entries, rng) if builder else None
    if data is None:
        return None
    return {**data, "exercise_type": exercise_type, "term": entry_term(entry),
            "lang": entry.get("lang", "nl")}
