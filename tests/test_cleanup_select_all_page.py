"""cleanup.py: кнопка «Выбрать все / Снять выбор на странице» (PR1, P1-1).

Намеренно ограничено объёмом PR1 — «выбрать все N элементов коллекции целиком»
(вторая, более рискованная кнопка) переносится в PR3 вместе со view_id/revision,
см. docs/audit-cleanup-plan.md.
"""
import asyncio

import pytest

import cleanup
import config
import store

CID = "cleanup-select-all-cid"


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


@pytest.fixture(autouse=True)
def _clean():
    store._mem.pop(config.FRIDGE_KEY, None)
    store.list_sel.pop(f"{CID}:fridge", None)
    yield
    store._mem.pop(config.FRIDGE_KEY, None)
    store.list_sel.pop(f"{CID}:fridge", None)


def _seed(n):
    store.set_list(config.FRIDGE_KEY, CID, [f"продукт {i}" for i in range(n)])


def _kb_texts(bot):
    last = bot.messages[-1]
    rows = last["reply_markup"].inline_keyboard
    return [btn.text for row in rows for btn in row]


def _kb_callbacks(bot):
    last = bot.messages[-1]
    rows = last["reply_markup"].inline_keyboard
    return [btn.callback_data for row in rows for btn in row]


@pytest.mark.unit
def test_no_select_all_button_with_single_item():
    _seed(1)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "fridge"))
    assert not any("страниц" in t for t in _kb_texts(bot))


@pytest.mark.unit
def test_select_all_button_appears_with_two_or_more_items():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "fridge"))
    assert any(t == "✅ Выбрать все на странице" for t in _kb_texts(bot))
    assert any(cb.startswith("cla_fridge_") for cb in _kb_callbacks(bot))


@pytest.mark.unit
def test_select_all_marks_every_item_on_current_page():
    _seed(3)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "fridge"))
    cla_cb = next(cb for cb in _kb_callbacks(bot) if cb and cb.startswith("cla_fridge_"))
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))
    assert cleanup._sel(CID, "fridge") == {0, 1, 2}


@pytest.mark.unit
def test_select_all_button_becomes_clear_all_when_page_fully_selected():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "fridge"))
    cla_cb = next(cb for cb in _kb_callbacks(bot) if cb and cb.startswith("cla_fridge_"))
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))
    assert any(t == "✅ Снять выбор на странице" for t in _kb_texts(bot))


@pytest.mark.unit
def test_clear_all_unmarks_every_item_on_current_page():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "fridge"))
    cla_cb = next(cb for cb in _kb_callbacks(bot) if cb and cb.startswith("cla_fridge_"))
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))  # select all
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))  # clear all
    assert cleanup._sel(CID, "fridge") == set()


@pytest.mark.unit
def test_select_all_only_affects_current_page_not_whole_collection():
    """Явно проверяет, что это НЕ «выбрать все N элементов коллекции» (PR3) —
    только видимая страница."""
    _seed(cleanup.CLEAN_PAGE + 2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, "fridge"))
    cla_cb = next(cb for cb in _kb_callbacks(bot) if cb and cb.startswith("cla_fridge_"))
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))
    assert cleanup._sel(CID, "fridge") == set(range(cleanup.CLEAN_PAGE))
