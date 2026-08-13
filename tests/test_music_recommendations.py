import os
import asyncio
from datetime import date

from telegram import MessageEntity

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_music
from ui import leisure as leisure_ui


def test_recent_artist_history_is_unique_and_limited(monkeypatch):
    profile = {"music_recent_artists": ["The xx", "Bicep", "the xx"]}
    saved = []

    def set_profile(_cid, value):
        profile.clear()
        profile.update(value)
        saved.append(dict(value))

    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "set_profile", set_profile)

    leisure_music._remember_artist("42", "BICEP")
    leisure_music._remember_artist("42", "FKA twigs")

    assert saved[-1]["music_recent_artists"] == ["The xx", "BICEP", "FKA twigs"]


def test_music_module_has_no_learning_language_priority():
    assert not hasattr(leisure_music, "_language_music_context")
    assert not hasattr(leisure_music, "_learning_language_code")


def test_artist_tracks_link_to_youtube_music_and_keep_the_note(monkeypatch):
    monkeypatch.setattr(
        leisure_music.youtube_tracks, "find_track_url",
        lambda track, artist: "https://music.youtube.com/watch?v=sweater123"
        if (track, artist) == ("Sweater Weather", "The Neighbourhood") else "",
    )

    data = asyncio.run(leisure_music._attach_track_links({
        "artist": "The Neighbourhood",
        "tracks": ["Sweater Weather - знаковый хит"],
        "fact": "Первый альбом вышел в 2013 году.",
    }))
    message = leisure_ui.artist_card(data)
    links = [entity for entity in message.entities if entity.type == MessageEntity.TEXT_LINK]

    assert "• Sweater Weather — знаковый хит" in message.text
    assert len(links) == 1
    assert links[0].url == "https://music.youtube.com/watch?v=sweater123"
    assert "💡 Полезно:" in message.text


def test_artist_card_links_have_short_notes_and_no_web_preview(monkeypatch):
    monkeypatch.setattr(leisure_music.youtube_tracks, "find_track_url", lambda *_args: "https://music.youtube.com/watch?v=x")
    data = asyncio.run(leisure_music._attach_track_links({
        "artist": "The Neighbourhood",
        "tracks": ["Sweater Weather", "Daddy Issues", "Afraid"],
    }))

    class Bot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    message = leisure_ui.artist_card(data)
    bot = Bot()
    asyncio.run(leisure_music._deliver_artist_card(bot, "42", message, reply_markup=None))

    assert all(track["note"] for track in data["tracks"])
    assert bot.sent[0]["disable_web_page_preview"] is True


def test_music_shows_a_local_artist_when_the_ai_chain_is_unavailable(monkeypatch):
    calls = []
    profile = {}

    class Status:
        async def replace(self, text, **kwargs):
            calls.append((text, kwargs))

    async def unavailable(*_args, **_kwargs):
        raise Exception("AI cooldown")

    monkeypatch.setattr(leisure_music.ai, "allm_json", unavailable)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "set_profile", lambda _cid, value: profile.update(value))
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert len(calls) == 1
    assert "Big Thief" in calls[0][0]
    assert "Не удалось подобрать" not in calls[0][0]


def test_music_keeps_recommending_when_ai_is_unavailable_and_first_fallback_is_known(monkeypatch):
    calls = []
    profile = {}

    class Status:
        async def replace(self, text, **kwargs):
            calls.append((text, kwargs))

    async def unavailable(*_args, **_kwargs):
        raise Exception("AI cooldown")

    monkeypatch.setattr(leisure_music.ai, "allm_json", unavailable)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "set_profile", lambda _cid, value: profile.update(value))
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: ["Big Thief"])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert len(calls) == 1
    assert "Не удалось подобрать" not in calls[0][0]
    assert "Alvvays" in calls[0][0]


