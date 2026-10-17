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

    async def no_warm(_cid):
        return None

    monkeypatch.setattr(vacation, "_warm_trip_leisure", no_warm)  # фоновый прогрев Досуга — без сети
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
    phrases = [{"text": f"Frase {i}", "ru": f"Фраза {i}", "say": f"фразэ {i}"} for i in range(10)]
    situations = [{"title": f"ситуация {i}", "tip": "«g» читается как хриплое «х»",
                   "phrases": [{"text": "Een koffie, asseblief", "ru": "Кофе, пожалуйста", "say": "ин кофи, асэблиф"},
                               {"text": "Dankie!", "ru": "Спасибо!", "say": "данки"}]} for i in range(8)]
    return {"v": 5, "language_code": code, "language_ru": "Африкаанс", "phrases": phrases, "situations": situations,
            "language_fact": "Африкаанс вырос из нидерландского XVII века",
            "tips": {"emergency": "112", "plugs": "тип M, 230 В", "tipping": "", "water": "можно"}}


def test_learning_switches_to_country_language_only_for_other_language(monkeypatch):
    import learning
    from ui import menu as menu_ui
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "ZA", "guide": _guide()}
    monkeypatch.setattr(store, "get_learning_language", lambda _cid: "nl")

    home = learning.build_learning_home("42")
    msg = menu_ui.learning_menu(home)
    # Не грамматика, а что пригодится в поездке: ситуация дня цитатой — название и фразы с произношением.
    assert msg.text.startswith("🇿🇦 Обучение в отпуске · Африкаанс\n\nСитуация:\nСитуация ")
    assert "\nEen koffie, asseblief → Кофе, пожалуйста (ин кофи, асэблиф)\nDankie! → Спасибо! (данки)\n" in msg.text
    utf16 = msg.text.encode("utf-16-le")
    quote = next(utf16[2 * e.offset:2 * (e.offset + e.length)].decode("utf-16-le")
                 for e in msg.entities if e.type == "blockquote")
    assert quote.startswith("Ситуация ") and quote.endswith("Dankie! → Спасибо! (данки)")  # всё — в цитате
    assert "Произношение" not in msg.text and "Грамматика" not in msg.text  # в поездке — только фразы и факт
    assert msg.text.endswith("Африкаанс вырос из нидерландского XVII века.")
    assert [b.callback_data for row in msg.reply_markup.inline_keyboard for b in row][0] == "vac_train"
    assert vacation.situation_today("42", date(2026, 10, 12)) != vacation.situation_today("42", date(2026, 10, 13))

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
    assert poll["options"][poll["correct_option_id"]] == "Фраза " + term.split()[-1]


def test_vacation_cuisine_known_or_local_with_home_fallback(monkeypatch):
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "IT"}
    assert day_menu.day_cuisine("42", date(2026, 10, 12)) == "italian"
    saved["vacation"] = {"home": HOME, "cc": "ZA", "guide": {"v": 5, "cuisine_ru": "Южноафриканская"}}
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
        "Языки": "африкаанс, английский, Zulu", "Валюта": "ZAR (R) · 1 € = 18,53 R",  # все языки, без «и ещё N»
        "Время": "на 1 ч больше, чем в Амстердаме", "Телефонный код": "+27",
    }
    text = myday_ui.day_summary("Пн, 12 окт", "Кейптаун", word_line="Dankie → Спасибо", vacation={
        "country_lines": [("Столица", "Pretoria")]}).text
    assert "В отпуске" not in text and "дома" not in text  # строку «✈️ В отпуске … · дома …» не пишем
    assert "🌍 Страна:\nСтолица: Pretoria" in text and "💬 Фраза дня: Dankie → Спасибо" in text  # в отпуске — фраза страны
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


def test_trip_weather_summary_counts_only_vacation_days(monkeypatch):
    import weather
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "ZA", "until": "2026-10-14"}
    monkeypatch.setattr(weather, "fetch_weather", lambda *_a: {"daily": {
        "time": ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"],
        "temperature_2m_max": [17.2, 21, 24.4, 30], "precipitation_probability_max": [70, 10, 0, 90],
        "windspeed_10m_max": [5, 9.4, 3, 20]}})

    summary = vacation.trip_weather_summary("42")
    # Только дни отпуска (до 14-го): 15-е с +30° и ливнем не считается.
    assert summary.endswith("днём от +17° до +24°, дождь в 1 из 3 дней, ветер до 9 м/с")
    assert summary.startswith("Кейптаун (ZA), до 14 октября")


