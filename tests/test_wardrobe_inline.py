import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe
import bot_callbacks
import util
from ui.wardrobe import purchase_check_card


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_empty_wardrobe_explains_how_to_fill_it():
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()

    asyncio.run(wardrobe.send_home(bot, "pytest-wardrobe-inline"))

    assert bot.message["text"] == (
        "🧶 Гардероб\n\n"
        "Добавь вещи один раз — дальше я буду собирать образ за тебя.\n\n"
        "Пришли список всей своей одежды одним сообщением. Я сам разложу всё по шкафу."
    )
    assert bot.message["reply_markup"] is not None
    assert _labels(bot.message["reply_markup"]) == [
        ["🆕 Заполнить шкаф"],
        ["#️⃣ Главная"],
    ]


def test_loading_indicator_is_one_vertical_inline_button(monkeypatch):
    class Query:
        markup = None

        async def edit_message_reply_markup(self, **kwargs):
            self.markup = kwargs["reply_markup"]

    monkeypatch.setattr(util, "loading_phrase", lambda: "🔍 Ищу нужную информацию…")
    query = Query()

    asyncio.run(util.ack_loading(query))

    assert len(query.markup.inline_keyboard) == 1
    assert len(query.markup.inline_keyboard[0]) == 1
    assert query.markup.inline_keyboard[0][0].text == "🔍 Ищу нужную информацию…"


def test_preserved_inline_status_changes_only_loading_button():
    class Message:
        def __init__(self):
            self.text_edits = []
            self.markup_edits = []

        async def edit_text(self, text, **kwargs):
            self.text_edits.append((text, kwargs))

        async def edit_reply_markup(self, **kwargs):
            self.markup_edits.append(kwargs["reply_markup"])

    class Query:
        message = Message()

    status = asyncio.run(util.StatusManager.start_inline(
        Query(), stages=((0, "⏳ Ищу..."),), preserve_message=True))
    assert Query.message.text_edits == []
    assert len(Query.message.markup_edits) == 1
    assert len(Query.message.markup_edits[0].inline_keyboard) == 1
    assert len(Query.message.markup_edits[0].inline_keyboard[0]) == 1

    asyncio.run(status.replace("Готовая карточка", reply_markup="final-kb"))

    assert Query.message.text_edits == [("Готовая карточка", {"reply_markup": "final-kb"})]
    assert len(Query.message.markup_edits) == 1


def test_inline_status_does_not_duplicate_loading_text_and_button():
    class Message:
        def __init__(self):
            self.text_edits = []
            self.markup_edits = []

        async def edit_text(self, text, **kwargs):
            self.text_edits.append((text, kwargs))

        async def edit_reply_markup(self, **kwargs):
            self.markup_edits.append(kwargs["reply_markup"])

    class Query:
        message = Message()

    status = asyncio.run(util.StatusManager.start_inline(
        Query(), stages=((0, "🔍 Подбираю разбор..."),), preserve_message=False))

    assert Query.message.text_edits == []
    assert _labels(Query.message.markup_edits[0]) == [["🔍 Подбираю разбор..."]]

    asyncio.run(status.replace("Готовая карточка", reply_markup="final-kb"))

    assert Query.message.text_edits == [("Готовая карточка", {"reply_markup": "final-kb"})]


def test_inline_status_progresses_through_all_waiting_stages():
    class Message:
        def __init__(self):
            self.markup_edits = []

        async def edit_reply_markup(self, **kwargs):
            self.markup_edits.append(kwargs["reply_markup"])

    class Query:
        message = Message()

    async def run_status():
        status = await util.StatusManager.start_inline(
            Query(),
            stages=(
                (0, "🕵️ Ищу загадку..."),
                (0, "📖 Проверяю текст..."),
                (0, "🧩 Собираю загадку..."),
            ),
            preserve_message=True,
        )
        await asyncio.sleep(0.01)
        await status.stop(delete=False)

    asyncio.run(run_status())

    assert [
        markup.inline_keyboard[0][0].text if markup is not None else None
        for markup in Query.message.markup_edits
    ] == [
        "🕵️ Ищу загадку...",
        "📖 Проверяю текст...",
        "🧩 Собираю загадку...",
        None,
    ]


def test_text_status_updates_its_message_without_an_inline_loading_button():
    class Message:
        def __init__(self):
            self.text_edits = []
            self.markup_edits = []

        async def edit_text(self, text, **kwargs):
            self.text_edits.append((text, kwargs))

        async def edit_reply_markup(self, **kwargs):
            self.markup_edits.append(kwargs["reply_markup"])

    class Bot:
        def __init__(self):
            self.message = Message()

        async def send_message(self, **_kwargs):
            return self.message

    async def run_status():
        bot = Bot()
        status = await util.StatusManager.start(
            bot, "42", stages=((0, "⏳ Подбираю перевод..."), (0, "🔍 Подбираю разбор...")),
        )
        await asyncio.sleep(0.01)
        await status.stop(delete=False)
        return bot.message

    message = asyncio.run(run_status())

    assert message.text_edits == [("🔍 Подбираю разбор...", {})]
    assert message.markup_edits == []


