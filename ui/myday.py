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


def _works_period(start, end, today=None):
    """«с 5 по 12 октября»; «с 30 сентября по 2 октября»; один день — «10 октября»; работы уже идут — только конец:
    «до 30 ноября»."""
    if not start or not end:
        return ""
    if today is not None and start <= today < end:
        return f"до {end.day} {_MONTHS_GENITIVE[end.month]}"
    if start == end:
        return f"{start.day} {_MONTHS_GENITIVE[start.month]}"
    if start.month == end.month and start.year == end.year:
        return f"с {start.day} по {end.day} {_MONTHS_GENITIVE[end.month]}"
    return f"с {start.day} {_MONTHS_GENITIVE[start.month]} по {end.day} {_MONTHS_GENITIVE[end.month]}"


def _works_section(title):
    """«Alkmaar – Uitgeest – Hoorn» → «Alkmaar - Hoorn» (крайние станции); без двух станций — как есть."""
    parts = [part.strip() for part in re.split(r"\s[-–—]\s", str(title or "")) if part.strip()]
    return f"{parts[0]} - {parts[-1]}" if len(parts) >= 2 else str(title or "").strip()


def _works_level(level) -> str:
    """«Меньше поездов.» → «меньше поездов»: продолжение строки после «·», без точки."""
    level = str(level or "").strip().rstrip(".")
    return f"{level[:1].lower()}{level[1:]}" if level else ""


def _works_text(works, today=None) -> str:
    """«Haarlem - Alkmaar до 30 ноября · меньше поездов»: участок и даты слитно, дальше — что с поездами."""
    period = _works_period(works.get("start"), works.get("end"), today)
    joiner = " " if period.startswith(("с ", "до ")) else " · "  # «… до 30 ноября», но «Station Alkmaar · 10 октября»
    head = joiner.join(part for part in (_works_section(works.get("title")), period) if part)
    return " · ".join(part for part in (head, _works_level(works.get("level"))) if part)


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


_AGENDA_SECTIONS = (("season", "Зима и праздники сезона:"), ("culture", "Концерты и культура:"),
                    ("other", "Другие события:"))
_AGENDA_PER_SECTION = 5


def holidays_agenda(city, period, holidays, events) -> MessageSpec:
    """«🎉 Праздники» в Досуге — городская афиша на месяц: «Праздники:», затем события по разделам (зима и праздники
    сезона, концерты и культура, другое), в каждом до 5 ближайших по дате: «• 28 ноября — **Winterfair** — зимняя
    ярмарка · Rekerdijk · бесплатно»."""
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
    first, shown = True, []
    if holidays:
        b.bold("Праздники:")
        b.newline()
        for holiday in holidays:
            b.line(f"• {day_label(holiday['date'])} — {holiday['name']}"
                   + (f": {holiday['about']}." if holiday.get("about") else ""))
        first = False
    for key, heading in _AGENDA_SECTIONS:
        group = sorted((event for event in events or [] if (event.get("section") or "other") == key),
                       key=lambda event: event["start"])[:_AGENDA_PER_SECTION]
        if not group:
            continue
        if not first:
            b.spacer()
        first = False
        b.bold(heading)
        b.newline()
        shown += group
        for event in group:
            b.text_line(f"• {span(event['start'], event['end'])} — ")
            url = str(event.get("url") or "")
            b.bold_link(event["name"], url) if url.startswith("http") else b.text_line(event["name"])
            if event.get("about"):
                b.text_line(f" — {event['about']}")
            meta = [str(event.get("place") or "").strip(), "бесплатно" if event.get("free") else ""]
            if any(meta):
                b.text_line(" · " + " · ".join(value for value in meta if value))
            b.newline()
    free = sum(1 for event in shown if event.get("free"))
    if free:
        b.spacer()
        b.italic(f"Бесплатных событий · {free}")
        b.newline()
    return b.build_stripped()


def no_holidays(city, period) -> MessageSpec:
    """«Праздники» в Досуге, когда на месяц вперёд ничего нет."""
    b = MessageBuilder()
    b.title(" · ".join(part for part in ("🎉 Праздники и мероприятия", city, period) if part))
    b.line("Праздников и событий в городе на ближайший месяц пока нет.")
    return b.build_stripped()


