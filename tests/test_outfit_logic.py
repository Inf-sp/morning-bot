"""Логика комплекта: что с чем не носят (docs/wardrobe.md «Оценка комплекта»)."""
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

import wardrobe_rules
import wardrobe_suitcase
from wardrobe_outfit import outfit_conflicts, outfit_role, top_outfits


def _it(item_id, zone, name, color, temp=(5, 30), warmth="обычные", **extra):
    return {"id": item_id, "zone": zone, "name": name, "colors": [color], "temp_range": list(temp),
            "warmth": warmth, **extra}


def _closet(*items):
    zones = {}
    for item in items:
        zones.setdefault(item["zone"], {}).setdefault("Все", []).append(item)
    return {"zones": zones}


def _ids(outfit):
    return {item["id"] for item in outfit}


GREY_TEE = _it("tee_grey", "Верх", "Серая футболка", "серый", (12, 35))
BLACK_TEE = _it("tee_black", "Верх", "Чёрная футболка", "чёрный", (12, 35))
WHITE_TEE = _it("tee_white", "Верх", "Белая футболка", "белый", (12, 35))
BEIGE_PRINT_TEE = _it("tee_beige_print", "Верх", "Бежевая футболка с рисунком", "бежевый", (12, 35))
BEIGE_TEE = _it("tee_beige", "Верх", "Бежевая футболка", "бежевый", (12, 35))
BROWN_TEE = _it("tee_brown", "Верх", "Коричневая футболка", "коричневый", (12, 35))
NAVY_LONGSLEEVE = _it("ls_navy", "Верх", "Тёмно-синий лонгслив", "тёмно-синий", (0, 25))
BLUE_SHIRT = _it("shirt_blue", "Верх", "Голубая рубашка", "голубой", (8, 28))
CHECK_SHIRT = _it("shirt_check", "Верх", "Серая рубашка в клетку", "серый", (8, 28))
OFFICE_SHIRT = _it("shirt_office", "Верх", "Белая офисная рубашка", "белый", (8, 28))
GREEN_POLO = _it("polo_green", "Верх", "Зелёное поло", "зелёный", (15, 32))

GREY_HOODIE = _it("hoodie_grey", "Кофты", "Серое худи", "серый", (0, 18))
NAVY_SWEATSHIRT = _it("sweatshirt_navy", "Кофты", "Тёмно-синий свитшот", "тёмно-синий", (0, 18))
BEIGE_SWEATER = _it("sweater_beige", "Кофты", "Бежевый свитер", "бежевый", (-10, 15), "тёплые")
PURPLE_FLEECE = _it("fleece_purple", "Кофты", "Фиолетовый флис", "фиолетовый", (-5, 16))
OLIVE_OVERSHIRT = _it("overshirt_olive", "Кофты", "Оливковый овершот", "оливковый", (8, 22))

JEANS = _it("jeans", "Низ", "Синие джинсы", "деним", (-5, 30))
BEIGE_CHINOS = _it("chinos_beige", "Низ", "Бежевые чиносы", "бежевый", (5, 30))
STRICT_TROUSERS = _it("trousers_strict", "Низ", "Серые строгие брюки", "серый", (0, 28))
JOGGERS = _it("joggers", "Низ", "Чёрные джоггеры", "чёрный", (-5, 25))
CHECK_TROUSERS = _it("trousers_check", "Низ", "Брюки в клетку", "серый", (0, 26))
OLIVE_CHINOS = _it("chinos_olive", "Низ", "Оливковые чиносы", "оливковый", (5, 30))

WHITE_SNEAKERS = _it("sneakers_white", "Обувь", "Белые кеды", "белый", (5, 32))
OXFORDS = _it("oxfords", "Обувь", "Коричневые оксфорды", "коричневый", (0, 28))
RUNNING = _it("running", "Обувь", "Чёрные спортивные кроссовки", "чёрный", (0, 32))
BOOTS = _it("boots", "Обувь", "Чёрные ботинки", "чёрный", (-20, 15), "тёплые")

