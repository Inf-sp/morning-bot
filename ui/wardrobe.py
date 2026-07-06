from telegram import MessageEntity

from .builder import MessageBuilder


def _stars(score):
    """Число 0-100 → строка звёзд (5 позиций, шаг 20)."""
    try:
        filled = max(0, min(5, round(int(score) / 20)))
    except (TypeError, ValueError):
        filled = 0
    return "⭐" * filled + "☆" * (5 - filled)


def improve_card(data):
    """Разбор гардероба как консультация стилиста.

    data: {score, summary, strengths[], weaknesses[{title,text}], buy[{rank,item,why}],
           avoid[], best_look{items[],why}, potential}
    """
    b = MessageBuilder()
    b.section("👕 Разбор гардероба")

    # Общая оценка
    score = data.get("score")
    b.section("Общая оценка")
    if score is not None:
        b.line(_stars(score))
        b.bold(f"{score} / 100")
        b.newline()
    summary = _clean_text(data.get("summary"))
    if summary:
        b.line(summary)

    # Сильные стороны
    strengths = [s for s in (data.get("strengths") or []) if _clean_text(s)]
    if strengths:
        b.section("✅ Сильные стороны")
        for s in strengths[:4]:
            b.bullet(_finish_dot(s))

    # Слабые места
    weaknesses = data.get("weaknesses") or []
    if weaknesses:
        b.section("⚠️ Что ограничивает гардероб")
        for i, wk in enumerate(weaknesses[:5], 1):
            if isinstance(wk, dict):
                title = _clean_text(wk.get("title"))
                text = _finish_dot(wk.get("text"))
            else:
                title, text = "", _finish_dot(wk)
            if title:
                b.spacer()
                b.bold(f"{i}. {title}")
                b.newline()
                if text:
                    b.line(text)
            elif text:
                b.bullet(text)

    # Что купить
    buy = data.get("buy") or []
    if buy:
        b.section("🛍 Что купить в первую очередь")
        medals = ["🥇", "🥈", "🥉", "•", "•"]
        for i, it in enumerate(buy[:5]):
            item = _clean_text(it.get("item")) if isinstance(it, dict) else _clean_text(it)
            why = _finish_dot(it.get("why")) if isinstance(it, dict) else ""
            b.spacer()
            b.text_line(f"{medals[i]} ")
            b.bold(item)
            b.newline()
            if why:
                b.line(why)

    # Чего не покупать
    avoid = [a for a in (data.get("avoid") or []) if _clean_text(a)]
    if avoid:
        b.section("🚫 Что покупать не стоит")
        for a in avoid[:3]:
            b.bullet(_finish_dot(a))

    # Лучший образ
    best = data.get("best_look") or {}
    look_items = [x for x in (best.get("items") or []) if _clean_text(x)]
    if look_items:
        b.section("✨ Лучший образ")
        for x in look_items:
            b.line(_clean_text(x))
        why = _finish_dot(best.get("why"))
        if why:
            b.spacer()
            b.line(why)

    # Потенциал
    potential = _finish_dot(data.get("potential"))
    if potential:
        b.section("🔮 Потенциал гардероба")
        b.line(potential)

    return b.build_stripped()


def _clean_text(value):
    return " ".join(str(value or "").split()).strip()


def _finish_dot(value):
    value = _clean_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


_STARS_5 = "★★★★★"
_STARS_EMPTY_5 = "☆☆☆☆☆"


def _stars5(score):
    """Число 1-5 → строка звёзд (5 позиций)."""
    try:
        filled = max(0, min(5, round(float(score))))
    except (TypeError, ValueError):
        filled = 0
    return _STARS_5[:filled] + _STARS_EMPTY_5[filled:]


