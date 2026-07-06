"""Гардероб: погодные флаги образа, зоны, статистика, слабые места, панель состояния."""
import pytest

import config
import store
import weather
import wardrobe

CID = "wardrobe-wx-cid"


@pytest.fixture(autouse=True)
def _clean():
    for key in (config.WARDROBE_GAPS_KEY, f"wardrobe_user_{CID}", config.SETTINGS_FILE):
        store._mem.pop(key, None)
    yield
    for key in (config.WARDROBE_GAPS_KEY, f"wardrobe_user_{CID}", config.SETTINGS_FILE):
        store._mem.pop(key, None)


# ---------- погодные флаги ----------
def _hourly(probs, mms, winds, base="2024-06-01"):
    times = [f"{base}T{h:02d}:00" for h in (7, 9, 14, 21, 23)]
    return {"hourly": {"time": times,
                       "precipitation_probability": probs,
                       "precipitation": mms,
                       "windspeed_10m": winds}}


@pytest.mark.unit
def test_daytime_window_ignores_night_rain():
    # Дождь только ночью (07 и 23), в дневном окне 8–22 сухо → rain_daytime False.
    data = _hourly([80, 10, 10, 10, 90], [5, 0, 0, 0, 6], [3, 3, 3, 3, 3])
    flags = weather.daytime_outfit_weather(data, "2024-06-01", 20, 3, 90, 6.0, 61)
    assert flags["rain_daytime"] is False


@pytest.mark.unit
def test_daytime_rain_detected_in_window():
    data = _hourly([10, 70, 80, 10, 10], [0, 3, 5, 0, 0], [3, 4, 5, 3, 3])
    flags = weather.daytime_outfit_weather(data, "2024-06-01", 22, 5, 80, 6.0, 63)
    assert flags["rain_daytime"] is True
    assert flags["heavy_rain"] is True  # 6 мм/сут >= порога


