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
    cid = str(cid)
    for poll_id, owner in list(store.train_polls.items()):
        owner_cid = owner.get("cid") if isinstance(owner, dict) else owner
        if str(owner_cid) == cid:
            store.train_polls.pop(poll_id, None)
    state: TrainerSession = {
        "lang": language,
        "queue": queue,
        "queue_idx": 0,
        "current": None,
        "last_exercise_type": "",
        "short_failures": {},
    }
    store.train_state[cid] = state
    return state


def get(cid) -> TrainerSession | None:
    return store.train_state.get(str(cid))


def expect_text_answer(cid) -> None:
    store.pending_input[str(cid)] = PENDING_ANSWER


def register_poll(cid, poll_id, task_id="") -> None:
    store.train_polls[str(poll_id)] = {
        "cid": str(cid),
        "task_id": str(task_id or ""),
    }


def take_poll_context(poll_id) -> tuple[str, str] | None:
    owner = store.train_polls.pop(str(poll_id), None)
    if not owner:
        return None
    if isinstance(owner, dict):
        return str(owner.get("cid") or ""), str(owner.get("task_id") or "")
    return str(owner), ""


def take_poll_chat(poll_id) -> str | None:
    context = take_poll_context(poll_id)
    return context[0] if context else None


def finish(cid) -> TrainerSession | None:
    cid = str(cid)
    state = store.train_state.pop(cid, None)
    if store.pending_input.get(cid) == PENDING_ANSWER:
        store.pending_input.pop(cid, None)
    for poll_id, owner in list(store.train_polls.items()):
        owner_cid = owner.get("cid") if isinstance(owner, dict) else owner
        if str(owner_cid) == cid:
            store.train_polls.pop(poll_id, None)
    return state
