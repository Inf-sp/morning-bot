import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import update_processor


def _update(chat):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat), effective_user=None)


def test_other_chats_do_not_wait_but_one_chat_keeps_order():
    log = []

    async def handler(name, delay):
        log.append(f"{name}+")
        await asyncio.sleep(delay)
        log.append(f"{name}-")

    async def main():
        processor = update_processor.PerChatUpdateProcessor(8)
        await asyncio.gather(
            processor.process_update(_update(1), handler("a1", 0.05)),  # долгий ответ ИИ в чате 1
            processor.process_update(_update(1), handler("a2", 0)),  # второе нажатие в том же чате — после первого
            processor.process_update(_update(2), handler("b1", 0)),  # другой чат — не ждёт
        )
        return processor

    processor = asyncio.run(main())
    assert log.index("b1-") < log.index("a1-")  # чужой чат закончил раньше долгого ответа
    assert log.index("a1-") < log.index("a2+")  # в одном чате — строго по очереди
    assert processor._locks == {} and processor._waiting == {}  # замки не копятся


def test_poll_answers_are_queued_per_user():
    update = SimpleNamespace(effective_chat=None, effective_user=SimpleNamespace(id=7))
    assert update_processor.chat_key(update) == "user:7"
    assert update_processor.chat_key(SimpleNamespace(effective_chat=None, effective_user=None)) == "other"
