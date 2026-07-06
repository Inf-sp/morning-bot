import re

from telegram import MessageEntity

from .builder import MessageBuilder, MessageSpec, u16_len


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


def movie_home_screen(loved_count, genre_labels):
    """Главный экран раздела «Кино»: польза, сколько уже в любимых, какие жанры
    выбраны в предпочтениях. Тот же визуальный паттерн, что у Гардероба (home_screen)."""
    b = MessageBuilder()
    b.text_line("🎬 ")
    b.bold("Кино")
    b.newline()
    b.spacer()
    b.line("Подбираю фильмы и сериалы под твой вкус — по любимым, по жанру или по настроению.")

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

    return b.build_stripped()


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
    icon = "📺" if kind == "tv" else "🎬"
    type_label = "Сериал" if kind == "tv" else ("Фильм" if kind == "movie" else "")

    b = MessageBuilder()

    # 1. Что это — заголовок.
    b.text_line(f"{icon} ")
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
            return f"🎭 Подборка в жанре «{label}»"
        if kind == "mood":
            return f"😊 Подборка для настроения «{label}»"
    because = tm.get("because")
    if because:
        title = _clip_title(because)
        if tm.get("via") == "similar":
            return f"💡 Похоже на «{title}»"
        return f"💡 Потому что вам понравился «{title}»"
    hook = (item.get("hook") or "").strip()
    return f"💡 {hook}" if hook else ""


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
        b.text_line("🎯 ")
        b.bold("Почему стоит читать")
        b.newline()
        for w in why:
            b.bullet(str(w).lstrip("-–— "))
    if item.get("plot"):
        b.spacer()
        b.text_line("✍🏻 ")
        b.bold("Коротко о сюжете")
        b.newline()
        b.line(item["plot"])
    if item.get("quote"):
        quote = str(item["quote"]).strip().strip("«»\"")
        b.spacer()
        b.text_line("💬 ")
        b.bold("Цитата")
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
        b.text_line("🎯 ")
        b.bold("Почему тебе зайдёт:")
        b.newline()
        for w in why:
            b.bullet(str(w))
    tracks = data.get("tracks") or []
    if isinstance(tracks, list) and tracks:
        b.spacer()
        b.text_line("🎧 ")
        b.bold("С чего начать:")
        b.newline()
        for t in tracks:
            b.bullet(str(t))
    if data.get("fact"):
        b.spacer()
        b.text_line("💡 ")
        b.bold("Факт:")
        b.newline()
        b.line(data["fact"])
    return b.build_stripped()


def concerts_list(place_label, events, empty_hint=""):
    """Список концертов твоих артистов -> MessageBuilder. Каждое событие - мини-блок:
    имя артиста, место, цена от, дата, скрытая ссылка "Подробнее…"."""
    b = MessageBuilder()
    b.text_line("🎤 ")
    b.bold(place_label)
    b.newline()
    if not events:
        b.spacer()
        b.line(empty_hint or "Сейчас ничего не нашёл. Попробуй другую страну 🌍")
        return b.build_stripped()
    for ev in events:
        b.spacer()
        b.bold(ev.get("artist", ""))
        b.newline()
        if ev.get("place"):
            b.line(f"📍 {ev.get('flag', '')} {ev['place']}")
        if ev.get("price"):
            b.line(f"💶 {ev['price']}")
        if ev.get("date"):
            b.line(f"🗓️ {ev['date']}")
        if ev.get("url"):
            b.link("Подробнее…", ev["url"])
            b.newline()
    return b.build_stripped()


def country_card(data):
    """Составная карточка (условные блоки) -> MessageBuilder."""
    b = MessageBuilder()
    b.text_line(f"{data.get('flag', '')} ")
    b.bold(data.get("country", ""))
    b.newline()
    if data.get("about"):
        b.spacer()
        b.line(data["about"])
    if data.get("for_what"):
        b.spacer()
        b.text_line("🎯 ")
        b.bold("Ради чего ехать:")
        b.line(f" {data['for_what']}")
    if data.get("langs"):
        b.spacer()
        b.text_line("🗣️ ")
        b.bold("Язык:")
        b.line(f" {data['langs']}")
    if data.get("note"):
        b.spacer()
        b.text_line("⚠️ ")
        b.bold("Главный нюанс:")
        b.line(f" {data['note']}")
    if data.get("fact"):
        b.spacer()
        b.text_line("🔎 ")
        b.bold("Факт:")
        b.line(f" {data['fact']}")
    return b.build_stripped()


def travel_plan(plan, fallback_country):
    """Текст плана путешествия персистируется как (text, entities) в NOTES_KEY (bucket='plan')
    и режется на chunks через util.chunk_text_with_entities в settings.plan_view — компоненты
    подходят так же, как для остальных карточек этого файла."""
    country = plan.get("title", fallback_country)
    b = MessageBuilder()
    b.text_line(f"{plan.get('flag', '')} ")
    b.bold(country)
    b.newline()
    if plan.get("about"):
        b.spacer()
        b.line(plan["about"])
    if plan.get("why"):
        b.spacer()
        b.text_line("🎯 ")
        b.bold("Почему тебе подойдёт")
        b.newline()
        for w in plan["why"]:
            b.bullet(str(w))
    if plan.get("best_time"):
        b.spacer()
        b.text_line("📅 ")
        b.bold("Лучшее время")
        b.newline()
        b.line(plan["best_time"])
    if plan.get("budget"):
        b.spacer()
        b.text_line("💰 ")
        b.bold("Бюджет")
        b.newline()
        for item in plan["budget"]:
            b.bullet(str(item))
    if plan.get("spots"):
        b.spacer()
        b.text_line("📸 ")
        b.bold("Не пропусти")
        b.newline()
        for spot in plan["spots"]:
            b.bullet(str(spot))
    if plan.get("lgbt"):
        b.spacer()
        b.text_line("🏳️‍🌈 ")
        b.bold("LGBTQ+")
        b.newline()
        b.line(plan["lgbt"])
    if plan.get("fact"):
        b.spacer()
        b.text_line("🍲 ")
        b.bold("Интересный факт")
        b.newline()
        b.line(plan["fact"])
    return b.build_stripped()


def plain_from_html(text):
    return re.sub(r"<[^>]+>", "", text or "")
