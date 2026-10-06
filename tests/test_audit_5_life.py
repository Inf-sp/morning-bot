import asyncio
import os
import threading

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import fridge
import myday
import recipe_generation
import weather
import weather_warn
import wardrobe_migration


class _Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)

    async def send_chat_action(self, **_kwargs):
        return None


def test_cold_warning_formats_negative_temperature():
    ctx = weather_warn.WarnContext(tmax=-3)

    events = [hazard.event(ctx) for hazard in weather_warn.evaluate(ctx)]

    assert "🥶 Мороз, днём около -3°C." in events


def test_stale_fridge_button_does_not_toggle_product_of_another_category(monkeypatch):
    state = [
        {"name": "курица", "cat": "мясо и рыба", "on": True},
        {"name": "яблоки", "cat": "овощи и фрукты", "on": True},
    ]
    monkeypatch.setattr(fridge.store, "get_list", lambda *_args: list(state))
    monkeypatch.setattr(fridge.store, "set_list", lambda _k, _c, value: state.__setitem__(slice(None), value))

    # Индекс 1 сейчас «яблоки», а кнопка пришла с экрана «Мясо и рыба» (cat 0).
    asyncio.run(fridge.fridge_toggle(_Bot(), "42", 1, 0, 0))
    assert all(item["on"] for item in state)

    asyncio.run(fridge.fridge_toggle(_Bot(), "42", 1, 1, 0))
    assert next(item for item in state if item["name"] == "яблоки")["on"] is False


def test_fridge_batch_without_ai_uses_only_fridge_products(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(recipe_generation.ai, "llm_json", unavailable)
    monkeypatch.setattr(recipe_generation, "_recipe_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(recipe_generation, "_cuisine_context", lambda _cid: "")

    items = recipe_generation._gen_leftovers_recipe_batch("рис, морковь, лук", cid=None)

    assert items
    assert "яйц" not in items[0]["ingredients"]
    assert "рис" in items[0]["ingredients"]


def test_myday_summary_survives_wardrobe_failure(monkeypatch):
    import wardrobe

    async def broken_looks(*_args, **_kwargs):
        raise RuntimeError("wardrobe down")

    monkeypatch.setattr(myday, "_load_day_cache", lambda *_args: None)
    monkeypatch.setattr(wardrobe, "get_cached_outfit_summary", lambda _cid: {"items": []})
    monkeypatch.setattr(wardrobe, "send_looks", broken_looks)
    monkeypatch.setattr(myday, "_build_day_text", lambda *_args, **_kwargs: ("Сводка", []))
    monkeypatch.setattr(myday, "_save_day_cache", lambda _cid, _today, text, entities, _ts: {
        "text": text, "entities": entities,
    })
    bot = _Bot()

    asyncio.run(myday.send_plany(bot, "42"))

    assert bot.messages[-1]["text"] == "Сводка"


def test_weather_screen_fetches_forecast_off_the_event_loop(monkeypatch):
    threads = []

    def fetch(*_args):
        threads.append(threading.current_thread())
        raise weather.WeatherDailyLimitExceeded("limit")

    monkeypatch.setattr(weather.store, "get_settings", lambda _cid: {"lat": 52.6, "lon": 4.7})
    monkeypatch.setattr(weather, "fetch_weather", fetch)
    bot = _Bot()

    asyncio.run(weather.send_weather(bot, "42", "today"))

    assert threads and threads[0] is not threading.main_thread()
    assert bot.messages[-1]["text"] == weather.WEATHER_LIMIT_FALLBACK


def test_migration_matches_ai_answer_by_item_id_after_concurrent_delete(monkeypatch):
    def item(item_id, name):
        return {"id": item_id, "name": name, "zone": "Верх", "subcategory": "Футболки"}

    snapshot = {"zones": {"Верх": {"Футболки": [item("a", "Белая футболка"), item("b", "Синяя футболка")]}}}
    current = {"zones": {"Верх": {"Футболки": [item("b", "Синяя футболка")]}}}

    async def answer(*_args, **_kwargs):
        return {"items": [
            {"i": 0, "clean_name": "Белая футболка", "warmth": "тёплые"},
            {"i": 1, "clean_name": "Синяя футболка", "warmth": "лёгкие"},
        ]}

    def mutate(_cid, mutator):
        mutator(current)
        return current

    monkeypatch.setattr(wardrobe_migration.ai, "allm_json", answer)
    monkeypatch.setattr(wardrobe_migration.store, "mutate_wardrobe", mutate)

    result = asyncio.run(wardrobe_migration.migrate_item_attrs("42", snapshot))

    remaining = result["zones"]["Верх"]["Футболки"][0]
    assert remaining["id"] == "b"
    assert remaining["warmth"] == "лёгкие"
