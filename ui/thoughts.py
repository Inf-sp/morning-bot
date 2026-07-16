"""Компактные Telegram-карточки раздела «😮‍💨 Мысли»."""

from .builder import MessageBuilder


def home(count_today, open_count):
    b = MessageBuilder()
    b.section("😮‍💨 Мысли")
    b.line("Не держи всё в голове.")
    b.line("Напиши мысль, задачу или тревогу одним сообщением.")
    b.spacer()
    b.line(f"Сегодня записано: {count_today}")
    b.line(f"Осталось разобрать: {open_count}")
    return b.build_stripped()


def saved(count_today):
    b = MessageBuilder()
    b.section("✅ Сохранено")
    b.line("Больше не нужно держать это в голове.")
    b.line(f"Сегодня записано: {count_today}")
    return b.build_stripped()


def inbox(items, open_count):
    b = MessageBuilder()
    b.section("😮‍💨 Что в голове")
    b.bold("Сегодня:")
    b.newline()
    if items:
        for item in items[:5]:
            b.bullet(item.get("text", ""))
        if len(items) > 5:
            b.line(f"И ещё: {len(items) - 5}")
    else:
        b.line("Пока ничего не записано.")
    b.spacer()
    b.line(f"Открыто: {open_count}")
    return b.build_stripped()


def scenario(title, body="", action="", question=""):
    b = MessageBuilder()
    b.section(title)
    if body:
        b.line(body)
    if action:
        b.spacer()
        b.line(action)
    if question:
        # Действие и вопрос составляют один короткий второй абзац.
        # Так карточка всегда укладывается в ограничение: максимум два абзаца.
        if not action:
            b.spacer()
        b.line(question)
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


def completed():
    b = MessageBuilder()
    b.section("✅ Готово")
    b.line("Эту мысль больше не нужно держать в голове.")
    return b.build_stripped()
