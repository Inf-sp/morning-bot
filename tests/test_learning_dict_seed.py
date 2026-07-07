import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import config
import learning
import store


def test_seed_candidates_skip_existing_words():
    cid = "seed-existing"
    store._save(config.DICT_KEY, {cid: [{"lang": "en", "word": "Achieve", "ru": "достигать", "kind": "word"}]})

    items = learning._seed_candidates(cid, "en", "B1", "word")

    assert all(item["word"].lower() != "achieve" for item in items)
    assert any(item["word"].lower() == "improve" for item in items)


def test_seed_known_items_are_excluded_on_add():
    cid = "seed-add"
    store._save(config.DICT_KEY, {cid: []})
    store.set_profile(cid, {
        "_dict_seed": {
            "lang": "en",
            "level": "B1",
            "kind": "word",
            "items": [
                {"lang": "en", "word": "Achieve", "ru": "достигать", "kind": "word"},
                {"lang": "en", "word": "Improve", "ru": "улучшать", "kind": "word"},
            ],
            "known": [0],
            "page": 0,
        }
    })

    class Bot:
        async def send_message(self, **kwargs):
            return None

    import asyncio
    asyncio.run(learning.seed_add_selected(Bot(), cid))

    words = store.get_list(config.DICT_KEY, cid)
    assert [item["word"] for item in words] == ["Improve"]
