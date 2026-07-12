from telegram import MessageEntity

from .builder import MessageBuilder
from .constants import ui_label


def _stars(score):
    """Число 0-100 → строка звёзд (5 позиций, шаг 20)."""
    try:
        filled = max(0, min(5, round(int(score) / 20)))
    except (TypeError, ValueError):
        filled = 0
    return "⭐️" * filled


def _lower_first(text):
    return text[:1].lower() + text[1:] if text else text


def _weakness_line(wk):
    if isinstance(wk, dict):
        title = _clean_text(wk.get("title"))
        text = _clean_text(wk.get("text"))
        return _finish_dot(f"{title} — {_lower_first(text)}" if title and text else title or text)
    return _finish_dot(wk)


def _buy_line(it):
    item = _clean_text(it.get("item")) if isinstance(it, dict) else _clean_text(it)
    why = _clean_text(it.get("why")) if isinstance(it, dict) else ""
    return _finish_dot(f"{item} — {_lower_first(why)}" if item and why else item or why)


def improve_card(data):
    """Разбор гардероба — сжатая карточка для быстрого сканирования (не аудит на экран текста):
    оценка, по 2-3 пункта на блок вместо развёрнутых мини-абзацев.

    data: {score, summary, strengths[], weaknesses[{title,text}], buy[{rank,item,why}],
           avoid[], best_look{items[],why}, potential}
    """
    b = MessageBuilder()
    b.section("👕 Разбор гардероба")
    b.spacer()

    score = data.get("score")
    if score is not None:
        b.text_line(f"{_stars(score)} ")
        b.bold(f"{score}/100")
        b.newline()
    summary = _clean_text(data.get("summary"))
    if summary:
        b.quote(_finish_dot(summary))

    raw_strengths = data.get("strengths")
    strengths = [_clean_text(s) for s in (raw_strengths if isinstance(raw_strengths, list) else [])]
    strengths = [s for s in strengths if s]
    if strengths:
        b.spacer()
        b.text_line("Сильное: ")
        b.line(_finish_dot(", ".join(strengths[:2])))

    raw_weaknesses = data.get("weaknesses")
    weaknesses = raw_weaknesses if isinstance(raw_weaknesses, list) else []
    if weaknesses:
        b.spacer()
        b.line("Слабое:")
        for wk in weaknesses[:3]:
            line = _weakness_line(wk)
            if line:
                b.bullet(line)

    buy = data.get("buy")
    buy = buy if isinstance(buy, list) else []
    if buy:
        b.spacer()
        b.line("Купить:")
        for it in buy[:3]:
            line = _buy_line(it)
            if line:
                b.bullet(line)

    raw_avoid = data.get("avoid")
    avoid = [_clean_text(a) for a in (raw_avoid if isinstance(raw_avoid, list) else [])]
    avoid = [a for a in avoid if a]
    if avoid:
        b.spacer()
        b.text_line("Не брать: ")
        b.line(_finish_dot(", ".join(avoid[:2])))

    best = data.get("best_look")
    best = best if isinstance(best, dict) else {}
    raw_look_items = best.get("items")
    look_items = [_clean_text(x) for x in (raw_look_items if isinstance(raw_look_items, list) else [])]
    look_items = [x for x in look_items if x]
    if look_items:
        b.spacer()
        b.text_line("✨ Готовый образ: ")
        b.line(_finish_dot(", ".join(look_items)))

    potential = _finish_dot(data.get("potential"))
    if potential:
        b.spacer()
        b.add(potential, MessageEntity.ITALIC)

    return b.build_stripped()


def _clean_text(value):
    return " ".join(str(value or "").split()).strip()


def _finish_dot(value):
    value = _clean_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


def look_message(look_data):
    """Образ на сегодня — компактная карточка на один экран: шапка с датой и городом,
    строка погоды, вещи построчно и одно объяснение.

    look_data: {short_date, city, weather_line, items[{name,short_name}], explanation, wardrobe_total}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    header_bits = [x for x in (_clean_text(look_data.get("short_date")), _clean_text(look_data.get("city"))) if x]
    b.section(" · ".join(["👟 Гардероб", *header_bits]))

    weather_line = _clean_text(look_data.get("weather_line"))
    if weather_line:
        b.spacer()
        b.line(_finish_dot(weather_line))

    items = [_clean_text(_item_display(it)) for it in (look_data.get("items") or [])]
    items = [it for it in items if it]
    if items:
        b.spacer()
        b.bold("Надень:")
        b.newline()
        for it in items:
            b.line(f"- {it}")

    explanation = _finish_dot(look_data.get("explanation"))
    if explanation:
        b.spacer()
        b.line(explanation)

    total = look_data.get("wardrobe_total")
    if total is not None:
        b.spacer()
        b.line(f"Всего в гардеробе: {total} " + _pluralize_items(total) + ".")

    return b.build_stripped()


def _item_display(it):
    if not isinstance(it, dict):
        return it
    return it.get("short_name") or it.get("name")


def _pluralize_items(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "вещь"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "вещи"
    return "вещей"


def entity_card(title, summary="", quote="", bullets=None, final="", bullet_label="Что важно:"):
    b = MessageBuilder()
    b.section(_clean_text(title).rstrip(".:"))

    summary = _finish_dot(summary)
    if summary:
        b.spacer()
        b.line(summary)

    quote = _finish_dot(quote)
    if quote:
        b.spacer()
        b.quote(quote)
        b.newline()

    clean_bullets = [_finish_dot(x) for x in (bullets or []) if _clean_text(x)]
    if clean_bullets:
        b.section(_clean_text(bullet_label).rstrip(":") + ":")
        b.line("\n".join(f"- {x}" for x in clean_bullets))

    final = _finish_dot(final)
    if final:
        b.spacer()
        b.line(final)

    return b.build_stripped()


def zone_picker_screen():
    b = MessageBuilder()
    b.section(ui_label("delete", "Что удалить"))
    b.line("Выбери категорию.")
    return b.build_stripped()


def wardrobe_home_screen(total):
    b = MessageBuilder()
    b.section("👔 Мой гардероб")
    if total:
        b.line(f"Всего вещей: {total}. Выбери категорию.")
    else:
        b.line("Пока пусто — добавь первую вещь.")
    return b.build_stripped()


def subcat_picker_screen(zone):
    b = MessageBuilder()
    b.section(_clean_text(zone))
    b.line("Выбери подкатегорию.")
    return b.build_stripped()
