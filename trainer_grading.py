"""Чистая локальная оценка ответов языкового тренажёра."""

from dataclasses import dataclass
from enum import Enum
import re

class AnswerQuality(str, Enum):
    NOT_REMEMBERED = "not_remembered"
    HINT_USED = "hint_used"
    CHOSE_OPTION = "chose_option"
    RECALLED_FREE = "recalled_free"
    USED_IN_SENTENCE = "used_in_sentence"
    CONFIDENT_NO_HINT = "confident_no_hint"


@dataclass(frozen=True)
class GradeResult:
    correct: bool
    quality: AnswerQuality


def _normalize(text):
    tokens = re.findall(r"[\wÀ-ÖØ-öø-ÿ'-]+", str(text or "").lower(), re.UNICODE)
    return " ".join(tokens)


def fuzzy_match(actual, expected):
    actual, expected = _normalize(actual), _normalize(expected)
    if not actual or not expected:
        return False
    if actual == expected:
        return True
    if abs(len(actual) - len(expected)) > max(2, len(expected) // 4):
        return False
    previous = list(range(len(expected) + 1))
    for i, left in enumerate(actual, 1):
        current = [i] + [0] * len(expected)
        for j, right in enumerate(expected, 1):
            cost = 0 if left == right else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1,
                             previous[j - 1] + cost)
        previous = current
    return previous[-1] <= max(1, len(expected) // 6)


def grade_choice(data, chosen_idx, options):
    if chosen_idx < 0 or chosen_idx >= len(options):
        return GradeResult(False, AnswerQuality.NOT_REMEMBERED)
    correct = str(options[chosen_idx]).strip().lower() == str(data["correct"]).strip().lower()
    return GradeResult(correct, AnswerQuality.CHOSE_OPTION if correct else AnswerQuality.NOT_REMEMBERED)


def grade_free_text(data, text, *, used_hint=False):
    variants = [data["correct"], *(data.get("alt") or [])]
    correct = any(fuzzy_match(text, variant) for variant in variants)
    if not correct:
        return GradeResult(False, AnswerQuality.NOT_REMEMBERED)
    quality = AnswerQuality.HINT_USED if used_hint else AnswerQuality.RECALLED_FREE
    return GradeResult(True, quality)


def grade_sentence(data, chosen_tokens):
    expected = data["tokens"]
    if sorted(token.lower() for token in chosen_tokens) != sorted(token.lower() for token in expected):
        return GradeResult(False, AnswerQuality.NOT_REMEMBERED)
    exact = [token.lower() for token in chosen_tokens] == [token.lower() for token in expected]
    quality = AnswerQuality.RECALLED_FREE if exact else AnswerQuality.USED_IN_SENTENCE
    return GradeResult(True, quality)


def grade_error_position(data, token_idx):
    correct = token_idx == data["broken_idx"]
    return GradeResult(correct, AnswerQuality.CHOSE_OPTION if correct else AnswerQuality.NOT_REMEMBERED)
