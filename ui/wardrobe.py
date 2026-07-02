from telegram import MessageEntity

from .builder import MessageBuilder


def _clean_text(value):
    return " ".join(str(value or "").split()).strip()


def _finish_dot(value):
    value = _clean_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


def look_message(items, intro="", add_text=""):
    b = MessageBuilder()
    b.bold("✨ Образ на сегодня").blank()
    if intro:
        b.text_line(f"{intro}\n\n")
    if items:
        quote = "\n".join(f"• {str(it).strip()}" for it in items if str(it).strip())
        if quote:
            b.quote(f"{quote}\n")
    if add_text:
        b.blank().add(add_text, MessageEntity.ITALIC)
    msg = b.build()
    msg.text = msg.text.rstrip()
    return msg


def entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Что важно:"):
    b = MessageBuilder()
    b.bold(_clean_text(title).rstrip(".:"))

    summary = _finish_dot(summary)
    if summary:
        b.blank().text_line(summary)

    quote = _finish_dot(quote)
    if quote:
        b.blank().quote(quote)

    clean_bullets = [_finish_dot(x) for x in (bullets or []) if _clean_text(x)]
    if clean_bullets:
        b.blank().bold(_clean_text(bullet_label).rstrip(":") + ":")
        b.newline().text_line("\n".join(f"- {x}" for x in clean_bullets))

    final = _finish_dot(final)
    if final:
        b.blank().text_line(final)

    msg = b.build()
    msg.text = msg.text.rstrip()
    return msg
