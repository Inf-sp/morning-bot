import html
import re
from datetime import date, datetime

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




def favorite_movie_list(genre, total):
    """Заголовок списка категории «Моего кино»; сами фильмы — кнопками, как в словаре."""
    b = MessageBuilder()
    b.section(f"🎬 {genre} · {int(total or 0)}")
    b.spacer()
    b.line("Выбери фильм.")
    return b.build_stripped()


def favorite_movies_home(total, genres):
    b = MessageBuilder()
    b.title(f"🎚️ Моё кино · {total} {_pluralize_titles(total)}")
    for group in genres or []:
        titles = [str(title or "").strip() for title in group.get("titles") or [] if str(title or "").strip()]
        if not titles:
            continue
        b.bold(f"{str(group.get('genre') or 'Без жанра').strip()}:")
        b.newline()
        b.line(", ".join(titles))
        b.spacer()
    if not total:
        b.line("Добавь любимые фильмы и сериалы — подбор станет точнее.")
    return b.build_stripped()


def favorite_books_home(total, genres):
    b = MessageBuilder()
    b.title(f"🎚️ Мои книги · {total} {_pluralize_books(total)}")
    for group in genres or []:
        titles = [str(title or "").strip() for title in group.get("titles") or [] if str(title or "").strip()]
        if not titles:
            continue
        b.bold(f"{str(group.get('genre') or 'Без жанра').strip()}:")
        b.newline()
        b.line(", ".join(titles))
        b.spacer()
    if not total:
        b.line("Добавь любимые книги — следующие рекомендации станут точнее.")
    return b.build_stripped()


def _pluralize_books(n):
    return ru_plural(n, "книга", "книги", "книг")


def favorite_book_delete_confirmation(title):
    b = MessageBuilder()
    b.line(f"Удалить «{str(title or 'Книга').strip()}»?")
    return b.build_stripped()


def yearly_top_screen(kind, year, item):
    labels = {
        "movie": "Фильмы", "tv": "Сериалы",
        "book": "Книги",
    }
    b = MessageBuilder()
    b.section(f"🏆 Топ-5 · {labels.get(kind, 'Лучшее')} {year}")
    if not item:
        b.line("Пока не удалось загрузить проверенный список.")
        return b.build_stripped()
    title = str(item.get("title") or item.get("name") or "").strip()
    url = str(item.get("url") or item.get("info_link") or "").strip()
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

    # 1. Что это — заголовок.
    b.text_line(f"{ui_label('cinema', '').strip()} ")
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
    # Дата выхода — в той же строке: полная дата премьеры, если известна, иначе год.
    release = (_event_date_label((tm or {}).get("release_date")) if tm else "") or year
    if release:
        meta_parts.append(release)
    if meta_parts:
        b.spacer()
        b.line(" · ".join(meta_parts))

    # 3. Статус и объём сериала (для фильма пусто — длительность и страна не показываются).
    detail = _detail_line(tm)
    if detail:
        b.spacer()
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

    # 6. Трейлер — ссылка, спрятанная в тексте, последней строкой.
    trailer = str((tm or {}).get("trailer_url") or "").strip()
    if trailer:
        b.spacer()
        b.link("Посмотреть трейлер", trailer)
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


def book_text(item):
    """Карточка книги: название → автор · страна · год · оригинал → жанр → сюжет."""
    author = str(item.get("author") or "").strip()
    country = str(item.get("country") or "").strip()
    title = str(item.get("title") or "Книга").strip()
    original = str(
        item.get("original_title") or item.get("title_en") or item.get("alternative_title") or ""
    ).strip()
    year = str(item.get("year") or "").strip()
    categories = item.get("categories") or []
    if isinstance(categories, str):
        categories = [categories]
    genre_names = {
        "fiction": "Художественная проза", "fantasy": "Фэнтези",
        "science fiction": "Фантастика", "mystery & detective": "Детектив",
        "thrillers": "Триллер", "romance": "Романтика", "history": "История",
        "biography & autobiography": "Биография", "psychology": "Психология",
    }
    genre = str(item.get("genre") or "").strip() or next(
        (genre_names.get(str(value).casefold(), str(value).strip())
         for value in categories if str(value).strip()), "")

    b = MessageBuilder()
    b.bold(title)
    b.newline()
    metadata = [value for value in (
        author,
        country,
        year,
        original if original.casefold() != title.casefold() else "",
    ) if value]
    if metadata:
        b.line(" · ".join(metadata))
    if genre:
        b.line(f"Жанр: {genre}")

    plot_source = item.get("plot") or item.get("description") or item.get("desc")
    if plot_source:
        plot = " ".join(str(plot_source).split()).strip()
        sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", plot) if part.strip()]
        plot = " ".join(sentences[:3])
        if plot and plot[-1] not in ".!?…":
            plot += "."
        b.spacer()
        b.bold("Сюжет")
        b.newline()
        b.line(plot)
    return b.build_stripped()


