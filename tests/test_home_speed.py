"""Главные экраны: дневной кэш без сети, фоновые прогревы и замер открытия."""
import asyncio
import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import ai
import bot
import config
import home_cache
import leisure_books
import leisure_games
import leisure_movies
import restaurant_discovery
import tracking
from ui import admin as admin_ui


def _boom(*_args, **_kwargs):
    raise AssertionError("network or AI called on cached home")


def test_cached_restaurant_is_served_without_weather_search_or_ai(monkeypatch):
    card = {"city": "Alkmaar", "name": "Roest", "map_url": "https://maps.example",
            "cached_at": datetime.now(config.TZ).isoformat()}
    monkeypatch.setattr(restaurant_discovery.store, "get_settings",
                        lambda _cid: {"city": "Alkmaar", "lat": 52.6, "lon": 4.7})
    monkeypatch.setattr(restaurant_discovery.store, "get_profile",
                        lambda _cid: {"food_restaurant_recommendation": card})
    monkeypatch.setattr(restaurant_discovery, "_good_terrace_weather", _boom)
    monkeypatch.setattr(restaurant_discovery.research, "web_search", _boom)
    monkeypatch.setattr(restaurant_discovery.ai, "llm_json", _boom)

    assert restaurant_discovery.get_restaurant("42")["name"] == "Roest"


def test_cached_movie_home_uses_saved_trailers_and_daily_content(monkeypatch):
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def local_movies(_cid, *, limit):
        return [{"id": 7, "title": "Фильм", "genres": ["drama"], "rating": 7.5,
                 "vote_count": 500, "popularity": 90, "overview": "Сюжет",
                 "trailer_url": "https://www.youtube.com/watch?v=saved"}]

    async def current(_cid):
        return {"title": "Фильм"}, {"id": 7, "kind": "movie",
                                    "trailer_url": "https://www.youtube.com/watch?v=reco"}

    monkeypatch.setattr(leisure_movies, "get_local_now_playing", local_movies)
    monkeypatch.setattr(leisure_movies, "get_current_movie", current)
    monkeypatch.setattr(leisure_movies, "_movie_city", lambda _cid: "Алкмар")
    monkeypatch.setattr(leisure_movies.tmdb, "trailer_url", _boom)
    monkeypatch.setattr(leisure_movies.requests, "get", _boom)
    monkeypatch.setattr(leisure_movies.monthly_rebuses, "for_day", _boom)
    monkeypatch.setattr(leisure_movies, "_cinema_birthday_cache_get", lambda _day: None)

    asyncio.run(leisure_movies.send_movie_now_playing(Bot(), "42"))

    assert "Фильм" in sent[0]["text"]


def test_books_warm_keeps_current_weekly_showcase(monkeypatch):
    calls = []

    async def weekly(*, refresh=False):
        calls.append(refresh)
        return []

    async def daily(*, refresh=False):
        return {}

    monkeypatch.setattr(leisure_books, "get_weekly_new_books", weekly)
    monkeypatch.setattr(leisure_books, "_daily_book_content", daily)
    monkeypatch.setattr(leisure_books, "_weekly_book_cache_get", lambda **_kw: [{}, {}, {}])

    asyncio.run(leisure_books.warm_books_home_cache("42"))

    assert calls == [False]


def test_games_warm_builds_missing_seasonal_showcase(monkeypatch):
    calls = []

    async def premieres(_cid, *, refresh=False, seasonal=False):
        calls.append(refresh)
        return [{"title": "Game"}] if refresh else []

    async def rebus(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(leisure_games, "get_game_premieres", premieres)
    monkeypatch.setattr(leisure_games, "has_seasonal_premieres_cache", lambda _cid: False)
    monkeypatch.setattr(leisure_games.monthly_rebuses, "for_day", rebus)

    assert asyncio.run(leisure_games.warm_games_home_cache("42")) is True
    assert calls == [False, True]


def _patch_warm_steps(monkeypatch, calls, probe=None):
    def step(name, is_async=True):
        if is_async:
            async def call(_cid, **_kwargs):
                calls.append(name)
                if probe:
                    probe(name)
                return True
            return call

        def sync_call(_cid, **_kwargs):
            calls.append(name)
            if probe:
                probe(name)
            return {"name": "Roest"} if name == "cooking" else True
        return sync_call

    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["42"])
    monkeypatch.setattr(bot.tracking, "has_active_actions", lambda: False)
    monkeypatch.setattr(bot.wardrobe, "warm_home_cache", step("wardrobe"))
    monkeypatch.setattr(bot.restaurant_discovery, "get_restaurant", step("cooking", False))
    monkeypatch.setattr(bot.learning, "warm_home_cache", step("learning", False))
    monkeypatch.setattr(bot.travel, "warm_home_cache", step("travel"))
    monkeypatch.setattr(bot.leisure_movies, "warm_movie_home_cache", step("cinema"))
    monkeypatch.setattr(bot.leisure_music, "warm_music_home_cache", step("music"))
    monkeypatch.setattr(bot.leisure_books, "warm_books_home_cache", step("books"))
    monkeypatch.setattr(bot.leisure_games, "warm_games_home_cache", step("games"))
    monkeypatch.setattr(bot.myday, "warm_day_cache", step("myday"))


