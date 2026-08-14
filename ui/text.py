"""Общие helpers для коротких Telegram-карточек."""

import re

from .builder import MessageBuilder


def clean_card_text(value):
    value = re.sub(r"<[^>]+>", "", str(value or ""))
    value = re.sub(r"^[\s\U0001F1E6-\U0001FAFF\u2600-\u27BF\u200D\uFE0F]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def finish_dot(value):
    value = clean_card_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


def entity_card(title, summary="", quote="", bullets=None, final="",
                bullet_label="Рекомендации:", emoji=""):
    b = MessageBuilder()
    heading = clean_card_text(title).rstrip(".:")
    b.section(f"{emoji} {heading}" if emoji else heading)

    summary = finish_dot(summary)
    if summary:
        b.spacer()
        b.line(summary)

    quote = finish_dot(quote)
    if quote:
        b.spacer()
        b.quote(quote)
        b.newline()

    clean_bullets = [finish_dot(x) for x in (bullets or []) if clean_card_text(x)]
    if clean_bullets:
        b.spacer()
        b.bold(clean_card_text(bullet_label).rstrip(":") + ":")
        b.newline()
        b.line("\n".join(f"- {x}" for x in clean_bullets))

    final = finish_dot(final)
    if final:
        b.spacer()
        b.line(final)

    return b.build_stripped()