def holidays_eve(city, lines):
    """Рассылка накануне в стиле уведомлений: заголовок «🎉 Завтра праздник · Alkmaar» (не жирный, как у всех), ниже
    пункты «• Sinterklaasavond — вечер подарков и шуточных стихов»."""
    b = MessageBuilder()
    b.title(" · ".join(part for part in ("🎉 Завтра праздник" if len(lines) == 1 else "🎉 Завтра праздники",
                                         str(city or "").strip()) if part))
    for line in lines:
        b.line(f"• {line}")
    return b.build_stripped()


_USUAL_COUNTRIES = {"US", "USA", "GB", "UK"}
_PREMIERE_GENRES = 2  # жанров в строке премьеры: больше — шум
_READ_TITLE_MAX = 60  # заголовок статьи дня — в одну строку на телефоне


def _clip_title(text) -> str:
    """Заголовок до _READ_TITLE_MAX по слову с «…»; короткий — как есть."""
    text = " ".join(str(text or "").split())
    if len(text) <= _READ_TITLE_MAX:
        return text
    return text[:_READ_TITLE_MAX].rsplit(" ", 1)[0].rstrip(",;:—-–«\"' ") + "…"


_PREMIERE_LABEL = {"movie": ("🎬", "Премьера в кино"), "series": ("📺", "Премьера сериала"),
                   "music": ("🎧", "Новый альбом")}


def _premiere_line(b: MessageBuilder, item) -> None:
    """«🎬 Премьера в кино: Уличный боец · боевик · триллер · USA»: название — ссылка на трейлер (у альбома —
    на YouTube Music), дальше жанры и страна кратко; у сериала — ещё сезон."""
    kind = item.get("kind")
    emoji, label = _PREMIERE_LABEL.get(kind, _PREMIERE_LABEL["movie"])
    country = str(item.get("country_label") or "").strip().partition(" ")[2]  # «🇺🇸 USA» → «USA»
    country = "" if country.upper() in _USUAL_COUNTRIES else country  # страна — только неожиданная, как в Досуге
    if kind == "music":
        name, url = item.get("artist"), youtube_music_link(item.get("artist"), item.get("title"))
        meta = [f"«{item['title']}»" if item.get("title") else "", item.get("genre_label"), country]
    else:
        name, url = item.get("title") if kind == "movie" else item.get("name"), trailer_link(item)
        season = int(item.get("season_number") or 0)
        genres = " · ".join([genre.strip() for genre in str(item.get("genres") or "").split(",")
                             if genre.strip()][:_PREMIERE_GENRES])
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


_MAX_BLOCKS = 10  # строк-блоков в сводке вместе с цитатой; больше — цитата не показывается


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
    quote_text="",
    quote_author="",
    rail_works=None,
    holidays=None,
    vacation=None,
    premiere=None,
    event=None,
    read=None,
    city_fact="",
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

    # Порядок — по срочности: что меняет планы (работы на ЖД), погода, праздники, фраза, затем событие, кино,
    # статья, город в цифрах и последней — цитата. Цитата — только если сводка не длиннее _MAX_BLOCKS строк: в насыщенный день она лишняя.
    blocks = sum((
        len(rail_works or []), bool(weather_line), len(holidays or []), bool(vacation.get("country_lines")),
        bool(word_line or movie_rebus),
        bool(str((vacation.get("try_dish") or {}).get("name") or "").strip()),
        bool(str(vacation.get("country_fact") or "").strip()), bool(premiere), bool(event), bool(read),
        bool(city_fact),
    ))

    # Плановые работы NS на станциях города — только в дни работ.
    for works in rail_works or []:
        # Одной строкой: «Haarlem - Alkmaar до 30 ноября · меньше поездов» (идут — без начала).
        from util import local_today
        text = _works_text(works, local_today())
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
        b.text_line("🧠 ")  # как у «Обучения» в главном меню
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

    # Ближайшее событие недели из «Событий» Досуга: «🎫 Концерт: Muse · 28 ноября 2026 · Ziggo Dome, Amsterdam».
    if event and str(event.get("name") or "").strip():
        from ui import leisure as leisure_ui
        emoji, label, meta = leisure_ui._event_meta(event)
        b.text_line(f"{emoji} ")
        b.label(label)
        b.text_line(" ")
        name, url = " ".join(str(event["name"]).split()), str(event.get("url") or "")
        b.link(name, url) if url.startswith("http") else b.text_line(name)
        values = [" ".join(str(value).split()) for value in meta if str(value or "").strip()]
        if values:
            b.text_line(" · " + " · ".join(values))
        b.newline()
        b.spacer()

    # Премьера дня из Досуга — только сегодняшняя, одна строка.
    if premiere:
        _premiere_line(b, premiere)

    # Статья дня из «Статей»: «📰 Статья дня: Заголовок до 60 символов… · Quanta · 8 мин».
    if read and str(read.get("title") or "").strip() and str(read.get("link") or "").startswith("http"):
        minutes = int(read.get("minutes") or 0)
        b.text_line("📰 ")
        b.label("Статья дня")
        b.text_line(" ")
        b.link(_clip_title(read["title"]), str(read["link"]))
        meta = [str(read.get("source") or "").strip(), f"{minutes} мин" if minutes else ""]  # язык — в «Статьях»
        if any(meta):
            b.text_line(" · " + " · ".join(value for value in meta if value))
        b.newline()
        b.spacer()

    # Город в цифрах (CBS): «📊 Город в цифрах: В Alkmaar живёт 110 000 человек — данные CBS за 2025 год.»
    if str(city_fact or "").strip():
        b.text_line("📊 ")
        b.labeled_line("Город в цифрах", finish_dot(str(city_fact).strip()), lowercase=False)
        b.spacer()

    quote_text = str(quote_text or "").strip().strip("«»\"")
    quote_author = str(quote_author or "").strip()
    if quote_text and blocks < _MAX_BLOCKS:
        b.text_line("💭 ")
        b.add(f"«{quote_text}»", MessageEntity.ITALIC)
        if quote_author:
            b.text_line(f" © {quote_author}")
        b.newline()

    return b.build_stripped()


