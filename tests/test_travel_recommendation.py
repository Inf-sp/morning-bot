import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import travel
import research
from ui import travel as travel_ui


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


def test_iceland_card_uses_verified_travel_fields_not_model_claims():
    facts = research.country_facts("Исландия")
    travel_facts = research.country_travel_facts("Исландия")
    plan = travel._plan_from_sources(
        "Исландия",
        {
            "about": "Уникальное сочетание природы.",
            "fit": "Можно путешествовать самолётом и велосипедом.",
            "spots": ["Случайное место"],
            "best_time": "всегда",
            "budget_level": "low",
            "budget_reason": "дёшево",
            "languages": ["русский"],
            "lgbt": "высокий риск — выдуманное утверждение",
        },
        facts, travel_facts, ["природа", "походы"], None,
    )

    assert plan["about"] == "Вулканы, ледники, горячие источники и дороги через почти незаселённые пейзажи."
    assert plan["fit"] == "если хочется поездки с природой и походами"
    assert plan["spots"] == [
        "Золотое кольцо — Гюдльфосс, Гейсир и Тингведлир",
        "Южное побережье и ледниковую лагуну Йёкюльсаурлоун",
        "Рейкьявик и геотермальные бассейны",
    ]
    assert plan["best_time"].startswith("июнь–август —")
    assert plan["budget"] == "высокий — особенно жильё, рестораны и транспорт"
    assert plan["languages"] == ["исландский", "английский"]
    assert plan["lgbt"].startswith("очень комфортно —")

    text = travel_ui.travel_plan(plan, "Исландия").text
    assert "📍 Не пропусти" in text
    assert "👩🏻‍🏫 Языки: исландский · английский" in text
    assert "Самолётом" not in text


def test_unverified_lgbt_model_text_is_not_shown_as_a_fact():
    plan = travel._plan_from_sources(
        "Тестовая страна", {"lgbt": "очень комфортно — модель так сказала"},
        {"cc": "ZZ", "languages": ["English"]}, {}, [], None,
    )

    assert plan["lgbt"] == "нужна осторожность — в карточке нет свежих проверенных данных"


def test_country_suggestion_prompt_does_not_make_transport_the_reason(monkeypatch):
    captured = {}

    def fake_llm(prompt, *_args, **_kwargs):
        captured["prompt"] = prompt
        return {"country": "Исландия"}

    monkeypatch.setattr(travel.ai, "llm_json", fake_llm)
    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: [])
    monkeypatch.setattr(travel.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(travel, "_plan_countries", lambda _cid: [])
    monkeypatch.setattr(travel.memory, "get_preferences", lambda _cid: ["Люблю природу и походы"])

    travel.travel_suggest_one("42")

    assert "Предпочтительный транспорт" not in captured["prompt"]
    assert "самолёта" in captured["prompt"]
