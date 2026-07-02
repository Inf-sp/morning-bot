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
    b.section("✨ Образ на сегодня")
    if intro:
        b.spacer()
        b.line(intro)
    if items:
        quote = "\n".join(f"• {str(it).strip()}" for it in items if str(it).strip())
        if quote:
            b.spacer()
            b.quote(quote)
            b.newline()
    if add_text:
        b.spacer()
        b.add(add_text, MessageEntity.ITALIC)
    return b.build_stripped()


def entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Что важно:"):
    b = MessageBuilder()
    b.section(_clean_text(title).rstrip(".:"))

    summary = _finish_dot(summary)
    if summary:
        b.spacer()
        b.line(summary)

    quote = _finish_dot(quote)
    if quote:
        b.spacer()
        b.quote(quote)
        b.newline()

    clean_bullets = [_finish_dot(x) for x in (bullets or []) if _clean_text(x)]
    if clean_bullets:
        b.section(_clean_text(bullet_label).rstrip(":") + ":")
        b.line("\n".join(f"- {x}" for x in clean_bullets))

    final = _finish_dot(final)
    if final:
        b.spacer()
        b.line(final)

    return b.build_stripped()
