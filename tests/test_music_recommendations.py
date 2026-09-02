import os
import asyncio
from datetime import date, timedelta

from telegram import MessageEntity

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_music
from ui import leisure as leisure_ui


def test_recent_artist_history_is_unique_and_limited(monkeypatch):
    profile = {"music_recent_artists": ["The xx", "Bicep", "the xx"]}
    saved = []

    def mutate_profile(_cid, change):
        value, result = change(dict(profile))
        profile.clear()
        profile.update(value)
        saved.append(dict(value))
        return result

    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_music.store, "mutate_profile", mutate_profile)

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
    monkeypatch.setattr(
        leisure_music.store, "mutate_profile",
        lambda _cid, change: profile.update(change(dict(profile))[0]),
    )
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
    monkeypatch.setattr(
        leisure_music.store, "mutate_profile",
        lambda _cid, change: profile.update(change(dict(profile))[0]),
    )
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: ["Big Thief"])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert len(calls) == 1
    assert "Не удалось подобрать" not in calls[0][0]
    assert "Alvvays" in calls[0][0]


def test_music_starts_a_new_local_cycle_when_ai_and_fresh_fallbacks_are_exhausted(monkeypatch):
    calls = []
    profile = {"music_recent_artists": ["Big Thief", "Alvvays"]}

    class Status:
        async def replace(self, text, **kwargs):
            calls.append((text, kwargs))

    async def unavailable(*_args, **_kwargs):
        raise Exception("AI cooldown")

    monkeypatch.setattr(leisure_music.ai, "allm_json", unavailable)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(
        leisure_music.store, "mutate_profile",
        lambda _cid, change: profile.update(change(dict(profile))[0]),
    )
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert calls
    assert "Не удалось подобрать" not in calls[0][0]
    assert "Big Thief" in calls[0][0]


def test_music_home_shows_concert_artist_fact_instead_of_unrelated_daily_rebus(monkeypatch):
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
    assert "🎧 Музыка рядом ·" in sent[0]["text"]
    assert "Музыкальный ребус:" not in sent[0]["text"]
    assert "Beyoncé" not in sent[0]["text"]
    assert "💡 Интересно: Romy: ближайшее подтверждённое выступление" in sent[0]["text"]
    assert [row[0].text for row in sent[0]["reply_markup"].inline_keyboard[:2]] == [
        "🎸 Что послушать", "🎚️ Мои артисты",
    ]


def test_second_music_home_open_uses_complete_daily_cache(monkeypatch):
    sent, calls = [], {"daily": 0, "concerts": 0}

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def daily(_cid):
        calls["daily"] += 1
        return {"rebus": {"emoji": "🎹", "answer": "Piano", "fact": "Факт."}}

    async def concerts(_cid):
        calls["concerts"] += 1
        return [{"artist": "Romy", "date": "21 августа", "place": "Алкмар"}]

    cache = {}
    monkeypatch.setattr(leisure_music.store, "_load", lambda key: cache.get(key, {}))
    monkeypatch.setattr(
        leisure_music.store, "mutate_kv",
        lambda key, change: cache.__setitem__(key, change(cache.get(key, {}))[0]),
    )
    monkeypatch.setattr(leisure_music, "_daily_music_content", daily)
    monkeypatch.setattr(leisure_music, "_weekly_concerts", concerts)
    monkeypatch.setattr(leisure_music, "_music_city", lambda _cid: "Алкмар")
    monkeypatch.setattr(leisure_music, "_ensure_artists", lambda _cid: ["Romy"])
    monkeypatch.setattr(leisure_music.store, "get_settings", lambda _cid: {"cc": "NL"})

    asyncio.run(leisure_music.send_music_home(Bot(), "42"))
    asyncio.run(leisure_music.send_music_home(Bot(), "42"))

    assert calls == {"daily": 1, "concerts": 1}
    assert sent[0]["text"] == sent[1]["text"]


