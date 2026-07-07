"""PR2 (P1-2): legacy callback'и стран/артистов/книг — redirect на канонический
маршрут `send_love_section`, а не собственный рендер. Новые карточки рекомендаций
должны сразу генерировать `as_love_*`, не `set_*`.

Правило совместимости (Раздел B плана): legacy callback не имеет своего рендерера,
только redirect — старые сообщения в чатах остаются рабочими бессрочно.
"""
import asyncio

import pytest

import config
import leisure
import routing
import settings
import store
import travel

CID = "settings-love-routes-cid"


class _FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kw):
        self.messages.append({"chat_id": chat_id, "text": text, **kw})


@pytest.fixture(autouse=True)
def _clean():
    keys = (config.ARTISTS_KEY, config.BOOKS_KEY, config.COUNTRIES_KEY, config.FAVCOUNTRIES_KEY)
    for key in keys:
        store._mem.pop(key, None)
    yield
    for key in keys:
        store._mem.pop(key, None)


def _last_texts(bot):
    return bot.messages[-1]["text"]


# ---------- новые точки входа больше не генерируют legacy callback ----------

@pytest.mark.unit
def test_travel_home_kb_uses_canonical_route():
    cb = [btn.callback_data for row in travel._home_kb().inline_keyboard for btn in row]
    assert "as_love_countries" in cb
    assert "set_countries" not in cb


@pytest.mark.unit
def test_travel_kb_uses_canonical_route():
    cb = [btn.callback_data for row in travel._travel_kb().inline_keyboard for btn in row]
    assert "as_love_countries" in cb
    assert "set_countries" not in cb


@pytest.mark.unit
def test_book_kb_uses_canonical_route():
    cb = [btn.callback_data for row in leisure._book_kb(0).inline_keyboard for btn in row]
    assert "as_love_books" in cb
    assert "set_books" not in cb


@pytest.mark.unit
def test_listen_kb_uses_canonical_route():
    cb = [btn.callback_data for row in leisure._listen_kb().inline_keyboard for btn in row]
    assert "as_love_artists" in cb
    assert "set_artists" not in cb


# ---------- legacy callback остаётся живым (redirect, не удалён) ----------

@pytest.mark.unit
def test_legacy_callbacks_still_resolve_via_routing():
    """Старые сообщения в чатах с этой кнопкой не должны сломаться."""
    for cb in ("set_countries", "set_artists", "set_books"):
        result = routing.resolve_callback_handler(cb)
        assert result["handled"] is True, f"{cb}: {result}"


@pytest.mark.unit
def test_set_artists_redirects_to_love_section_same_data():
    """Артисты: legacy и канонический путь читают один и тот же ARTISTS_KEY —
    redirect не должен менять видимые пользователю данные."""
    store.set_list(config.ARTISTS_KEY, CID, ["The xx", "Portishead"])
    bot = _FakeBot()
    asyncio.run(settings.handle_callback(bot, CID, "set_artists"))
    assert "The xx" in _last_texts(bot)
    assert "Portishead" in _last_texts(bot)


@pytest.mark.unit
def test_set_books_redirects_to_love_section_same_data():
    store.set_list(config.BOOKS_KEY, CID, ["Дюна"])
    bot = _FakeBot()
    asyncio.run(settings.handle_callback(bot, CID, "set_books"))
    assert "Дюна" in _last_texts(bot)


@pytest.mark.unit
def test_set_countries_redirects_to_favcountries_not_visited_countries():
    """Страны — намеренное расхождение (уже описанная в плане починка): legacy
    `send_countries` показывал COUNTRIES_KEY (посещённые), канонический
    `send_love_section` показывает FAVCOUNTRIES_KEY (реально любимые, отмеченные
    сердечком). После redirect пользователь видит ЛЮБИМЫЕ страны, а не посещённые
    — это исправление старой путаницы, не регрессия данных."""
    store.set_list(config.COUNTRIES_KEY, CID, [{"name": "Япония", "flag": "🇯🇵"}])
    store.set_list(config.FAVCOUNTRIES_KEY, CID, [{"name": "Норвегия", "flag": "🇳🇴"}])
    bot = _FakeBot()
    asyncio.run(settings.handle_callback(bot, CID, "set_countries"))
    assert "Норвегия" in _last_texts(bot)
    assert "Япония" not in _last_texts(bot)


@pytest.mark.unit
def test_legacy_renderers_not_called_by_new_route(monkeypatch):
    """Redirect не должен иметь собственного рендера — send_countries/send_artists/
    send_books (старые функции) не вызываются на пути set_*, только send_love_section."""
    calls = []
    monkeypatch.setattr(settings, "send_countries", lambda *a, **kw: calls.append("send_countries"))
    monkeypatch.setattr(settings, "send_artists", lambda *a, **kw: calls.append("send_artists"))
    monkeypatch.setattr(settings, "send_books", lambda *a, **kw: calls.append("send_books"))
    bot = _FakeBot()
    for cb in ("set_countries", "set_artists", "set_books"):
        asyncio.run(settings.handle_callback(bot, CID, cb))
    assert calls == [], f"legacy-рендеры не должны вызываться напрямую: {calls}"


@pytest.mark.unit
def test_legacy_callback_logs_usage(caplog):
    import logging
    bot = _FakeBot()
    with caplog.at_level(logging.INFO, logger="settings"):
        asyncio.run(settings.handle_callback(bot, CID, "set_countries"))
    assert any("legacy callback used" in r.message for r in caplog.records)
