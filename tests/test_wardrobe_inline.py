import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe
import bot_callbacks
import travel
import util
from ui.wardrobe import purchase_check_card, purchase_suggestions_card


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
    assert "Модный ребус" not in q.message.edits[0][0][0]
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
    assert "Как носить:" not in message.text


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
        ["✨ Обновить"],
        ["💳 Что докупить"],
        ["🎚️ Мой шкаф"],
    ]
    assert "📝 Предпочтения" not in sum(_labels(wardrobe.build_wardrobe_keyboard()), [])


def test_purchase_menu_immediately_recommends_a_missing_item(monkeypatch):
    calls = []

    async def recommend(_bot, cid):
        calls.append(cid)

    monkeypatch.setattr(wardrobe, "recommend_missing_purchase", recommend)

    asyncio.run(wardrobe.handle_callback(object(), "42", None, "w_buy"))

    assert calls == ["42"]
    assert _labels(wardrobe._purchase_hub_kb()) == [
        ["⬅️ Назад", "#️⃣ Главная"],
    ]


def test_purchase_suggestions_show_colors_and_three_real_outfits():
    message = purchase_suggestions_card({
        "item": "худи",
        "headline": "Лучше выбрать спокойный оттенок.",
        "colors": [
            {"color": "тёмно-синий", "reason": "собирает серые и оливковые вещи"},
        ],
        "outfits": ["Худи + серые джинсы", "Худи + оливковые брюки", "Худи + белые кеды"],
    })

    assert "💳 Что докупить · худи" in message.text
    assert "Лучшие цвета:\n• Тёмно-синий — собирает серые и оливковые вещи." in message.text
    assert "С чем носить:\n• Худи + серые джинсы." in message.text
    assert "• Худи + белые кеды." in message.text


def test_purchase_menu_recommends_three_gaps_and_waits_for_chat_request(monkeypatch):
    sent = []
    wardrobe_data = {
        "zones": {
            "Верх": {"Рубашки": [{"name": "Голубая рубашка", "zone": "Верх"}]},
            "Низ": {"Брюки": [{"name": "Бежевые брюки", "zone": "Низ"}]},
            "Обувь": {"Кеды": [{"name": "Белые кеды", "zone": "Обувь"}]},
        }
    }

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: wardrobe_data)
    monkeypatch.setattr(wardrobe, "has_wardrobe_items", lambda _cid: True)
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: None)
    monkeypatch.setattr(wardrobe._settings, "wardrobe_styles", lambda _cid: [])

    asyncio.run(wardrobe.recommend_missing_purchase(Bot(), "42"))

    assert sent[0]["text"].startswith("💳 Что докупить\n\nСерые широкие джинсы")
    assert "Серые широкие джинсы" in sent[0]["text"]
    assert "Закроют пробел в шкафу" in sent[0]["text"]
    assert wardrobe.store.pending_input["42"] == "wardrobe_buy"
    assert _labels(sent[0]["reply_markup"]) == [
        ["✨ Подобрать другую вещь"],
        ["⬅️ Назад", "#️⃣ Главная"],
    ]
    wardrobe.store.pending_input.pop("42", None)