WINDBREAKER = _it("windbreaker", "Верхняя одежда", "Тёмно-синяя ветровка", "тёмно-синий", (5, 20), rain_ok=True)
FLEECE_JACKET = _it("fleece_jacket", "Верхняя одежда", "Чёрная флисовая куртка", "чёрный", (0, 15))
PUFFER = _it("puffer", "Верхняя одежда", "Чёрный пуховик", "чёрный", (-25, 5), "тёплые")
TRENCH = _it("trench", "Верхняя одежда", "Бежевый тренч", "бежевый", (8, 20))
LEATHER = _it("leather", "Верхняя одежда", "Коричневая кожаная куртка", "коричневый", (5, 18))

EVERYTHING = (GREY_TEE, BLACK_TEE, WHITE_TEE, BEIGE_PRINT_TEE, NAVY_LONGSLEEVE, BLUE_SHIRT, CHECK_SHIRT,
              OFFICE_SHIRT, GREEN_POLO, GREY_HOODIE, NAVY_SWEATSHIRT, BEIGE_SWEATER, PURPLE_FLEECE, OLIVE_OVERSHIRT,
              JEANS, BEIGE_CHINOS, STRICT_TROUSERS, JOGGERS, CHECK_TROUSERS, OLIVE_CHINOS, WHITE_SNEAKERS, OXFORDS,
              RUNNING, BOOTS, WINDBREAKER, FLEECE_JACKET, PUFFER, TRENCH, LEATHER)

WEATHERS = {  # дневная погода: максимум, ночной минимум, дождь, ветер (как у дня поездки)
    "мороз −3°": {"tmax": -3, "tmin": -8, "rain": False, "wind": 4},
    "холодно и ветрено +5°": {"tmax": 5, "tmin": 1, "rain": False, "wind": 11},
    "дождь +12°": {"tmax": 12, "tmin": 8, "rain": True, "wind": 5},
    "свежо +17°": {"tmax": 17, "tmin": 11, "rain": False, "wind": 3},
    "тепло +22°": {"tmax": 22, "tmin": 15, "rain": False, "wind": 3},
    "жара +28°": {"tmax": 28, "tmin": 19, "rain": False, "wind": 2},
}


@pytest.mark.parametrize("items, problem", [
    ((GREY_TEE, GREY_HOODIE, PURPLE_FLEECE, JEANS, WHITE_SNEAKERS), "два толстых слоя"),
    ((GREY_TEE, GREY_HOODIE, FLEECE_JACKET, JEANS, WHITE_SNEAKERS), "два толстых слоя"),  # флис-куртка — тоже флис
    ((BLUE_SHIRT, PURPLE_FLEECE, JEANS, WHITE_SNEAKERS), "худи или флис поверх рубашки"),
    ((BLUE_SHIRT, GREY_HOODIE, JEANS, WHITE_SNEAKERS), "худи или флис поверх рубашки"),
    ((GREY_TEE, GREY_HOODIE, STRICT_TROUSERS, WHITE_SNEAKERS), "спортивное со строгим"),
    ((GREY_TEE, JOGGERS, OXFORDS), "спортивное со строгим"),
    ((BEIGE_PRINT_TEE, PURPLE_FLEECE, JEANS, WHITE_SNEAKERS), "больше одного заметного элемента"),
    ((CHECK_SHIRT, CHECK_TROUSERS, WHITE_SNEAKERS), "больше одного заметного элемента"),
    ((GREEN_POLO, _it("burgundy", "Низ", "Бордовые брюки", "бордовый"), WHITE_SNEAKERS),
     "больше одного заметного элемента"),
])
def test_illogical_outfits_are_named(items, problem):
    assert problem in outfit_conflicts(list(items))


def test_logical_outfits_have_no_conflicts():
    for items in ((GREY_TEE, PURPLE_FLEECE, JEANS, WHITE_SNEAKERS),
                  (BLACK_TEE, FLEECE_JACKET, JOGGERS, RUNNING),  # флис — внешним слоем в прохладу
                  (GREY_TEE, PURPLE_FLEECE, WINDBREAKER, JEANS, BOOTS),  # флис под ветровкой в холод
                  (OFFICE_SHIRT, STRICT_TROUSERS, OXFORDS),
                  (BLUE_SHIRT, OLIVE_OVERSHIRT, BEIGE_CHINOS, WHITE_SNEAKERS),
                  (GREEN_POLO, _it("green2", "Низ", "Зелёные брюки", "зелёный"), WHITE_SNEAKERS)):  # тон в тон
        assert outfit_conflicts(list(items)) == [], items


