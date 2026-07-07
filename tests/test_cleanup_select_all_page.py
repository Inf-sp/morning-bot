"""cleanup.py: кнопка «Выбрать все / Снять выбор на странице» (PR1, P1-1).

Намеренно ограничено объёмом PR1 — «выбрать все N элементов коллекции целиком»
(вторая, более рискованная кнопка) переносится в PR3 вместе со view_id/revision,
см. docs/cleanup.md.

Тестируется на контексте cfg_countries — единственном контексте cleanup.py,
который намеренно НЕ мигрирует на view-режим ни в одном из PR3a-d (legacy
compatibility-слой, ждёт отдельного решения P1-2) и поэтому остаётся стабильной
опорой для проверки именно старого позиционного формата (handle_cleanup/_sel).
"""
import asyncio

import pytest

import cleanup
import config
import store

CID = "cleanup-select-all-cid"
CTX = "cfg_countries"


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


@pytest.fixture(autouse=True)
def _clean():
    store._mem.pop(config.COUNTRIES_KEY, None)
    store.list_sel.pop(f"{CID}:{CTX}", None)
    yield
    store._mem.pop(config.COUNTRIES_KEY, None)
    store.list_sel.pop(f"{CID}:{CTX}", None)


def _seed(n):
    store.set_list(config.COUNTRIES_KEY, CID, [f"продукт {i}" for i in range(n)])


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
    asyncio.run(cleanup.open_cleanup(bot, CID, CTX))
    assert not any("страниц" in t for t in _kb_texts(bot))


@pytest.mark.unit
def test_select_all_button_appears_with_two_or_more_items():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, CTX))
    assert any(t == "✅ Выбрать все на странице" for t in _kb_texts(bot))
    assert any(cb.startswith(f"cla_{CTX}_") for cb in _kb_callbacks(bot))


@pytest.mark.unit
def test_select_all_marks_every_item_on_current_page():
    _seed(3)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, CTX))
    cla_cb = next(cb for cb in _kb_callbacks(bot) if cb and cb.startswith(f"cla_{CTX}_"))
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))
    assert cleanup._sel(CID, CTX) == {0, 1, 2}


@pytest.mark.unit
def test_select_all_button_becomes_clear_all_when_page_fully_selected():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, CTX))
    cla_cb = next(cb for cb in _kb_callbacks(bot) if cb and cb.startswith(f"cla_{CTX}_"))
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))
    assert any(t == "✅ Снять выбор на странице" for t in _kb_texts(bot))


@pytest.mark.unit
def test_clear_all_unmarks_every_item_on_current_page():
    _seed(2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, CTX))
    cla_cb = next(cb for cb in _kb_callbacks(bot) if cb and cb.startswith(f"cla_{CTX}_"))
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))  # select all
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))  # clear all
    assert cleanup._sel(CID, CTX) == set()


@pytest.mark.unit
def test_select_all_only_affects_current_page_not_whole_collection():
    """Явно проверяет, что это НЕ «выбрать все N элементов коллекции» (PR3) —
    только видимая страница."""
    _seed(cleanup.CLEAN_PAGE + 2)
    bot = _FakeBot()
    asyncio.run(cleanup.open_cleanup(bot, CID, CTX))
    cla_cb = next(cb for cb in _kb_callbacks(bot) if cb and cb.startswith(f"cla_{CTX}_"))
    asyncio.run(cleanup.handle_cleanup(bot, CID, cla_cb))
    assert cleanup._sel(CID, CTX) == set(range(cleanup.CLEAN_PAGE))
