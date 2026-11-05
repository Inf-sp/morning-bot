"""Обработка обновлений Telegram: разные чаты — параллельно, один чат — строго по очереди.

python-telegram-bot по умолчанию обрабатывает обновления по одному: пока один пользователь ждёт ответа ИИ (до 15 с),
нажатия и сообщения всех остальных стоят в очереди. Здесь у каждого чата своя очередь (asyncio.Lock): внутри чата
порядок как прежде (двойное нажатие, ввод после вопроса), а чаты друг друга не ждут.
"""
import asyncio

from telegram.ext import BaseUpdateProcessor

MAX_CONCURRENT_UPDATES = 32


def chat_key(update):
    """Чей это апдейт: чат, иначе пользователь (ответ на опрос), иначе общая очередь."""
    chat = getattr(update, "effective_chat", None)
    if chat is not None:
        return f"chat:{chat.id}"
    user = getattr(update, "effective_user", None)
    return f"user:{user.id}" if user is not None else "other"


class PerChatUpdateProcessor(BaseUpdateProcessor):
    def __init__(self, max_concurrent_updates: int = MAX_CONCURRENT_UPDATES):
        super().__init__(max_concurrent_updates)
        self._locks = {}
        self._waiting = {}

    async def do_process_update(self, update, coroutine) -> None:
        key = chat_key(update)
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            async with lock:
                await coroutine
        finally:
            self._waiting[key] -= 1
            if not self._waiting[key]:  # очередь чата пуста — замок не копится в памяти
                self._waiting.pop(key, None)
                self._locks.pop(key, None)

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None
