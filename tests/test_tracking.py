"""Трекинг-примитивы админ-панели: errors (rolling) + activity (last_seen/счётчики)."""
import time

import pytest

import config
import store
import tracking


@pytest.fixture(autouse=True)
def _clean_store(monkeypatch):
    """Изолируем store в память на каждый тест (без БД)."""
    mem = {}
    monkeypatch.setattr(store, "_load", lambda k: mem.get(k, {}))
    monkeypatch.setattr(store, "_save", lambda k, v: mem.__setitem__(k, v))
    yield mem


# ---------- ошибки ----------

@pytest.mark.unit
def test_log_and_get_errors_newest_first():
    tracking.log_error("service", "TMDB 502", kind="502")
    tracking.log_error("llm", "all providers failed")
    errs = tracking.get_errors()
    assert errs[0]["msg"] == "all providers failed"
    assert errs[1]["source"] == "service"


@pytest.mark.unit
def test_errors_filter_by_source():
    tracking.log_error("service", "a")
    tracking.log_error("llm", "b")
    assert len(tracking.get_errors(source="llm")) == 1
    assert tracking.get_errors(source="llm")[0]["msg"] == "b"


@pytest.mark.unit
def test_errors_today_and_clear():
    tracking.log_error("app", "x")
    assert tracking.errors_today() == 1
    tracking.clear_errors()
    assert tracking.errors_today() == 0
    assert tracking.get_errors() == []


@pytest.mark.unit
def test_error_log_is_rolling(monkeypatch):
    monkeypatch.setattr(tracking, "_ERR_MAX", 5)
    for i in range(10):
        tracking.log_error("app", f"e{i}")
    errs = tracking.get_errors(limit=100)
    assert len(errs) == 5
    assert errs[0]["msg"] == "e9"


# ---------- активность ----------

@pytest.mark.unit
def test_touch_sets_last_seen_and_count():
    tracking.touch("42")
    tracking.touch("42")
    rec = tracking.get_activity("42")
    assert rec["count"] == 2
    assert rec["last_ts"] > 0
    assert len(rec["days"]) == 1  # оба в один день


@pytest.mark.unit
def test_active_count_respects_window():
    tracking.touch("1")
    # искусственно состарим второго пользователя на 3 дня
    data = store._load(config.ACTIVITY_KEY)
    data["old"] = {"last_ts": int(time.time()) - 3 * 86400, "count": 1, "days": [], "first_ts": 0}
    store._save(config.ACTIVITY_KEY, data)
    assert tracking.active_count(1) == 1
    assert tracking.active_count(7) == 2


@pytest.mark.unit
def test_avg_messages():
    tracking.touch("1")
    tracking.touch("1")
    tracking.touch("2")
    assert tracking.avg_messages() == 1.5


@pytest.mark.unit
def test_human_last_seen_variants():
    assert tracking.human_last_seen("nobody") == "Не заходил"
    data = store._load(config.ACTIVITY_KEY)
    data["fresh"] = {"last_ts": int(time.time()) - 120, "count": 1, "days": [], "first_ts": 0}
    data["old"] = {"last_ts": int(time.time()) - 20 * 86400, "count": 1, "days": [], "first_ts": 0}
    store._save(config.ACTIVITY_KEY, data)
    assert "мин назад" in tracking.human_last_seen("fresh")
    assert tracking.human_last_seen("old").startswith("Не заходил:")


@pytest.mark.unit
def test_churn_dot():
    data = store._load(config.ACTIVITY_KEY)
    data["green"] = {"last_ts": int(time.time()), "count": 1, "days": [], "first_ts": 0}
    data["red"] = {"last_ts": int(time.time()) - 30 * 86400, "count": 1, "days": [], "first_ts": 0}
    store._save(config.ACTIVITY_KEY, data)
    assert tracking.churn_dot("green") == "🟢"
    assert tracking.churn_dot("red") == "🔴"
    assert tracking.churn_dot("missing") == "🔴"
