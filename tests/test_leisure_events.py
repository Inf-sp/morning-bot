from datetime import date

import leisure_events
from ui import leisure as leisure_ui

TODAY = date(2026, 11, 24)


def _stub(monkeypatch):
    monkeypatch.setattr(leisure_events, "_today", lambda _cid: (TODAY, "NL"))
    monkeypatch.setattr(leisure_events, "_concerts", lambda *_a: [
        {"kind": "concert", "name": "Muse", "url": "https://t/muse", "date": "2026-11-28", "end": "2026-11-28",
         "venue": "Ziggo Dome", "place": "Ziggo Dome, Amsterdam", "mine": True,
         "about": "Стадионный рок с оркестром.", "image": ""}])
    monkeypatch.setattr(leisure_events, "_city", lambda *_a: [
        {"kind": "festival", "name": "Winterfest", "url": "https://w", "date": "2026-11-29", "end": "2026-11-30",
         "place": "Hortus", "free": True, "about": "зимний фестиваль", "image": "https://img/w.jpg"},
        {"kind": "exhibition", "name": "Vermeer", "url": "", "date": "2026-11-20", "end": "2026-12-20",
         "place": "Stedelijk Museum", "free": False, "about": "", "image": ""}])
    monkeypatch.setattr(leisure_events, "_holidays", lambda *_a: [])
    monkeypatch.setattr(leisure_events, "_premieres", lambda *_a: [
        {"kind": "premiere", "premiere": "movie", "name": "Дюна", "url": "https://y", "date": "2026-11-20",
         "end": "2026-11-20", "meta": ["фантастика"], "about": "Пол Атрейдес.", "image": "https://img/d.jpg"}])
    monkeypatch.setattr(leisure_events, "_reads", lambda *_a: [
        {"kind": "read", "name": "DNA repair", "url": "https://q/dna", "date": "2026-11-21", "end": "2026-11-21",
         "meta": ["Quanta", "EN", "8 мин"], "about": "Клетки чинят ДНК.", "image": "https://img/q.jpg"}])


def test_events_are_a_week_journal_by_date_with_dates_on_every_card(monkeypatch):
    _stub(monkeypatch)
    cards = leisure_events.feed("42")
    # Уже идущее и вышедшее — как сегодня; дальше по дате, как расписание.
    assert [card["name"] for card in cards] == ["Vermeer", "Дюна", "DNA repair", "Muse", "Winterfest"]
    assert leisure_ui.event_card(cards, 3).text == (
        "🎫 Muse\n\nКонцерт · 28 ноября 2026 · Ziggo Dome, Amsterdam · твой артист\n\nСтадионный рок с оркестром.")
    assert leisure_ui.event_card(cards, 2).text.startswith("📰 DNA repair\n\nСтатья · 21 ноября · Quanta · EN · 8 мин")
    assert leisure_ui.event_card(cards, 4).text.startswith("🎪 Winterfest\n\nФестиваль · 29–30 ноября · Hortus · бесплатно")
    rows = leisure_ui.event_card(cards, 3).reply_markup.inline_keyboard
    assert [b.callback_data for b in rows[0]] == ["lz_ev_2", "noop", "lz_ev_4"] and rows[0][1].text == "4/5"


def test_every_card_gets_a_picture(monkeypatch):
    import countries
    asked = []
    monkeypatch.setattr(countries, "artist_photo", lambda name, fetch=True: f"https://photo/{name}.jpg")
    monkeypatch.setattr(countries, "wiki_image",
                        lambda query, fetch=True: asked.append(query) or ("https://wiki/city.jpg" if query == "Alkmaar"
                                                                          else ""))
    concert = {"kind": "concert", "name": "Muse", "venue": "Ziggo Dome", "image": ""}
    assert leisure_events.image_for(concert, "Alkmaar") == "https://photo/Muse.jpg"  # фото исполнителя
    assert leisure_events.image_for({"kind": "festival", "name": "W", "image": "https://own.jpg"}, "Alkmaar") == \
        "https://own.jpg"
    holiday = {"kind": "holiday", "name": "Sinterklaasavond", "image": ""}
    # Своей нет — Википедия: событие, затем город.
    assert leisure_events.image_for(holiday, "Alkmaar") == "https://wiki/city.jpg"
    assert asked == ["Sinterklaasavond", "Alkmaar"]