def look_message(look_data):
    """Образ на сегодня как консультация персонального стилиста.

    look_data: {weather_intro, items[{emoji,name,why}], why_works, palette,
                style, comfort, practicality, recommendation}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    b.section("✨ Образ на сегодня")

    weather_intro = _clean_text(look_data.get("weather_intro"))
    if weather_intro:
        b.spacer()
        b.line(weather_intro)

    items = [it for it in (look_data.get("items") or []) if _clean_text(_item_name(it))]
    if items:
        b.section("Что надеваем")
        for it in items:
            emoji = _clean_text(it.get("emoji")) if isinstance(it, dict) else ""
            name = _clean_text(_item_name(it))
            why = _finish_dot(it.get("why")) if isinstance(it, dict) else ""
            b.spacer()
            b.text_line(f"{emoji} " if emoji else "")
            b.bold(name)
            b.newline()
            if why:
                b.line(why)

    why_works = _finish_dot(look_data.get("why_works"))
    if why_works:
        b.section("Почему этот образ работает")
        b.line(why_works)

    palette = _clean_text(look_data.get("palette"))
    style = _clean_text(look_data.get("style"))
    comfort = look_data.get("comfort")
    practicality = look_data.get("practicality")
    if palette or style or comfort is not None or practicality is not None:
        b.divider()
        if palette:
            b.text_line("🎨 Палитра: ")
            b.bold(palette)
            b.newline()
        if style:
            b.text_line("👔 Стиль: ")
            b.bold(style)
            b.newline()
        if comfort is not None:
            b.text_line("⭐ Комфорт: ")
            b.bold(_stars5(comfort))
            b.newline()
        if practicality is not None:
            b.text_line("🎯 Практичность: ")
            b.bold(_stars5(practicality))
            b.newline()

    recommendation = _finish_dot(look_data.get("recommendation"))
    if recommendation:
        b.spacer()
        b.add(recommendation, MessageEntity.ITALIC)

    return b.build_stripped()


def _item_name(it):
    return it.get("name") if isinstance(it, dict) else it


def _wardrobe_verdict(total):
    """Живая оценка наполненности шкафа, без канцелярита."""
    if total <= 0:
        return "Пусто — самое время начать."
    if total < 6:
        return "Вещей пока мало для разнообразных образов."
    if total < 15:
        return "База есть, можно уже собирать образы."
    return "Шкаф заполнен хорошо — есть, из чего выбирать."


def home_screen(total, zone_counts, zone_order, zone_emoji, params_filled, missing):
    """Главный экран раздела «Гардероб»: польза, состояние шкафа, действия."""
    b = MessageBuilder()
    b.text_line("👟 ")
    b.bold("Гардероб")
    b.newline()
    b.spacer()
    b.line("Каждое утро — точный образ по погоде, без лишних раздумий у шкафа.")

    b.spacer()
    if total <= 0:
        b.line("В шкафу пока нет вещей.")
        b.spacer()
        b.line("Добавь первые вещи — и я подберу образ, разберу гардероб и подскажу, что докупить.")
    else:
        b.text_line(f"👕 В шкафу {total} " + _pluralize_items(total))
        b.newline()
        b.line(_wardrobe_verdict(total))
        b.spacer()
        for z in zone_order:
            n = zone_counts.get(z, 0)
            if n > 0:
                b.bullet(f"{zone_emoji.get(z, '•')} {z} — {n}")

    if missing:
        b.spacer()
        b.line("Чуть точнее образы будут, если добавить:")
        for label in missing:
            b.bullet(label)

    return b.build_stripped()


def _pluralize_items(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "вещь"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "вещи"
    return "вещей"


def entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Что важно:"):
    b = MessageBuilder()
    b.section(_clean_text(title).rstrip(".:"))

    summary = _finish_dot(summary)
    if summary:
        b.spacer()
        b.line(summary)

    quote = _finish_dot(quote)
    if quote:
        b.spacer()
        b.quote(quote)
        b.newline()

    clean_bullets = [_finish_dot(x) for x in (bullets or []) if _clean_text(x)]
    if clean_bullets:
        b.section(_clean_text(bullet_label).rstrip(":") + ":")
        b.line("\n".join(f"- {x}" for x in clean_bullets))

    final = _finish_dot(final)
    if final:
        b.spacer()
        b.line(final)

    return b.build_stripped()


def zone_picker_screen():
    b = MessageBuilder()
    b.section("🧹 Что удалить")
    b.line("Выбери категорию.")
    return b.build_stripped()


def subcat_picker_screen(zone):
    b = MessageBuilder()
    b.section(f"🧹 {_clean_text(zone)}")
    b.line("Выбери подкатегорию.")
    return b.build_stripped()