def test_concerts_in_trip_country_only_for_trip_dates(monkeypatch):
    import leisure_concerts
    saved, _places = _env(monkeypatch, CAPE)
    assert leisure_concerts.trip_end("42", "ZA") is None  # дома — без ограничения
    saved["vacation"] = {"home": HOME, "cc": "ZA", "until": "2026-10-20"}
    assert leisure_concerts.trip_end("42", "za") == date(2026, 10, 20)
    assert leisure_concerts.trip_end("42", "NL") is None  # другая страна — обычная афиша
    saved["vacation"]["until"] = ""
    monkeypatch.setattr(leisure_concerts, "_today", lambda: date(2026, 10, 12))
    assert leisure_concerts.trip_end("42", "ZA") == date(2026, 11, 11)  # без даты — 30 дней

    ends = []
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_get", lambda *_a: [])
    monkeypatch.setattr(leisure_concerts, "concert_items_between", lambda _e, _s, end, _l: ends.append(end) or [])
    leisure_concerts.cached_favorite_concerts("42", 3)
    assert ends == [date(2026, 11, 11)]


def test_country_instead_of_city_asks_for_city_and_keeps_date(monkeypatch):
    import countries
    import weather
    saved, places = _env(monkeypatch)
    monkeypatch.setattr(countries, "_table", lambda: {"ES": ["Испания", "europe", "Spain"], "AE": ["ОАЭ", "asia", "UAE"]})
    queries, finished = [], []

    async def set_city(_bot, _cid, text, **_kwargs):
        queries.append(text)
        places["now"] = {"lat": 41.4, "lon": 2.2, "city": "Барселона", "country": "Испания", "cc": "ES"}
        return True

    async def finish(_bot, _cid, until):
        finished.append(until)

    monkeypatch.setattr(weather, "set_city_text", set_city)
    monkeypatch.setattr(vacation, "_finish", finish)
    bot = Bot()

    asyncio.run(vacation.city_entered(bot, "42", "Испания до 20 октября"))
    assert queries == [] and bot.sent[0]["text"].startswith("✈️ Испания 🇪🇸\n\nКакой город?")
    assert store.pending_input["42"] == "vacation_city"

    asyncio.run(vacation.city_entered(bot, "42", "Барселона"))
    assert queries == ["Барселона, Испания"]  # город ищется в этой стране
    assert finished and finished[0].month == 10 and finished[0].day == 20  # дата из первого сообщения
    assert vacation.place_label(places["now"]) == "Барселона, ES 🇪🇸"


