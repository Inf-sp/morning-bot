import asyncio
import copy
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import bot
import onboard
import tracking
from ui import menu as menu_ui


def _labels(markup):
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_inactivity_reminder_has_exact_copy_and_main_menu():
    message = menu_ui.inactivity_reminder()

    assert message.text == (
        "🫪 Давно не виделись\n\n"
        "Загляни - подберу образ на сегодня, помогу с планами или предложу что-то полезное.\n\n"
        "Выбери раздел или просто напиши, что нужно."
    )
    assert _labels(message.reply_markup) == [
        ["🌡️ Мой день"],
        ["👟 Гардероб", "🥣 Готовка"],
        ["📚 Обучение", "🚑 Здоровье"],
        ["✈️ Поездки", "🍿 Досуг"],
        ["🎚️ Настройки"],
    ]


def test_menu_command_sends_transient_main_menu():
    sent = []

    async def send_message(**kwargs):
        sent.append(kwargs)

    update = SimpleNamespace(effective_chat=SimpleNamespace(id="menu-user"))
    context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))

    asyncio.run(bot.menu_command(update, context))

    assert len(sent) == 1
    assert sent[0]["transient"] is True
    assert sent[0]["text"].startswith("👋🏻 Привет! Я DM")
    assert _labels(sent[0]["reply_markup"])[0] == ["🌡️ Мой день"]


def test_main_menu_markup_is_recognized_for_legacy_cleanup():
    assert bot.menu.is_main_menu_markup(bot.menu.main_menu_kb()) is True
    assert bot.menu.is_main_menu_markup(None) is False


def test_start_welcome_is_transient(monkeypatch):
    sent = []

    async def send_message(**kwargs):
        sent.append(kwargs)

    async def remove_reply_keyboard(*_args):
        return None

    monkeypatch.setattr(bot.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot, "_remove_reply_kb_once", remove_reply_keyboard)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id="start-user"))
    context = SimpleNamespace(
        args=[],
        bot=SimpleNamespace(send_message=send_message),
    )

    asyncio.run(bot.start(update, context))

    assert len(sent) == 1
    assert sent[0]["transient"] is True
    assert sent[0]["text"].startswith("👋🏻 Привет! Я DM")


