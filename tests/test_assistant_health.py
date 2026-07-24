import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import sys
import types

import assistant
from ui import doctor as doctor_ui
from ui import menu as menu_ui
from ui import medicine as medicine_ui


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


def test_health_menu_has_one_doctor_entry_for_symptoms_and_medicines():
    menu = menu_ui.menu_screen("m_balance")
    labels = [button.text for row in menu.reply_markup.inline_keyboard for button in row]

    assert "👩🏻‍⚕️ Врач" in labels
    assert "💊 Лекарства" not in labels
    assert "лекарств" in doctor_ui.prompt_screen().text.casefold()
    assert medicine_ui.medicine_card({"drug_name": "Ибупрофен"}).text.startswith("👩🏻‍⚕️ Врач · Ибупрофен")
