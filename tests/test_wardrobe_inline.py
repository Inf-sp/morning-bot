import asyncio
import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import wardrobe
from ui.wardrobe import purchase_check_card


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_send_home_includes_inline_keyboard():
    class Bot:
        message = None

        async def send_message(self, **kwargs):
            self.message = kwargs

    bot = Bot()

    asyncio.run(wardrobe.send_home(bot, "pytest-wardrobe-inline"))

    assert bot.message["reply_markup"] is not None
    assert _labels(bot.message["reply_markup"]) == [
        ["✨ Подобрать образ"],
        ["🧐 Оценить покупку", "🧶 Мой шкаф"],
        ["🎚️ Предпочтения"],
        ["#️⃣ Главная"],
    ]


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


def test_purchase_action_sits_directly_below_other_outfit():
    assert _labels(wardrobe.build_wardrobe_keyboard())[:2] == [
        ["✨ Другой образ"],
        ["🧐 Оценить покупку", "🧶 Мой шкаф"],
    ]


def test_other_outfit_keeps_result_card_instead_of_deleting_it(monkeypatch):
    calls = []

    class Status:
        async def stop(self, delete=True):
            calls.append(("stop", delete))

    status = Status()

    async def start_inline(q, bot=None, cid=None, stages=None):
        calls.append(("start_inline", q, bot, cid, stages))
        return status

    async def unexpected_start(*_args, **_kwargs):
        raise AssertionError("inline wardrobe refresh must not use message-mode status")

    async def fake_send_looks(bot, cid, **kwargs):
        calls.append(("send_looks", bot, cid, kwargs))
        assert kwargs["status"] is status
        assert kwargs["previous_style_tip"] == "старый совет"

    monkeypatch.setattr(wardrobe.util.StatusManager, "start_inline", start_inline)
    monkeypatch.setattr(wardrobe.util.StatusManager, "start", unexpected_start)
    monkeypatch.setattr(wardrobe, "_get_cached_look", lambda _cid: {
        "item_ids": ["old-item"],
        "look_data": {"style_tip": "старый совет"},
    })
    monkeypatch.setattr(wardrobe, "send_looks", fake_send_looks)

    class Query:
        message = object()

    asyncio.run(wardrobe.handle_callback(object(), "42", Query(), "w_look"))

    assert calls[0][0] == "start_inline"
    assert calls[-1] == ("stop", True)


def test_closet_screen_does_not_show_edit_button(monkeypatch):
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
    assert all("✏️ Изменить" not in row for row in labels)
