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


def entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Рекомендации:", emoji=""):
    b = MessageBuilder()
    heading = clean_card_text(title).rstrip(".:")
    b.section(f"{emoji} {heading}" if emoji else heading)

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


def doctor_card(data):
    """Разбор симптомов: возможные причины связаны с признаками (не диагноз-заголовок),
    срочный и плановый сценарий обращения к врачу разделены на два явных блока, "Итог"
    показывается только когда даёт одно чёткое решение, а не повторяет предыдущий блок.

    data: {title, summary, causes, bullets[], urgent, plan, final, bullet_label}
    """
    data = data or {}
    b = MessageBuilder()
    b.section(f"👩🏻‍⚕️ {clean_card_text(data.get('title')).rstrip('.:') or 'Разбор симптомов'}")

    summary = finish_dot(data.get("summary"))
    if summary:
        b.spacer()
        b.text_line("Основная жалоба: ")
        b.line(summary)

    causes = finish_dot(data.get("causes"))
    if causes:
        b.spacer()
        b.text_line("Возможные причины: ")
        b.line(causes)

    clean_bullets = [finish_dot(x) for x in (data.get("bullets") or []) if clean_card_text(x)]
    if clean_bullets:
        b.spacer()
        b.bold(clean_card_text(data.get("bullet_label") or "Что сделать").rstrip(":") + ":")
        b.newline()
        b.line("\n".join(f"- {x}" for x in clean_bullets))

    urgent = finish_dot(data.get("urgent"))
    if urgent:
        b.spacer()
        b.text_line("Срочно за помощью: ")
        b.line(urgent)

    plan = finish_dot(data.get("plan"))
    if plan:
        b.spacer()
        b.text_line("Записаться к врачу: ")
        b.line(plan)

    final = finish_dot(data.get("final"))
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
        b.section(ui_label("worries", "Тревоги за сегодня:"))
        for worry in worries:
            b.bullet(worry["text"])
        b.spacer()
        b.line("Напиши новые мысли сообщением или очисти список.")
    else:
        b.line("Пока пусто. Напиши тревоги одним сообщением.")
    return b.build_stripped()


def evening_review(worries, items=None, summary="", principle="", analysis_failed=False):
    b = MessageBuilder()
    b.text_line("🥸 ")
    b.bold("Вечерний разбор")
    b.newline()
    b.spacer()
    b.line("Сегодня тебя беспокоили:")
    for worry in worries:
        b.bullet(worry["text"])

    items = items or []
    if items:
        b.spacer()
        b.bold("Разбор тревог")
        b.newline()
        for it in items:
            if not isinstance(it, dict):
                continue
            worry_text = (it.get("worry") or "").strip()
            fact = (it.get("fact") or "").strip()
            assumption = (it.get("assumption") or "").strip()
            if not worry_text:
                continue
            b.spacer()
            b.text_line("📌 ")
            b.bold(worry_text)
            b.newline()
            if fact:
                b.text_line("Факт: ")
                b.line(cap_sentence(fact))
            if assumption:
                b.text_line("Предположение: ")
                b.line(cap_sentence(assumption))

    if summary:
        b.spacer()
        b.text_line("🧠 ")
        b.bold("Итог дня")
        b.newline()
        b.line(cap_sentence(summary))
    elif analysis_failed:
        b.spacer()
        b.line("⚠️ Не удалось собрать разбор. Попробуй ещё раз чуть позже.")

    if principle:
        b.spacer()
        b.line(f"🌿 {cap_sentence(principle)}")

    return b.build_stripped()


def worries_cleared():
    return MessageSpec(text="✅ Дневник тревог очищен. Приятного настроения!")


def worries_saved(count):
    return MessageSpec(text=f"Записал в дневник тревоги: +{count}. Вечером проверим, что реально случилось.")
