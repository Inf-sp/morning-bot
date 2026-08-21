"""Проверенная ЛГБТ-инклюзия в рекомендациях без классификации через AI."""

import re

import store


_PROFILE_KEY = "inclusive_recommendation_rotation"
_INTERVAL = 5

_TITLES = {
    "movie": {
        "moonlight", "лунный свет",
        "portrait of a lady on fire", "портрет девушки в огне",
        "nimona", "нимона",
        "heartstopper", "трепет сердца",
        "it's a sin", "это грех",
        "pose", "поза",
    },
    "book": {
        "the song of achilles", "песнь ахилла",
        "giovanni's room", "комната джованни",
        "the price of salt", "цена соли", "кэрол",
        "last night at the telegraph club", "прошлой ночью в телеграфном клубе",
    },
    "game": {
        "hades", "life is strange: true colors", "tell me why",
        "the last of us part ii", "the last of us часть ii",
    },
}


def _normalise(value):
    text = re.sub(r"\([^)]*\)\s*$", "", str(value or "")).casefold()
    return " ".join(text.replace("ё", "е").split()).strip(" «»\"'.,!?")


def is_inclusive(kind, *titles):
    known = {_normalise(value) for value in _TITLES.get(kind, set())}
    return any(_normalise(title) in known for title in titles if title)


def is_due(cid, kind):
    state = store.get_profile(cid).get(_PROFILE_KEY) or {}
    return int(state.get(kind) or 0) >= _INTERVAL - 1


def record(cid, kind, inclusive):
    """Сбрасывает цикл после ЛГБТ-проекта, иначе приближает обязательную попытку."""
    def change(profile):
        state = dict(profile.get(_PROFILE_KEY) or {})
        state[kind] = 0 if inclusive else min(_INTERVAL - 1, int(state.get(kind) or 0) + 1)
        profile = {**profile, _PROFILE_KEY: state}
        return profile, None

    store.mutate_profile(cid, change)

