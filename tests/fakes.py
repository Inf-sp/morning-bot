"""Общие тестовые подделки Telegram-объектов."""


class RecordingBot:
    """Запоминает отправленные сообщения: все в ``sent``, последнее в ``message``."""

    def __init__(self, sent=None):
        self.sent = [] if sent is None else sent
        self.message = None

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        self.message = kwargs
