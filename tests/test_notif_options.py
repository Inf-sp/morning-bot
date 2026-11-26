"""Доп. настройки рассылок: что именно присылать."""
import os
from datetime import datetime

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import notif_options


def _settings(monkeypatch, saved=None):
    saved = dict(saved or {})
    import settings
    monkeypatch.setattr(settings, "get", lambda _cid, key, default=None: saved.get(key, default))
    monkeypatch.setattr(settings, "set_", lambda _cid, key, value: saved.__setitem__(key, value))
    return saved


def test_defaults_keep_old_behaviour_and_multi_keeps_at_least_one(monkeypatch):
    _settings(monkeypatch)
    assert notif_options.get("42", "weather_warn", "kinds") == ("storm", "ice", "wind", "rain", "heat", "fog")
    assert notif_options.get("42", "ns_disruptions", "level") == "all"
    assert notif_options.summary("42", "evening_weather", "blocks") == "все"
    notif_options.choose("42", "bus_disruptions", "delay", "off")
    assert notif_options.get("42", "bus_disruptions", "delay") == "off"
    notif_options.choose("42", "weekend_events", "scope", "nonsense")  # чужое значение не сохраняется
    assert notif_options.get("42", "weekend_events", "scope") == "all"


def test_weather_warning_skips_switched_off_kinds(monkeypatch):
    import weather_warn
    _settings(monkeypatch, {"notif_opt_weather_warn_kinds": ["storm"]})
    assert weather_warn.WARN_GROUPS["high_uv"] == "heat" and weather_warn.WARN_GROUPS["snow"] == "ice"
    assert set(notif_options.get("42", "weather_warn", "kinds")) == {"storm"}


def test_ns_only_severe_and_commute_hours(monkeypatch):
    import ns_alerts
    _settings(monkeypatch, {"notif_opt_ns_disruptions_level": "severe", "notif_opt_ns_disruptions_hours": "commute"})
    morning = datetime(2026, 11, 26, 8)
    assert ns_alerts.wanted("42", {"level": "поездов нет или почти нет"}, morning)
    assert not ns_alerts.wanted("42", {"level": "меньше поездов"}, morning)
    assert not ns_alerts.wanted("42", {"level": "поездов нет или почти нет"}, datetime(2026, 11, 26, 13))
    assert ns_alerts.wanted("42", {"level": ""}, morning)  # уровень неизвестен — сказать


def test_bus_only_cancellations_or_longer_delays():
    import bus_alerts
    now = datetime(2026, 11, 26, 8, tzinfo=bus_alerts.config.TZ)
    from datetime import timedelta
    passes = [{"id": "a", "status": "DRIVING", "delay_min": 12, "planned": now + timedelta(minutes=20)},
              {"id": "b", "status": "CANCEL", "delay_min": 0, "planned": now + timedelta(minutes=30)}]
    data = {"passes": passes, "messages": []}
    assert [event["kind"] for event in bus_alerts.events(data, now, 10)] == ["delay", "cancel"]
    assert [event["kind"] for event in bus_alerts.events(data, now, 15)] == ["cancel"]
    assert [event["kind"] for event in bus_alerts.events(data, now, None)] == ["cancel"]


def test_plan_hides_switched_off_blocks(monkeypatch):
    import plan
    _settings(monkeypatch, {"notif_opt_evening_weather_blocks": ["works"]})
    out = plan._only_chosen("42", {"bike": "сухо 08:00–13:00", "waste": ["бумага"], "events": [1], "premieres": [1],
                                   "releases": [1], "concerts_soon": [1], "works": [1], "works_end": [1],
                                   "weather": {"line": "x", "take": ["зонт"]}})
    assert out["bike"] == "" and out["waste"] == [] and out["events"] == [] and out["releases"] == []
    assert out["works"] == [1] and out["weather"]["take"] == []
