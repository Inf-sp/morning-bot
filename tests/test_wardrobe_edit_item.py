import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import store
import wardrobe
import wardrobe_management
import wardrobe_router
from fakes import RecordingBot


def test_edit_button_asks_new_description_and_replaces_item(monkeypatch):
    data = {"zones": {"Верх": {"Футболки": [{"id": "t1", "name": "Синяя футболка", "color": "синий",
                                               "zone": "Верх", "subcategory": "Футболки"}]}}}
    monkeypatch.setattr(store, "load_wardrobe", lambda _cid: data)
    monkeypatch.setattr(store, "mutate_wardrobe", lambda _cid, fn: fn(data))
    sent = []
    bot = RecordingBot(sent)

    asyncio.run(wardrobe_router.handle_callback(bot, "42", None, "w_edit_t1"))

    assert store.pending_input["42"] == "wardrobe_edit" and store.wardrobe_edit_item["42"] == "t1"
    assert sent[-1]["text"].startswith("✏️ Изменить вещь\n\nСейчас: Синяя футболка\nЦвет: синий")

    async def parse(_text):
        return [{"name": "Голубая футболка", "color": "голубой", "zone": "Верх", "subcategory": "Футболки"}]

    monkeypatch.setattr(wardrobe_management, "_parse_items", parse)
    shown = []

    async def card(_bot, _cid, item_id, q=None):
        shown.append(item_id)

    monkeypatch.setattr(wardrobe_management._wardrobe, "send_item_card", card)
    asyncio.run(wardrobe_management.edit_item_text(bot, "42", "голубая футболка"))

    item = data["zones"]["Верх"]["Футболки"][0]
    assert (item["id"], item["name"], item["color"]) == ("t1", "Голубая футболка", "голубой") and shown == ["t1"]
    store.pending_input.pop("42", None)
