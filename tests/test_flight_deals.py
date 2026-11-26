import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import flight_deals
from fakes import RecordingBot

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Amsterdam, Netherlands to Hong Kong for only \xe2\x82\xac201 one-way</title>
<link>https://www.secretflying.com/posts/ams-hkg/?utm_source=rss</link><guid>https://www.secretflying.com/?p=1</guid></item>
<item><title>Brussels or Amsterdam to Bali, Indonesia from only \xe2\x82\xac455 roundtrip</title>
<link>https://www.secretflying.com/posts/bali/</link><guid>https://www.secretflying.com/?p=2</guid></item>
<item><title>Paris, France to Amsterdam, Netherlands for only \xe2\x82\xac30 one-way</title>
<link>https://www.secretflying.com/posts/par-ams/</link><guid>https://www.secretflying.com/?p=3</guid></item>
</channel></rss>"""


def test_parse_feed_and_filter_departures_from_amsterdam():
    items = flight_deals.parse_feed(RSS, "Secret Flying")
    assert [item["id"] for item in items] == [
        "https://www.secretflying.com/?p=1", "https://www.secretflying.com/?p=2", "https://www.secretflying.com/?p=3",
    ]
    titles = [item["title"] for item in items if flight_deals.departs_from_amsterdam(item["title"])]
    assert titles == [
        "Amsterdam, Netherlands to Hong Kong for only €201 one-way",
        "Brussels or Amsterdam to Bali, Indonesia from only €455 roundtrip",
    ]
    assert flight_deals.departs_from_amsterdam("Cheap flights from Amsterdam to Curaçao for €399")
    assert not flight_deals.departs_from_amsterdam("Cheap flights from Paris to Amsterdam for €39")


def test_deal_details_from_title():
    item = {"title": "Amsterdam, Netherlands to Hong Kong for only €201 one-way"}
    assert flight_deals.deal_details(item) == {
        "destination": "Hong Kong", "price": "€201", "trip": "в одну сторону", "error_fare": False,
    }


def test_first_check_only_remembers_then_sends_new_once(monkeypatch):
    profile = {}

    def mutate(_cid, fn):
        updated, _ = fn(dict(profile))
        profile.clear()
        profile.update(updated)

    monkeypatch.setattr(flight_deals.store, "get_profile", lambda _cid: dict(profile))
    monkeypatch.setattr(flight_deals.store, "mutate_profile", mutate)
    monkeypatch.setattr(flight_deals, "_ai_info", lambda _item: {})  # ИИ не ответил — разбор из заголовка
    monkeypatch.setattr(flight_deals.store, "_load", lambda _key: {})
    monkeypatch.setattr(flight_deals, "filters", lambda _cid: {"budget": 0, "hours": 0, "trip": "any"})
    old = {"id": "a", "title": "Amsterdam, Netherlands to Lima, Peru for only €399 roundtrip",
           "link": "https://example.com/a", "source": "Secret Flying"}
    new = {"id": "b", "title": "Amsterdam, Netherlands to Tokyo, Japan for only €450 roundtrip",
           "link": "https://example.com/b", "source": "Fly4free"}
    sent = []

    asyncio.run(flight_deals.check_user(RecordingBot(sent), "42", [old]))
    assert sent == [] and profile["flight_deals"]["primed"]

    asyncio.run(flight_deals.check_user(RecordingBot(sent), "42", [old, new]))
    asyncio.run(flight_deals.check_user(RecordingBot(sent), "42", [old, new]))
    assert len(sent) == 1
    assert sent[0]["text"].startswith("✈️ Амстердам → Tokyo, Japan\n\nЦена: от €450 · туда-обратно\nИсточник: Fly4free")
    assert sent[0]["reply_markup"].inline_keyboard[0][0].url == "https://example.com/b"


CAPE_VERDE = {"id": "cv", "title": "Exotic combination of flights 😎 🌴 Cape Verde + Recife, Brazil in one tropical "
              "trip from Amsterdam for €526", "link": "https://example.com/cv", "source": "Fly4free", "summary": ""}
CAPE_VERDE_AI = {"places": [{"city": "Кабо-Верде", "country": "Кабо-Верде", "cc": "CV", "lat": 16.7, "lon": -22.9},
                            {"city": "Ресифи", "country": "Бразилия", "cc": "BR", "lat": -8.1, "lon": -34.9}],
                 "price": 526, "currency": "EUR", "trip": "return", "dates": "февраль–май 2027"}


def test_deal_card_says_where_from_and_to_in_russian_with_price_dates_and_flight_time():
    from ui import transport as transport_ui
    info = flight_deals._clean_info(CAPE_VERDE_AI, CAPE_VERDE)
    text = transport_ui.flight_deal(CAPE_VERDE, info).text
    assert text.startswith("✈️ Амстердам → Кабо-Верде и Ресифи 🇨🇻 🇧🇷\n\n"
                           "Цена: от €526 · туда-обратно\nКогда: февраль–май 2027\nВ полёте: ~10 ч\n")
    assert "Exotic combination" not in text  # огромного английского заголовка нет


def test_filters_budget_flight_time_and_ticket_type():
    far = flight_deals._clean_info(CAPE_VERDE_AI, CAPE_VERDE)
    near = flight_deals._clean_info({"places": [{"city": "Барселона", "country": "Испания", "cc": "ES", "lat": 41.3,
                                                 "lon": 2.08}], "price": 45, "currency": "EUR", "trip": "oneway"},
                                    {"id": "b", "title": "Amsterdam to Barcelona for €45 one-way"})
    europe_cheap = {"budget": 50, "hours": 4, "trip": "any"}
    assert flight_deals.matches(near, europe_cheap) and not flight_deals.matches(far, europe_cheap)
    assert not flight_deals.matches(near, {"budget": 0, "hours": 0, "trip": "return"})
    assert flight_deals.matches(far, {"budget": 0, "hours": 0, "trip": "any"})
    assert flight_deals.flight_hours(41.3, 2.08) == 2.1  # Амстердам — Барселона: реально около 2 ч


def test_same_offer_from_two_sites_is_sent_once(monkeypatch):
    profile = {"flight_deals": {"primed": True, "seen": []}}
    monkeypatch.setattr(flight_deals.store, "get_profile", lambda _cid: dict(profile))
    monkeypatch.setattr(flight_deals.store, "mutate_profile",
                        lambda _cid, fn: profile.update(fn(dict(profile))[0]))
    monkeypatch.setattr(flight_deals, "deal_info", lambda item: flight_deals._clean_info(CAPE_VERDE_AI, item))
    monkeypatch.setattr(flight_deals, "filters", lambda _cid: {"budget": 0, "hours": 0, "trip": "any"})
    sent = []
    copy = {**CAPE_VERDE, "id": "cv2", "source": "Secret Flying"}
    asyncio.run(flight_deals.check_user(RecordingBot(sent), "42", [CAPE_VERDE, copy]))
    assert len(sent) == 1 and set(profile["flight_deals"]["seen"]) == {"cv", "cv2"}
