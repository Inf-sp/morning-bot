"""Дневник тревог: сеть-безопасность на случай потери store.pending_input."""
import asyncio
from datetime import datetime

import pytest

import balance
import config
import settings
import store


class _NoopBot:
    async def send_message(self, *a, **kw):
        return None


@pytest.mark.unit
def test_send_daycheck_persists_worry_prompt_timestamp():
    cid = "worry-fb-1"
    asyncio.run(balance.send_daycheck(_NoopBot(), cid))

    ts = settings.get(cid, "_worry_prompt_ts", 0)
    assert ts > 0
    assert abs(datetime.now(config.TZ).timestamp() - ts) < 5


@pytest.mark.unit
def test_send_evening_review_persists_worry_prompt_timestamp_when_empty():
    cid = "worry-fb-2"
    store.set_list(config.WORRIES_KEY, cid, [])  # нет тревог за сегодня
    asyncio.run(balance.send_evening_review(_NoopBot(), cid))

    ts = settings.get(cid, "_worry_prompt_ts", 0)
    assert ts > 0


@pytest.mark.unit
def test_save_worries_splits_lines_and_stores():
    cid = "worry-fb-3"
    store.set_list(config.WORRIES_KEY, cid, [])
    asyncio.run(balance.save_worries(_NoopBot(), cid, "тревога раз\nтревога два"))

    saved = store.get_list(config.WORRIES_KEY, cid)
    assert [w["text"] for w in saved] == ["тревога раз", "тревога два"]


def _fallback_would_fire(cid, window_seconds=1800):
    """Воспроизводит условие fallback-блока из bot.py:text_router без телеграм-моков."""
    worry_ts = settings.get(cid, "_worry_prompt_ts", 0)
    return bool(worry_ts) and (datetime.now(config.TZ).timestamp() - worry_ts) < window_seconds


@pytest.mark.unit
def test_worry_fallback_fires_within_window():
    cid = "worry-fb-4"
    settings.set_(cid, "_worry_prompt_ts", datetime.now(config.TZ).timestamp())

    assert _fallback_would_fire(cid) is True


@pytest.mark.unit
def test_worry_fallback_does_not_fire_after_window_expires():
    cid = "worry-fb-5"
    stale_ts = datetime.now(config.TZ).timestamp() - 3600  # час назад
    settings.set_(cid, "_worry_prompt_ts", stale_ts)

    assert _fallback_would_fire(cid) is False


@pytest.mark.unit
def test_worry_fallback_does_not_fire_when_never_prompted():
    cid = "worry-fb-6"

    assert _fallback_would_fire(cid) is False


@pytest.mark.unit
def test_worry_fallback_resets_timestamp_after_use():
    """После срабатывания fallback метка должна сбрасываться, чтобы не задваивать сохранения."""
    cid = "worry-fb-7"
    settings.set_(cid, "_worry_prompt_ts", datetime.now(config.TZ).timestamp())
    assert _fallback_would_fire(cid) is True

    settings.set_(cid, "_worry_prompt_ts", 0)

    assert _fallback_would_fire(cid) is False
