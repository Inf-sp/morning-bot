"""Чистое ядро очереди и выбора форматов языкового тренажёра.

Не знает о Telegram, store, AI и user id. Получает готовые
словарные записи и возвращает план тренировки.
"""

from datetime import date
import random

import srs


EXERCISE_CHOOSE_TRANSLATION = "choose_translation"
EXERCISE_RECALL_FREE = "recall_free"
EXERCISE_BUILD_SENTENCE = "build_sentence"
EXERCISE_FIND_ERROR = "find_error"
EXERCISE_CHOOSE_NATURAL = "choose_natural"
EXERCISE_FILL_GAP = "fill_gap"
EXERCISE_TRANSLATE_CONTEXT = "translate_context"
EXERCISE_CHOOSE_REACTION = "choose_reaction"
EXERCISE_CONTINUE_DIALOGUE = "continue_dialogue"

ALL_EXERCISES = (
    EXERCISE_CHOOSE_TRANSLATION, EXERCISE_RECALL_FREE,
    EXERCISE_BUILD_SENTENCE, EXERCISE_FIND_ERROR,
    EXERCISE_CHOOSE_NATURAL, EXERCISE_FILL_GAP,
    EXERCISE_TRANSLATE_CONTEXT, EXERCISE_CHOOSE_REACTION,
    EXERCISE_CONTINUE_DIALOGUE,
)

DEFAULT_QUEUE_SIZE = 12


def _entry_term(entry):
    return str(entry.get("term") or entry.get("word") or "").strip()


def _entry_kind(entry):
    if str(entry.get("construction") or "").strip():
        return "rule"
    return "phrase" if " " in _entry_term(entry) else "word"


def _srs_state(entry):
    if "srs_due_at" not in entry:
        return srs.default_srs_state()
    return {key: entry.get(key) for key in (
        "srs_level", "srs_easiness", "srs_interval_days", "srs_due_at",
        "srs_history", "srs_last_exercise_type",
    )}


def select_exercise_type(entry, avoid="", rng=random):
    """Выбирает доступный формат по уровню и типу материала."""
    level = int(entry.get("srs_level") or 0)
    kind = _entry_kind(entry)
    last = entry.get("srs_last_exercise_type") or ""

    if level <= 1:
        candidates = [EXERCISE_CHOOSE_TRANSLATION, EXERCISE_RECALL_FREE]
        if entry.get("examples"):
            candidates.append(EXERCISE_FILL_GAP)
    elif level <= 3:
        candidates = [EXERCISE_RECALL_FREE, EXERCISE_FILL_GAP]
        if kind == "phrase" and len(_entry_term(entry).split()) >= 3:
            candidates.append(EXERCISE_BUILD_SENTENCE)
        if entry.get("situation_type"):
            candidates.append(EXERCISE_CHOOSE_REACTION)
        if kind == "rule":
            candidates.append(EXERCISE_FIND_ERROR)
    else:
        candidates = [EXERCISE_TRANSLATE_CONTEXT, EXERCISE_RECALL_FREE]
        if entry.get("situation_type"):
            candidates.append(EXERCISE_CONTINUE_DIALOGUE)
        if kind == "phrase":
            candidates.append(EXERCISE_CHOOSE_NATURAL)

    filtered = [item for item in candidates if item != last and item != avoid]
    filtered = filtered or [item for item in candidates if item != last] or candidates
    return rng.choice(filtered)


def build_training_queue(entries, today=None, queue_size=DEFAULT_QUEUE_SIZE, rng=random):
    """Собирает очередь: повторение, сложные места и новый материал."""
    entries = list(entries or [])
    if not entries:
        return []
    today = today or date.today()

    due = [entry for entry in entries if srs.is_due(_srs_state(entry), today)]
    mistakes = [entry for entry in due if int(entry.get("srs_level") or 0) <= 1]
    due_ok = [entry for entry in due if entry not in mistakes]
    new_material = [entry for entry in entries if not entry.get("srs_history")]
    new_material = [entry for entry in new_material if entry not in due]

    target_due = round(queue_size * 0.6)
    target_mistakes = round(queue_size * 0.2)
    target_new = queue_size - target_due - target_mistakes
    if len(mistakes) > target_mistakes:
        extra = min(len(mistakes) - target_mistakes, target_new)
        target_mistakes += extra
        target_new -= extra

    picked = []

    def take(pool, count):
        pool = list(pool)
        rng.shuffle(pool)
        picked.extend(pool[:count])

    take(mistakes, target_mistakes)
    take(due_ok, target_due)
    take(new_material, target_new)

    if len(picked) < queue_size:
        picked_terms = {_entry_term(entry) for entry in picked}
        rest = [entry for entry in entries if _entry_term(entry) not in picked_terms]
        rng.shuffle(rest)
        picked.extend(rest[:queue_size - len(picked)])

    rng.shuffle(picked)
    queue = []
    previous_type = ""
    for entry in picked:
        exercise_type = select_exercise_type(entry, avoid=previous_type, rng=rng)
        queue.append({"entry": entry, "exercise_type": exercise_type})
        previous_type = exercise_type
    return queue
