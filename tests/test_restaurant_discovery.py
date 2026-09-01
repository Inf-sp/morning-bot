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
        "address": "Hekelstraat 30", "format": "restaurant · café",
        "map_url": "https://www.google.com/maps/search/?api=1&query=De+Eendracht%2C+Alkmaar",
        "cuisine": "нидерландская", "price": "€€", "signature_dish": "сате",
        "opening_hours": "с 09:00 до 00:00", "dish_emoji": "🍔", "dish_price": "€22,50",
        "description": "Современное городское кафе в историческом центре.",
        "fact": "Ресторан находится в здании бывшей школы.",
    })

    assert message.text.startswith("🍽️ Куда сходить · Alkmaar\n\nDe Eendracht")
    assert "(Hekelstraat 30 · restaurant · café · нидерландская · €€)" in message.text
    assert "Что взять: 🍔 сате · €22,50" in message.text
    assert "💡 Интересно: Ресторан находится в здании бывшей школы." in message.text
    links = [entity for entity in message.entities if entity.type == MessageEntity.TEXT_LINK]
    assert len(links) == 1
    assert links[0].url.startswith("https://www.google.com/maps/search/")


def test_myday_restaurant_summary_reads_only_ready_cached_card(monkeypatch):
    card = {
        "city": "Alkmaar", "name": "Roest Alkmaar",
        "cuisine": "современная европейская", "price": "€€",
        "map_url": "https://maps.example/roest",
        "cached_at": datetime.now(restaurant_discovery.config.TZ).isoformat(),
    }
    monkeypatch.setattr(
        restaurant_discovery.store, "get_settings", lambda _cid: {"city": "Alkmaar"},
    )
    monkeypatch.setattr(
        restaurant_discovery.store, "get_profile",
        lambda _cid: {"food_restaurant_recommendation": card},
    )

    assert restaurant_discovery.cached_restaurant_summary("42") == (
        "Roest Alkmaar · современная европейская · €€"
    )


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
        "content": (
            "De Eendracht is a Dutch restaurant in central Alkmaar known for satay. "
            "The restaurant occupies a former school and has a moderate €€ price range."
        ),
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
        "evidence": {
            "name": {"source_id": "source:0", "quote": "De Eendracht"},
            "cuisine": {"source_id": "source:0", "quote": "Dutch restaurant"},
            "price": {"source_id": "source:0", "quote": "€€ price range"},
            "signature_dish": {"source_id": "source:0", "quote": "known for satay"},
            "description": {"source_id": "source:0", "quote": "central Alkmaar"},
            "fact": {"source_id": "source:0", "quote": "occupies a former school"},
        },
    })

    first = restaurant_discovery.get_restaurant("42")
    second = restaurant_discovery.get_restaurant("42")

    assert first == second
    assert calls == ["search"]
    assert first["map_url"].startswith("https://www.google.com/maps/search/")


def test_restaurant_recommendation_rotates_on_the_next_day(monkeypatch):
    profile = {}
    current = {"now": datetime(2026, 8, 26, 12, tzinfo=restaurant_discovery.config.TZ)}
    names = iter(("De Eendracht", "Soepp"))
    searches = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current["now"]

    monkeypatch.setattr(restaurant_discovery, "datetime", Clock)
    monkeypatch.setattr(restaurant_discovery.store, "get_settings", lambda _cid: {"city": "Alkmaar"})
    monkeypatch.setattr(restaurant_discovery.store, "get_profile", lambda _cid: dict(profile))
    monkeypatch.setattr(
        restaurant_discovery.store, "mutate_profile",
        lambda _cid, change: profile.update(change(dict(profile))[0]),
    )
    monkeypatch.setattr(
        restaurant_discovery.research, "web_search",
        lambda *_args, **_kwargs: searches.append(current["now"].date()) or [{
            "title": "De Eendracht and Soepp Alkmaar",
            "url": "https://example.com/restaurant",
            "content": (
                "De Eendracht and Soepp are verified Dutch restaurants in central Alkmaar. "
                "Both serve a documented daily dish in the €€ price range."
            ),
        }],
    )

    def recommendation(*_args, **_kwargs):
        name = next(names)
        return {
            "name": name, "cuisine": "нидерландская", "price": "€€",
            "signature_dish": "блюдо дня", "description": "Городское кафе.",
            "fact": "Находится в центре.",
            "evidence": {
                "name": {"source_id": "source:0", "quote": name},
                "cuisine": {"source_id": "source:0", "quote": "Dutch restaurants"},
                "price": {"source_id": "source:0", "quote": "€€ price range"},
                "signature_dish": {"source_id": "source:0", "quote": "daily dish"},
                "description": {"source_id": "source:0", "quote": "restaurants in central Alkmaar"},
                "fact": {"source_id": "source:0", "quote": "central Alkmaar"},
            },
        }

    monkeypatch.setattr(restaurant_discovery.ai, "llm_json", recommendation)

    first = restaurant_discovery.get_restaurant("42")
    current["now"] = datetime(2026, 8, 27, 12, tzinfo=restaurant_discovery.config.TZ)
    second = restaurant_discovery.get_restaurant("42")

    assert first["name"] == "De Eendracht"
    assert second["name"] == "Soepp"
    assert len(searches) == 2