def test_purchase_menu_uses_pexels_photo_with_text_fallback(monkeypatch):
    import wardrobe_photos

    sent = []
    wardrobe_data = {"zones": {
        "Верх": {"Рубашки": [{"name": "Голубая рубашка", "zone": "Верх"}]},
        "Низ": {"Брюки": [{"name": "Бежевые брюки", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Белые кеды", "zone": "Обувь"}]},
    }}

    class Bot:
        async def send_photo(self, **kwargs):
            sent.append(("photo", kwargs))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: wardrobe_data)
    monkeypatch.setattr(wardrobe, "has_wardrobe_items", lambda _cid: True)
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: None)
    monkeypatch.setattr(wardrobe._settings, "wardrobe_styles", lambda _cid: [])
    photo_calls = []

    def purchase_photo(item, audience):
        photo_calls.append((item, audience))
        return {
            "url": "https://images.pexels.com/example.jpg",
            "query": item,
            "alt": "Man wearing gray wide leg jeans",
        }

    monkeypatch.setattr(wardrobe.store, "get_profile", lambda _cid: {"name": "Vladimir"})
    monkeypatch.setattr(
        wardrobe_photos, "purchase_photo",
        purchase_photo,
    )

    asyncio.run(wardrobe.recommend_missing_purchase(Bot(), "42"))

    assert [kind for kind, _kwargs in sent] == ["photo"]
    assert sent[0][1]["photo"] == "https://images.pexels.com/example.jpg"
    assert sent[0][1]["caption"].startswith("💳 Что докупить")
    assert "Где купить" in sent[0][1]["caption"]
    assert photo_calls == [("Серые широкие джинсы", "male")]


def test_purchase_carousel_edits_the_same_photo_card(monkeypatch):
    import wardrobe_photos

    wardrobe_data = {"zones": {"Верх": {"Рубашки": [{"name": "Рубашка"}]}}}
    edited = []

    class Query:
        async def edit_message_media(self, **kwargs):
            edited.append(kwargs)

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: wardrobe_data)
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: None)
    monkeypatch.setattr(wardrobe._settings, "wardrobe_styles", lambda _cid: [])
    monkeypatch.setattr(wardrobe.store, "get_profile", lambda _cid: {"name": "Vladimir"})
    monkeypatch.setattr(
        wardrobe_photos, "purchase_photo",
        lambda item, audience: {
            "url": f"https://images.pexels.com/{audience}/{item}.jpg",
            "alt": "Matching clothing photo",
        },
    )
    monkeypatch.setattr(wardrobe_photos, "_photo_matches_item", lambda *_args: True)

    asyncio.run(wardrobe.show_purchase_page(object(), "42", 1, q=Query()))

    assert len(edited) == 1
    assert edited[0]["media"].caption.startswith("💳 Что докупить")
    assert _labels(edited[0]["reply_markup"])[0] == ["✨ Подобрать другую вещь"]


def test_purchase_photos_are_male_for_admin_even_without_profile_name(monkeypatch):
    monkeypatch.setattr(wardrobe.config, "CHAT_ID", "42")
    monkeypatch.setattr(wardrobe.store, "get_profile", lambda _cid: {})

    assert wardrobe._purchase_photo_audience("42") == "male"


def test_purchase_other_variant_uses_next_english_pexels_result(monkeypatch):
    import wardrobe_photos

    calls = []
    wardrobe_photos.purchase_photo.cache_clear()
    monkeypatch.setattr(
        wardrobe_photos, "pexels_photo",
        lambda query, **kwargs: calls.append((query, kwargs)) or {"url": "photo"},
    )

    wardrobe_photos.purchase_photo("Серые широкие джинсы", "male", 2)

    query, options = calls[0]
    assert query == "men gray wide leg jeans"
    assert not any("а" <= char.casefold() <= "я" for char in query)
    assert options["result_index"] == 2
    assert options["result_validator"]({
        "alt": "Man wearing a blue formal shirt",
    }) is False
    assert options["result_validator"]({
        "alt": "Man wearing grey wide leg jeans",
    }) is True


def test_purchase_photo_query_keeps_color_and_silhouette_in_english():
    import wardrobe_photos

    assert wardrobe_photos._purchase_query(
        "Молочная оверсайз-рубашка", "male",
    ) == "men cream oversized shirt"


def test_purchase_photo_rejects_an_image_that_describes_another_item():
    import wardrobe_photos

    assert wardrobe_photos._photo_matches_item(
        "Серые широкие джинсы",
        {"alt": "Man wearing a blue formal shirt"},
    ) is False
    assert wardrobe_photos._photo_matches_item(
        "Серые широкие джинсы",
        {"alt": "Man wearing gray wide leg jeans"},
    ) is True


