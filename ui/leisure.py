import html
import re
from datetime import date, datetime
from urllib.parse import quote_plus

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from util import local_today

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
    b.line("✅ Добавлен в «Мои артисты»")
    b.spacer()
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
    b.line("✅ Добавлены в «Мои артисты»")
    for artist in artists[:8]:
        b.newline()
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
    b.line("✅ Добавлен в «Моё кино»")
    b.spacer()
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
    b.line("✅ Добавлены в «Моё кино»")
    for title in titles[:8]:
        b.newline()
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
    current = day and day.year == local_today().year
    return [f"«{title}»", _format_date_label(day) if current else released[:4]]


def _concert_date_label(value) -> str:
    """Дата концерта — всегда с годом («6 декабря 2026»): афиша на год вперёд, даты должны читаться одинаково."""
    day = _parse_event_date(str(value or "")[:10])
    return _format_date_label(day, include_year=True) if day else ""


def _event_date_label(value) -> str:
    """Дата события в хабе — как у концертов: «16 октября», год только если не текущий."""
    day = _parse_event_date(str(value or "")[:10])
    return _format_date_label(day, include_year=day.year != local_today().year) if day else ""


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
        line = " — " + ", ".join(part for part in (_concert_date_label(item.get("date")), place) if part) + "."
        about = str(item.get("about") or "").strip().strip("«»\"“”„'")  # фраза AI в кавычках — без них
        about = re.sub(r"[«»\"“”„]\.$", ".", about).strip()
        b.text_line(line + (f" {_finish_sentence(about)}" if about else ""))
        b.newline()


def _popular_concerts_line(b: MessageBuilder, items):
    """«Популярное рядом:» одной строкой через запятую: «Kehlani (6 декабря), Coldplay (12 января)»; имя — ссылка
    на билеты."""
    if not items:
        return
    b.spacer()
    b.bold("Популярное рядом:")
    b.newline()
    for index, item in enumerate(items):
        if index:
            b.text_line(", ")
        name = item.get("artist") or item.get("title")
        b.link(name, item["url"]) if item.get("url") else b.text_line(name)
        date = _concert_date_label(item.get("date"))
        if date:
            b.text_line(f" ({date})")
    b.newline()


def weekly_concerts_card(mine, popular, title="🎫 Концерты недели") -> MessageSpec:
    """Рассылка «Концерты недели» и экран «Концерты» Досуга: любимые артисты, затем популярное рядом."""
    b = MessageBuilder()
    b.title(title)
    _weekly_concert_rows(b, "Твои артисты:", mine)
    _popular_concerts_line(b, popular)
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




def _today_name(b: MessageBuilder, pick, meta=True) -> None:
    """«**Имя**(ссылка) · метаданные» без перевода строки."""
    name = " ".join(str(pick.get("name") or "").split())
    url = str(pick.get("url") or "").strip()
    b.bold_link(name, url) if url else b.bold(name)
    values = [" ".join(str(value).split()) for value in pick.get("meta") or () if str(value or "").strip()]
    if meta and values:
        b.text_line(" · " + " · ".join(values))


def _finish_sentence(text) -> str:
    return text if text[-1:] in ".!?…" else f"{text}."


_TODAY_SINGLE = (("movie", "Фильм:"), ("tv", "Сериал:"), ("music", "Артист дня:"))
_TODAY_PREMIERE_TYPE = {"movie": "фильм", "series": "сериал", "music": "альбом"}


def today_premiere(card) -> dict:
    """Премьера «Событий» → строка «Премьер недели»: «**Имя** (фильм · приключения · анимация · 🇺🇸 USA · 25 ноября)
    — описание». Жанров — не больше двух; у альбома — «альбом «Название»»."""
    kind = card.get("premiere")
    values = [" ".join(str(value).split()) for value in card.get("meta") or () if str(value or "").strip()]
    label = _TODAY_PREMIERE_TYPE.get(kind, "фильм")
    if kind == "music" and values and values[0].startswith("«"):
        label, values = f"{label} {values[0]}", values[1:]
    meta = [label]
    for value in values:
        meta += value.split(" · ")[:2] if " · " in value else [value]
    return {"kind": "premiere", "name": card.get("name") or "", "url": card.get("url") or "",
            "meta": [*meta, _event_date_label(card.get("date"))], "about": card.get("about") or ""}


