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
    EXERCISE_VERB_FORM,
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
    pos = str(entry.get("pos") or "").strip().casefold()
    if not pos:
        return []
    lang = str(entry.get("lang") or "nl").strip().casefold()
    shape = _grammar_shape(correct, lang)
    pool = []
    for other in other_entries:
        if str(other.get("pos") or "").strip().casefold() != pos:
            continue
        forms = other.get("forms") or []
        if not isinstance(forms, list):
            forms = []
        term = entry_term(other)
        bare = re.sub(r"^(de|het|een|to|the|a|an)\s+", "", term, flags=re.I)
        values = [term, bare, *forms]
        for value in values:
            value = str(value or "").strip()
            if (1 <= len(_tokens(value)) <= 3 and len(value) <= 32
                    and _grammar_shape(value, lang) == shape):
                pool.append(value)
    rng.shuffle(pool)
    return clean_options(correct, pool)


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


def _translate_context(entry, _other_entries, _rng):
    alternatives = entry.get("alt_translations") or []
    if not isinstance(alternatives, list):
        alternatives = []
    return {"ru": _first_translation(entry), "correct": _cap(entry_term(entry)),
            "alt": alternatives,
            "situation": entry.get("situation_type") or ""}


def _verb_progress(entry):
    value = entry.get("verb_forms_progress") or {}
    return value if isinstance(value, dict) else {}


def _verb_form(entry, _other_entries, rng):
    infinitive = str(entry.get("infinitive") or "").strip().lower()
    past = str(entry.get("past_singular") or "").strip().lower()
    participle = str(entry.get("past_participle") or "").strip().lower()
    if not all((infinitive, past, participle)):
        return None
    level = int(entry.get("srs_level") or 0)
    progress = _verb_progress(entry)
    focus = min(
        ("past", "participle"),
        key=lambda key: int(progress.get(key) or 0),
    )
    correct = past if focus == "past" else participle
    if level <= 1:
        return {
            "mode": "choice", "prompt": f"{infinitive} → ?", "correct": correct,
            "wrong": [value for value in (infinitive, participle if focus == "past" else past)
                      if value != correct],
            "form_focus": focus,
        }
    if level <= 3 and rng.choice((True, False)):
        correct_row = f"{infinitive} → {past} → {participle}"
        wrong = [
            f"{infinitive} → {infinitive} → {participle}",
            f"{infinitive} → {past} → {infinitive}",
        ]
        return {
            "mode": "choice", "prompt": "Выбери правильный ряд",
            "correct": correct_row, "wrong": wrong, "form_focus": focus,
        }
    auxiliary = str(entry.get("auxiliary") or "hebben").strip().lower()
    if focus == "participle":
        finite_aux = "ben" if auxiliary == "zijn" else "heb"
        sentence = f"Ik {finite_aux} gisteren naar Amsterdam ____."
    else:
        sentence = "Ik ____ gisteren naar Amsterdam."
    return {
        "mode": "write" if level >= 4 else "choice",
        "prompt": sentence,
        "correct": correct,
        "wrong": [value for value in (infinitive, participle if focus == "past" else past)
                  if value != correct],
        "form_focus": focus,
    }


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
    EXERCISE_VERB_FORM: _verb_form,
}


def build_exercise(entry, other_entries, exercise_type, *, situation=None, rng=None):
    """Возвращает полные данные одного из десяти форматов или ``None``."""
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
