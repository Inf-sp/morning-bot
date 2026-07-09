from telegram import MessageEntity

from .builder import MessageBuilder
from .constants import ui_label


def _stars(score):
    """Число 0-100 → строка звёзд (5 позиций, шаг 20)."""
    try:
        filled = max(0, min(5, round(int(score) / 20)))
    except (TypeError, ValueError):
        filled = 0
    return "⭐️" * filled


def improve_card(data):
    """Разбор гардероба как консультация стилиста.

    data: {score, summary, strengths[], weaknesses[{title,text}], buy[{rank,item,why}],
           avoid[], best_look{items[],why}, potential}
    """
    b = MessageBuilder()
    b.section("👕 Разбор гардероба")

    # Общая оценка
    score = data.get("score")
    b.section(ui_label("assessment", "Общая оценка"))
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
        b.section(ui_label("shopping", "Что купить в первую очередь"))
        for i, it in enumerate(buy[:5]):
            item = _clean_text(it.get("item")) if isinstance(it, dict) else _clean_text(it)
            why = _finish_dot(it.get("why")) if isinstance(it, dict) else ""
            b.spacer()
            b.text_line(f"{i + 1}. ")
            b.bold(item)
            b.newline()
            if why:
                b.line(why)

    # Чего не покупать
    avoid = [a for a in (data.get("avoid") or []) if _clean_text(a)]
    if avoid:
        b.section(ui_label("avoid", "Что покупать не стоит"))
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
        b.section(ui_label("potential", "Потенциал гардероба"))
        b.line(potential)

    return b.build_stripped()


def _clean_text(value):
    return " ".join(str(value or "").split()).strip()


def _finish_dot(value):
    value = _clean_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


def look_message(look_data):
    """Образ на сегодня — максимально короткая карточка для быстрого решения:
    одна строка погоды, простой список вещей без эмодзи, одна итоговая фраза.

    look_data: {weather_line, items[{name,short_name}], summary, recommendation}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    b.section("✨ Образ на сегодня")

    weather_line = _clean_text(look_data.get("weather_line"))
    if weather_line:
        b.spacer()
        b.line(_finish_dot(weather_line))

    items = [_clean_text(_item_display(it)) for it in (look_data.get("items") or [])]
    items = [it for it in items if it]
    if items:
        b.spacer()
        b.line("Надеть:")
        for it in items:
            b.bullet(it[:1].lower() + it[1:] if it else it)

    summary = _clean_text(look_data.get("summary"))
    if summary:
        b.spacer()
        b.text_line("Коротко: ")
        b.text_line(_finish_dot(summary))
        b.newline()

    recommendation = _finish_dot(look_data.get("recommendation"))
    if recommendation:
        b.spacer()
        b.add(recommendation, MessageEntity.ITALIC)

    return b.build_stripped()


def _item_display(it):
    if not isinstance(it, dict):
        return it
    return it.get("short_name") or it.get("name")


def _wardrobe_verdict(total):
    """Живая оценка наполненности шкафа, без канцелярита."""
    if total <= 0:
        return "В шкафу пока пусто."
    if total < 10:
        return "База уже есть, но для точных образов нужно добавить ещё верх, низ и обувь."
    if total < 30:
        return "Шкаф уже рабочий - можно собирать базовые образы."
    return "Шкаф заполнен хорошо - есть, из чего собирать образы."


def home_screen(total, zone_counts, zone_order):
    """Главный экран раздела «Гардероб»: польза, состояние шкафа, действия."""
    b = MessageBuilder()
    b.text_line("👟 ")
    b.bold("Гардероб")
    b.newline()
    b.spacer()
    b.line("Образ на сегодня, разбор шкафа и проверка покупки.")

    b.spacer()
    if total <= 0:
        b.quote(_wardrobe_verdict(total))
        b.spacer()
        b.line("Добавь несколько вещей, и бот сможет собирать образы под погоду.")
    else:
        b.text_line(f"В шкафу {total} " + _pluralize_items(total) + " · ")
        b.text_line(_wardrobe_verdict(total).rstrip("."))
        b.newline()
        b.spacer()
        for z in zone_order:
            n = zone_counts.get(z, 0)
            if n > 0:
                b.metric(z, n, width=24)

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
    b.section(ui_label("delete", "Что удалить"))
    b.line("Выбери категорию.")
    return b.build_stripped()


def subcat_picker_screen(zone):
    b = MessageBuilder()
    b.section(_clean_text(zone))
    b.line("Выбери подкатегорию.")
    return b.build_stripped()