def test_serpapi_shopping_is_primary_for_purchase_photo(monkeypatch):
    import wardrobe_photos

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"shopping_results": [
                {
                    "product_id": "wrong",
                    "title": "Men blue formal shirt",
                    "thumbnail": "https://shop.example/shirt.jpg",
                },
                {
                    "product_id": "right",
                    "title": "Men gray wide leg jeans",
                    "thumbnail": "https://shop.example/jeans.jpg",
                    "product_link": "https://shop.example/jeans",
                    "price": "€79",
                    "source": "Example Shop",
                },
            ]}

    calls = []
    wardrobe_photos.purchase_photo.cache_clear()
    monkeypatch.setattr(wardrobe_photos.config, "SERP_API_KEY", "test-key")
    monkeypatch.setattr(
        wardrobe_photos.requests, "get",
        lambda url, **kwargs: calls.append((url, kwargs)) or Response(),
    )
    monkeypatch.setattr(
        wardrobe_photos, "pexels_photo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Pexels called")),
    )
    monkeypatch.setattr(wardrobe_photos.api_usage, "record_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wardrobe_photos.provider_runtime, "record_result", lambda *_args, **_kwargs: None)

    photo = wardrobe_photos.purchase_photo("Серые широкие джинсы", "male")

    assert photo["provider"] == "serpapi"
    assert photo["id"] == "right"
    assert photo["url"] == "https://shop.example/jeans.jpg"
    assert photo["page_url"] == "https://shop.example/jeans"
    assert calls[0][1]["params"] == {
        "engine": "google_shopping",
        "q": "men gray wide leg jeans",
        "gl": "nl",
        "hl": "en",
        "api_key": "test-key",
    }


def test_purchase_photo_falls_back_to_pexels_when_shopping_has_no_match(monkeypatch):
    import wardrobe_photos

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {"shopping_results": [{
                "title": "Men blue formal shirt",
                "thumbnail": "https://shop.example/shirt.jpg",
            }]}

    wardrobe_photos.purchase_photo.cache_clear()
    monkeypatch.setattr(wardrobe_photos.config, "SERP_API_KEY", "test-key")
    monkeypatch.setattr(wardrobe_photos.requests, "get", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(wardrobe_photos.api_usage, "record_request", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wardrobe_photos.provider_runtime, "record_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(wardrobe_photos.provider_runtime, "activate_fallback", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        wardrobe_photos, "pexels_photo",
        lambda *_args, **_kwargs: {
            "provider": "pexels",
            "url": "https://images.pexels.com/jeans.jpg",
            "alt": "Man wearing gray wide leg jeans",
        },
    )

    photo = wardrobe_photos.purchase_photo("Серые широкие джинсы", "male")

    assert photo["provider"] == "pexels"


def test_fresh_purchase_candidates_exclude_the_current_carousel():
    wardrobe_data = {"zones": {
        "Верх": {"Рубашки": [{"name": "Голубая рубашка", "zone": "Верх"}]},
        "Низ": {"Брюки": [{"name": "Бежевые брюки", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Белые кеды", "zone": "Обувь"}]},
    }}
    current = wardrobe._purchase_candidates(wardrobe_data, limit=3)
    current_names = {item["item"].casefold() for item in current}

    fresh = wardrobe._purchase_candidates(
        wardrobe_data, limit=3, exclude_names=current_names,
    )

    assert fresh
    assert current_names.isdisjoint(item["item"].casefold() for item in fresh)


def test_recommend_another_purchase_replaces_the_current_batch(monkeypatch):
    import wardrobe_photos

    sent = []
    wardrobe_data = {"zones": {
        "Верх": {"Рубашки": [{"name": "Голубая рубашка", "zone": "Верх"}]},
        "Низ": {"Брюки": [{"name": "Бежевые брюки", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Белые кеды", "zone": "Обувь"}]},
    }}

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    cid = "fresh-purchase-batch"
    wardrobe.store.set_profile(cid, {})
    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: wardrobe_data)
    monkeypatch.setattr(wardrobe, "has_wardrobe_items", lambda _cid: True)
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: None)
    monkeypatch.setattr(wardrobe._settings, "wardrobe_styles", lambda _cid: [])
    monkeypatch.setattr(wardrobe_photos, "purchase_photo", lambda *_args: None)

    asyncio.run(wardrobe.recommend_missing_purchase(Bot(), cid))
    first_text = sent[-1]["text"]
    asyncio.run(wardrobe.recommend_another_purchase(Bot(), cid))
    second_text = sent[-1]["text"]

    assert first_text != second_text
    assert "Серые широкие джинсы" in first_text
    assert "Серые широкие джинсы" not in second_text


def test_rejected_third_purchase_option_does_not_return_in_later_batches(monkeypatch):
    import wardrobe_photos

    wardrobe_data = {"zones": {
        "Верх": {"Рубашки": [{"name": "Голубая рубашка", "zone": "Верх"}]},
        "Низ": {"Брюки": [{"name": "Бежевые брюки", "zone": "Низ"}]},
        "Обувь": {"Кеды": [{"name": "Белые кеды", "zone": "Обувь"}]},
    }}

    class Bot:
        async def send_message(self, **_kwargs):
            pass

    cid = "rejected-third-purchase"
    wardrobe.store.set_profile(cid, {})
    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: wardrobe_data)
    monkeypatch.setattr(wardrobe, "has_wardrobe_items", lambda _cid: True)
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: None)
    monkeypatch.setattr(wardrobe._settings, "wardrobe_styles", lambda _cid: [])
    monkeypatch.setattr(wardrobe_photos, "purchase_photo", lambda *_args: None)

    asyncio.run(wardrobe.recommend_missing_purchase(Bot(), cid))
    first = wardrobe.store.get_profile(cid)["wardrobe_purchase_carousel"]["items"]
    rejected = first[2]["item"]

    asyncio.run(wardrobe.show_purchase_page(Bot(), cid, 2))
    asyncio.run(wardrobe.recommend_another_purchase(Bot(), cid, page=2))
    rejected_history = wardrobe.store.get_profile(cid)["wardrobe_purchase_rejections"]["items"]
    assert rejected_history == [rejected]
    assert first[0]["item"] not in rejected_history

    asyncio.run(wardrobe.recommend_missing_purchase(Bot(), cid))
    later = wardrobe.store.get_profile(cid)["wardrobe_purchase_carousel"]["items"]

    assert rejected.casefold() not in {item["item"].casefold() for item in later}


def test_purchase_card_falls_back_to_text_when_photo_does_not_match(monkeypatch):
    import wardrobe_photos

    sent = []

    class Bot:
        async def send_photo(self, **kwargs):
            sent.append(("photo", kwargs))

        async def send_message(self, **kwargs):
            sent.append(("message", kwargs))

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: {
        "zones": {"Верх": {"Рубашки": [{"name": "Рубашка"}]}}
    })
    monkeypatch.setattr(
        wardrobe, "_purchase_carousel_candidates",
        lambda *_args, **_kwargs: [{
            "item": "Серые широкие джинсы", "reason": "закроют пробел",
        }],
    )
    monkeypatch.setattr(wardrobe, "_purchase_photo_audience", lambda _cid: "male")
    monkeypatch.setattr(
        wardrobe_photos, "purchase_photo",
        lambda *_args: {
            "url": "https://images.pexels.com/wrong.jpg",
            "alt": "Man wearing a blue formal shirt",
        },
    )

    asyncio.run(wardrobe.show_purchase_page(Bot(), "42"))

    assert [kind for kind, _kwargs in sent] == ["message"]


def test_purchase_analysis_uses_new_reserve_after_all_previous_ideas(monkeypatch):
    calls = []
    monkeypatch.setattr(wardrobe, "_purchase_carousel_signature", lambda *_args: "sig")
    monkeypatch.setattr(wardrobe.store, "get_profile", lambda *_args: {
        "wardrobe_purchase_rejections": {"items": ["Куртка"]},
    })
    monkeypatch.setattr(wardrobe.store, "mutate_profile", lambda *_args: None)

    def candidates(_cid, _wardrobe, exclude_names=None):
        calls.append(set(exclude_names or []))
        return [] if exclude_names else [{"item": "Куртка", "reason": "закрывает пробел"}]

    monkeypatch.setattr(wardrobe, "_missing_purchase_candidates", candidates)

    result = wardrobe._purchase_carousel_candidates("42", {}, reset=True)

    assert result[0]["item"] != "Куртка"
    assert len(calls) == 1


def test_purchase_analysis_has_a_local_candidate_even_if_analysis_is_empty(monkeypatch):
    monkeypatch.setattr(wardrobe, "_purchase_carousel_signature", lambda *_args: "sig")
    monkeypatch.setattr(wardrobe.store, "get_profile", lambda *_args: {})
    monkeypatch.setattr(wardrobe.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(wardrobe, "_missing_purchase_candidates", lambda *_args, **_kwargs: [])

    result = wardrobe._purchase_carousel_candidates("42", {}, reset=True)

    assert result[0]["item"] == "Универсальный верхний слой"


def test_purchase_refresh_never_returns_the_just_rejected_item(monkeypatch):
    monkeypatch.setattr(wardrobe, "_purchase_carousel_signature", lambda *_args: "sig")
    monkeypatch.setattr(wardrobe.store, "get_profile", lambda *_args: {})
    monkeypatch.setattr(wardrobe.store, "mutate_profile", lambda *_args: None)
    monkeypatch.setattr(wardrobe, "_missing_purchase_candidates", lambda *_args, **_kwargs: [])

    result = wardrobe._purchase_carousel_candidates(
        "42", {}, reset=True, exclude_names=["Универсальный верхний слой"],
    )

    assert result[0]["item"] != "Универсальный верхний слой"


def test_other_purchase_variant_requests_a_fresh_recommendation(monkeypatch):
    import wardrobe_photos

    wardrobe_data = {"zones": {"Верх": {"Рубашки": [{"name": "Рубашка"}]}}}
    calls, edited = [], []
    variants = [
        {"item": "Молочная оверсайз-рубашка", "reason": "добавит второй слой"},
        {"item": "Серые широкие джинсы", "reason": "закроют пробел"},
    ]

    class Query:
        async def edit_message_media(self, **kwargs):
            edited.append(kwargs)

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: wardrobe_data)
    monkeypatch.setattr(
        wardrobe, "_missing_purchase_candidates", lambda *_args, **_kwargs: variants,
    )
    monkeypatch.setattr(wardrobe, "_purchase_photo_audience", lambda _cid: "male")
    monkeypatch.setattr(
        wardrobe_photos, "purchase_photo",
        lambda item, audience, variant=0: calls.append((item, audience, variant))
        or {
            "url": f"https://images.pexels.com/{variant}.jpg",
            "alt": (
                "Man wearing cream oversized shirt"
                if "рубаш" in item.casefold()
                else "Man wearing gray wide leg jeans"
            ),
        },
    )
    wardrobe.store.set_profile("same-purchase-photo", {})

    asyncio.run(wardrobe.show_purchase_page(object(), "same-purchase-photo", 0, q=Query()))
    asyncio.run(wardrobe.show_purchase_page(
        object(), "same-purchase-photo", 1, q=Query(),
    ))

    assert calls == [
        ("Молочная оверсайз-рубашка", "male", 0),
        ("Серые широкие джинсы", "male", 0),
    ]
    assert "Серые широкие джинсы" in edited[1]["media"].caption
    assert edited[0]["reply_markup"].inline_keyboard[0][0].callback_data == "w_buy_new:0"

    refreshed = []

    async def recommend_another(_bot, cid, q=None, page=None):
        refreshed.append((cid, q, page))

    monkeypatch.setattr(
        wardrobe, "recommend_another_purchase", recommend_another, raising=False,
    )
    query = Query()
    asyncio.run(wardrobe.handle_callback(object(), "same-purchase-photo", query, "w_buy_new:1"))

    assert refreshed == [("same-purchase-photo", query, 1)]


def test_purchase_suggestions_keep_only_outfits_with_real_wardrobe_items(monkeypatch):
    wardrobe_data = {
        "zones": {
            "Верх": {"Худи": [{"name": "Оливковая худи", "zone": "Верх"}]},
            "Низ": {"Джинсы": [{"name": "Серые джинсы", "zone": "Низ"}]},
            "Обувь": {"Кеды": [{"name": "Белые кеды", "zone": "Обувь"}]},
        }
    }
    sent = []

    class Bot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    async def recommend(*_args, **_kwargs):
        return {
            "colors": [{"color": "бордовый", "reason": "даёт акцент"}],
            "outfits": ["Худи + выдуманные вещи"],
        }

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: wardrobe_data)
    monkeypatch.setattr(wardrobe.store, "wardrobe_to_text", lambda _w: "Оливковая худи; Серые джинсы; Белые кеды")
    monkeypatch.setattr(wardrobe._settings, "wardrobe_prefs_context", lambda _cid: "")
    monkeypatch.setattr(wardrobe.ai, "allm_json", recommend)

    asyncio.run(wardrobe.recommend_purchase(Bot(), "42", "худи"))

    assert "Бордовый — даёт акцент." in sent[0]["text"]
    assert "выдуманные вещи" not in sent[0]["text"]
    assert "Оливковая худи + Серые джинсы" in sent[0]["text"]


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


def test_main_menu_static_sections_replace_the_welcome(monkeypatch):
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)

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
        ("m_learn", "Обучение"),
        ("m_settings", "Настройки"),
    ):
        message = Message()
        query = type("Query", (), {"data": callback_data, "message": message})()
        update = type("Update", (), {"callback_query": query})()
        asyncio.run(bot_callbacks.handle(update, type("Context", (), {"bot": Bot()})(), None))

        assert len(message.edits) == 1
        assert title in message.edits[0][0]


