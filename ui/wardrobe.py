from telegram import MessageEntity

from .builder import MessageBuilder
from .constants import ui_label


def _lower_first(text):
    return text[:1].lower() + text[1:] if text else text


def improve_card(data):
    """Разбор шкафа — капсульный аудит всего гардероба сразу: что уже работает,
    что выбивается, что менять первым, что пока не покупать и как выглядит
    капсула после следующей замены. Без повтора погоды, образа дня и статистики,
    которые уже есть на главном экране раздела (см. render_wardrobe_message).

    data: {headline, works[], clashes[], fix_first[], skip_buying, next_capsule}
    """
    b = MessageBuilder()
    b.section("✂️ Разбор шкафа")
    b.spacer()

    headline = _clean_text(data.get("headline"))
    if headline:
        b.labeled_line("Главный вывод", _finish_dot(headline))

    works = [_finish_dot(x) for x in (data.get("works") or []) if _clean_text(x)]
    if works:
        b.section("Что работает")
        b.line("\n".join(f"- {x}" for x in works[:5]))

    clashes = [_finish_dot(x) for x in (data.get("clashes") or []) if _clean_text(x)]
    if clashes:
        b.section("Что выбивается")
        b.line("\n".join(f"- {x}" for x in clashes[:5]))

    fix_first = [_finish_dot(x) for x in (data.get("fix_first") or []) if _clean_text(x)]
    if fix_first:
        b.section("Что менять первым")
        b.line("\n".join(f"{i}. {x}" for i, x in enumerate(fix_first[:3], 1)))

    skip_buying = _finish_dot(data.get("skip_buying"))
    if skip_buying:
        b.spacer()
        b.labeled_line("Пока не покупать", skip_buying)

    next_capsule = _clean_text(data.get("next_capsule"))
    if next_capsule:
        b.spacer()
        b.labeled_line("После следующей замены", _finish_dot(next_capsule))

    return b.build_stripped()


def _clean_text(value):
    return " ".join(str(value or "").split()).strip()


def _finish_dot(value):
    value = _clean_text(value)
    if value and value[-1] not in ".!?…":
        return value + "."
    return value


def render_wardrobe_message(look_data):
    """Образ на сегодня — компактная карточка: одно погодное решение вместо цифр,
    вещи списком по одной на строку, до трёх причин подбора, совет по стилю и
    опциональный инсайт по истории образов. Без даты и города в шапке — они уже
    есть в "Мой день".

    look_data: {weather_decision, items[{name}], reasons[], style_tip, insight}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    b.section("👟 Гардероб")

    items = [_clean_text(_item_display(it)) for it in (look_data.get("items") or [])]
    items = [it for it in items if it]
    if items:
        b.spacer()
        b.labeled_line("Надень")
        for it in items:
            b.line(f"- {it}")

    tip = _finish_dot(look_data.get("style_tip"))
    if tip:
        b.spacer()
        b.labeled_line("Как носить", tip)

    reasons = [_clean_text(r).rstrip(".!?") for r in (look_data.get("reasons") or []) if _clean_text(r)]
    if reasons:
        b.spacer()
        b.labeled_line("Почему работает", _finish_dot(" и ".join(reasons[:3])))

    decision = _finish_dot(look_data.get("weather_decision"))
    if decision:
        b.spacer()
        b.labeled_line("Образ готов", decision)

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


def purchase_check_card(data):
    """Оценка покупки: до трёх цифр, влияющих на решение (сколько уже есть, сколько
    похоже, сколько новых сочетаний даст покупка), не голые комплименты. Заголовок
    несёт эмодзи кнопки «🔍 Оценка», из которой пользователь сюда попал.

    data: {item, verdict, why[], have_count, have_category, similar_count,
           reconsider_if, alternative}
    """
    data = data or {}
    b = MessageBuilder()
    b.section("🔍 Оценка")
    b.spacer()
    b.bold(_clean_text(data.get("item")))

    verdict = _clean_text(data.get("verdict"))
    if verdict:
        b.spacer()
        b.labeled_line("Вердикт", _finish_dot(verdict))

    why = [_finish_dot(x) for x in (data.get("why") or []) if _clean_text(x)]
    if why:
        b.section("Почему:")
        b.line("\n".join(f"- {x}" for x in why[:3]))

    have_category = _clean_text(data.get("have_category"))
    have_count = data.get("have_count")
    if have_category and have_count is not None:
        similar = data.get("similar_count")
        similar_bit = f" · {similar} похожих по назначению" if similar else ""
        b.spacer()
        b.labeled_line("У тебя уже", _finish_dot(f"{have_count} {have_category}{similar_bit}"))

    reconsider_if = _finish_dot(data.get("reconsider_if"))
    if reconsider_if:
        b.spacer()
        b.labeled_line("Рассмотреть можно, если", reconsider_if)

    alternative = _clean_text(data.get("alternative"))
    if alternative:
        b.spacer()
        b.labeled_line("Лучше искать", _finish_dot(alternative))

    return b.build_stripped()


def zone_picker_screen():
    b = MessageBuilder()
    b.section(ui_label("delete", "Что удалить"))
    b.line("Выбери категорию.")
    return b.build_stripped()


def wardrobe_home_screen(total):
    b = MessageBuilder()
    b.section("👕 Мой гардероб")
    if total:
        b.label("Всего вещей", total, lowercase=False)
        b.line(". Выбери категорию.")
    else:
        b.line("Пока пусто — добавь первую вещь.")
    return b.build_stripped()


def subcat_picker_screen(zone):
    b = MessageBuilder()
    b.section(_clean_text(zone))
    b.line("Выбери подкатегорию.")
    return b.build_stripped()
