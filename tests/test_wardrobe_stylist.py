import os
import asyncio

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from telegram import MessageEntity

import wardrobe
from ui.settings import wardrobe_style
from ui.myday import day_summary
from ui.wardrobe import (
    outfit_emoji, outfit_header, outfit_item_names, render_wardrobe_message,
)
from wardrobe_model import normalize_parsed_item, public_item_name
from wardrobe_outfit import (
    build_main_accent,
    build_how_to_wear,
    build_sock_recommendation,
    SAFE_NEUTRAL_STYLE_TIP,
    build_style_tip,
    outfit_display_order,
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


def test_outfit_card_shows_three_base_items_without_weather_intro():
    message = render_wardrobe_message({
        "items": [
            {"name": "Белая футболка", "zone": "Верх"},
            {"name": "Широкие брюки", "zone": "Низ"},
            {"name": "Белые кеды", "zone": "Обувь"},
        ],
        "style_tip": "Оставь верх навыпуск, чтобы силуэт выглядел расслабленнее",
        "sock_recommendation": "Белые носки",
        "how_to_wear": ["Футболку оставить навыпуск"],
        "main_accent": "Белая футболка связывает светлую обувь с низом",
    })

    assert _entities(message, MessageEntity.ITALIC) == []
    assert "Жарко и сухо" not in message.text
    assert "Надень:" in message.text
    assert "- Белая футболка" in message.text
    assert "- Широкие брюки" in message.text
    assert "- Белые кеды" in message.text
    assert "- Белые носки" not in message.text
    assert "Как носить:" not in message.text
    assert "💡 Главный акцент:" in message.text
    assert "белая футболка связывает светлую обувь с низом." in message.text
    assert "белые носки поддержат" not in message.text
    assert "💡 Полезно:" not in message.text


def test_layered_outfit_gets_concrete_wearing_actions_and_sock_color():
    items = [
        {"name": "Зелёная рубашка с длинным рукавом", "zone": "Верх", "colors": ["зелёный"]},
        {"name": "Белая футболка", "zone": "Верх", "colors": ["белый"]},
        {"name": "Оливковые шорты", "zone": "Низ", "colors": ["оливковый"]},
        {"name": "Белые кеды", "zone": "Обувь", "colors": ["белый"]},
    ]

    assert build_how_to_wear(items) == [
        "Рубашку полностью расстегнуть",
        "Рукава слегка подвернуть",
        "Футболку оставить навыпуск",
    ]
    assert build_sock_recommendation(items) == "Светло-серые носки"


def test_outfit_card_does_not_show_legacy_fashion_rebus():
    message = render_wardrobe_message({
        "items": [
            {"name": "Белая футболка", "zone": "Верх"},
            {"name": "Широкие брюки", "zone": "Низ"},
            {"name": "Белые кеды", "zone": "Обувь"},
        ],
        "fashion_rebus": {
            "emoji": "🧥 🌧️ 👢",
            "answer": "Тренчкот",
            "fact": (
                "Изначально плащ-тренчкот был разработан Томасом Бёрберри для солдат "
                "британской армии в годы Первой мировой войны, а слово «trench» "
                "буквально переводится как «окоп»."
            ),
        },
    })

    assert "Модный ребус" not in message.text
    assert "Тренчкот" not in message.text
    assert "💡 Интересно" not in message.text


def test_outfit_card_capitalizes_item_names_without_lowercasing_the_rest():
    message = render_wardrobe_message({
        "items": [{"name": "цепочка со значком сторон света"}, {"name": "футболка Levi's"}],
    })

    assert "Дополнительно:\n- Цепочка со значком сторон света" in message.text
    assert "- Футболка Levi's" in message.text
    assert "Надень:" in message.text


def test_outfit_card_shows_selected_accessories_after_main_items():
    message = render_wardrobe_message({
        "items": [
            {"name": "Футболка", "zone": "Верх"},
            {"name": "Брюки", "zone": "Низ"},
            {"name": "Кеды", "zone": "Обувь"},
            {"name": "Синие носки", "zone": "Аксессуары"},
        ],
    })

    assert "- Брюки" in message.text
    assert "- Кеды" in message.text
    assert "Дополнительно:\n- Синие носки" not in message.text
    assert "Главный акцент: синие носки поддержат обувь и соберут образ." in message.text


def test_outfit_card_puts_each_selected_top_on_its_own_line():
    message = render_wardrobe_message({
        "items": [
            {"name": "Голубая рубашка", "zone": "Верх"},
            {"name": "Белая футболка", "zone": "Верх"},
            {"name": "Тёмные брюки", "zone": "Низ"},
        ],
    })

    assert "- Голубая рубашка\n- Белая футболка" in message.text
    assert "Голубая рубашка, Белая футболка" not in message.text


def test_selected_accessory_can_be_the_main_accent_instead_of_socks():
    message = render_wardrobe_message({
        "items": [
            {"name": "Белая футболка", "zone": "Верх"},
            {"name": "Чёрные брюки", "zone": "Низ"},
            {"name": "Серебристый браслет", "zone": "Аксессуары"},
        ],
        "sock_recommendation": "Белые носки",
        "main_accent": "Серебристый браслет добавит контраст",
    })

    assert "главный акцент: серебристый браслет" in message.text.casefold()
    assert "белые носки поддержат" not in message.text.casefold()


def test_main_accent_rotates_and_can_use_an_accessory():
    items = [
        {"name": "Белая футболка", "zone": "Верх", "colors": ["белый"]},
        {"name": "Синие брюки", "zone": "Низ", "colors": ["синий"]},
        {"name": "Белые кеды", "zone": "Обувь", "colors": ["белый"]},
        {"name": "Серебристый браслет", "zone": "Аксессуары", "colors": ["серебристый"]},
    ]

    first = build_main_accent(items)
    second = build_main_accent(items, avoid_accents={first})

    assert "браслет" in first.casefold()
    assert second != first


def test_outfit_card_shows_outerwear_as_an_extra_only_when_selected():
    message = render_wardrobe_message({
        "items": [
            {"name": "Фиолетовая футболка", "zone": "Верх"},
            {"name": "Лёгкая ветровка", "zone": "Верхняя одежда"},
            {"name": "Бежевые брюки", "zone": "Низ"},
            {"name": "Белые кеды", "zone": "Обувь"},
        ],
    })

    assert "Надень:\n- Фиолетовая футболка" in message.text
    assert "Дополнительно:\n- Лёгкая ветровка" in message.text
    assert "Верхняя одежда:" not in message.text


def test_outfit_header_uses_emoji_of_selected_style():
    assert outfit_header("Минимализм") == "👕 Образ на сегодня · Минимализм"
    assert outfit_header("Городской") == "🧢 Образ на сегодня · Городской"
    assert outfit_header("Повседневный") == "👖 Образ на сегодня · Повседневный"
    assert outfit_header("Скандинавский") == "🧥 Образ на сегодня · Скандинавский"
    assert outfit_header("Классический") == "👔 Образ на сегодня · Классический"
    assert outfit_header("Спортивный") == "👟 Образ на сегодня · Спортивный"


def test_myday_summary_uses_every_visible_outfit_item_in_the_same_order(monkeypatch):
    look = {
        "primary_style": "Минимализм",
        "items": [
            {"name": "Белая футболка", "zone": "Верх"},
            {"name": "Чёрные брюки", "zone": "Низ"},
            {"name": "Белые кеды", "zone": "Обувь"},
            {"name": "Серые часы", "zone": "Аксессуары"},
            {"name": "Белые носки", "zone": "Аксессуары"},
        ],
        "sock_recommendation": "Белые носки",
    }

    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: {"look_data": look})
    summary = wardrobe.get_cached_outfit_summary("42")

    assert summary["emoji"] == outfit_emoji(look["primary_style"]) == "👕"
    assert summary["items"] == outfit_item_names(look) == [
        "Белая футболка", "Чёрные брюки", "Белые кеды", "Серые часы",
    ]
    message = day_summary(
        "Вт, 1 сентября", "Алкмар",
        outfit_items=summary["items"], outfit_emoji=summary["emoji"],
    )
    assert "👕 Образ: Белая футболка, Чёрные брюки, Белые кеды, Серые часы." in message.text
    assert "носки" not in message.text.casefold()


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