def _today_about(b: MessageBuilder, pick) -> None:
    about = _hub_about(pick.get("about"))
    if about:
        b.text_line(f" — {_finish_sentence(about)}")  # регистр не трогаем: описание часто начинается с имени
    b.newline()


def _today_bullets(b: MessageBuilder, label, picks, *, brackets=False) -> None:
    """«**Премьеры недели:**» и ниже «• **Имя** (метаданные) — описание» (статьи — «· метаданные»)."""
    b.bold(label)
    b.newline()
    for pick in picks:
        b.text_line("• ")
        if brackets:
            _today_name(b, {**pick, "meta": ()})
            values = [" ".join(str(value).split()) for value in pick.get("meta") or () if str(value or "").strip()]
            if values:
                b.text_line(f" ({' · '.join(values)})")
        else:
            _today_name(b, pick)
        _today_about(b, pick)


def leisure_today_screen(lineup, *, city="", library=None, reply_markup=None) -> MessageSpec:
    """«🍿 Досуг на сегодня · Alkmaar»: «Премьеры недели:» (фильм, альбом, сериал списком), «Фильм:», «Сериал:»,
    «Артист дня:» — строкой сразу после подписи, «Статьи дня:» — три списком; подписи жирные, без значков. Внизу
    курсивом статистика библиотеки."""
    b = MessageBuilder()
    b.title("🍿 Досуг на сегодня" + (f" · {str(city).strip()}" if str(city or "").strip() else ""))
    lineup = lineup or {}
    blocks = []
    if lineup.get("premieres"):
        blocks.append(lambda: _today_bullets(b, "Премьеры недели:", lineup["premieres"], brackets=True))
    for kind, label in _TODAY_SINGLE:
        if lineup.get(kind):
            blocks.append(lambda pick=lineup[kind], label=label: _today_single(b, label, pick))
    if lineup.get("reads"):
        blocks.append(lambda: _today_bullets(b, "Статьи дня:", lineup["reads"]))
    if not blocks:
        b.line("Собираю премьеры, фильмы и статьи — загляни чуть позже или открой разделы ниже.")
    for index, block in enumerate(blocks):
        if index:
            b.spacer()
        block()
    if library:
        b.spacer()
        b.italic(library_stats_line(library.get("movies"), library.get("artists")))
        b.newline()
    return b.build_stripped(reply_markup=reply_markup)


def _today_single(b: MessageBuilder, label, pick) -> None:
    """«**Фильм:** Восставший · 2026 · драма · DE · 1 ч 52 мин — описание» — одной строкой."""
    b.bold(label)
    b.text_line(" ")
    name = " ".join(str(pick.get("name") or "").split())
    url = str(pick.get("url") or "").strip()
    b.link(name, url) if url else b.text_line(name)
    values = [" ".join(str(value).split()) for value in (*(pick.get("meta") or ()), pick.get("runtime"))
              if str(value or "").strip()]
    if values:
        b.text_line(" · " + " · ".join(values))
    _today_about(b, pick)


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
    """«Хиты страны:» — «трек — артист», «Легенды:» — «артист — главный трек»: списками, как кино и сериалы, по
    строке на пункт со ссылкой на YouTube Music."""
    if hits:
        b.spacer()
        b.bold("Хиты страны:")
        b.newline()
        for hit in hits:
            b.text_line("• ")
            b.link(f"{hit['track']} — {hit['artist']}", youtube_music_link(hit["track"], hit["artist"]))
            b.newline()
    if legends:
        b.spacer()
        b.bold("Легенды:")
        b.newline()
        for legend in legends:
            b.text_line("• ")
            name = f"{legend['artist']} — {legend['track']}" if legend.get("track") else legend["artist"]
            b.link(name, youtube_music_link(legend.get("track") or "", legend["artist"]))
            b.newline()


