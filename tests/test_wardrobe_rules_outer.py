import wardrobe_outfit


def test_frost_with_strong_wind_picks_a_warm_coat_not_the_windbreaker():
    w = {"zones": {"Верхняя одежда": {
        "Ветровки": [{"id": "wb", "name": "Бежевая ветровка", "zone": "Верхняя одежда", "subcategory": "Ветровки",
                      "warmth": "лёгкие", "wind_ok": True, "colors": ["бежевый"]}],
        "Куртки": [{"id": "pk", "name": "Чёрная парка", "zone": "Верхняя одежда", "subcategory": "Куртки",
                    "warmth": "тёплые", "colors": ["чёрный"]}]}}}
    ctx = {"tmin": -5, "tmax": -2, "feels_min": -11, "feels_max": -6, "strong_wind": True, "wind_ms": 14,
           "gust_max": 14}
    outer = wardrobe_outfit.select_outfit_candidates(w, ctx)["Верхняя одежда"]
    assert [item["id"] for item in outer] == ["pk"]  # в мороз — тёплая, даже если ветровка непродуваемая
    mild = {**ctx, "tmin": 10, "tmax": 14, "feels_min": 9, "feels_max": 12}
    assert [item["id"] for item in wardrobe_outfit.select_outfit_candidates(w, mild)["Верхняя одежда"]] == ["wb"]
