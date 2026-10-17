import html
import re
from datetime import date, datetime
from urllib.parse import quote_plus

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .builder import MessageBuilder, MessageSpec, u16_len
from .constants import ui_label
from .navigation import nav_row
from .text import ru_plural


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


def _clean_external_text(value):
    text = html.unescape(str(value or ""))
    text = text.replace("\ufffd", "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _pluralize_titles(n):
    return ru_plural(n, "фильм/сериал", "фильма/сериала", "фильмов/сериалов")


FAVORITE_LIST_PAGE = 12  # названий на странице жанра «Моей библиотеки»


def favorite_genre_list(icon, genre, titles, page, *, prompt, card_cb, list_cb, back):
    """Жанр «Моей библиотеки» списком, как «Мой словарь»: названия кнопками в два столбца, нажатие открывает
    карточку. card_cb(index) и list_cb(page) дают callback_data. Возвращает (MessageSpec, клавиатура)."""
    pages = max(1, -(-len(titles) // FAVORITE_LIST_PAGE))
    page = max(0, min(int(page), pages - 1))
    start = page * FAVORITE_LIST_PAGE
    buttons = [InlineKeyboardButton(title[:24], callback_data=card_cb(start + offset))
               for offset, title in enumerate(titles[start:start + FAVORITE_LIST_PAGE])]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    if pages > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=list_cb((page - 1) % pages)),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=list_cb((page + 1) % pages)),
        ])
    rows.append(nav_row(back))
    b = MessageBuilder()
    b.section(f"{icon} {genre} · {len(titles)}")
    b.spacer()
    b.line(prompt)
    return b.build_stripped(), InlineKeyboardMarkup(rows)


def favorite_movies_home(total):
    b = MessageBuilder()
    b.title(f"🎚️ Моё кино · {total} {_pluralize_titles(total)}")
    if not total:
        b.spacer()
        b.line("Добавь любимые фильмы и сериалы — подбор станет точнее.")
    else:
        b.italic("По любимым фильмам и сериалам подбираю похожее.")
        b.newline()
    return b.build_stripped()


def yearly_top_screen(kind, year, item):
    labels = {
        "movie": "Фильмы", "tv": "Сериалы",
    }
    b = MessageBuilder()
    b.section(f"🏆 Топ-5 · {labels.get(kind, 'Лучшее')} {year}")
    if not item:
        b.line("Пока не удалось загрузить проверенный список.")
        return b.build_stripped()
    title = str(item.get("title") or item.get("name") or "").strip()
    url = trailer_link(item)  # трейлер на YouTube, не страница TMDb
    if url:
        b.link(title, url)
        b.newline()
    else:
        b.bold(title)
        b.newline()
    meta = " · ".join(filter(None, (
        str(item.get("author") or "").strip(),
        str(item.get("genre") or item.get("genres") or "").strip(),
    )))
    if meta:
        b.line(meta)
    summary = _movie_premiere_summary(
        item.get("summary") or item.get("overview") or item.get("description"),
        limit=220,
    )
    if summary:
        if summary[-1] not in ".!?…":
            summary += "."
        b.spacer()
        b.line(summary)
    return b.build_stripped()


def _movie_genres_for_line(movie) -> str:
    raw_genres = _item_value(movie, "genres")
    if isinstance(raw_genres, str):
        raw_genres = re.split(r"\s*[,·/]\s*", raw_genres)
    if not isinstance(raw_genres, list):
        raw_genres = [_item_value(movie, "genre")]
    translations = {
        "music": "музыка", "adventure": "приключения", "science fiction": "фантастика",
        "fantasy": "фэнтези", "drama": "драма", "comedy": "комедия", "horror": "ужасы",
        "thriller": "триллер", "romance": "романтика", "animation": "анимация",
        "documentary": "документальный фильм", "crime": "криминал", "action": "боевик",
    }
    labels = []
    for value in raw_genres:
        genre = _clean_external_text(value)
        if genre:
            labels.append(translations.get(genre.casefold(), genre.casefold()))
    return ", ".join(dict.fromkeys(labels[:3]))


