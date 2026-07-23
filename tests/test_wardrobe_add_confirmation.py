import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe
from ui import wardrobe as wardrobe_ui


class _Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


def test_success_confirmation_uses_brand_and_compact_known_details():
    message = wardrobe_ui.add_success({
        "name": "светло-серая рубашка",
        "brand": "GU",
        "zone": "Верх",
        "color": "светло-серый",
        "length": "короткий рукав",
        "warmth": "лёгкие",
    })

    assert message.text == (
        "✅ Вещь добавлена в «Мой шкаф»\n\n"
        "Светло-серая рубашка GU · короткий рукав · лёгкая ткань"
    )
    assert "Светло-серая рубашка GU" in [
        message.text.encode("utf-16-le")[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")
        for entity in message.entities
    ]


def test_success_confirmation_without_brand_omits_empty_and_technical_fields():
    message = wardrobe_ui.add_success({
        "name": "чёрные широкие брюки",
        "brand": "",
        "zone": "Низ",
        "color": "чёрный",
        "warmth": "лёгкие",
        "material": None,
        "length": "",
        "fit": None,
    })

    assert message.text == "✅ Вещь добавлена в «Мой шкаф»\n\nЧёрные широкие брюки · лёгкие"
    assert all(label not in message.text for label in ("Категория:", "Цвет:", "Тепло:"))
    assert "None" not in message.text


def test_success_confirmation_is_sent_only_after_store_returns_saved_item(monkeypatch):
    item = {"id": "shirt-1", "name": "Белая футболка", "zone": "Верх", "warmth": "обычные"}

    async def parse(_text):
        return [item]

    monkeypatch.setattr(wardrobe, "_parse_items", parse)
    monkeypatch.setattr(wardrobe.store, "add_wardrobe_items", lambda _cid, _items: [item])
    bot = _Bot()

    asyncio.run(wardrobe.add_item(bot, "wardrobe-save", "Белая футболка"))

    assert bot.messages[0]["text"].startswith("✅ Вещь добавлена в «Мой шкаф»")


def test_no_success_confirmation_when_store_did_not_save_item(monkeypatch):
    item = {"name": "Белая футболка", "zone": "Верх", "warmth": "обычные"}

    async def parse(_text):
        return [item]

    monkeypatch.setattr(wardrobe, "_parse_items", parse)
    monkeypatch.setattr(wardrobe.store, "add_wardrobe_items", lambda _cid, _items: [])
    bot = _Bot()

    asyncio.run(wardrobe.add_item(bot, "wardrobe-duplicate", "Белая футболка"))

    assert bot.messages[0]["text"] == "Такая вещь уже есть в шкафу."
    assert "✅ Вещь добавлена" not in bot.messages[0]["text"]
