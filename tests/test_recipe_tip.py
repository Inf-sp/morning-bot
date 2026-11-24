from ui import food as food_ui
from ui import menu as menu_ui

STEPS = ["ты смешай муку, сахар и щепотку соли, добавь холодное масло и поруби до крошки",
         "ты добавь яйцо и быстро замеси тесто, сформируй диск и охлади",
         "ты раскатай тесто, выложи в форму, положи джем и запеки"]


def test_steps_and_tip_start_with_capital_verb_and_tip_does_not_repeat_a_step():
    idea = {"cuisine": "italian", "name": "Crostata", "ingredients": ["мука 200 г"], "steps": STEPS,
            "tip": "охлади тесто перед раскаткой, чтобы оно не растягивалось."}
    text = menu_ui.food_menu(idea, meal="lunch").text
    assert "• Смешай муку" in text and "ты " not in text
    assert "охлади тесто перед" not in text.casefold()  # повтор шага «…и охлади» не показываем


def test_useful_tip_stays_and_gets_capital_letter():
    steps = [food_ui.recipe_sentence(s) for s in ("взбей яйца с солью", "вылей на сковороду и жарь")]
    assert food_ui.recipe_tip("сними сковороду с огня, когда центр ещё влажный", steps) == \
        "Сними сковороду с огня, когда центр ещё влажный."