def _item_value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def movie_card(item, tm):
    """Карточка рекомендации кино, спроектированная под быстрое решение (3-5 сек).

    Иерархия сверху вниз: что это (заголовок) → стоит ли смотреть и что за жанр
    (рейтинг · тип · жанры) → о чём (короткое описание) → почему именно мне
    (персональная причина). Дата выхода — в строке рейтинга. Длительность фильма,
    страна и подпись «Подборка в жанре…» не показываются.
    """
    item = item if isinstance(item, dict) else {"title": str(item)}
    title = (tm.get("name") if tm else "") or item.get("title", "")
    year = str(tm.get("year") or "") if tm else ""
    kind = (tm.get("kind") if tm else "") or ""
    type_label = "Сериал" if kind == "tv" else ("Фильм" if kind == "movie" else "")

    b = MessageBuilder()
    trailer = str((tm or {}).get("trailer_url") or "").strip()

    # 1. Что это — заголовок; трейлер спрятан в названии.
    b.text_line(f"{ui_label('cinema', '').strip()} ")
    if trailer:
        b.bold_link(title, trailer)
    else:
        b.bold(title)
    b.newline()

    # 2. Стоит ли смотреть + что за жанр — одна строка-якорь без источника рейтинга.
    meta_parts = []
    # Не показываем эффектный рейтинг вроде 10.0, если он основан на нескольких
    # случайных голосах: для рекомендации нужна минимальная выборка.
    if tm and tm.get("rating") and int(tm.get("vote_count") or 0) >= 50:
        meta_parts.append(f"⭐ {tm['rating']:.1f}")
    if type_label:
        meta_parts.append(type_label)
    if tm and tm.get("lgbt"):
        meta_parts.append("🏳️‍🌈 ЛГБТ")
    if tm and tm.get("genres"):
        meta_parts.append(tm["genres"])
    if tm and tm.get("country_label"):
        meta_parts.append(tm["country_label"])
    # Дата выхода — в той же строке: полная дата премьеры, если известна, иначе год.
    # Год виден всегда, в том числе у премьер текущего года: «16 октября 2026».
    released = _parse_event_date(str((tm or {}).get("release_date") or "")[:10]) if tm else None
    release = (_format_date_label(released, include_year=True) if released else "") or year
    if release:
        meta_parts.append(release)
    if meta_parts:
        b.spacer()
        b.line(" · ".join(meta_parts))


    # 4. О чём — короткое описание (2-4 строки).
    if tm and tm.get("overview"):
        b.spacer()
        b.line(clip(tm["overview"], limit=260))

    # 5. Почему именно мне — персональная причина.
    reason = _reason_line(item, tm)
    if reason:
        b.spacer()
        b.line(reason)

    # 6. Статус и объём сериала («Завершено · 6 сезонов • 63 серии») — финальная строка курсивом.
    detail = _detail_line(tm)
    if detail:
        b.spacer()
        b.italic(detail)
        b.newline()

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
      Recommendations объясняется через любимый фильм, а Similar — только через
      подтверждённые общие жанры, без сильного утверждения «похоже на».
    - иначе — old-path LLM-хук (item["hook"]) как есть.
    """
    tm = tm or {}
    reason = tm.get("reason")
    if reason:
        kind = reason.get("kind")
        label = _clip_title(reason.get("label", ""))
        if kind == "genre":
            # Подборку по жанру в карточке не подписываем строкой «Подборка в жанре…».
            return ""
        if kind == "mood":
            return f"Подборка для настроения «{label}»"
    # «Подходит по жанрам: …» не пишем: жанры уже в строке рейтинга.
    hook = (item.get("hook") or "").strip()
    return hook if hook else ""


def _detail_line(tm):
    """Строка статуса и объёма для сериала. Для фильма длительность и страна
    не показываются, поэтому строка пустая. Статус сериала — ровно один вариант."""
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
            vol = f"{seasons} {ru_plural(seasons, 'сезон', 'сезона', 'сезонов')}"
            if eps:
                vol += f" • {eps} {ru_plural(eps, 'серия', 'серии', 'серий')}"
            parts.append(vol)
        return " · ".join(parts)
    if kind == "movie":
        # Длительность и страна в карточке фильма не показываются.
        return ""
    return ""


def _fmt_date(iso):
    """'2024-10-18' → '18 октября'."""
    try:
        y, m, d = iso.split("-")
        return f"{int(d)} {_MONTHS_RU[int(m)]}"
    except (ValueError, IndexError):
        return iso


def artist_card(data):
    """Карточка артиста: «🇨🇦 **Имя**» (имя — ссылка на YouTube Music), «Жанр · Страна · Годы», образное
    описание, «С чего начать:» — три трека с короткими пометками, в конце курсивом проверяемый факт.
    Нет части — нет строки; «почему тебе зайдёт» в карточке нет."""
    artist = str(data.get("artist") or "").strip()
    flag, _space, country = str(data.get("country_label") or "").strip().partition(" ")
    b = MessageBuilder()
    b.text_line(f"{flag or '🎸'} ")
    b.bold_link(artist, youtube_music_link(artist)) if artist else b.bold(artist)
    b.newline()
    style = " ".join(str(data.get("style") or data.get("genre_label") or "").split())
    meta = [style[:1].upper() + style[1:], country.strip(), str(data.get("years") or "").strip()]
    if any(meta):
        b.line(" · ".join(value for value in meta if value))
    if str(data.get("desc") or "").strip():
        b.spacer()
        b.line(" ".join(str(data["desc"]).split()))
    tracks = data.get("tracks") or []
    if isinstance(tracks, list) and tracks:
        b.spacer()
        b.bold("С чего начать:")
        b.newline()
        for track in tracks[:3]:
            if isinstance(track, dict):
                title = str(track.get("title") or track.get("track") or track.get("name") or "").strip()
                note = str(track.get("note") or "").strip()
                url = str(track.get("url") or "").strip()
            else:
                title, separator, note = str(track or "").partition(" - ")
                title, note, url = title.strip(), note.strip() if separator else "", ""
            if not title:
                continue
            b.text_line("• ")
            if url:
                b.link(title, url)
            else:
                b.text_line(title)
            if re.match(r"[А-ЯЁ][а-яё]", note):  # пометка со строчной: «— солнечный джэнгл-поп»
                note = note[0].lower() + note[1:]
            if note:
                b.text_line(f" — {note}")
            b.newline()
    fact = " ".join(str(data.get("fact") or "").split()).strip()
    if fact:
        b.spacer()
        b.italic(fact if fact[-1] in ".!?…" else fact + ".")  # финальная строка — курсивом и с точкой
        b.newline()
    return b.build_stripped()


def favorite_artist_added_card(artist, style_labels, data=None):
    """Короткая честная карточка после ручного добавления артиста.

    Для вручную введённого имени мы не угадываем жанр исполнителя. Вместо этого
    показываем только выбранные пользователем стили, которые действительно будут
    использоваться в следующих рекомендациях.
    """
    artist = str(artist or "Артист").strip() or "Артист"
    data = data if isinstance(data, dict) else {}
    b = MessageBuilder()
    b.line("✅ Добавлен в «🎚️ Мои артисты»")
    b.spacer()
    b.text_line("🎸 ")
    b.bold(artist)
    description = clip(str(data.get("desc") or ""), limit=170)
    if description:
        b.spacer()
        b.line(description)
    labels = [str(label).strip() for label in style_labels or [] if str(label).strip()]
    if labels:
        b.spacer()
        b.line(f"Учту в подборках: {' · '.join(labels[:3])}")
    else:
        b.spacer()
        b.line("Учту в следующих подборках.")
    return b.build_stripped()


def favorite_artists_added_card(artists, style_labels):
    """Одна компактная карточка, когда пользователь добавил несколько артистов."""
    artists = [str(artist or "").strip() for artist in artists or [] if str(artist or "").strip()]
    b = MessageBuilder()
    b.line("✅ Добавлены в «🎚️ Мои артисты»")
    for artist in artists[:8]:
        b.newline()
        b.text_line("🎸 ")
        b.bold(artist)
    labels = [str(label).strip() for label in style_labels or [] if str(label).strip()]
    if labels:
        b.spacer()
        b.line(f"Учту в подборках: {' · '.join(labels[:3])}")
    return b.build_stripped()


def favorite_movie_added_card(title, tm=None):
    """Подтверждение ручного добавления фильма с только проверенными метаданными."""
    title = str(title or "Фильм").strip() or "Фильм"
    tm = tm if isinstance(tm, dict) else {}
    shown_title = str(tm.get("name") or title).strip() or title
    year = str(tm.get("year") or "").strip()
    genres = str(tm.get("genres") or "").strip()
    kind = str(tm.get("kind") or "").strip()
    type_label = "Сериал" if kind == "tv" else ("Фильм" if kind == "movie" else "")
    b = MessageBuilder()
    b.line("✅ Добавлен в «🎚️ Моё кино»")
    b.spacer()
    b.text_line("🎬 ")
    b.bold(shown_title)
    details = [part for part in (year, type_label, genres) if part]
    if details:
        b.text_line(" · " + " · ".join(details))
    b.spacer()
    b.line("Учту в следующих подборках.")
    return b.build_stripped()


def favorite_movies_added_card(titles):
    """Подтверждение пакетного добавления без серии лишних запросов к TMDb."""
    titles = [str(title or "").strip() for title in titles or [] if str(title or "").strip()]
    b = MessageBuilder()
    b.line("✅ Добавлены в «🎚️ Моё кино»")
    for title in titles[:8]:
        b.newline()
        b.text_line("🎬 ")
        b.bold(title)
    b.spacer()
    b.line("Учту в следующих подборках.")
    return b.build_stripped()


def movie_premieres_screen(country, date_range, items):
    """Подпись карточки премьеры кино."""
    b = MessageBuilder()
    b.text_line("🎟️ ")
    b.bold(f"Премьеры фильмов · {country}")
    b.newline()
    b.spacer()
    b.line(date_range)
    if not items:
        b.spacer()
        b.line("Витрина появится после ближайшего ночного обновления.")
        return b.build_stripped()
    for item in list(items or [])[:5]:
        title = str(_item_value(item, "title", "") or "").strip()
        if not title:
            continue
        card = MessageBuilder()
        trailer_url = str(_item_value(item, "trailer_url", "") or "").strip()
        if trailer_url:
            card.link(f"«{title}»", trailer_url)
        else:
            card.bold(f"«{title}»")
        meta = []
        genres = _movie_genres_for_line(item)
        if genres:
            meta.append(genres.replace(", ", " · "))
        premiere_date = _movie_premiere_date(item)
        if premiere_date:
            meta.append(premiere_date)
        if meta:
            card.text_line(f" · {' · '.join(meta)}")
        card.newline()
        overview = _movie_premiere_summary(_item_value(item, "overview", ""), limit=90)
        if overview:
            if overview[-1] not in ".!?…":
                overview += "."
            card.line(overview)
        card = card.build_stripped()
        # Подпись альбома ограничена 1024 UTF-16 единицами.
        # Карточку добавляем целиком, без обрыва последней строки.
        if u16_len(b.text) + 2 + u16_len(card.text) > 1024:
            break
        b.embed(card)
    return b.build_stripped()


def _movie_premiere_summary(value, limit=None):
    """Первое законченное предложение: короче исходной завязки, но без обрыва."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    summary = sentences[0].strip()
    return clip(summary, limit=limit) if limit else summary


