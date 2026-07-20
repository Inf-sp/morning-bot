from telegram import MessageEntity

from .balance import finish_dot
from .builder import MessageBuilder, lower_initial
from util import cap_sentence


def _compact_line(b, emoji, label, content):
    b.text_line(f"{emoji} ")
    b.labeled_line(label, content)
    b.spacer()


def _split_word_translation(value):
    """Разделяет иностранную фразу и перевод для отдельного spoiler-entity."""
    value = str(value or "")
    if "→" not in value:
        return cap_sentence(value.strip()), ""
    term, translation = value.split("→", 1)
    return cap_sentence(term.strip()), cap_sentence(translation.strip())


def day_summary(
    header,
    city,
    flag="",
    weather_icon="🌡️",
    weather_line="",
    humidity_line="",
    word_line="",
    word_lang="nl",
    mood="",
    outfit_items=None,
    lifehack="",
    quote_text="",
    quote_author="",
):
    """Сводка дня: заголовок, затем по одной строке на блок с пустой строкой между ними."""
    b = MessageBuilder()
    b.bold(f"Мой день · {header} · {city}{' 📍' if city else ''}")
    b.newline()
    b.spacer()

    if weather_line:
        _compact_line(b, weather_icon, "Погода", weather_line)
    if humidity_line:
        b.line(humidity_line)
        b.spacer()

    if word_line:
        word_label = "Нидерландский" if word_lang == "nl" else "Английский"
        word_flag = "🇳🇱" if word_lang == "nl" else "🇬🇧"
        term, translation = _split_word_translation(word_line)
        b.text_line(f"{word_flag} ")
        b.label(word_label)
        if term:
            b.text_line(f" {term}")
        if translation:
            b.text_line(" → ")
            b.add(translation, MessageEntity.SPOILER)
        b.newline()
        b.spacer()

    if mood:
        b.text_line("⚡️ ")
        b.labeled_line("Настрой", finish_dot(mood), lowercase=False)
        b.spacer()

    outfit = ", ".join(
        str(item).strip()
        for item in (outfit_items or [])
        if str(item).strip()
    )
    outfit = cap_sentence(outfit)
    if outfit:
        b.text_line("👕 ")
        b.labeled_line("Надень", finish_dot(outfit), lowercase=False)
        b.spacer()

    if lifehack:
        b.text_line("🦉")
        b.labeled_line("Лайфхак", cap_sentence(finish_dot(lifehack)), lowercase=False)
        b.spacer()

    if quote_text:
        quote_line = f"«{quote_text}»" + (f" — по {quote_author}" if quote_author else "")
        b.add(f"💭 {quote_line}", MessageEntity.ITALIC)
        b.newline()

    return b.build_stripped()
