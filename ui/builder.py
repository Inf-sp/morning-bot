from dataclasses import dataclass

from telegram import InlineKeyboardMarkup, MessageEntity


@dataclass
class MessageSpec:
    text: str
    entities: list[MessageEntity] | None = None
    reply_markup: InlineKeyboardMarkup | None = None
    parse_mode: str | None = None


def u16_len(text: str) -> int:
    return len((text or "").encode("utf-16-le")) // 2


class MessageBuilder:
    def __init__(self):
        self._chunks = []
        self._entities = []

    @property
    def text(self) -> str:
        return "".join(self._chunks)

    def add(self, text: str, entity_type=None):
        offset = u16_len(self.text)
        self._chunks.append(text)
        if entity_type and text:
            self._entities.append(MessageEntity(entity_type, offset, u16_len(text)))
        return self

    def text_line(self, text: str):
        return self.add(text)

    def bold(self, text: str):
        return self.add(text, MessageEntity.BOLD)

    def quote(self, text: str):
        return self.add(text, MessageEntity.BLOCKQUOTE)

    def blank(self):
        return self.add("\n\n")

    def newline(self):
        return self.add("\n")

    def build(self, reply_markup=None, parse_mode=None) -> MessageSpec:
        return MessageSpec(
            text=self.text,
            entities=self._entities,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