def _movie_premiere_date(item):
    raw = str(
        _item_value(item, "date", "")
        or _item_value(item, "release_date", "")
        or ""
    ).strip()
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return str(_item_value(item, "date_label", "") or "").strip()
    return _format_date_label(value, include_year=True)


def series_premiere_screen(item):
    b = MessageBuilder()
    b.title("📺 Премьеры сериалов")
    if not item:
        b.line("Пока нет премьер с рейтингом выше 7.")
        return b.build_stripped()
    title = str(_item_value(item, "name", "") or "Сериал").strip()
    url = trailer_link(item)  # трейлер на YouTube, не страница TMDb
    if url:
        b.link(title, url)
    else:
        b.bold(title)
    b.newline()
    meta = []
    season = int(_item_value(item, "season_number", 0) or 0)
    if season:
        meta.append(f"{season} сезон")
    else:
        meta.append("Новый сериал")
    if _item_value(item, "favorite", False):
        meta.append("из Моего кино")
    release_date = _movie_premiere_date(item)
    if release_date:
        meta.append(release_date)
    genres = _movie_genres_for_line(item)
    if genres:
        meta.append(genres.replace(", ", " · "))
    b.line(" · ".join(meta))
    overview = _movie_premiere_summary(_item_value(item, "overview", ""), limit=180)
    if overview:
        b.spacer()
        b.line(overview if overview[-1] in ".!?…" else overview + ".")
    return b.build_stripped()