def test_empty_week_is_honest():
    assert leisure_ui.event_card([]).text == "🎟️ События\n\nНа этой неделе событий пока нет — загляни чуть позже."


def test_every_event_gets_a_short_description(monkeypatch):
    saved = {}
    monkeypatch.setattr(leisure_events.store, "_load", lambda key: saved.get(key))
    monkeypatch.setattr(leisure_events.store, "mutate_kv",
                        lambda key, change: saved.__setitem__(key, change(saved.get(key))[0]))
    import ai
    monkeypatch.setattr(ai, "llm_json", lambda *_a, **_k: {"items": [
        {"id": 0, "about": "Каток под открытым небом прямо у весовой палаты — с глинтвейном и огнями."}]})
    rink = {"kind": "city", "name": "Ice rink on Waagplein", "date": "2026-11-27", "place": "Waagplein", "about": ""}
    assert leisure_events.describe([rink], "Alkmaar") == 1
    assert rink["about"].startswith("Каток под открытым небом")
    assert leisure_events._saved_notes()[leisure_events._note_key(rink)] == rink["about"]  # кэш на месяц
    text = leisure_ui.event_card([rink]).text
    assert "Городское событие · 27 ноября" in text and "Каток под открытым небом" in text
    # AI не ответил — короткая подпись без AI, карточка не остаётся пустой.
    assert leisure_events._plain_about({"kind": "city", "place": "Waagplein"}, "Alkmaar") == (
        "Городское событие — Waagplein, Alkmaar.")


def test_city_event_photo_matches_its_topic_not_the_square(monkeypatch):
    import countries
    asked = []
    monkeypatch.setattr(countries, "stock_photo", lambda topic, fetch=True: asked.append(topic) or f"https://stock/{topic}")
    monkeypatch.setattr(countries, "wiki_image", lambda _q, fetch=True: "https://wiki/waag.jpg")
    rink = {"kind": "city", "name": "Ice rink on Waagplein", "place": "Waagplein",
            "image": "https://alkmaar.nl/og-image-default.jpg"}
    assert leisure_events.topic_of(rink, "Alkmaar") == "ice rink"
    assert leisure_events.image_for(rink, "Alkmaar") == "https://stock/ice rink"  # каток, а не площадь
    assert leisure_events.topic_of({"kind": "city", "name": "Kerstmarkt Alkmaar"}, "Alkmaar") == "christmas market"
    poster = {"kind": "festival", "name": "Winterfest", "image": "https://fest.nl/poster-2026.jpg"}
    assert leisure_events.image_for(poster, "Alkmaar") == "https://fest.nl/poster-2026.jpg"  # у фестиваля — афиша


def test_my_day_feed_skips_premieres_and_reads_which_may_go_online(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(leisure_events, "_premieres", lambda *_a: (_ for _ in ()).throw(AssertionError("no premieres")))
    monkeypatch.setattr(leisure_events, "_reads", lambda *_a: (_ for _ in ()).throw(AssertionError("no reads")))
    names = [card["name"] for card in leisure_events.feed("42", parts=("concerts", "city"))]
    assert names == ["Vermeer", "Muse", "Winterfest"]


def test_concert_image_never_searches_wikipedia_by_artist_name(monkeypatch):
    import countries
    asked = []
    monkeypatch.setattr(countries, "artist_photo", lambda *_a, **_k: "")
    monkeypatch.setattr(countries, "wiki_image", lambda query, fetch=True: asked.append(query) or "")
    card = {"kind": "concert", "name": "U2", "venue": "Johan Cruijff ArenA", "place": "Johan Cruijff ArenA, Amsterdam"}
    leisure_events.image_for(card, "Alkmaar")
    assert "U2" not in asked and asked[0] == "Johan Cruijff ArenA Alkmaar"


def test_deezer_photo_takes_the_most_famous_namesake(monkeypatch):
    import countries

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"name": "U2", "nb_fan": 12, "picture_xl": "https://e-cdns/artist/aaa/x.jpg"},
                             {"name": "U2", "nb_fan": 4_000_000, "picture_xl": "https://e-cdns/artist/bbb/x.jpg"}]}

    monkeypatch.setattr(countries.requests, "get", lambda *_a, **_k: Response())
    assert countries._deezer_photo("U2") == "https://e-cdns/artist/bbb/x.jpg"
