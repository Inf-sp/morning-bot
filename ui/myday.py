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
    b.section(f"Мой день • {header} • {city}{title_flag}")

    priorities = [p for p in (priorities or []) if p]
    if priorities:
        b.text_line("🎯 ")
        b.bold("Фокус:")
        b.line(f" {', '.join(priorities)}")

    b.section(weather_title)
    b.line(weather_line)
    if wind_title:
        b.warning(wind_title)
        b.line(wind_line)
    if humidity_title:
        b.section(humidity_title)
        b.line(humidity_line)

    if word_line:
        b.section("📚 Слово дня")
        b.line(word_line)

    if fact:
        b.section("🔬 Интересный факт")
        b.line(str(fact).strip())

    if lifehack:
        b.section("💡 База знаний")
        b.line(lifehack)

    if quote_line:
        b.section("💭 Цитата")
        b.line(quote_line)

    return b.build_stripped()
