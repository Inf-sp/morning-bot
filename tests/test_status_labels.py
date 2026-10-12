import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import pytest

import bot_callbacks
from fakes import RecordingBot


def test_movie_recommendation_keeps_main_screen_while_loading(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", preserve_message))
        return Status()

    async def send_recos(bot, cid, kind, status=None):
        calls.append(("send_recos", cid, kind, status))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.leisure_movies, "send_recos", send_recos)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)

    class Query:
        data = "movie_reco"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls[0] == ("start_inline", True)
    assert calls[1][:3] == ("send_recos", "42", "movie")
    assert calls[-1] == ("stop", True)




def test_weather_warning_opens_myday_without_replacing_the_warning(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", preserve_message))
        return Status()

    async def send_plany(bot, cid, status=None):
        calls.append(("send_plany", cid, status.mode))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.myday, "send_plany", send_plany)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)

    class Query:
        data = "weather_myday"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls == [
        ("start_inline", True),
        ("send_plany", "42", "inline"),
        ("stop", True),
    ]


def test_learning_notification_opens_learning_without_replacing_words(monkeypatch):
    sent = []
    markup = object()

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.trainer, "cancel", lambda _cid: None)
    monkeypatch.setattr(
        bot_callbacks.menu,
        "menu_screen",
        lambda key, cid: (f"{key}:{cid}", [], markup),
    )

    class Query:
        data = "notify_learning"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = RecordingBot(sent)

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert sent == [{
        "chat_id": "42",
        "text": "m_learn:42",
        "entities": [],
        "reply_markup": markup,
        "transient": True,
    }]


def test_inline_status_starts_with_action_specific_text():
    cases = {
        "m_food_next": "🍳 Составляю меню...",
        "w_look": "👕 Открываю шкаф...",
        "movie_reco": "🎬 Перебираю фильмы...",
        "book_reco": "📚 Листаю полки...",
        "music_g_indie": "🎧 Слушаю новинки...",
        "listen_no": "🎧 Слушаю новинки...",
        "a_concerts_nl": "🎫 Проверяю афиши...",
        "lz_concerts": "🎫 Проверяю афиши...",
        "m_leisure": "🎬 Смотрю афиши кинотеатров...",
        "game_again": "🕵️ Ищу загадку...",
        "a_recipe_lunch": "📖 Открываю книгу рецептов...",
        "food_mine": "🧊 Заглядываю в холодильник...",
        "m_myday": "☀️ Смотрю на погоду...",
        "set_preferences": "⚙️ Открываю настройки...",
    }

    for data, expected in cases.items():
        stages = bot_callbacks._status_stages(data)
        assert stages[0][1] == expected
        # Фразы меняются: четыре разные, все с «...» — зелёная кнопка ожидания.
        assert [delay for delay, _ in stages] == [0, 1.5, 3.5, 6]
        assert len({text for _, text in stages}) == 4 and all(text.endswith("...") for _, text in stages)


def test_slow_button_shows_rotating_waiting_text_and_restores_keyboard(monkeypatch):
    import telegram_runtime
    edits = []

    class Message:
        chat_id, message_id, reply_markup = "42", 7, "ORIGINAL"

        async def edit_reply_markup(self, reply_markup=None):
            telegram_runtime._count_edit("editMessageReplyMarkup", {"chat_id": "42", "message_id": 7})
            edits.append(reply_markup if reply_markup == "ORIGINAL" else reply_markup.inline_keyboard[0][0].text)

    class Query:
        message = Message()

    monkeypatch.setattr(bot_callbacks, "_WAITING_AFTER", 0.01)
    monkeypatch.setattr(bot_callbacks, "_status_stages", lambda _data: ((0, "Раз..."), (0.02, "Два...")))

    async def slow():
        await asyncio.sleep(0.06)

    async def run():
        await bot_callbacks._run_with_waiting(Query(), "lz_concerts", asyncio.ensure_future(slow()), lambda: False)

    asyncio.run(run())
    assert edits == ["Раз...", "Два...", "ORIGINAL"]  # ответ пришёл отдельно — кнопки вернулись


