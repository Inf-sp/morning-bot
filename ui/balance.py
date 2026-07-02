import re

from .builder import MessageBuilder
from .builder import MessageSpec
from util import esc, cap_sentence


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
    b.bold(clean_card_text(title).rstrip(".:"))

    summary = finish_dot(summary)
    if summary:
        b.blank().text_line(summary)

    quote = finish_dot(quote)
    if quote:
        b.blank().quote(quote)

    clean_bullets = [finish_dot(x) for x in (bullets or []) if clean_card_text(x)]
    if clean_bullets:
        b.blank().bold(clean_card_text(bullet_label).rstrip(":") + ":")
        b.newline().text_line("\n".join(f"- {x}" for x in clean_bullets))

    final = finish_dot(final)
    if final:
        b.blank().text_line(final)

    msg = b.build()
    msg.text = msg.text.rstrip()
    return msg


def worries_diary(worries):
    lines = [
        "📓 <b>Дневник тревог</b>",
        "",
        "Сюда выгружай всё, что крутится в голове. Не анализируй - просто запиши.",
        "Каждую тревогу с новой строки. Вечером проверим, что было фактами, а что шумом.",
        "",
    ]
    if worries:
        lines.append("<b>Тревоги за сегодня:</b>")
        for worry in worries:
            lines.append(f"• {esc(worry['text'])}")
        lines += ["", "Напиши новые мысли сообщением или очисти список 👇"]
    else:
        lines.append("Пока пусто. Напиши тревоги одним сообщением.")
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def evening_review_empty():
    return MessageSpec(
        text=(
            "🥸 <b>Вечерний разбор</b>\n\n"
            "Сегодня тревог не записано. Если что-то крутится - выгрузи сейчас, каждую с новой строки."
        ),
        parse_mode="HTML",
    )


def evening_review(worries, items=None, summary=""):
    lines = ["🥸 <b>Вечерний разбор</b>", "", "<b>Сегодня тебя беспокоили:</b>"]
    items = items or []
    for idx, worry in enumerate(worries):
        lines.append(f"• {esc(worry['text'])}")
        note = ""
        if idx < len(items) and isinstance(items[idx], dict):
            note = (items[idx].get("note") or "").strip()
        if note:
            lines.append(f"<i>{esc(note)}</i>")
    if summary:
        lines += ["", "<b>Итог дня:</b>", esc(cap_sentence(summary))]
    return MessageSpec(text="\n".join(lines), parse_mode="HTML")


def worries_cleared():
    return MessageSpec(text="✅ Дневник тревог очищен. Приятного настроения!")


def worries_saved(count):
    return MessageSpec(text=f"📝 Записал в дневник тревоги: +{count}. Вечером проверим, что реально случилось.")
