import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import asyncio

import learning
import menu
import onboard
import settings
import store


class Bot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return None


class Message:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, entities=None, reply_markup=None):
        self.edits.append({"text": text, "entities": entities, "reply_markup": reply_markup})


class Query:
    def __init__(self):
        self.message = Message()


def _button_texts(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def _button_data(markup):
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def test_onboarding_language_choice_saves_learning_language():
    cid = "learn-onboard-lang"
    onboard._ob[str(cid)] = {}
    q = Query()

    asyncio.run(onboard.handle_callback(Bot(), cid, q, "ob_lang_en"))

    assert store.get_learning_language(cid) == "en"
    assert settings.study_lang(cid) == "английский"
    assert store.has_level(cid, "нидерландский")


def test_learning_settings_screen_shows_only_active_language_and_level():
    cid = "learn-settings-screen"
    store.set_learning_language(cid, "nl")
    store.set_level(cid, "нидерландский", "B1")
    store.set_level(cid, "английский", "A2")
    bot = Bot()

    asyncio.run(learning.send_learning_settings(bot, cid))

    text = bot.messages[0]["text"]
    buttons = _button_texts(bot.messages[0]["reply_markup"])
    assert "🇳🇱 Нидерландский" in text
    assert "Сложный (B1+)" in text
    assert "🇬🇧 Английский" not in text
    assert buttons[0] == ["📚 Язык: 🇳🇱 Нидерландский"]
    assert buttons[1] == ["🇳🇱 Лёгкий", "✅ 🇳🇱 Сложный"]
    assert "Назад" in buttons[-1][0]


def test_toggle_learning_language_loads_saved_level():
    cid = "learn-toggle"
    store.set_learning_language(cid, "nl")
    store.set_level(cid, "нидерландский", "B1")
    store.set_level(cid, "английский", "A2")
    q = Query()

    asyncio.run(learning.handle_learning_settings_callback(Bot(), cid, q, "toggle_learning_language"))

    assert store.get_learning_language(cid) == "en"
    assert "🇬🇧 Английский" in q.message.edits[0]["text"]
    assert "Лёгкий (A1–A2)" in q.message.edits[0]["text"]
    assert _button_texts(q.message.edits[0]["reply_markup"])[1] == ["✅ 🇬🇧 Лёгкий", "🇬🇧 Сложный"]


def test_learning_level_changes_only_active_language():
    cid = "learn-level-active-only"
    store.set_learning_language(cid, "en")
    store.set_level(cid, "нидерландский", "A2")
    store.set_level(cid, "английский", "A2")

    asyncio.run(learning.handle_learning_settings_callback(Bot(), cid, Query(), "set_learning_level_B1"))

    assert store.get_level(cid, "английский") == "B1"
    assert store.get_level(cid, "нидерландский") == "A2"


def test_learning_menu_hides_inactive_language():
    cid = "learn-menu-active-only"
    store.set_learning_language(cid, "en")

    text, _entities, markup = menu.menu_screen("m_learn", cid)

    assert "🇬🇧 Английский" in text
    assert "🇳🇱 Нидерландский" not in text
    flat = [button for row in _button_texts(markup) for button in row]
    assert any("Английский" in button for button in flat)
    assert not any("Нидерландский" in button for button in flat)


def test_trainers_use_learning_language():
    cid = "learn-train-active"
    store.set_learning_language(cid, "en")
    store.set_list("dict.json", cid, [
        {"lang": "nl", "word": "Huis", "ru": "дом", "kind": "word"},
        {"lang": "en", "word": "House", "ru": "дом", "kind": "word"},
        {"lang": "nl", "word": "Geen probleem.", "ru": "Без проблем.", "kind": "phrase"},
        {"lang": "en", "word": "No problem.", "ru": "Без проблем.", "kind": "phrase"},
    ])

    assert learning._train_words(cid, learning.active_language(cid)) == [("House", "дом")]
    assert learning._train_phrases(cid, learning.active_language(cid)) == [("No problem.", "Без проблем.")]


def test_scheduled_words_use_learning_language(monkeypatch):
    calls = []

    async def fake_morning_word(_bot, cid, language=None, with_kb=True):
        calls.append((cid, language, with_kb))

    monkeypatch.setattr(learning, "send_morning_word", fake_morning_word)
    cid = "learn-scheduled-words"
    store.set_learning_language(cid, "en")

    asyncio.run(settings.send_scheduled_notification(Bot(), cid, "daily_words_nl"))
    asyncio.run(settings.send_scheduled_notification(Bot(), cid, "daily_words_en"))

    assert calls == [
        (cid, "английский", False),
        (cid, "английский", False),
    ]


def test_live_language_uses_learning_language(monkeypatch):
    calls = []

    async def fake_allm_json(prompt, *_args, **_kwargs):
        calls.append(prompt)
        return {
            "nl": "",
            "en": "No worries.",
            "analogs": ["не переживай"],
            "meaning": "",
            "example": "Sorry, I forgot to reply yesterday. No worries.",
            "example_ru": "Прости, я вчера забыл ответить. Не переживай.",
        }

    monkeypatch.setattr(learning.ai, "allm_json", fake_allm_json)
    cid = "learn-live-lang"
    store.set_learning_language(cid, "en")
    bot = Bot()

    asyncio.run(learning.send_proverb_both(bot, cid, with_kb=False))

    assert "английский" in calls[0]
    assert "нидерландском" not in calls[0]
    assert bot.messages[0]["reply_markup"] is None
    assert "No worries." in bot.messages[0]["text"]
    assert "Как говорить ПРАВИЛЬНО" not in bot.messages[0]["text"]
    assert "Покрути в голове" not in bot.messages[0]["text"]


def test_live_language_manual_uses_menu_language(monkeypatch):
    async def fake_allm_json(_prompt, *_args, **_kwargs):
        return {
            "nl": "Dat is de druppel!",
            "en": "That's the last straw!",
            "analogs": ["это последняя капля", "моё терпение лопнуло"],
            "meaning": "Когда очередная мелочь окончательно добивает.",
            "example": "En nu dit?! Dat is de druppel!",
            "example_ru": "А теперь еще и это?! Это последняя капля!",
        }

    monkeypatch.setattr(learning.ai, "allm_json", fake_allm_json)
    bot = Bot()

    asyncio.run(learning.send_proverb(bot, "learn-live-menu", "nl"))

    payload = bot.messages[0]
    text = payload["text"]
    buttons = _button_texts(payload["reply_markup"])
    data = _button_data(payload["reply_markup"])
    assert text.startswith("💭 Живой язык")
    assert "Dat is de druppel!" in text
    assert "That's the last straw" not in text
    assert "«Это последняя капля»." in text
    assert "моё терпение" not in text
    assert buttons == [["✨ Ещё вариант"], ["⬅️ Назад"]]
    assert data == [["a_proverb_nl"], ["m_nl"]]
    assert "Назад" in buttons[-1][0]


def test_live_language_fallbacks_are_diverse():
    assert len(learning._PROVERB_FALLBACKS["nl"]) >= 10
    assert len(learning._PROVERB_FALLBACKS["en"]) >= 10


def test_no_old_two_language_level_ui():
    cid = "learn-no-old-level-ui"
    store.set_learning_language(cid, "nl")
    store.set_level(cid, "нидерландский", "A2")
    markup = learning.learning_settings_kb(learning.active_language(cid), "A2")

    assert _button_data(markup)[1] == ["set_learning_level_A2", "set_learning_level_B1"]
    assert all("lvl_en" not in data and "lvl_nl" not in data for row in _button_data(markup) for data in row)
