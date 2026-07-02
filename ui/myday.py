from .builder import MessageSpec
from util import esc


def day_summary(
    header,
    city,
    flag="",
    priorities=None,
    weather_title="",
    weather_line="",
    humidity="",
    word_line="",
    fact="",
    lifehack="",
    quote_line="",
):
    title_flag = f" {flag}" if flag else ""
    lines = [f"<b>Мой день • {esc(header)} • {esc(city)}{title_flag}</b>", ""]

    priorities = [p for p in (priorities or []) if p]
    if priorities:
        lines += [f"🎯 <b>Фокус:</b> {esc(', '.join(priorities))}", ""]

    lines.append(f"<b>{weather_title}</b>")
    lines.append(weather_line)
    if humidity:
        lines.append(f"💧 {esc(humidity)}")
    lines.append("")

    if word_line:
        lines += ["<b>📚 Слово дня</b>", esc(word_line), ""]

    if fact:
        lines += ["<b>🔬 Интересный факт</b>", esc(str(fact).strip()), ""]

    if lifehack:
        lines += ["<b>💡 База знаний</b>", esc(lifehack)]

    if quote_line:
        lines += ["", "<b>💭 Цитата</b>", quote_line]

    return MessageSpec(text="\n".join(lines).strip(), parse_mode="HTML")