@pytest.mark.unit
def test_strong_wind_flag():
    data = _hourly([0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [3, 9, 10, 5, 3])
    flags = weather.daytime_outfit_weather(data, "2024-06-01", 15, 10, 0, 0.0, 3)
    assert flags["strong_wind"] is True


@pytest.mark.unit
def test_sunny_flag_only_when_clear_hot_dry():
    data = _hourly([0, 0, 0, 0, 0], [0, 0, 0, 0, 0], [2, 2, 2, 2, 2])
    flags = weather.daytime_outfit_weather(data, "2024-06-01", 27, 2, 0, 0.0, 0)
    assert flags["sunny"] is True
    # тот же ясный код, но прохладно — не «жара»
    flags_cold = weather.daytime_outfit_weather(data, "2024-06-01", 12, 2, 0, 0.0, 0)
    assert flags_cold["sunny"] is False


# ---------- зоны ----------
@pytest.mark.unit
@pytest.mark.parametrize("cat,zone", [
    ("куртка джинсовая", "Верхняя одежда"),
    ("дождевик жёлтый", "Верхняя одежда"),
    ("ветровка", "Верхняя одежда"),
    ("футболка белая", "Верх"),
    ("джинсы синие", "Низ"),
    ("кроссовки", "Обувь"),
    ("кепка", "Аксессуары"),
    ("зонт", "Другое"),
])
def test_zone_of(cat, zone):
    assert wardrobe._zone_of(cat) == zone


def _w(zones):
    """Собирает гардероб новой схемы: {zone: {subcat: ["имя", ...]}} -> полная схема с id."""
    out = {"_v": 0, "zones": {}}
    i = 0
    for zone, subs in zones.items():
        out["zones"][zone] = {}
        for subcat, names in subs.items():
            items = []
            for name in names:
                i += 1
                items.append({"id": str(i), "name": name, "zone": zone, "subcategory": subcat,
                             "color": "", "color_secondary": None, "material": None,
                             "style": None, "season": None})
            out["zones"][zone][subcat] = items
    return out


# ---------- статистика ----------
@pytest.mark.unit
def test_wardrobe_stats_counts_by_zone():
    w = _w({"Верхняя одежда": {"Куртки": ["куртка"]},
            "Верх": {"Футболки": ["белая", "серая"]},
            "Низ": {"Джинсы": ["джинсы"]}})
    total, counts = wardrobe.wardrobe_stats(w)
    assert total == 4
    assert counts["Верхняя одежда"] == 1
    assert counts["Верх"] == 2
    assert counts["Низ"] == 1
    assert counts["Обувь"] == 0


# ---------- слабые места ----------
@pytest.mark.unit
def test_has_rain_outerwear():
    assert wardrobe._has_rain_outerwear(_w({"Верхняя одежда": {"Куртки": ["дождевик"]}})) is True
    assert wardrobe._has_rain_outerwear(_w({"Верх": {"Футболки": ["белая"]}})) is False


@pytest.mark.unit
def test_add_wardrobe_gap_dedups():
    assert wardrobe.add_wardrobe_gap(CID, "непромокаемая верхняя одежда", "дождь") is True
    assert wardrobe.add_wardrobe_gap(CID, "Непромокаемая Верхняя Одежда", "дождь") is False
    gaps = wardrobe.get_wardrobe_gaps(CID)
    assert len(gaps) == 1
    assert gaps[0]["priority"] is True


@pytest.mark.unit
def test_build_weather_rules_records_gap_when_no_rain_outer():
    flags = {"rain_daytime": True, "heavy_rain": False, "strong_wind": False, "sunny": False,
             "rain_prob": 70, "rain_mm": 3.0, "wind_ms": 4}
    w = _w({"Верх": {"Футболки": ["белая"]}})  # нет дождевой верхней одежды
    rules, gap_note = wardrobe._build_weather_rules(CID, w, flags)
    assert gap_note  # честная фраза есть
    assert "дождевик" in gap_note.lower()
    assert wardrobe.get_wardrobe_gaps(CID)  # пробел зафиксирован


@pytest.mark.unit
def test_build_weather_rules_no_gap_when_rain_outer_present():
    flags = {"rain_daytime": True, "heavy_rain": False, "strong_wind": False, "sunny": False,
             "rain_prob": 70, "rain_mm": 3.0, "wind_ms": 4}
    w = _w({"Верхняя одежда": {"Куртки": ["дождевик"]}})
    rules, gap_note = wardrobe._build_weather_rules(CID, w, flags)
    assert gap_note == ""
    assert wardrobe.get_wardrobe_gaps(CID) == []


@pytest.mark.unit
def test_build_weather_rules_resyncs_existing_gap_when_item_added():
    # Пробел записан ранее (например, был удалён дождевик), но сейчас в шкафу он снова есть.
    wardrobe.add_wardrobe_gap(CID, "непромокаемая верхняя одежда", "дождливая погода", priority=True)
    assert wardrobe.get_wardrobe_gaps(CID)
    flags = {"rain_daytime": True, "heavy_rain": False, "strong_wind": False, "sunny": False,
             "rain_prob": 70, "rain_mm": 3.0, "wind_ms": 4}
    w = _w({"Верхняя одежда": {"Куртки": ["дождевик"]}})
    wardrobe._build_weather_rules(CID, w, flags)
    assert wardrobe.get_wardrobe_gaps(CID) == []


# ---------- готовность параметров ----------
@pytest.mark.unit
def test_params_filled():
    import settings
    assert wardrobe._params_filled(CID) is False
    settings.set_(CID, "wardrobe_profile", "рост 180, минимализм")
    assert wardrobe._params_filled(CID) is True


# ---------- главный экран (рендер) ----------
@pytest.mark.unit
def test_home_screen_empty_shows_hint_and_no_ready_wording():
    from ui import wardrobe as uw
    msg = uw.home_screen(0, {z: 0 for z in wardrobe.ZONE_ORDER},
                         wardrobe.ZONE_ORDER, wardrobe.ZONE_EMOJI, False, [])
    assert "пока нет вещей" in msg.text
    assert "готов" not in msg.text.lower()
    assert "готова" not in msg.text.lower()
    assert "статус" not in msg.text.lower()


@pytest.mark.unit
def test_home_screen_filled_shows_counts_and_hides_zero_categories():
    from ui import wardrobe as uw
    counts = {"Верх": 9, "Низ": 6, "Верхняя одежда": 3, "Обувь": 5, "Аксессуары": 5, "Другое": 0}
    msg = uw.home_screen(28, counts, wardrobe.ZONE_ORDER, wardrobe.ZONE_EMOJI, True, [])
    assert "28" in msg.text
    assert "Верхняя одежда — 3" in msg.text
    assert "Другое — 0" not in msg.text
    assert "готов" not in msg.text.lower()
    assert "готова" not in msg.text.lower()


@pytest.mark.unit
def test_home_screen_missing_params_shown_without_boilerplate_phrase():
    from ui import wardrobe as uw
    counts = {"Верх": 2, "Низ": 1, "Верхняя одежда": 0, "Обувь": 1, "Аксессуары": 0, "Другое": 0}
    msg = uw.home_screen(4, counts, wardrobe.ZONE_ORDER, wardrobe.ZONE_EMOJI, False, ["👤 Мои параметры"])
    assert "👤 Мои параметры" in msg.text
    assert "для более точных рекомендаций осталось заполнить" not in msg.text.lower()
