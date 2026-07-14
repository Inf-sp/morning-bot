"""Предметные репозитории поверх KV-драйвера."""

import config
import storage_driver


class UserListRepository:
    """Коллекция одного пользователя внутри общего KV-ключа."""

    def __init__(self, key, cid):
        self.key = key
        self.cid = str(cid)

    def all(self):
        data = storage_driver.load(self.key)
        value = data.get(self.cid, []) if isinstance(data, dict) else []
        return list(value) if isinstance(value, list) else []

    def save(self, items):
        items = list(items)

        def change(data):
            data[self.cid] = items
            return data, None

        storage_driver.mutate(self.key, change)

    def mutate(self, function):
        def change(data):
            current = list(data.get(self.cid, []))
            updated, result = function(current)
            data[self.cid] = list(updated)
            return data, result
        return storage_driver.mutate(self.key, change)


class ProfileRepository:
    def __init__(self, cid):
        self.cid = str(cid)

    def get(self):
        return dict(storage_driver.load(config.PROFILE_KEY).get(self.cid, {}))

    def save(self, profile):
        profile = dict(profile)

        def change(data):
            data[self.cid] = profile
            return data, None
        storage_driver.mutate(config.PROFILE_KEY, change)
