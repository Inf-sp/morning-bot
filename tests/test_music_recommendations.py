import os
import asyncio
from datetime import date, timedelta

from telegram import MessageEntity

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import leisure_music
from ui import leisure as leisure_ui
from fakes import RecordingBot


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
    # Имя артиста — поиск на YouTube Music, трек — проверенная ссылка на YouTube Music.
    assert [link.url for link in links] == [
        "https://music.youtube.com/search?q=The+Neighbourhood",
        "https://music.youtube.com/watch?v=sweater123",
    ]
    assert "Полезно:" not in message.text
    assert "Первый альбом вышел в 2013 году." in message.text


def test_artist_card_links_have_short_notes_and_no_web_preview(monkeypatch):
    monkeypatch.setattr(leisure_music.youtube_tracks, "find_track_url", lambda *_args: "https://music.youtube.com/watch?v=x")
    data = asyncio.run(leisure_music._attach_track_links({
        "artist": "The Neighbourhood",
        "tracks": ["Sweater Weather", "Daddy Issues", "Afraid"],
    }))

    message = leisure_ui.artist_card(data)
    bot = RecordingBot()
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
        ("📝 Выбрать предпочтения", "music_prefs"),
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

    monkeypatch.setattr(leisure_music, "_task_for_today", lambda _key: {
        "title": "Тренировка", "track": "Gorilla", "artist": "Little Simz",
        "tag": "Уверенный грув.", "note": "Когда нужен темп.",
    })

    asyncio.run(leisure_music.send_music_task(RecordingBot(sent), "42", "workout"))

    assert "Gorilla — Little Simz" in sent[0]["text"]
    assert [(button.text, button.callback_data) for button in sent[0]["reply_markup"].inline_keyboard[0]] == [
        ("⬅️ Назад", "m_leisure"), ("#️⃣ Главная", "m_menu"),
    ]



def test_music_falls_back_to_a_real_new_release_artist_when_ai_and_local_lists_are_exhausted(monkeypatch):
    import apple_music
    calls = []
    every_local = [item["artist"] for items in leisure_music._LOCAL_ARTIST_FALLBACKS.values() for item in items]

    class Status:
        async def replace(self, text, **kwargs):
            calls.append(text)

    async def unavailable(*_args, **_kwargs):
        raise Exception("AI cooldown")

    async def same(data):
        return data

    monkeypatch.setattr(leisure_music.ai, "allm_json", unavailable)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(leisure_music.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(leisure_music.store, "get_settings", lambda _cid: {"cc": "NL"})
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: every_local)
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie", "electronic"])
    monkeypatch.setattr(leisure_music, "_with_country", same)
    monkeypatch.setattr(leisure_music, "_attach_track_links", same)
    monkeypatch.setattr(leisure_music, "_deliver_artist_card",
                        lambda _bot, _cid, msg, _kb, status: status.replace(msg.text))
    monkeypatch.setattr(apple_music, "new_releases", lambda genre, _cc: [
        {"title": "Bleeds", "artist": "Wednesday", "date": "2026-10-09", "url": "https://music.example/a"},
    ] if genre == "electronic" else [])
    import countries
    monkeypatch.setattr(countries, "artist_summary",
                        lambda name, fetch=True: "Wednesday — американская инди-рок-группа из Эшвилла.")

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert calls and "Не удалось подобрать" not in calls[0]
    assert "Wednesday" in calls[0] and "Свежий альбом «Bleeds» вышел 9 октября." in calls[0]
    assert "американская инди-рок-группа из Эшвилла" in calls[0]


def test_music_premieres_and_novelty_link_to_youtube_music():
    album = {"title": "Blue Rev", "artist": "Alvvays", "url": "https://music.apple.com/nl/album/1"}

    for message in (leisure_ui.premiere_card("music", [album]), leisure_ui.novelty_card("music", album)):
        links = [entity.url for entity in message.entities if entity.type == MessageEntity.TEXT_LINK]
        assert links == ["https://music.youtube.com/search?q=Alvvays+Blue+Rev"]  # не Apple Music