def test_hot_dry_weather_prioritizes_shorts_over_trousers():
    top = _item("top", "Верх", "Белая футболка")
    trousers = _item("trousers", "Низ", "Чёрные брюки")
    shorts = _item("shorts", "Низ", "Оливковые шорты")
    shoes = _item("shoes", "Обувь", "Белые кроссовки")
    top["warmth"] = shorts["warmth"] = "лёгкие"
    trousers["warmth"] = shoes["warmth"] = "обычные"
    wardrobe_data = {"zones": {
        "Верх": {"Футболки": [top]},
        "Низ": {"Брюки": [trousers], "Шорты": [shorts]},
        "Обувь": {"Кроссовки": [shoes]},
    }}
    weather = {
        "tmax": 25, "hot": True, "warm": False, "has_rain": False,
        "strong_wind": False, "sunny": True,
    }

    outfit = pick_best_outfit(wardrobe_data, weather, [], "", selected_styles=[])

    assert {item["id"] for item in outfit} == {"top", "shorts", "shoes"}


def test_suitable_accessory_is_preferred_when_it_is_available():
    wardrobe_data = {"zones": {
        "Верх": {"Футболки": [_item("t1", "Верх", "Белая футболка")]},
        "Низ": {"Брюки": [_item("b1", "Низ", "Бежевые брюки")]},
        "Обувь": {"Кеды": [_item("s1", "Обувь", "Белые кеды")]},
        "Аксессуары": {"Часы": [_item("a1", "Аксессуары", "Чёрные часы")]},
    }}

    outfit = pick_best_outfit(
        wardrobe_data,
        {"tmax": 22, "has_rain": False, "strong_wind": False, "warm": True, "sunny": False},
        [],
        "",
    )

    assert any(item["id"] == "a1" for item in outfit)