def combined_premiere_screen(item):
    """Одна общая карточка новой премьеры фильма или сериала."""
    b = MessageBuilder()
    b.section("✨ Новая премьера")
    if not item:
        b.spacer()
        b.line("Пока не удалось подтвердить ближайшие премьеры.")
        return b.build_stripped()
    kind = str(_item_value(item, "premiere_kind", "movie"))
    title = str(_item_value(item, "name", "") or _item_value(item, "title", "") or "Премьера").strip()
    b.spacer()
    b.bold(title)
    b.newline()
    meta = ["Сериал" if kind == "series" else "Фильм"]
    release_date = _movie_premiere_date(item)
    if release_date:
        meta.append(release_date)
    genres = _movie_genres_for_line(item)
    if genres:
        meta.append(genres.replace(", ", " · "))
    b.line(" · ".join(meta))
    overview = _movie_premiere_summary(_item_value(item, "overview", ""), limit=220)
    if overview:
        b.spacer()
        b.line(overview if overview[-1] in ".!?…" else overview + ".")
    return b.build_stripped()


def music_activity_screen(task):
    """Небольшая карточка одного трека под выбранное занятие."""
    task = task or {}
    b = MessageBuilder()
    b.text_line("🎧 ")
    b.bold(str(task.get("title") or "Музыка под занятие"))
    b.newline()
    b.spacer()
    b.bold(f"{str(task.get('track') or '')} — {str(task.get('artist') or '')}".strip(" —"))
    if task.get("tag"):
        b.spacer()
        b.line(str(task["tag"]))
    if task.get("note"):
        b.spacer()
        b.line(str(task["note"]))
    return b.build_stripped()


def _concert_context_text(event) -> str:
    return str(event.get("context") if isinstance(event, dict) else event or "").strip()


