from telegram import MessageEntity

from .builder import MessageBuilder
from .constants import ui_label


def _lower_first(text):
    return text[:1].lower() + text[1:] if text else text


def improve_card(data):
    """Разбор гардероба — одна тема за показ (баланс/дубли/цвета/слои/сезон/...),
    без повтора погоды, образа дня и статистики, которые уже есть на главном
    экране раздела (см. render_wardrobe_message).

    data: {score, headline, summary, imbalance_title, imbalance, covered[],
           missing_title, missing, next_buy_title, next_buy_item, next_buy_why}
    """
    b = MessageBuilder()
    b.section("👕 Разбор гардероба")
    b.spacer()

    headline = _clean_text(data.get("headline"))
    if headline:
        b.bold(headline)
        b.newline()
        b.spacer()

    summary = _clean_text(data.get("summary"))
    if summary:
        b.line(_finish_dot(summary))

    imbalance = _clean_text(data.get("imbalance"))
    if imbalance:
        b.spacer()
        b.bold(_clean_text(data.get("imbalance_title")) or "Главный перекос")
        b.newline()
        b.line(_finish_dot(imbalance))

    raw_covered = data.get("covered")
    covered = [_clean_text(c) for c in (raw_covered if isinstance(raw_covered, list) else [])]
    covered = [c for c in covered if c]
    if covered:
        b.spacer()
        b.bold("Что уже закрыто")
        b.newline()
        b.line(" · ".join(covered[:4]))

    missing = _clean_text(data.get("missing"))
    if missing:
        b.spacer()
        b.bold(_clean_text(data.get("missing_title")) or "Чего реально не хватает")
        b.newline()
        b.line(_finish_dot(missing))

    buy_item = _clean_text(data.get("next_buy_item"))
    buy_why = _clean_text(data.get("next_buy_why"))
    if buy_item:
        b.spacer()
        b.bold(_clean_text(data.get("next_buy_title")) or "Следующая разумная покупка")
        b.newline()
        b.line(_finish_dot(f"{buy_item} — {_lower_first(buy_why)}" if buy_why else buy_item))

    return b.build_stripped()


def _clean_text(value):
    return " ".join(str(value or "").split()).strip()


def _finish_dot(value):
    value = _clean_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


def render_wardrobe_message(look_data):
    """Образ на сегодня — компактная карточка: шапка с датой и городом, строка
    погоды, состав образа одной строкой, причины подбора, совет по стилю и
    опциональный инсайт по истории образов.

    look_data: {short_date, city, weather_line, items[{name}], reasons[], style_tip, insight}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    header_bits = [x for x in (_clean_text(look_data.get("short_date")), _clean_text(look_data.get("city"))) if x]
    b.section(" · ".join(["👟 Гардероб", *header_bits]))

    weather_line = _clean_text(look_data.get("weather_line"))
    if weather_line:
        b.spacer()
        b.line(weather_line)

    items = [_clean_text(_item_display(it)) for it in (look_data.get("items") or [])]
    items = [it for it in items if it]
    if items:
        b.spacer()
        b.bold("Образ дня:")
        b.newline()
        b.line(" · ".join(items))

    reasons = [_finish_dot(r) for r in (look_data.get("reasons") or []) if _clean_text(r)]
    if reasons:
        b.spacer()
        b.bold("Почему сегодня")
        b.newline()
        for r in reasons[:3]:
            b.line(f"- {r}")

    tip = _finish_dot(look_data.get("style_tip"))
    if tip:
        b.spacer()
        b.text_line("Совет по стилю: ")
        b.line(tip)

    insight = _finish_dot(look_data.get("insight"))
    if insight:
        b.spacer()
        b.line(insight)

    return b.build_stripped()


# Старое имя — на случай, если что-то ещё зовёт карточку образа по прежней сигнатуре.
look_message = render_wardrobe_message


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
    b.section("🎚️ Настройки гардероба")
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
