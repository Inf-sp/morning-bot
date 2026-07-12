from telegram import MessageEntity

from .builder import MessageBuilder


def _compact_line(b, emoji, label, content):
    b.text_line(f"{emoji} ")
    b.bold(f"{label}:")
    b.text_line(f" {content}")
    b.newline()


def day_summary(
    header,
    city,
    flag="",
    weather_icon="🌡️",
    weather_line="",
    humidity_line="",
    word_line="",
    word_lang="nl",
    fact="",
    lifehack="",
    quote_text="",
    quote_author="",
    lagom_line="",
):
    """Компактная сводка: заголовок, затем по одной строке на блок без пустых строк
    между ними (правило проекта — пробел только после заголовка)."""
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

    if word_line:
        word_label = "Нидерландский" if word_lang == "nl" else "Английский"
        word_flag = "🇳🇱" if word_lang == "nl" else "🇬🇧"
        _compact_line(b, word_flag, word_label, word_line)

    if fact:
        _compact_line(b, "👨🏻‍💻", "Факт", str(fact).strip())

    if lifehack:
        _compact_line(b, "🦉", "Полезно", lifehack)

    if quote_text:
        quote_line = f"«{quote_text}»" + (f" — по {quote_author}" if quote_author else "")
        b.add(f"💬 {quote_line}", MessageEntity.ITALIC)
        b.newline()

    if lagom_line:
        b.add(f"💭 {lagom_line}", MessageEntity.ITALIC)

    return b.build_stripped()
