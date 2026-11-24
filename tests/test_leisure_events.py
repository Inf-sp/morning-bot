import asyncio
from datetime import date

import leisure_events
from ui import leisure as leisure_ui


def test_events_feed_mixes_all_kinds_by_date_with_photos(monkeypatch):
    today = date(2026, 11, 24)
    monkeypatch.setattr(leisure_events, "_today", lambda _cid: (today, "NL"))
    monkeypatch.setattr(leisure_events, "_concerts", lambda *_a: [
        {"kind": "concert", "name": "Muse", "url": "https://t/muse", "date": "2026-12-04", "end": "2026-12-04",
         "place": "Ziggo Dome, Amsterdam", "mine": True, "about": "Стадионный рок с оркестром.", "image": ""}])
    monkeypatch.setattr(leisure_events, "_city", lambda *_a: [
        {"kind": "festival", "name": "Winterfest", "url": "https://w", "date": "2026-11-29", "end": "2026-11-30",
         "place": "Hortus", "free": True, "about": "зимний фестиваль", "image": "https://img/w.jpg"},
        {"kind": "exhibition", "name": "Vermeer", "url": "", "date": "2026-11-20", "end": "2026-12-20",
         "place": "Stedelijk Museum", "free": False, "about": "", "image": ""}])
    monkeypatch.setattr(leisure_events, "_holidays", lambda *_a: [
        {"kind": "holiday", "name": "Sinterklaasavond", "url": "", "date": "2026-12-05", "end": "2026-12-05",
         "about": "вечер подарков", "image": ""}])
    monkeypatch.setattr(leisure_events, "_premieres", lambda *_a: [
        {"kind": "premiere", "premiere": "movie", "name": "Дюна", "url": "https://y", "date": "2026-11-20",
         "end": "2026-11-20", "meta": ["фантастика"], "about": "Пол Атрейдес.", "image": "https://img/d.jpg"}])

    cards = leisure_events.feed("42")
    # Уже идущее и вышедшее — как сегодня; дальше по дате.
    assert [card["name"] for card in cards] == ["Vermeer", "Дюна", "Winterfest", "Muse", "Sinterklaasavond"]

    concert = leisure_ui.event_card(cards, 3)
    assert concert.text == ("🎫 Muse\n\nКонцерт · 4 декабря 2026 · Ziggo Dome, Amsterdam · твой артист\n\n"
                            "Стадионный рок с оркестром.")
    rows = concert.reply_markup.inline_keyboard
    assert [b.callback_data for b in rows[0]] == ["lz_ev_2", "noop", "lz_ev_4"] and rows[0][1].text == "4/5"
    festival = leisure_ui.event_card(cards, 2).text
    assert festival.startswith("🎪 Winterfest\n\nФестиваль · 29–30 ноября · Hortus · бесплатно")

    import countries
    monkeypatch.setattr(countries, "artist_photo", lambda name, fetch=True: f"https://photo/{name}.jpg")
    assert asyncio.run(leisure_events.image_of(cards[3])) == "https://photo/Muse.jpg"  # фото исполнителя
    assert asyncio.run(leisure_events.image_of(cards[2])) == "https://img/w.jpg"
    assert asyncio.run(leisure_events.image_of(cards[0])) == ""


def test_empty_events_screen_is_honest():
    msg = leisure_ui.event_card([])
    assert msg.text == "🎟️ События\n\nСобытий на ближайший месяц пока нет — загляни чуть позже."
