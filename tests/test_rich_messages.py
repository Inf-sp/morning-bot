import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

from telegram import Message
from telegram.error import BadRequest

import bot
import assistant
import config
import rich_delivery
import store
from ui import admin as admin_ui
from ui.assistant import assistant_answer


class _RichBot:
    def __init__(self, *, fail_rich=False):
        self.fail_rich = fail_rich
        self.rich = []
        self.classic = []
        self.drafts = []

    async def send_rich_message(self, **kwargs):
        self.rich.append(kwargs)
        if self.fail_rich:
            if isinstance(self.fail_rich, BaseException):
                raise self.fail_rich
            raise BadRequest("rich messages are unavailable")
        return SimpleNamespace(message_id=101)

    async def send_message(self, **kwargs):
        self.classic.append(kwargs)
        return SimpleNamespace(message_id=102)

    async def send_chat_action(self, **_kwargs):
        return True

    async def send_rich_message_draft(self, **kwargs):
        self.drafts.append(kwargs)
        return True


def test_rich_messages_are_disabled_by_default_for_standard_telegram_text():
    """Обычные экраны не должны менять типографику в Telegram-клиенте."""
    assert config.TELEGRAM_RICH_MESSAGES is False


def _table_blocks(message):
    return [block for block in message.rich_message["blocks"] if block["type"] == "table"]


def test_system_screen_has_grouped_native_tables_and_datetime_footer():
    message = admin_ui.api_ai(
        [
            "AI",
            "🟢 Groq · Основной · gpt-oss-20b · 900/1 000 осталось",
            "Данные",
            "🟡 Google Books · Книги · временно недоступен",
        ],
        "12:30",
        1_780_000_000,
    )

    tables = _table_blocks(message)
    assert len(tables) == 2
    assert [cell["text"] for cell in tables[0]["cells"][0]] == ["Сервис", "Состояние"]
    assert [cell["text"] for cell in tables[0]["cells"][1]] == [
        "🟢 Groq", "Основной · gpt-oss-20b · 900/1 000 осталось",
    ]
    footer = message.rich_message["blocks"][-1]
    assert footer["type"] == "footer"
    assert footer["text"] == [
        "Обновлено в ",
        {
            "type": "date_time", "text": "12:30",
            "unix_time": 1_780_000_000, "date_time_format": "t",
        },
    ]


def test_logs_use_phone_friendly_table():
    logs = admin_ui.logs(
        ["08:00 · Система · Groq · лимит исчерпан"], 1, "12:30",
    )
    log_table = _table_blocks(logs)[0]
    assert [cell["text"] for cell in log_table["cells"][0]] == ["Время", "Инцидент"]
    assert [cell["text"] for cell in log_table["cells"][1]] == [
        "08:00", "Система · Groq · лимит исчерпан",
    ]


def test_rich_delivery_uses_rich_payload_and_falls_back_on_api_validation_error(monkeypatch):
    monkeypatch.setattr(rich_delivery.config, "TELEGRAM_RICH_MESSAGES", True)
    message = admin_ui.logs(["08:00 · Система · Groq · лимит исчерпан"], 1, "12:30")

    rich_bot = _RichBot()
    asyncio.run(rich_delivery.send(rich_bot, "42", message, reply_markup="buttons"))
    assert rich_bot.rich == [{
        "chat_id": "42", "rich_message": message.rich_message, "reply_markup": "buttons",
    }]
    assert rich_bot.classic == []

    fallback_bot = _RichBot(fail_rich=True)
    asyncio.run(rich_delivery.send(fallback_bot, "42", message, reply_markup="buttons"))
    assert len(fallback_bot.rich) == 1
    assert fallback_bot.classic == [{
        "chat_id": "42", "text": message.text, "entities": message.entities,
        "reply_markup": "buttons", "parse_mode": message.parse_mode,
    }]


def test_rich_edit_not_modified_is_a_noop_not_a_new_message(monkeypatch):
    monkeypatch.setattr(rich_delivery.config, "TELEGRAM_RICH_MESSAGES", True)
    message = admin_ui.logs(["08:00 · Система · Groq · лимит исчерпан"], 1, "12:30")

    class EditBot(_RichBot):
        async def edit_rich_message(self, **kwargs):
            self.rich.append(kwargs)
            raise BadRequest("Message is not modified")

    target = SimpleNamespace(chat_id="42", message_id=101)
    query = SimpleNamespace(message=target)
    bot_instance = EditBot()

    result = asyncio.run(rich_delivery.show(bot_instance, "42", message, query=query))

    assert result is target
    assert len(bot_instance.rich) == 1
    assert bot_instance.classic == []


def test_rich_table_keeps_zero_values():
    from ui import rich

    table = rich.table(("Попытки",), [(0,)])

    assert table["cells"][1][0]["text"] == "0"


def test_rich_draft_uses_one_nonzero_id_and_thinking_block(monkeypatch):
    monkeypatch.setattr(rich_delivery.config, "TELEGRAM_RICH_MESSAGES", True)
    bot_instance = _RichBot()

    draft = asyncio.run(rich_delivery.start_draft(bot_instance, "42"))

    assert draft is not None
    assert bot_instance.drafts[0]["chat_id"] == "42"
    assert bot_instance.drafts[0]["draft_id"] > 0
    assert bot_instance.drafts[0]["rich_message"]["blocks"] == [
        {"type": "thinking", "text": "Думаю…"},
    ]


def test_assistant_answer_describes_heading_list_and_quote_as_rich_blocks():
    message = assistant_answer("Ответ\n- Первый пункт\n- Второй пункт\n> Важная цитата")
    types = [block["type"] for block in message.rich_message["blocks"]]

    assert types == ["heading", "list", "blockquote"]


