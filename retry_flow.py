"""Координатор кнопки «Продолжить» между предметными разделами."""

import balance
import cooking


async def retry_last_response(bot, cid, status=None):
    if await cooking.retry_last_action(bot, cid, status=status):
        return
    await balance.retry(bot, cid, status=status)