def test_music_home_shows_three_nearby_concerts_as_separate_items():
    message = leisure_music.leisure_ui.music_week_screen("Алкмар", {}, [
        {"artist": "Romy", "date": "21 августа", "place": "Алкмар",
         "description": "Тёплая&#x20;электроника\nи сильное живое шоу."},
        {"artist": "FKA twigs", "date": "3 сентября", "place": "Амстердам"},
        {"artist": "The National", "date": "14 сентября", "place": "Утрехт"},
    ], day=date(2026, 8, 25))

    assert "🎧 Музыка рядом · 25 августа" in message.text
    assert "В ближайшее время:\n• Romy (21 августа · Алкмар)" in message.text
    assert "Тёплая электроника" not in message.text
    assert "• FKA twigs (3 сентября · Амстердам)" in message.text
    assert "• The National (14 сентября · Утрехт)" in message.text


def test_music_home_shows_at_most_five_artists_and_fact_about_the_first_one():
    events = [
        {"artist": f"Артист {index}", "date": f"{index + 1} октября", "place": "Амстердам"}
        for index in range(6)
    ]
    events[0]["artist_fact"] = "Артист 0 выпустил дебютный альбом в 2024 году."

    message = leisure_music.leisure_ui.music_week_screen("Алкмар", {}, events)

    assert message.text.count("\n• Артист ") == 5
    assert "Артист 4" in message.text
    assert "Артист 5" not in message.text
    assert "💡 Интересно: Артист 0 выпустил дебютный альбом" in message.text


def test_music_home_keeps_concert_type_in_compact_preview():
    message = leisure_music.leisure_ui.music_week_screen("Алкмар", {}, [{
        "artist": "Romy", "date": "21 августа", "place": "Биддингхёйзен",
        "context": "Фестиваль · Lowlands",
    }])

    assert "• Romy (фестиваль · Lowlands · 21 августа · Биддингхёйзен)" in message.text