def concerts_list(place_label, events, empty_hint=""):
    """Список концертов твоих артистов -> MessageBuilder. Каждое событие - мини-блок:
    кликабельное имя артиста, место, цена от и дата."""
    events = list(events or [])
    b = MessageBuilder()
    b.text_line(f"{ui_label('concerts', '')} ")
    b.bold(place_label)
    b.newline()
    if not events:
        b.spacer()
        b.line(empty_hint or "Сейчас ничего не нашёл. Попробуй другую страну.")
    else:
        for ev in events:
            b.spacer()
            artist = str(ev.get("artist") or "").strip()
            url = str(ev.get("url") or "").strip()
            if url:
                b.link(artist, url)
            else:
                b.bold(artist)
            b.newline()
            context = _concert_context_text(ev)
            if context:
                b.line(context)
            date_place = " · ".join(x for x in (ev.get("date"), ev.get("place")) if x)
            if ev.get("flag"):
                date_place = f"{date_place} {ev['flag']}".strip()
            if date_place:
                b.line(f"Концерт: {date_place}")
            if ev.get("price"):
                b.line(ev["price"])
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


_ABOUT_MAX = 160


_HUB_ABOUT_MAX = 260  # описание в хабе — до двух предложений


def _hub_about(text) -> str:
    """До двух первых предложений описания, только русский текст; иначе ""."""
    text = " ".join(re.sub(r"<[^>]+>", " ", str(text or "")).split())
    if not re.search(r"[а-яё]", text, re.I):
        return ""
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    about = sentences[0]
    if len(sentences) > 1 and len(about) + 1 + len(sentences[1]) <= _HUB_ABOUT_MAX:
        about = f"{about} {sentences[1]}"
    if len(about) > _HUB_ABOUT_MAX:
        about = about[:_HUB_ABOUT_MAX].rsplit(" ", 1)[0].rstrip(",;:—- ") + "…"
    return about


def album_meta(album) -> list:
    """«Blue Rev» и дата выхода альбома: в этом году — «9 октября», раньше — год; нет альбома — []."""
    title = " ".join(str((album or {}).get("title") or "").split())
    released = str((album or {}).get("date") or "")
    if not title or not released[:4].isdigit():
        return []
    day = _parse_event_date(released[:10]) if len(released) >= 10 else None
    current = day and day.year == date.today().year
    return [f"«{title}»", _format_date_label(day) if current else released[:4]]


def _event_date_label(value) -> str:
    """Дата события в хабе — как у концертов: «16 октября», год только если не текущий."""
    day = _parse_event_date(str(value or "")[:10])
    return _format_date_label(day, include_year=day.year != date.today().year) if day else ""


def _weekly_concert_rows(b: MessageBuilder, title, items):
    """«• Stevie Wonder — 28 октября, Ziggo Dome. Икона соула, пропускать нельзя.»

    Один артист — одна строка; имя — ссылка на билеты, фраза об артисте — если есть.
    """
    if not items:
        return
    b.spacer()
    b.bold(title)
    b.newline()
    for item in items:
        name = item.get("artist") or item.get("title")
        place = item.get("venue") or item.get("city") or ""
        b.text_line("• ")
        if item.get("url"):
            b.link(name, item["url"])
        else:
            b.text_line(name)
        line = " — " + ", ".join(part for part in (_event_date_label(item.get("date")), place) if part) + "."
        about = str(item.get("about") or "").strip()
        b.text_line(line + (f" {about}" if about else ""))
        b.newline()


def weekly_concerts_card(mine, popular, title="🎫 Концерты недели") -> MessageSpec:
    """Рассылка «Концерты недели» и экран «Концерты» Досуга: любимые артисты, затем популярное рядом."""
    b = MessageBuilder()
    b.title(title)
    _weekly_concert_rows(b, "Твои артисты:", mine)
    _weekly_concert_rows(b, "Популярное рядом:", popular)
    return b.build_stripped()


def library_stats_line(movies, artists) -> str:
    movies, artists = int(movies or 0), int(artists or 0)
    return (f"Твоя библиотека · {movies} {ru_plural(movies, 'фильм', 'фильма', 'фильмов')}"
            f" · {artists} {ru_plural(artists, 'артист', 'артиста', 'артистов')}")


def plain_from_html(text):
    return re.sub(r"<[^>]+>", "", text or "")


def _column_kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data)] for text, data in rows])


def runtime_label(minutes, *, episode=False) -> str:
    """«1 ч 52 мин», «серия 45 мин»; 0 — ""."""
    minutes = int(minutes or 0)
    if minutes <= 0:
        return ""
    hours, rest = divmod(minutes, 60)
    label = " ".join(part for part in (f"{hours} ч" if hours else "", f"{rest} мин" if rest else "") if part)
    return f"серия {label}" if episode else label


_TODAY_KIND = {"movie": "фильм", "tv": "сериал", "music": "музыкальный альбом"}


def _today_name(b: MessageBuilder, pick, meta=True) -> None:
    """«**Имя**(ссылка) · метаданные» без перевода строки."""
    name = " ".join(str(pick.get("name") or "").split())
    url = str(pick.get("url") or "").strip()
    b.bold_link(name, url) if url else b.bold(name)
    values = [" ".join(str(value).split()) for value in pick.get("meta") or () if str(value or "").strip()]
    if meta and values:
        b.text_line(" · " + " · ".join(values))


