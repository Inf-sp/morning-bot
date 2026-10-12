import asyncio
from datetime import date

import day_menu
import settings
import store
import vacation

REAL_TRIP_HOLIDAYS = vacation.trip_holidays
from ui import myday as myday_ui
from ui import vacation as vacation_ui

HOME = {"lat": 52.63, "lon": 4.74, "city": "Алкмар", "country": "Нидерланды", "cc": "NL"}
CAPE = {"lat": -33.9, "lon": 18.4, "city": "Кейптаун", "country": "ЮАР", "cc": "za"}


class Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


def _env(monkeypatch, place=None):
    saved, places = {}, {"now": dict(place or HOME)}
    monkeypatch.setattr(settings, "get", lambda _cid, key, default=None: saved.get(key, default))
    monkeypatch.setattr(settings, "set_", lambda _cid, key, value: saved.__setitem__(key, value))
    monkeypatch.setattr(store, "get_settings", lambda _cid: dict(places["now"]))
    monkeypatch.setattr(store, "set_settings", lambda _cid, lat, lon, city, country="", cc="": places.__setitem__(
        "now", {"lat": lat, "lon": lon, "city": city, "country": country, "cc": cc}))
    monkeypatch.setattr(store, "mutate_profile", lambda *_a, **_k: None)
    monkeypatch.setattr(vacation, "_reset_day_caches", lambda _cid: None)
    monkeypatch.setattr(vacation, "country_facts", lambda _cid: {})  # без сети
    monkeypatch.setattr(vacation, "trip_holidays", lambda *_a: [])
    return saved, places


def test_vacation_city_then_days_then_return_home(monkeypatch):
    saved, places = _env(monkeypatch)
    import weather
    import myday

    async def set_city(_bot, _cid, _text, **kwargs):
        assert kwargs["retry_kind"] == "vacation_city" and kwargs["announce"] is False
        places["now"] = dict(CAPE)
        return True

    async def plany(*_a, **_k):
        return None

    monkeypatch.setattr(weather, "set_city_text", set_city)
    monkeypatch.setattr(vacation, "guide", lambda _cid: {})
    monkeypatch.setattr(myday, "send_plany", plany)
    bot = Bot()

    asyncio.run(vacation.ask_city(bot, "42"))
    assert store.pending_input["42"] == "vacation_city"
    asyncio.run(vacation.city_entered(bot, "42", "Cape Town"))
    assert vacation.state("42")["home"]["city"] == "Алкмар" and vacation.state("42")["cc"] == "ZA"
    assert [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard] == [
        ["set_vacd_3"], ["set_vacd_7"], ["set_vacd_14"], ["set_vacd_0"]]

    asyncio.run(vacation.set_days(bot, "42", "7"))
    assert vacation.state("42")["until"] and vacation.until_label("42").startswith("до ")

    asyncio.run(vacation.return_home(bot, "42"))
    assert places["now"]["city"] == "Алкмар" and not vacation.active("42")
    assert bot.sent[-1]["text"].startswith("🏠 С возвращением!")


def test_ordinary_city_change_ends_vacation_without_restoring(monkeypatch):
    saved, places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "until": "", "cc": "ZA"}

    vacation.forget("42")

    assert not vacation.active("42") and places["now"]["city"] == "Кейптаун"


def test_vacation_ends_on_return_day(monkeypatch):
    import access
    saved, places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "until": "2026-10-20", "cc": "ZA"}
    monkeypatch.setattr(access, "get_allowed_cids", lambda: ["42"])

    asyncio.run(vacation.finish_due(Bot(), today=date(2026, 10, 19)))
    assert vacation.active("42")
    asyncio.run(vacation.finish_due(Bot(), today=date(2026, 10, 20)))
    assert not vacation.active("42") and places["now"]["city"] == "Алкмар"


def _guide(code="af"):
    lessons = [{"text": f"Les {i}", "translation": f"Урок {i}", "rule": "_Hoe_ → как.", "more": "Hoe laat?",
                "more_translation": "Который час?"} for i in range(7)]
    words = [{"text": f"Woord {i}", "ru": f"Слово {i}", "say": f"вурд {i}"} for i in range(30)]
    return {"v": 3, "language_code": code, "language_ru": "Африкаанс", "lessons": lessons, "words": words}


