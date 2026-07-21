import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import sys
import types

import assistant


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def test_medical_chat_routes_to_doctor_prompt(monkeypatch):
    calls = []

    async def fake_answer(bot, cid, text):
        calls.append((bot, cid, text))

    monkeypatch.setattr(assistant.store, "last_action", {})
    monkeypatch.setattr(assistant.store, "last_source", {})
    monkeypatch.setattr(assistant.store, "chat_history", {})
    monkeypatch.setattr(assistant.store, "last_surface", {})
    fake_module = types.SimpleNamespace(answer=fake_answer)
    monkeypatch.setitem(sys.modules, "doctor", fake_module)

    import asyncio

    bot = _Bot()
    asyncio.run(assistant.chat_reply(bot, "42", "У меня температура 38 и кашель"))

    assert calls == [(bot, "42", "У меня температура 38 и кашель")]
