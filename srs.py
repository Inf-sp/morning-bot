"""Интервальное повторение (SM-2-подобный алгоритм) для тренажёра языка.

Отвечает только за расчёт: уровень знания, дату следующего показа, интервал.
Не знает про Telegram, UI или AI — принимает и возвращает plain dict (srs-поля
записи словаря), см. docs/word-trainer.md.
"""

from datetime import datetime, timedelta
import config

# Качество ответа -> числовой score 0-5 (используется для SM-2 и для выбора
# следующего действия). Соответствует таблице уровней в docs/word-trainer.md.
NOT_REMEMBERED = "not_remembered"
HINT_USED = "hint_used"
CHOSE_OPTION = "chose_option"
RECALLED_FREE = "recalled_free"
USED_IN_SENTENCE = "used_in_sentence"
CONFIDENT_NO_HINT = "confident_no_hint"

_QUALITY_SCORE = {
    NOT_REMEMBERED: 0,
    HINT_USED: 1,
    CHOSE_OPTION: 2,
    RECALLED_FREE: 3,
    USED_IN_SENTENCE: 4,
    CONFIDENT_NO_HINT: 5,
}

_MIN_EASINESS = 1.3
_DEFAULT_EASINESS = 2.5
_HISTORY_LIMIT = 20


def default_srs_state() -> dict:
    """Начальное SRS-состояние для новой записи словаря."""
    today = datetime.now(config.TZ).date().isoformat()
    return {
        "srs_level": 0,
        "srs_easiness": _DEFAULT_EASINESS,
        "srs_interval_days": 0,
        "srs_due_at": today,
        "srs_history": [],
        "srs_last_exercise_type": "",
    }


def calculate_knowledge_level(current_level: int, quality: str) -> int:
    """Новый уровень знания (0-5) по качеству последнего ответа.

    Ошибка/подсказка откатывает уровень на 0-1, уверенный самостоятельный
    ответ поднимает его максимум на один шаг за раз — чтобы одна удачная
    догадка не перепрыгивала сразу в "устойчивое знание" (см. таблицу
    уровней в docs/word-trainer.md)."""
    score = _QUALITY_SCORE.get(quality, 0)
    if score <= 1:
        return score  # not_remembered -> 0, hint_used -> 1
    return min(5, max(current_level, score - 1) + 1)


def schedule_next_review(srs_state: dict, quality: str) -> dict:
    """Возвращает НОВЫЙ srs_state (не мутирует вход) после ответа качества
    `quality`. SM-2: easiness корректируется по score 0-5, интервал растёт как
    interval * easiness при score >= 3, сбрасывается на 1 день при score < 3."""
    score = _QUALITY_SCORE.get(quality, 0)
    easiness = float(srs_state.get("srs_easiness", _DEFAULT_EASINESS))
    interval = int(srs_state.get("srs_interval_days", 0))
    level = int(srs_state.get("srs_level", 0))

    easiness = max(_MIN_EASINESS, easiness + (0.1 - (5 - score) * (0.08 + (5 - score) * 0.02)))

    if score < 3:
        interval = 1
    elif interval == 0:
        interval = 1
    elif interval == 1:
        interval = 6
    else:
        interval = round(interval * easiness)

    today = datetime.now(config.TZ).date()
    due = (today + timedelta(days=interval)).isoformat()

    history = list(srs_state.get("srs_history", []))
    history.append({
        "ts": datetime.now(config.TZ).isoformat(),
        "exercise_type": srs_state.get("_last_exercise_type", ""),
        "result": quality,
    })
    history = history[-_HISTORY_LIMIT:]

    return {
        **srs_state,
        "srs_level": calculate_knowledge_level(level, quality),
        "srs_easiness": round(easiness, 2),
        "srs_interval_days": interval,
        "srs_due_at": due,
        "srs_history": history,
    }


def record_answer(srs_state: dict, exercise_type: str, quality: str) -> dict:
    """Обёртка над schedule_next_review, которая также запоминает тип
    последнего задания (не повторять один формат подряд для одного материала)."""
    state = {**srs_state, "_last_exercise_type": exercise_type}
    updated = schedule_next_review(state, quality)
    updated["srs_last_exercise_type"] = exercise_type
    updated.pop("_last_exercise_type", None)
    return updated


def is_due(srs_state: dict, today=None) -> bool:
    today = today or datetime.now(config.TZ).date()
    due_at = srs_state.get("srs_due_at")
    if not due_at:
        return True
    try:
        return datetime.fromisoformat(due_at).date() <= today
    except ValueError:
        return True
