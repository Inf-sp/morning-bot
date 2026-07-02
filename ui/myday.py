from .builder import MessageBuilder


def day_summary(
    header,
    city,
    flag="",
    priorities=None,
    weather_title="",
    weather_line="",
    wind_title="",
    wind_line="",
    humidity_title="",
    humidity_line="",
    word_line="",
    fact="",
    lifehack="",
    quote_line="",
):
    title_flag = f" {flag}" if flag else ""
    b = MessageBuilder()
    b.bold(f"Мой день • {header} • {city}{title_flag}")
    b.blank()

    priorities = [p for p in (priorities or []) if p]
    if priorities:
        b.text_line("🎯 ")
        b.bold("Фокус:")
        b.text_line(f" {', '.join(priorities)}")
        b.blank()

    b.bold(weather_title)
    b.newline()
    b.text_line(weather_line)
    b.blank()
    if wind_title:
        b.bold(wind_title)
        b.newline()
        b.text_line(wind_line)
        b.blank()
    if humidity_title:
        b.bold(humidity_title)
        b.newline()
        b.text_line(humidity_line)
        b.blank()

    if word_line:
        b.bold("📚 Слово дня")
        b.newline()
        b.text_line(word_line)
        b.blank()

    if fact:
        b.bold("🔬 Интересный факт")
        b.newline()
        b.text_line(str(fact).strip())
        b.blank()

    if lifehack:
        b.bold("💡 База знаний")
        b.newline()
        b.text_line(lifehack)

    if quote_line:
        b.blank()
        b.bold("💭 Цитата")
        b.newline()
        b.text_line(quote_line)

    msg = b.build()
    msg.text = msg.text.strip()
    return msg