def test_layer_misfiled_as_top_is_completed_with_a_base_top():
    wardrobe_data = {"zones": {
        # Старые вещи могли быть добавлены AI в «Верх», поэтому роль определяем
        # по самой вещи, а не доверяем только сохранённой зоне.
        "Верх": {"Другое": [
            _item("vest", "Верх", "Чёрный жилет без рукавов"),
            _item("tee", "Верх", "Белая футболка"),
        ]},
        "Низ": {"Брюки": [_item("trousers", "Низ", "Чёрные брюки")]},
        "Обувь": {"Кеды": [_item("sneakers", "Обувь", "Бежевые кеды")]},
    }}

    outfit = pick_best_outfit(
        wardrobe_data,
        {"tmax": 20, "has_rain": False, "strong_wind": False, "warm": True},
        [],
        "",
    )

    assert outfit is not None
    assert {item["id"] for item in outfit} == {"vest", "tee", "trousers", "sneakers"}


def test_relaxed_shirt_can_be_worn_open_over_a_tshirt():
    wardrobe_data = {"zones": {
        "Верх": {
            "Рубашки": [_item("shirt", "Верх", "Голубая свободная рубашка")],
            "Футболки": [_item("tee", "Верх", "Белая футболка")],
        },
        "Низ": {"Брюки": [_item("trousers", "Низ", "Тёмно-синие брюки")]},
        "Обувь": {"Кеды": [_item("sneakers", "Обувь", "Белые кеды")]},
    }}

    outfit = pick_best_outfit(
        wardrobe_data,
        {"tmax": 18, "has_rain": False, "strong_wind": False, "warm": False},
        [],
        "",
        selected_styles=["Городской"],
    )

    assert outfit is not None
    assert {"shirt", "tee"}.issubset({item["id"] for item in outfit})
    assert [item["id"] for item in sorted(outfit, key=outfit_display_order)][:2] == ["tee", "shirt"]


def test_shirt_and_tshirt_can_be_recommended_without_a_selected_style():
    wardrobe_data = {"zones": {
        "Верх": {
            "Рубашки": [_item("shirt", "Верх", "Голубая свободная рубашка")],
            "Футболки": [_item("tee", "Верх", "Белая футболка")],
        },
        "Низ": {"Брюки": [_item("trousers", "Низ", "Тёмно-синие брюки")]},
        "Обувь": {"Кеды": [_item("sneakers", "Обувь", "Белые кеды")]},
    }}

    outfit = pick_best_outfit(
        wardrobe_data,
        {"tmax": 17, "has_rain": False, "strong_wind": False, "warm": True},
        [],
        "",
    )

    assert {"shirt", "tee"}.issubset({item["id"] for item in outfit})