def test_waiting_indicator_stops_when_handler_shows_new_screen(monkeypatch):
    import telegram_runtime
    edits = []

    class Message:
        chat_id, message_id, reply_markup = "43", 8, "ORIGINAL"

        async def edit_reply_markup(self, reply_markup=None):
            telegram_runtime._count_edit("editMessageReplyMarkup", {"chat_id": "43", "message_id": 8})
            edits.append(reply_markup if reply_markup == "ORIGINAL" else reply_markup.inline_keyboard[0][0].text)

    class Query:
        message = Message()

    monkeypatch.setattr(bot_callbacks, "_WAITING_AFTER", 0.01)
    monkeypatch.setattr(bot_callbacks, "_status_stages", lambda _data: ((0, "Раз..."), (0.03, "Два...")))

    async def handler_edits_screen():
        await asyncio.sleep(0.02)
        telegram_runtime._count_edit("editMessageText", {"chat_id": "43", "message_id": 8})  # новый экран
        await asyncio.sleep(0.04)

    async def run():
        await bot_callbacks._run_with_waiting(Query(), "m_leisure", asyncio.ensure_future(handler_edits_screen()),
                                              lambda: False)

    asyncio.run(run())
    assert edits == ["Раз..."]  # новый экран не затёрт ни следующей фразой, ни старыми кнопками


def test_fast_button_shows_no_waiting_indicator():
    class Query:
        message = None

    async def fast():
        return "ok"

    async def run():
        return await bot_callbacks._run_with_waiting(Query(), "set_home", asyncio.ensure_future(fast()), lambda: False)

    assert asyncio.run(run()) == "ok"


def test_long_main_screens_have_a_specific_tracking_topic():
    cases = {
        "m_myday": "myday",
        "m_wardrobe": "wardrobe",
        "m_food": "food",
        "m_leisure": "leisure",
        "lz_prem": "leisure",
        "lz_lib": "leisure",
        "notify_learning": "learning",
    }

    for data, expected in cases.items():
        assert bot_callbacks._status_topic(data) == expected


def test_long_inline_actions_have_four_distinct_progress_stages():
    for data in (
        "game_again", "m_food_next", "w_look", "movie_reco", "book_reco",
        "listen_no", "music_g_indie", "a_concerts_nl",
        "a_dictadd_smart_nl", "ex_next_task", "m_myday",
    ):
        stages = bot_callbacks._status_stages(data)
        assert [delay for delay, _text in stages] == [0, 1.5, 3.5, 6]
        assert len({text for _delay, text in stages}) == 4


def test_removed_travel_callbacks_open_main_menu(monkeypatch):
    sent = []

    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.menu, "main_menu_screen", lambda _cid: ("menu", [], "kb"))

    for data in ("m_travel", "a_trav_go", "a_trav_country_NL_0", "a_trav_countries_0"):
        query = type("Query", (), {
            "data": data, "message": type("Message", (), {"chat_id": "42", "message_id": 7})(),
        })()
        update = type("Update", (), {"callback_query": query})()
        asyncio.run(bot_callbacks.handle(update, type("Context", (), {"bot": RecordingBot(sent)})(), None))

    assert [(m["text"], m["reply_markup"]) for m in sent] == [("menu", "kb")] * 4
    assert bot_callbacks._status_topic("a_trav_go") is None


def test_replacing_card_deletes_old_only_after_new_card_arrived(monkeypatch):
    import telegram_runtime
    import verify
    deleted = []

    class Message:
        chat_id, message_id = "77", 5

        async def delete(self):
            deleted.append(self.message_id)

    ctx = bot_callbacks.Ctx(None, "77", type("Q", (), {"message": Message()})(), "movie_next", None)

    async def sends_new_card(_c):
        telegram_runtime._count_edit("sendPhoto", {"chat_id": "77"})

    async def edits_in_place(_c):
        telegram_runtime._count_edit("editMessageText", {"chat_id": "77", "message_id": 5})

    async def fails(_c):
        await verify.safe_error(RecordingBot([]), "77", RuntimeError("boom"))
        telegram_runtime._count_edit("sendMessage", {"chat_id": "77"})  # текст ошибки — тоже отправка

    asyncio.run(bot_callbacks._replaced(sends_new_card)(ctx))
    assert deleted == [5]  # новая карточка пришла — прежняя удалена
    for handler in (edits_in_place, fails):
        asyncio.run(bot_callbacks._replaced(handler)(ctx))
    assert deleted == [5]  # правка на месте или ошибка — прежняя остаётся
