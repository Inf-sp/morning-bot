from telegram import MessageEntity

from .balance import finish_dot
from .builder import MessageBuilder


def _compact_line(b, emoji, label, content):
    b.text_line(f"{emoji} ")
    b.labeled_line(label, content)
    b.spacer()


def day_summary(
    header,
    city,
    flag="",
    weather_icon="🌡️",
    weather_line="",
    humidity_line="",
    word_line="",
    word_lang="nl",
    lifehack="",
    quote_text="",
    quote_author="",
):
    """Сводка дня: заголовок, затем по одной строке на блок с пустой строкой между ними."""
    title_flag = f" {flag}" if flag else ""
    b = MessageBuilder()
    b.bold("Мой день")
    b.text_line(f" · {header} · {city}{title_flag}")
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
        _compact_line(b, word_flag, word_label, word_line)

    if lifehack:
        _compact_line(b, "🦉", "Полезно", finish_dot(lifehack))

    if quote_text:
        quote_line = f"«{quote_text}»" + (f" — по {quote_author}" if quote_author else "")
        b.add(f"💭 {quote_line}", MessageEntity.ITALIC)
        b.newline()

    return b.build_stripped()
