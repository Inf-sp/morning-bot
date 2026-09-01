import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot


def test_external_showcases_have_automatic_refresh_jobs(monkeypatch):
    monkeypatch.setattr(bot.config, "TELEGRAM_TOKEN", "123456:TESTTOKEN")

    application = bot._build_application()
    names = {job.name for job in application.job_queue.jobs()}

    assert {
        "movie_premieres_cache_weekly",
        "book_premieres_cache_weekly",
        "game_premieres_cache_weekly",
        "concerts_cache_weekly",
    } <= names


def test_all_home_screens_have_daily_warm_jobs(monkeypatch):
    monkeypatch.setattr(bot.config, "TELEGRAM_TOKEN", "123456:TESTTOKEN")

    application = bot._build_application()
    names = {job.name for job in application.job_queue.jobs()}

    assert {
        f"warm_home_{section}_daily"
        for section, _time_label in bot._HOME_WARM_SCHEDULE
    } <= names
