"""Компактные Telegram-карточки раздела «😮‍💨 Мысли»."""

from .builder import MessageBuilder


def home(items):
    b = MessageBuilder()
    b.section("😮‍💨 Мысли")
    b.line("Не держи всё в голове.")
    b.line("Напиши мысль, задачу или тревогу одним сообщением.")
    b.spacer()
    b.bold("Сейчас в голове:")
    b.newline()
    if items:
        for item in items:
            b.bullet(item.get("text", ""))
    else:
        b.line("Список пуст.")
    return b.build_stripped()


def cleared_home():
    b = MessageBuilder()
    b.section("😮‍💨 Мысли")
    b.line("Голова немного свободнее.")
    b.line("Можешь записать новую мысль, задачу или тревогу.")
    return b.build_stripped()


def review(summary, analysis, next_step):
    b = MessageBuilder()
    b.section("🧐 Разбор мыслей")
    if summary:
        b.line(summary)
    if analysis:
        b.spacer()
        b.bold("Что происходит:")
        b.newline()
        for item in analysis[:3]:
            b.bullet(item)
    if next_step:
        b.spacer()
        b.bold("Сейчас сделай одно:")
        b.newline()
        b.line(next_step)
    return b.build_stripped()


def clear_confirmation():
    b = MessageBuilder()
    b.section("Очистить все записи?")
    return b.build_stripped()


def medical():
    b = MessageBuilder()
    b.section("🩺 Это вопрос о здоровье")
    b.line("Симптомы, лекарства и лечение лучше разобрать во вкладке «Врач».")
    return b.build_stripped()


def day_reminder():
    b = MessageBuilder()
    b.section("😮‍💨 Есть что выгрузить?")
    b.line("Запиши то, что занимает голову, одним сообщением — оно сохранится как одна мысль.")
    b.line("Разбирать сейчас не нужно.")
    return b.build_stripped()


def evening(open_count):
    b = MessageBuilder()
    b.section("😌 Закроем день")
    b.line(f"Осталось записей: {open_count}")
    b.line("Разберём или оставим до завтра?")
    return b.build_stripped()
