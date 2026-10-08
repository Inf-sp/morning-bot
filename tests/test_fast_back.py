import asyncio
from types import SimpleNamespace

import bot_callbacks
import home_cache
import leisure_concerts


class Message:
    chat_id = "42"
    message_id = 7

    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def _click(monkeypatch, data, ready):
    started = []

    async def start_inline(*_args, **_kwargs):
        started.append(True)

        class Status:
            async def replace(self, text, **_kw):
                return True

            async def stop(self, delete=True):
                return None
        return Status()

    async def send_home(_bot, _cid, status=None):
        await status.replace("Надень сегодня")

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(home_cache, "is_ready", lambda _section, _cid: ready)
    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.wardrobe, "send_home", send_home)
    message = Message()
    query = SimpleNamespace(data=data, message=message)
    bot = Bot()
    asyncio.run(bot_callbacks.handle(
        SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot), None,
    ))
    return started, message, bot


def test_back_to_ready_section_edits_screen_without_waiting_indicator(monkeypatch):
    started, message, bot = _click(monkeypatch, "m_wardrobe", ready=True)

    assert started == []
    assert message.edits == ["Надень сегодня"] and bot.sent == []


def test_section_without_cache_still_shows_waiting_indicator(monkeypatch):
    started, _message, _bot = _click(monkeypatch, "m_wardrobe", ready=False)

    assert started == [True]


def test_concerts_open_in_place_from_cache(monkeypatch):
    shown = []

    async def show(_bot, _cid, message, *, reply_markup=None, query=None):
        shown.append((message.text, query))

    monkeypatch.setattr(leisure_concerts.rich_delivery, "show", show)
    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Romy"])
    monkeypatch.setattr(leisure_concerts.config, "TICKETMASTER_API_KEY", "key")
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_get", lambda *_args: [])
    query = object()

    asyncio.run(leisure_concerts.find_concerts(object(), "42", "home", q=query))

    assert len(shown) == 1 and shown[0][1] is query
