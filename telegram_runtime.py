"""Telegram transport adapters shared by the application bootstrap."""

import asyncio
import logging
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


# Цвет кнопки по контракту из AGENTS.md (Bot API 9.4): действие с данными — по эмодзи.
_BUTTON_STYLES = (("✅", "success"), ("❌", "danger"), ("✨", "primary"))
_buttons_enhanced = True  # выключается до рестарта, если Telegram отклонит новые поля


def _enhance_markup(markup):
    """Inline-клавиатура с цветами и неактивными служебными кнопками, либо None.

    ``noop``-кнопки (счётчик страниц, индикатор ожидания) становятся disabled
    (Bot API 10.3): нажатие ничего не отправляет боту.
    """
    if not isinstance(markup, InlineKeyboardMarkup):
        return None
    data = markup.to_dict()
    changed = False
    for row in data.get("inline_keyboard", []):
        for button in row:
            if button.get("callback_data") == "noop":
                button.pop("callback_data")
                button["disabled"] = {}
                changed = True
                continue
            text = str(button.get("text") or "")
            style = next((value for prefix, value in _BUTTON_STYLES if text.startswith(prefix)), None)
            if style and "style" not in button:
                button["style"] = style
                changed = True
    return data if changed else None


class MenuCleanupBot(ExtBot):
    """Telegram delivery wrapper that keeps previously sent inline controls usable."""

    async def _post(self, endpoint, data=None, *, api_kwargs=None, **kwargs):
        global _buttons_enhanced
        data = {**(data or {}), **(api_kwargs or {})}
        enhanced = _enhance_markup(data.get("reply_markup")) if _buttons_enhanced else None
        if enhanced is None:
            return await super()._post(endpoint, data, **kwargs)
        try:
            return await super()._post(endpoint, {**data, "reply_markup": enhanced}, **kwargs)
        except BadRequest as error:
            if "not modified" in str(error).casefold():
                raise
            result = await super()._post(endpoint, data, **kwargs)
            _buttons_enhanced = False
            _log.warning("Telegram rejected button styles; plain keyboards until restart: %s", error)
            return result

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