def test_preserved_inline_status_sends_ready_result_as_new_message():
    sent = []

    class Message:
        reply_markup = "old-kb"
        text_edits = []
        markup_edits = []

        async def edit_text(self, text, **kwargs):
            self.text_edits.append((text, kwargs))

        async def edit_reply_markup(self, **kwargs):
            self.markup_edits.append(kwargs["reply_markup"])

    class Query:
        message = Message()

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    status = asyncio.run(util.StatusManager.start_inline(
        Query(), bot=Bot(), cid="42", stages=((0, "⏳ Ищу..."),), preserve_message=True))
    asyncio.run(status.replace("Готовая карточка", reply_markup="final-kb"))

    assert sent == [{"chat_id": "42", "text": "Готовая карточка", "reply_markup": "final-kb"}]
    assert Query.message.text_edits == []
    assert Query.message.markup_edits[-1] == "old-kb"


def test_inline_status_does_not_send_duplicate_after_uncertain_edit():
    sent = []

    class Message:
        reply_markup = "old-kb"

        def __init__(self):
            self.text_edits = []

        async def edit_text(self, text, **kwargs):
            self.text_edits.append((text, kwargs))
            if len(self.text_edits) == 2:
                raise TimeoutError("Telegram response was lost after the edit")

        async def edit_reply_markup(self, **kwargs):
            pass

    class Query:
        message = Message()

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    status = asyncio.run(util.StatusManager.start_inline(
        Query(), bot=Bot(), cid="42", stages=((0, "⏳ Ищу..."),), preserve_message=False))
    asyncio.run(status.replace("Готовая карточка", reply_markup="final-kb"))

    assert sent == []
    assert status._finalized is True


def test_cached_home_edits_once_without_loading_message(monkeypatch):
    cached = {
        "date": wardrobe._day_key(),
        "text": "cached",
        "look_data": {
            "items": [{"name": "Белая футболка"}, {"name": "Синие брюки"}, {"name": "Белые кеды"}],
            "reasons": ["Светлый верх поддерживает обувь"],
            "style_tip": "Заправь футболку только спереди",
            "final_text": "ничего добавлять не нужно",
        },
    }
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: cached)

    class Message:
        edits = []

        async def edit_text(self, *args, **kwargs):
            self.edits.append((args, kwargs))

    class Query:
        message = Message()

    class Bot:
        sends = []

        async def send_message(self, **kwargs):
            self.sends.append(kwargs)

    q = Query()
    bot = Bot()
    asyncio.run(wardrobe.send_home(bot, "cached-fast", q=q))

    assert len(q.message.edits) == 1
    assert bot.sends == []


def test_purchase_check_card_uses_decision_format_and_limits_outfits():
    message = purchase_check_card({
        "verdict": "брать",
        "fits_count": 3,
        "duplicates": "нет",
        "closes_gap": "да",
        "why": "Добавляет недостающий яркий низ и сочетается с базовыми вещами",
        "wear_with": ["С белой футболкой", "С чёрной рубашкой", "Третий комплект"],
    })

    assert message.text.startswith("🧐 Проверка покупки")
    assert "Вердикт: брать." in message.text
    assert "Подойдёт: к 3 вещам из шкафа" in message.text
    assert "Дублирует: нет." in message.text
    assert "Закрывает пробел: да." in message.text
    assert "Почему: добавляет недостающий яркий низ" in message.text
    assert "Как носить:" in message.text
    assert "Третий комплект" not in message.text


def test_purchase_check_rejects_unexplained_negative_verdict():
    result = wardrobe._normalize_purchase_check({
        "verdict": "не брать",
        "not_buy_reason": "style",
        "why": "Не соответствует стилю",
        "fits_count": 4,
        "duplicates": "нет",
        "closes_gap": "нет",
    })

    assert result["verdict"] == "недостаточно данных"
    assert "конкретной причины" in result["why"]


def test_purchase_check_keeps_supported_negative_verdict():
    result = wardrobe._normalize_purchase_check({
        "verdict": "не брать",
        "not_buy_reason": "duplicate",
        "why": "Почти полностью дублирует уже имеющуюся красную юбку",
        "fits_count": 3,
        "duplicates": "да",
        "closes_gap": "нет",
    })

    assert result["verdict"] == "не брать"


def test_purchase_check_does_not_invent_zero_compatibility():
    result = wardrobe._normalize_purchase_check({"verdict": "недостаточно данных"})
    message = purchase_check_card(result)

    assert result["fits_count"] == "недостаточно данных"
    assert "Подойдёт: недостаточно данных" in message.text


def test_wardrobe_home_actions_use_one_column():
    assert _labels(wardrobe.build_wardrobe_keyboard())[:3] == [
        ["✨ Подобрать другой образ"],
        ["🧐 Оценить новую покупку"],
        ["🎚️ Мой шкаф"],
    ]
    assert "📌 Предпочтения" not in sum(_labels(wardrobe.build_wardrobe_keyboard()), [])