def test_free_chat_streams_into_rich_draft_then_persists_final_answer(monkeypatch):
    monkeypatch.setattr(rich_delivery.config, "TELEGRAM_RICH_MESSAGES", True)
    monkeypatch.setattr(assistant.research, "requires_explicit_web_search", lambda _text: False)
    cid = "rich-stream-test"
    store.chat_history.pop(cid, None)

    async def stream(_history, _cid, on_delta=None):
        await on_delta("x" * 90)
        await on_delta(" готово")
        return "Ответ\n- Первый пункт"

    monkeypatch.setattr(assistant.ai, "achat_chain_stream", stream)
    bot_instance = _RichBot()

    asyncio.run(assistant.chat_reply(bot_instance, cid, "Расскажи что-нибудь необычное"))

    assert bot_instance.drafts[0]["rich_message"]["blocks"][0]["type"] == "thinking"
    assert any(
        call["rich_message"]["blocks"][0]["type"] == "paragraph"
        for call in bot_instance.drafts[1:]
    )
    assert bot_instance.rich[-1]["rich_message"]["blocks"][0]["type"] == "heading"
    assert bot_instance.classic == []
    assert store.chat_history[cid][-1]["content"] == "Ответ\n- Первый пункт"
    store.chat_history.pop(cid, None)


def test_free_chat_never_exposes_model_reasoning_in_draft_or_final_message(monkeypatch):
    monkeypatch.setattr(rich_delivery.config, "TELEGRAM_RICH_MESSAGES", True)
    monkeypatch.setattr(assistant.research, "requires_explicit_web_search", lambda _text: False)
    cid = "rich-stream-reasoning"
    store.chat_history.pop(cid, None)
    leaked_reasoning = "<think>Внутреннее рассуждение модели.</think>\n\nПривет, я на связи."

    async def stream(_history, _cid, on_delta=None):
        # SSE может разрезать служебный тег посередине.
        for delta in ("<th", "ink>Внутреннее рассуждение модели.",
                      "</think>\n\nПривет, я на связи."):
            await on_delta(delta)
        return leaked_reasoning

    monkeypatch.setattr(assistant.ai, "achat_chain_stream", stream)
    bot_instance = _RichBot()

    asyncio.run(assistant.chat_reply(bot_instance, cid, "Скажи привет"))

    visible_draft_text = "\n".join(
        str(block.get("text") or "")
        for call in bot_instance.drafts[1:]
        for block in call["rich_message"]["blocks"]
    )
    final_text = "\n".join(
        str(block.get("text") or "")
        for call in bot_instance.rich
        for block in call["rich_message"]["blocks"]
    )
    assert "<think>" not in visible_draft_text
    assert "Внутреннее рассуждение" not in visible_draft_text
    assert "<think>" not in final_text
    assert "Внутреннее рассуждение" not in final_text
    assert store.chat_history[cid][-1]["content"] == "Привет, я на связи."
    store.chat_history.pop(cid, None)


def test_free_chat_does_not_duplicate_after_uncertain_rich_final_delivery(monkeypatch):
    monkeypatch.setattr(rich_delivery.config, "TELEGRAM_RICH_MESSAGES", True)
    monkeypatch.setattr(assistant.research, "requires_explicit_web_search", lambda _text: False)
    cid = "rich-stream-uncertain"
    store.chat_history.pop(cid, None)

    async def stream(_history, _cid, on_delta=None):
        await on_delta("Часть ответа")
        return "Готовый ответ"

    monkeypatch.setattr(assistant.ai, "achat_chain_stream", stream)
    bot_instance = _RichBot(fail_rich=RuntimeError("connection lost after send"))

    asyncio.run(assistant.chat_reply(bot_instance, cid, "Расскажи что-нибудь"))

    assert len(bot_instance.rich) == 1
    assert bot_instance.classic == []
    store.chat_history.pop(cid, None)


class _AdapterBot(bot._MenuCleanupBot):
    __slots__ = ("calls",)

    def __init__(self):
        super().__init__(token="123:abc")
        object.__setattr__(self, "calls", [])

    async def do_api_request(self, endpoint, api_kwargs=None, return_type=None, **_kwargs):
        self.calls.append((endpoint, api_kwargs, return_type))
        return SimpleNamespace(message_id=7, reply_markup=None)

    async def _pre_send(self, chat_id):
        self.calls.append(("pre_send", chat_id, None))

    def _post_send(self, *args, **kwargs):
        self.calls.append(("post_send", args, kwargs))


def test_project_bot_adapter_uses_public_ptb_api_for_send_edit_and_draft():
    adapter = _AdapterBot()

    asyncio.run(adapter.send_rich_message("42", {"blocks": []}, reply_markup="kb"))
    asyncio.run(adapter.edit_rich_message("42", 7, {"blocks": []}, reply_markup="kb2"))
    asyncio.run(adapter.send_rich_message_draft("42", 9, {"blocks": [{"type": "thinking", "text": "…"}]}))

    assert adapter.calls[0] == (
        "sendRichMessage",
        {"chat_id": "42", "rich_message": {"blocks": []}, "reply_markup": "kb"},
        Message,
    )
    assert ("editMessageText", {
        "chat_id": "42", "message_id": 7, "rich_message": {"blocks": []}, "reply_markup": "kb2",
    }, Message) in adapter.calls
    assert ("sendRichMessageDraft", {
        "chat_id": "42", "draft_id": 9,
        "rich_message": {"blocks": [{"type": "thinking", "text": "…"}]},
    }, None) in adapter.calls