def leisure_today_screen(picks, *, city="", outing=None, library=None, reply_markup=None) -> MessageSpec:
    """«🍿 Досуг на сегодня · Alkmaar»: главная карточка (первая, обычно фильм) без подписи — имя, метаданные,
    длительность и описание одним абзацем через «•»; остальные — «- Имя · сериал · …», событие рядом (если есть),
    статистика библиотеки курсивом. picks — готовые данные (leisure_hub.today_data)."""
    b = MessageBuilder()
    b.title("🍿 Досуг на сегодня" + (f" · {str(city).strip()}" if str(city or "").strip() else ""))
    if not picks:
        b.line("Собираю варианты на сегодня — загляни чуть позже или открой «Фильмы» и «Сериалы» ниже.")
    else:
        first = picks[0]
        _today_name(b, {**first, "meta": [*first.get("meta", ()), first.get("runtime")]})
        about = _hub_about(first.get("about"))
        if about:
            b.text_line(f" • {about}")
        b.newline()
        rest = [pick for pick in picks if pick is not first]
        if rest:
            b.spacer()
            b.bold("Если не хочется кино:" if first["kind"] == "movie" else "Другие варианты:")
            b.newline()
            for pick in rest:  # «- Колесо Времени · сериал · 2021 · Prime Video · фантастика · USA»
                b.text_line("- ")
                _today_name(b, {**pick, "meta": [_TODAY_KIND.get(pick["kind"], ""), *pick.get("meta", ())]})
                b.newline()
    if outing:
        b.spacer()
        b.bold("Если захочется выйти:")
        b.newline()
        _today_name(b, {"name": outing.get("name"), "url": outing.get("url")}, meta=False)
        tail = ", ".join(part for part in (outing.get("when"), outing.get("place")) if str(part or "").strip())
        if tail:
            b.text_line(f" — {tail}")
        b.newline()
    if library:
        b.spacer()
        b.italic(library_stats_line(library.get("movies"), library.get("artists")))
        b.newline()
    return b.build_stripped(reply_markup=reply_markup)


_TRIP_DURING_MAX, _TRIP_HOLIDAYS_MAX, _TRIP_CONCERTS_MAX = 6, 2, 2  # «Во время отпуска» — не длиннее 6 строк


def _trip_titles(b: MessageBuilder, label, items) -> None:
    """«Кино страны:» — по строке на фильм или сериал: название жирной ссылкой на трейлер · год · жанры."""
    if not items:
        return
    b.spacer()
    b.bold(f"{label}:")
    b.newline()
    for item in items:
        b.text_line("- ")
        b.bold_link(item["title"], trailer_link(item))
        meta = [str(item.get("year") or ""), str(item.get("genres") or "").replace(", ", " · ")]
        b.text_line("".join(f" · {value}" for value in meta if value.strip()))
        b.newline()


def _trip_music(b: MessageBuilder, hits, legends) -> None:
    """«Хиты страны:» — «трек — артист» ссылками на YouTube Music; «Легенды:» строкой «артист — главный трек»."""
    if hits:
        b.spacer()
        b.bold("Хиты страны:")
        b.newline()
        for hit in hits:
            b.text_line("- ")
            b.link(f"{hit['track']} — {hit['artist']}", youtube_music_link(hit["track"], hit["artist"]))
            b.newline()
    if legends:
        if not hits:  # без хитов «Легенды» начинают свой блок; с хитами — сразу под ними
            b.spacer()
        b.label("Легенды")
        b.text_line(" ")
        for index, legend in enumerate(legends):
            b.text_line(" · ") if index else None
            name = f"{legend['artist']} — {legend['track']}" if legend.get("track") else legend["artist"]
            b.link(name, youtube_music_link(legend.get("track") or "", legend["artist"]))
        b.newline()