def book_add_candidate_card(data):
    """Короткая карточка подтверждения: факты каталога без длинного служебного текста."""
    item = dict(data or {})
    description = str(item.get("description") or item.get("desc") or "").strip()
    if description:
        item["description"] = clip(description, limit=320)
        item.pop("desc", None)
    for field in ("why", "plot", "quote", "quote_author"):
        item.pop(field, None)
    return book_text(item)


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
            if note:
                b.text_line(f" — {note}")
            b.newline()
    if data.get("fact"):
        b.spacer()
        b.bold(ui_label("interesting", "Полезно:"))
        b.newline()
        b.line(data["fact"])
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


def favorite_book_added_card(data, *, already=False):
    """Подтверждение ручного добавления книги с проверенными метаданными."""
    data = data if isinstance(data, dict) else {}
    title = str(data.get("title") or data.get("value") or "Книга").strip()
    url = str(data.get("url") or data.get("info_link") or "").strip()
    details = [
        str(data.get("year") or "").strip(),
        str(data.get("genre_label") or "").strip(),
    ]
    b = MessageBuilder()
    b.line("✅ Уже в «🎚️ Мои книги»" if already
           else "✅ Добавлена в «🎚️ Мои книги»")
    b.spacer()
    b.text_line("📚 ")
    if url:
        b.link(title, url)
    else:
        b.bold(title)
    details = [value for value in details if value]
    if details:
        b.text_line(" · " + " · ".join(details))
    b.newline()
    author = str(data.get("author") or "").strip()
    if author:
        b.labeled_line("Автор", author, lowercase=False)
    try:
        rating = float(data.get("rating") or 0)
        ratings_count = int(data.get("ratings_count") or 0)
    except (TypeError, ValueError):
        rating, ratings_count = 0, 0
    if rating and ratings_count:
        b.line(f"⭐ {rating:.1f}/5 · {ratings_count:,} оценок".replace(",", " "))
    description = str(data.get("description") or data.get("desc") or "").strip()
    if description:
        b.spacer()
        b.line(clip(description, limit=260))
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
    url = str(_item_value(item, "url", "") or "").strip()
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


def book_premieres_screen(month, items):
    """Одна карточка книжной премьеры для перелистываемой витрины."""
    b = MessageBuilder()
    b.text_line("🆕 ")
    b.bold(f"Премьеры книг · {month}")
    b.newline()
    b.spacer()
    if not items:
        b.line("Витрина появится после ближайшего ночного обновления.")
        return b.build_stripped()
    for item in list(items or [])[:1]:
        card = MessageBuilder()
        _write_book_premiere(card, item, summary_limit=90)
        card = card.build_stripped()
        if u16_len(b.text) + 2 + u16_len(card.text) > 1024:
            break
        b.embed(card)
    return b.build_stripped()


def _write_book_premiere(
    builder: MessageBuilder, item, *, compact=False, summary_limit=310,
) -> None:
    title = str(_item_value(item, "title", "") or "").strip()
    if not title:
        return
    author = str(_item_value(item, "author", "") or "").strip()
    genres = _book_premiere_genres(item)
    summary = str(_item_value(item, "summary", "") or _item_value(item, "vibe", "") or "").strip()
    url = str(_item_value(item, "url", "") or "").strip()

    if compact:
        builder.text_line("• ")
        if url:
            builder.link(title, url)
        else:
            builder.text_line(title)
        if author:
            builder.text_line(f" — {author}")
        if genres:
            builder.text_line(f" · {genres}")
        if summary:
            builder.text_line(f" · {_book_premiere_summary(summary, limit=150)}")
        builder.newline()
        return

    if url:
        builder.link(f"«{title}»", url)
    else:
        builder.bold(f"«{title}»")
    builder.newline()
    if author:
        builder.line(author)
    if genres:
        builder.line(genres)
    premiere_date = _book_premiere_date(item)
    if premiere_date:
        builder.line(f"Премьера: {premiere_date}")
    if summary:
        builder.spacer()  # описание отделено от данных книги пустой строкой
        builder.line(_book_premiere_summary(summary, limit=summary_limit))
    builder.newline()


