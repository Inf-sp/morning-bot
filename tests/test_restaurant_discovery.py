import os
import asyncio
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from telegram import MessageEntity

import restaurant_discovery
import menu
from ui.menu import restaurant_menu


def test_restaurant_card_has_google_link_and_compact_details():
    message = restaurant_menu({
        "city": "Alkmaar", "name": "De Eendracht",
        "map_url": "https://www.google.com/maps/search/?api=1&query=De+Eendracht%2C+Alkmaar",
        "cuisine": "нидерландская", "price": "€€", "signature_dish": "сате",
        "opening_hours": "с 09:00 до 00:00", "dish_emoji": "🍔", "dish_price": "€22,50",
        "description": "Современное городское кафе в историческом центре.",
        "fact": "Ресторан находится в здании бывшей школы.",
    })

    assert message.text.startswith("🍽️ Куда сходить · Alkmaar\n\nСегодня: De Eendracht")
    assert "- Цена €€\n- нидерландская\n- Открыто с 09:00 до 00:00." in message.text
    assert "Что взять: 🍔 сате · €22,50" in message.text
    assert "Интересно: Ресторан находится в здании бывшей школы." in message.text
    links = [entity for entity in message.entities if entity.type == MessageEntity.TEXT_LINK]
    assert len(links) == 1
    assert links[0].url.startswith("https://www.google.com/maps/search/")


def test_restaurant_screen_always_disables_link_preview(monkeypatch):
    replaced = {}

    class Status:
        async def replace(self, text, **kwargs):
            replaced.update(kwargs)

    monkeypatch.setattr(
        restaurant_discovery, "get_restaurant",
        lambda *_args, **_kwargs: {
            "city": "Alkmaar", "name": "De Eendracht",
            "map_url": "https://www.google.com/maps/search/?api=1&query=De+Eendracht",
            "cuisine": "нидерландская", "price": "€€", "signature_dish": "сате",
            "description": "Городское кафе.", "fact": "Находится в центре.",
        },
    )

    asyncio.run(menu.send_food_menu(object(), "42", status=Status()))

    assert replaced["disable_web_page_preview"] is True


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


def test_restaurant_context_changes_with_time_and_good_weather(monkeypatch):
    monkeypatch.setattr(restaurant_discovery, "_good_terrace_weather", lambda _settings: True)

    morning = restaurant_discovery._context({}, datetime(2026, 8, 26, 9, 0, tzinfo=restaurant_discovery.config.TZ))
    lunch = restaurant_discovery._context({}, datetime(2026, 8, 26, 13, 0, tzinfo=restaurant_discovery.config.TZ))
    weekend = restaurant_discovery._context({}, datetime(2026, 8, 28, 19, 0, tzinfo=restaurant_discovery.config.TZ))

    assert morning == ("morning_terrace", "coffee and breakfast with a terrace")
    assert lunch == ("lunch_terrace", "lunch with a terrace")
    assert weekend == ("weekend_dinner_terrace", "Friday or Saturday dinner with a terrace")


def test_alkmaar_keeps_a_useful_restaurant_when_live_search_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        restaurant_discovery.store, "get_settings", lambda _cid: {"city": "Alkmaar"},
    )
    monkeypatch.setattr(restaurant_discovery.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(restaurant_discovery.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(restaurant_discovery.research, "web_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        restaurant_discovery.ai, "llm_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("AI unavailable")),
    )

    card = restaurant_discovery.get_restaurant("42")

    assert card["city"] == "Alkmaar"
    assert card["name"]
    assert card["map_url"].startswith("https://www.google.com/maps/search/")
    assert card["description"]


def test_other_place_rotates_through_alkmaar_reserve(monkeypatch):
    cached = restaurant_discovery._fallback_card("Alkmaar")
    monkeypatch.setattr(
        restaurant_discovery.store, "get_settings", lambda _cid: {"city": "Alkmaar"},
    )
    monkeypatch.setattr(restaurant_discovery.store, "get_profile", lambda _cid: {
        "food_restaurant_recommendation": cached,
    })
    monkeypatch.setattr(restaurant_discovery.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(restaurant_discovery.research, "web_search", lambda *_args, **_kwargs: [])

    card = restaurant_discovery.get_restaurant("42", refresh=True)

    assert card["name"] != cached["name"]
    assert card["map_url"].startswith("https://www.google.com/maps/search/")
