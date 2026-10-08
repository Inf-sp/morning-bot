from .builder import MessageBuilder, MessageSpec


def digest(city, items) -> MessageSpec:
    """Итог дня: заголовок, затем по строке на новость — ссылка на оригинал и источник."""
    b = MessageBuilder()
    b.section(f"📰 Главное за день · {city}" if city else "📰 Главное за день")
    b.spacer()
    for item in items:
        b.text_line("• ")
        b.link(item["title"], item["link"])
        b.text_line(f" — {item['source']}")
        b.newline()
    return b.build_stripped()
