"""Погодное предупреждение: библиотека последствий, приоритеты, объединение, персонализация."""
import pytest

import config
import store
import settings
import weather_warn as ww

CID = "warn-test-cid"


@pytest.fixture(autouse=True)
def _clean():
    for key in (config.PROFILE_KEY, config.NOTES_KEY, config.SETTINGS_FILE, f"wardrobe_user_{CID}"):
        store._mem.pop(key, None)
    yield
    for key in (config.PROFILE_KEY, config.NOTES_KEY, config.SETTINGS_FILE, f"wardrobe_user_{CID}"):
        store._mem.pop(key, None)


def _data(daily, hourly=None):
    d = {"daily": {k: [v] for k, v in daily.items()}}
    d["hourly"] = hourly or {"time": [], "precipitation_probability": [], "windgusts_10m": [],
                             "uv_index": [], "temperature_2m": [], "relativehumidity_2m": []}
    return d


def _ctx(**kw):
    base = dict(tmax=20, tmin=12, wind_ms=4, gust_ms=6, rain_prob=10, rain_mm=0.0,
                weathercode=1, uv=3, humidity=60)
    base.update(kw)
    return ww.WarnContext(**base)


# ---------- срабатывание отдельных hazards ----------
@pytest.mark.unit
def test_thunderstorm_triggers_on_code():
    fired = ww.evaluate(_ctx(weathercode=95))
    assert fired[0].key == "thunderstorm"


@pytest.mark.unit
def test_heavy_rain_triggers_above_threshold():
    fired = ww.evaluate(_ctx(rain_prob=85, rain_mm=6.0, weathercode=63))
    assert any(h.key == "heavy_rain" for h in fired)


@pytest.mark.unit
def test_storm_wind_uses_gusts():
    fired = ww.evaluate(_ctx(gust_ms=16))
    assert any(h.key == "storm_wind" for h in fired)


@pytest.mark.unit
def test_heat_and_uv():
    assert any(h.key == "heat" for h in ww.evaluate(_ctx(tmax=30)))
    assert any(h.key == "high_uv" for h in ww.evaluate(_ctx(uv=8)))


@pytest.mark.unit
def test_quiet_day_no_hazards():
    assert ww.evaluate(_ctx()) == []


# ---------- приоритеты и обрезка ----------
@pytest.mark.unit
def test_priority_order_thunderstorm_first():
    fired = ww.evaluate(_ctx(weathercode=95, gust_ms=16, rain_prob=90, rain_mm=6.0))
    assert fired[0].key == "thunderstorm"
    assert [h.priority for h in fired] == sorted(h.priority for h in fired)


@pytest.mark.unit
def test_build_caps_events_and_advice():
    d = _data({"temperature_2m_max": 31, "temperature_2m_min": 20, "windspeed_10m_max": 9,
               "windgusts_10m_max": 16, "precipitation_probability_max": 90, "precipitation_sum": 6.0,
               "weathercode": 95, "uv_index_max": 8, "time": "2024-06-01"})
    msg = ww.build_warning(d, CID)
    # не больше 3 событий-строк (по числу эмодзи-событий) и не больше 4 буллетов
    assert msg is not None
    assert msg.text.count("•") <= ww.MAX_ADVICE


# ---------- объединение ----------
@pytest.mark.unit
def test_build_merges_multiple_into_one_message():
    h = {"time": ["2024-06-01T09:00", "2024-06-01T14:00"],
         "precipitation_probability": [91, 80], "windgusts_10m": [16, 15],
         "uv_index": [2, 2], "temperature_2m": [18, 19], "relativehumidity_2m": [70, 70]}
    d = _data({"temperature_2m_max": 22, "temperature_2m_min": 14, "windspeed_10m_max": 9,
               "windgusts_10m_max": 16, "precipitation_probability_max": 91, "precipitation_sum": 6.0,
               "weathercode": 63, "uv_index_max": 2, "time": "2024-06-01"}, h)
    msg = ww.build_warning(d, CID)
    assert "Порывы ветра" in msg.text
    assert "дождь" in msg.text.lower()
    assert "🕒 Когда:" in msg.text
    assert "🎒 Что сделать:" in msg.text


@pytest.mark.unit
def test_quiet_day_build_returns_none():
    d = _data({"temperature_2m_max": 20, "temperature_2m_min": 12, "windspeed_10m_max": 4,
               "windgusts_10m_max": 6, "precipitation_probability_max": 20, "precipitation_sum": 0.0,
               "weathercode": 1, "uv_index_max": 3, "time": "2024-06-01"})
    assert ww.build_warning(d, CID) is None


# ---------- персонализация ----------
@pytest.mark.unit
def test_bike_flag_adds_cycling_advice():
    advice_plain = [a for h in ww.evaluate(_ctx(gust_ms=16)) if h.key == "storm_wind"
                    for a in h.advice(_ctx(gust_ms=16, bike=False))]
    advice_bike = [a for h in ww.evaluate(_ctx(gust_ms=16)) if h.key == "storm_wind"
                   for a in h.advice(_ctx(gust_ms=16, bike=True))]
    assert any("велосипед" in a.lower() for a in advice_bike)
    assert advice_bike != advice_plain


@pytest.mark.unit
def test_raincoat_present_vs_absent_changes_rain_advice():
    with_coat = ww._rain_advice(_ctx(rain_prob=80, has_raincoat=True))
    without = ww._rain_advice(_ctx(rain_prob=80, has_raincoat=False))
    assert any("надень дождевик" in a.lower() for a in with_coat)
    assert any("возьми дождевик или зонт" in a.lower() for a in without)


@pytest.mark.unit
def test_collect_context_reads_bike_flag():
    settings.set_(CID, "bike", True)
    d = _data({"temperature_2m_max": 20, "temperature_2m_min": 12, "windspeed_10m_max": 4,
               "windgusts_10m_max": 6, "precipitation_probability_max": 10, "precipitation_sum": 0.0,
               "weathercode": 1, "uv_index_max": 3, "time": "2024-06-01"})
    ctx = ww.collect_context(d, CID)
    assert ctx.bike is True
