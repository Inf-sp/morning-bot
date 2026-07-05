from telegram import MessageEntity

from .builder import MessageBuilder


def _clean_text(value):
    return " ".join(str(value or "").split()).strip()


def _finish_dot(value):
    value = _clean_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


def look_message(items, intro="", add_text=""):
    b = MessageBuilder()
    b.section("✨ Образ на сегодня")
    if intro:
        b.spacer()
        b.line(intro)
    if items:
        quote = "\n".join(f"• {str(it).strip()}" for it in items if str(it).strip())
        if quote:
            b.spacer()
            b.quote(quote)
            b.newline()
    if add_text:
        b.spacer()
        b.add(add_text, MessageEntity.ITALIC)
    return b.build_stripped()


def home_screen(total, zone_counts, zone_order, zone_emoji, params_filled, missing):
    """Динамическая панель состояния раздела «Гардероб».

    total — всего вещей; zone_counts — {zone: n}; missing — список
    (emoji_label,) недостающих пунктов для точных рекомендаций.
    """
    b = MessageBuilder()
    b.text_line("👕 ")
    b.bold("Гардероб")
    b.newline()
    b.spacer()
    b.line("Одежда без хаоса: подберу образ по погоде, разберу шкаф и подскажу, что докупить.")

    b.spacer()
    if total <= 0:
        b.line("👕 В шкафу пока нет вещей.")
        b.spacer()
        b.line("Добавь несколько вещей в шкаф, и я смогу:")
        b.bullet("собирать образ на сегодня")
        b.bullet("анализировать гардероб")
        b.bullet("советовать, что стоит докупить")
        b.bullet("проверять новые покупки на совместимость")
    else:
        b.line(f"👕 В шкафу: {total} вещей")
        b.spacer()
        for z in zone_order:
            b.bullet(f"{z} — {zone_counts.get(z, 0)}")

    # Готовность раздела
    b.spacer()
    if total > 0 and params_filled:
        b.line("🟢 Гардероб готов к работе.")
    else:
        b.line("⚠️ Для более точных рекомендаций осталось заполнить:")
        for label in missing:
            b.bullet(label)

    # Готовность функций
    b.spacer()
    has_items = total > 0
    b.line(("✅ Образ на сегодня — готов" if has_items
            else "⚠️ Образ на сегодня — добавьте вещи в шкаф"))
    b.line(("✅ Разбор гардероба — готов" if has_items
            else "⚠️ Разбор гардероба — добавьте вещи в шкаф"))
    b.line(("✅ Проверка покупки — готова" if has_items
            else "⚠️ Проверка покупки — доступна (без гардероба — менее точно)"))

    return b.build_stripped()


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
