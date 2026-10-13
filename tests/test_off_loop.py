import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import countries
import util
import vacation

_REAL_TABLE = countries._table  # conftest подменяет таблицу заглушкой для всех тестов


def test_guide_in_handler_does_not_block_on_network_or_ai(monkeypatch):
    scheduled = []
    monkeypatch.setattr(vacation, "state", lambda _cid: {"cc": "ZA"})
    monkeypatch.setattr(vacation.ai, "llm_json", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("AI on loop")))
    monkeypatch.setattr(vacation.requests, "get", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("net on loop")))
    monkeypatch.setattr(util, "build_off_loop", lambda key, func, *args: scheduled.append(key))

    async def handler():
        return vacation.guide("trip")

    assert asyncio.run(handler()) == {}
    assert scheduled == [("vacation_guide", "trip")]


def test_country_table_in_handler_uses_stale_copy(monkeypatch):
    scheduled = []
    monkeypatch.setattr(countries, "_table", _REAL_TABLE)
    monkeypatch.setattr(countries.store, "_load", lambda _key: {"ts": 0, "value": {"ZA": ["ЮАР", "africa", "South Africa"]}})
    monkeypatch.setattr(countries.requests, "get", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("net on loop")))
    monkeypatch.setattr(util, "build_off_loop", lambda key, func, *args: scheduled.append(key))

    async def handler():
        return countries.region("ZA")

    assert asyncio.run(handler()) == "africa"
    assert scheduled == ["countries_table"]


def test_build_off_loop_runs_once_until_retry_window(monkeypatch):
    calls = []
    monkeypatch.setattr(util, "_OFF_LOOP_STARTED", {})

    async def main():
        util.build_off_loop("k", calls.append, 1)
        util.build_off_loop("k", calls.append, 2)  # уже запущено — не дублируем
        await asyncio.sleep(0.05)

    asyncio.run(main())
    assert calls == [1]


def test_background_keeps_reference_until_done():
    async def main():
        task = util.background(asyncio.sleep(0), "test")
        assert task in util._BACKGROUND_TASKS
        await task
        await asyncio.sleep(0)
        return task in util._BACKGROUND_TASKS

    assert asyncio.run(main()) is False
