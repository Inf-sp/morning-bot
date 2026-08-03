"""Central, opt-in delivery for Telegram Bot API Rich Messages.

``python-telegram-bot`` has not added typed Rich Message methods yet.  The
library's public ``do_api_request`` escape hatch is intentionally used by the
custom bot wrapper in ``bot.py``.  Feature screens pass a ``MessageSpec`` with
both rich content and a complete classic fallback, so delivery never depends on
one Telegram client capability.
"""

from __future__ import annotations

import logging
import secrets

from telegram.error import BadRequest, EndPointNotFound

import config

_log = logging.getLogger(__name__)
_FALLBACK_ERRORS = (BadRequest, EndPointNotFound)


def enabled(bot) -> bool:
    """Whether this bot instance can safely use the project Rich adapter."""
    return bool(
        config.TELEGRAM_RICH_MESSAGES
        and callable(getattr(bot, "send_rich_message", None))
    )


def _rich_payload(message):
    return getattr(message, "rich_message", None) or None


def _is_not_modified(error) -> bool:
    """Telegram treats an identical edit as a successful no-op."""
    return "message is not modified" in str(error or "").casefold()


async def _send_classic(bot, cid, message, reply_markup):
    return await bot.send_message(
        chat_id=cid,
        text=message.text,
        entities=message.entities,
        reply_markup=reply_markup,
        parse_mode=message.parse_mode,
    )


async def _edit_classic(query, message, reply_markup):
    return await query.message.edit_text(
        text=message.text,
        entities=message.entities,
        reply_markup=reply_markup,
        parse_mode=message.parse_mode,
    )


async def show(bot, cid, message, *, reply_markup=None, query=None):
    """Edit or send a ``MessageSpec``, preferring its optional rich payload.

    Only a Bot API validation/availability error falls back to classic text.
    Network timeouts deliberately propagate: sending a second message after an
    uncertain request could duplicate a useful result in the chat.
    """
    markup = reply_markup if reply_markup is not None else message.reply_markup
    rich_message = _rich_payload(message)
    can_send_rich = bool(rich_message and enabled(bot))
    target = getattr(query, "message", None) if query is not None else None

    if can_send_rich and target is not None and callable(getattr(bot, "edit_rich_message", None)):
        try:
            return await bot.edit_rich_message(
                chat_id=getattr(target, "chat_id", cid),
                message_id=target.message_id,
                rich_message=rich_message,
                reply_markup=markup,
            )
        except _FALLBACK_ERRORS as error:
            if _is_not_modified(error):
                return target
            _log.info("Rich edit unavailable; using classic fallback: %s", error)

    if target is not None:
        try:
            return await _edit_classic(query, message, markup)
        except Exception:
            # This mirrors the existing screen behavior: a stale/non-editable
            # callback message should not prevent the new screen from opening.
            pass

    if can_send_rich:
        try:
            return await bot.send_rich_message(
                chat_id=cid,
                rich_message=rich_message,
                reply_markup=markup,
            )
        except _FALLBACK_ERRORS as error:
            _log.info("Rich send unavailable; using classic fallback: %s", error)

    return await _send_classic(bot, cid, message, markup)


async def send(bot, cid, message, *, reply_markup=None):
    """Send only (without a callback edit path)."""
    return await show(bot, cid, message, reply_markup=reply_markup)


class Draft:
    """Ephemeral Bot API rich draft for one free-chat response.

    Telegram keeps a rich draft for at most 30 seconds.  The caller must finish
    the interaction with a normal rich message; this class never stores user
    content and only manages its non-zero draft id.
    """

    def __init__(self, bot, cid, draft_id):
        self.bot = bot
        self.cid = cid
        self.draft_id = draft_id

    async def thinking(self, text="Думаю…"):
        return await self.bot.send_rich_message_draft(
            chat_id=self.cid,
            draft_id=self.draft_id,
            rich_message={
                "blocks": [{"type": "thinking", "text": str(text or "Думаю…")}],
            },
        )

    async def text(self, value):
        """Replace the preview with generated text; used by token streams."""
        return await self.bot.send_rich_message_draft(
            chat_id=self.cid,
            draft_id=self.draft_id,
            rich_message={"blocks": [{"type": "paragraph", "text": str(value or "")}]},
        )


async def start_draft(bot, cid):
    """Start a non-blocking live preview, or return ``None`` for classic UI."""
    if not (enabled(bot) and callable(getattr(bot, "send_rich_message_draft", None))):
        return None
    draft = Draft(bot, cid, secrets.randbelow(2_147_483_646) + 1)
    try:
        await draft.thinking()
    except Exception as error:
        # A draft is strictly an enhancement. The normal status indicator and
        # response remain available if a chat is not private or a client/API
        # cannot display the new preview yet.
        _log.info("Rich draft unavailable; using classic status: %s", error)
        return None
    return draft
