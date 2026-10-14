import os
from datetime import date, timedelta

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe_outfit


def _wardrobe():
    def items(zone, names):
        return [{"id": f"{zone}-{i}", "zone": zone, "name": name, "colors": [color], "fit": "прямая", "use_count": 0}
                for i, (name, color) in enumerate(names)]
    return {"zones": {
        "Верх": {"Футболки": items("Верх", [("Белая футболка", "белый"), ("Серая футболка", "серый"),
                                            ("Чёрная футболка", "чёрный"), ("Бежевая рубашка", "бежевый"),
                                            ("Белая рубашка", "белый")])},
        "Низ": {"Брюки": items("Низ", [("Чёрные брюки", "чёрный"), ("Серые брюки", "серый"),
                                        ("Бежевые чиносы", "бежевый"), ("Тёмные джинсы", "тёмно-серый")])},
        "Обувь": {"Кеды": items("Обувь", [("Белые кеды", "белый"), ("Чёрные ботинки", "чёрный"),
                                           ("Серые кроссовки", "серый")])},
    }}


def test_daily_outfit_rotates_through_the_whole_closet():
    w = _wardrobe()
    weather = {"tmax": 17, "tmin": 11, "has_rain": False, "tags": ["cool"]}
    history, worn = [], set()
    start = date(2026, 10, 14)
    for day in range(7):
        today = (start + timedelta(days=day)).isoformat()
        best = wardrobe_outfit.top_outfits(w, weather, history, "")[0]
        ids = [it["id"] for it in best]
        worn |= set(ids)
        history.append({"date": today, "item_ids": ids})
        for _zone, _sub, item in wardrobe_outfit._flat_wardrobe_items(w):  # как save_outfit_feedback
            if item["id"] in ids:
                item["use_count"] += 1
                item["last_used"] = today

    # За неделю в образы попадает весь шкаф, а не первые три вещи каждой зоны.
    assert worn == {it["id"] for _z, _s, it in wardrobe_outfit._flat_wardrobe_items(w)}


def test_stylist_gets_only_outfits_that_do_not_repeat_recent_looks():
    def outfit(*ids):
        return [{"id": item_id} for item_id in ids]
    yesterday = date(2026, 10, 13).isoformat()
    history = [{"date": yesterday, "item_ids": ["t1", "b1", "s1"]}]
    favourite, near_repeat, fresh = outfit("t1", "b1", "s2"), outfit("t2", "b1", "s1"), outfit("t2", "b2", "s1")

    assert wardrobe_outfit.fresh_outfits([favourite, near_repeat, fresh], history, date(2026, 10, 14)) == [fresh]
    # Маленький шкаф: свежих нет — ИИ получает всё, образ всё равно собирается.
    assert wardrobe_outfit.fresh_outfits([favourite], history, date(2026, 10, 14)) == [favourite]
    # Старше FRESH_DAYS — уже не повтор.
    assert wardrobe_outfit.fresh_outfits([favourite, fresh], history, date(2026, 10, 17)) == [favourite, fresh]