def test_main_menu_personal_sections_replace_welcome_with_prepared_card(monkeypatch):
    monkeypatch.setattr(bot_callbacks.access, "is_allowed", lambda _cid: True)

    class Message:
        chat_id = "42"
        message_id = 7

        def __init__(self):
            self.edits = []

        async def edit_reply_markup(self, **_kwargs):
            return None

        async def edit_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

    class Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("the prepared home card must replace the welcome")

    async def prepared_card(_bot, _cid, *args, status=None, **kwargs):
        assert status is not None
        await status.replace("✨ Готовая карточка")

    handlers = (
        ("m_myday", bot_callbacks.myday, "send_plany"),
        ("m_wardrobe", bot_callbacks.wardrobe, "send_home"),
        ("m_food", bot_callbacks.menu, "send_food_menu"),
        ("m_travel", bot_callbacks.travel, "send_home"),
        ("m_movie", bot_callbacks.leisure_movies, "send_movie_home"),
        ("m_books", bot_callbacks.leisure_books, "send_books_home"),
        ("m_music", bot_callbacks.leisure_music, "send_music_home"),
        ("m_games", bot_callbacks.leisure_games, "send_games_home"),
    )
    for callback_data, module, name in handlers:
        monkeypatch.setattr(module, name, prepared_card)
        message = Message()
        query = type("Query", (), {"data": callback_data, "message": message})()
        update = type("Update", (), {"callback_query": query})()

        asyncio.run(bot_callbacks.handle(update, type("Context", (), {"bot": Bot()})(), None))

        assert message.edits == [("✨ Готовая карточка", {})]


