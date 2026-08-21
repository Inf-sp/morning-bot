import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import cache_refresh
import settings


def test_settings_refresh_starts_one_background_queue(monkeypatch):
    started = []
    edits = []

    monkeypatch.setattr(cache_refresh, "start", lambda bot, cid: started.append((bot, cid)) or True)

    class Message:
        async def edit_text(self, text, **kwargs):
            edits.append((text, kwargs))

    class Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("inline refresh must edit the settings message")

    query = type("Query", (), {"message": Message()})()
    bot = Bot()
    asyncio.run(settings.handle_callback(bot, "42", "set_refresh_data", query))

    assert started == [(bot, "42")]
    assert "в течение 5 минут" in edits[0][0]
    assert edits[0][1]["reply_markup"].inline_keyboard[0][0].callback_data == "set_home"


def test_cache_refresh_rejects_parallel_run(monkeypatch):
    async def pending(_bot, _cid):
        await asyncio.Event().wait()

    monkeypatch.setattr(cache_refresh, "_run", pending)

    async def scenario():
        assert cache_refresh.start(object(), "42") is True
        assert cache_refresh.start(object(), "42") is False
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert not cache_refresh.is_running("42")
