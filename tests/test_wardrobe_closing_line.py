"""Нижняя строка Гардероба: пробел в шкафу под погоду → давно не надетая вещь → «Твой шкаф · N вещей»."""
import os
from datetime import date

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import wardrobe
from ui import wardrobe as wardrobe_ui

FROST = {"tmin": -6, "tmax": -1, "feels_min": -9, "feels_max": -3, "has_rain": False, "strong_wind": False}
MILD = {"tmin": 9, "tmax": 14, "feels_min": 8, "feels_max": 13, "has_rain": False, "strong_wind": False}


def _w(*items):
    zones = {}
    for zone, name, extra in items:
        zones.setdefault(zone, {}).setdefault("x", []).append({"id": name, "name": name, "warmth": "обычные",
                                                                "temp_range": [5, 26], **extra})
    return {"zones": zones}


def test_frost_gap_names_what_is_missing():
    w = _w(("Верхняя одежда", "Зелёная куртка", {}), ("Аксессуары", "Чёрная шапка", {}))
    stats = wardrobe.wardrobe_counts(w, FROST)
    assert stats["gap"] == "Для мороза нет тёплой куртки и перчаток"
    assert wardrobe_ui.closing_line(stats) == "Для мороза нет тёплой куртки и перчаток — добавь, и образ будет теплее."


def test_forgotten_item_when_nothing_is_missing():
    w = _w(("Верх", "Белая футболка", {"last_used": "2026-11-25"}),
           ("Верхняя одежда", "Зелёная куртка", {"last_used": "2026-11-01"}))
    stats = {"items": 2, "gap": "", "forgotten": wardrobe.forgotten_item(w, MILD, today=date(2026, 11, 26))}
    assert wardrobe_ui.closing_line(stats) == "Давно не было в образах: Зелёная куртка — 3 недели."


def test_plain_count_when_there_is_nothing_to_say():
    assert wardrobe_ui.closing_line({"items": 36, "gap": "", "forgotten": {}}) == "Твой шкаф · 36 вещей"
