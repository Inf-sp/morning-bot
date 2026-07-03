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


def from_html(html_text: str) -> MessageSpec:
    """Собирает готовый HTML-текст (составленный из нескольких кусков/эскейпов) в MessageSpec
    с entities — удобно, когда проще склеить строки с тегами, чем звать MessageBuilder по кускам."""
    from util import html_to_entities

    plain, entities = html_to_entities(html_text)
    return MessageSpec(text=plain, entities=entities)


WARNING_EMOJI = "⚠️"
TIP_EMOJI = "💡"
BULLET_MARK = "•"
DIVIDER_LINE = "—" * 16


class MessageBuilder:
    """Низкоуровневый билдер: пишет чанки текста и запоминает entities по UTF-16 offset.

    Поверх него — компонентные методы (section/line/bullet/warning/tip/divider/spacer),
    задающие ЕДИНЫЙ визуальный язык бота: одинаковый отступ вокруг заголовков, одинаковый
    вид предупреждений/советов и т.д. Правь оформление здесь — оно применится сразу во всех
    сообщениях, использующих эти методы, а не в каждой функции ui/*.py по отдельности.
    """

    def __init__(self):
        self._chunks = []
        self._entities = []
        self._has_content = False

    @property
    def text(self) -> str:
        return "".join(self._chunks)

    def add(self, text: str, entity_type=None):
        offset = u16_len(self.text)
        self._chunks.append(text)
        if entity_type and text:
            self._entities.append(MessageEntity(entity_type, offset, u16_len(text)))
        if text.strip():
            self._has_content = True
        return self

    def text_line(self, text: str):
        return self.add(text)

    def bold(self, text: str):
        return self.add(text, MessageEntity.BOLD)

    def italic(self, text: str):
        return self.add(text, MessageEntity.ITALIC)

    def code(self, text: str):
        return self.add(text, MessageEntity.CODE)

    def quote(self, text: str):
        return self.add(text, MessageEntity.BLOCKQUOTE)

    def link(self, text: str, url: str):
        offset = u16_len(self.text)
        self._chunks.append(text)
        if text:
            entity = MessageEntity(MessageEntity.TEXT_LINK, offset, u16_len(text), url=url)
            self._entities.append(entity)
            self._has_content = True
        return self

    def blank(self):
        return self.add("\n\n")

    def newline(self):
        return self.add("\n")

    def _ensure_blank_line(self):
        """Гарантирует ровно одну пустую строку перед следующим блоком, независимо от того,
        сколько переносов строк уже висит в хвосте буфера (0, 1 или больше)."""
        if not self._has_content:
            return self
        text = self.text
        trailing_newlines = len(text) - len(text.rstrip("\n"))
        needed = 2 - trailing_newlines
        if needed > 0:
            self.add("\n" * needed)
        return self

    # ---------- компоненты: единый визуальный язык бота ----------

    def section(self, title: str):
        """Заголовок раздела: bold-строка. Сама расставляет отступы —
        ровно одну пустую строку перед собой (если после неё уже что-то было) и перевод строки после."""
        self._ensure_blank_line()
        self.bold(title)
        self.newline()
        return self

    def line(self, text: str):
        """Обычная строка контента раздела."""
        self.text_line(text)
        self.newline()
        return self

    def bullet(self, text: str):
        """Пункт списка: '• текст'."""
        self.text_line(f"{BULLET_MARK} {text}")
        self.newline()
        return self

    def warning(self, text: str, emoji: str = WARNING_EMOJI):
        """Блок-предупреждение: 'emoji жирный текст' отдельной строкой с отступами вокруг."""
        self._ensure_blank_line()
        self.text_line(f"{emoji} ")
        self.bold(text)
        self.newline()
        return self

    def tip(self, text: str, emoji: str = TIP_EMOJI):
        """Блок-совет: тот же вид, что и warning(), другой emoji по умолчанию."""
        return self.warning(text, emoji=emoji)

    def divider(self):
        """Визуальный разделитель между смысловыми блоками одного сообщения."""
        self._ensure_blank_line()
        self.text_line(DIVIDER_LINE)
        self.newline()
        return self

    def spacer(self):
        """Явный контроль пустой строки, когда авто-отступов section()/warning() недостаточно."""
        return self._ensure_blank_line()

    def embed(self, msg: MessageSpec):
        """Вставляет уже готовый MessageSpec (text+entities) из другой функции — например
        встроить отдельно собранное штормовое предупреждение внутрь прогноза погоды.
        Сдвигает offset каждой entity на текущую позицию (в UTF-16 units), ничего не
        парсит заново. Сама расставляет отступ перед собой, как section()/warning()."""
        self._ensure_blank_line()
        offset = u16_len(self.text)
        self._chunks.append(msg.text)
        for e in (msg.entities or []):
            self._entities.append(MessageEntity(e.type, e.offset + offset, e.length, url=getattr(e, "url", None)))
        if msg.text.strip():
            self._has_content = True
        return self

    def build(self, reply_markup=None, parse_mode=None) -> MessageSpec:
        return MessageSpec(
            text=self.text,
            entities=self._entities,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    def build_stripped(self, reply_markup=None, parse_mode=None) -> MessageSpec:
        """Как build(), но обрезает финальный текст от краевых пустых строк.
        Безопасно для entities: section()/warning()/divider() добавляют пустые строки
        только МЕЖДУ блоками (has_content-гейт), поэтому единственное, что может остаться
        по краям — концевой перевод строки после последнего блока; entities его не занимают."""
        msg = self.build(reply_markup=reply_markup, parse_mode=parse_mode)
        msg.text = msg.text.strip()
        return msg
