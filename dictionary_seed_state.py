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
        store.mutate_profile(self.cid, lambda profile: (
            {**profile, STATE_PROFILE_KEY: state}, None,
        ))

    def clear(self):
        def change(profile):
            profile.pop(STATE_PROFILE_KEY, None)
            return profile, None

        store.mutate_profile(self.cid, change)

    def seen_keys(self):
        raw = store.get_profile(self.cid).get(SEEN_PROFILE_KEY) or []
        return {tuple(value) for value in raw
                if isinstance(value, (list, tuple)) and len(value) == 3}

    def mark_seen(self, keys):
        keys = set(keys)
        if not keys:
            return
        def change(profile):
            raw = profile.get(SEEN_PROFILE_KEY) or []
            seen = {
                tuple(value) for value in raw
                if isinstance(value, (list, tuple)) and len(value) == 3
            }
            profile[SEEN_PROFILE_KEY] = [list(value) for value in sorted(seen | keys)]
            return profile, None

        store.mutate_profile(self.cid, change)
