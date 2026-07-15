import os
import asyncio

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from telegram import MessageEntity

from ui.settings import wardrobe_style
from ui.wardrobe import render_wardrobe_message
from wardrobe_model import normalize_parsed_item, public_item_name
from wardrobe_outfit import (
    SAFE_NEUTRAL_STYLE_TIP,
    build_style_tip,
    pick_best_outfit,
    validate_outfit_copy,
)
import settings


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


def test_outfit_card_capitalizes_item_names_without_lowercasing_the_rest():
    message = render_wardrobe_message({
        "items": [{"name": "цепочка со значком сторон света"}, {"name": "футболка Levi's"}],
    })

    assert "- Цепочка со значком сторон света" in message.text
    assert "- Футболка Levi's" in message.text


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


def test_style_screen_reads_settings_once(monkeypatch):
    calls = {"count": 0}

    def fake_all():
        calls["count"] += 1
        return {"fast-style": {
            "style": ["минимализм"],
            "wardrobe_fit": "прямая",
            "wardrobe_palette": ["тёмные"],
            "wardrobe_style_avoid": ["узкий крой"],
        }}

    monkeypatch.setattr(settings, "_all", fake_all)

    class Message:
        async def edit_text(self, *args, **kwargs):
            return None

    class Query:
        message = Message()

    class Bot:
        async def send_message(self, **kwargs):
            raise AssertionError("edit_text should be used")

    asyncio.run(settings.send_wardrobe_style(Bot(), "fast-style", q=Query()))

    assert calls["count"] == 1


def test_outfit_copy_rejects_short_sleeve_hallucinations_and_internal_tags():
    shirt = {
        "id": "top-1",
        "zone": "Верх",
        "subcategory": "Рубашки",
        "name": "Голубая рубашка с коротким рукавом (летняя, utility casual, город)",
        "color": "голубой",
        "colors": ["голубой"],
        "fit": None,
        "season": ["лето"],
        "style": "utility casual",
        "occasions": ["город"],
    }
    trousers = _item("bottom-1", "Низ", "Синие брюки")
    shoes = _item("shoe-1", "Обувь", "Белые кеды")
    selected = [shirt, trousers, shoes]
    wardrobe = {"zones": {
        "Верх": {"Рубашки": [shirt]},
        "Низ": {"Брюки": [trousers]},
        "Обувь": {"Кеды": [shoes]},
    }}

    result = validate_outfit_copy(
        selected,
        wardrobe,
        {},
        ["Объёмные рукава рубашки уравновешивают широкие брюки."],
        "Подверни рукава и оставь рубашку навыпуск.",
        "Образ готов",
        "Добавь серебристые часы.",
    )

    assert public_item_name(shirt) == "Голубая рубашка с коротким рукавом"
    assert result["style_tip"] == SAFE_NEUTRAL_STYLE_TIP
    assert all("объём" not in reason.casefold() and "широк" not in reason.casefold() for reason in result["reasons"])
    assert "utility" not in " ".join(result["reasons"]).casefold()
    assert result["final_text"] == "Комплект собран из вещей твоего шкафа"


def test_style_tip_rolls_sleeves_only_when_length_is_confirmed():
    short = {"zone": "Верх", "subcategory": "Рубашки", "name": "Рубашка с коротким рукавом"}
    long = {"zone": "Верх", "subcategory": "Рубашки", "name": "Рубашка с длинными рукавами"}

    assert build_style_tip([short]) == SAFE_NEUTRAL_STYLE_TIP
    assert build_style_tip([long]).startswith("Подверни рукава")


def test_final_accessory_is_allowed_only_when_selected_and_present_in_database():
    watch = {
        "id": "watch-1",
        "zone": "Аксессуары",
        "subcategory": "Часы",
        "name": "Серебристые часы",
        "colors": ["серебристый"],
    }
    wardrobe = {"zones": {"Аксессуары": {"Часы": [watch]}}}

    result = validate_outfit_copy(
        [watch], wardrobe, {}, ["Серебристые часы завершают комплект."],
        SAFE_NEUTRAL_STYLE_TIP, "Образ готов", "Добавь серебристые часы.",
    )

    assert result["final_text"] == "Добавь серебристые часы."