def trip_leisure_screen(place, data, events=(), concerts=(), flag="", footer="", country=(),
                        about=(), useful=(), activity=None) -> MessageSpec:
    """«🇿🇦 Досуг в отпуске · ЮАР»: «Коротко о стране» и «Полезно знать» списком, хиты местных артистов и легенды, и что идёт
    во время отпуска — праздники, афиша города (концерты, фестивали, выставки), концерты твоих артистов; до 6 строк,
    без эмодзи. Пока кино и музыка страны собираются — строка-подсказка; внизу курсивом footer: твои артисты из этой
    страны или факт дня о её культуре (библиотека целиком здесь не пишется). Премьер, рекомендаций и кнопок разделов
    нет."""
    b = MessageBuilder()
    b.title(f"{flag or '🍿'} Досуг в отпуске" + (f" · {place}" if place else ""))  # «🇿🇦 Досуг в отпуске · ЮАР»
    activity = activity or {}
    if not activity.get("name"):  # список мест ещё собирается — честно, а не пустой экран с одной подписью
        b.line("Подбираю развлечения на поездку — загляни чуть позже.")
        b.spacer()
    if activity.get("name"):  # одно развлечение на день: место — ссылка на Google Maps · город, ниже описание
        name, city = str(activity["name"]).strip(), str(activity.get("city") or "").strip()
        # Поиск на карте — по оригинальному названию латиницей, если есть: так Google Maps точнее находит место.
        query = ", ".join(part for part in (str(activity.get("name_en") or "").strip() or name, city, place) if part)
        if city:  # как в погодном предупреждении: «Йоханнесбург:», ниже «• Место — описание»
            b.bold(f"{city}:")
            b.newline()
        about = str(activity.get("about") or "").strip()
        if about.casefold().startswith(name.casefold()):  # «Сад Уолтера Сисулу известен…» — без повтора названия
            about = about[len(name):].lstrip(" :—-,")
        about = about[:1].lower() + about[1:] if about else ""
        b.text_line("• ")
        b.bold_link(name, "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query))
        b.line(f" — {about}" if about else "")
        b.spacer()
    data = data or {}
    _trip_music(b, data.get("hits") or [], data.get("legends") or [])
    # Блока «Во время отпуска» нет: праздник — в подписи внизу, ближайшие события и праздники — рассылкой
    # накануне и в «Моём дне» в их день.
    if str(footer or "").strip():  # «Твои артисты отсюда: …» или факт дня о культуре страны — курсивом
        b.spacer()
        b.italic(_finish_sentence(str(footer).strip()))
        b.newline()
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 О стране", callback_data="lz_country")],  # справка о стране — отдельным экраном
        [InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ]))


def trip_country_screen(place, about=(), useful="", flag="") -> MessageSpec:
    """«🇿🇦 О стране · ЮАР»: сведения о стране списком, без заголовка; внизу курсивом — один пункт «полезно знать»
    на день (экстренный номер, полиция, скорая, розетки — по кругу); ниже «Назад» (к Досугу) | «Главная»."""
    b = MessageBuilder()
    b.title(f"{flag or '🌍'} О стране" + (f" · {place}" if place else ""))
    for line in about or ():
        b.line(f"• {line}")
    useful = str(useful or "").strip()
    if useful:
        if about:
            b.spacer()
        b.italic(useful)
        b.newline()
    if not about and not useful:
        b.line("Собираю сведения о стране — загляни чуть позже.")
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([nav_row("m_leisure")]))


def reads_card(items, index=0) -> MessageSpec:
    """«📰 Статьи» лентой, как «Премьеры»: «📰 **Заголовок**» ссылкой на статью, «Quanta · 21 ноября · EN · 8 мин»
    (дата публикации, год — если не текущий), краткое содержание по-русски;
    ← n/N → по кругу по набору дня (lz_reads_<n>), «Назад» — в хаб. Картинку-превью прикладывает обработчик."""
    b = MessageBuilder()
    items = [item for item in items or () if str(item.get("title") or "").strip()
             and str(item.get("link") or "").startswith("http")]
    if not items:
        b.title("📰 Статьи")
        b.line("Статьи на сегодня ещё собираются — загляни чуть позже.")
        return b.build_stripped(reply_markup=InlineKeyboardMarkup([nav_row("m_leisure")]))
    index %= len(items)
    item = items[index]
    b.text_line("📰 ")
    b.bold_link(" ".join(str(item["title"]).split()), str(item["link"]))
    b.newline()
    minutes = int(item.get("minutes") or 0)
    meta = [str(item.get("source") or "").strip(), _event_date_label(item.get("published")),
            str(item.get("lang") or "").strip(), f"{minutes} мин" if minutes else ""]
    if any(meta):
        b.line(" · ".join(value for value in meta if value))
    about = str(item.get("about") or "").strip()
    if about:
        b.spacer()
        b.line(_finish_sentence(about))
    # «Пропустить» — красная, первой строкой: статья уходит из ленты, на её месте — следующая.
    rows = [[InlineKeyboardButton("Пропустить", callback_data=f"lz_rdone_{index}")]]
    if len(items) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"lz_reads_{(index - 1) % len(items)}"),
            InlineKeyboardButton(f"{index + 1}/{len(items)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"lz_reads_{(index + 1) % len(items)}"),
        ])
    rows.append(nav_row("m_leisure"))
    return b.build_stripped(reply_markup=InlineKeyboardMarkup(rows))