def test_learning_switches_to_country_language_only_for_other_language(monkeypatch):
    import learning
    from ui import menu as menu_ui
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "ZA", "guide": _guide()}
    monkeypatch.setattr(store, "get_learning_language", lambda _cid: "nl")

    home = learning.build_learning_home("42")
    msg = menu_ui.learning_menu(home)
    assert msg.text.startswith("🇿🇦 Изучаем сегодня · Африкаанс")
    assert "Грамматика: Hoe" in msg.text and "Пример: Hoe laat? → Который час?" in msg.text
    assert msg.text.endswith("Как.") and "Фраза дня" not in msg.text
    assert [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row][0] == "vac_train"
    assert vacation.lesson_today("42", date(2026, 10, 12)) != vacation.lesson_today("42", date(2026, 10, 13))
    assert vacation.word_today("42", date(2026, 10, 12)).count(" → ") == 1

    saved["vacation"]["guide"] = _guide("nl")  # Бельгия: учишь нидерландский — обычное обучение
    assert not vacation.replaces_learning("42") and vacation.learning_home("42") is None


def test_vacation_quiz_is_a_translation_poll(monkeypatch):
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "ZA", "guide": _guide()}
    monkeypatch.setattr(store, "get_learning_language", lambda _cid: "nl")
    polls = []

    class PollBot(Bot):
        async def send_poll(self, **kwargs):
            polls.append(kwargs)

    asyncio.run(vacation.send_quiz(PollBot(), "42"))

    poll = polls[0]
    assert poll["type"] == "quiz" and len(poll["options"]) == 4 and poll["question"].startswith("🎯 Выбери перевод")
    term = poll["question"].split("Что значит ", 1)[1].split(" [", 1)[0]
    assert poll["options"][poll["correct_option_id"]] == "Слово " + term.split()[-1]


def test_vacation_cuisine_known_or_local_with_home_fallback(monkeypatch):
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "IT"}
    assert day_menu.day_cuisine("42", date(2026, 10, 12)) == "italian"
    saved["vacation"] = {"home": HOME, "cc": "ZA", "guide": {"v": 3, "cuisine_ru": "Южноафриканская"}}
    assert day_menu.day_cuisine("42", date(2026, 10, 12)) == "local_za"
    assert day_menu.cuisine_flag("local_za") == "🇿🇦"

    import recipe_generation
    monkeypatch.setattr(recipe_generation, "_home_idea_context", lambda *_a, **_k: {"available": []})
    monkeypatch.setattr(store, "get_profile", lambda _cid: {})
    calls = []

    def ai_dishes(_cid, cuisine, *_a, **_k):
        calls.append(cuisine)
        if cuisine == "local_za":
            return {meal: {"name": f"Bobotie {meal}", "note": "запеканка"} for meal in day_menu.MEALS}
        return {}

    monkeypatch.setattr(day_menu, "_ai_dishes", ai_dishes)
    menu = day_menu.build_day_menu("42", cuisine="local_za")
    assert menu["cuisine_label"] == "Южноафриканская" and menu["dishes"]["lunch"]["name"] == "Bobotie lunch"

    monkeypatch.setattr(day_menu, "_ai_dishes", lambda *_a, **_k: {})
    fallback = day_menu.build_day_menu("42", cuisine="local_za")
    assert not fallback["cuisine"].startswith("local_") and "cuisine_label" not in fallback


def test_country_block_and_summary_lines():
    facts = {"country": {"capital": "Pretoria", "area": 1221037, "languages": ["Afrikaans", "English", "Zulu"],
                         "currency": "ZAR", "symbol": "R", "calling": "+27"},
             "population": {"value": 64747319, "year": "2025"}, "rate": 18.527, "hours": 1}
    lines = dict(vacation_ui.country_lines(facts, {"languages_ru": {"Afrikaans": "африкаанс", "English": "английский"}}))

    assert lines == {
        "Столица": "Pretoria", "Население": "64,7 млн (2025)", "Площадь": "1 221 037 км²",
        "Языки": "африкаанс, английский и ещё 1", "Валюта": "ZAR (R) · 1 € = 18,53 R",
        "Время": "на 1 ч больше, чем в Амстердаме", "Телефонный код": "+27",
    }
    text = myday_ui.day_summary("Пн, 12 окт", "Кейптаун", word_line="Dankie → Спасибо", vacation={
        "until": "до 20 октября", "home": "Алкмар", "country_lines": [("Столица", "Pretoria")]}).text
    assert "✈️ В отпуске до 20 октября · дома Алкмар" in text
    assert "🌍 Страна:\nСтолица: Pretoria" in text and "🎯 Тренажер: Dankie → Спасибо" in text
    assert "Разговорник" not in text


