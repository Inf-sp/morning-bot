import re

from .builder import MessageBuilder
from .builder import MessageSpec
from .constants import ui_label
from util import cap_sentence


def clean_card_text(value):
    value = re.sub(r"<[^>]+>", "", str(value or ""))
    value = re.sub(r"^[\s\U0001F1E6-\U0001FAFF\u2600-\u27BF\u200D\uFE0F]+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def finish_dot(value):
    value = clean_card_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


def entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Рекомендации:"):
    b = MessageBuilder()
    b.section(clean_card_text(title).rstrip(".:"))

    summary = finish_dot(summary)
    if summary:
        b.spacer()
        b.line(summary)

    quote = finish_dot(quote)
    if quote:
        b.spacer()
        b.quote(quote)
        b.newline()

    clean_bullets = [finish_dot(x) for x in (bullets or []) if clean_card_text(x)]
    if clean_bullets:
        b.spacer()
        b.bold(clean_card_text(bullet_label).rstrip(":") + ":")
        b.newline()
        b.line("\n".join(f"- {x}" for x in clean_bullets))

    final = finish_dot(final)
    if final:
        b.spacer()
        b.line(final)

    return b.build_stripped()


def worries_diary(worries):
    b = MessageBuilder()
    b.section(ui_label("worry_diary", "Дневник тревог"))
    b.spacer()
    b.line("Сюда выгружай всё, что крутится в голове. Не анализируй - просто запиши.")
    b.line("Каждую тревогу с новой строки. Вечером проверим, что было фактами, а что шумом.")
    b.spacer()
    if worries:
        b.section("Тревоги за сегодня:")
        for worry in worries:
            b.bullet(worry["text"])
        b.spacer()
        b.line("Напиши новые мысли сообщением или очисти список.")
    else:
        b.line("Пока пусто. Напиши тревоги одним сообщением.")
    return b.build_stripped()


def evening_review_empty():
    b = MessageBuilder()
    b.section("Вечерний разбор")
    b.spacer()
    b.line("Сегодня тревог не записано. Если что-то крутится - выгрузи сейчас, каждую с новой строки.")
    return b.build_stripped()


def evening_review(worries, items=None, summary=""):
    b = MessageBuilder()
    b.section("Вечерний разбор")
    b.spacer()
    b.section("Сегодня тебя беспокоили:")
    items = items or []
    for idx, worry in enumerate(worries):
        b.bullet(worry["text"])
        note = ""
        if idx < len(items) and isinstance(items[idx], dict):
            note = (items[idx].get("note") or "").strip()
        if note:
            b.italic(note)
            b.newline()
    if summary:
        b.spacer()
        b.section("Итог дня:")
        b.line(cap_sentence(summary))
    return b.build_stripped()


def worries_cleared():
    return MessageSpec(text="✅ Дневник тревог очищен. Приятного настроения!")


def worries_saved(count):
    return MessageSpec(text=f"Записал в дневник тревоги: +{count}. Вечером проверим, что реально случилось.")
