"""Компактные Telegram-карточки раздела «😮‍💨 Мысли»."""

from .builder import MessageBuilder


def home(count_today, items, notice_title="", notice_body=""):
    b = MessageBuilder()
    if notice_title:
        b.section(notice_title)
        if notice_body:
            b.line(notice_body)
        b.spacer()
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
    b.spacer()
    b.line(f"Сегодня записано: {count_today}")
    return b.build_stripped()


def review(summary, actions, reframe=""):
    b = MessageBuilder()
    b.section("✨ Разбор мыслей")
    if summary:
        b.line(summary)
    if actions:
        b.spacer()
        b.bold("Что можно сделать:")
        b.newline()
        for action in actions[:3]:
            b.bullet(action)
    if reframe:
        b.spacer()
        b.line(reframe)
    return b.build_stripped()


def clear_confirmation():
    b = MessageBuilder()
    b.section("Очистить мысли?")
    b.line("Все записи из текущего списка будут убраны.")
    return b.build_stripped()


def medical():
    b = MessageBuilder()
    b.section("🩺 Это вопрос о здоровье")
    b.line("Симптомы, лекарства и лечение лучше разобрать во вкладке «Врач».")
    return b.build_stripped()


def day_reminder():
    b = MessageBuilder()
    b.section("😮‍💨 Есть что выгрузить?")
    b.line("Запиши то, что занимает голову, через запятую или с новой строки — каждую мысль отдельно.")
    b.line("Разбирать сейчас не нужно.")
    return b.build_stripped()


def evening(open_count):
    b = MessageBuilder()
    b.section("😌 Закроем день")
    b.line(f"Осталось записей: {open_count}")
    b.line("Разберём или оставим до завтра?")
    return b.build_stripped()