def test_other_outfit_keeps_result_card_instead_of_deleting_it(monkeypatch):
    calls = []

    class Status:
        async def stop(self, delete=True):
            calls.append(("stop", delete))

    status = Status()

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", q, bot, cid, stages, preserve_message))
        return status

    async def unexpected_start(*_args, **_kwargs):
        raise AssertionError("inline wardrobe refresh must not use message-mode status")

    async def fake_send_looks(bot, cid, **kwargs):
        calls.append(("send_looks", bot, cid, kwargs))
        assert kwargs["status"] is status

    monkeypatch.setattr(wardrobe.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(wardrobe.util.StatusManager, "start", unexpected_start)
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: {
        "item_ids": ["old-item"],
        "look_data": {},
    })
    monkeypatch.setattr(wardrobe, "send_looks", fake_send_looks)

    class Query:
        message = object()

    asyncio.run(wardrobe.handle_callback(object(), "42", Query(), "w_look"))

    assert calls[0][0] == "start_inline"
    assert calls[0][-1] is True
    assert calls[-1] == ("stop", True)


def test_wardrobe_callback_reuses_shared_inline_status(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def handle_callback(bot, cid, q, data, status=None):
        calls.append(("wardrobe", bot, cid, q, data, status))
        assert status.mode == "inline"

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", q, bot, cid, stages, preserve_message))
        return Status()

    async def ack_loading(q):
        calls.append(("ack_loading", q))

    monkeypatch.setattr(bot_callbacks.wardrobe, "handle_callback", handle_callback)
    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks, "_ack", ack_loading)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.balance.thoughts, "cancel_capture", lambda _cid: None)

    class Query:
        data = "w_look"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls[0][0] == "start_inline"
    assert calls[0][-1] is True
    assert calls[1][0] == "wardrobe"
    assert calls[1][-1].mode == "inline"
    assert calls[-1] == ("stop", True)


def test_book_refresh_uses_preserved_inline_status(monkeypatch):
    calls = []

    class Status:
        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", preserve_message))
        return Status()

    async def book_dislike(bot, cid, index):
        calls.append(("book_dislike", bot, cid, index))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.leisure_books, "book_dislike", book_dislike)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.balance.thoughts, "cancel_capture", lambda _cid: None)

    class Query:
        data = "book_no_0"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls[0] == ("start_inline", True)
    assert calls[1][-3:] == (Context.bot, "42", 0)
    assert calls[-1] == ("stop", True)


def test_week_forecast_uses_preserved_inline_status(monkeypatch):
    calls = []

    class Status:
        mode = "inline"

        async def stop(self, delete=True):
            calls.append(("stop", delete))

    async def start_inline(q, bot=None, cid=None, stages=None, preserve_message=False):
        calls.append(("start_inline", preserve_message))
        return Status()

    async def send_weather(bot, cid, mode, status=None):
        calls.append(("weather", bot, cid, mode, status.mode))

    monkeypatch.setattr(bot_callbacks.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(bot_callbacks.weather, "send_weather", send_weather)
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.balance.thoughts, "cancel_capture", lambda _cid: None)

    class Query:
        data = "a_w_week"
        message = type("Message", (), {"chat_id": "42", "message_id": 7})()

    class Update:
        callback_query = Query()

    class Context:
        bot = object()

    asyncio.run(bot_callbacks.handle(Update(), Context(), None))

    assert calls[0] == ("start_inline", True)
    assert calls[1][-2:] == ("week", "inline")
    assert calls[-1] == ("stop", True)


def test_main_menu_sections_replace_the_welcome_before_starting_a_result(monkeypatch):
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot_callbacks.balance.thoughts, "cancel_capture", lambda _cid: None)

    class Message:
        chat_id = "42"
        message_id = 7

        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    class Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("first-level menu must edit the current welcome")

    for callback_data, title in (
        ("m_myday", "Мой день"), ("m_wardrobe", "Гардероб"),
        ("m_food", "Готовка"), ("m_learn", "Обучение"),
        ("m_balance", "Здоровье"), ("m_travel", "Поездки"),
        ("m_music", "Музыка"), ("m_movie", "Кино"), ("m_books", "Книги"),
        ("m_settings", "Настройки"),
    ):
        message = Message()
        query = type("Query", (), {"data": callback_data, "message": message})()
        update = type("Update", (), {"callback_query": query})()
        asyncio.run(bot_callbacks.handle(update, type("Context", (), {"bot": Bot()})(), None))

        assert len(message.edits) == 1
        assert title in message.edits[0][0]


def test_closet_screen_uses_one_column_without_edit_button(monkeypatch):
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: {
        "zones": {"Верх": {"Футболки": [{"id": "top-1", "name": "Футболка"}]}}
    })

    bot = Bot()
    asyncio.run(wardrobe.send_wardrobe_zones(bot, "closet-test"))

    labels = _labels(bot.message["reply_markup"])
    assert labels[-2] == ["🆕 Добавить вещь"]
    assert labels[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert all(len(row) == 1 for row in labels[:-1])
    assert all("✏️ Изменить" not in row for row in labels)