def _book_premiere_genres(item) -> str:
    categories = _item_value(item, "categories", [])
    if isinstance(categories, str):
        categories = [categories]
    if not isinstance(categories, list):
        return ""
    translations = {
        "fiction": "Художественная проза",
        "fantasy": "Фэнтези",
        "science fiction": "Научная фантастика",
        "space opera": "Космическая опера",
        "dystopian": "Дистопия",
        "dystopias": "Дистопия",
        "historical": "Историческая проза",
        "family life": "Семейная проза",
        "coming of age": "Взросление",
        "literary": "Литературная проза",
        "magical realism": "Магический реализм",
        "mystery & detective": "Детектив",
        "thrillers": "Триллер",
        "romance": "Романтика",
        "history": "История",
        "biography": "Биография",
        "biography & autobiography": "Биография",
        "psychology": "Психология",
        "poetry": "Поэзия",
        "juvenile fiction": "Детская литература",
    }
    labels = []
    for category in categories:
        parts = re.split(r"\s*(?:/|>)\s*", str(category or ""))
        for raw in parts:
            raw = raw.strip()
            translated = translations.get(raw.casefold())
            if translated:
                labels.append(translated)
            elif any("а" <= char.casefold() <= "я" for char in raw):
                labels.append(raw[:1].upper() + raw[1:])
    return " · ".join(dict.fromkeys(labels[:3]))


def _book_premiere_summary(summary, *, limit):
    summary = clip(str(summary or ""), limit=limit)
    summary = summary[:1].upper() + summary[1:] if summary else ""
    return summary if summary.endswith((".", "!", "?", "…")) else summary + "."


def _book_premiere_date(item):
    raw = str(_item_value(item, "published_date", "") or "").strip()
    month_match = re.fullmatch(r"(\d{4})-(\d{2})", raw)
    if month_match:
        year, month = int(month_match.group(1)), int(month_match.group(2))
        month_names = (
            "", "январь", "февраль", "март", "апрель", "май", "июнь",
            "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
        )
        return f"{month_names[month]} {year}" if 1 <= month <= 12 else ""
    if re.fullmatch(r"\d{4}", raw):
        return raw
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return ""
    return _format_date_label(value, include_year=True)


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


def _weekly_rating(value, count, scale) -> str:
    try:
        rating = float(value or 0)
        ratings_count = int(count or 0)
    except (TypeError, ValueError):
        return ""
    if rating <= 0 or ratings_count <= 0:
        return ""
    return f"⭐ {rating:.1f}/{scale}"


def _weekly_item(builder: MessageBuilder, title, url="", meta=()) -> None:
    title = " ".join(str(title or "").split())
    if not title:
        return
    builder.text_line("• ")
    if str(url or "").strip():
        builder.link(title, str(url).strip())
    else:
        builder.bold(title)
    values = [" ".join(str(value).split()) for value in meta if str(value or "").strip()]
    if values:
        builder.text_line(f" · {' · '.join(values)}")
    builder.newline()


def _movie_event_rows(b: MessageBuilder, title, items, limit) -> bool:
    rows = [item for item in list(items or []) if _item_value(item, "title", "")][:limit]
    if rows:
        b.section(title)
    for item in rows:
        movie_id = _item_value(item, "id", "")
        url = str(_item_value(item, "trailer_url", "") or "").strip()
        if not url and movie_id:
            url = f"https://www.themoviedb.org/movie/{movie_id}"
        genres = _movie_genres_for_line(item).replace(", ", " · ")
        _weekly_item(b, f"«{_item_value(item, 'title', '')}»", url,
                     (genres, _event_date_label(_item_value(item, "date", ""))))
    return bool(rows)


def _event_date_label(value) -> str:
    """Дата события в хабе — как у концертов: «16 октября», год только если не текущий."""
    day = _parse_event_date(str(value or "")[:10])
    return _format_date_label(day, include_year=day.year != date.today().year) if day else ""


def _concert_event_rows(b: MessageBuilder, title, items, limit) -> bool:
    rows = [item for item in list(items or []) if item.get("title")][:limit]
    if rows:
        b.section(title)
    for item in rows:
        _weekly_item(b, item.get("title"), item.get("url"), (item.get("genre"), _event_date_label(item.get("date"))))
    return bool(rows)


def _book_event_rows(b: MessageBuilder, title, items, limit) -> bool:
    rows = [item for item in list(items or []) if _item_value(item, "title", "")][:limit]
    if rows:
        b.section(title)
    for item in rows:
        rating = _weekly_rating(
            _item_value(item, "rating", 0), _item_value(item, "ratings_count", 0), 5,
        )
        _weekly_item(
            b, f"«{_item_value(item, 'title', '')}»", _item_value(item, "url", ""),
            (_book_premiere_genres(item), _event_date_label(_item_value(item, "published_date", "")), rating),
        )
    return bool(rows)


def _event_sections(b: MessageBuilder, sections) -> bool:
    """Пишет блоки (заголовок, writer, items, limit); пустой блок скрыт."""
    added = [writer(b, title, items, limit) for title, writer, items, limit in sections]
    return any(added)