def trip_leisure_screen(place, data, events=(), concerts=(), flag="") -> MessageSpec:
    """«🇿🇦 Досуг в отпуске · Кейптаун»: кино и сериалы страны (TMDb), хиты местных артистов и легенды, и что идёт
    во время отпуска — праздники, афиша города (концерты, фестивали, выставки), концерты твоих артистов; до 6 строк,
    без эмодзи. Пока кино и музыка страны собираются — строка-подсказка. Премьер, рекомендаций и кнопок разделов нет."""
    b = MessageBuilder()
    b.title(f"{flag or '🍿'} Досуг в отпуске" + (f" · {place}" if place else ""))  # «🇿🇦 Досуг в отпуске · Кейптаун»
    data = data or {}
    if not any(data.get(key) for key in ("films", "series", "hits", "legends")):
        b.line("Собираю кино и музыку страны — загляни чуть позже.")
    _trip_titles(b, "Кино страны", data.get("films") or [])
    _trip_titles(b, "Сериалы страны", data.get("series") or [])
    _trip_music(b, data.get("hits") or [], data.get("legends") or [])
    holidays = [f"{day} — {name}" for day, name in (data.get("holidays") or [])[:_TRIP_HOLIDAYS_MAX]]
    shows = [" — ".join(part for part in (concert.get("artist") or "", ", ".join(part for part in (
        _event_date_label(concert.get("date")), concert.get("venue") or concert.get("city") or "") if part)) if part)
        for concert in list(concerts or ())[:_TRIP_CONCERTS_MAX]]
    afisha = []
    for event in events or ():
        start, end = _event_date_label(event.get("start")), _event_date_label(event.get("end"))
        when = start if not end or end == start else f"{start} – {end}"
        tail = ", ".join(part for part in (when, event.get("place") or "") if part)
        afisha.append(" — ".join(part for part in (event.get("name") or "", tail) if part))
    during = [*holidays, *afisha[:max(0, _TRIP_DURING_MAX - len(holidays) - len(shows))], *shows]
    if during:
        b.spacer()
        b.bold("Во время отпуска:")
        b.newline()
        for line in during:
            b.line(line)
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("#️⃣ Главная",
                                                                                     callback_data="m_menu")]]))


