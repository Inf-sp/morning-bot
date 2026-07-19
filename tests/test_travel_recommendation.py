import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import travel


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def test_rejected_visited_country_changes_next_generation_request(monkeypatch):
    attempts = []

    def suggest(_cid, excluded=None):
        attempts.append(list(excluded or []))
        country = "Чили" if not excluded else "Япония"
        return {"country": country}

    selected = []

    async def send_plan(_bot, cid):
        selected.append(travel.store.last_recipe[str(cid)]["country"])

    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: ["CL"])
    monkeypatch.setattr(travel, "_country_name", lambda code: "Чили" if code == "CL" else code)
    monkeypatch.setattr(travel.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(travel, "_plan_countries", lambda _cid: [])
    monkeypatch.setattr(travel, "travel_suggest_one", suggest)
    monkeypatch.setattr(travel, "_resolve_country_code", lambda name: {"Чили": "CL", "Япония": "JP"}[name])
    monkeypatch.setattr(travel, "_resolve_country_flag", lambda name, *_args: ("🇯🇵", {"cc": "JP"}))
    monkeypatch.setattr(travel, "_recommendation_photo", lambda *_args: None)
    monkeypatch.setattr(travel, "send_plan", send_plan)
    bot = FakeBot()

    asyncio.run(travel.send_go(bot, "42"))

    assert attempts == [[], ["Чили"]]
    assert selected == ["Япония"]
    assert bot.sent == []
