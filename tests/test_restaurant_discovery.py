import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from telegram import MessageEntity

import restaurant_discovery
from ui.menu import restaurant_menu


def test_restaurant_card_has_google_link_and_compact_details():
    message = restaurant_menu({
        "city": "Alkmaar", "name": "De Eendracht",
        "map_url": "https://www.google.com/maps/search/?api=1&query=De+Eendracht%2C+Alkmaar",
        "cuisine": "нидерландская", "price": "€€", "signature_dish": "сате",
        "description": "Современное городское кафе в историческом центре.",
        "fact": "Ресторан находится в здании бывшей школы.",
    })

    assert message.text.startswith("🍽️ Что поесть · Alkmaar\n\nКуда сходить:\n• De Eendracht")
    assert "(нидерландская · €€ · сате)" in message.text
    assert "💡 Интересно: Ресторан находится в здании бывшей школы." in message.text
    links = [entity for entity in message.entities if entity.type == MessageEntity.TEXT_LINK]
    assert len(links) == 1
    assert links[0].url.startswith("https://www.google.com/maps/search/")


def test_restaurant_search_is_verified_cached_and_linked_to_google(monkeypatch):
    profile = {}
    rows = [{
        "title": "De Eendracht Alkmaar",
        "url": "https://example.com/de-eendracht",
        "content": "De Eendracht is a Dutch restaurant in Alkmaar known for satay.",
    }]
    calls = []
    monkeypatch.setattr(restaurant_discovery.store, "get_settings", lambda _cid: {"city": "Alkmaar"})
    monkeypatch.setattr(restaurant_discovery.store, "get_profile", lambda _cid: dict(profile))
    monkeypatch.setattr(
        restaurant_discovery.store, "mutate_profile",
        lambda _cid, change: profile.update(change(dict(profile))[0]),
    )
    monkeypatch.setattr(
        restaurant_discovery.research, "web_search",
        lambda *_args, **_kwargs: calls.append("search") or rows,
    )
    monkeypatch.setattr(restaurant_discovery.ai, "llm_json", lambda *_args, **_kwargs: {
        "name": "De Eendracht", "cuisine": "нидерландская", "price": "€€",
        "signature_dish": "сате", "description": "Городское кафе в центре.",
        "fact": "Ресторан работает в здании бывшей школы.",
        "source_url": "https://example.com/de-eendracht",
    })

    first = restaurant_discovery.get_restaurant("42")
    second = restaurant_discovery.get_restaurant("42")

    assert first == second
    assert calls == ["search"]
    assert first["map_url"].startswith("https://www.google.com/maps/search/")