def tomorrow_plan(plan, reply_markup=None) -> MessageSpec:
    """«🗓️ План на завтра · ср, 25 ноября»: «**Город:**», «**Погода:**» и строки частей суток «❄️ Утром: до -1°C ·
    без дождя · ветер 4 м/с», затем по строке «**Подпись:** значение» — KNMI, праздник, события, концерты, премьеры,
    работы на ЖД (только то, что есть); пусто — курсивом «Завтра без особых событий»."""
    from ui import leisure as leisure_ui
    from util import _MONTHS
    day = plan["day"]
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    b = MessageBuilder()
    # Заголовок — как у «Моего дня»: «План на завтра · Ср, 25 ноября · Alkmaar, NL 🇳🇱», жирным, без эмодзи.
    place = " ".join(part for part in (f"{plan.get('city') or ''}{', ' + plan['cc'] if plan.get('cc') else ''}",
                                       plan.get("flag") or "") if part).strip(" ,")
    b.bold(" · ".join(part for part in ("План на завтра", f"{weekdays[day.weekday()]}, {day.day} {_MONTHS[day.month - 1]}",
                                        place) if part))
    b.newline()
    if plan.get("weather"):  # что это погода — и так понятно: сразу части суток
        b.spacer()
        for part in plan["weather"]:
            b.line(f"{part['title']}: {part['temp']} · {part['rain']} · {part['wind']}")
    extra = []
    for line in plan.get("official") or []:  # «Код жёлтый KNMI — ветер, завтра с 10:00 до 18:00»
        extra.append(("⚠️", "Предупреждение", finish_dot(line)))
    for holiday in plan.get("holidays") or []:
        about = str(holiday.get("about") or "").strip()
        extra.append(("🎉", "Праздник", finish_dot(holiday["name"] + (f" — {about}" if about else ""))))
    for card in plan.get("events") or []:
        emoji, label, meta = leisure_ui._event_meta(card)
        values = [value for value in meta if str(value or "").strip()]
        extra.append((emoji, label, " · ".join([card["name"], *values[1:]])))  # дата — уже «завтра»
    for card in plan.get("premieres") or []:
        emoji, label, meta = leisure_ui._event_meta(card)
        values = [value for value in meta[:-1] if str(value or "").strip()]  # без даты — она завтра
        extra.append((emoji, "Премьера", " · ".join([card["name"], *values])))
    for works in plan.get("works") or []:
        extra.append(("🚧", "Работы на ЖД", " · ".join(part for part in (
            _works_section(works.get("title")), _works_level(works.get("level"))) if part)))
    if extra:
        b.spacer()
        for emoji, label, value in extra:
            b.text_line(f"{emoji} ")
            b.labeled_line(label, value, lowercase=False)
    else:
        b.spacer()
        b.italic("Завтра без особых событий — день можно планировать свободно.")
        b.newline()
    return b.build_stripped(reply_markup=reply_markup)

