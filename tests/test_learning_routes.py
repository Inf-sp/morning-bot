import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot_callbacks
import routing


def test_empty_learning_add_words_button_opens_dictionary_input(monkeypatch):
    """The first-use button must not fall through the generic a_ router."""
    calls = []

    async def send_dict_manage(bot, cid, lang, q=None):
        calls.append((bot, cid, lang, q))

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.balance.thoughts, "cancel_capture", lambda _cid: None)
    monkeypatch.setattr(bot_callbacks.learning_router.dictionary, "send_dict_manage", send_dict_manage)

    class Query:
        data = "a_dictadd_smart_nl"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls == [(Context.bot, "42", "nl", Update.callback_query)]


def test_navigation_audit_recognizes_all_travel_saved_country_callbacks():
    callbacks = (
        "a_trav_countries_0",
        "a_trav_country_add",
        "a_trav_country_NL_0",
        "a_trav_country_del_NL_0",
        "a_trav_country_yes_NL_0",
    )

    assert all(routing.resolve_callback_handler(data)["handled"] for data in callbacks)


def test_cooking_learning_health_and_travel_menu_callbacks_are_routable():
    callbacks = {
        "cooking": (
            "m_food", "m_food_next", "as_food", "as_food_back", "as_recipe_save",
            "as_my_recipes", "as_fridge_home", "as_fridge_add", "as_fridge_pick_0",
            "as_fridge_cat_0_0", "as_fridge_clean_0", "as_fridge_tgl_0_0_0",
            "as_my_recipe_0", "a_recipe_breakfast", "a_recipe_lunch", "a_recipe_dinner",
            "set_cuisines",
        ),
        "learning": (
            "m_learn", "a_train", "a_train_nl", "a_train_en", "a_train_progress",
            "a_game", "game_again", "game_hint", "game_reveal", "a_dictadd_smart_nl",
            "a_dictseed_start_nl", "a_dictlang_nl_from_menu", "a_dictlang_nl",
            "a_dictedit_nl", "a_dictviewid_0_word", "set_learning",
            "toggle_learning_language", "set_learning_level_easy",
        ),
        "health": (
            "m_balance", "as_health_principles", "as_health_principle_sleep",
            "as_daycheck", "as_motiv", "as_medicine", "as_doctor", "thought_capture",
            "thought_review", "thought_review_later", "thought_review_clear",
            "thought_review_clear_cancel", "thought_review_clear_yes",
        ),
        "travel": (
            "m_travel", "a_trav_go", "a_trav_no", "a_trav_plan", "a_trav_fav",
            "a_trav_save", "a_trav_countries_0", "a_trav_country_add",
            "a_trav_country_NL_0", "a_trav_country_del_NL_0",
            "a_trav_country_yes_NL_0", "a_trav_transport", "a_trav_mode_train",
        ),
    }

    missing = {
        section: [data for data in actions if not routing.resolve_callback_handler(data)["handled"]]
        for section, actions in callbacks.items()
    }

    assert missing == {section: [] for section in callbacks}
