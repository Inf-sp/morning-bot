"""Telegram transport adapters shared by the application bootstrap."""

import asyncio
import logging
import re
from urllib.parse import urlparse

from telegram import InlineKeyboardMarkup, Message
from telegram.error import BadRequest, TimedOut
from telegram.request import HTTPXRequest
from telegram.ext import ExtBot

import store
import tracking
import util

_log = logging.getLogger(__name__)

class RetryingHTTPXRequest(HTTPXRequest):
    """Отдельный пул Telegram API с одним безопасным повтором ConnectTimeout.

    Повторяем только ошибку установления соединения: запрос ещё не был отправлен,
    поэтому sendMessage не может продублироваться.
    """

    @staticmethod
    def _request_label(url: str, request_data=None):
        path = urlparse(url).path.rstrip("/")
        endpoint = path.rsplit("/", 1)[-1] if path else ""
        operation = endpoint or "telegram_api"
        chat_id = None
        update_type = ""
        try:
            params = getattr(request_data, "parameters", None) or {}
            chat_id = params.get("chat_id") or params.get("chatId")
            if "callback_query_id" in params:
                update_type = "callback_query"
            elif "message_id" in params and "chat_id" in params:
                update_type = "message_edit"
            elif endpoint == "getUpdates":
                update_type = "polling"
            elif endpoint == "answerCallbackQuery":
                update_type = "callback_query"
            elif endpoint == "sendMessage":
                update_type = "message"
            elif endpoint == "sendRichMessage":
                update_type = "rich_message"
            elif endpoint == "sendRichMessageDraft":
                update_type = "rich_draft"
            elif endpoint == "editMessageText":
                update_type = "edit_message"
            elif endpoint == "sendPhoto":
                update_type = "photo"
            elif endpoint == "sendDocument":
                update_type = "document"
            elif endpoint == "sendPoll":
                update_type = "poll"
        except Exception:
            _log.debug("_request_label: ignored error", exc_info=True)
        return operation, chat_id, update_type

    async def do_request(self, *args, **kwargs):
        url = args[0] if args else ""
        request_data = args[2] if len(args) > 2 else kwargs.get("request_data")
        operation, chat_id, update_type = self._request_label(url, request_data)
        attempts = 0
        try:
            attempts += 1
            return await super().do_request(*args, **kwargs)
        except TimedOut as error:
            cause = error.__cause__
            timeout_type = "connect" if type(cause).__name__ == "ConnectTimeout" else "read/write/pool"
            _log.warning(
                "Telegram timeout operation=%s chat_id=%s update_type=%s timeout_type=%s attempts=%s",
                operation, chat_id, update_type, timeout_type, attempts,
            )
            if timeout_type != "connect":
                raise
            await asyncio.sleep(0.25)
            attempts += 1
            _log.warning(
                "Telegram timeout operation=%s chat_id=%s update_type=%s timeout_type=%s attempts=%s retry=1",
                operation, chat_id, update_type, timeout_type, attempts,
            )
            return await super().do_request(*args, **kwargs)


# Кнопки действий с данными (Bot API 9.4): эмодзи убирается, цвет задаёт смысл.
# Отметки выбора («✅ Комедия», «❌ Не добавлять») не трогаем — только глаголы.
_ADD_RE = re.compile(r"^✅\s*((?:Добавить|Создать)\b.*)$", re.S)
_DELETE_RE = re.compile(r"^❌\s*((?:Удалить|Очистить|Убрать)\b.*)$", re.S)
_REFRESH_RE = re.compile(r"^✨\s*(Обновить)$")
# Уровни оформления: 2 — цвет + disabled, 1 — только цвет, 0 — только текст без
# эмодзи. Если Telegram отклонил поле, бот спускается на уровень ниже до рестарта,
# но эмодзи у «Добавить/Удалить» не возвращаются никогда.
_KEYBOARD_ERRORS = ("button", "keyboard", "reply markup", "reply_markup", "style", "disabled")
_buttons_enhanced = True  # совместимость: False — уже только текстовый уровень
_button_level = 2


def _enhance_markup(markup, level=2):
    """Inline-клавиатура по контракту кнопок из AGENTS.md, либо None без изменений.

    «Добавить» — зелёная, без эмодзи и всегда в верхнем ряду; «Удалить» — красная
    без эмодзи; ``noop``-кнопки (счётчик страниц, индикатор ожидания) становятся
    disabled (Bot API 10.3) и ничего не отправляют боту.
    """
    if not isinstance(markup, InlineKeyboardMarkup):
        return None
    rows = markup.to_dict().get("inline_keyboard", [])
    changed = False
    add_rows = set()
    for index, row in enumerate(rows):
        for button in row:
            if level >= 2 and button.get("callback_data") == "noop":
                button.pop("callback_data")
                button["disabled"] = {}
                changed = True
                continue
            text = str(button.get("text") or "")
            for pattern, style in (
                (_ADD_RE, "success"), (_DELETE_RE, "danger"), (_REFRESH_RE, "primary"),
            ):
                match = pattern.match(text)
                if match:
                    button["text"] = match.group(1).strip()
                    if level >= 1:
                        button.setdefault("style", style)
                    changed = True
                    if style == "success":
                        add_rows.add(index)
    if not changed:
        return None
    order = sorted(range(len(rows)), key=lambda index: index not in add_rows)
    return {"inline_keyboard": [rows[index] for index in order]}


