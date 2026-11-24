from datetime import date, datetime, timedelta

import cbs
import config
import meteoalarm

NOW = datetime(2026, 11, 24, 9, 0, tzinfo=config.TZ)
ALKMAAR = (52.63, 4.75)
SQUARE = "52.0,4.0 53.0,4.0 53.0,5.5 52.0,5.5"  # Северная Голландия
EAST = "52.0,6.0 53.0,6.0 53.0,7.0 52.0,7.0"


def _warning(polygon, level="3; orange; Severe", kind="1; Wind", hours=(2, 10)):
    return {"alert": {"info": [
        {"language": "nl-NL", "parameter": [], "area": []},
        {"language": "en-GB", "onset": (NOW + timedelta(hours=hours[0])).isoformat(),
         "expires": (NOW + timedelta(hours=hours[1])).isoformat(),
         "parameter": [{"valueName": "awareness_level", "value": level},
                       {"valueName": "awareness_type", "value": kind}],
         "area": [{"areaDesc": "Noord-Holland", "polygon": [polygon]}]}]}}


def test_official_knmi_warning_only_for_the_users_area(monkeypatch):
    monkeypatch.setattr(meteoalarm, "_feed", lambda _cc: [_warning(SQUARE), _warning(EAST, "2; yellow; Moderate")])
    found = meteoalarm.warnings_for(*ALKMAAR, now=NOW)
    assert [(item["level_ru"], item["type_ru"]) for item in found] == [("оранжевый", "ветер")]  # восток — не наш
    assert meteoalarm.line(found[0], now=NOW) == "Код оранжевый KNMI — ветер, с 11:00 до 19:00"
    monkeypatch.setattr(meteoalarm, "_feed", lambda _cc: None)
    assert meteoalarm.warnings_for(*ALKMAAR, now=NOW) is None  # сервис не ответил


def test_weather_warning_starts_with_the_official_knmi_line(monkeypatch):
    import weather_warn
    monkeypatch.setattr(weather_warn.store, "get_settings",
                        lambda _cid: {"city": "Alkmaar", "cc": "NL", "lat": ALKMAAR[0], "lon": ALKMAAR[1]})
    monkeypatch.setattr(meteoalarm, "_feed", lambda _cc: [_warning(SQUARE, hours=(-1, 10))])
    monkeypatch.setattr(weather_warn, "collect_context", lambda *_a: {})
    monkeypatch.setattr(weather_warn, "evaluate", lambda _ctx: [])  # свой расчёт ничего не нашёл
    msg = weather_warn.build_warning({}, "42")
    assert msg is not None and msg.text.startswith("💨 Погодное предупреждение\n\nГород: Alkmaar\n")
    assert "Причина: Код оранжевый KNMI — ветер, до" in msg.text


def test_cbs_city_facts_take_the_latest_filled_year_by_title(monkeypatch):
    saved = {}
    monkeypatch.setattr(cbs.store, "_load", lambda key: saved.get(key))
    monkeypatch.setattr(cbs.store, "mutate_kv", lambda key, change: saved.__setitem__(key, change(saved.get(key))[0]))

    def get(path):
        if path.startswith("/RegioS"):
            return [{"Key": "GM0361  ", "Title": "Alkmaar"}]
        if path.startswith("/DataProperties"):
            return [{"Key": "TotaleBevolking_1", "Title": "Totale bevolking"},
                    {"Key": "k_65Tot80Jaar_8", "Title": "65 tot 80 jaar"},
                    {"Key": "k_80JaarOfOuder_9", "Title": "80 jaar of ouder"},
                    {"Key": "Bevolkingsdichtheid_27", "Title": "Bevolkingsdichtheid"}]
        return [{"Perioden": "1995JJ00", "TotaleBevolking_1": 93011.0, "k_65Tot80Jaar_8": 8770.0,
                 "k_80JaarOfOuder_9": 2535.0, "Bevolkingsdichtheid_27": 3526.0},
                {"Perioden": "2025JJ00", "TotaleBevolking_1": 111000.0, "k_65Tot80Jaar_8": 15000.0,
                 "k_80JaarOfOuder_9": 5000.0, "Bevolkingsdichtheid_27": None}]

    monkeypatch.setattr(cbs, "_get", get)
    found = cbs.facts("Alkmaar")
    assert found[0] == "В Alkmaar живёт 111 000 человек — данные CBS за 2025 год."
    assert "Плотность населения Alkmaar — 3 526 человек на км²." in found  # 2025 пусто — последний заполненный
    assert "18% жителей Alkmaar старше 65 лет." in found
    assert cbs.fact_of_day("Alkmaar", date(2026, 11, 24)) in found  # без сети, из кэша