def weekly_events_card(movies, concerts, books) -> MessageSpec:
    """Одна строка на событие; для концертов — до шести ближайших афиш."""
    b = MessageBuilder()
    b.title("🎲 Ближайшие события")
    if not _event_sections(b, (
        ("🎬 Кино", _movie_event_rows, movies, 3),
        ("🎫 Концерты", _concert_event_rows, concerts, 6),
        ("📚 Книги", _book_event_rows, books, 3),
    )):
        b.line("Пока нет подтверждённых премьер и событий.")
    return b.build_stripped()


LEISURE_HUB_LIMIT = 3


def leisure_hub_screen(concerts, movies, books, reply_markup=None) -> MessageSpec:
    """Хаб «Досуг»: только готовые данные из кэшей, пустые блоки скрыты."""
    b = MessageBuilder()
    b.title(ui_label("leisure", "Досуг"))
    if not _event_sections(b, (
        ("🎫 Концерты", _concert_event_rows, concerts, LEISURE_HUB_LIMIT),
        ("🎟️ Премьеры кино", _movie_event_rows, movies, LEISURE_HUB_LIMIT),
        ("📚 Новые книги", _book_event_rows, books, LEISURE_HUB_LIMIT),
    )):
        b.line("Выбери, что посмотреть, почитать или послушать.")
    return b.build_stripped(reply_markup=reply_markup)


def plain_from_html(text):
    return re.sub(r"<[^>]+>", "", text or "")


def _column_kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data)] for text, data in rows])


def leisure_hub_kb():
    rows = _column_kb((
        ("🎬 Подобрать кино", "movie_reco"),
        ("📚 Подобрать книгу", "book_reco"),
        ("🎧 Подобрать музыку", "music_reco"),
    )).inline_keyboard
    return InlineKeyboardMarkup([*rows, [
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
        InlineKeyboardButton("🎚️ Настроить", callback_data="lz_lib"),
    ]])


def leisure_premieres_menu() -> MessageSpec:
    b = MessageBuilder()
    b.title("🆕 Премьеры и концерты")
    b.line("Свежие релизы и ближайшие концерты любимых артистов.")
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([*_column_kb((
        ("🎟️ Премьеры кино", "movie_premieres"),
        ("🆕 Премьеры книг", "book_premieres"),
        ("🎫 Концерты", "a_concerts_find"),
    )).inline_keyboard, nav_row("m_leisure")]))


def leisure_library_menu() -> MessageSpec:
    b = MessageBuilder()
    b.title("🎚️ Моя библиотека")
    b.line("Любимое кино, книги и артисты — по ним подбираю рекомендации.")
    return b.build_stripped(reply_markup=InlineKeyboardMarkup([*_column_kb((
        ("🎬 Кино", "movie_favorites"),
        ("📚 Книги", "book_favorites"),
        ("🎧 Музыка", "artist_favorites"),
        ("🎫 Концерты", "a_concerts_find"),
    )).inline_keyboard, nav_row("m_leisure")]))


# ---------- «Новинка» в меню «Выбрать жанр» ----------
_NOVELTY_EMPTY = {
    "movie": "Свежих премьер в кино пока нет — загляни позже.",
    "book": "Свежих книжных премьер пока нет — загляни позже.",
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
    if kind == "book":
        return f"📚 Новая книга · {label}" if label else "📚 Новая книга"
    return f"🎧 Новый альбом · {label}" if label else "🎧 Новый альбом"


def novelty_card(kind, item) -> MessageSpec:
    """Карточка новинки: плашка, название, данные одной строкой, описание после пустой строки."""
    b = MessageBuilder()
    b.bold(_novelty_status(kind, item))
    b.newline()
    b.spacer()
    title = str(_item_value(item, "title", "") or "").strip()
    url = str(_item_value(item, "trailer_url", "") or _item_value(item, "url", "") or "").strip()
    if kind == "movie" and not url and _item_value(item, "id", ""):
        url = f"https://www.themoviedb.org/movie/{_item_value(item, 'id', '')}"
    heading = f"«{title}»" + (f" — {item.get('artist')}" if kind == "music" and item.get("artist") else "")
    if url:
        b.link(heading, url)
    else:
        b.bold(heading)
    b.newline()
    if kind == "movie":
        # Премьеры кино — без оценок: у свежих фильмов они ещё не устоялись.
        meta = [_movie_genres_for_line(item).replace(", ", " · "),
                _event_date_label(str(_item_value(item, "date", "") or ""))]
        summary = _movie_premiere_summary(_item_value(item, "overview", ""), limit=300)
    elif kind == "book":
        meta = [str(_item_value(item, "author", "") or ""), _book_premiere_genres(item)]
        summary = _book_premiere_summary(str(_item_value(item, "summary", "") or ""), limit=300)
    else:
        meta = []
        summary = ""
    meta = [value for value in meta if value.strip()]
    if meta:
        b.line(" · ".join(meta))
    if summary.strip():
        b.spacer()
        b.line(summary.strip())
    return b.build_stripped()
