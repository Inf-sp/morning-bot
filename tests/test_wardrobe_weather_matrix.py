"""Матрица погоды: для типичного шкафа образ верный от жары до мороза (диапазоны «ощущается» — docs/wardrobe.md)."""
import pytest

import wardrobe_model
import wardrobe_outfit
import wardrobe_rules

def item(i, zone, sub, name, warmth="обычные", colors=("чёрный",), **kw):
    return {"id": i, "zone": zone, "subcategory": sub, "name": name, "warmth": warmth, "colors": list(colors),
            "temp_range": list(wardrobe_model.WARMTH_TEMP_RANGE[warmth]), **kw}

W = {"zones": {
 "Верх": {"Футболки": [item("t1","Верх","Футболки","Белая футболка",colors=("белый",)),
                      item("t2","Верх","Футболки","Бежевая футболка с оранжевым принтом",colors=("бежевый","оранжевый"))],
          "Рубашки": [item("s1","Верх","Рубашки","Льняная рубашка","лёгкие",("белый",),material="лён")],
          "Майки": [item("m1","Верх","Майки","Чёрная майка","лёгкие")]},
 "Кофты": {"Худи": [item("h1","Кофты","Худи","Оливковое худи с капюшоном",colors=("оливковый",))],
           "Свитеры": [item("h2","Кофты","Свитеры","Серый шерстяной свитер","тёплые",("серый",),material="шерсть")]},
 "Низ": {"Брюки": [item("b1","Низ","Брюки","Чёрные брюки"), item("b2","Низ","Брюки","Льняные брюки","лёгкие",("бежевый",),material="лён")],
         "Джинсы": [item("j1","Низ","Джинсы","Синие джинсы",colors=("синий",))],
         "Шорты": [item("sh","Низ","Шорты","Чёрные шорты","лёгкие")]},
 "Обувь": {"Кроссовки": [item("k1","Обувь","Кроссовки","Чёрные Timberland кроссовки")],
           "Кеды": [item("k2","Обувь","Кеды","Белые текстильные кеды","лёгкие",("белый",),material="текстиль")],
           "Ботинки": [item("k3","Обувь","Ботинки","Коричневые утеплённые ботинки","тёплые",("коричневый",),rain_ok=True)],
           "Сандалии": [item("k4","Обувь","Сандалии","Чёрные сандалии","лёгкие")]},
 "Верхняя одежда": {"Ветровки": [item("o1","Верхняя одежда","Ветровки","Бежевая ветровка",colors=("бежевый",),wind_ok=True)],
                    "Куртки": [item("o2","Верхняя одежда","Куртки","Зелёная куртка",colors=("зелёный",)),
                               item("o3","Верхняя одежда","Куртки","Чёрная пуховая парка","тёплые",wind_ok=False)],
                    "Дождевики": [item("o4","Верхняя одежда","Дождевики","Чёрный дождевик","лёгкие",rain_ok=True,wind_ok=True)]}}}

def ctx(tmin, tmax, fmin, fmax, rain=False, gust=4, wind=3, sunny=False):
    strong = gust >= 10 or wind >= 8
    return {"tmin": tmin, "tmax": tmax, "has_rain": rain, "wind_ms": wind, "strong_wind": strong,
            "sunny": sunny and not rain and tmax >= 24, "hot": tmax >= 24, "warm": 17 <= tmax < 24,
            "feels_min": fmin, "feels_max": fmax, "gust_max": gust, "rain_from": 9 if rain else None,
            "layering": fmax - fmin >= 7 and fmin < 18}


# имя: (погода, верхняя одежда, кофта)
CASES = {
    "жара +31": (ctx(22, 31, 23, 32, sunny=True), None, None),
    "+24 сухо": (ctx(17, 24, 18, 25), None, None),
    "+20 дождь": (ctx(15, 20, 14, 20, rain=True), "Чёрный дождевик", None),
    "+17 сухо": (ctx(10, 17, 9, 17), "Бежевая ветровка", "Оливковое худи с капюшоном"),
    "+12 ветер": (ctx(8, 12, 4, 10, gust=12, wind=7), "Зелёная куртка", "Оливковое худи с капюшоном"),
    "+10 дождь": (ctx(6, 10, 3, 9, rain=True, gust=8, wind=5), "Зелёная куртка", "Оливковое худи с капюшоном"),
    "+6 сухо": (ctx(2, 6, -1, 4), "Чёрная пуховая парка", "Серый шерстяной свитер"),
    "+3 ветер и дождь": (ctx(1, 3, -4, 0, rain=True, gust=15, wind=9), "Чёрная пуховая парка", "Серый шерстяной свитер"),
    "-2 порывы 14": (ctx(-5, -2, -11, -6, gust=14, wind=9), "Чёрная пуховая парка", "Серый шерстяной свитер"),
    "-8 мороз": (ctx(-12, -8, -18, -12), "Чёрная пуховая парка", "Серый шерстяной свитер"),
}


@pytest.mark.parametrize("name", list(CASES))
def test_outfit_fits_weather(name):
    weather, outer, sweater = CASES[name]
    best = wardrobe_outfit.pick_best_outfit(W, weather, {}, "", selected_styles=None)
    assert best, "в любую погоду образ собирается"
    zones = {item["zone"]: item for item in best}
    assert (zones.get("Верхняя одежда") or {}).get("name") == outer
    assert (zones.get("Кофты") or {}).get("name") == sweater
    low = weather["feels_min"]
    if low >= wardrobe_rules.WARM_ITEMS_FEELS_MIN:
        assert all(item["warmth"] != "тёплые" for item in best)
    if "Кофты" in zones and "Верхняя одежда" in zones:
        assert sum(item["zone"] == "Верх" for item in best) == 1  # без рубашки четвёртым слоем
    if weather["has_rain"]:
        assert zones["Обувь"].get("material") != "текстиль"
