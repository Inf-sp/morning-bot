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


def movie_card(item, tm):
    """Составная карточка (условные блоки) -> MessageBuilder."""
    item = item if isinstance(item, dict) else {"title": str(item)}
    title = (tm.get("name") if tm else "") or item.get("title", "")
    year = f" ({tm.get('year')})" if tm and tm.get("year") else ""
    kind = (tm.get("kind") if tm else "") or ""
    icon = "📺" if kind == "tv" else "🎬"
    type_label = "Сериал" if kind == "tv" else ("Фильм" if kind == "movie" else "")
    en = (tm.get("name_en") if tm else "") or item.get("title_en", "")

    b = MessageBuilder()
    b.text_line(f"{icon} ")
    b.bold(f"{title}{year}")
    b.newline()
    if en and en.lower() != title.lower():
        b.italic(en)
        b.newline()
    genre_bits = " · ".join(x for x in [type_label, (tm.get("genres") if tm else "")] if x)
    if genre_bits:
        b.spacer()
        b.line(f"🎭 {genre_bits}")
    if tm and tm.get("rating"):
        b.line(f"⭐ {tm.get('rating'):.1f}/10 TMDb")
    if tm and tm.get("overview"):
        b.spacer()
        b.line(clip(tm["overview"]))
    b.spacer()
    b.line(f"💡 {item.get('hook', '')}")
    if tm and tm.get("url"):
        b.spacer()
        b.line(f"🔗 {tm['url']}")
    return title, b.build_stripped()


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
