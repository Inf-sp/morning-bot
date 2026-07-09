from .builder import MessageBuilder
from .constants import ui_label


def day_summary(
    header,
    city,
    flag="",
    priorities=None,
    weather_title="",
    weather_line="",
    humidity_title="",
    humidity_line="",
    word_line="",
    word_lang="nl",
    fact="",
    lifehack="",
    quote_text="",
    quote_author="",
):
    title_flag = f" {flag}" if flag else ""
    b = MessageBuilder()
    b.section(f"Мой день • {header} • {city}{title_flag}")

    b.section(weather_title)
    b.line(weather_line)
    if humidity_title:
        b.section(humidity_title)
        b.line(humidity_line)

    if word_line:
        word_title = "Нидерландское слово дня" if word_lang == "nl" else "Английское слово дня"
        b.section(ui_label("learning", word_title))
        b.line(word_line)

    if fact:
        b.section(ui_label("interesting", "Интересный факт"))
        b.line(str(fact).strip())

    if lifehack:
        b.section(ui_label("knowledge", "База знаний"))
        b.line(lifehack)

    if quote_text:
        b.section(ui_label("quote", "Цитата"))
        b.italic(f"«{quote_text}»")
        b.newline()
        if quote_author:
            b.text_line(f"— {quote_author}")
            b.newline()

    return b.build_stripped()
