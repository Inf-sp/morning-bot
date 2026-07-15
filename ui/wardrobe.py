from .builder import MessageBuilder
from .constants import ui_label
from wardrobe_model import public_zone_name


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
    b.section("👟 Гардероб · Образ на сегодня")

    intro = _finish_dot(look_data.get("weather_intro"))
    if intro:
        b.spacer()
        b.italic(intro)
        b.newline()

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
        b.labeled_line("Почему работает", _finish_dot(reasons[0]))

    final_text = _finish_dot(look_data.get("final_text") or look_data.get("weather_decision"))
    if final_text:
        b.spacer()
        b.labeled_line(look_data.get("final_heading") or "Образ готов", final_text)

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
    несёт эмодзи кнопки «🧐 Оценка», из которой пользователь сюда попал.

    data: {item, verdict, why[], have_count, have_category, similar_count,
           reconsider_if, alternative}
    """
    data = data or {}
    b = MessageBuilder()
    b.section("🧐 Оценка вещи")

    verdict = _clean_text(data.get("verdict"))
    if verdict:
        b.spacer()
        b.labeled_line("Вердикт", _finish_dot(verdict))

    why = [_finish_dot(x) for x in (data.get("why") or []) if _clean_text(x)]
    if why:
        b.section("Почему:")
        b.line("\n".join(f"- {x}" for x in why[:3]))

    wear_with = [_finish_dot(x) for x in (data.get("wear_with") or []) if _clean_text(x)]
    if wear_with:
        b.section("С чем носить:")
        b.line("\n".join(f"- {x}" for x in wear_with[:3]))

    outcome = _finish_dot(data.get("outcome"))
    if outcome:
        b.spacer()
        b.labeled_line("Итог", outcome)

    return b.build_stripped()


def zone_picker_screen():
    b = MessageBuilder()
    b.section(ui_label("delete", "Что удалить"))
    b.line("Выбери категорию.")
    return b.build_stripped()


def wardrobe_home_screen(total):
    b = MessageBuilder()
    b.section(f"👕 Мой шкаф · {total} {_pluralize_items(total)}")
    b.line("Выбери категорию.")
    return b.build_stripped()


def subcat_picker_screen(zone):
    b = MessageBuilder()
    b.section(_clean_text(zone))
    b.line("Выбери подкатегорию.")
    return b.build_stripped()


def category_screen(zone, items):
    b = MessageBuilder()
    b.section(f"{_clean_text(zone)} · {len(items)}")
    if items:
        b.spacer()
        for index, item in enumerate(items, 1):
            b.line(f"{index}. {_clean_text(_item_display(item))}")
    return b.build_stripped()


def item_card(item):
    item = item or {}
    b = MessageBuilder()
    b.section(_clean_text(item.get("name")) or "Вещь")
    b.spacer()
    b.labeled_line("Категория", _lower_first(public_zone_name(item.get("zone"))))
    if item.get("color"):
        b.labeled_line("Цвет", item["color"])
    b.labeled_line("Тепло", item.get("warmth") or "обычные")
    if item.get("material"):
        b.labeled_line("Материал", item["material"])
    if item.get("length"):
        b.labeled_line("Длина", item["length"])
    if item.get("fit"):
        b.labeled_line("Посадка", item["fit"])
    if item.get("style"):
        b.labeled_line("Стиль", str(item["style"]).replace("/", " · "))
    return b.build_stripped()


def add_preview(item, remaining=0):
    item = item or {}
    b = MessageBuilder()
    b.section("Добавить вещь?")
    b.spacer()
    b.bold(_clean_text(item.get("name")) or "Вещь")
    b.newline()
    b.spacer()
    b.labeled_line("Категория", _lower_first(public_zone_name(item.get("zone"))))
    if item.get("color"):
        b.labeled_line("Цвет", item["color"])
    b.labeled_line("Тепло", item.get("warmth") or "обычные")
    if item.get("material"):
        b.labeled_line("Материал", item["material"])
    if item.get("length"):
        b.labeled_line("Длина", item["length"])
    if item.get("rain_ok"):
        b.labeled_line("Дождь", "подходит")
    if item.get("wind_ok"):
        b.labeled_line("Ветер", "защищает")
    if remaining:
        b.line(f"После этой останется: {remaining}.")
    return b.build_stripped()


def add_batch_preview(items):
    b = MessageBuilder().section("Добавлены вещи")
    for item in items or []:
        b.spacer()
        b.bold(_clean_text(item.get("name")) or "Вещь")
        b.newline()
        details = [_lower_first(public_zone_name(item.get("zone")))]
        if item.get("color"):
            details.append(str(item["color"]))
        details.append(str(item.get("warmth") or "обычные"))
        b.line(" · ".join(details))
    return b.build_stripped()


def search_results(query, items):
    b = MessageBuilder()
    b.section("🔍 Найдено")
    b.line(f"По запросу «{_clean_text(query)}»: {len(items)}.")
    if items:
        b.spacer()
        for index, item in enumerate(items, 1):
            b.line(f"{index}. {_clean_text(_item_display(item))}")
    return b.build_stripped()


def delete_confirmation(item):
    b = MessageBuilder()
    b.section("Удалить вещь?")
    b.line(f"Удалить «{_clean_text((item or {}).get('name'))}» из шкафа?")
    return b.build_stripped()