def test_dates_in_any_form_and_city_with_dates_in_one_message():
    today = date(2026, 10, 12)
    assert vacation.split_city_and_until("Cape Town до 20 октября", today) == ("Cape Town", date(2026, 10, 20))
    assert vacation.split_city_and_until("Рим с 15 по 25 октября", today) == ("Рим", date(2026, 10, 25))
    assert vacation.split_city_and_until("Барселона на неделю", today) == ("Барселона", date(2026, 10, 19))
    assert vacation.split_city_and_until("Cape Town", today) == ("Cape Town", None)
    assert vacation.parse_until("20.10", today) == date(2026, 10, 20)
    assert vacation.parse_until("5 января", today) == date(2027, 1, 5)  # прошедшая дата — следующий год
    assert vacation.parse_until("когда-нибудь", today) is None


def test_city_with_dates_skips_question_and_manual_date_works(monkeypatch):
    saved, places = _env(monkeypatch)
    import weather
    import myday
    finished = []

    async def set_city(_bot, _cid, text, **_kwargs):
        assert text == "Cape Town"  # сроки отрезаны от города
        places["now"] = dict(CAPE)
        return True

    async def plany(*_a, **_k):
        finished.append(vacation.state("42")["until"])

    monkeypatch.setattr(weather, "set_city_text", set_city)
    monkeypatch.setattr(vacation, "guide", lambda _cid: {})
    monkeypatch.setattr(myday, "send_plany", plany)
    monkeypatch.setattr(vacation, "parse_until", lambda text, today=None: (
        date(2026, 10, 20) if "20" in str(text) else None))

    asyncio.run(vacation.city_entered(Bot(), "42", "Cape Town до 20 октября"))
    assert finished == ["2026-10-20"]

    bot = Bot()
    asyncio.run(vacation.until_entered(bot, "42", "не знаю"))
    assert store.pending_input["42"] == "vacation_until" and "Не понял дату" in bot.sent[-1]["text"]
    asyncio.run(vacation.until_entered(bot, "42", "20.10"))
    assert finished[-1] == "2026-10-20"


def test_ask_days_screen_shows_flag_facts_and_italic_question():
    msg = vacation_ui.ask_days("Кейптаун", "🇿🇦", [("Столица", "Pretoria"), ("Валюта", "ZAR (R) · 1 € = 18,53 R")])
    assert msg.text.startswith("✈️ Кейптаун 🇿🇦\n\nСтолица: Pretoria\nВалюта:")
    italic = [msg.text.encode("utf-16-le")[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")
              for e in msg.entities if e.type == "italic"]
    assert italic and italic[-1].startswith("До какого дня отпуск?") and "дату в чат" in italic[-1]



def test_holidays_of_the_trip_are_listed_with_russian_names(monkeypatch):
    import public_holidays
    saved, _places = _env(monkeypatch, {**CAPE, "cc": "ZA"})
    saved["vacation"] = {"home": HOME, "cc": "ZA", "until": "2026-12-31"}
    monkeypatch.setattr(vacation, "trip_holidays", REAL_TRIP_HOLIDAYS)
    rows = [{"date": "2026-12-16", "local": "Day of Reconciliation", "name": "Day of Reconciliation"},
            {"date": "2026-12-25", "local": "Christmas Day", "name": "Christmas Day"}]
    monkeypatch.setattr(public_holidays, "_year_holidays", lambda *_a: rows)
    monkeypatch.setattr(public_holidays, "holidays_between", lambda *_a: rows)
    monkeypatch.setattr(vacation.ai, "llm_json", lambda *_a, **_k: {"items": [
        {"name": "Day of Reconciliation", "ru": "День примирения"}, {"name": "Christmas Day", "ru": "Рождество"}]})

    lines = vacation.trip_holidays("42", date(2026, 12, 31))
    assert lines == [("16 декабря", "Day of Reconciliation — День примирения"), ("25 декабря", "Christmas Day — Рождество")]

    text = vacation_ui.started("Кейптаун", "до 31 декабря", "Алкмар", lines).text
    assert "Праздники в эти дни:\n• 16 декабря · Day of Reconciliation — День примирения" in text
    assert "Государственных праздников в эти дни нет." in vacation_ui.started("Кейптаун", "до 1 ноября", "", []).text