def test_music_home_shows_daily_rebus_and_concerts(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def daily(_cid):
        return {"rebus": {"emoji": "👑 🐝 🎤", "answer": "Beyoncé", "fact": "Факт."}}

    async def concerts(_cid):
        return [{"artist": "Romy", "date": "21 августа", "place": "Алкмар"}]

    monkeypatch.setattr(leisure_music, "_daily_music_content", daily)
    monkeypatch.setattr(leisure_music, "_weekly_concerts", concerts)
    monkeypatch.setattr(leisure_music, "_music_city", lambda _cid: "Алкмар")
    asyncio.run(leisure_music.send_music_home(Bot(), "42"))

    assert len(sent) == 1
    assert "🎧 Музыка этой недели · Алкмар" in sent[0]["text"]
    assert "Музыкальный ребус: 👑 🐝 🎤 → Beyoncé" in sent[0]["text"]
    assert [row[0].text for row in sent[0]["reply_markup"].inline_keyboard[:2]] == [
        "✨ Подобрать новую музыку", "🎭 По жанру",
    ]


def test_music_home_shows_three_nearby_concerts_as_separate_items():
    message = leisure_music.leisure_ui.music_week_screen("Алкмар", {}, [
        {"artist": "Romy", "date": "21 августа", "place": "Алкмар"},
        {"artist": "FKA twigs", "date": "3 сентября", "place": "Амстердам"},
        {"artist": "The National", "date": "14 сентября", "place": "Утрехт"},
    ])

    assert "Концерты рядом:\n• Romy - 21 августа · Алкмар" in message.text
    assert "• FKA twigs - 3 сентября · Амстердам" in message.text
    assert "• The National - 14 сентября · Утрехт" in message.text


def test_weekly_concert_loader_keeps_three_confirmed_events(monkeypatch):
    import leisure_concerts

    events = [
        {"_artist": "Romy", "dates": {"start": {"localDate": "2026-08-21"}},
         "_embedded": {"venues": [{"city": {"name": "Алкмар"}}]}},
        {"_artist": "FKA twigs", "dates": {"start": {"localDate": "2026-09-03"}},
         "_embedded": {"venues": [{"city": {"name": "Амстердам"}}]}},
        {"_artist": "The National", "dates": {"start": {"localDate": "2026-09-14"}},
         "_embedded": {"venues": [{"city": {"name": "Утрехт"}}]}},
    ]
    requested = {}
    monkeypatch.setattr(leisure_music.config, "TICKETMASTER_API_KEY", "ticketmaster-key")
    monkeypatch.setattr(leisure_music.store, "get_settings", lambda _cid: {"cc": "NL", "country": "Нидерланды"})
    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Romy", "FKA twigs", "The National"])
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_get", lambda *_args: None)

    async def fetch(artists, cc, country, **_kwargs):
        requested.update({"artists": artists, "cc": cc, "country": country})
        return events

    monkeypatch.setattr(leisure_concerts, "_fetch_concerts", fetch)
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_set", lambda *_args: None)

    result = asyncio.run(leisure_music._weekly_concerts("42"))

    assert requested == {
        "artists": ["Romy", "FKA twigs", "The National"],
        "cc": "NL",
        "country": "Нидерланды",
    }
    assert [item["artist"] for item in result] == ["Romy", "FKA twigs", "The National"]


def test_weekly_concert_loader_uses_confirmed_fallback_when_ticketmaster_is_empty(monkeypatch):
    import leisure_concerts

    events = [
        {"_artist": "Romy", "dates": {"start": {"localDate": "2026-08-21"}},
         "_embedded": {"venues": [{"city": {"name": "Лилль"}}]}},
        {"_artist": "FKA twigs", "dates": {"start": {"localDate": "2026-09-03"}},
         "_embedded": {"venues": [{"city": {"name": "Париж"}}]}},
        {"_artist": "The National", "dates": {"start": {"localDate": "2026-09-14"}},
         "_embedded": {"venues": [{"city": {"name": "Лион"}}]}},
    ]
    fallback_calls = []
    monkeypatch.setattr(leisure_music.config, "TICKETMASTER_API_KEY", "ticketmaster-key")
    monkeypatch.setattr(leisure_music.store, "get_settings", lambda _cid: {"cc": "FR", "country": "Франция"})
    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Romy", "FKA twigs", "The National"])
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_get", lambda *_args: None)
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_set", lambda *_args: None)

    async def ticketmaster(*_args, **_kwargs):
        return []

    async def fetch(artists, cc, country, **_kwargs):
        fallback_calls.append((artists, cc, country))
        return events

    monkeypatch.setattr(leisure_concerts, "_ticketmaster_events_many", ticketmaster)
    monkeypatch.setattr(leisure_concerts, "_fetch_concerts", fetch)

    result = asyncio.run(leisure_music._weekly_concerts("42"))

    assert fallback_calls == [(["Romy", "FKA twigs", "The National"], "FR", "Франция")]
    assert [item["artist"] for item in result] == ["Romy", "FKA twigs", "The National"]


def test_music_recommendation_requires_a_selected_style(monkeypatch):
    calls = []

    class Status:
        async def replace(self, text, **kwargs):
            calls.append((text, kwargs))

    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: [])
    monkeypatch.setattr(
        leisure_music.ai, "allm_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI called")),
    )

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert calls[0][0] == "Сначала отметь хотя бы один жанр в 📌 Предпочтения → Музыка."
    assert [(button.text, button.callback_data) for button in calls[0][1]["reply_markup"].inline_keyboard[0]] == [
        ("📌 Предпочтения", "set_pref_music"),
    ]


def test_music_recommendation_rejects_an_artist_outside_selected_styles(monkeypatch):
    calls = []
    profile = {}

    class Status:
        async def replace(self, text, **kwargs):
            calls.append((text, kwargs))

    async def wrong_genre(*_args, **_kwargs):
        return {"artist": "FKA twigs", "genre": "rnb"}

    monkeypatch.setattr(leisure_music.ai, "allm_json", wrong_genre)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "set_profile", lambda _cid, value: profile.update(value))
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert "Big Thief" in calls[0][0]
    assert "FKA twigs" not in calls[0][0]


def test_music_task_returns_a_usable_track(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(leisure_music, "_task_for_today", lambda _key: {
        "title": "Тренировка", "track": "Gorilla", "artist": "Little Simz",
        "tag": "Уверенный грув.", "note": "Когда нужен темп.",
    })

    asyncio.run(leisure_music.send_music_task(Bot(), "42", "workout"))

    assert "Gorilla — Little Simz" in sent[0]["text"]
    assert [(button.text, button.callback_data) for button in sent[0]["reply_markup"].inline_keyboard[0]] == [
        ("⬅️ Назад", "m_music"), ("#️⃣ Главная", "m_menu"),
    ]


def test_music_legend_does_not_retry_an_empty_daily_cache(monkeypatch):
    today = date(2026, 8, 5)
    cache = {today.isoformat(): {
        "version": leisure_music._MUSIC_LEGEND_CACHE_VERSION,
        "legend": {},
    }}
    monkeypatch.setattr(leisure_music.store, "_load", lambda _key: cache)
    monkeypatch.setattr(leisure_music.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    assert leisure_music._load_music_legend(today) == {}
