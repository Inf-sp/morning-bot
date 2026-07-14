"""Персистентное состояние мастера начального наполнения словаря."""

import store


STATE_PROFILE_KEY = "_dict_seed"
SEEN_PROFILE_KEY = "_dict_seed_seen"


class SeedStateRepository:
    def __init__(self, cid):
        self.cid = str(cid)

    def get(self):
        state = store.get_profile(self.cid).get(STATE_PROFILE_KEY)
        return state if isinstance(state, dict) else {}

    def set(self, state):
        profile = store.get_profile(self.cid)
        profile[STATE_PROFILE_KEY] = state
        store.set_profile(self.cid, profile)

    def clear(self):
        profile = store.get_profile(self.cid)
        profile.pop(STATE_PROFILE_KEY, None)
        store.set_profile(self.cid, profile)

    def seen_keys(self):
        raw = store.get_profile(self.cid).get(SEEN_PROFILE_KEY) or []
        return {tuple(value) for value in raw
                if isinstance(value, (list, tuple)) and len(value) == 3}

    def mark_seen(self, keys):
        keys = set(keys)
        if not keys:
            return
        profile = store.get_profile(self.cid)
        seen = self.seen_keys() | keys
        profile[SEEN_PROFILE_KEY] = [list(value) for value in sorted(seen)]
        store.set_profile(self.cid, profile)