class MenuCleanupBot(ExtBot):
    """Telegram delivery wrapper that keeps previously sent inline controls usable."""

    async def _post(self, endpoint, data=None, *, api_kwargs=None, **kwargs):
        global _buttons_enhanced, _button_level
        data = {**(data or {}), **(api_kwargs or {})}
        start = _button_level if _buttons_enhanced else 0
        last_error = None
        for level in range(start, -1, -1):
            enhanced = _enhance_markup(data.get("reply_markup"), level)
            if enhanced is None:
                return await super()._post(endpoint, data, **kwargs)
            try:
                result = await super()._post(endpoint, {**data, "reply_markup": enhanced}, **kwargs)
            except BadRequest as error:
                if "not modified" in str(error).casefold():
                    raise
                last_error = error
                if not any(marker in str(error).casefold() for marker in _KEYBOARD_ERRORS):
                    break  # ошибка не про кнопки — сразу исходный запрос
                continue
            if level < start:
                _button_level = level
                _log.warning(
                    "Telegram rejected button fields; button level %s until restart: %s",
                    level, last_error,
                )
            return result
        # Даже текстовый уровень не прошёл — ошибка не в кнопках: исходный запрос.
        return await super()._post(endpoint, data, **kwargs)

    @staticmethod
    def _without_link_preview(kwargs):
        """Глобальный UI-контракт: бот никогда не прикрепляет превью ссылок."""
        kwargs = dict(kwargs)
        kwargs.pop("link_preview_options", None)
        kwargs["disable_web_page_preview"] = True
        return kwargs

    def mark_transient_message(self, chat_id, message_id):
        """Compatibility hook: temporary screens now remain available in chat."""
        key = str(chat_id)
        store.transient_message.pop(key, None)
        store.clear_persisted_transient_message_id(key)

    def mark_persistent_inline_message(self, chat_id, message_id):
        """Не снимает кнопки с полезной карточки при следующих сообщениях бота."""
        key = str(chat_id)
        if message_id and store.last_inline_message.get(key) == message_id:
            store.last_inline_message.pop(key, None)
        if message_id and store.transient_message.get(key) == message_id:
            store.transient_message.pop(key, None)
        if message_id:
            store.clear_persisted_transient_message_id(key, message_id)

    async def _delete_transient(self, chat_id):
        """Forget legacy cleanup markers without deleting a message or its buttons."""
        key = str(chat_id)
        store.transient_message.pop(key, None)
        store.last_inline_message.pop(key, None)
        store.clear_persisted_transient_message_id(key)

    async def _pre_send(self, chat_id):
        await self._delete_transient(chat_id)

    @staticmethod
    def _mark_send_done(task):
        try:
            task.result()
        except Exception:
            return
        tracking.mark_first_feedback()

    def _post_send(self, chat_id, msg, transient=False, persistent_inline=False):
        if (not persistent_inline
                and isinstance(getattr(msg, "reply_markup", None), InlineKeyboardMarkup)):
            store.last_inline_message[str(chat_id)] = msg.message_id
        if transient:
            self.mark_transient_message(chat_id, msg.message_id)

    async def _send_message_once(self, chat_id, *args, **kwargs):
        transient = kwargs.pop("transient", False)
        preserve_previous_inline = kwargs.pop("preserve_previous_inline", False)
        persistent_inline = kwargs.pop("persistent_inline", False)
        kwargs = self._without_link_preview(kwargs)
        send = asyncio.create_task(super().send_message(chat_id, *args, **kwargs))
        send.add_done_callback(self._mark_send_done)
        if preserve_previous_inline:
            msg = await send
        else:
            msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(
            chat_id, msg, transient=transient, persistent_inline=persistent_inline)
        return msg

    async def send_message(self, chat_id, *args, **kwargs):
        """Безопасная единая отправка: Telegram не принимает текст длиннее 4096.

        Большинство экранов вызывают ``bot.send_message`` напрямую, поэтому
        защита здесь покрывает и AI-ответы, и редкие длинные служебные карточки.
        Для HTML оставляем специальным доставщикам их разметочное разбиение.
        """
        text = kwargs.get("text") if "text" in kwargs else (args[0] if args else "")
        if (isinstance(text, str) and not kwargs.get("parse_mode")
                and len(text.encode("utf-16-le")) // 2 > 4000):
            entities = kwargs.get("entities")
            chunks = util.chunk_text_with_entities(text, entities, 4000)
            tail_args = args[1:] if args else ()
            last = None
            for index, (chunk_text, chunk_entities) in enumerate(chunks):
                part = dict(kwargs)
                part["text"] = chunk_text
                if chunk_entities:
                    part["entities"] = chunk_entities
                else:
                    part.pop("entities", None)
                if index < len(chunks) - 1:
                    part.pop("reply_markup", None)
                    part.pop("transient", None)
                    part.pop("persistent_inline", None)
                last = await self._send_message_once(chat_id, *tail_args, **part)
            return last
        return await self._send_message_once(chat_id, *args, **kwargs)

    async def edit_message_text(self, *args, **kwargs):
        """Отключает превью и при обновлении уже отправленной карточки."""
        return await super().edit_message_text(
            *args, **self._without_link_preview(kwargs),
        )

    async def _send_rich_message_once(self, chat_id, rich_message, **kwargs):
        """Send a Bot API Rich Message while preserving normal send semantics.

        PTB 21.x intentionally exposes ``do_api_request`` for Bot API methods
        that have appeared before the SDK has typed wrappers.  This stays here,
        rather than in every UI module, so transient-screen cleanup, inline
        button preservation and first-feedback telemetry keep working exactly
        like they do for ``send_message``.
        """
        transient = kwargs.pop("transient", False)
        preserve_previous_inline = kwargs.pop("preserve_previous_inline", False)
        persistent_inline = kwargs.pop("persistent_inline", False)
        api_kwargs = {"chat_id": chat_id, "rich_message": rich_message}
        for key in (
            "reply_markup", "disable_notification", "protect_content",
            "message_thread_id", "business_connection_id", "message_effect_id",
            "reply_parameters",
        ):
            value = kwargs.pop(key, None)
            if value is not None:
                api_kwargs[key] = value
        # Keep unknown caller options visible instead of silently discarding a
        # future Bot API field. They are still sent through PTB's public API.
        api_kwargs.update({key: value for key, value in kwargs.items() if value is not None})
        send = asyncio.create_task(self.do_api_request(
            "sendRichMessage", api_kwargs=api_kwargs, return_type=Message,
        ))
        send.add_done_callback(self._mark_send_done)
        if preserve_previous_inline:
            msg = await send
        else:
            msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(
            chat_id, msg, transient=transient, persistent_inline=persistent_inline,
        )
        return msg

    async def send_rich_message(self, chat_id, rich_message, **kwargs):
        """Public project adapter for Bot API ``sendRichMessage``.

        Callers always supply a classic fallback through ``rich_delivery``;
        this method deliberately only handles the successful Rich transport.
        """
        return await self._send_rich_message_once(chat_id, rich_message, **kwargs)

    async def edit_rich_message(self, chat_id, message_id, rich_message, **kwargs):
        """Public project adapter for ``editMessageText.rich_message``."""
        api_kwargs = {
            "chat_id": chat_id,
            "message_id": message_id,
            "rich_message": rich_message,
        }
        for key in ("reply_markup", "business_connection_id"):
            value = kwargs.pop(key, None)
            if value is not None:
                api_kwargs[key] = value
        api_kwargs.update({key: value for key, value in kwargs.items() if value is not None})
        edit = asyncio.create_task(self.do_api_request(
            "editMessageText", api_kwargs=api_kwargs, return_type=Message,
        ))
        edit.add_done_callback(self._mark_send_done)
        return await edit

    async def send_message_draft(self, chat_id, draft_id, text=""):
        """Живой черновик ответа (Bot API 9.3+): исчезает сам, итог отправляет send_message.

        Пустой текст показывает системную заглушку «Thinking…».
        """
        draft = asyncio.create_task(self.do_api_request(
            "sendMessageDraft",
            api_kwargs={"chat_id": chat_id, "draft_id": int(draft_id), "text": str(text or "")[:4000]},
        ))
        draft.add_done_callback(self._mark_send_done)
        return await draft

    async def send_rich_message_draft(self, chat_id, draft_id, rich_message, **kwargs):
        """Send the short-lived Rich draft used by the free-chat stream."""
        api_kwargs = {
            "chat_id": chat_id,
            "draft_id": int(draft_id),
            "rich_message": rich_message,
        }
        value = kwargs.pop("message_thread_id", None)
        if value is not None:
            api_kwargs["message_thread_id"] = value
        api_kwargs.update({key: value for key, value in kwargs.items() if value is not None})
        draft = asyncio.create_task(self.do_api_request(
            "sendRichMessageDraft", api_kwargs=api_kwargs,
        ))
        draft.add_done_callback(self._mark_send_done)
        return await draft

    async def send_photo(self, chat_id, *args, **kwargs):
        send = asyncio.create_task(super().send_photo(chat_id, *args, **kwargs))
        send.add_done_callback(self._mark_send_done)
        msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(chat_id, msg)
        return msg

    async def send_document(self, chat_id, *args, **kwargs):
        send = asyncio.create_task(super().send_document(chat_id, *args, **kwargs))
        send.add_done_callback(self._mark_send_done)
        msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(chat_id, msg)
        return msg

    async def send_poll(self, chat_id, *args, **kwargs):
        send = asyncio.create_task(super().send_poll(chat_id, *args, **kwargs))
        send.add_done_callback(self._mark_send_done)
        msg, _ = await asyncio.gather(send, self._pre_send(chat_id))
        self._post_send(chat_id, msg)
        return msg
