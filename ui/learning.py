from telegram import MessageEntity

from .builder import MessageBuilder, MessageSpec, u16_len


def train_question(word):
    prefix = "Переведи слово «"
    suffix = "»"
    text = f"{prefix}{word}{suffix}"
    return MessageSpec(
        text=text,
        entities=[MessageEntity(MessageEntity.BOLD, u16_len(prefix), u16_len(str(word)))],
    )


def phrase_poll_question(blank_phrase, sentence_ru):
    b = MessageBuilder()
    b.bold("Фраза-тренажёр")
    b.blank()
    b.quote(str(blank_phrase or "").strip())
    if sentence_ru:
        b.blank()
        b.bold("Перевод:")
        b.text_line(f" {str(sentence_ru).strip()}")
    b.blank()
    b.text_line("Выбери пропущенное слово из вариантов ниже.")
    msg = b.build()
    msg.text = msg.text.strip()[:300]
    return msg


def _as_list(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _cap_first(text):
    text = (text or "").strip()
    return text[:1].upper() + text[1:] if text else text


def proverb_card(flag, original, analogs=None, meaning="", examples=None):
    b = MessageBuilder()
    header = f"💭{flag} Живой язык" if flag else "💭 Живой язык"
    b.bold(header)
    b.blank()

    if original:
        offset = u16_len(b.text)
        b.text_line(original)
        length = u16_len(original)
        b._entities.append(MessageEntity(MessageEntity.BOLD, offset, length))
        b._entities.append(MessageEntity(MessageEntity.BLOCKQUOTE, offset, length))

    analogs = _as_list(analogs)
    if analogs:
        b.blank()
        b.bold("Как это переводится?")
        b.newline()
        visible_analogs = analogs[:4]
        for i, analog in enumerate(visible_analogs):
            if i:
                b.text_line(" или " if i == len(visible_analogs) - 1 else ", ")
            b.text_line(f"«{_cap_first(analog) if i == 0 else analog}»")
        if meaning:
            b.text_line(f" ({meaning})")
        b.text_line(".")

    examples = _as_list(examples)
    if examples:
        b.blank()
        b.bold("Как говорить ПРАВИЛЬНО")
        b.newline()
        b.text_line(examples[0])

    b.blank()
    b.add("Прочитай вслух. Покрути в голове. Всё.", MessageEntity.ITALIC)
    msg = b.build()
    msg.text = msg.text.rstrip()
    return msg
