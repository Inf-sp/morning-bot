"""Хаб «🍿 Досуг»: меню, блоки из кэшей, кнопки, старые callbacks и прогрев."""
import asyncio
import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot_callbacks
import cleanup
import config
import home_cache
import leisure_concerts
import leisure_hub
import leisure_movies
import menu
import routing
import store
from ui import leisure as leisure_ui
from ui import menu as menu_ui
from fakes import RecordingBot


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def _boom(*_args, **_kwargs):
    raise AssertionError("network or AI called on hub open")


def _memory_store(monkeypatch):
    memory = {config.SETTINGS_FILE: {"42": {"cc": "NL", "city": "Alkmaar"}}}

    def load(key):
        return memory.get(key, {})

    def save(key, value):
        memory[key] = value

    def mutate(key, callback):
        data, result = callback(memory.get(key, {}))
        memory[key] = data
        return result

    monkeypatch.setattr(store, "_load", load)
    monkeypatch.setattr(store, "_save", save)
    monkeypatch.setattr(store, "mutate_kv", mutate)
    return memory


def _seed_caches(monkeypatch):
    today = datetime.now(config.TZ).date()
    soon = (today + timedelta(days=5)).isoformat()
    monkeypatch.setattr(leisure_concerts, "_ensure_artists", lambda _cid: ["Muse"])
    leisure_concerts._concerts_cache_set("42", "NL", [{
        "_artist": "Muse", "url": "https://tickets.example/muse",
        "dates": {"start": {"localDate": soon}},
        "_embedded": {"venues": [{"city": {"name": "Amsterdam"}, "country": {"countryCode": "NL"}}]},
    }])
    leisure_movies._movie_premieres_cache_set("NL", today + timedelta(days=7), [
        {"id": 1, "title": "Дюна", "trailer_url": "https://youtube.example/dune"},
    ])


def test_main_menu_has_leisure_hub_instead_of_four_sections():
    assert _labels(menu.main_menu_kb()) == [
        ["☀️ Мой день"],
        ["👔 Гардероб", "🥣 Кухня"],
        ["🧠 Обучение", "🍿 Досуг"],
        ["🎚️ Настройки"],
    ]
    assert "m_leisure" in _callbacks(menu.main_menu_kb())
    assert menu.is_main_menu_markup(menu.main_menu_kb())
    old_menu = menu_ui.ikb([
        [("☀️ Мой день", "m_myday")], [("👔", "m_wardrobe"), ("🥣", "m_food")],
        [("🧠", "m_learn")], [("🎬", "m_movie"), ("🎧", "m_music")], [("🎚️", "m_settings")],
    ])
    assert menu.is_main_menu_markup(old_menu)