class _Job:
    def __init__(self, data):
        self.data = data


class _Context:
    def __init__(self, data):
        self.job = _Job(data)
        self.bot = None
        self.job_queue = None


def test_retry_warm_rebuilds_only_sections_without_today_cache(monkeypatch):
    calls = []
    _patch_warm_steps(monkeypatch, calls)
    missing = {"travel", "myday"}
    monkeypatch.setattr(bot.home_cache, "is_ready", lambda section, _cid: section not in missing)

    asyncio.run(bot.job_warm_home_pages(_Context("retry")))

    assert calls == ["travel", "myday"]


def test_retry_warm_is_noop_when_all_caches_exist(monkeypatch):
    calls = []
    _patch_warm_steps(monkeypatch, calls)
    monkeypatch.setattr(bot.home_cache, "is_ready", lambda _section, _cid: True)

    asyncio.run(bot.job_warm_home_pages(_Context("retry")))

    assert calls == []


def test_retry_warm_jobs_are_scheduled_at_three_and_six(monkeypatch):
    monkeypatch.setattr(bot.config, "TELEGRAM_TOKEN", "123456:TESTTOKEN")

    jobs = {job.name: job for job in bot._build_application().job_queue.jobs()}

    assert jobs["warm_home_retry_0300"].data == "retry"
    assert jobs["warm_home_retry_0600"].data == "retry"


def test_home_warm_runs_ai_in_background_mode(monkeypatch):
    modes = []
    _patch_warm_steps(monkeypatch, [], probe=lambda _name: modes.append(ai._is_background()))

    asyncio.run(bot.job_warm_home_pages(_Context("wardrobe")))

    assert modes == [True]
    assert ai._is_background() is False


def test_background_jobs_propagate_mode_into_threads(monkeypatch):
    modes = []

    def refresh_pool():
        modes.append(ai._is_background())
        return {}

    monkeypatch.setattr(bot.tracking, "has_active_actions", lambda: False)
    monkeypatch.setattr(bot.category_news, "refresh_pool", refresh_pool)

    asyncio.run(bot.job_refresh_category_news(_Context(None)))

    assert modes == [True]
    for job in (bot.job_warm_weather_cache, bot.job_warm_movie_premieres_cache,
                bot.job_warm_book_premieres_cache, bot.job_warm_game_premieres_cache,
                bot.job_refresh_concerts_cache, bot.job_retry_dictionary_adds,
                bot.job_requested_dictionary_rechecks):
        assert hasattr(job, "__wrapped__"), job.__name__


def test_home_open_stats_line_uses_today_latency_journal(monkeypatch):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=config.TZ)
    today = now.timestamp() - 3600
    rows = [
        {"ts": today, "action": "m_movie", "duration_ms": 3100},
        {"ts": today, "action": "m_myday", "duration_ms": 400},
        {"ts": today, "action": "m_wardrobe", "duration_ms": 300},
        {"ts": today, "action": "movie_reco", "duration_ms": 9000},
        {"ts": now.timestamp() - 86400, "action": "m_books", "duration_ms": 9000},
    ]
    monkeypatch.setattr(tracking, "get_action_latencies", lambda limit=100: rows)

    assert home_cache.today_open_stats_line(now) == (
        "⏱ Разделы сегодня: медиана 0,4 с · худший 3,1 с (Кино)"
    )


def test_home_open_stats_line_is_empty_without_data(monkeypatch):
    monkeypatch.setattr(tracking, "get_action_latencies", lambda limit=100: [])

    assert home_cache.today_open_stats_line() == ""


def test_admin_home_shows_speed_line_under_version():
    msg = admin_ui.home(system_rows=[], version_line="v1.2.3",
                        speed_line="⏱ Разделы сегодня: медиана 0,4 с · худший 3,1 с (Кино)")

    assert "v1.2.3\n⏱ Разделы сегодня: медиана 0,4 с" in msg.text


def test_bounded_timeout_follows_live_action_budget():
    assert tracking.bounded_timeout(15) == 15.0
    trace = tracking.start_action("42", "Кино", "movie_reco", budget_seconds=3)
    try:
        assert 2.5 <= tracking.bounded_timeout(15) <= 3.0
    finally:
        tracking.finish_action(trace)
