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
    assert sent[0]["text"].startswith("✈️ Дешёвый билет из Амстердама\n\nAmsterdam → Tokyo, Japan\nот €450 · туда-обратно · Fly4free")
    assert sent[0]["reply_markup"].inline_keyboard[0][0].url == "https://example.com/b"