def test_weekly_concert_loader_keeps_three_confirmed_events(monkeypatch):
    import leisure_concerts

    today = leisure_music.datetime.now(leisure_music.config.TZ).date()
    future_dates = [
        (today + timedelta(days=offset)).isoformat()
        for offset in (1, 2, 3)
    ]
    events = [
        {"_artist": "Romy", "description": "Тёплая электроника и сильное живое шоу.",
         "dates": {"start": {"localDate": future_dates[0]}},
         "_embedded": {"venues": [{"city": {"name": "Алкмар"}}]}},
        {"_artist": "FKA twigs", "info": "Театральный поп и пластичное сценическое шоу.",
         "dates": {"start": {"localDate": future_dates[1]}},
         "_embedded": {"venues": [{"city": {"name": "Амстердам"}}]}},
        {"_artist": "The National", "dates": {"start": {"localDate": future_dates[2]}},
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
    assert "description" not in result[0]
    assert "description" not in result[1]
    message = leisure_ui.music_week_screen("Алкмар", {}, result, day=today)
    assert "• Romy (сольный концерт" in message.text
    assert "Тёплая электроника" not in message.text


def test_weekly_concert_loader_does_not_translate_descriptions(monkeypatch):
    import leisure_concerts

    event_date = (leisure_music.datetime.now(leisure_music.config.TZ).date()
                  + timedelta(days=1)).isoformat()
    event = {
        "_artist": "Romy",
        "description": "Warm electronic music and a powerful live show.",
        "dates": {"start": {"localDate": event_date}},
        "_embedded": {"venues": [{"city": {"name": "Amsterdam"}}]},
    }
    monkeypatch.setattr(leisure_music.config, "TICKETMASTER_API_KEY", "ticketmaster-key")
    monkeypatch.setattr(leisure_music.store, "get_settings", lambda _cid: {"cc": "NL", "country": "Нидерланды"})
    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Romy"])
    monkeypatch.setattr(leisure_concerts, "_concerts_cache_get", lambda *_args: [event])

    async def translate(*_args, **_kwargs):
        raise AssertionError("concert descriptions must not call AI")

    monkeypatch.setattr(leisure_music.ai, "allm_json", translate)

    result = asyncio.run(leisure_music._weekly_concerts("42"))

    assert "description" not in result[0]


def test_music_home_ignores_concert_description_without_ai():
    message = leisure_music.leisure_ui.music_week_screen("Алкмар", {}, [{
        "artist": "Romy", "description": "Warm electronic music and a powerful live show.",
    }])

    assert "Warm electronic" not in message.text


def test_weekly_concert_loader_uses_confirmed_fallback_when_ticketmaster_is_empty(monkeypatch):
    import leisure_concerts

    today = leisure_music.datetime.now(leisure_music.config.TZ).date()
    future_dates = [
        (today + timedelta(days=offset)).isoformat()
        for offset in (1, 2, 3)
    ]
    events = [
        {"_artist": "Romy", "dates": {"start": {"localDate": future_dates[0]}},
         "_embedded": {"venues": [{"city": {"name": "Лилль"}}]}},
        {"_artist": "FKA twigs", "dates": {"start": {"localDate": future_dates[1]}},
         "_embedded": {"venues": [{"city": {"name": "Париж"}}]}},
        {"_artist": "The National", "dates": {"start": {"localDate": future_dates[2]}},
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

    assert calls[0][0] == "Сначала выбери хотя бы один музыкальный жанр."
    assert [(button.text, button.callback_data) for button in calls[0][1]["reply_markup"].inline_keyboard[0]] == [
        ("🔣 Выбрать предпочтения", "music_prefs"),
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
    monkeypatch.setattr(
        leisure_music.store, "mutate_profile",
        lambda _cid, change: profile.update(change(dict(profile))[0]),
    )
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert "Big Thief" in calls[0][0]
    assert "FKA twigs" not in calls[0][0]


def test_music_selects_from_one_batch_without_retrying_ai(monkeypatch):
    delivered = []
    ai_calls = []
    profile = {}

    class Status:
        async def replace(self, text, **kwargs):
            delivered.append(text)

    async def candidates(*_args, **_kwargs):
        ai_calls.append(True)
        return {"candidates": [
            {"artist": "Wrong Genre", "genre": "rnb"},
            {
                "artist": "Alvvays", "genre": "indie", "desc": "Мелодичный инди-поп.",
                "why": ["Гитарная мелодика", "Светлее по настроению"],
                "tracks": ["Archie, Marry Me - начало"], "fact": "Группа из Канады.",
            },
        ]}

    async def no_links(data):
        return data

    monkeypatch.setattr(leisure_music.ai, "allm_json", candidates)
    monkeypatch.setattr(leisure_music, "_attach_track_links", no_links)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(
        leisure_music.store, "mutate_profile",
        lambda _cid, change: profile.update(change(dict(profile))[0]),
    )
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert ai_calls == [True]
    assert delivered and "Alvvays" in delivered[0]


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

def test_music_home_limits_to_five_nearby_concerts_and_shows_artist_fact():
    message = leisure_music.leisure_ui.music_week_screen("Алкмар", {}, [
        {"artist": "Romy", "date": "21 августа", "place": "Алкмар", "artist_fact": "Британская певица и продюсер."},
        {"artist": "FKA twigs", "date": "3 сентября", "place": "Амстердам"},
        {"artist": "The National", "date": "14 сентября", "place": "Утрехт"},
        {"artist": "Beyoncé", "date": "20 сентября", "place": "Роттердам"},
        {"artist": "Arctic Monkeys", "date": "27 сентября", "place": "Гаага"},
        {"artist": "Sixth", "date": "4 октября", "place": "Амстердам"},
    ], day=date(2026, 8, 25))

    assert "В ближайшее время:" in message.text
    assert "• Romy (21 августа · Алкмар)" in message.text
    assert "• FKA twigs (3 сентября · Амстердам)" in message.text
    assert "• The National (14 сентября · Утрехт)" in message.text
    assert "• Beyoncé (20 сентября · Роттердам)" in message.text
    assert "• Arctic Monkeys (27 сентября · Гаага)" in message.text
    assert "• Sixth (4 октября · Амстердам)" not in message.text
    assert "💡 Интересно: Британская певица и продюсер." in message.text