def test_travel_home_replaces_main_menu_without_sending_a_duplicate(monkeypatch):
    class Status:
        replaced = None

        async def replace(self, text, **kwargs):
            self.replaced = (text, kwargs)

    class Bot:
        async def send_message(self, **_kwargs):
            raise AssertionError("travel home must not send a second copy after status.replace")

    monkeypatch.setattr(travel, "_home_idea", lambda _cid: {
        "emoji": "🗺️", "transport_title": "Нидерланды", "from": "Алкмар",
        "to": "Утрехт", "intro": "На один день.", "route": ["Поезжай"],
        "tip": "Проверь расписание.",
    })
    monkeypatch.setattr(travel, "_visited_codes", lambda _cid: [])
    monkeypatch.setattr(travel, "_daily_travel_rebus", lambda: {})

    status = Status()
    asyncio.run(travel.send_home(Bot(), "42", status=status))

    assert status.replaced is not None


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
    assert bot.message["text"] == "🎚️ Мой шкаф · 1 вещь\n\nВерх:\nФутболка"
    assert "Выбери категорию" not in bot.message["text"]
    assert labels[-3:-1] == [["🆕 Добавить вещь"], ["🔣 Выбрать предпочтения"]]
    assert labels[-1] == ["⬅️ Назад", "#️⃣ Главная"]
    assert all(len(row) == 1 for row in labels[:-1])
    assert all("✏️ Изменить" not in row for row in labels)


