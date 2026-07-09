import re
from datetime import date, datetime

from telegram import MessageEntity

from .builder import MessageBuilder, MessageSpec, u16_len
from .constants import ui_label


def clip(text, limit=450):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= int(limit * 0.5):
        return cut[:end + 1].strip()
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut).rstrip(" ,.;:—-") + "…"


def _pluralize_titles(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "фильм/сериал"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "фильма/сериала"
    return "фильмов/сериалов"


def movie_home_screen(loved_count, genre_labels, country_label=None, now_playing=None):
    """Главный экран раздела «Кино»: польза, сколько уже в любимых, какие жанры
    выбраны в предпочтениях, что сейчас в прокате. Тот же визуальный паттерн,
    что у Гардероба (home_screen)."""
    b = MessageBuilder()
    b.text_line("🍿 ")
    b.bold("Что посмотреть")
    b.newline()
    b.spacer()
    b.line("Подберу фильм или сериал под твой вечер.")

    b.spacer()
    if loved_count <= 0:
        b.line("В любимых пока пусто.")
        b.spacer()
        b.line("Добавь фильмы или сериалы, которые понравились, — и я подберу похожее.")
    else:
        b.line(f"❤️ В любимых {loved_count} {_pluralize_titles(loved_count)}")

    if genre_labels:
        b.spacer()
        b.line("Жанры в предпочтениях:")
        for label in genre_labels:
            b.bullet(label)

    if now_playing:
        b.spacer()
        b.text_line("🎬 ")
        b.bold(f"В кино сейчас · {country_label}")
        b.newline()
        for item in now_playing:
            _format_movie_row(b, item)

    return b.build_stripped()


def _item_value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _primary_genre(movie) -> str | None:
    genres = _item_value(movie, "genres")
    if isinstance(genres, list):
        if not genres:
            return None
        return _movie_genre_text(genres[0])
    genre = _item_value(movie, "genre")
    return _movie_genre_text(genre) if genre else None


def _format_rating(rating: float | None) -> str | None:
    try:
        value = float(rating)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return f"⭐ {value:.1f}"


def _format_movie_row(b: MessageBuilder, movie) -> None:
    title = str(_item_value(movie, "title", "") or "").strip()
    if not title:
        return
    b.text_line("• ")
    b.bold(title)
    genre = _primary_genre(movie)
    if genre:
        b.text_line(f" · {genre}")
    rating = _format_rating(_item_value(movie, "rating"))
    if rating:
        b.text_line(f" · {rating}")
    b.newline()


def movie_card(item, tm):
    """Карточка рекомендации кино, спроектированная под быстрое решение (3-5 сек).

    Иерархия сверху вниз: что это (заголовок) → стоит ли смотреть и что за жанр
    (рейтинг · тип · жанры) → насколько долго (одна строка) → о чём (короткое
    описание) → почему именно мне (персональная причина).
    """
    item = item if isinstance(item, dict) else {"title": str(item)}
    title = (tm.get("name") if tm else "") or item.get("title", "")
    year = f" ({tm.get('year')})" if tm and tm.get("year") else ""
    kind = (tm.get("kind") if tm else "") or ""
    type_label = "Сериал" if kind == "tv" else ("Фильм" if kind == "movie" else "")

    b = MessageBuilder()

    # 1. Что это — заголовок.
    b.text_line(f"{ui_label('cinema', '')} ")
    b.bold(f"{title}{year}")
    b.newline()

    # 2. Стоит ли смотреть + что за жанр — одна строка-якорь без источника рейтинга.
    meta_parts = []
    if tm and tm.get("rating"):
        meta_parts.append(f"⭐ {tm['rating']:.1f}")
    if type_label:
        meta_parts.append(type_label)
    if tm and tm.get("genres"):
        meta_parts.append(tm["genres"])
    if meta_parts:
        b.spacer()
        b.line(" · ".join(meta_parts))

    # 3. Насколько это долго — компактная строка деталей (одна).
    detail = _detail_line(tm)
    if detail:
        b.line(detail)

    # 4. О чём — короткое описание (2-4 строки).
    if tm and tm.get("overview"):
        b.spacer()
        b.line(clip(tm["overview"], limit=260))

    # 5. Почему именно мне — персональная причина.
    reason = _reason_line(item, tm)
    if reason:
        b.spacer()
        b.line(reason)

    return title, b.build_stripped()


_MONTHS_RU = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _clip_title(s, limit=40):
    s = (s or "").strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _reason_line(item, tm):
    """Персональная причина «почему мне» — единственный источник истины: реальный
    источник рекомендации (§ниже), никогда не шаблонная/случайная фраза.

    Источники, в порядке проверки:
    - reason={"kind": "genre"|"mood", ...} — подбор по жанру/настроению (TMDb Discover),
      никак не связан с конкретным любимым тайтлом → не пишем «понравился», а называем
      реальный критерий подбора.
    - because + via — обычная рекомендация от TMDb Recommendations/Similar по любимому:
      Recommendations → «понравился», Similar → «похоже на» (разные степени уверенности).
    - иначе — old-path LLM-хук (item["hook"]) как есть.
    """
    tm = tm or {}
    reason = tm.get("reason")
    if reason:
        kind = reason.get("kind")
        label = _clip_title(reason.get("label", ""))
        if kind == "genre":
            return f"Подборка в жанре «{label}»"
        if kind == "mood":
            return f"Подборка для настроения «{label}»"
    because = tm.get("because")
    if because:
        title = _clip_title(because)
        if tm.get("via") == "similar":
            return f"Похоже на «{title}»"
        return f"Потому что вам понравился «{title}»"
    hook = (item.get("hook") or "").strip()
    return hook if hook else ""


def _detail_line(tm):
    """Одна компактная строка длительности/объёма. Статус сериала — ровно один вариант."""
    if not tm:
        return ""
    kind = tm.get("kind")
    if kind == "tv":
        parts = []
        # Статус — только ОДИН вариант (без дубля «продолжается» + «новый сезон ожидается»).
        status = (tm.get("status") or "").lower()
        ongoing = status in ("returning series", "in production", "planned")
        nxt = tm.get("next_episode")
        if ongoing and isinstance(nxt, dict) and nxt.get("air_date"):
            parts.append(f"Следующая серия — {_fmt_date(nxt['air_date'])}")
        elif ongoing:
            parts.append("Новый сезон ожидается")
        elif status:
            parts.append("Завершено")
        seasons, eps = tm.get("seasons"), tm.get("episodes")
        if seasons:
            plural_s = "сезон" if seasons == 1 else ("сезона" if 2 <= seasons <= 4 else "сезонов")
            vol = f"{seasons} {plural_s}"
            if eps:
                vol += f" • {eps} серий"
            parts.append(vol)
        return " · ".join(parts)
    if kind == "movie":
        parts = []
        if tm.get("runtime"):
            parts.append(f"{tm['runtime']} мин")
        countries = tm.get("countries") or []
        if countries:
            parts.append(", ".join(countries[:2]))
        return " · ".join(parts)
    return ""


def _fmt_date(iso):
    """'2024-10-18' → '18 октября'."""
    try:
        y, m, d = iso.split("-")
        return f"{int(d)} {_MONTHS_RU[int(m)]}"
    except (ValueError, IndexError):
        return iso


def book_text(item):
    """Составная карточка (условные блоки) -> MessageBuilder."""
    author = item.get("author", "")
    title = item.get("title", "")
    en = item.get("title_en", "")
    year = str(item.get("year", ""))
    head_meta = ", ".join(x for x in [en, year] if x)
    head = f"{author} • «{title}»" if author else f"«{title}»"

    b = MessageBuilder()
    b.text_line("📚 ")
    if not head_meta:
        b.bold(head)
    else:
        # "(meta)" одновременно жирный (продолжение заголовка) и курсивный —
        # как в исходном "<b>...<i>(meta)</i></b>": вложенные entity на одном диапазоне,
        # весь head+" (meta)" остаётся одной непрерывной bold-entity.
        meta_text = f"({head_meta})"
        head_and_gap_offset = u16_len(b.text)
        b.bold(f"{head} {meta_text}")
        meta_offset = head_and_gap_offset + u16_len(head) + 1
        b._entities.append(MessageEntity(MessageEntity.ITALIC, meta_offset, u16_len(meta_text)))
    b.newline()
    if item.get("desc"):
        b.spacer()
        b.line(item["desc"])
    why = item.get("why") or []
    if isinstance(why, list) and why:
        b.spacer()
        b.bold("Почему стоит читать")
        b.newline()
        for w in why:
            b.bullet(str(w).lstrip("-–— "))
    if item.get("plot"):
        b.spacer()
        b.text_line("✏️ ")
        b.bold("Коротко о сюжете")
        b.newline()
        b.line(item["plot"])
    if item.get("quote"):
        quote = str(item["quote"]).strip().strip("«»\"")
        b.spacer()
        b.bold(ui_label("quote", "Цитата"))
        b.newline()
        b.line(f"«{quote}»")
    return b.build_stripped()


def artist_card(data):
    """Составная карточка (условные блоки) -> MessageBuilder."""
    artist = data.get("artist", "")
    b = MessageBuilder()
    b.text_line("🎸 ")
    b.bold(artist)
    b.newline()
    if data.get("desc"):
        b.spacer()
        b.line(data["desc"])
    why = data.get("why") or []
    if isinstance(why, list) and why:
        b.spacer()
        b.bold("Почему тебе зайдёт:")
        b.newline()
        for w in why:
            b.bullet(str(w))
    tracks = data.get("tracks") or []
    if isinstance(tracks, list) and tracks:
        b.spacer()
        b.bold("С чего начать:")
        b.newline()
        for t in tracks:
            b.bullet(str(t))
    if data.get("fact"):
        b.spacer()
        b.bold(ui_label("interesting", "Факт:"))
        b.newline()
        b.line(data["fact"])
    return b.build_stripped()


def concerts_list(place_label, events, empty_hint=""):
    """Список концертов твоих артистов -> MessageBuilder. Каждое событие - мини-блок:
    имя артиста, место, цена от, дата, скрытая ссылка "Подробнее…"."""
    b = MessageBuilder()
    b.text_line(f"{ui_label('music', '')} ")
    b.bold(place_label)
    b.newline()
    if not events:
        b.spacer()
        b.line(empty_hint or "Сейчас ничего не нашёл. Попробуй другую страну.")
        return b.build_stripped()
    for ev in events:
        b.spacer()
        b.bold(ev.get("artist", ""))
        b.newline()
        if ev.get("place"):
            place = f"{ev.get('flag', '')} {ev['place']}".strip()
            b.line(f"Место: {place}")
        if ev.get("price"):
            b.line(f"Цена: {ev['price']}")
        if ev.get("date"):
            b.line(f"Дата: {ev['date']}")
        if ev.get("url"):
            b.link("Подробнее…", ev["url"])
            b.newline()
    return b.build_stripped()


def _parse_event_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_date_label(day: date, *, include_year: bool = False) -> str:
    text = f"{day.day} {_MONTHS_RU[day.month]}"
    if include_year:
        text += f" {day.year}"
    return text


def _join_with_and(parts) -> str:
    parts = [str(p) for p in parts if str(p).strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} и {parts[1]}"
    return f"{', '.join(parts[:-1])} и {parts[-1]}"


def _format_event_period(start_date: date, end_date: date) -> str:
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    if start_date == end_date:
        return _format_date_label(start_date, include_year=True)
    if start_date.year != end_date.year:
        return (
            f"{_format_date_label(start_date, include_year=True)}"
            f" – {_format_date_label(end_date, include_year=True)}"
        )
    if start_date.month == end_date.month:
        return f"{start_date.day}–{end_date.day} {_MONTHS_RU[start_date.month]}"
    return f"{_format_date_label(start_date)} – {_format_date_label(end_date)}"


def _format_dates(dates: list[date]) -> str:
    unique_dates = sorted(set(dates))
    if not unique_dates:
        return ""

    current_year = date.today().year
    same_month = all(d.year == unique_dates[0].year and d.month == unique_dates[0].month for d in unique_dates)
    if same_month:
        days = [str(d.day) for d in unique_dates]
        include_year = unique_dates[0].year != current_year
        if len(unique_dates) >= 3 and all(
            (unique_dates[idx] - unique_dates[idx - 1]).days == 1 for idx in range(1, len(unique_dates))
        ):
            text = f"{unique_dates[0].day}–{unique_dates[-1].day} {_MONTHS_RU[unique_dates[0].month]}"
        else:
            text = f"{_join_with_and(days)} {_MONTHS_RU[unique_dates[0].month]}"
        if include_year:
            text += f" {unique_dates[0].year}"
        return text

    if len(unique_dates) >= 3 and all(
        (unique_dates[idx] - unique_dates[idx - 1]).days == 1 for idx in range(1, len(unique_dates))
    ):
        return (
            f"{_format_date_label(unique_dates[0], include_year=unique_dates[0].year != current_year)}"
            f" – {_format_date_label(unique_dates[-1], include_year=unique_dates[-1].year != current_year)}"
        )

    return _join_with_and(
        _format_date_label(day, include_year=day.year != current_year) for day in unique_dates
    )


def _group_concerts(events) -> list[dict]:
    groups = {}
    order = []
    for event in events or []:
        title = str(event.get("title", "")).strip()
        place = str(event.get("place", "")).strip()
        day = _parse_event_date(event.get("date"))
        key = (title, place)
        if key not in groups:
            groups[key] = {"title": title, "place": place, "dates": []}
            order.append(key)
        if day:
            groups[key]["dates"].append(day)
    return [groups[key] for key in order if groups[key].get("title")]


def _group_movies_by_date(events) -> list[tuple[date, list[dict]]]:
    groups = {}
    order = []
    for event in events or []:
        day = _parse_event_date(_item_value(event, "release_date") or _item_value(event, "date"))
        if not day:
            continue
        if day not in groups:
            groups[day] = []
            order.append(day)
        groups[day].append(event)
    return [(day, groups[day]) for day in order]


def _movie_genre_text(genre: str | None) -> str:
    raw = str(genre or "").strip()
    mapping = {
        "Семейный": "семейный фильм",
        "семейный": "семейный фильм",
        "История": "исторический фильм",
        "история": "исторический фильм",
        "Документальный": "документальный фильм",
        "документальный": "документальный фильм",
        "Мультфильм": "мультфильм",
        "мультфильм": "мультфильм",
        "Премьера": "премьера",
        "премьера": "премьера",
    }
    if raw in mapping:
        return mapping[raw]
    return raw.lower()


def _concert_card(b: MessageBuilder, event: dict) -> None:
    b.bold(event.get("title", ""))
    b.newline()
    if event.get("place"):
        b.line(f"Место: {event['place']}")
    date_text = _format_dates([d for d in event.get("dates", []) if isinstance(d, date)])
    if date_text:
        b.line(f"Дата: {date_text}")


def _movie_item(b: MessageBuilder, event: dict) -> None:
    title = str(_item_value(event, "title", "") or "").strip()
    if not title:
        return
    b.text_line("• ")
    b.bold(title)
    genre = _primary_genre(event)
    if genre:
        b.text_line(f" · {genre}")
    b.newline()


def weekly_events_card(period_start: date, period_end: date, concerts, movies) -> MessageSpec:
    concert_groups = _group_concerts(concerts)
    movie_groups = _group_movies_by_date(movies)

    b = MessageBuilder()
    b.text_line(f"{ui_label('music', '')} ")
    b.bold(f"Ближайшие события · {_format_event_period(period_start, period_end)}")
    b.newline()

    if concert_groups:
        b.section("🎸 Концерты")
        for idx, event in enumerate(concert_groups):
            if idx:
                b.spacer()
            _concert_card(b, event)

    if movie_groups:
        b.section("🎬 Кино")
        b.newline()
        for idx, (day, items) in enumerate(movie_groups):
            if idx:
                b.newline()
            b.line(_format_date_label(day, include_year=day.year != date.today().year))
            for event in items:
                _movie_item(b, event)

    if not concert_groups and not movie_groups:
        b.spacer()
        b.line("Пока ничего интересного не нашлось.")

    return b.build_stripped()


def plain_from_html(text):
    return re.sub(r"<[^>]+>", "", text or "")