def test_music_prompt_asks_for_a_lively_verified_card_without_why(monkeypatch):
    prompts, delivered = [], []

    class Status:
        async def replace(self, text, **kwargs):
            delivered.append(text)

    async def capture(prompt, *_args, **_kwargs):
        prompts.append(prompt)
        return {"candidates": [{
            "artist": "Wednesday", "genre": "indie", "style": "инди-рок",
            "desc": "Гитары на грани развала. Голос срывается в крик.",
            "tracks": ["Bull Believer - восемь минут нарастающего шума"], "fact": "",
        }]}

    async def same(data):
        return data

    monkeypatch.setattr(leisure_music.ai, "allm_json", capture)
    monkeypatch.setattr(leisure_music, "_attach_track_links", same)
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(leisure_music.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    prompt = prompts[0]
    assert '"style"' in prompt and "2–3 предложения" in prompt and "Используй только проверяемые факты" in prompt
    assert '"why"' not in prompt and "почему" not in prompt.casefold()
    assert delivered[0].startswith("🎸 Wednesday\n\nИнди-рок") and "Почему" not in delivered[0]


def test_obscure_ai_candidate_is_skipped_for_a_well_known_one(monkeypatch):
    import countries
    delivered = []
    fans = {"Tiny Band": 4000, "Wolf Alice": 450000}

    class Status:
        async def replace(self, text, **kwargs):
            delivered.append(text)

    async def candidates(*_args, **_kwargs):
        return {"candidates": [
            {"artist": "Tiny Band", "genre": "indie", "desc": "Тихий инди."},
            {"artist": "Wolf Alice", "genre": "indie", "desc": "Гитарный рок."},
        ]}

    async def same(data):
        return data

    monkeypatch.setattr(leisure_music.ai, "allm_json", candidates)
    monkeypatch.setattr(leisure_music, "_attach_track_links", same)
    monkeypatch.setattr(countries, "artist_fans", lambda name, fetch=True: fans.get(name))
    monkeypatch.setattr(leisure_music.store, "get_list", lambda *_args: [])
    monkeypatch.setattr(leisure_music.store, "get_profile", lambda _cid: {})
    monkeypatch.setattr(leisure_music.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(leisure_music.store, "_load", lambda *_args: {})
    monkeypatch.setattr(leisure_music.store, "mutate_kv", lambda _key, change: change({})[1])
    monkeypatch.setattr(leisure_music.recommendation_stoplist, "values", lambda *_args: [])
    monkeypatch.setattr(leisure_music, "_music_styles", lambda _cid: ["indie"])

    asyncio.run(leisure_music.send_listen(object(), "42", force=True, status=Status()))

    assert "Wolf Alice" in delivered[0] and "Tiny Band" not in delivered[0]  # 4 тыс. поклонников — не «топ»


def test_most_popular_ai_candidate_wins_and_only_unknown_names_are_dropped(monkeypatch):
    import countries
    fans = {"Overmono": 8206, "Barry Can't Swim": 4965, "Nobody Knows": 120}
    monkeypatch.setattr(countries, "artist_fans", lambda name, fetch=True: fans.get(name))
    candidates = [{"artist": "Barry Can't Swim"}, {"artist": "Overmono"}, {"artist": "Nobody Knows"}]

    # Британской сцене на Deezer мало поклонников — не отсекаем, а берём самого известного.
    assert asyncio.run(leisure_music._most_popular(candidates))["artist"] == "Overmono"
    assert asyncio.run(leisure_music._most_popular(candidates[2:])) is None  # совсем безвестный
    monkeypatch.setattr(countries, "artist_fans", lambda name, fetch=True: None)  # Deezer недоступен
    assert asyncio.run(leisure_music._most_popular(candidates))["artist"] == "Barry Can't Swim"  # порядок AI