def test_shirt_layer_is_not_added_in_hot_weather():
    wardrobe_data = {"zones": {
        "Верх": {
            "Рубашки": [_item("shirt", "Верх", "Голубая свободная рубашка")],
            "Футболки": [_item("tee", "Верх", "Белая футболка")],
        },
        "Низ": {"Шорты": [_item("shorts", "Низ", "Синие шорты")]},
        "Обувь": {"Кеды": [_item("sneakers", "Обувь", "Белые кеды")]},
    }}

    outfit = pick_best_outfit(
        wardrobe_data,
        {"tmax": 27, "hot": True, "has_rain": False, "strong_wind": False},
        [],
        "",
        selected_styles=["Городской"],
    )

    assert outfit is not None
    assert not {"shirt", "tee"}.issubset({item["id"] for item in outfit})


def test_layer_without_a_base_top_is_not_a_complete_outfit():
    wardrobe_data = {"zones": {
        "Верх": {"Другое": [_item("vest", "Верх", "Чёрный жилет без рукавов")]},
        "Низ": {"Брюки": [_item("trousers", "Низ", "Чёрные брюки")]},
        "Обувь": {"Кеды": [_item("sneakers", "Обувь", "Бежевые кеды")]},
    }}

    outfit = pick_best_outfit(
        wardrobe_data,
        {"tmax": 20, "has_rain": False, "strong_wind": False, "warm": True},
        [],
        "",
    )

    assert outfit is None


@pytest.mark.parametrize("layer_name", [
    "Серая рубашка overshirt",
    "Чёрный кардиган",
    "Чёрная ветровка",
])
def test_other_layers_and_outerwear_do_not_replace_base_top(layer_name):
    wardrobe_data = {"zones": {
        "Верх": {"Другое": [_item("layer", "Верх", layer_name)]},
        "Низ": {"Брюки": [_item("trousers", "Низ", "Чёрные брюки")]},
        "Обувь": {"Кеды": [_item("sneakers", "Обувь", "Бежевые кеды")]},
    }}

    outfit = pick_best_outfit(
        wardrobe_data,
        {"tmax": 20, "has_rain": False, "strong_wind": False, "warm": True},
        [],
        "",
    )

    assert outfit is None


def test_layer_is_shown_after_its_base_top():
    items = [
        _item("vest", "Верх", "Чёрный жилет без рукавов"),
        _item("trousers", "Низ", "Чёрные брюки"),
        _item("sneakers", "Обувь", "Бежевые кеды"),
        _item("tee", "Верх", "Белая футболка"),
    ]

    assert [item["id"] for item in sorted(items, key=outfit_display_order)] == [
        "tee", "vest", "trousers", "sneakers",
    ]


def test_city_style_prefers_a_relaxed_top_over_office_shirts_outside_the_first_pool():
    wardrobe_data = {"zones": {
        "Верх": {"Рубашки": [
            _item("office", "Верх", "Белая офисная рубашка"),
            _item("formal-1", "Верх", "Голубая деловая рубашка"),
            _item("formal-2", "Верх", "Серая строгая рубашка"),
            _item("formal-3", "Верх", "Чёрная костюмная рубашка"),
            _item("tee", "Верх", "Белая свободная футболка"),
        ]},
        "Низ": {"Брюки": [_item("trousers", "Низ", "Чёрные брюки")]},
        "Обувь": {"Кеды": [_item("sneakers", "Обувь", "Чёрные кеды")]},
    }}

    outfit = pick_best_outfit(
        wardrobe_data,
        {"tmax": 20, "has_rain": False, "strong_wind": False, "warm": True},
        [],
        "",
        selected_styles=["Городской"],
    )

    assert outfit is not None
    assert "tee" in {item["id"] for item in outfit}
    assert "office" not in {item["id"] for item in outfit}