def test_vacation_hides_home_data_settings_buttons(monkeypatch):
    import telegram_runtime
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    saved, _places = _env(monkeypatch, CAPE)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ Другое меню на сегодня", callback_data="food_pick")],
        [InlineKeyboardButton("🎚️ Настроить холодильник", callback_data="as_fridge_home"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
    assert telegram_runtime._without_home_settings(markup, "42") is markup  # дома — без изменений
    saved["vacation"] = {"home": HOME, "cc": "ZA"}
    texts = [b.text for row in telegram_runtime._without_home_settings(markup, "42").inline_keyboard for b in row]
    assert texts == ["✨ Другое меню на сегодня", "#️⃣ Главная"]


def test_vacation_adds_trip_priority_rule_to_leisure(monkeypatch):
    saved, _places = _env(monkeypatch, CAPE)
    assert vacation.trip_priority_rule("42", "исполнителей") == ""  # дома — без правила
    saved["vacation"] = {"home": HOME, "cc": "za", "until": ""}

    rule = vacation.trip_priority_rule("42", "исполнителей")
    assert "ЮАР (ZA)" in rule and "Сначала предлагай исполнителей" in rule


def test_settings_on_vacation_keep_only_return_home_and_notifications(monkeypatch):
    saved, _places = _env(monkeypatch, CAPE)
    bot = Bot()
    asyncio.run(settings.send_home(bot, "42"))
    home = [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard]
    assert "set_vac" in sum(home, []) and "set_preferences" in sum(home, [])  # дома — всё

    saved["vacation"] = {"home": HOME, "until": "", "cc": "ZA"}
    asyncio.run(settings.send_home(bot, "42"))
    trip = [[b.callback_data for b in row] for row in bot.sent[-1]["reply_markup"].inline_keyboard]
    assert trip == [["set_vachome"], ["set_notif"], ["m_menu"]]  # без предпочтений, города и экспорта


def test_trip_phrases_cover_every_day_of_the_trip_without_repeats(monkeypatch):
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "ZA", "since": "2026-10-12", "until": "2026-10-21", "guide": _guide()}

    assert vacation.trip_days("42") == 10  # 12–21 октября включительно
    days = [vacation.phrase_today("42", date(2026, 10, 12 + i)) for i in range(10)]
    assert len(set(days)) == 10 and days[0] == "Frase 0 (фразэ 0) → Фраза 0"
    saved["vacation"]["until"] = ""
    assert vacation.trip_days("42") == 30  # без даты возвращения
    saved["vacation"]["until"] = "2027-06-01"
    assert vacation.trip_days("42") == 60  # долгая поездка — не больше 60 фраз


def test_guide_keeps_phrases_for_the_trip_days_and_traveller_tips():
    data = {"language_ru": "Африкаанс", "language_code": "af",
            "phrases": [{"text": f"F{i}", "ru": f"Ф{i}"} for i in range(12)] + [{"text": "F1", "ru": "дубль"}],
            "situations": [{"title": "в кафе", "phrases": [{"text": "Dankie", "ru": "Спасибо"}], "tip": "Мягко."},
                           {"title": "пусто", "phrases": []}],
            "tips": {"emergency": "112", "plugs": "тип M"}}

    guide = vacation._clean_guide(data, days=10)

    assert len(guide["phrases"]) == 10 and guide["phrases"][1]["ru"] == "Ф1"  # по числу дней, без дублей
    assert [item["title"] for item in guide["situations"]] == ["в кафе"]  # ситуация без фраз отброшена
    assert guide["tips"] == {"emergency": "112", "plugs": "тип M", "tipping": "", "water": ""}
    lines = dict(vacation_ui.country_lines({}, guide))
    assert lines == {"Экстренный номер": "112", "Розетки": "тип M"}  # пустые пункты памятки не показываются


def test_trip_leisure_keeps_only_verified_films_and_artists_of_the_country(monkeypatch):
    import countries
    import tmdb
    films = {"District 9": {"id": 1, "name": "Район №9", "name_en": "District 9", "year": "2009", "genres": "фантастика"},
             "Inception": {"id": 2, "name": "Начало", "name_en": "Inception", "year": "2010"}}
    monkeypatch.setattr(tmdb, "lookup_title", lambda title, *_a, **_k: films.get(title))
    monkeypatch.setattr(tmdb, "detail", lambda movie_id, _kind: {"countries": ["ZA"] if movie_id == 1 else ["US"]})
    monkeypatch.setattr(countries, "label", lambda cc: f"flag {cc}")
    monkeypatch.setattr(countries, "artist_label", lambda name, fetch=True: "flag ZA" if name == "Miriam Makeba" else "flag US")

    picked = vacation._verified_films([{"title": "District 9"}, {"title": "Inception"}, {"title": "Выдумка"}], "ZA")
    assert [film["title"] for film in picked] == ["Район №9"]  # американский и несуществующий — отброшены
    assert vacation._verified_artists(["Miriam Makeba", "Taylor Swift"], "ZA") == ["Miriam Makeba"]


def test_trip_leisure_screen_has_country_culture_and_what_is_on_during_the_trip():
    from ui import leisure as leisure_ui
    msg = leisure_ui.trip_leisure_screen("ЮАР 🇿🇦", {
        "films": [{"id": 1, "title": "Район №9", "name_en": "District 9", "year": "2009", "genres": "фантастика"}],
        "music": ["Tyla", "Die Antwoord", "Miriam Makeba"],
        "holidays": [("24 сентября", "Heritage Day — День наследия")],
    }, events=[{"name": "Cape Town Jazz Festival", "start": "2026-09-26", "end": "2026-09-27"}],
        concerts=[{"artist": "Muse", "date": "2026-09-30", "venue": "Cape Town Stadium"}])

    text = msg.text
    assert text.startswith("🍿 Досуг · ЮАР 🇿🇦\n\nФильмы страны:\n- Район №9 · 2009 · фантастика")
    assert "Музыка страны: Tyla · Die Antwoord · Miriam Makeba" in text and "Легенды" not in text
    assert "Во время отпуска:\n🎉 24 сентября — Heritage Day — День наследия\n🎪 Cape Town Jazz Festival — 26 сентября" in text
    assert "🎫 Muse — 30 сентября, Cape Town Stadium" in text
    assert [[b.callback_data for b in row] for row in msg.reply_markup.inline_keyboard] == [["m_menu"]]


def test_leisure_buttons_open_the_trip_screen_on_vacation(monkeypatch):
    import bot_callbacks
    saved, _places = _env(monkeypatch, CAPE)
    assert bot_callbacks._legacy_alias("movie_reco", "42") == "movie_reco"  # дома — как обычно
    saved["vacation"] = {"home": HOME, "cc": "ZA", "until": ""}
    for data in ("movie_reco", "tv_reco", "music_reco", "lz_prem", "lz_prem_movie_2", "lz_concerts"):
        assert bot_callbacks._legacy_alias(data, "42") == "m_leisure"
    assert bot_callbacks._legacy_alias("m_myday", "42") == "m_myday"


def test_local_cuisine_menu_has_an_intro_like_home_cuisines(monkeypatch):
    import menu
    saved, _places = _env(monkeypatch, CAPE)
    monkeypatch.setattr(vacation, "cuisine_facts", lambda _cid: [])
    saved["vacation"] = {"home": HOME, "cc": "ZA", "guide": {"v": 5, "cuisine_ru": "Южноафриканская",
                                                             "cuisine_intro": "Мясо на открытом огне и пряные карри."}}
    text = menu._day_menu_message({"cuisine": "local_za", "cuisine_label": "Южноафриканская",
                                        "dishes": {"lunch": {"name": "Брай", "note": "мясо на открытом огне"}}}, "42").text
    assert text.startswith("🇿🇦 Пробуем в отпуске · Южноафриканская кухня\n\nМясо на открытом огне и пряные карри.")


def test_country_music_is_up_to_three_local_artists_chart_first(monkeypatch):
    import apple_music
    import countries
    local = {"Tyla", "Kabza De Small", "Die Antwoord", "Black Coffee"}
    monkeypatch.setattr(countries, "label", lambda cc: f"flag {cc}")
    monkeypatch.setattr(countries, "artist_label", lambda name, fetch=True: "flag ZA" if name in local else "flag US")
    monkeypatch.setattr(apple_music, "_feed", lambda _cc: [
        {"artistName": "Taylor Swift"}, {"artistName": "Tyla"}, {"artistName": "Tyla"}, {"artistName": "Drake"},
        {"artistName": "Kabza De Small"}])

    music = vacation._verified_artists([*vacation._chart_artists("ZA"), "Die Antwoord", "Black Coffee"], "ZA")

    # Из чарта — только местные (Тейлор Свифт и Дрейк — нет), затем известные из AI; не больше трёх, без повторов.
    assert music == ["Tyla", "Kabza De Small", "Die Antwoord"]


def test_trip_menu_is_what_to_try_with_top_dish_and_no_recipe_buttons(monkeypatch):
    import day_menu
    import menu
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "ZA", "guide": {"v": 5, "cuisine_ru": "Южноафриканская",
                                                             "cuisine_intro": "Мясо на открытом огне."}}
    prompts = []

    def dishes(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return {"breakfast": {"name": "Руськи", "note": "сухари к кофе"},
                "lunch": {"name": "Боботи", "note": "запечённый фарш с карри"},
                "dinner": {"name": "Брай", "note": "мясо на открытом огне"}, "top": "dinner"}

    monkeypatch.setattr(day_menu.ai, "llm_json", dishes)
    monkeypatch.setattr(day_menu, "_settings", lambda: type("S", (), {
        "food_meals": staticmethod(lambda _cid: ["breakfast", "lunch", "dinner"]),
        "food_restrictions": staticmethod(lambda _cid: []),
        "food_restrictions_prompt": staticmethod(lambda _cid: ""),
        "cuisines": staticmethod(lambda _cid: [])})())
    monkeypatch.setattr(store, "get_profile", lambda _cid: {})

    built = day_menu.build_day_menu("42", cuisine="local_za")

    assert "обязательно стоит попробовать в этой стране" in prompts[0] and '"top"' in prompts[0]
    assert built["top"] == "dinner"
    monkeypatch.setattr(vacation, "cuisine_facts", lambda _cid: ["Брай — национальная традиция: 24 сентября в ЮАР "
                                                                   "отмечают Национальный день брая"])
    msg = menu._day_menu_message(built, "42")
    assert [[b.callback_data for b in row] for row in msg.reply_markup.inline_keyboard] == [
        ["food_trip_next"], ["m_menu"]]  # «Другое блюдо» и «Главная», без рецептов «Завтрак / Обед / Ужин»
    # Одно блюдо — то, что стоит попробовать сегодня (top), жирным, ниже описание; без «Завтрак / Обед / Ужин».
    assert msg.text == ("🇿🇦 Пробуем в отпуске · Южноафриканская кухня\n\nМясо на открытом огне.\n\nБрай\n"
                        "Мясо на открытом огне.\n\nБрай — национальная традиция: 24 сентября в ЮАР отмечают "
                        "Национальный день брая.")
    utf16 = msg.text.encode("utf-16-le")  # смещения Telegram — в UTF-16: флаг 🇿🇦 занимает 4 единицы
    bold = [utf16[2 * e.offset:2 * (e.offset + e.length)].decode("utf-16-le") for e in msg.entities if e.type == "bold"]
    assert "Брай" in bold and "Завтрак" not in msg.text and "Обед" not in msg.text


def test_another_dish_on_trip_goes_through_todays_set_then_asks_for_a_new_one(monkeypatch):
    import day_menu
    profile = {"cooking_day_menu": {
        "date": day_menu._today(None).isoformat(), "cuisine": "local_za", "top": "dinner",
        "dishes": {"breakfast": {"name": "Руськи"}, "lunch": {"name": "Боботи"}, "dinner": {"name": "Брай"}}}}
    monkeypatch.setattr(store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(store, "mutate_profile", lambda _cid, change: profile.update(change(dict(profile))[0]))
    monkeypatch.setattr(day_menu, "_settings", lambda: type("S", (), {
        "food_meals": staticmethod(lambda _cid: ["breakfast", "lunch", "dinner"])})())
    refreshed = []
    monkeypatch.setattr(day_menu, "get_day_menu", lambda cid, now=None, refresh=False, cuisine=None: refreshed.append(
        refresh) or {"dishes": {"lunch": {"name": "Потжи"}}})

    shown = [day_menu.trip_dish(profile["cooking_day_menu"])["name"]]
    for _press in range(2):
        shown.append(day_menu.trip_dish(day_menu.next_trip_dish("42"))["name"])
    assert shown == ["Брай", "Руськи", "Боботи"] and not refreshed  # сначала блюдо дня, затем остальные — без AI
    assert day_menu.trip_dish(day_menu.next_trip_dish("42"))["name"] == "Потжи" and refreshed == [True]


def test_cuisine_facts_are_built_once_per_trip_and_rotate(monkeypatch):
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "cc": "ZA"}
    calls = []
    monkeypatch.setattr(vacation.ai, "llm_json", lambda *_a, **_k: calls.append(1) or {
        "facts": ["Боботи считают национальным блюдом ЮАР", "Билтонг — вяленое мясо, его едят как снек", "no"]})

    assert vacation.cuisine_facts("42") == ["Боботи считают национальным блюдом ЮАР",
                                            "Билтонг — вяленое мясо, его едят как снек"]  # без кириллицы — мимо
    assert vacation.cuisine_facts("42") and calls == [1]  # на поездку — один запрос
    day = date(2026, 10, 17)
    assert vacation.cuisine_fact("42", 0, day) != vacation.cuisine_fact("42", 1, day)  # «Другое блюдо» — другой факт


def test_my_day_on_trip_shows_the_dish_to_try_today():
    text = myday_ui.day_summary("Пт, 16 окт", "Кейптаун", vacation={
        "try_dish": {"name": "Брай", "note": "Мясо на открытом огне"}}).text
    assert "🍽 Попробовать сегодня: Брай — мясо на открытом огне" in text
    assert "Попробовать" not in myday_ui.day_summary("Пт", "Алкмар").text  # дома строки нет


def test_trip_period_from_city_and_dates_in_any_form():
    today = date(2026, 10, 16)
    split = vacation.split_city_and_period
    assert split("Кейптаун, 20–27 октября", today) == ("Кейптаун", (date(2026, 10, 20), date(2026, 10, 27)))
    assert split("Рим с 20 по 25 октября", today) == ("Рим", (date(2026, 10, 20), date(2026, 10, 25)))
    assert split("Барселона на неделю с 5 ноября", today) == ("Барселона", (date(2026, 11, 5), date(2026, 11, 11)))
    assert split("Рим 20 октября – 3 ноября", today) == ("Рим", (date(2026, 10, 20), date(2026, 11, 3)))
    assert split("Лиссабон до 30 октября", today) == ("Лиссабон", (today, date(2026, 10, 30)))
    assert split("Мадрид 1-5 января", today) == ("Мадрид", (date(2027, 1, 1), date(2027, 1, 5)))
    assert split("Рим с 15 по 25 октября", today)[1] == (today, date(2026, 10, 25))  # поездка уже идёт
    assert split("Кейптаун", today) == ("Кейптаун", None)
    assert vacation.parse_period("с 1 по 30 марта и ещё месяц", today) is not None
    assert vacation.parse_period("с 1 января по 1 мая", today) is None  # длиннее 60 дней


def test_vacation_offers_the_saved_suitcase_trip_and_starts_it(monkeypatch):
    saved, places = _env(monkeypatch)
    import wardrobe_suitcase
    finished = []
    trip = {"city": "Кейптаун", "cc": "ZA", "lat": -33.9, "lon": 18.4, "start": "2026-10-20", "end": "2026-10-27"}
    monkeypatch.setattr(vacation, "datetime", type("Fixed", (), {
        "now": staticmethod(lambda tz=None: __import__("datetime").datetime(2026, 10, 16, 9, tzinfo=tz))}))
    monkeypatch.setattr(wardrobe_suitcase, "saved_suitcase", lambda _cid: {"trip": trip, "result": {"groups": []}})

    async def finish(_bot, _cid, until):
        finished.append(until)

    monkeypatch.setattr(vacation, "_finish", finish)
    bot = Bot()

    asyncio.run(vacation.ask_city(bot, "42"))
    button = bot.sent[-1]["reply_markup"].inline_keyboard[0][0]
    assert (button.text, button.callback_data) == ("🧳 Кейптаун · до 27 октября", "set_vac_trip")

    asyncio.run(vacation.start_suitcase_trip(bot, "42"))
    assert places["now"]["city"] == "Кейптаун" and places["now"]["lat"] == -33.9  # координаты чемодана, без поиска
    assert vacation.state("42")["home"]["city"] == "Алкмар" and vacation.state("42")["cc"] == "ZA"
    assert finished == [date(2026, 10, 27)]


def test_vacation_has_no_suitcase_button_without_a_current_trip(monkeypatch):
    _env(monkeypatch)
    import wardrobe_suitcase
    old = {"city": "Кейптаун", "cc": "ZA", "lat": -33.9, "lon": 18.4, "start": "2025-10-20", "end": "2025-10-27"}
    monkeypatch.setattr(wardrobe_suitcase, "saved_suitcase", lambda _cid: {"trip": old, "result": {}})
    bot = Bot()

    asyncio.run(vacation.ask_city(bot, "42"))

    assert bot.sent[-1]["reply_markup"] is None  # поездка уже закончилась — только вопрос о городе


def test_settings_on_vacation_show_trip_home_trip_language_and_skip_paused_learning(monkeypatch):
    import countries
    saved, _places = _env(monkeypatch, CAPE)
    saved["vacation"] = {"home": HOME, "until": "2026-11-04", "cc": "ZA"}
    monkeypatch.setattr(countries, "_table", lambda: {"ZA": ["ЮАР", "africa"]})
    monkeypatch.setattr(vacation, "replaces_learning", lambda _cid: True)
    monkeypatch.setattr(vacation, "guide", lambda _cid: {"language_ru": "Африкаанс"})
    on = {"daily_words", "evening_weather"}
    monkeypatch.setattr(settings, "notif_on", lambda _cid, kind: kind in on)
    bot = Bot()

    asyncio.run(settings.send_home(bot, "42"))

    text = bot.sent[-1]["text"]
    assert "Отпуск: Кейптаун, ЮАР 🇿🇦 · до 4 ноября\nДома: Алкмар\nЯзык в поездке: Африкаанс — фразы на каждый день" \
        in text
    assert "ZA ·" not in text and "Язык обучения" not in text and "кнопкой «Настроить»" not in text
    # «Обучение языку» в отпуске на паузе: не считается и не показывается следующим.
    assert "Уведомления: включено 1 · следующая: Погода на завтра" in text
    assert settings.notification_paused("42", "daily_words") and not settings.notification_paused("42", "evening_weather")
