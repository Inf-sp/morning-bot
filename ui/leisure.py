import re
from datetime import date, datetime

from telegram import MessageEntity

from .builder import MessageBuilder, MessageSpec, u16_len
from .constants import ui_label


def _birthday_date_label(value):
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not match:
        return ""
    try:
        birthday = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return ""
    return _format_date_label(birthday, include_year=True)


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


def _safe_rebus_fact(rebus, *candidates):
    """Не показывает факт, если он напрямую раскрывает скрытый ответ ребуса."""
    answer = " ".join(str((rebus or {}).get("answer") or "").casefold().split()).strip("«»\"'")
    for value in candidates:
        fact = " ".join(str(value or "").split()).strip()
        if fact and (not answer or answer not in fact.casefold()):
            return fact
    return ""


def _pluralize_titles(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "фильм/сериал"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "фильма/сериала"
    return "фильмов/сериалов"


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


def favorite_movie_genre(genre, total):
    b = MessageBuilder()
    b.section(f"🎬 {str(genre or 'Без жанра').strip()} · {total} {_pluralize_titles(total)}")
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
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "книга"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "книги"
    return "книг"


def favorite_book_genre(genre, total):
    b = MessageBuilder()
    b.section(f"📚 {str(genre or 'Без жанра').strip()} · {total} {_pluralize_books(total)}")
    return b.build_stripped()


def favorite_book_delete_confirmation(title):
    b = MessageBuilder()
    b.line(f"Удалить «{str(title or 'Книга').strip()}»?")
    return b.build_stripped()


def game_set_home(total, genres):
    b = MessageBuilder()
    b.title(f"🎚️ Мой набор игр · {total} {_pluralize_games(total)}")
    for group in genres or []:
        names = [str(name or "").strip() for name in group.get("names") or [] if str(name or "").strip()]
        if not names:
            continue
        b.bold(f"{str(group.get('genre') or 'Без жанра').strip()}:")
        b.newline()
        b.line(", ".join(names))
        b.spacer()
    if not total:
        b.line("Добавь любимые игры — следующие рекомендации станут точнее.")
    return b.build_stripped()


def _pluralize_games(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "игра"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "игры"
    return "игр"


def game_set_genre(genre, total):
    b = MessageBuilder()
    b.section(f"👾 {str(genre or 'Без жанра').strip()} · {total} {_pluralize_games(total)}")
    return b.build_stripped()


def game_set_card(data):
    data = data or {}
    b = MessageBuilder()
    b.text_line("👾 ")
    b.bold(str(data.get("name") or "Игра"))
    b.newline()
    meta = [str(data.get("genre_label") or "").strip()]
    if data.get("year"):
        meta.append(str(data["year"]))
    platforms = " · ".join(str(value) for value in data.get("platform_labels") or [])
    if platforms:
        meta.append(platforms)
    meta = [value for value in meta if value]
    if meta:
        b.spacer()
        b.line(" · ".join(meta))
    if data.get("description"):
        b.spacer()
        b.line(str(data["description"]).strip())
    return b.build_stripped()


def game_delete_confirmation(name):
    b = MessageBuilder()
    b.line(f"Удалить «{str(name or 'Игра').strip()}»?")
    return b.build_stripped()


def favorite_game_added_card(data):
    b = MessageBuilder()
    b.line("✅ Добавлена в «🎚️ Мой набор игр»")
    b.spacer()
    b.bold(str((data or {}).get("name") or "Игра"))
    b.newline()
    meta = [str((data or {}).get("genre_label") or "").strip()]
    platforms = " · ".join(str(value) for value in (data or {}).get("platform_labels") or [])
    if platforms:
        meta.append(platforms)
    if (data or {}).get("year"):
        meta.append(str(data["year"]))
    meta = [value for value in meta if value]
    if meta:
        b.line(" · ".join(meta))
    return b.build_stripped()


def game_card(data):
    """Компактная рекомендация игры в том же ритме, что кино и музыка."""
    data = data or {}
    b = MessageBuilder()
    if not data:
        b.section("👾 Игра не нашлась")
        b.line("Для выбранных платформ пока нет варианта в этом жанре.")
        return b.build_stripped()
    platforms = " · ".join(str(value) for value in data.get("platform_labels") or [])
    b.text_line("👾 ")
    b.bold("Игра для тебя" + (f" · {platforms}" if platforms else ""))
    b.newline()
    b.spacer()
    name = str(data.get("name") or "Игра")
    trailer_url = str(data.get("trailer_url") or "").strip()
    if trailer_url:
        b.link(name, trailer_url)
    else:
        b.bold(name)
    meta = [str(data.get("genre_label") or "").strip()]
    try:
        year = int(data.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    if year:
        meta.append(str(year))
    try:
        rating = float(data.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0
    if rating:
        meta.append(f"⭐ {rating:.1f}/10")
    meta = [value for value in meta if value]
    if meta:
        b.text_line(f" · {' · '.join(meta)}")
    b.newline()
    if data.get("lgbt"):
        b.line("🏳️‍🌈 ЛГБТ")
    description = str(data.get("description") or "").strip()
    if description:
        b.spacer()
        b.line(description)
    reasons = [str(value).strip() for value in data.get("reasons") or [] if str(value).strip()]
    if reasons:
        b.spacer()
        b.bold("Почему тебе:")
        b.newline()
        for reason in reasons[:2]:
            b.bullet(reason)
    start = str(data.get("start") or "").strip()
    if start:
        b.spacer()
        b.labeled_line("С чего начать", start, lowercase=False)
    return b.build_stripped()


def game_home_screen(city, items, daily, *, year=None, season="лета"):
    daily = daily or {}
    year = int(year or datetime.now().year)
    b = MessageBuilder()
    b.text_line("👾 ")
    b.bold(f"Игры на сегодня · Новинки {year}")
    b.newline()

    b.spacer()
    b.bold(f"Новинки {str(season or 'лета').strip()}:")
    b.newline()
    rows = [item for item in list(items or []) if str(item.get("title") or "").strip()][:3]
    if rows:
        for item in rows:
            b.text_line("• ")
            title = str(item.get("title") or "").strip()
            url = str(item.get("trailer_url") or item.get("url") or "").strip()
            if url:
                b.link(title, url)
            else:
                b.bold(title)
            meta = " · ".join(
                str(value).strip()
                for value in (item.get("genre"), item.get("platform_label"))
                if str(value or "").strip()
            )
            if meta:
                b.text_line(f" · {meta}")
            b.newline()
    else:
        b.line("Пока не удалось подтвердить ближайшие релизы.")

    b.spacer()
    b.bold("Ребус недели:")
    b.newline()
    b.text_line(str(daily.get("emoji") or "🎮 ❓"))
    b.text_line(" → ")
    b.add(str(daily.get("answer") or "Ответ").strip(), MessageEntity.SPOILER)

    fact = _safe_rebus_fact(daily, daily.get("fact"))
    if fact:
        b.spacer()
        b.bold("💡 Интересно:")
        b.text_line(" ")
        b.line(fact)
    return b.build_stripped()


def game_genres_screen():
    b = MessageBuilder()
    b.section("🎭 Жанр игры")
    b.line("Выбери настроение — подберу игру для твоих платформ.")
    return b.build_stripped()


def game_preferences(current, recency, rating):
    b = MessageBuilder()
    b.section("👾 Игры")
    b.line("Это приоритеты для рекомендаций и премьер, а не жёсткие ограничения.")
    b.spacer()
    b.labeled_line(
        "Платформы", " · ".join(current) if current else "все популярные", lowercase=False,
    )
    b.labeled_line("Период", recency or "Любые годы", lowercase=False)
    b.labeled_line("Рейтинг", rating or "любая", lowercase=False)
    return b.build_stripped()


def game_premieres_screen(items):
    b = MessageBuilder()
    b.section("🎮 Премьеры игр")
    if not items:
        b.line("Пока не удалось подтвердить ближайшие релизы.")
        return b.build_stripped()
    for item in items[:7]:
        card = MessageBuilder()
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        url = str(item.get("trailer_url") or item.get("url") or "").strip()
        if url:
            card.link(title, url)
        else:
            card.bold(title)
        card.newline()
        meta = " · ".join(
            str(value).strip()
            for value in (item.get("date_label"), item.get("platform_label"), item.get("genre"))
            if str(value or "").strip()
        )
        if meta:
            card.line(meta)
        summary = _movie_premiere_summary(item.get("summary"), limit=90)
        if summary:
            if summary[-1] not in ".!?…":
                summary += "."
            card.line(summary)
        card = card.build_stripped()
        # Подпись нативной Telegram-галереи ограничена 1024 UTF-16 единицами.
        # Последнюю карточку не обрываем: она либо помещается целиком, либо не
        # попадает в подпись альбома.
        if u16_len(b.text) + 2 + u16_len(card.text) > 1024:
            break
        b.embed(card)
    return b.build_stripped()


def movie_home_screen(genre_labels, country_label=None, now_playing=None):
    """Главный экран раздела «Кино»: как искать и что сейчас в прокате. Тот же
    визуальный паттерн, что у Гардероба (home_screen)."""
    b = MessageBuilder()
    b.text_line("🎬 ")
    b.bold("Кино")
    b.newline()
    b.spacer()
    if now_playing:
        b.spacer()
        b.text_line("🎟️ ")
        b.bold(f"Сейчас в кино · {country_label}")
        b.newline()
        for item in now_playing[:5]:
            _format_movie_row(b, item)
        if len(now_playing) > 5:
            b.line(f"Ещё {len(now_playing) - 5} фильма в прокате")
    elif country_label:
        b.spacer()
        b.text_line("🎟️ ")
        b.bold(f"Сейчас в кино · {country_label}")
        b.newline()
        b.line("Пока не удалось подтвердить актуальные кинотеатральные показы.")

    return b.build_stripped()


def movie_now_playing_screen(city, now_playing, cinema_day):
    """Ежедневная кино-витрина: лёгкая, короткая и без табличного вида."""
    city = str(city or "твоего города").strip()
    cinema_day = cinema_day or {}
    rebus = cinema_day.get("rebus") or {}
    birthday = cinema_day.get("birthday") or {}
    b = MessageBuilder()
    b.text_line("🎬 ")
    b.bold(f"Кино на сегодня · {city}")
    b.newline()

    b.spacer()
    b.bold("Что в кино:")
    cinema = _movie_now_playing_lines(now_playing)
    if cinema:
        b.newline()
        for movie in cinema:
            b.text_line("• ")
            trailer_url = str(_item_value(movie, "trailer_url", "") or "").strip()
            title = str(_item_value(movie, "title", "") or "").strip()
            label = f"«{title}»"
            if trailer_url:
                b.link(label, trailer_url)
            else:
                b.text_line(label)
            genres = _movie_genres_for_line(movie)
            if genres:
                b.text_line(f" ({genres})")
            b.newline()
    else:
        b.text_line(" ")
        b.line("Пока не удалось подтвердить актуальные показы.")

    if birthday.get("name"):
        b.spacer()
        b.bold("Именинник дня:")
        b.text_line(" ")
        b.bold(str(birthday["name"]))
        birth_date = _birthday_date_label(birthday.get("birth"))
        if birth_date:
            b.text_line(f" · {birth_date}")
        b.text_line(f" — {str(birthday.get('role') or 'кинематографист').strip()}.")
        birthday_fact = str(birthday.get("fact") or "").strip()
        if birthday_fact:
            b.text_line(f" {birthday_fact}")
        b.newline()

    fact = _safe_rebus_fact(rebus, rebus.get("fact"), cinema_day.get("fact"))
    b.spacer()
    b.bold("Ребус дня:")
    b.text_line(" ")
    b.text_line(str(rebus.get("emoji") or "🎬 ❓"))
    b.text_line(" → ")
    b.add(str(rebus.get("answer") or "Ответ").strip(), MessageEntity.SPOILER)
    if fact:
        b.spacer()
        b.bold("💡 Интересно:")
        b.text_line(" ")
        b.line(fact)
    return b.build_stripped()


def _movie_now_playing_lines(now_playing) -> list[dict]:
    entries = []
    for item in now_playing or []:
        title = str(_item_value(item, "title", "") or "").strip()
        if not title:
            continue
        entries.append(item)
    return entries


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
        genre = str(value or "").strip()
        if genre:
            labels.append(translations.get(genre.casefold(), genre.casefold()))
    return ", ".join(dict.fromkeys(labels[:3]))


def _item_value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _primary_genre(movie) -> str | None:
    genres = _item_value(movie, "genres")
    if isinstance(genres, list):
        if not genres:
            return None
        value = _movie_genre_text(genres[0])
        return value[:1].upper() + value[1:] if value else None
    genre = _item_value(movie, "genre")
    value = _movie_genre_text(genre) if genre else None
    return value[:1].upper() + value[1:] if value else None


def _format_rating(rating: float | None) -> str | None:
    try:
        value = float(rating)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return f"⭐ {value:.1f}"


def has_visible_movie_rating(movie) -> bool:
    """Рейтинг можно показать, только если он не основан на нескольких голосах."""
    vote_count = int(_item_value(movie, "vote_count", 0) or 0)
    return bool(_format_rating(_item_value(movie, "rating"))) and vote_count >= 25


def _format_movie_row(b: MessageBuilder, movie, *, with_description=False) -> None:
    title = str(_item_value(movie, "title", "") or "").strip()
    if not title:
        return
    b.text_line("• ")
    b.bold(title)
    genre = _primary_genre(movie)
    if genre:
        b.text_line(f" · {genre}")
    if with_description:
        overview = clip(str(_item_value(movie, "overview", "") or ""), limit=110)
        if overview:
            if overview[-1] not in ".!?…":
                overview += "."
        if overview:
            b.text_line(f" · {overview}")
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
            return f"Подборка в жанре «{label}»"
        if kind == "mood":
            return f"Подборка для настроения «{label}»"
    because = tm.get("because")
    if because:
        if tm.get("via") == "similar":
            genres = ", ".join(tm.get("shared_genres") or [])
            return f"Подходит по жанрам: {genres}" if genres else ""
        title = _clip_title(because)
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
    url = str(item.get("url") or "").strip()

    b = MessageBuilder()
    b.text_line("📚 ")
    if url:
        if author:
            b.bold(f"{author} • ")
        b.link(f"«{title}»", url)
        if head_meta:
            b.bold(f" ({head_meta})")
    elif not head_meta:
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
    if item.get("lgbt"):
        b.line("🏳️‍🌈 ЛГБТ")
    try:
        rating = float(item.get("rating"))
        ratings_count = int(item.get("ratings_count") or 0)
    except (TypeError, ValueError):
        rating = 0
        ratings_count = 0
    if rating > 0 and ratings_count > 0:
        b.spacer()
        count_text = f"{ratings_count:,}".replace(",", " ")
        b.line(f"⭐ Оценка читателей: {rating:.1f}/5 · {count_text} оценок")
    if item.get("desc"):
        desc = str(item["desc"]).strip()
        if desc and desc[-1] not in ".!?…":
            desc += "."
        b.spacer()
        b.line(desc)
    why = item.get("why") or []
    if isinstance(why, list) and why:
        b.spacer()
        b.bold("Почему стоит читать:")
        b.newline()
        for w in why:
            b.bullet(str(w).lstrip("-–— "))
    if item.get("plot"):
        plot = str(item["plot"]).strip()
        if plot and plot[-1] not in ".!?…":
            plot += "."
        b.spacer()
        b.bold("Коротко о сюжете:")
        b.text_line(" ")
        b.line(plot)
    if item.get("quote"):
        quote = str(item["quote"]).strip().strip("«»\"")
        b.spacer()
        b.add(f"💭 «{quote}»", MessageEntity.ITALIC)
        b.newline()
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


def weekly_books_screen(city, daily_book, items, *, season=""):
    """Недельная литературная витрина без рейтингов и служебных подписей."""
    city = str(city or "твоего города").strip()
    daily_book = daily_book or {}
    rebus = daily_book.get("rebus") or {}
    birthday = daily_book.get("birthday") or {}
    b = MessageBuilder()
    b.text_line("📚 ")
    b.bold(f"Литературный вайб · {city}")
    b.newline()

    premieres = _book_premiere_items(items)
    b.spacer()
    b.bold(f"Новинки {str(season or 'сезона').strip()}:")
    if premieres:
        b.newline()
        for premiere in premieres:
            _write_book_premiere(b, premiere, compact=True)
    else:
        b.text_line(" ")
        b.line("Пока не удалось подтвердить заметные новинки.")

    if birthday.get("name"):
        b.spacer()
        b.bold("Автор недели:")
        b.text_line(" ")
        b.bold(str(birthday["name"]))
        birth_date = _birthday_date_label(birthday.get("birth"))
        if birth_date:
            b.text_line(f" · {birth_date}")
        b.line(f" — {str(birthday.get('detail') or 'писатель').strip()}.")

    fact = _safe_rebus_fact(
        rebus, birthday.get("fact"), rebus.get("fact"), daily_book.get("fact"),
    )
    b.spacer()
    b.bold("Литературный ребус:")
    b.text_line(" ")
    b.text_line(str(rebus.get("emoji") or "📚 ❓"))
    b.text_line(" → ")
    b.add(str(rebus.get("answer") or "Ответ").strip(), MessageEntity.SPOILER)
    if fact:
        b.spacer()
        b.bold("💡 Интересно:")
        b.text_line(" ")
        b.line(fact)
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
    rating = float(_item_value(item, "rating", 0) or 0)
    meta.append(f"⭐ {rating:.1f}/10")
    genres = _movie_genres_for_line(item)
    if genres:
        meta.append(genres.replace(", ", " · "))
    b.line(" · ".join(meta))
    overview = _movie_premiere_summary(_item_value(item, "overview", ""), limit=180)
    if overview:
        b.spacer()
        b.line(overview if overview[-1] in ".!?…" else overview + ".")
    return b.build_stripped()


def book_premieres_screen(month, items):
    """Подпись книжной Telegram-галереи: до семи премьер с обложками."""
    b = MessageBuilder()
    b.text_line("🆕 ")
    b.bold(f"Премьеры книг · {month}")
    b.newline()
    b.spacer()
    b.line("Свежие книги разных жанров; список обновляется раз в неделю.")
    b.spacer()
    if not items:
        b.line("Витрина появится после ближайшего ночного обновления.")
        return b.build_stripped()
    for item in list(items or [])[:7]:
        card = MessageBuilder()
        _write_book_premiere(card, item, summary_limit=90)
        card = card.build_stripped()
        if u16_len(b.text) + 2 + u16_len(card.text) > 1024:
            break
        b.embed(card)
    return b.build_stripped()


def _book_premiere_items(items) -> list:
    entries = []
    for item in list(items or [])[:3]:
        title = str(_item_value(item, "title", "") or "").strip()
        if not title:
            continue
        entries.append(item)
    return entries


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
            builder.link(f"«{title}»", url)
        else:
            builder.text_line(f"«{title}»")
        if genres:
            builder.text_line(f" ({genres})")
        if author:
            builder.text_line(f" · {author}")
        if summary:
            builder.text_line(" · ")
            builder.line(_book_premiere_summary(summary, limit=160))
        else:
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
        "science fiction": "Фантастика",
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
        raw = str(category or "").strip()
        translated = translations.get(raw.casefold())
        if translated:
            labels.append(translated)
        elif any("а" <= char.casefold() <= "я" for char in raw):
            labels.append(raw[:1].upper() + raw[1:])
    return " · ".join(dict.fromkeys(labels[:2]))


def _book_premiere_summary(summary, *, limit):
    summary = clip(str(summary or ""), limit=limit)
    summary = summary[:1].upper() + summary[1:] if summary else ""
    return summary if summary.endswith((".", "!", "?", "…")) else summary + "."


def _book_premiere_date(item):
    raw = str(_item_value(item, "published_date", "") or "").strip()
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return ""
    return _format_date_label(value, include_year=True)


def music_week_screen(city, daily_music, concerts):
    """Короткая недельная витрина Музыки без чартов и таблиц."""
    city = str(city or "твоего города").strip()
    daily_music = daily_music or {}
    rebus = daily_music.get("rebus") or {}
    legend = daily_music.get("legend") or {}
    b = MessageBuilder()
    b.text_line("🎧 ")
    b.bold(f"Музыка этой недели · {city}")
    b.newline()

    b.spacer()
    events = [item for item in concerts or [] if _item_value(item, "artist")][:3]
    b.bold("Концерты рядом:")
    if events:
        b.newline()
        for event in events:
            artist = str(_item_value(event, "artist", "") or "").strip()
            date = str(_item_value(event, "date", "") or "").strip()
            place = str(_item_value(event, "place", "") or "").strip()
            context = _concert_context_text(_item_value(event, "context", ""))
            url = str(_item_value(event, "url", "") or "").strip()
            details = " · ".join(value for value in (date, place) if value)
            b.text_line("• ")
            if url:
                b.link(artist, url)
            else:
                b.text_line(artist)
            if context:
                b.text_line(f" ({_lower_initial(context)})")
            if details:
                b.text_line(f" · {details}")
            b.newline()
    else:
        b.line(" Пока нет подтверждённых ближайших выступлений.")

    if legend.get("name"):
        b.spacer()
        b.bold("Артист недели:")
        b.text_line(" ")
        b.bold(str(legend["name"]))
        birth_date = _birthday_date_label(legend.get("birth"))
        if birth_date:
            b.text_line(f" · {birth_date}")
        b.line(f" — {str(legend.get('detail') or 'музыкант').strip()}.")

    fact = _safe_rebus_fact(rebus, rebus.get("fact"), daily_music.get("fact"))
    b.spacer()
    b.bold("Музыкальный ребус:")
    b.text_line(" ")
    b.text_line(str(rebus.get("emoji") or "🎧 ❓"))
    b.text_line(" → ")
    b.add(str(rebus.get("answer") or "Ответ").strip(), MessageEntity.SPOILER)
    if fact:
        b.spacer()
        b.bold("💡 Интересно:")
        b.text_line(" ")
        b.line(fact)
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


def _lower_initial(value):
    value = str(value or "")
    return value[:1].lower() + value[1:] if value else ""


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
        context = str(event.get("context", "")).strip()
        key = (title, place, context)
        if key not in groups:
            groups[key] = {"title": title, "place": place, "context": context, "dates": []}
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
    if event.get("context"):
        b.labeled_line("Формат", event["context"], lowercase=False)
    if event.get("place"):
        b.labeled_line("Место", event["place"], lowercase=False)
    date_text = _format_dates([d for d in event.get("dates", []) if isinstance(d, date)])
    if date_text:
        b.labeled_line("Дата", date_text, lowercase=False)


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


def weekly_events_card(movies, concerts, books, games) -> MessageSpec:
    """Одна строка на событие, максимум три пункта в каждой категории."""
    b = MessageBuilder()
    b.title("🎲 Ближайшие события")

    sections_added = 0
    movie_rows = [item for item in list(movies or []) if _item_value(item, "title", "")][:3]
    if movie_rows:
        b.section("🎬 Кино")
        sections_added += 1
        for item in movie_rows:
            movie_id = _item_value(item, "id", "")
            url = str(_item_value(item, "trailer_url", "") or "").strip()
            if not url and movie_id:
                url = f"https://www.themoviedb.org/movie/{movie_id}"
            genres = _movie_genres_for_line(item).replace(", ", " · ")
            rating = _weekly_rating(
                _item_value(item, "rating", 0), _item_value(item, "vote_count", 0), 10,
            )
            _weekly_item(b, f"«{_item_value(item, 'title', '')}»", url, (genres, rating))

    concert_rows = [item for item in list(concerts or []) if item.get("title")][:3]
    if concert_rows:
        b.section("🎫 Концерты")
        sections_added += 1
        for item in concert_rows:
            day = _parse_event_date(item.get("date"))
            date_label = _format_date_label(day, include_year=day.year != date.today().year) if day else ""
            _weekly_item(
                b, item.get("title"), item.get("url"), (item.get("genre"), date_label),
            )

    book_rows = [item for item in list(books or []) if _item_value(item, "title", "")][:3]
    if book_rows:
        b.section("📚 Книги")
        sections_added += 1
        for item in book_rows:
            rating = _weekly_rating(
                _item_value(item, "rating", 0), _item_value(item, "ratings_count", 0), 5,
            )
            _weekly_item(
                b,
                f"«{_item_value(item, 'title', '')}»",
                _item_value(item, "url", ""),
                (_book_premiere_genres(item), rating),
            )

    game_rows = [item for item in list(games or []) if item.get("title")][:3]
    if game_rows:
        b.section("👾 Игры")
        sections_added += 1
        for item in game_rows:
            _weekly_item(
                b, item.get("title"), item.get("trailer_url") or item.get("url"),
                (item.get("genre"), item.get("date_label"), item.get("platform_label")),
            )

    if not sections_added:
        b.line("Пока нет подтверждённых премьер и событий.")
    return b.build_stripped()


def plain_from_html(text):
    return re.sub(r"<[^>]+>", "", text or "")
