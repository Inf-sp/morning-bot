import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe
import wardrobe_suitcase
from ui import wardrobe as wardrobe_ui


def _item(name, temp=(5, 26), rain_ok=False):
    return {"name": name, "temp_range": list(temp), "rain_ok": rain_ok}


CLOTHES = [
    ("Верх", _item("Белая футболка", (15, 35))), ("Верх", _item("Серая футболка", (15, 35))),
    ("Низ", _item("Бежевые чиносы")), ("Низ", _item("Джинсы")),
    ("Обувь", _item("Белые кеды")),
    ("Верхняя одежда", _item("Пуховик", (-20, 5))), ("Верхняя одежда", _item("Ветровка", (5, 26), rain_ok=True)),
]


def test_ai_suitcase_keeps_only_real_wardrobe_items(monkeypatch):
    monkeypatch.setattr(wardrobe_suitcase.ai, "llm_json",
                        lambda *_a, **_k: {"items": [0, 2, 2, 99, "кроссовки", 4], "tip": "Вечером у океана прохладно"})
    monkeypatch.setattr(wardrobe_suitcase.store, "get_settings", lambda _cid: {"city": "Кейптаун"})
    monkeypatch.setattr(wardrobe_suitcase.vacation, "trip_weather_summary", lambda _cid: "")

    picked, tip = wardrobe_suitcase._ai_pick("42", CLOTHES, {}, 9)

    assert picked == [0, 2, 4]  # несуществующий номер, повтор и придуманная вещь отброшены
    assert tip == "Вечером у океана прохладно"


def test_rule_suitcase_fits_trip_weather_and_takes_rain_jacket():
    picked = wardrobe_suitcase.rule_pick(CLOTHES, {"tmin": 14, "tmax": 22, "rainy": 2}, days=9)
    names = [CLOTHES[i][1]["name"] for i in picked]

    assert "Ветровка" in names and "Пуховик" not in names  # тёплый пуховик не по погоде, дождь — ветровка
    assert {"Белая футболка", "Серая футболка", "Бежевые чиносы", "Джинсы", "Белые кеды"} <= set(names)


def test_suitcase_screen_lists_wardrobe_by_zone_with_weather_and_tip():
    msg = wardrobe_ui.suitcase_screen({
        "place": "Кейптаун", "days": 9, "weather": "днём от +17° до +24°, дождь в 2 из 7 дней, ветер до 9 м/с",
        "groups": [("Верх", ["Белая футболка", "Серая футболка"]), ("Верхняя одежда", ["Ветровка"])],
        "tip": "Вечером у океана прохладно",
    })

    assert msg.text == (
        "🧳 Чемодан · Кейптаун · 9 дней\n\n"
        "Днём от +17° до +24°, дождь в 2 из 7 дней, ветер до 9 м/с.\n\n"
        "Футболки и рубашки: белая футболка, серая футболка\n"
        "Куртки и ветровки: ветровка\n\n"
        "Вечером у океана прохладно."
    )
    assert wardrobe_ui.suitcase_screen({}).text.startswith("🧳 Чемодан\n\n")


def test_pack_button_only_on_vacation(monkeypatch):
    def callbacks(markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: False)
    assert "w_pack" not in callbacks(wardrobe.build_wardrobe_keyboard("42"))
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: True)
    # В отпуске нет стилей и «Что докупить»: «Ещё вариант» — новый образ дня, ниже «Собрать чемодан».
    assert callbacks(wardrobe.build_wardrobe_keyboard("42")) == ["w_look", "w_pack", "w_closet", "m_menu"]
    assert "w_pack" not in callbacks(wardrobe.build_wardrobe_keyboard())  # без чата — как дома


def test_vacation_look_uses_only_packed_items_when_suitcase_has_a_full_outfit(monkeypatch):
    wardrobe_data = {"_v": 2, "zones": {
        "Верх": {"Футболки": [{"name": "Белая футболка"}, {"name": "Чёрная футболка"}]},
        "Низ": {"Джинсы": [{"name": "Джинсы"}, {"name": "Брюки"}]},
        "Обувь": {"Кеды": [{"name": "Белые кеды"}]},
    }}
    profile = {"suitcase": {"key": "trip|abc", "result": {"groups": [
        ["Верх", ["Белая футболка"]], ["Низ", ["Джинсы"]], ["Обувь", ["Белые кеды"]]]}}}
    monkeypatch.setattr(wardrobe_suitcase.vacation, "active", lambda _cid: True)
    monkeypatch.setattr(wardrobe_suitcase.vacation, "trip_key", lambda _cid: "trip")
    monkeypatch.setattr(wardrobe_suitcase.store, "get_profile", lambda _cid: profile)

    packed = wardrobe_suitcase.packed_wardrobe("42", wardrobe_data)
    assert packed["zones"]["Верх"]["Футболки"] == [{"name": "Белая футболка"}]  # только из чемодана

    profile["suitcase"]["result"]["groups"] = [["Верх", ["Белая футболка"]]]  # нет низа и обуви
    assert wardrobe_suitcase.packed_wardrobe("42", wardrobe_data) is wardrobe_data
    profile["suitcase"]["key"] = "other-trip|abc"  # чемодан прошлой поездки не в счёт
    assert wardrobe_suitcase.packed_wardrobe("42", wardrobe_data) is wardrobe_data