def test_closet_hides_other_category_but_keeps_legacy_items_accessible(monkeypatch):
    class Bot:
        messages = []

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: {
        "zones": {"Другое": {"Другое": [{"id": "legacy-1", "name": "Старинная брошь"}]}},
    })

    bot = Bot()
    asyncio.run(wardrobe.send_wardrobe_zones(bot, "closet-test"))
    home_labels = _labels(bot.messages[-1]["reply_markup"])

    assert all("Другое" not in row for row in home_labels)
    assert "Аксессуары:\nСтаринная брошь" in bot.messages[-1]["text"]

    asyncio.run(wardrobe.send_category(bot, "closet-test", "acc"))
    category_labels = _labels(bot.messages[-1]["reply_markup"])
    assert category_labels[0] == ["Старинная брошь"]


def test_closet_screen_lists_nonempty_categories_with_spacing(monkeypatch):
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: {
        "zones": {
            "Верх": {"Футболки": [
                {"id": "top-1", "name": "Белая футболка"},
                {"id": "top-2", "name": "Синяя рубашка"},
            ]},
            "Низ": {"Брюки": [{"id": "bottom-1", "name": "Чёрные брюки"}]},
        },
    })

    bot = Bot()
    asyncio.run(wardrobe.send_wardrobe_zones(bot, "closet-test"))

    assert bot.message["text"] == (
        "🎚️ Мой шкаф · 3 вещи\n\n"
        "Верх:\nБелая футболка, Синяя рубашка\n\n"
        "Низ:\nЧёрные брюки"
    )