def test_city_style_recommends_a_relaxed_tshirt_when_only_office_top_is_available():
    wardrobe_data = {"zones": {
        "Верх": {"Рубашки": [{"name": "Белая офисная рубашка", "zone": "Верх"}]},
        "Низ": {"Брюки": [{"name": "Чёрные брюки", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Чёрные кеды", "zone": "Обувь"}]},
    }}

    recommendation = wardrobe._purchase_candidate(
        wardrobe_data,
        {"has_rain": False},
        selected_styles=["Городской"],
    )

    assert recommendation["item"] == "Белая свободная футболка"
    assert "городскую базу" in recommendation["reason"]


def test_city_style_does_not_recommend_a_tshirt_when_a_city_base_already_exists():
    wardrobe_data = {"zones": {
        "Верх": {"Футболки": [{"name": "Белая свободная футболка", "zone": "Верх"}]},
        "Низ": {"Джинсы": [{"name": "Серые широкие джинсы", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Чёрные кеды", "zone": "Обувь"}]},
    }}

    assert wardrobe._purchase_candidate(
        wardrobe_data,
        {"has_rain": False},
        selected_styles=["Городской"],
    ) is None


def test_weather_intro_changes_between_new_outfits():
    weather = {"tmax": 22, "has_rain": False, "strong_wind": False, "warm": True}

    assert wardrobe._weather_decision(weather, variant=0) != wardrobe._weather_decision(weather, variant=1)


def test_purchase_recommendation_is_stable_until_a_more_important_gap(monkeypatch):
    profile = {}
    monkeypatch.setattr(wardrobe.store, "get_wardrobe_purchase_recommendation", lambda _cid: dict(profile))
    monkeypatch.setattr(wardrobe.store, "set_wardrobe_purchase_recommendation", lambda _cid, value: profile.update(value))

    w = {"zones": {
        "Верх": {"Рубашки": [{"name": "Голубая рубашка", "zone": "Верх"}]},
        "Низ": {"Брюки": [{"name": "Бежевые брюки", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Белые кеды", "zone": "Обувь"}]},
    }}
    dry = {"has_rain": False}
    first = wardrobe._get_or_create_purchase_recommendation("stable-rec", w, dry)
    second = wardrobe._get_or_create_purchase_recommendation("stable-rec", w, {"has_rain": False, "strong_wind": True})

    assert first["item"] == "Серые широкие джинсы"
    assert second == first

    important = wardrobe._get_or_create_purchase_recommendation(
        "stable-rec", w, {"has_rain": True})
    assert important["item"] == "Лёгкая непромокаемая ветровка"

def test_wardrobe_keeps_a_wear_tip_when_no_purchase_gap_exists(monkeypatch):
    profile = {}
    monkeypatch.setattr(wardrobe.store, "get_wardrobe_purchase_recommendation", lambda _cid: dict(profile))
    monkeypatch.setattr(wardrobe.store, "set_wardrobe_purchase_recommendation", lambda _cid, value: profile.update(value))

    w = {"zones": {
        "Верх": {"Рубашки": [{"name": "Зелёная рубашка", "zone": "Верх"}]},
        "Низ": {"Джинсы": [{"name": "Синие джинсы", "zone": "Низ"}]},
    }}
    recommendation = wardrobe._get_or_create_purchase_recommendation(
        "wear-tip", w, {"has_rain": False}, fallback_tip="Оставь рубашку навыпуск, чтобы образ выглядел легче.",
    )

    assert recommendation["kind"] == "wear"
    assert recommendation["reason"].startswith("Оставь рубашку")


def test_cached_outfit_repairs_missing_useful_recommendation(monkeypatch):
    profile = {}
    wardrobe_data = {"zones": {
        "Верх": {"Рубашки": [{"name": "Голубая рубашка", "zone": "Верх"}]},
        "Низ": {"Брюки": [{"name": "Бежевые брюки", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Бежевые кеды с чёрной точкой", "zone": "Обувь"}]},
    }}
    look_data = {
        "primary_style": "Городской",
        "items": [
            {"name": "Голубая рубашка"},
            {"name": "Бежевые брюки"},
            {"name": "Бежевые кеды с чёрной точкой"},
        ],
        "purchase_recommendation": {},
    }
    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: wardrobe_data)
    monkeypatch.setattr(wardrobe.store, "get_wardrobe_purchase_recommendation", lambda _cid: dict(profile))
    monkeypatch.setattr(wardrobe.store, "set_wardrobe_purchase_recommendation", lambda _cid, value: profile.update(value))

    repaired = wardrobe._repair_missing_purchase_recommendation("cached-tip", look_data)
    message = render_wardrobe_message(repaired)

    assert repaired["purchase_recommendation"]
    assert "💡 Полезно:" not in message.text


def test_malformed_saved_recommendation_is_replaced_for_another_outfit(monkeypatch):
    profile = {"version": wardrobe.PURCHASE_RECOMMENDATION_VERSION, "priority": 0}
    wardrobe_data = {"zones": {
        "Верх": {"Рубашки": [{"name": "Голубая рубашка", "zone": "Верх"}]},
        "Низ": {"Джинсы": [{"name": "Синие джинсы", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Бежевые кеды", "zone": "Обувь"}]},
    }}
    monkeypatch.setattr(wardrobe.store, "get_wardrobe_purchase_recommendation", lambda _cid: dict(profile))
    monkeypatch.setattr(wardrobe.store, "set_wardrobe_purchase_recommendation", lambda _cid, value: profile.update(value))

    recommendation = wardrobe._get_or_create_purchase_recommendation(
        "another-outfit", wardrobe_data, {},
        fallback_tip="Сделай обувь финальным акцентом, чтобы образ выглядел собраннее.",
    )

    assert recommendation["reason"]


def test_outfit_card_has_no_save_action():
    labels = [
        button.text
        for row in wardrobe.build_wardrobe_keyboard().inline_keyboard
        for button in row
    ]

    assert "💾 Сохранить" not in labels
    assert "❌ Не сейчас" not in labels


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


def test_style_preferences_invalidate_current_outfit_and_purchase_advice(monkeypatch):
    invalidated = []
    monkeypatch.setattr(settings.store, "clear_wardrobe_daylook", lambda cid: invalidated.append(("look", cid)))
    monkeypatch.setattr(settings.store, "clear_wardrobe_purchase_recommendation", lambda cid: invalidated.append(("purchase", cid)))
    monkeypatch.setattr(settings, "get", lambda _cid, _key, default=None: default)
    monkeypatch.setattr(settings, "set_", lambda *_args: None)

    settings._toggle_palette("42", 0)

    assert invalidated == [("look", "42"), ("purchase", "42")]


def test_wardrobe_preferences_show_only_six_emoji_styles():
    markup = settings._wardrobe_style_kb("prefs-test", state={
        "styles": [], "fit": "", "palette": [], "avoid": [],
    })
    labels = [button.text for row in markup.inline_keyboard for button in row]

    assert labels[:-2] == [
        "👕 Минимализм", "🧥 Скандинавский", "👖 Повседневный",
        "🧢 Городской", "👔 Классический", "👟 Спортивный",
    ]
    assert all(len(row) == 1 for row in markup.inline_keyboard[:-1])
    assert markup.inline_keyboard[-1][0].callback_data == "w_closet"


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

    assert build_style_tip([short]) != "Слегка заправь верх спереди, чтобы силуэт выглядел собраннее."
    assert build_style_tip([long]).startswith("Подверни рукава")


def test_generic_style_tip_can_change_for_another_outfit():
    items = [
        {"id": "top-1", "zone": "Верх", "name": "Футболка"},
        {"id": "bottom-1", "zone": "Низ", "name": "Брюки"},
        {"id": "shoe-1", "zone": "Обувь", "name": "Кеды"},
    ]

    first = build_style_tip(items)
    second = build_style_tip(items, avoid_tips={first})

    assert first != second
    assert "заправь верх спереди" not in first.casefold()
    assert "заправь верх спереди" not in second.casefold()


def test_generic_style_tip_has_a_broader_safe_rotation():
    tips = {
        build_style_tip([
            {"id": f"top-{index}", "zone": "Верх", "name": "Футболка"},
            {"id": f"bottom-{index}", "zone": "Низ", "name": "Брюки"},
            {"id": f"shoe-{index}", "zone": "Обувь", "name": "Кеды"},
        ])
        for index in range(12)
    }

    assert len(tips) >= 5
    assert all("чтобы" in tip.casefold() for tip in tips)


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