def test_week_premieres_list_the_same_items_hub_shows_each_day(monkeypatch):
    import leisure_hub
    from datetime import date, timedelta

    movies = [{"title": f"Фильм {i}", "overview": "Описание.", "popularity": 10 - i} for i in range(9)]
    series = [{"kind": "tv", "name": "Суд", "network": "HBO", "overview": "Описание.", "genres": "Драма",
               "origin_country": ["US"], "url": "https://tmdb.example/tv/1"},
              {"kind": "tv", "name": "Без жанра", "overview": "Описание.", "origin_country": ["US"]},
              {"kind": "tv", "name": "Без страны", "overview": "Описание.", "genres": "Драма"}]
    monkeypatch.setattr(leisure_hub.leisure_movies, "_movie_premieres_cache_get", lambda *_a, **_k: movies)
    monkeypatch.setattr(leisure_hub, "_cached_series", lambda *_a: series)
    monkeypatch.setattr(leisure_hub, "new_music", lambda *_a: [])
    monday = date(2026, 10, 12)

    films = leisure_hub.week_data("42", "movie", day=monday + timedelta(days=2))

    assert [weekday for weekday, _item in films] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    assert [item["title"] for _weekday, item in films] == [f"Фильм {i}" for i in range(7)]  # топ-7 по популярности

    menu_screen = leisure_ui.premieres_menu()
    assert _labels(menu_screen.reply_markup) == [
        ["Новые фильмы"], ["Новые сериалы"], ["Новая музыка"], ["⬅️ Назад", "#️⃣ Главная"]]
    assert _callbacks(menu_screen.reply_markup)[:3] == ["lz_prem_movie", "lz_prem_series", "lz_prem_music"]

    items = leisure_ui.week_items("movie", films)
    card = leisure_ui.premiere_card("movie", items, 0)
    # Карточка-журнал: имя, метаданные, полное описание; ← 1/7 → по кругу, «Назад» — в меню премьер.
    assert card.text == "🎬 Фильм 0\n\nОписание."
    assert _labels(card.reply_markup) == [["◀️", "1/7", "▶️"], ["⬅️ Назад", "#️⃣ Главная"]]
    assert _callbacks(card.reply_markup) == ["lz_prem_movie_6", "noop", "lz_prem_movie_1", "lz_prem", "m_menu"]
    raw = card.text.encode("utf-16-le")
    marked = [(e.type, raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")) for e in card.entities]
    assert ("bold", "Фильм 0") in marked

    # Сериалы — свой вид; без жанра или страны не попадают.
    series_items = leisure_ui.week_items("series", leisure_hub.week_data("42", "series", day=monday))
    tab = leisure_ui.premiere_card("series", series_items)
    assert tab.text == "📺 Суд\n\nдрама · 🇺🇸 USA · HBO\n\nОписание."
    raw = tab.text.encode("utf-16-le")
    marked = [(e.type, raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")) for e in tab.entities]
    assert ("bold", "Суд") in marked and ("text_link", "Суд") in marked  # имя — жирная ссылка
    assert _labels(tab.reply_markup) == [["⬅️ Назад", "#️⃣ Главная"]]  # одна премьера — без стрелок

    album = leisure_ui.premiere_card("music", [{
        "title": "Blue Rev", "artist": "Alvvays", "genre_label": "Инди", "country_label": "🇨🇦 CA",
        "url": "https://music.example/a", "about": "Мечтательный инди-поп."}])
    assert album.text == "🎧 Alvvays — «Blue Rev»\n\nИнди · 🇨🇦 CA\n\nМечтательный инди-поп."  # сначала исполнитель
    empty = leisure_ui.premiere_card("music", [])
    assert empty.text == "🎧 Новая музыка\n\nПремьер на эту неделю пока нет."


def test_premiere_flip_replaces_photo_in_place(monkeypatch):
    import leisure_hub
    films = [("Пн", {"title": "A", "poster": "a.jpg", "overview": "Первый."}),
             ("Вт", {"title": "B", "poster": "b.jpg", "overview": "Второй."})]
    monkeypatch.setattr(leisure_hub, "week_data", lambda *_a: films)
    edited = []

    class Query:
        async def edit_message_media(self, **kwargs):
            edited.append(kwargs)

    asyncio.run(leisure_hub.send_week_premieres(object(), "42", "movie_3", q=Query()))

    assert edited[0]["media"].media == "b.jpg" and "Второй." in edited[0]["media"].caption  # 3 % 2 → вторая

def test_music_week_is_fixed_from_friday_to_thursday(monkeypatch):
    import leisure_hub
    from datetime import date, timedelta

    cid = "music-week"
    leisure_hub.store.set_profile(cid, {})
    chart = {"items": [{"title": f"Альбом {i}", "artist": f"A{i}"} for i in range(9)]}
    today = {"day": date(2026, 10, 16)}  # пятница
    styles = {"value": ["rock"]}
    monkeypatch.setattr(leisure_hub, "new_music", lambda *_a: list(chart["items"]))
    monkeypatch.setattr(leisure_hub, "_today_and_cc", lambda _cid: (today["day"], "NL"))
    monkeypatch.setattr(leisure_hub.leisure_music, "_music_styles", lambda _cid: styles["value"])

    friday = leisure_hub.weekly_music(cid, "NL", today["day"])
    chart["items"] = [{"title": "Другой", "artist": "B"}]  # чарт поменялся среди недели
    today["day"] = date(2026, 10, 22)  # четверг той же недели
    assert leisure_hub.weekly_music(cid, "NL", today["day"]) == friday
    # Прогрев «на завтра» в четверг не затирает текущий выпуск.
    assert leisure_hub.weekly_music(cid, "NL", date(2026, 10, 23))[0]["title"] == "Другой"
    assert leisure_hub.weekly_music(cid, "NL", today["day"]) == friday
    # Новая пятница — новый выпуск; чарт недоступен — остаётся прошлый.
    today["day"] = date(2026, 10, 23)
    assert leisure_hub.weekly_music(cid, "NL", today["day"])[0]["title"] == "Другой"
    chart["items"] = []
    styles["value"] = ["jazz"]
    assert leisure_hub.weekly_music(cid, "NL", today["day"])[0]["title"] == "Другой"

    # Пт — первый альбом выпуска, список «Премьеры → Музыка» начинается с пятницы.
    leisure_hub.store.set_profile(cid, {})
    chart["items"] = [{"title": f"Альбом {i}", "artist": f"A{i}"} for i in range(9)]
    today["day"] = date(2026, 10, 18)  # воскресенье
    monkeypatch.setattr(leisure_hub.leisure_movies, "_movie_premieres_cache_get", lambda *_a, **_k: [])
    monkeypatch.setattr(leisure_hub, "_cached_series", lambda *_a: [])
    rows = leisure_hub.week_data(cid, "music")
    assert [day for day, _item in rows] == ["Пт", "Сб", "Вс", "Пн", "Вт", "Ср", "Чт"]
    assert [item["title"] for _day, item in rows] == [f"Альбом {i}" for i in range(7)]


def test_concerts_button_opens_month_concerts(monkeypatch):
    import leisure_hub
    shown = []

    async def month(_cid):
        return leisure_ui.weekly_concerts_card(
            [{"title": "Placebo", "date": "2026-10-27", "venue": "Ziggo Dome", "city": "Amsterdam"}], [],
            title="🎫 Концерты на месяц")

    async def show(_bot, _cid, msg, q=None):
        shown.append(msg)

    monkeypatch.setattr(leisure_hub.leisure_concerts, "build_month_concerts_msg", month)
    monkeypatch.setattr(leisure_hub, "_show", show)
    asyncio.run(leisure_hub.send_concerts(None, "42"))

    assert shown[0].text.startswith("🎫 Концерты на месяц") and "Placebo" in shown[0].text
    assert _callbacks(shown[0].reply_markup) == ["m_leisure", "m_menu"]


def test_hub_is_ready_only_with_fresh_caches_and_movie_card(monkeypatch):
    _memory_store(monkeypatch)
    assert home_cache.is_ready("leisure", "42") is False

    _seed_caches(monkeypatch)
    monkeypatch.setattr(leisure_movies, "_cached_movie", lambda _cid: ({"title": "A"}, {}))

    assert home_cache.is_ready("leisure", "42") is True


def test_warm_runs_every_step_and_reports_a_failed_one(monkeypatch):
    calls = []

    def step(name, fail=False):
        async def call(*_args, **_kwargs):
            calls.append(name)
            if fail:
                raise RuntimeError("down")
            return []
        return call

    monkeypatch.setattr(leisure_hub, "_warm_concerts", step("concerts", fail=True))
    monkeypatch.setattr(leisure_hub, "_warm_movie_premieres", step("movies"))
    async def current(_cid, kind="movie"):
        calls.append("movie_reco" if kind == "movie" else "tv_reco")

    monkeypatch.setattr(leisure_movies, "get_current_movie", current)
    monkeypatch.setattr(leisure_hub.leisure_music, "send_listen", step("artist_reco"))
    monkeypatch.setattr(leisure_hub.apple_music, "_feed", lambda _cc: calls.append("music") or [])
    monkeypatch.setattr(leisure_hub, "_warm_series", step("series"))
    monkeypatch.setattr(leisure_hub, "_warm_album_notes", step("album_notes"))
    monkeypatch.setattr(leisure_hub, "_warm_details", step("details"))

    assert asyncio.run(leisure_hub.warm_hub_cache("42")) is False
    assert leisure_hub.failed_steps["42"] == ["концерты"]
    assert calls == ["concerts", "movies", "music", "movie_reco", "tv_reco", "artist_reco",
                     "series", "album_notes", "details"]


def test_premieres_and_library_submenus():
    library = leisure_ui.leisure_library_menu()

    assert _labels(library.reply_markup) == [
        ["🎬 Фильмы и сериалы"], ["🎧 Музыка"], ["🎫 Концерты"], ["⬅️ Назад", "#️⃣ Главная"],
    ]
    assert _callbacks(library.reply_markup) == [
        "movie_favorites", "artist_favorites", "a_concerts_find", "m_leisure", "m_menu",
    ]
    for collection in ("cinema_favorites", "music_favorite_artists"):
        assert cleanup.COLLECTIONS[collection]["back"] == "lz_lib"


def test_every_hub_and_submenu_button_has_a_route():
    markups = (
        leisure_ui.leisure_hub_kb(),
        leisure_ui.premieres_menu().reply_markup,
        leisure_ui.premiere_card("movie", [{"title": "A"}, {"title": "B"}]).reply_markup,
        leisure_ui.leisure_library_menu().reply_markup,
    )
    for data in {data for markup in markups for data in _callbacks(markup)}:
        assert len(data.encode()) <= 64
        assert routing.resolve_callback_handler(data)["handled"], data


def _dispatch(monkeypatch, data, module, name):
    calls = []

    async def handler(*args, **kwargs):
        calls.append((args, kwargs))
        status = kwargs.get("status")
        if status is not None:
            await status.replace("ok")

    class Status:
        async def replace(self, *_args, **_kwargs):
            return None

        async def stop(self, delete=True):
            return None

    async def start_inline(*_args, **_kwargs):
        return Status()

    monkeypatch.setattr(module, name, handler)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    query = type("Query", (), {
        "data": data, "message": type("Message", (), {"chat_id": "42", "message_id": 7})(),
    })()
    update = type("Update", (), {"callback_query": query})()
    asyncio.run(bot_callbacks.handle(update, type("Context", (), {"bot": object()})(), None))
    return calls


@pytest.mark.parametrize("data, module, name", [
    ("m_leisure", leisure_hub, "send_hub"),
    ("lz_prem", leisure_hub, "send_week_premieres"),
    ("tv_reco", leisure_movies, "send_current_movie"),
    ("tv_next", leisure_movies, "send_recos"),
    ("tv_g_10765", leisure_movies, "send_movie_by_genre"),
    ("tv_no_0", leisure_movies, "movie_dislike"),
    ("lz_prem_movie", leisure_hub, "send_week_premieres"),
    ("lz_lib", leisure_hub, "send_library_menu"),
    ("movie_reco", leisure_movies, "send_current_movie"),
    ("movie_next", leisure_movies, "send_recos"),
    ("music_reco", bot_callbacks.leisure_music, "send_listen"),
    ("music_next", bot_callbacks.leisure_music, "listen_next"),
    # Старые кнопки игр из истории чата открывают хаб.
    ("vg_reco", leisure_hub, "send_hub"),
    ("vg_premieres", leisure_hub, "send_hub"),
    ("nov_game", leisure_hub, "send_hub"),
])
def test_hub_buttons_route_to_existing_flows(monkeypatch, data, module, name):
    assert len(_dispatch(monkeypatch, data, module, name)) == 1


@pytest.mark.parametrize("data", [
    "m_movie", "m_music", "m_books", "m_games", "movie_now_playing", "a_watch", "a_read", "a_listen",
    "book_reco", "book_favorites", "bfg:tok:0:0",
])
def test_old_section_callbacks_open_the_hub(monkeypatch, data):
    assert len(_dispatch(monkeypatch, data, leisure_hub, "send_hub")) == 1


def test_what_to_watch_opens_cached_daily_card_without_new_pick(monkeypatch):
    sent = []

    async def card(_bot, _cid, it, _i, tm=None, status=None, **_kwargs):
        sent.append((it["title"], tm["name"]))

    monkeypatch.setattr(leisure_movies, "_cached_movie", lambda *_args: ({"title": "Дюна"}, {"name": "Dune"}))
    monkeypatch.setattr(leisure_movies, "send_recos", _boom)
    monkeypatch.setattr(leisure_movies, "_send_movie_card", card)
    monkeypatch.setattr(leisure_movies.movie_engine, "mark_shown", lambda *_args: None)

    asyncio.run(leisure_movies.send_current_movie(object(), "42"))

    assert sent == [("Дюна", "Dune")]


def test_new_music_follows_music_styles_with_one_album_per_artist(monkeypatch):
    import leisure_hub
    from datetime import date

    rows = [
        {"name": "A1", "artistName": "Alvvays", "releaseDate": "2026-10-05", "genres": [{"genreId": "20"}]},
        {"name": "A2", "artistName": "Alvvays", "releaseDate": "2026-10-01", "genres": [{"genreId": "20"}]},
        {"name": "P1", "artistName": "Pop Star", "releaseDate": "2026-10-07", "genres": [{"genreId": "14"}]},
        {"name": "R1", "artistName": "Rocker", "releaseDate": "2026-10-08", "genres": [{"genreId": "21"}]},
    ]
    monkeypatch.setattr(leisure_hub.apple_music, "_feed", lambda _cc: rows)
    monkeypatch.setattr(leisure_hub.leisure_music, "_music_styles", lambda _cid: ["indie", "pop"])

    music = leisure_hub.new_music("42", "NL", date(2026, 10, 9))

    assert [(item["title"], item["genre_label"]) for item in music] == [("P1", "Поп"), ("A1", "Инди")]


def test_album_descriptions_are_stored_once(monkeypatch):
    import leisure_hub
    saved, calls = {}, []
    monkeypatch.setattr(leisure_hub.store, "_load", lambda _key: dict(saved))

    def mutate(_key, change):
        data, _ = change(dict(saved))
        saved.clear()
        saved.update(data)

    async def ai_ok(*_a, **_k):
        calls.append(1)
        return {"items": [{"id": 0, "about": "Мечтательный инди-поп"}]}

    monkeypatch.setattr(leisure_hub.store, "mutate_kv", mutate)
    monkeypatch.setattr(leisure_hub.ai, "allm_json", ai_ok)
    album = {"title": "Blue Rev", "artist": "Alvvays"}
    asyncio.run(leisure_hub.describe_albums([album]))
    asyncio.run(leisure_hub.describe_albums([album]))

    assert leisure_hub._album_notes()["alvvays|blue rev"] == "Мечтательный инди-поп." and calls == [1]


def test_hub_shows_most_popular_on_monday_then_next_each_day():
    import leisure_hub
    from datetime import date
    movies = [{"title": f"Фильм {i}", "popularity": i} for i in range(10)]
    monday = date(2026, 10, 12)
    picks = [leisure_hub._daily(movies, date.fromordinal(monday.toordinal() + d), 1, leisure_hub._popularity)[0]["title"]
             for d in range(7)]
    assert picks == [f"Фильм {i}" for i in (9, 8, 7, 6, 5, 4, 3)]  # топ-7 недели по популярности


def test_old_series_list_without_genres_is_rebuilt():
    import leisure_hub
    from datetime import date

    today = date(2026, 10, 13)
    old = {"date": today.isoformat(), "items": [{"name": "Суд"}]}
    leisure_hub.store.set_profile("series-v", {"hub_series": old})
    assert leisure_hub._cached_series("series-v", today) is None
    leisure_hub.store.set_profile("series-v", {"hub_series": {**old, "v": leisure_hub._SERIES_VERSION}})
    assert leisure_hub._cached_series("series-v", today) == [{"name": "Суд"}]


def test_series_card_and_genre_menu_stay_in_series():
    card = leisure_movies._movie_kb(3, kind="tv")
    assert _labels(card)[:2] == [["Не интересно"], ["По жанру"]]
    assert _callbacks(card)[:2] == ["tv_no_3", "tv_pick_3"]
    menu = leisure_movies._movie_genre_menu_kb(back="tv_card_3", kind="tv")
    assert _callbacks(menu) == ["tv_g_35", "tv_g_18", "tv_g_10765", "tv_g_9648", "tv_g_80", "tv_g_10759", "tv_g_16",
                                "tv_card_3", "m_menu"]
    for data in _callbacks(card) + _callbacks(menu):
        assert routing.resolve_callback_handler(data)["handled"], data


def test_week_tab_without_country_fetches_it_in_background(monkeypatch):
    import leisure_hub
    calls = []

    def week(_cid, kind, _day=None, fetch=False):
        calls.append((kind, fetch))
        return [("Пт", {"title": "Blue Rev", "artist": "Alvvays", "about": "Мечтательный инди-поп."})]

    async def show(*_args):
        return None

    monkeypatch.setattr(leisure_hub, "week_data", week)
    monkeypatch.setattr(leisure_hub, "_show_card", show)

    async def main():
        await leisure_hub.send_week_premieres(None, "42", "music")
        for _ in range(5):
            await asyncio.sleep(0.01)

    asyncio.run(main())
    assert calls == [("music", False), ("music", True)]  # открытие — из кэша, страна — догрузка в фоне


def _today_picks(monkeypatch, *, rain=False, concert=True):
    import leisure_hub
    from datetime import date
    film = ({"title": "Восставший"}, {"id": 1, "kind": "movie", "name": "Восставший", "year": "2026", "countries": ["DE"],
                                     "genres": "история, драма",
                                     "runtime": 112, "overview": "Историческая драма о восстании. Второе. Третье.",
                                     "because": "Гладиатор", "trailer_url": "https://y.example/t"})
    series = ({"title": "Нили"}, {"id": 2, "kind": "tv", "name": "Нили", "release_date": "2025-09-12", "origin_country": ["US"],
                                 "genres": "драма", "network": "Prime Video",
                                 "episode_runtime": 45, "url": "https://tmdb.example/tv/2"})
    monkeypatch.setattr(leisure_hub.leisure_movies, "_cached_movie",
                        lambda _cid, kind="movie", stale=False: film if kind == "movie" else series)
    monkeypatch.setattr(leisure_hub.leisure_music, "_cached_artist",
                        lambda _cid, stale=False: {"artist": "Focus Group", "genre": "indie", "country_label": "🇬🇧 UK",
                                                   "why": ["Как Bonobo, но теплее"]})
    monkeypatch.setattr(leisure_hub, "_evening_rain", lambda *_a: rain)
    events = [{"dates": {"start": {"localDate": "2026-10-13"}}, "name": "Muse", "url": "https://t.example/m",
               "_embedded": {"venues": [{"name": "Ziggo Dome", "city": {"name": "Amsterdam"}}]}}] if concert else []
    monkeypatch.setattr(leisure_hub.leisure_concerts, "_concerts_cache_get", lambda *_a: events)
    monkeypatch.setattr(leisure_hub.leisure_concerts, "_not_a_concert", lambda _e: False)
    monkeypatch.setattr(leisure_hub.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": "NL"})
    monkeypatch.setattr(leisure_hub.store, "get_list", lambda key, _cid: ["a"] * {
        leisure_hub.config.FAVORITE_MOVIES_KEY: 55}.get(key, 42))
    monkeypatch.setattr(leisure_hub.movie_recommendation, "runtime_limit", lambda _kind, day=None: 130)
    return leisure_hub.today_data("42", day=date(2026, 10, 13))


def test_today_screen_is_a_plan_with_main_film_alternatives_outing_and_stats(monkeypatch):
    data = _today_picks(monkeypatch)
    msg = leisure_ui.leisure_today_screen(data["picks"], city=data["city"], outing=data["outing"],
                                          library=data["library"])

    assert msg.text == (
        "🍿 Досуг на сегодня · Alkmaar\n\n"
        "Восставший · 2026 · история · драма · DE · 1 ч 52 мин • Историческая драма о восстании. Второе.\n\n"
        "Если не хочется кино:\n"
        "- Нили · сериал · 2025 · Prime Video · драма · USA\n"  # страна кратко, без флага
        "- Focus Group · музыкальный альбом · UK\n\n"
        "Если захочется выйти:\n"
        "Muse — сегодня, Ziggo Dome\n\n"
        "Твоя библиотека · 55 фильмов · 42 артиста"
    )
    raw = msg.text.encode("utf-16-le")
    marked = [(e.type, raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le")) for e in msg.entities]
    assert ("text_link", "Восставший") in marked and ("bold", "Нили") in marked
    assert [e.type for e in msg.entities][-1] == "italic"


def test_rain_hides_outing_and_premieres_button_is_first(monkeypatch):
    data = _today_picks(monkeypatch, rain=True)
    assert data["outing"] is None  # вечером дождь — прогулку не советуем

    # Без фильма главной становится первая карточка — без описания строка не ломается.
    msg = leisure_ui.leisure_today_screen(data["picks"][1:], city="Alkmaar")
    assert msg.text.startswith("🍿 Досуг на сегодня · Alkmaar\n\nНили · 2025 · Prime Video")
    assert "Другие варианты:\n- Focus Group · музыкальный альбом · UK" in msg.text

    kb = leisure_ui.leisure_hub_kb()
    assert _labels(kb)[:3] == [["🆕 Премьеры"], ["🎬 Фильм", "📺 Сериал", "🎧 Музыка"], ["🎫 Концерты"]] and _callbacks(kb)[0] == "lz_prem"
    assert "✨ Другой вариант" not in sum(_labels(kb), [])
    assert routing.resolve_callback_handler("lz_today_2")["handled"]  # старая кнопка из истории — хаб


def test_hub_open_reads_caches_without_network_or_ai(monkeypatch):
    import leisure_hub
    _today_picks(monkeypatch, concert=False)
    monkeypatch.setattr(leisure_hub.ai, "allm_json", _boom)
    import local_events
    monkeypatch.setattr(local_events, "events", lambda _cid, fetch=True: [])
    bot = RecordingBot()

    asyncio.run(leisure_hub.send_hub(bot, "42"))

    assert bot.sent[0]["text"].startswith("🍿 Досуг на сегодня · Alkmaar\n\nВосставший · 2026")
    assert "Если захочется выйти" not in bot.sent[0]["text"]
    assert _callbacks(bot.sent[0]["reply_markup"])[0] == "lz_prem"


def test_runtime_label_and_weekday_limit():
    from datetime import date
    import movie_recommendation
    assert leisure_ui.runtime_label(112) == "1 ч 52 мин"
    assert leisure_ui.runtime_label(45, episode=True) == "серия 45 мин"
    assert leisure_ui.runtime_label(120) == "2 ч" and leisure_ui.runtime_label(0) == ""
    assert movie_recommendation.runtime_limit("movie", date(2026, 10, 15)) == 130  # четверг
    assert movie_recommendation.runtime_limit("tv", date(2026, 10, 16)) is None  # пятница — без ограничения
    assert movie_recommendation.fits_runtime({"runtime": 150}, 130) is False
    assert movie_recommendation.fits_runtime({}, 130) is True  # неизвестная длительность не отсекает


def test_day_cards_stay_a_week_as_reserve_when_refresh_fails(monkeypatch):
    memory = _memory_store(monkeypatch)
    today = datetime.now(config.TZ).date()
    monkeypatch.setattr(leisure_hub.leisure_music, "_music_styles", lambda _cid: ["indie"])
    for age, expected in ((3, "Wolf Alice"), (config.RECO_STALE_DAYS, None)):
        memory[config.MUSIC_RECO_CACHE_KEY] = {"42": {
            "date": (today - timedelta(days=age)).isoformat(), "styles": ["indie"], "item": {"artist": "Wolf Alice"}}}
        assert leisure_hub.leisure_music._cached_artist("42") is None  # не сегодняшний — прогрев соберёт новый
        assert (leisure_hub.leisure_music._cached_artist("42", stale=True) or {}).get("artist") == expected


def test_music_line_shows_the_latest_album_and_its_date(monkeypatch):
    import countries
    from datetime import date
    albums = {"Focus Group": {"title": "Blue Rev", "date": "2022-10-07"}}
    monkeypatch.setattr(countries, "artist_latest_album", lambda name, fetch=True: albums.get(name, {}))
    data = _today_picks(monkeypatch)

    assert "- Focus Group · музыкальный альбом · UK · «Blue Rev» · 2022" in leisure_ui.leisure_today_screen(data["picks"]).text
    this_year = date.today().replace(month=1, day=9).isoformat()
    albums["Focus Group"] = {"title": "Signal", "date": this_year}
    data = _today_picks(monkeypatch)
    assert "· «Signal» · 9 января" in leisure_ui.leisure_today_screen(data["picks"]).text  # в этом году — дата


def test_premiere_of_day_picks_one_todays_premiere_by_personal_priority(monkeypatch):
    import leisure_hub
    from datetime import date
    today = date(2026, 10, 16)
    movies = [{"title": "Дюна", "date": "2026-10-16", "overview": "x"}, {"title": "Вчера", "date": "2026-10-15"}]
    series = [{"name": "Медведь", "release_date": "2026-10-16", "season_number": 5, "favorite": True,
               "genres": "драма", "origin_country": ["US"]}]
    albums = [{"artist": "Alvvays", "title": "Blue Rev", "date": "2026-10-16"}]
    monkeypatch.setattr(leisure_hub, "_pools", lambda *_a, **_k: {
        "movies": (movies, None, 0), "series": (series, None, 0)})
    monkeypatch.setattr(leisure_hub.store, "get_profile", lambda _cid: {"hub_music": {"items": albums}})
    monkeypatch.setattr(leisure_hub.leisure_music, "_ensure_artists", lambda _cid: ["Alvvays"])

    # Новый сезон любимого сериала — важнее всего.
    assert leisure_hub.premiere_of_day("42", today)["name"] == "Медведь"
    series[0]["favorite"] = False
    pick = leisure_hub.premiere_of_day("42", today)
    assert (pick["kind"], pick["artist"]) == ("music", "Alvvays")  # затем альбом любимого артиста
    monkeypatch.setattr(leisure_hub.leisure_music, "_ensure_artists", lambda _cid: [])
    assert leisure_hub.premiere_of_day("42", today)["title"] == "Дюна"  # затем фильм в кино
    assert leisure_hub.premiere_of_day("42", date(2026, 10, 20)) is None  # не сегодня — строки нет

    # Релиз любимого артиста с Deezer (не из чарта) — тоже премьера дня.
    profile = {"hub_music": {"items": []}, "hub_music_fav": {"date": "2026-10-16", "items": [
        {"artist": "Tokio Hotel", "title": "ENCORE", "date": "2026-10-16", "favorite": True}]}}
    monkeypatch.setattr(leisure_hub.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_hub, "_pools", lambda *_a, **_k: {"movies": ([], None, 0), "series": ([], None, 0)})
    monkeypatch.setattr(leisure_hub.leisure_music, "_ensure_artists", lambda _cid: ["Tokio Hotel"])
    assert leisure_hub.premiere_of_day("42", today)["title"] == "ENCORE"


def test_premiere_line_in_my_day():
    from ui import myday as myday_ui
    movie = myday_ui.day_summary("Пт, 16 октября", "Alkmaar", premiere={
        "kind": "movie", "title": "Уличный боец", "genres": "боевик, триллер", "country_label": "🇺🇸 USA",
        "trailer_url": "https://www.youtube.com/watch?v=t"})
    season = myday_ui.day_summary("Пт", "Alkmaar", premiere={"kind": "series", "name": "Медведь", "season_number": 5})
    album = myday_ui.day_summary("Пт", "Alkmaar", premiere={"kind": "music", "artist": "Alvvays", "title": "Blue Rev",
                                                            "genre_label": "Инди", "country_label": "🇨🇦 CA"})

    assert movie.text.endswith("🎬 Премьера в кино: Уличный боец · боевик · триллер · USA")
    assert any(e.type == "text_link" and e.url == "https://www.youtube.com/watch?v=t" for e in movie.entities)
    assert season.text.endswith("📺 Премьера сериала: Медведь · 5-й сезон")
    assert album.text.endswith("🎧 Новый альбом: Alvvays — «Blue Rev» · Инди · CA")
    assert any(e.type == "text_link" and "music.youtube.com" in e.url for e in album.entities)
    assert "Премьера" not in myday_ui.day_summary("Пт", "Alkmaar").text


def test_today_screen_shows_country_without_flag_emoji(monkeypatch):
    data = _today_picks(monkeypatch)
    text = leisure_ui.leisure_today_screen(data["picks"], city=data["city"]).text
    assert "🇩🇪" not in text and "🇺🇸" not in text and "🇬🇧" not in text  # страна есть, флагов нет
    assert "· DE ·" in text and "· USA\n" in text and "альбом · UK" in text


def test_artist_releases_keep_fresh_albums_eps_and_singles_of_the_week(monkeypatch):
    import apple_music
    from datetime import date
    answers = {
        "search/artist": {"data": [{"id": 9, "name": "Tokio Hotel Tribute"}, {"id": 7, "name": "Tokio Hotel"}]},
        "artist/7/albums": {"data": [
            {"title": "ENCORE", "release_date": "2026-10-16", "record_type": "album", "link": "u1"},
            {"title": "Run, Run, Run", "release_date": "2026-10-12", "record_type": "single"},
            {"title": "Best of Tokio Hotel", "release_date": "2026-10-14", "record_type": "compile"},
            {"title": "Live in Berlin (Live)", "release_date": "2026-10-14", "record_type": "album"},
            {"title": "2001", "release_date": "2022-11-18", "record_type": "album"},
            {"title": "Next Year", "release_date": "2027-01-22", "record_type": "album"},
        ]},
    }
    monkeypatch.setattr(apple_music, "_deezer_json", lambda path, _params: answers[path])

    releases = apple_music.artist_releases("tokio hotel", date(2026, 10, 10), date(2026, 10, 16))

    assert [(item["title"], item["genre_label"]) for item in releases] == [("ENCORE", ""), ("Run, Run, Run", "сингл")]
    assert releases[0]["artist"] == "Tokio Hotel" and releases[0]["favorite"] is True  # имя — как на Deezer


def test_week_music_puts_favorite_releases_first_and_chart_fills_the_rest(monkeypatch):
    import leisure_hub
    from datetime import date
    profile = {"hub_music_fav": {"date": "2026-10-16", "items": [
        {"artist": "Tokio Hotel", "title": "ENCORE", "date": "2026-10-16"},
        {"artist": "Elderbrook", "title": "Lost in the View", "date": "2026-10-13"},
        {"artist": "Old Friend", "title": "Old", "date": "2026-09-01"},  # старше недели — нет
    ]}}
    monkeypatch.setattr(leisure_hub.store, "get_profile", lambda _cid: profile)
    chart = [{"artist": f"Chart {i}", "title": f"A{i}"} for i in range(7)] + [{"artist": "Elderbrook", "title": "X"}]

    week = leisure_hub._with_favorites("42", chart, date(2026, 10, 16))

    assert [item["artist"] for item in week] == ["Tokio Hotel", "Elderbrook", "Chart 0", "Chart 1", "Chart 2",
                                                "Chart 3", "Chart 4"]  # любимые первыми, всего 7
    pick = leisure_hub._with_favorites("42", [], date(2026, 10, 16))
    assert [item["title"] for item in pick] == ["ENCORE", "Lost in the View"]


def test_favorite_releases_warm_once_a_day_and_keeps_old_list_when_deezer_is_down(monkeypatch):
    import apple_music
    import leisure_hub
    from datetime import date
    profile = {"hub_music_fav": {"date": "2026-10-15", "items": [{"artist": "Old", "title": "Kept"}]}}
    monkeypatch.setattr(leisure_hub, "_today_and_cc", lambda _cid: (date(2026, 10, 16), "NL"))
    monkeypatch.setattr(leisure_hub.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_hub.store, "mutate_profile",
                        lambda _cid, change: profile.update(change(dict(profile))[0]))
    monkeypatch.setattr(leisure_hub.leisure_music, "_ensure_artists", lambda _cid: ["Elderbrook", "Tokio Hotel"])
    monkeypatch.setattr(leisure_hub, "_DEEZER_PAUSE", 0)

    def down(*_args):
        raise RuntimeError("deezer error")

    monkeypatch.setattr(apple_music, "artist_releases", down)
    asyncio.run(leisure_hub._warm_favorite_releases("42"))
    assert profile["hub_music_fav"]["items"] == [{"artist": "Old", "title": "Kept"}]  # сбой — прежний список

    releases = {"Elderbrook": [{"artist": "Elderbrook", "title": "Lost in the View", "date": "2026-10-13"}],
                "Tokio Hotel": [{"artist": "Tokio Hotel", "title": "ENCORE", "date": "2026-10-16"}]}
    monkeypatch.setattr(apple_music, "artist_releases", lambda artist, _since, _until: releases[artist])
    asyncio.run(leisure_hub._warm_favorite_releases("42"))
    assert profile["hub_music_fav"] == {"date": "2026-10-16", "items": [releases["Tokio Hotel"][0],
                                                                       releases["Elderbrook"][0]]}  # свежие первыми


def test_music_premieres_check_favorite_releases_in_background_once(monkeypatch):
    import leisure_hub
    from datetime import date
    started = []
    profile = {}
    monkeypatch.setattr(leisure_hub, "_today_and_cc", lambda _cid: (date(2026, 10, 16), "NL"))
    monkeypatch.setattr(leisure_hub.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(leisure_hub.util, "background", lambda coro, _name: started.append(coro.close()))

    leisure_hub._refresh_favorite_releases("42")
    leisure_hub._refresh_favorite_releases("42")  # листание, пока проверка идёт, — второй не запускаем
    assert len(started) == 1
    leisure_hub._FAVORITES_WARMING.clear()
    profile["hub_music_fav"] = {"date": "2026-10-16", "items": []}
    leisure_hub._refresh_favorite_releases("42")  # сегодня уже проверено
    assert len(started) == 1
