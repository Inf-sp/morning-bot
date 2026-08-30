import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot_callbacks
import routing


def test_empty_learning_add_words_button_opens_dictionary_input(monkeypatch):
    """The add button must immediately ask the user to type a word in chat."""
    manage_calls = []

    async def send_dict_manage(bot, cid, lang, q=None):
        manage_calls.append((bot, cid, lang, q))

    class Bot:
        def __init__(self):
            self.messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.learning_router.dictionary, "send_dict_manage", send_dict_manage)

    class Query:
        data = "a_dictadd_smart_nl"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = Bot()

    bot_callbacks.store.pending_input.pop("42", None)
    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert manage_calls == []
    assert "напиши слово" in Context.bot.messages[0]["text"].lower()
    assert bot_callbacks.store.pending_input["42"] == "dictadd_smart_nl"
    bot_callbacks.store.pending_input.pop("42", None)


def test_my_dictionary_opens_from_learning_menu(monkeypatch):
    calls = []

    async def send_dict_lang(bot, cid, lang, page=0, back="m_learn", q=None):
        calls.append((bot, cid, lang, page, back, q))

    class Bot:
        pass

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(
        bot_callbacks.learning_router.dictionary, "send_dict_lang", send_dict_lang,
    )

    class Query:
        data = "a_dictlang_nl_from_menu"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = Bot()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls == [(Context.bot, "42", "nl", 0, "m_learn", Update.callback_query)]


def test_navigation_audit_recognizes_all_travel_saved_country_callbacks():
    callbacks = (
        "a_trav_countries_0",
        "a_trav_country_add",
        "a_trav_country_NL_0",
        "a_trav_country_del_NL_0",
        "a_trav_country_yes_NL_0",
    )

    assert all(routing.resolve_callback_handler(data)["handled"] for data in callbacks)


def test_cooking_learning_and_travel_menu_callbacks_are_routable():
    callbacks = {
        "cooking": (
            "m_food", "m_food_next", "as_food", "as_food_back",
            "as_fridge_home", "as_fridge_add", "as_fridge_pick_0",
            "as_fridge_cat_0_0", "as_fridge_clean_0", "as_fridge_tgl_0_0_0",
            "a_recipe_breakfast", "a_recipe_lunch", "a_recipe_dinner",
            "set_cuisines",
        ),
        "learning": (
            "m_learn", "a_train", "a_train_nl", "a_train_en", "a_train_progress",
            "a_game", "game_again", "game_hint", "game_reveal", "a_dictadd_smart_nl",
            "a_dictseed_start_nl", "a_dictlang_nl_from_menu", "a_dictlang_nl", "a_dictlang_active", "a_dictcat_nl_2_0",
            "a_dictcatdel_nl_2_0_word", "a_dictcatdelok_nl_2_0_word",
            "a_dictedit_nl", "a_dictviewid_0_word", "set_learning", "set_learning_dict",
            "toggle_learning_language", "toggle_learning_language_dict", "set_learning_global", "set_learning_language_nl", "set_learning_language_en_dict", "set_learning_language_none", "set_learning_language_nl_settings", "set_learning_level_easy", "set_learning_level_easy_dict",
        ),
        "travel": (
            "m_travel", "a_trav_go", "a_trav_no", "a_trav_plan", "a_trav_fav",
            "a_trav_countries_0", "a_trav_country_add",
            "a_trav_country_NL_0", "a_trav_country_del_NL_0",
            "a_trav_country_yes_NL_0", "a_trav_transport", "a_trav_mode_train",
        ),
        "music": (
            "music_reco", "music_genre_menu", "music_g_indie",
        ),
    }

    missing = {
        section: [data for data in actions if not routing.resolve_callback_handler(data)["handled"]]
        for section, actions in callbacks.items()
    }

    assert missing == {section: [] for section in callbacks}
