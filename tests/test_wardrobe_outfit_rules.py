import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import wardrobe
from ui import wardrobe as wardrobe_ui


def test_wardrobe_wind_layer_rule_starts_at_6(monkeypatch):
    gaps = []
    monkeypatch.setattr(wardrobe, "_resync_wardrobe_gaps", lambda cid, w: None)
    monkeypatch.setattr(wardrobe, "_has_rain_outerwear", lambda w: True)
    monkeypatch.setattr(wardrobe, "add_wardrobe_gap", lambda *args, **kwargs: gaps.append(args))

    rules, gap_note = wardrobe._build_weather_rules("1", {}, {
        "rain_daytime": False,
        "heavy_rain": False,
        "strong_wind": False,
        "wind_ms": 6,
        "sunny": False,
    })

    assert "ВЕТЕР ОТ 6 М/С" in rules
    assert "лёгкая ветровка" in rules
    assert gap_note == ""
    assert gaps == []


def test_look_message_renders_styling_tip():
    msg = wardrobe_ui.look_message({
        "intro": "Свободная рубашка и лёгкие брюки.",
        "items": [
            {"emoji": "👔", "name": "Белая рубашка", "short_name": "Белая рубашка"},
            {"emoji": "👖", "name": "Чёрные брюки", "short_name": "Чёрные брюки"},
            {"emoji": "👟", "name": "Белые кеды", "short_name": "Белые кеды"},
        ],
        "style": "Japanese Minimalism",
        "reasons": ["Лёгкий верх подходит по температуре."],
        "styling_tip": "Рукава рубашки можно закатать, а саму рубашку оставить навыпуск.",
    })

    assert "Рукава рубашки можно закатать" in msg.text
    assert "навыпуск" in msg.text