def leisure_hub_kb():
    """«Премьеры» — первой строкой; Фильмы | Сериалы | Музыка — полные карточки; ниже «Концерты»."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Премьеры", callback_data="lz_prem")],  # стандартного цвета
        [InlineKeyboardButton("🎬 Фильм", callback_data="movie_reco"),
         InlineKeyboardButton("📺 Сериал", callback_data="tv_reco"),
         InlineKeyboardButton("🎧 Музыка", callback_data="music_reco")],
        [InlineKeyboardButton("🎫 Концерты", callback_data="lz_concerts")],
        [InlineKeyboardButton("🎚️ Настроить библиотеку", callback_data="lz_lib"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


def no_month_concerts(title="🎫 Ближайшие концерты") -> MessageSpec:
    b = MessageBuilder()
    b.title(title)
    b.line("В ближайшие 30 дней концертов рядом не нашлось.")
    return b.build_stripped()


WEEK_PAGES = ("movie", "series", "music")  # «Премьеры недели»: виды в меню по порядку
_WEEK_TABS = {"movie": ("🎬", "Фильмы"), "series": ("📺", "Сериалы"), "music": ("🎧", "Музыка")}


def youtube_music_link(*parts) -> str:
    """Поиск на YouTube Music: «артист», «артист альбом» или «трек артист»; без текста — ""."""
    query = " ".join(" ".join(str(part).split()) for part in parts if str(part or "").strip())
    return f"https://music.youtube.com/search?q={quote_plus(query)}" if query else ""


def trailer_link(item) -> str:
    """Официальный трейлер на YouTube (`tmdb.trailer_url`: TMDb, иначе поиск YouTube API); не нашёлся — поиск
    «название год official trailer» на YouTube по оригинальному названию. Страницы TMDb пользователю не даём."""
    url = str(_item_value(item, "trailer_url", "") or "").strip()
    if url:
        return url
    name = next((str(_item_value(item, key, "") or "").strip() for key in ("name_en", "original_title", "name", "title")
                 if str(_item_value(item, key, "") or "").strip()), "")
    year = str(_item_value(item, "year", "") or _item_value(item, "release_date", "") or _item_value(item, "date", "") or "")[:4]
    query = " ".join(part for part in (name, year if year.isdigit() else "", "official trailer") if part)
    return f"https://www.youtube.com/results?search_query={quote_plus(query)}" if name else ""


def _week_item(item, kind) -> tuple:
    """(имя, ссылка, метаданные, описание) пункта вкладки — как блок хаба; у альбома сначала исполнитель."""
    if kind == "music":
        return (item.get("artist"), youtube_music_link(item.get("artist"), item.get("title")),
                (f"— «{item.get('title')}»", item.get("genre_label"), item.get("country_label")), item.get("about"))
    if kind == "series":
        season = int(item.get("season_number") or 0)
        return (item.get("name"), trailer_link(item),
                (_movie_genres_for_line(item).replace(", ", " · "), f"{season}-й сезон" if season > 1 else "",
                 item.get("country_label"), item.get("network")), item.get("overview"))
    return (_item_value(item, "title", ""), trailer_link(item),
            (_movie_genres_for_line(item).replace(", ", " · "), item.get("country_label")), _item_value(item, "overview", ""))


def week_items(kind, rows) -> list:
    """Пункты недели в порядке дней без повторов (одно имя и первая метка — один пункт)."""
    seen, items = set(), []
    for _weekday, item in rows:
        name, _url, meta, _about = _week_item(item, kind)
        key = (" ".join(str(name or "").split()), *[str(value) for value in meta if str(value or "").strip()][:1])
        if key[0] and key not in seen:
            seen.add(key)
            items.append(item)
    return items


def premiere_card(feed, index=0) -> MessageSpec:
    """Карточка ленты «Премьеры» (feed — [(вид, пункт)], фильмы, сериалы и музыка вперемешку): «🎬 **Имя**» ссылкой,
    метаданные через «·», описание в одно-два предложения; ← n/N → по кругу по всей ленте, «Назад» — в хаб.
    Картинку (постер, обложку) прикладывает обработчик."""
    b = MessageBuilder()
    if not feed:
        b.title("🆕 Премьеры недели")
        b.line("Премьер на эту неделю пока нет.")
        return b.build_stripped(reply_markup=InlineKeyboardMarkup([nav_row("m_leisure")]))
    index %= len(feed)
    kind, item = feed[index]
    kind = kind if kind in WEEK_PAGES else "movie"
    emoji, _tab = _WEEK_TABS[kind]
    name, url, meta, about = _week_item(item, kind)
    name = " ".join(str(name or "").split())
    b.text_line(f"{emoji} ")
    b.bold_link(name, str(url).strip()) if str(url or "").strip() else b.bold(name)
    values = [" ".join(str(value).split()) for value in meta if str(value or "").strip()]
    if kind == "music" and values:  # «Alvvays — «Blue Rev»» в заголовке, дальше жанр и страна
        b.text_line(f" {values.pop(0)}")
    b.newline()
    if values:
        b.line(" · ".join(values))
    about = _hub_about(item.get("note") or about)  # описание премьеры (AI), без него — начало описания источника
    if about:
        b.spacer()
        b.line(about)
    rows = []
    if len(feed) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"lz_prem_{(index - 1) % len(feed)}"),
            InlineKeyboardButton(f"{index + 1}/{len(feed)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"lz_prem_{(index + 1) % len(feed)}"),
        ])
    rows.append(nav_row("m_leisure"))
    return b.build_stripped(reply_markup=InlineKeyboardMarkup(rows))


def leisure_library_menu() -> MessageSpec:
    b = MessageBuilder()
    b.title("🎚️ Моя библиотека")
    b.line("Любимое кино и артисты — по ним подбираю рекомендации.")
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([*_column_kb((
        ("🎬 Фильмы и сериалы", "movie_favorites"),
        ("🎧 Музыка", "artist_favorites"),
        ("🎫 Концерты", "a_concerts_find"),
    )).inline_keyboard, nav_row("m_leisure")]))


# ---------- «Новинка» в меню «Выбрать жанр» ----------
_NOVELTY_EMPTY = {
    "movie": "Свежих премьер в кино пока нет — загляни позже.",
    "music": "Свежих альбомов в твоих жанрах пока нет — загляни позже.",
}


def novelty_empty(kind) -> MessageSpec:
    b = MessageBuilder()
    b.line(_NOVELTY_EMPTY.get(kind, "Новинок пока нет."))
    return b.build_stripped()


def _novelty_status(kind, item) -> str:
    """Плашка новинки: идёт сейчас или выходит позже, дата — как в хабе."""
    day = _parse_event_date(str(_item_value(item, "date", "") or _item_value(item, "published_date", ""))[:10])
    label = _event_date_label(day.isoformat()) if day else ""
    if kind == "movie":
        # В «Новинку» попадают только вышедшие фильмы; дата премьеры — в строке жанров.
        return "🎬 Уже в кино"
    return f"🎧 Новый альбом · {label}" if label else "🎧 Новый альбом"


def novelty_card(kind, item) -> MessageSpec:
    """Карточка новинки: плашка, название, данные одной строкой, описание после пустой строки."""
    b = MessageBuilder()
    b.bold(_novelty_status(kind, item))
    b.newline()
    b.spacer()
    title = str(_item_value(item, "title", "") or "").strip()
    url = trailer_link(item) if kind == "movie" else youtube_music_link(item.get("artist"), title)
    heading = f"«{title}»" + (f" — {item.get('artist')}" if kind == "music" and item.get("artist") else "")
    if url:
        b.link(heading, url)
    else:
        b.bold(heading)
    b.newline()
    country = str(item.get("country_label") or "")
    if kind == "movie":
        # Премьеры кино — без оценок: у свежих фильмов они ещё не устоялись.
        meta = [_movie_genres_for_line(item).replace(", ", " · "), country,
                _event_date_label(str(_item_value(item, "date", "") or ""))]
        summary = _movie_premiere_summary(_item_value(item, "overview", ""), limit=300)
    else:
        meta = [str(item.get("genre_label") or ""), country, _event_date_label(str(item.get("date") or ""))]
        summary = str(item.get("about") or "")
    meta = [value for value in meta if value.strip()]
    if meta:
        b.line(" · ".join(meta))
    if summary.strip():
        b.spacer()
        b.line(summary.strip())
    return b.build_stripped()
