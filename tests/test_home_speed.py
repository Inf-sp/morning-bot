"""Главные экраны: дневной кэш без сети, фоновые прогревы и замер открытия."""
import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import ai
import bot
import bot_maintenance
import leisure_books
import leisure_movies
import tracking


def _boom(*_args, **_kwargs):
    raise AssertionError("network or AI called on cached home")


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
    monkeypatch.setattr(bot_maintenance.recipe_generation, "warm_cooking_home_ideas", step("cooking", False))
    monkeypatch.setattr(bot.learning, "warm_home_cache", step("learning", False))
    monkeypatch.setattr(bot_maintenance.leisure_hub, "warm_hub_cache", step("leisure"))
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
    missing = {"leisure", "myday"}
    monkeypatch.setattr(bot.home_cache, "is_ready", lambda section, _cid: section not in missing)

    asyncio.run(bot.job_warm_home_pages(_Context("retry")))

    assert calls == ["leisure", "myday"]


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
                bot.job_warm_book_premieres_cache,
                bot.job_refresh_concerts_cache, bot.job_retry_dictionary_adds,
                bot.job_requested_dictionary_rechecks):
        assert hasattr(job, "__wrapped__"), job.__name__


def test_bounded_timeout_follows_live_action_budget():
    assert tracking.bounded_timeout(15) == 15.0
    trace = tracking.start_action("42", "Кино", "movie_reco", budget_seconds=3)
    try:
        assert 2.5 <= tracking.bounded_timeout(15) <= 3.0
    finally:
        tracking.finish_action(trace)