def test_restaurant_rejects_yesterdays_name_even_if_ai_repeats_it(monkeypatch):
    cached = restaurant_discovery._fallback_card("Alkmaar")
    cached["cached_at"] = "2026-08-26T12:00:00+02:00"

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 27, 12, tzinfo=restaurant_discovery.config.TZ)

    monkeypatch.setattr(restaurant_discovery, "datetime", Clock)
    monkeypatch.setattr(restaurant_discovery.store, "get_settings", lambda _cid: {"city": "Alkmaar"})
    monkeypatch.setattr(restaurant_discovery.store, "get_profile", lambda _cid: {
        "food_restaurant_recommendation": cached,
    })
    monkeypatch.setattr(restaurant_discovery.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(restaurant_discovery.research, "web_search", lambda *_args, **_kwargs: [{
        "title": cached["name"], "url": cached["source_url"],
        "content": f"{cached['name']} is a Dutch restaurant with a €€ price range and burger menu.",
    }])
    monkeypatch.setattr(restaurant_discovery.ai, "llm_json", lambda *_args, **_kwargs: {
        "name": cached["name"], "cuisine": "нидерландская", "price": "€€",
        "signature_dish": "бургер", "description": "Городской ресторан.",
        "fact": "Работает весь день.", "evidence": {},
    })

    card = restaurant_discovery.get_restaurant("42")

    assert card["name"] != cached["name"]


def test_restaurant_is_stable_when_context_changes_during_same_day(monkeypatch):
    profile = {}
    current = {"now": datetime(2026, 8, 27, 9, tzinfo=restaurant_discovery.config.TZ)}
    calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current["now"]

    monkeypatch.setattr(restaurant_discovery, "datetime", Clock)
    monkeypatch.setattr(restaurant_discovery, "_good_terrace_weather", lambda _settings: False)
    monkeypatch.setattr(restaurant_discovery.store, "get_settings", lambda _cid: {"city": "Alkmaar"})
    monkeypatch.setattr(restaurant_discovery.store, "get_profile", lambda _cid: dict(profile))
    monkeypatch.setattr(
        restaurant_discovery.store, "mutate_profile",
        lambda _cid, change: profile.update(change(dict(profile))[0]),
    )
    monkeypatch.setattr(
        restaurant_discovery, "_fallback_card",
        lambda city, previous="", context_key="", history=None: {
            "city": city, "name": "Stable", "description": "Description",
            "map_url": "https://maps.example/stable", "context_key": context_key,
            "cached_at": Clock.now().isoformat(),
        },
    )
    monkeypatch.setattr(
        restaurant_discovery.research, "web_search",
        lambda *_args, **_kwargs: calls.append(current["now"].hour) or [],
    )

    first = restaurant_discovery.get_restaurant("42")
    current["now"] = datetime(2026, 8, 27, 19, tzinfo=restaurant_discovery.config.TZ)
    second = restaurant_discovery.get_restaurant("42")

    assert first == second
    assert calls == [9]


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
def test_city_fallback_rotates_through_every_place_before_repeating():
    first = restaurant_discovery._fallback_card("Alkmaar")
    second = restaurant_discovery._fallback_card(
        "Alkmaar", history=[first["name"]],
    )
    third = restaurant_discovery._fallback_card(
        "Alkmaar", history=[first["name"], second["name"]],
    )

    assert len({first["name"], second["name"], third["name"]}) == 3


def test_restaurant_search_excludes_full_recent_history_from_search_and_ai_cache(monkeypatch):
    cached = restaurant_discovery._fallback_card("Alkmaar")
    cached["history"] = ["Roest Alkmaar", "MADA", cached["name"]]
    queries = []
    cache_contexts = []

    monkeypatch.setattr(
        restaurant_discovery.store, "get_settings", lambda _cid: {"city": "Alkmaar"},
    )
    monkeypatch.setattr(
        restaurant_discovery.store, "get_profile",
        lambda _cid: {"food_restaurant_recommendation": cached},
    )
    monkeypatch.setattr(restaurant_discovery.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(
        restaurant_discovery.research, "web_search",
        lambda query, **_kwargs: queries.append(query) or [{
            "title": "Another restaurant Alkmaar",
            "url": "https://example.com/another",
            "content": "Another restaurant in Alkmaar with a documented menu and prices.",
        }],
    )

    def llm(*_args, **kwargs):
        cache_contexts.append(kwargs["cache_context"])
        raise RuntimeError("use reserve")

    monkeypatch.setattr(restaurant_discovery.ai, "llm_json", llm)

    restaurant_discovery.get_restaurant("42", refresh=True)

    assert all(f'-"{name}"' in queries[0] for name in cached["history"])
    assert cache_contexts[0]["history"] == [
        "roest alkmaar", "mada", cached["name"].casefold(),
    ]
