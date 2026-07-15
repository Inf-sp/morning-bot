import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from telegram import MessageEntity

from ui.settings import wardrobe_style
from ui.wardrobe import render_wardrobe_message
from wardrobe_model import normalize_parsed_item
from wardrobe_outfit import pick_best_outfit


def _entities(message, entity_type):
    return [
        message.text.encode("utf-16-le")[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")
        for entity in message.entities
        if entity.type == entity_type
    ]


def _item(item_id, zone, name):
    return {
        "id": item_id,
        "zone": zone,
        "name": name,
        "colors": ["белый"],
        "fit": "прямая",
        "use_count": 0,
    }


def test_outfit_card_has_one_italic_intro_and_dynamic_final():
    message = render_wardrobe_message({
        "weather_intro": "Жарко и сухо — нужен лёгкий образ",
        "items": [{"name": "Белая футболка"}, {"name": "Широкие брюки"}, {"name": "Белые кеды"}],
        "style_tip": "Заправь футболку только спереди",
        "reasons": ["Свободный низ поддерживает объём верха, а светлая обувь облегчает силуэт"],
        "final_heading": "Финальный штрих",
        "final_text": "добавь серебристые часы",
    })

    assert _entities(message, MessageEntity.ITALIC) == ["Жарко и сухо — нужен лёгкий образ."]
    assert "Финальный штрих: добавь серебристые часы." in message.text


def test_other_outfit_changes_the_base_not_one_random_item():
    wardrobe = {"zones": {
        "Верх": {"Футболки": [_item("t1", "Верх", "Белая футболка"), _item("t2", "Верх", "Серая футболка")]},
        "Низ": {"Брюки": [_item("b1", "Низ", "Бежевые брюки"), _item("b2", "Низ", "Синие брюки")]},
        "Обувь": {"Кеды": [_item("s1", "Обувь", "Белые кеды"), _item("s2", "Обувь", "Серые кеды")]},
        "Аксессуары": {"Часы": [_item("a1", "Аксессуары", "Чёрные часы"), _item("a2", "Аксессуары", "Серебристые часы")]},
    }}
    weather = {"tmax": 22, "has_rain": False, "strong_wind": False, "warm": True}

    alternative = pick_best_outfit(
        wardrobe, weather, [], "", previous_item_ids={"t1", "b1", "s1", "a1"})

    assert alternative is not None
    assert len({"t1", "b1", "s1", "a1"} - {item["id"] for item in alternative}) >= 2


def test_parsed_item_keeps_fit_season_and_occasions():
    item = normalize_parsed_item({
        "name": "Голубая свободная рубашка Uniqlo",
        "zone": "Верх",
        "subcategory": "Рубашки",
        "color": "голубой",
        "fit": "свободная",
        "season": ["лето", "деми"],
        "occasions": ["город", "офис"],
    })

    assert item["fit"] == "свободная"
    assert item["season"] == ["лето", "деми"]
    assert item["occasions"] == ["город", "офис"]


def test_style_summary_explains_that_avoid_checks_are_restrictions():
    message = wardrobe_style(
        ["минимализм", "скандинавский"], "свободная", ["тёмные", "светлые"], ["узкий крой"])

    assert "Стиль: минимализм · скандинавский" in message.text
    assert "Не предлагать: узкий крой" in message.text
