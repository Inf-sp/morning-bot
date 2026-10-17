"""Фоновые задачи не наследуют лимит действия; фильм, не найденный за лимит, приходит позже, а не ошибкой."""
import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import tracking
import util


def test_background_task_runs_without_the_user_action_budget():
    seen = []

    async def job():
        seen.append(tracking.remaining_action_seconds())

    async def main():
        trace = tracking.start_action("42", "leisure", "m_leisure", budget_seconds=15)
        await util.background(job(), "test")
        seen.append(tracking.remaining_action_seconds())
        tracking.finish_action(trace)

    asyncio.run(main())
    assert seen[0] is None  # фон — без лимита: TMDb-запросы не обрываются через 15 с
    assert seen[1] is not None and seen[1] > 0  # у самого действия лимит остаётся


def test_movie_not_found_in_time_is_sent_later_instead_of_an_error(monkeypatch):
    import leisure_movies
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs["text"])

    async def current(_cid, kind="movie"):
        return {"title": "Дюна"}, {"id": 1, "kind": "movie", "name": "Дюна"}

    async def card(_bot, _cid, status=None, kind="movie"):
        sent.append(f"card {kind}")

    monkeypatch.setattr(leisure_movies, "get_current_movie", current)
    monkeypatch.setattr(leisure_movies, "send_current_movie", card)

    async def main():
        await leisure_movies._deliver_later(Bot(), "later-42", "movie")
        await leisure_movies._deliver_later(Bot(), "later-42", "movie")  # повторное нажатие — без второго подбора
        await asyncio.sleep(0.01)

    asyncio.run(main())
    assert sent[0].startswith("Подбираю дольше обычного") and sent.count("card movie") == 1
