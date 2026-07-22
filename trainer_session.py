"""Единая точка доступа к временному состоянию языкового тренажёра.

Состояние остаётся process-local и переживать рестарт не обязано, но детали
хранения больше не протекают в обработчики и бизнес-логику тренажёра.
"""

from typing import Any, TypedDict

import store


PENDING_ANSWER = "trainer_answer"


class TrainerSession(TypedDict):
    lang: str
    queue: list[dict[str, Any]]
    queue_idx: int
    current: dict[str, Any] | None
    last_exercise_type: str
    short_failures: dict[str, int]


def start(cid, language, queue) -> TrainerSession:
    state: TrainerSession = {
        "lang": language,
        "queue": queue,
        "queue_idx": 0,
        "current": None,
        "last_exercise_type": "",
        "short_failures": {},
    }
    store.train_state[str(cid)] = state
    return state


def get(cid) -> TrainerSession | None:
    return store.train_state.get(str(cid))


def expect_text_answer(cid) -> None:
    store.pending_input[str(cid)] = PENDING_ANSWER


def register_poll(cid, poll_id) -> None:
    store.train_polls[str(poll_id)] = str(cid)


def take_poll_chat(poll_id) -> str | None:
    return store.train_polls.pop(str(poll_id), None)


def finish(cid) -> TrainerSession | None:
    cid = str(cid)
    state = store.train_state.pop(cid, None)
    if store.pending_input.get(cid) == PENDING_ANSWER:
        store.pending_input.pop(cid, None)
    for poll_id, poll_cid in list(store.train_polls.items()):
        if str(poll_cid) == cid:
            store.train_polls.pop(poll_id, None)
    return state
