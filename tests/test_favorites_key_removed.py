"""P1-3: FAVORITES_KEY удалён без миграции — на проде исторических данных под
`favorites.json` не найдено (владелец бота проверил все страницы таблицы `kv`),
поэтому шаг 2 плана (docs/cleanup.md) — прямое удаление константы,
`send_fav`/`add_fav` и связанных веток роутинга — выполнен без импорта.

Этот файл фиксирует два факта:
1. Мёртвый путь `FAVORITES_KEY`/`send_fav`/`add_fav`/`a_fav` полностью убран из
   кода, а не просто стал недостижимым (это отличает результат от orphan-таблицы
   PR1 — там код остаётся, здесь его больше нет).
2. Реальный механизм «Любимое» (`send_love_section`/`as_love_*`, работающий на
   WATCHLIST_KEY/BOOKS_KEY/ARTISTS_KEY/FAVCOUNTRIES_KEY) корректно ведёт себя на
   пустом состоянии — удаление FAVORITES_KEY не задело его.
"""
import asyncio

import pytest

import config
import leisure
import routing
import settings
import store

CID = "favorites-key-removed-cid"


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


@pytest.fixture(autouse=True)
def _clean():
    keys = (config.WATCHLIST_KEY, config.BOOKS_KEY, config.ARTISTS_KEY, config.FAVCOUNTRIES_KEY)
    for key in keys:
        store._mem.pop(key, None)
    yield
    for key in keys:
        store._mem.pop(key, None)


# ---------- FAVORITES_KEY константа и код полностью убраны ----------

@pytest.mark.unit
def test_favorites_key_constant_removed():
    assert not hasattr(config, "FAVORITES_KEY")


@pytest.mark.unit
def test_send_fav_and_add_fav_removed_from_leisure():
    assert not hasattr(leisure, "send_fav")
    assert not hasattr(leisure, "add_fav")


@pytest.mark.unit
def test_a_fav_callback_no_longer_routed():
    """Раньше это был orphan (handler есть, кнопки нет) — теперь весь путь
    убран, callback не резолвится вообще ни на одном уровне."""
    result = routing.resolve_callback_handler("a_fav")
    assert result == {
        "handled": False,
        "module": None,
        "detail": "no matching branch in bot.py answer_callback",
    }


@pytest.mark.unit
def test_favorites_key_not_in_per_user_keys():
    assert not any(k == "favorites.json" for k in store._PER_USER_KEYS)


# ---------- пустое состояние: реальное «Любимое» не пострадало ----------

@pytest.mark.unit
def test_love_section_empty_state_for_each_category():
    """send_love_section на пустых данных для всех 4 категорий работает и не
    падает — независимая проверка, что удаление FAVORITES_KEY не задело
    реальный механизм «Любимое»."""
    bot = _FakeBot()
    for key in ("movies", "countries", "artists", "books"):
        asyncio.run(settings.send_love_section(bot, CID, key))
    assert len(bot.messages) == 4
    for msg in bot.messages:
        assert "пусто" in msg["text"].lower()


@pytest.mark.unit
def test_love_section_add_button_present_on_empty_state():
    bot = _FakeBot()
    asyncio.run(settings.send_love_section(bot, CID, "movies"))
    rows = bot.messages[-1]["reply_markup"].inline_keyboard
    callbacks = [btn.callback_data for row in rows for btn in row]
    assert "as_loveadd_movies" in callbacks
