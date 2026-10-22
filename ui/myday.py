import re
from telegram import MessageEntity

from .text import finish_dot
from .builder import MessageBuilder
from .leisure import trailer_link, youtube_music_link
from util import cap_sentence


def _compact_line(b, emoji, label, content):
    b.text_line(f"{emoji} ")
    b.labeled_line(label, content)
    b.spacer()


_MONTHS_GENITIVE = ("", "января", "февраля", "марта", "апреля", "мая", "июня",
                    "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _works_period(start, end):
    """«с 5 по 12 октября»; «с 30 сентября по 2 октября»; один день — «10 октября»."""
    if not start or not end:
        return ""
    if start == end:
        return f"{start.day} {_MONTHS_GENITIVE[start.month]}"
    if start.month == end.month and start.year == end.year:
        return f"с {start.day} по {end.day} {_MONTHS_GENITIVE[end.month]}"
    return f"с {start.day} {_MONTHS_GENITIVE[start.month]} по {end.day} {_MONTHS_GENITIVE[end.month]}"


def _works_section(title):
    """«Alkmaar - Hoorn» → «от Alkmaar до Hoorn»; без двух станций — как есть."""
    parts = [part.strip() for part in re.split(r"\s[-–—]\s", str(title or "")) if part.strip()]
    return f"от {parts[0]} до {parts[-1]}" if len(parts) >= 2 else str(title or "").strip()


def _split_word_translation(value):
    """Разделяет иностранную фразу и перевод для отдельного spoiler-entity."""
    value = str(value or "")
    if "→" not in value:
        return cap_sentence(value.strip()), ""
    term, translation = value.split("→", 1)
    return cap_sentence(term.strip()), cap_sentence(translation.strip())


def holidays_eve(city, lines):
    """Рассылка накануне: «🎉 Завтра · Алкмар» и по строке на праздник или событие."""
    b = MessageBuilder()
    b.bold(f"🎉 Завтра · {city}".rstrip(" ·"))
    b.newline()
    b.spacer()
    for line in lines:
        b.line(line)
    return b.build_stripped()


_PREMIERE_LABEL = {"movie": ("🎬", "Премьера в кино"), "series": ("📺", "Премьера сериала"),
                   "music": ("🎧", "Новый альбом")}


def _premiere_line(b: MessageBuilder, item) -> None:
    """«🎬 Премьера в кино: Уличный боец · боевик · триллер · USA»: название — ссылка на трейлер (у альбома —
    на YouTube Music), дальше жанры и страна кратко; у сериала — ещё сезон."""
    kind = item.get("kind")
    emoji, label = _PREMIERE_LABEL.get(kind, _PREMIERE_LABEL["movie"])
    country = str(item.get("country_label") or "").strip().partition(" ")[2]  # «🇺🇸 USA» → «USA»
    if kind == "music":
        name, url = item.get("artist"), youtube_music_link(item.get("artist"), item.get("title"))
        meta = [f"«{item['title']}»" if item.get("title") else "", item.get("genre_label"), country]
    else:
        name, url = item.get("title") if kind == "movie" else item.get("name"), trailer_link(item)
        season = int(item.get("season_number") or 0)
        genres = str(item.get("genres") or "").replace(", ", " · ")
        meta = [f"{season}-й сезон" if kind == "series" and season > 1 else "", genres, country]
    name = " ".join(str(name or "").split())
    if not name:
        return
    b.text_line(f"{emoji} ")
    b.label(label)
    b.text_line(" ")
    b.link(name, url) if url else b.text_line(name)
    joiner = " — " if kind == "music" else " · "  # «Alvvays — «Blue Rev» · Инди · CA»
    values = [" ".join(str(value).split()) for value in meta if str(value or "").strip()]
    if values:
        b.text_line(joiner + values[0] + "".join(f" · {value}" for value in values[1:]))
    b.newline()
    b.spacer()


def day_summary(
    header,
    city,
    flag="",
    country="",
    weather_icon="🌡️",
    weather_line="",
    word_line="",
    word_lang="nl",
    word_title="Фраза дня",
    movie_rebus=None,
    outfit_items=None,
    outfit_emoji="🧶",
    quote_text="",
    quote_author="",
    rail_works=None,
    holidays=None,
    vacation=None,
    premiere=None,
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
    vacation = vacation or {}
    b.bold(f"Мой день{' в отпуске' if vacation else ''} · {header} · {place}".rstrip(" ·"))
    b.newline()
    b.spacer()

    if weather_line:
        _compact_line(b, weather_icon, "Погода", weather_line)

    # Праздники страны — в день праздника и накануне.
    for line in holidays or []:
        b.line(f"🎉 {line}")
        b.spacer()

    # Плановые работы NS на станциях города — только в дни работ.
    for works in rail_works or []:
        title = _works_section(works.get("title"))
        period = _works_period(works.get("start"), works.get("end"))
        text = " · ".join(part for part in (title, period) if part)
        if text:
            b.text_line("🚧 ")
            b.labeled_line("Работы на ЖД", text, lowercase=False)
            b.spacer()

    if vacation.get("country_lines"):
        b.bold("🌍 Страна:")
        b.newline()
        for label, value in vacation["country_lines"]:
            b.labeled_line(label, value, lowercase=False)
        b.spacer()

    if word_line:
        term, translation = _split_word_translation(word_line)
        b.text_line("💬 ")
        b.label(word_title or "Фраза дня")
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

    # В отпуске — блюдо, которое стоит попробовать сегодня (главное в меню Кухни).
    dish = vacation.get("try_dish") or {}
    if str(dish.get("name") or "").strip():
        note = str(dish.get("note") or "").strip()
        b.text_line("🍽 ")
        b.label("Попробовать сегодня")
        b.text_line(f" {dish['name']}" + (f" — {note[:1].lower()}{note[1:]}" if note else ""))
        b.newline()
        b.spacer()

    # В отпуске — интересный факт о стране, свой на каждый день поездки.
    country_fact = str(vacation.get("country_fact") or "").strip()
    if country_fact:
        b.text_line("💡 ")
        b.labeled_line("Факт дня", finish_dot(country_fact), lowercase=False)
        b.spacer()

    # Премьера дня из Досуга — только сегодняшняя, одна строка.
    if premiere:
        _premiere_line(b, premiere)

    quote_text = str(quote_text or "").strip().strip("«»\"")
    quote_author = str(quote_author or "").strip()
    if quote_text:
        b.text_line("💭 ")
        b.add(f"«{quote_text}»", MessageEntity.ITALIC)
        if quote_author:
            b.text_line(f" © {quote_author}")
        b.newline()

    return b.build_stripped()
