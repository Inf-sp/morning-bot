import re

from .builder import MessageBuilder
from .builder import MessageSpec
from .constants import choose_label, ui_label
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


def worries_diary(worries):
    """Совместимый рендер старого вызова до перехода на ui.thoughts."""
    b = MessageBuilder()
    b.section(ui_label("worry_diary", "Мысли"))
    b.line("Не держи всё в голове.")
    b.line("Напиши мысль, задачу или тревогу одним сообщением.")
    b.spacer()
    b.line(f"Сегодня записано: {len(worries)}")
    b.line(f"Осталось разобрать: {len(worries)}")
    return b.build_stripped()


def evening_review(worries, items=None, summary="", principle="", analysis_failed=False):
    b = MessageBuilder()
    b.text_line("🥸 ")
    b.bold("Закроем день")
    b.newline()
    b.spacer()
    b.labeled_line("Сегодня в голове")
    for worry in worries:
        b.bullet(worry["text"])

    items = items or []
    if items:
        b.spacer()
        b.bold("Разбор мыслей")
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
                b.labeled_line("Факт", cap_sentence(fact))
            if assumption:
                b.labeled_line("Предположение", cap_sentence(assumption))

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
    return MessageSpec(text="Массовое удаление записей недоступно.")


def worries_saved(count):
    return MessageSpec(text=f"✅ Сохранено: +{count}.")


def health_principles(selected_count):
    b = MessageBuilder()
    b.section(choose_label("Выбрать принципы"))
    b.line("Выбери, что важно поддерживать сейчас.")
    b.spacer()
    if selected_count:
        b.line(f"Выбрано: {selected_count}.")
    else:
        b.line("Пока ничего не выбрано.")
    return b.build_stripped()