def test_onboarding_finish_sends_transient_menu(monkeypatch):
    sent = []
    profile = {}
    cid = "onboarding-user"
    onboard._ob[cid] = {"langs": []}

    async def send_message(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(onboard, "_save_step", lambda *_args: None)
    monkeypatch.setattr(onboard.store, "get_profile", lambda _cid: dict(profile))
    monkeypatch.setattr(
        onboard.store,
        "set_profile",
        lambda _cid, value: profile.update(value),
    )

    asyncio.run(
        onboard._finish(SimpleNamespace(send_message=send_message), cid)
    )

    assert len(sent) == 1
    assert sent[0]["transient"] is True
    assert _labels(sent[0]["reply_markup"])[0] == ["🌡️ Мой день"]


def test_transient_navigation_message_is_deleted_before_next_message():
    deleted = []
    cid = "transient-user"
    bot.store.transient_message[cid] = 42
    bot.store.last_inline_message[cid] = 42

    class Cleanup:
        async def delete_message(self, **kwargs):
            deleted.append(kwargs)

        async def edit_message_reply_markup(self, **_kwargs):
            raise AssertionError("delete_message should handle the cleanup")

    asyncio.run(bot._MenuCleanupBot._delete_transient(Cleanup(), cid))

    assert deleted == [{"chat_id": cid, "message_id": 42}]
    assert cid not in bot.store.transient_message
    assert cid not in bot.store.last_inline_message


def test_transient_message_survives_restart_and_is_then_deleted():
    deleted = []
    cid = "persisted-transient-user"
    bot.store.transient_message.pop(cid, None)
    bot.store.set_persisted_transient_message_id(cid, 91)

    class Cleanup:
        async def delete_message(self, **kwargs):
            deleted.append(kwargs)

        async def edit_message_reply_markup(self, **_kwargs):
            raise AssertionError("persisted message should be deleted")

    asyncio.run(bot._MenuCleanupBot._delete_transient(Cleanup(), cid))

    assert deleted == [{"chat_id": cid, "message_id": 91}]
    assert bot.store.get_persisted_transient_message_id(cid) is None


def test_reminder_is_due_once_and_activity_starts_new_cycle(monkeypatch):
    state = {}
    clock = {"now": 1_000_000}

    monkeypatch.setattr(tracking.time, "time", lambda: clock["now"])
    monkeypatch.setattr(tracking.store, "_load", lambda _key: copy.deepcopy(state))

    def save(_key, data):
        state.clear()
        state.update(copy.deepcopy(data))

    monkeypatch.setattr(tracking.store, "_save", save)
    tracking._last_touch.pop("user-1", None)

    tracking.touch("user-1")
    first_since = state["user-1"]["inactivity_since_ts"]
    assert tracking.due_inactivity_reminders(["user-1"], now=first_since + 72 * 3600 - 1) == []
    assert tracking.due_inactivity_reminders(["user-1"], now=first_since + 72 * 3600) == [
        ("user-1", first_since)
    ]

    assert tracking.mark_inactivity_reminded("user-1", first_since, sent_ts=clock["now"])
    assert tracking.due_inactivity_reminders(["user-1"], now=first_since + 10 * 72 * 3600) == []

    clock["now"] = first_since + 72 * 3600 + 120
    tracking.touch("user-1")
    second_since = state["user-1"]["inactivity_since_ts"]
    assert second_since == clock["now"]
    assert "inactivity_reminded_for_ts" not in state["user-1"]
    assert tracking.due_inactivity_reminders(["user-1"], now=second_since + 72 * 3600) == [
        ("user-1", second_since)
    ]


def test_existing_activity_keeps_historical_baseline_for_first_send(monkeypatch):
    state = {"user-1": {"last_ts": 100, "count": 2, "days": []}}
    monkeypatch.setattr(tracking.store, "_load", lambda _key: copy.deepcopy(state))

    def save(_key, data):
        state.clear()
        state.update(copy.deepcopy(data))

    monkeypatch.setattr(tracking.store, "_save", save)

    assert tracking.initialize_inactivity_tracking(["user-1"], now=1_000) == 1
    assert state["user-1"]["inactivity_since_ts"] == 100
    assert tracking.initialize_inactivity_tracking(["user-1"], now=2_000) == 0
    assert state["user-1"]["inactivity_since_ts"] == 100


def test_any_allowed_message_and_callback_count_as_activity(monkeypatch):
    touched = []
    monkeypatch.setattr(bot.access, "is_allowed", lambda _cid: True)
    monkeypatch.setattr(bot.tracking, "touch", lambda cid: touched.append(str(cid)))

    update = SimpleNamespace(effective_chat=SimpleNamespace(id="user-message"))
    asyncio.run(bot.message_activity_handler(update, None))

    class Query:
        message = SimpleNamespace(chat_id="user-callback")

        async def answer(self):
            return None

    callback_update = SimpleNamespace(callback_query=Query())
    context = SimpleNamespace(bot=object())
    monkeypatch.setattr(bot.bot_callbacks, "handle", lambda *_args, **_kwargs: asyncio.sleep(0))
    asyncio.run(bot.answer_callback(callback_update, context))

    assert touched == ["user-message", "user-callback"]


def test_job_sends_reminder_and_marks_cycle(monkeypatch):
    sent = []
    marked = []
    monkeypatch.setattr(bot.access, "get_allowed_cids", lambda: ["user-1"])
    monkeypatch.setattr(bot.tracking, "due_inactivity_reminders", lambda _cids: [("user-1", 123)])
    monkeypatch.setattr(
        bot.tracking,
        "mark_inactivity_reminded",
        lambda cid, since: marked.append((cid, since)) or True,
    )

    async def send_message(**kwargs):
        sent.append(kwargs)

    context = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    asyncio.run(bot.job_inactivity_reminders(context))

    assert len(sent) == 1
    assert sent[0]["chat_id"] == "user-1"
    assert sent[0]["transient"] is True
    assert sent[0]["text"].startswith("🫪 Давно не виделись")
    assert marked == [("user-1", 123)]
