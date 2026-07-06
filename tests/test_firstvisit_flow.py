"""firstvisit: статусы, пропуск, теги-чекбоксы."""
import asyncio

import pytest

import config
import store
import firstvisit
import onboarding_status as obs

CID = "fv-flow-cid"


class _FakeBot:
    """Мини-бот: копит отправленные сообщения, чтобы проверять флоу."""
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


@pytest.fixture(autouse=True)
def _clean():
    for key in (config.PROFILE_KEY, config.WATCHLIST_KEY, config.ARTISTS_KEY, config.BOOKS_KEY):
        store._mem.pop(key, None)
    store.pending_input.pop(str(CID), None)
    firstvisit._tag_selection.pop(str(CID), None)
    yield
    for key in (config.PROFILE_KEY, config.WATCHLIST_KEY, config.ARTISTS_KEY, config.BOOKS_KEY):
        store._mem.pop(key, None)


@pytest.mark.unit
def test_skip_sets_skipped_status():
    bot = _FakeBot()
    asyncio.run(firstvisit.skip(bot, CID, "wardrobe"))
    assert obs.get(CID, "wardrobe") == obs.SKIPPED
    assert obs.is_settled(CID, "wardrobe")


@pytest.mark.unit
def test_needs_setup_auto_configures_when_data_present():
    # У пользователя уже есть жанры досуга → опрос не нужен, статус auto_configured.
    store.set_profile(CID, {"leisure_genres": "🎭 Драма"})
    assert firstvisit.needs_setup(CID, "leisure") is False
    assert obs.get(CID, "leisure") == obs.AUTO_CONFIGURED


@pytest.mark.unit
def test_health_tags_done_saves_focus_and_completes():
    bot = _FakeBot()
    # Выбираем два тега и жмём «Готово».
    asyncio.run(firstvisit.show_prompt(bot, CID, "health"))
    firstvisit._tag_selection[str(CID)]["health"] = {"sleep", "energy"}
    asyncio.run(firstvisit.tags_done(bot, CID, "health"))
    prof = store.get_profile(CID)
    assert "Сон" in prof["health_focus"]
    assert "Энергия" in prof["health_focus"]
    assert obs.get(CID, "health") == obs.COMPLETED


@pytest.mark.unit
def test_tags_done_with_no_selection_is_skip():
    bot = _FakeBot()
    asyncio.run(firstvisit.show_prompt(bot, CID, "health"))
    asyncio.run(firstvisit.tags_done(bot, CID, "health"))
    assert obs.get(CID, "health") == obs.SKIPPED


@pytest.mark.unit
def test_leisure_text_prompt_saves_genres_and_switches_to_text(monkeypatch):
    bot = _FakeBot()
    asyncio.run(firstvisit.show_prompt(bot, CID, "leisure"))
    firstvisit._tag_selection[str(CID)]["leisure"] = {"drama"}
    asyncio.run(firstvisit.leisure_text_prompt(bot, CID))
    # Жанр сохранён, и бот перешёл в текстовый режим (ждёт названия).
    assert "Драма" in store.get_profile(CID).get("leisure_genres", "")
    assert store.pending_input.get(str(CID)) == "firstvisit_leisure_titles"


@pytest.mark.unit
def test_leisure_titles_saved_via_handle_response(monkeypatch):
    bot = _FakeBot()

    async def _fake_llm(*a, **kw):
        return {"movies": ["Паразиты"], "artists": ["The xx"], "books": ["Дюна"]}

    monkeypatch.setattr(firstvisit.ai, "allm_json", _fake_llm)
    store.pending_input[str(CID)] = "firstvisit_leisure_titles"
    asyncio.run(firstvisit.handle_response(bot, CID, "leisure_titles", "Паразиты, The xx, Дюна"))
    assert "Паразиты" in store.get_list(config.WATCHLIST_KEY, CID)
    assert obs.get(CID, "leisure") == obs.COMPLETED

