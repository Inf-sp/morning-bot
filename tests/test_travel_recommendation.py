import asyncio
import os
from datetime import date

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import travel
import research
from telegram import MessageEntity
from ui import travel as travel_ui


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class FakeInlineStatus:
    def __init__(self):
        self.replaced = []
        self.stopped = []

    async def replace(self, text, **kwargs):
        self.replaced.append({"text": text, **kwargs})
        return True

    async def stop(self, delete=True):
        self.stopped.append(delete)


def test_travel_cache_week_starts_on_monday():
    assert travel._travel_week_start(date(2026, 8, 17)) == "2026-08-17"
    assert travel._travel_week_start(date(2026, 8, 23)) == "2026-08-17"
    assert travel._travel_week_start(date(2026, 8, 24)) == "2026-08-24"


def test_travel_plan_with_inline_status_keeps_country_photo(monkeypatch):
    photo = {"url": "https://example.test/iceland.jpg", "width": 1600, "height": 900}

    class PhotoBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.photos = []

        async def send_photo(self, **kwargs):
            self.photos.append(kwargs)

    async def plan_json(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(travel.store, "last_recipe", {
        "42": {"country": "Исландия", "flag": "🇮🇸", "photo": photo},
    })
    monkeypatch.setattr(travel.store, "suggested_countries", {"42": "Исландия"})
    monkeypatch.setattr(travel.store, "get_settings", lambda _cid: {"city": "Алкмар"})
    monkeypatch.setattr(travel.research, "country_facts", lambda _name: {"cc": "IS"})
    monkeypatch.setattr(travel.research, "country_travel_facts", lambda _name: {})
    monkeypatch.setattr(travel.research, "facts_block", lambda _facts: "")
    monkeypatch.setattr(travel, "_travel_interests", lambda _cid: [])
    monkeypatch.setattr(travel.ai, "allm_json", plan_json)
    monkeypatch.setattr(
        travel, "_plan_from_sources",
        lambda country, *_args: {"title": country, "flag": "🇮🇸", "about": "Вулканы и горячие источники.", "photo": photo},
    )

    bot = PhotoBot()
    status = FakeInlineStatus()
    asyncio.run(travel.send_plan(bot, "42", status=status))

    assert len(bot.photos) == 1
    assert bot.photos[0]["chat_id"] == "42"
    assert bot.photos[0]["photo"] == photo["url"]
    assert bot.photos[0]["caption"] == "🇮🇸 Исландия\n\nВулканы и горячие источники."
    assert status.replaced == []
    assert status.stopped == [False]


def test_travel_home_keeps_preferences_inside_suitcase():
    labels = [[button.text for button in row] for row in travel._home_kb().inline_keyboard]

    assert labels == [
        ["✨ Подобрать новое путешествие"],
        ["💡 Что интересного"],
        ["🎚️ Мой чемодан"],
        ["#️⃣ Главная"],
    ]


def test_today_trip_scope_is_limited_to_home_country_and_neighbours(monkeypatch):
    monkeypatch.setattr(travel.store, "get_settings", lambda _cid: {"cc": "NL"})
    monkeypatch.setattr(travel, "_country_label", lambda code: code)

    home, _label, allowed = travel._day_trip_scope("42")

    assert home == "NL"
    assert allowed == ("NL", "BE", "DE")


def test_today_trip_rejects_a_country_outside_the_local_scope(monkeypatch):
    monkeypatch.setattr(travel.store, "get_settings", lambda _cid: {"city": "Алкмар", "cc": "NL"})
    monkeypatch.setattr(travel.store, "_load", lambda _key: {})
    monkeypatch.setattr(travel, "_country_label", lambda code: {"NL": "Нидерланды", "BE": "Бельгия", "DE": "Германия"}[code])
    monkeypatch.setattr(
        travel.ai,
        "llm_json",
        lambda *_args, **_kwargs: {"country_code": "JP", "to": "Токио", "route": ["a", "b", "c"]},
    )

    idea = travel._generate_home_idea("42")

    assert idea["to"] == "Берген"
    assert idea["transport_title"] == "Нидерланды"


def test_today_trip_cache_is_invalidated_when_home_country_changes(monkeypatch):
    state = {
        "42": {
            "version": 2,
            "date": travel.datetime.now(travel.config.TZ).date().isoformat(),
            "city": "Алкмар",
            "cc": "NL",
            "idea": {"to": "Старый маршрут"},
        }
    }
    generated = {"to": "Новый маршрут"}
    monkeypatch.setattr(travel.store, "get_settings", lambda _cid: {"city": "Алкмар", "cc": "DE"})
    monkeypatch.setattr(travel.store, "_load", lambda _key: state)
    monkeypatch.setattr(travel, "_generate_home_idea", lambda _cid: generated)
    monkeypatch.setattr(travel.store, "mutate_kv", lambda _key, change: change(state))

    assert travel._home_idea("42") == generated
    assert state["42"]["cc"] == "DE"


def test_travel_home_shows_place_of_the_day_without_rebus():
    idea = {
        "emoji": "🚆", "transport_title": "Поезд", "intro": "Короткий маршрут.",
        "from": "Алкмар", "to": "Лейден", "route": [], "tip": "Проверь расписание.",
        "transport": "Из Алкмара: 1 ч", "cost": "От €20",
        "duration": "На поездку: 1 день", "why": "каналы и прогулка без спешки",
    }

    message = travel_ui.home_screen(
        idea, rebus={"emoji": "🌋 ♨️ ❄️", "answer": "Исландия"},
    )

    assert "Прогресс:" not in message.text
    assert "посещено" not in message.text
    assert message.text.startswith("🚆 Место дня · Поезд\nЛейден — короткий маршрут.")
    assert "Туристический ребус:" not in message.text
    assert "💡 Полезно: Проверь расписание." in message.text
    assert not any(entity.type == MessageEntity.SPOILER for entity in message.entities)


def test_travel_card_entities_stay_on_utf16_boundaries_without_separate_flag():
    message = travel_ui.travel_plan({"title": "🇯🇵 Япония", "about": "Горы и поезда."}, "Япония")
    encoded = message.text.encode("utf-16-le")

    for entity in message.entities:
        encoded[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")


def test_suitcase_keeps_only_saved_countries(monkeypatch):
    monkeypatch.setattr(travel, "_sorted_countries", lambda _cid: [])

    keyboard, _page, _pages = travel._countries_kb("42", 0)
    labels = [[button.text for button in row] for row in keyboard.inline_keyboard]

    assert "📝 Предпочтения" not in [label for row in labels for label in row]
    assert labels[-2] == ["🆕 Добавить страну"]
    assert labels[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_rejected_visited_country_changes_next_generation_request(monkeypatch):
    attempts = []

    def suggest(_cid, excluded=None):
        attempts.append(list(excluded or []))
        country = "Чили" if not excluded else "Япония"
        return {"country": country}

    selected = []

    async def send_plan(_bot, cid, *, status=None):
        selected.append(travel.store.last_recipe[str(cid)]["country"])

    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: ["CL"])
    monkeypatch.setattr(travel, "_country_name", lambda code: "Чили" if code == "CL" else code)
    monkeypatch.setattr(travel.recommendation_stoplist, "values", lambda *_args: [])
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


def test_other_trip_uses_local_country_after_repeated_visited_ai_answer(monkeypatch):
    selected = []

    def repeated_visited_country(*_args, **_kwargs):
        return {"country": "Чили"}

    async def send_plan(_bot, _cid, *, status=None):
        selected.append(travel.store.suggested_countries["42"])

    monkeypatch.setattr(travel, "travel_suggest_one", repeated_visited_country)
    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: ["CL"])
    monkeypatch.setattr(travel, "_country_name", lambda code: "Чили" if code == "CL" else code)
    monkeypatch.setattr(travel.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(travel, "_resolve_country_code", lambda _name: "CL")
    monkeypatch.setattr(travel, "_resolve_country_flag", lambda name, *_args: ("🇮🇸", {"cc": "IS"}))
    monkeypatch.setattr(travel, "_recommendation_photo", lambda *_args: None)
    monkeypatch.setattr(travel, "send_plan", send_plan)
    bot = FakeBot()

    asyncio.run(travel.send_go(bot, "42"))

    assert selected == ["Исландия"]
    assert bot.sent == []


def test_other_trip_keeps_finding_new_countries_after_the_initial_local_set_is_visited(monkeypatch):
    selected = []
    names = {"IS": "Исландия", "PT": "Португалия", "DK": "Дания", "JP": "Япония"}

    def repeated_visited_country(*_args, **_kwargs):
        return {"country": "Исландия"}

    async def send_plan(_bot, _cid, *, status=None):
        selected.append(travel.store.suggested_countries["42"])

    monkeypatch.setattr(travel, "travel_suggest_one", repeated_visited_country)
    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: list(names))
    monkeypatch.setattr(travel, "_country_name", lambda code: names[code])
    monkeypatch.setattr(travel.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(travel, "_resolve_country_code", lambda _name: "IS")
    monkeypatch.setattr(travel, "_resolve_country_flag", lambda name, *_args: ("🇨🇦", {"cc": "CA"}))
    monkeypatch.setattr(travel, "_recommendation_photo", lambda *_args: None)
    monkeypatch.setattr(travel, "send_plan", send_plan)
    bot = FakeBot()

    asyncio.run(travel.send_go(bot, "42"))

    assert selected == ["Канада"]
    assert bot.sent == []


def test_other_trip_uses_local_country_when_ai_is_unavailable(monkeypatch):
    selected = []

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("AI temporarily unavailable")

    async def send_plan(_bot, _cid, *, status=None):
        selected.append(travel.store.suggested_countries["42"])

    monkeypatch.setattr(travel, "travel_suggest_one", unavailable)
    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: [])
    monkeypatch.setattr(travel.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(travel, "_recommendation_photo", lambda *_args: None)
    monkeypatch.setattr(travel, "send_plan", send_plan)
    bot = FakeBot()

    asyncio.run(travel.send_go(bot, "42"))

    assert selected
    assert not bot.sent


def test_country_card_uses_local_facts_when_ai_is_unavailable(monkeypatch):
    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("AI temporarily unavailable")

    monkeypatch.setattr(travel.store, "last_recipe", {"42": {"country": "Португалия", "flag": "🇵🇹"}})
    monkeypatch.setattr(travel.store, "suggested_countries", {"42": "Португалия"})
    monkeypatch.setattr(travel.ai, "allm_json", unavailable)
    monkeypatch.setattr(travel, "_travel_interests", lambda _cid: [])
    status = FakeInlineStatus()
    bot = FakeBot()

    asyncio.run(travel.send_plan(bot, "42", status=status))

    assert len(status.replaced) == 1
    text = status.replaced[0]["text"]
    assert "Португалия" in text
    assert "✨ Тебе подойдёт" in text
    assert "📍 Не пропусти" in text
    assert "☀️ Когда ехать" in text
    assert "💶 Бюджет" in text
    assert "👩🏻‍🏫 Языки" in text
    assert "🏳️‍🌈 LGBTQ+" in text
    assert bot.sent == []


def test_local_recommendation_pool_has_complete_ready_made_cards():
    required = {"about", "fit", "spots", "best_time", "budget", "languages", "lgbt", "sources"}

    for country in travel._LOCAL_COUNTRY_FALLBACKS:
        profile = research.country_travel_facts(country)
        assert required <= profile.keys(), country
        assert len(profile["spots"]) == 3, country
        assert profile["sources"], country


def test_ready_made_cards_do_not_need_network(monkeypatch):
    monkeypatch.setattr(
        research.country_catalog.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )

    for country in travel._LOCAL_COUNTRY_FALLBACKS:
        assert research.country_travel_facts(country)["about"]


def test_saved_country_card_uses_inline_status_when_it_needs_building(monkeypatch):
    card = {
        "country_code": "NL", "country_name": "Нидерланды", "flag": "🇳🇱",
        "description": "Каналы, города и море.", "highlight": "велосипедные маршруты",
        "languages": ["нидерландский"], "currency": "евро · EUR",
        "main_nuance": "погода быстро меняется", "fact": "Амстердам известен каналами",
    }
    status = FakeInlineStatus()
    bot = FakeBot()

    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: ["NL"])
    monkeypatch.setattr(travel, "_build_country_card", lambda _code: card)

    asyncio.run(travel.send_country_card(bot, "42", "NL", status=status))

    assert bot.sent == []
    assert status.replaced[0]["text"].startswith("🇳🇱 Нидерланды")


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


def test_colombia_uses_real_flag_instead_of_model_text():
    plan = travel._plan_from_sources(
        "Колумбия",
        {"flag": "флаг Колумбии", "about": "Тропическая страна."},
        {"cc": ""}, {}, [], None,
    )

    assert plan["flag"] == "🇨🇴"
    assert travel_ui.travel_plan(plan, "Колумбия").text.startswith("🇨🇴 Колумбия")


def test_country_suggestion_prompt_does_not_make_transport_the_reason(monkeypatch):
    captured = {}

    def fake_llm(prompt, *_args, **_kwargs):
        captured["prompt"] = prompt
        return {"country": "Исландия"}

    monkeypatch.setattr(travel.ai, "llm_json", fake_llm)
    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: [])
    monkeypatch.setattr(travel.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(travel.memory, "get_preferences", lambda _cid: ["Люблю природу и походы"])

    travel.travel_suggest_one("42")

    assert "транспорт" not in captured["prompt"].casefold()
