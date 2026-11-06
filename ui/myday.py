import re
from telegram import MessageEntity

from .text import finish_dot
from .builder import MessageBuilder, MessageSpec
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


def week_holidays(city, period, holidays, events) -> MessageSpec:
    """Недельная рассылка: «🎉 Праздники и мероприятия · Alkmaar · 3–9 ноября», «Праздники:» — «• 11 ноября —
    Sint-Maarten: дети с фонариками…», «В городе:» — «• 3–8 ноября — Filmfestival Alkmaar — кинофестиваль»."""
    from util import _MONTHS

    def day_label(value):
        from datetime import date
        day = date.fromisoformat(value)
        return f"{day.day} {_MONTHS[day.month - 1]}"

    def span(start, end):
        if start == end:
            return day_label(start)
        if start[:7] == end[:7]:
            return f"{int(start[8:])}–{day_label(end)}"
        return f"{day_label(start)} – {day_label(end)}"

    b = MessageBuilder()
    b.title(" · ".join(part for part in ("🎉 Праздники и мероприятия", city, period) if part))
    if holidays:
        b.bold("Праздники:")
        b.newline()
        for holiday in holidays:
            b.line(f"• {day_label(holiday['date'])} — {holiday['name']}"
                   + (f": {holiday['about']}." if holiday.get("about") else ""))
    if events:
        if holidays:
            b.spacer()
        b.bold("В городе:")
        b.newline()
        for event in events:
            b.text_line(f"• {span(event['start'], event['end'])} — ")
            url = str(event.get("url") or "")
            b.bold_link(event["name"], url) if url.startswith("http") else b.text_line(event["name"])
            if event.get("about"):
                b.text_line(f" — {event['about']}")
            b.newline()
    return b.build_stripped()


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


_FACT_MAX = 110
_NOTE_MAX = 60


def _short_note(text) -> str:
    """Что это за блюдо — очень коротко, одной частью: первое предложение до «, » / « — » / «; » (до _NOTE_MAX);
    со строчной буквы, без точки; не вышло коротко — ""."""
    text = " ".join(str(text or "").split())
    text = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0].rstrip(".!?")
    for separator in (" — ", "; ", ", ", ": "):
        if len(text) <= _NOTE_MAX:
            break
        text = text.split(separator, 1)[0]
    if not text or len(text) > _NOTE_MAX:
        return ""
    return text[:1].lower() + text[1:]


def _short_fact(text) -> str:
    """Факт дня в сводке — коротко: длинный режется по «, » / « — » / «; » не дальше _FACT_MAX, иначе по слову."""
    text = " ".join(str(text or "").split()).rstrip(".")
    if len(text) <= _FACT_MAX:
        return text
    head = text[:_FACT_MAX]
    cut = max(head.rfind(", "), head.rfind(" — "), head.rfind("; "))
    if cut >= 40:
        return head[:cut]
    return head.rsplit(" ", 1)[0].rstrip(",;:—- ") + "…"


_MAX_BLOCKS = 7  # строк-блоков в сводке вместе с цитатой; больше — цитата не показывается


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
    # В отпуске — какой это день поездки: «Мой 8 день в отпуске · Пн, 26 октября · Кейптаун, ZA 🇿🇦»; с датой
    # возвращения — сколько всего: «Мой 3 день в отпуске из 7».
    trip_day, trip_days = int(vacation.get("day") or 0), int(vacation.get("days") or 0)
    title = ((f"Мой {trip_day} день в отпуске" + (f" из {trip_days}" if trip_days >= trip_day else ""))
             if trip_day > 0 else "Мой день в отпуске") if vacation else "Мой день"
    b.bold(f"{title} · {header} · {place}".rstrip(" ·"))
    b.newline()
    b.spacer()

    # Порядок — по срочности: что меняет планы (работы на ЖД), погода, праздники и события, затем фраза, образ,
    # цитата. Цитата — только если сводка не длиннее _MAX_BLOCKS строк: в насыщенный день она лишняя.
    blocks = sum((
        len(rail_works or []), bool(weather_line), len(holidays or []), bool(vacation.get("country_lines")),
        bool(word_line or movie_rebus), bool([item for item in outfit_items or [] if str(item).strip()]),
        bool(str((vacation.get("try_dish") or {}).get("name") or "").strip()),
        bool(str(vacation.get("country_fact") or "").strip()), bool(premiere),
    ))

    # Плановые работы NS на станциях города — только в дни работ.
    for works in rail_works or []:
        title = _works_section(works.get("title"))
        period = _works_period(works.get("start"), works.get("end"))
        # Одной строкой: участок · даты · что с поездами («Меньше поездов.»).
        level = str(works.get("level") or "")
        text = " · ".join(part for part in (title, period, f"{level[:1].upper()}{level[1:]}." if level else "") if part)
        if text:
            b.text_line("🚧 ")
            b.labeled_line("Работы на ЖД", text, lowercase=False)
            b.spacer()

    if weather_line:
        _compact_line(b, weather_icon, "Погода", weather_line)

    # Праздники страны и события города — в день праздника и накануне.
    for line in holidays or []:  # «🎉 Сегодня: Koningsdag — День короля» — подпись жирной, как у остальных строк
        label, sep, rest = str(line).partition(": ")
        b.text_line("🎉 ")
        if sep and len(label.split()) <= 2:
            b.labeled_line(label, rest, lowercase=False)
        else:
            b.line(str(line))
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
    if str(dish.get("name") or "").strip():  # «🍽 Попробовать: Банни чоу — хлеб с карри.»: название и очень коротко
        note = _short_note(dish.get("note"))
        b.text_line("🍽 ")
        b.labeled_line("Попробовать", finish_dot(str(dish["name"]).strip() + (f" — {note}" if note else "")),
                       lowercase=False)
        b.spacer()

    # В отпуске — интересный факт о стране, свой на каждый день поездки.
    country_fact = str(vacation.get("country_fact") or "").strip()
    if country_fact:
        b.text_line("💡 ")
        b.labeled_line("Факт дня", finish_dot(_short_fact(country_fact)), lowercase=False)
        b.spacer()

    # Премьера дня из Досуга — только сегодняшняя, одна строка.
    if premiere:
        _premiere_line(b, premiere)

    quote_text = str(quote_text or "").strip().strip("«»\"")
    quote_author = str(quote_author or "").strip()
    if quote_text and blocks < _MAX_BLOCKS:
        b.text_line("💭 ")
        b.add(f"«{quote_text}»", MessageEntity.ITALIC)
        if quote_author:
            b.text_line(f" © {quote_author}")
        b.newline()

    return b.build_stripped()