def test_fleece_is_a_warm_middle_layer_whatever_zone_it_is_stored_in():
    assert outfit_role(PURPLE_FLEECE) == "layer" and outfit_role(FLEECE_JACKET) == "layer"


def test_purple_fleece_gets_a_plain_grey_or_black_tee_never_a_print_or_a_shirt():
    closet = _closet(BEIGE_PRINT_TEE, BEIGE_TEE, BLUE_SHIRT, GREY_TEE, BLACK_TEE, PURPLE_FLEECE, JEANS,
                     WHITE_SNEAKERS)
    cool = wardrobe_suitcase._day_context(WEATHERS["свежо +17°"])

    anchored = top_outfits(closet, cool, [], "", selected_styles=[], anchor_id="fleece_purple")
    cold = wardrobe_suitcase._day_context(WEATHERS["дождь +12°"])
    daily = [outfit for ctx in (cool, cold) for outfit in top_outfits(closet, ctx, [], "", selected_styles=[])
             if "fleece_purple" in _ids(outfit)]  # образ дня берёт флис, только когда он нужен

    assert anchored
    for outfit in [*anchored, *daily]:
        assert _ids(outfit) & {"tee_grey", "tee_black"}, _ids(outfit)
        assert not _ids(outfit) & {"tee_beige_print", "shirt_blue"}
    # Под холодный цвет — холодная база: однотонная бежевая проигрывает серой и чёрной.
    assert _ids(anchored[0]) & {"tee_grey", "tee_black"}
    assert wardrobe_rules.harmony_score([BEIGE_TEE, PURPLE_FLEECE, JEANS, WHITE_SNEAKERS]) < \
        wardrobe_rules.harmony_score([GREY_TEE, PURPLE_FLEECE, JEANS, WHITE_SNEAKERS])


def test_two_earth_layers_next_to_each_other_lose():
    muddy = wardrobe_rules.harmony_score([BROWN_TEE, OLIVE_OVERSHIRT, JEANS, WHITE_SNEAKERS])
    clean = wardrobe_rules.harmony_score([WHITE_TEE, OLIVE_OVERSHIRT, JEANS, WHITE_SNEAKERS])
    assert muddy < clean


def test_extra_layers_are_not_rewarded_just_for_being_one_more_item():
    closet = _closet(GREY_TEE, GREY_HOODIE, WINDBREAKER, JEANS, WHITE_SNEAKERS)
    mild = {"tmin": 19, "tmax": 21, "has_rain": False, "wind_ms": 2, "strong_wind": False, "sunny": False,
            "hot": False, "warm": True, "tags": ["warm"]}

    best = top_outfits(closet, mild, [], "", selected_styles=[])[0]

    assert _ids(best) == {"tee_grey", "jeans", "sneakers_white"}  # +21°: ни худи, ни куртки


@pytest.mark.parametrize("weather", list(WEATHERS))
def test_every_outfit_of_a_big_closet_is_logical_in_any_weather(weather):
    """Проверка «всех комбинаций»: образ дня и «Собрать образ» с каждой вещью шкафа — без нарушений логики."""
    closet = _closet(*EVERYTHING)
    ctx = wardrobe_suitcase._day_context(WEATHERS[weather])

    daily = top_outfits(closet, ctx, [], "", selected_styles=[])
    assert daily
    for outfit in daily:
        assert outfit_conflicts(outfit) == [], (weather, sorted(_ids(outfit)), outfit_conflicts(outfit))
    for item in EVERYTHING:
        for outfit in top_outfits(closet, ctx, [], "", selected_styles=[], anchor_id=item["id"]):
            assert item["id"] in _ids(outfit)
            assert outfit_conflicts(outfit) == [], (weather, item["id"], sorted(_ids(outfit)),
                                                    outfit_conflicts(outfit))