def test_closet_category_has_add_item_button_above_navigation(monkeypatch):
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: {
        "zones": {"Верх": {"Футболки": [{"id": "top-1", "name": "Футболка"}]}},
    })

    bot = Bot()
    asyncio.run(wardrobe.send_category(bot, "closet-test", "top"))

    labels = _labels(bot.message["reply_markup"])
    assert labels[-2] == ["🆕 Добавить вещь"]
    assert labels[-1] == ["⬅️ Назад", "#️⃣ Главная"]


def test_closet_category_uses_movie_style_pagination(monkeypatch):
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    items = [
        {"id": f"top-{index}", "name": f"Вещь {index}"}
        for index in range(1, 11)
    ]
    monkeypatch.setattr(wardrobe.store, "load_wardrobe", lambda _cid: {
        "zones": {"Верх": {"Футболки": items}},
    })

    bot = Bot()
    asyncio.run(wardrobe.send_category(bot, "closet-test", "top", page=1))

    labels = _labels(bot.message["reply_markup"])
    assert labels[:2] == [["Вещь 9"], ["Вещь 10"]]
    assert labels[-3] == ["◀️", "2/2", "▶️"]
    assert labels[-2] == ["🆕 Добавить вещь"]
    assert bot.message["text"].startswith("👕 Верх · 10 вещей")
