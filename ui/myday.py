from telegram import MessageEntity

from .text import finish_dot
from .builder import MessageBuilder
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
    country="",
    weather_icon="🌡️",
    weather_line="",
    word_line="",
    word_lang="nl",
    movie_rebus=None,
    outfit_items=None,
    outfit_emoji="🧶",
    restaurant_line="",
    lifehack="",
    quote_text="",
    quote_author="",
):
    """Сводка дня: заголовок, затем по одной строке на блок с пустой строкой между ними."""
    b = MessageBuilder()
    place = str(city or "").strip()
    country = str(country or "").strip()
    flag = str(flag or "").strip()
    if country:
        place += f", {country}"
    if flag:
        place += f" {flag}"
    b.bold(f"Мой день · {header} · {place}".rstrip(" ·"))
    b.newline()
    b.spacer()

    if weather_line:
        _compact_line(b, weather_icon, "Погода", weather_line)

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
    elif movie_rebus:
        emoji = str(movie_rebus.get("emoji") or "🎬 ❓").strip()
        answer = str(movie_rebus.get("answer") or "Ответ").strip()
        b.text_line("🎬 ")
        b.bold("Ребус дня:")
        b.text_line(f" {emoji} → ")
        b.add(answer, MessageEntity.SPOILER)
        b.newline()
        b.spacer()

    outfit = ", ".join(
        str(item).strip()
        for item in (outfit_items or [])
        if str(item).strip()
    )
    outfit = cap_sentence(outfit)
    if outfit:
        b.text_line(f"{str(outfit_emoji or '👕').strip()} ")
        b.labeled_line("Образ", finish_dot(outfit), lowercase=False)
        b.spacer()

    if restaurant_line:
        b.text_line("🍽️ ")
        b.labeled_line(
            "Куда сходить", finish_dot(str(restaurant_line).strip()),
            lowercase=False,
        )
        b.spacer()

    if lifehack:
        b.labeled_line("🦉Лайфхак", cap_sentence(finish_dot(lifehack)), lowercase=False)
        b.spacer()

    quote_text = str(quote_text or "").strip().strip("«»\"")
    quote_author = str(quote_author or "").strip()
    if quote_text:
        b.text_line("💭 ")
        b.add(f"«{quote_text}»", MessageEntity.ITALIC)
        if quote_author:
            b.text_line(f" — {quote_author}")
        b.newline()

    return b.build_stripped()
