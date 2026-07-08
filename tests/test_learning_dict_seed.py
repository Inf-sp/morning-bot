import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import config
import learning
import store


class Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return None


class Message:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append({"text": text, "reply_markup": reply_markup})


class Query:
    def __init__(self):
        self.message = Message()


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

    import asyncio
    asyncio.run(learning.seed_add_selected(Bot(), cid))

    words = store.get_list(config.DICT_KEY, cid)
    assert [item["word"] for item in words] == ["Improve"]


def test_seed_candidates_are_limited_to_first_batch():
    cid = "seed-limit"
    store._save(config.DICT_KEY, {cid: []})

    items = learning._seed_candidates(cid, "en", "B1", "word")

    assert len(items) == 30
    assert items[0]["word"] == "Achieve"


def test_seed_toggle_checkbox_and_page_are_persisted():
    cid = "seed-toggle-page"
    store._save(config.DICT_KEY, {cid: []})
    store.set_profile(cid, {
        "_dict_seed": {
            "lang": "en",
            "level": "B1",
            "kind": "word",
            "items": learning._seed_candidates(cid, "en", "B1", "word"),
            "known": [],
            "page": 0,
        }
    })

    import asyncio
    q = Query()
    asyncio.run(learning.seed_toggle(Bot(), cid, 0, q=q))
    asyncio.run(learning.seed_page(Bot(), cid, 1, q=q))

    st = store.get_profile(cid)["_dict_seed"]
    assert st["known"] == [0]
    assert st["page"] == 1
    assert "☑ Achieve" in q.message.edits[0]["text"]
    assert "Страница 2 из 3" in q.message.edits[-1]["text"]


def test_seed_reopen_after_confirm_excludes_seen_items():
    cid = "seed-reopen"
    store._save(config.DICT_KEY, {cid: []})
    store.set_profile(cid, {
        "_dict_seed": {
            "lang": "en",
            "level": "B1",
            "kind": "word",
            "items": learning._seed_candidates(cid, "en", "B1", "word")[:2],
            "known": [0],
            "page": 0,
        }
    })

    import asyncio
    asyncio.run(learning.seed_add_selected(Bot(), cid))
    reopened = learning._seed_candidates(cid, "en", "B1", "word")

    assert all(item["word"] not in {"Achieve", "Although"} for item in reopened)


def test_level_change_offer_uses_new_level():
    cid = "seed-level-change"
    store._save(config.DICT_KEY, {cid: []})
    bot = Bot()

    import asyncio
    asyncio.run(learning.offer_seed_for_level_change(bot, cid, "английский", "B2"))

    assert "Уровень обновлён до B2" in bot.messages[0]["text"]
    keyboard = bot.messages[0]["reply_markup"].inline_keyboard
    assert keyboard[0][0].text == "✨ Добавить слова B2"