def leisure_hub_kb():
    """Широкая «События» сверху (концерты, фестивали, выставки, праздники, город и премьеры одной лентой), ниже
    Фильм | Сериал | Музыка в три столбца и широкая «Статьи» (старые «Премьеры / Концерты / Праздники» работают)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Премьеры", callback_data="lz_prem")],  # события — в «Моём дне» и «Плане на завтра»
        [InlineKeyboardButton("🎬 Фильм", callback_data="movie_reco"),
         InlineKeyboardButton("📺 Сериал", callback_data="tv_reco"),
         InlineKeyboardButton("🎧 Музыка", callback_data="music_reco")],
        [InlineKeyboardButton("📰 Статьи", callback_data="lz_reads")],
        [InlineKeyboardButton("🎚️ Настроить библиотеку", callback_data="lz_lib"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


_EVENT_KIND = {"concert": ("🎫", "Концерт"), "festival": ("🎪", "Фестиваль"), "exhibition": ("🖼️", "Выставка"),
               "holiday": ("🎉", "Праздник"), "city": ("🏙️", "Городское событие"), "read": ("📰", "Статья")}
_PREMIERE_EMOJI = {"movie": "🎬", "series": "📺", "music": "🎧"}


def _event_span(start, end) -> str:
    """«28 ноября», «12–14 декабря», «27 ноября – 22 декабря»; год — если не текущий."""
    first, last = _parse_event_date(str(start or "")[:10]), _parse_event_date(str(end or start or "")[:10])
    if not first:
        return ""
    year = first.year != local_today().year
    if not last or last <= first:
        return _format_date_label(first, include_year=year)
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day}–{_format_date_label(last, include_year=year)}"
    return f"{_format_date_label(first, include_year=year)} – {_format_date_label(last, include_year=year)}"


def _event_meta(card) -> tuple:
    """(эмодзи, вид, метаданные) карточки «Событий»: «Концерт», [«4 декабря 2026», «Ziggo Dome», «твой артист»]."""
    if card["kind"] == "premiere":
        kind = card.get("premiere")
        return (_PREMIERE_EMOJI.get(kind, "🎬"), f"Премьера · {_PREMIERE_TYPE.get(kind, 'Фильм').lower()}",
                [*card.get("meta", ()), _event_date_label(card.get("date"))])
    emoji, label = _EVENT_KIND.get(card["kind"], _EVENT_KIND["city"])
    if card["kind"] == "read":  # «Статья · 21 ноября · Quanta · EN · 8 мин»
        return emoji, label, [_event_date_label(card.get("date")), *card.get("meta", ())]
    date = (_concert_date_label(card.get("date")) if card["kind"] == "concert"
            else _event_span(card.get("date"), card.get("end")))
    return emoji, label, [date, card.get("place") or "", "бесплатно" if card.get("free") else "",
                          "твой артист" if card.get("mine") else ""]


_PICK_LABEL = {"concert": "Ближайший концерт:", "premiere": "Премьера:"}


def event_pick(card) -> dict:
    """Карточка «Событий» → строка «Досуга на сегодня»: подпись («Ближайший концерт:», «Фестиваль:», «Премьера:»),
    «• **Имя** · метаданные — описание»."""
    _emoji, label, meta = _event_meta(card)
    if card["kind"] == "premiere":
        meta = [_PREMIERE_TYPE.get(card.get("premiere"), "Фильм"), *meta]
    return {"kind": card["kind"], "label": _PICK_LABEL.get(card["kind"], f"{label}:"), "name": card.get("name") or "",
            "url": card.get("url") or "", "meta": meta, "about": card.get("about") or ""}


def event_card(cards, index=0) -> MessageSpec:
    """Карточка журнала недели «🎟️ События»: «🎫 **Muse**» ссылкой, «Концерт · 4 декабря 2026 · Ziggo Dome,
    Amsterdam · твой артист» (дата — всегда), описание; ← n/N → по кругу (lz_ev_<n>), «Назад» — в хаб. Картинку
    прикладывает обработчик."""
    b = MessageBuilder()
    if not cards:
        b.title("🎟️ События")
        b.line("На этой неделе событий пока нет — загляни чуть позже.")
        return b.build_stripped(reply_markup=InlineKeyboardMarkup([nav_row("m_leisure")]))
    index %= len(cards)
    card = cards[index]
    emoji, label, meta = _event_meta(card)
    meta = [label, *meta]
    name = " ".join(str(card.get("name") or "").split())
    url = str(card.get("url") or "")
    b.text_line(f"{emoji} ")
    b.bold_link(name, url) if url.startswith("http") else b.bold(name)
    b.newline()
    values = [" ".join(str(value).split()) for value in meta if str(value or "").strip()]
    if values:
        b.line(" · ".join(values))
    about = _hub_about(card.get("about"))
    if about:
        b.spacer()
        b.line(_finish_sentence(about))
    rows = []
    if len(cards) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"lz_ev_{(index - 1) % len(cards)}"),
            InlineKeyboardButton(f"{index + 1}/{len(cards)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"lz_ev_{(index + 1) % len(cards)}"),
        ])
    rows.append(nav_row("m_leisure"))
    return b.build_stripped(reply_markup=InlineKeyboardMarkup(rows))


def concerts_eve(concerts, day=None) -> MessageSpec:
    """Рассылка накануне: «🎫 Завтра концерт · среда, 4 ноября», на концерт — имя жирной ссылкой на билеты, фраза об
    артисте и строки «**Начало:** 20:00 / Где / Жанр / Билеты / Тур» — подпись жирным, как во всех уведомлениях
    (только то, что известно)."""
    b = MessageBuilder()
    when = ""
    if day is not None:
        weekday = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")[day.weekday()]
        when = f"{weekday}, {_format_date_label(day, include_year=False)}"
    b.title(" · ".join(part for part in ("🎫 Завтра концерт" if len(concerts) == 1 else "🎫 Завтра концерты", when)
                       if part))
    for number, concert in enumerate(concerts):
        if number:
            b.spacer()
        name, url = str(concert.get("artist") or concert.get("title") or "").strip(), str(concert.get("url") or "")
        b.bold_link(name, url) if url.startswith("http") else b.bold(name)
        about = str(concert.get("about") or "").strip()
        b.text_line(f" — {about}" if about else "")
        b.newline()
        place = ", ".join(part for part in (concert.get("venue"), concert.get("address"), concert.get("city"))
                          if str(part or "").strip())
        title = str(concert.get("title") or "").strip()
        rows = [
            ("Начало", concert.get("time")),
            ("Где", place),
            ("Жанр", str(concert.get("genre") or "").lower()),
            ("Билеты", concert.get("price")),
            ("Тур", title if title and title.casefold() != name.casefold() else ""),
        ]
        for label, value in rows:
            if str(value or "").strip():
                b.labeled_line(label, str(value).strip(), lowercase=False)
    return b.build_stripped()


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


_PREMIERE_TYPE = {"movie": "Фильм", "series": "Сериал", "music": "Музыкальный альбом"}
_MONTHS_NOMINATIVE = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
                      "ноябрь", "декабрь")
_PREMIERE_DATE_KEYS = {"movie": ("date", "release_date"), "series": ("release_date", "air_date", "date"),
                       "music": ("date", "release_date")}
_PREMIERE_DATE_PREFIX = {"movie": "в кино с ", "series": "с "}


def premiere_date(kind, item) -> str:
    """Дата премьеры — всегда: у фильма — прокат в стране пользователя («в кино с 28 ноября 2026»), у сериала —
    старт («с 3 декабря 2026»), у альбома — день выхода; дня нет — месяц и год («декабрь 2026»), только год — год;
    совсем нет — «дата пока неизвестна»."""
    raw = next((str(_item_value(item, key, "") or "").strip() for key in _PREMIERE_DATE_KEYS.get(kind, ("date",))
                if str(_item_value(item, key, "") or "").strip()), "")
    day = _parse_event_date(raw[:10]) if len(raw) >= 10 else None
    prefix = _PREMIERE_DATE_PREFIX.get(kind, "")
    if day:
        return prefix + _format_date_label(day, include_year=True)
    if re.match(r"\d{4}-(0[1-9]|1[0-2])(?!\d)", raw):  # только месяц: «с декабря 2026» / «декабрь 2026»
        from util import _MONTHS
        month = int(raw[5:7]) - 1
        return f"{prefix}{_MONTHS[month]} {raw[:4]}" if prefix else f"{_MONTHS_NOMINATIVE[month]} {raw[:4]}"
    if re.match(r"\d{4}(?!\d)", raw):  # только год
        return f"{prefix.replace(' с ', ' ').removesuffix('с ')}в {raw[:4]} году" if prefix else f"{raw[:4]} год"
    return "дата пока неизвестна"
_RELEASE_TYPES = {"сингл": "Сингл", "EP": "EP"}


def premiere_card(feed, index=0) -> MessageSpec:
    """Карточка ленты «Премьеры» (feed — [(вид, пункт)], фильмы, сериалы и музыка вперемешку): «🎬 **Имя**» ссылкой,
    первой в метаданных — что это (Фильм, Сериал, Музыкальный альбом), описание в одно-два предложения; сверху
    красная «Пропустить» (lz_prem_x_<n>), ← n/N → по кругу по всей ленте, «Назад» — в хаб. Картинку (постер,
    обложку) прикладывает обработчик."""
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
    # Что это за карточка — первым: «Фильм · драма · 🇩🇪 DE»; у сингла и EP вместо «Музыкальный альбом» — их тип.
    release = next((value for value in values if value in _RELEASE_TYPES), "") if kind == "music" else ""
    # Дата премьеры — сразу за типом, всегда («Фильм · в кино с 28 ноября 2026 · драма»).
    values = [_RELEASE_TYPES.get(release) or _PREMIERE_TYPE[kind], premiere_date(kind, item),
              *(value for value in values if value != release)]
    b.line(" · ".join(values))
    about = _hub_about(item.get("note") or about)  # описание премьеры (AI), без него — начало описания источника
    if about:
        b.spacer()
        b.line(about)
    rows = [[InlineKeyboardButton("Пропустить", callback_data=f"lz_prem_x_{index}")]]
    if len(feed) > 1:
        rows.append([
            InlineKeyboardButton("◀️", callback_data=f"lz_prem_{(index - 1) % len(feed)}"),
            InlineKeyboardButton(f"{index + 1}/{len(feed)}", callback_data="noop"),
            InlineKeyboardButton("▶️", callback_data=f"lz_prem_{(index + 1) % len(feed)}"),
        ])
    rows.append(nav_row("m_leisure"))
    return b.build_stripped(reply_markup=InlineKeyboardMarkup(rows))


def leisure_library_menu(back="m_leisure") -> MessageSpec:
    b = MessageBuilder()
    b.title("🎚️ Моя библиотека")
    b.line("Любимое кино и артисты — по ним подбираю рекомендации.")
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([*_column_kb((
        ("🎬 Фильмы и сериалы", "movie_favorites"),
        ("🎧 Музыка", "artist_favorites"),
        ("🎫 Концерты", "a_concerts_find"),
        ("📰 Статьи", "lz_rt"),
    )).inline_keyboard, nav_row(back)]))


def read_themes_screen(labels, chosen, sources=None) -> MessageSpec:
    """«📰 Темы статей»: источники выбранных тем строками «**Тема:** источник · источник», переключатели тем
    (✅ выбрано / □ нет) в два столбца; ниже «Назад» — в «Мою библиотеку»."""
    b = MessageBuilder()
    b.title("📰 Темы статей")
    b.line("Из этих тем собираю статьи дня — на русском, с источником и языком оригинала.")
    picked = [(key, label) for key, label in labels if key in chosen and (sources or {}).get(key)]
    if picked:
        b.spacer()
        for key, label in picked:
            b.labeled_line(label.split(" ", 1)[-1], " · ".join(sources[key]), lowercase=False)
    buttons = [InlineKeyboardButton(("✅ " if key in chosen else "□ ") + label, callback_data=f"lz_rt_{key}")
               for key, label in labels]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append(nav_row("lz_lib"))
    return b.build_stripped(reply_markup=InlineKeyboardMarkup(rows))


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
