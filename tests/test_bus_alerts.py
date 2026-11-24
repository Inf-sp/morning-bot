import asyncio
from datetime import datetime, timedelta

import bus_alerts
import config
import ovapi
from ui import transport as transport_ui

NOW = datetime(2026, 11, 24, 8, 0, tzinfo=config.TZ)


def _iso(minutes):
    return (NOW + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


RAW = {"ALKSTN": {"5000": {
    "Stop": {"TimingPointName": "Station Alkmaar"},
    "Passes": {
        "a": {"LinePublicNumber": "6", "DestinationName50": "Bergen", "TransportType": "BUS",
              "TargetDepartureTime": _iso(12), "ExpectedDepartureTime": _iso(12), "TripStopStatus": "CANCEL"},
        "b": {"LinePublicNumber": "165", "DestinationName50": "Hoorn", "TransportType": "BUS",
              "TargetDepartureTime": _iso(15), "ExpectedDepartureTime": _iso(30), "TripStopStatus": "DRIVING"},
        "c": {"LinePublicNumber": "3", "DestinationName50": "Heerhugowaard", "TransportType": "BUS",
              "TargetDepartureTime": _iso(5), "ExpectedDepartureTime": _iso(6), "TripStopStatus": "DRIVING"},
    },
    "GeneralMessages": {"m": {"MessageContent": "Halte Kanaalkade vervalt wegens werkzaamheden"}},
}}}


def test_station_stops_include_alkmaar_noord(monkeypatch):
    saved = {}
    monkeypatch.setattr(ovapi.store, "_load", lambda key: saved.get(key))
    monkeypatch.setattr(ovapi.store, "mutate_kv", lambda key, change: saved.__setitem__(key, change(saved.get(key))[0]))
    monkeypatch.setattr(ovapi, "_get", lambda _path: {
        "ALKSTN": {"TimingPointTown": "Alkmaar", "TimingPointName": "Station Alkmaar"},
        "ALKNRD": {"TimingPointTown": "Alkmaar", "TimingPointName": "Station Alkmaar Noord"},
        "ALKCTR": {"TimingPointTown": "Alkmaar", "TimingPointName": "Kanaalkade"},
        "BRG": {"TimingPointTown": "Bergen", "TimingPointName": "Station"}})
    assert ovapi.station_codes("Alkmaar") == ["ALKNRD", "ALKSTN"]  # и Alkmaar Noord


def test_bus_alert_sends_cancel_delay_and_message_once(monkeypatch):
    profile, sent = {}, []
    monkeypatch.setattr(bus_alerts.store, "get_settings", lambda _cid: {"city": "Alkmaar", "cc": "NL"})
    monkeypatch.setattr(bus_alerts.store, "get_profile", lambda _cid: profile)
    monkeypatch.setattr(bus_alerts.store, "mutate_profile", lambda _cid, change: profile.update(change(dict(profile))[0]))
    monkeypatch.setattr(bus_alerts.ovapi, "station_codes", lambda _town: ["ALKSTN"])
    monkeypatch.setattr(ovapi, "_get", lambda _path: RAW)
    monkeypatch.setattr(bus_alerts, "_translate", lambda texts: ["Остановка Kanaalkade не обслуживается из-за работ"])

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs["text"])

    asyncio.run(bus_alerts.check_user(Bot(), "42", now=NOW))
    asyncio.run(bus_alerts.check_user(Bot(), "42", now=NOW))
    assert len(sent) == 1  # второй проход — без повтора
    assert sent[0] == ("🔴 Сбой автобусов\n\nОстановка: Station Alkmaar\n"
                       "• 6 → Bergen 08:12 — отменён\n"
                       "• 165 → Hoorn 08:30 — опаздывает на 15 мин (по плану 08:15)\n"
                       "• Остановка Kanaalkade не обслуживается из-за работ.")


def test_ns_alert_lists_buses_from_the_station(monkeypatch):
    monkeypatch.setattr(ovapi, "station_codes", lambda _town: ["ALKSTN"])
    monkeypatch.setattr(ovapi, "_get", lambda _path: RAW)
    buses = ovapi.next_buses("Alkmaar", now=NOW)
    assert [bus["line"] for bus in buses] == ["3", "165"]  # отменённый 6 — нет
    text = transport_ui.ns_alert("Alkmaar", {"type": "DISRUPTION", "cause": "сбой", "title": "Hoorn - Alkmaar"},
                                 ["Hoorn - Alkmaar"], buses).text
    assert "Автобусы от вокзала: 3 → Heerhugowaard 08:06 · 165 → Hoorn 08:30 (+15 мин)" in text
