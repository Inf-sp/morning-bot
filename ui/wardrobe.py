from .builder import MessageBuilder
from .constants import ui_label
from wardrobe_model import public_zone_name


def _lower_first(text):
    return text[:1].lower() + text[1:] if text else text


def _upper_first(text):
    """Поднимает первую букву названия, не меняя регистр остальной строки."""
    for index, char in enumerate(text or ""):
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1:]
    return text


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
    """Образ на сегодня: погодное решение, вещи и один практический совет.
    Без повторяющих объяснений и итогового подтверждения готовности образа.

    look_data: {primary_style, weather_intro, items[{name}], style_tip}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    header = "👕 Образ на сегодня"
    primary_style = _clean_text(look_data.get("primary_style"))
    if primary_style:
        header += f" · {primary_style}"
    b.section(header)

    intro = _finish_dot(look_data.get("weather_intro"))
    if intro:
        b.spacer()
        b.line(intro)

    items = [_upper_first(_clean_text(_item_display(it))) for it in (look_data.get("items") or [])]
    items = [it for it in items if it]
    if items:
        b.spacer()
        b.labeled_line("Надень")
        for it in items:
            b.bullet(it)

    tip = _finish_dot(look_data.get("style_tip"))
    if tip:
        b.spacer()
        b.text_line("💡 ")
        b.labeled_line("Полезно", tip)

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
    """Проверка покупки отвечает на один вопрос: стоит ли добавлять вещь в шкаф.

    data: {verdict, fits_count, duplicates, closes_gap, why, wear_with[]}
    """
    data = data or {}
    b = MessageBuilder()
    b.section("🧐 Оценка покупки")

    verdict = _clean_text(data.get("verdict"))
    if verdict:
        b.spacer()
        verdict_labels = {
            "брать": "Брать",
            "можно брать": "Можно брать",
            "скорее не брать": "Скорее не брать",
            "не брать": "Не брать",
        }
        b.bold(verdict_labels.get(verdict.casefold(), _upper_first(verdict).rstrip(".")))
        b.newline()

    fits_count = data.get("fits_count")
    if isinstance(fits_count, int) and not isinstance(fits_count, bool) and fits_count >= 0:
        if fits_count == 0:
            b.labeled_line("Сочетается", "ни с одной вещью из текущего шкафа")
        else:
            b.labeled_line("Сочетается", f"примерно с {fits_count} {_pluralize_dative_items(fits_count)}")
    elif fits_count == "недостаточно данных":
        b.labeled_line("Сочетается", "недостаточно данных")

    duplicates = _clean_text(data.get("duplicates"))
    if duplicates:
        b.labeled_line("Дублирует", _finish_dot(duplicates))

    closes_gap = _clean_text(data.get("closes_gap"))
    if closes_gap:
        b.labeled_line("Закрывает пробел", _finish_dot(closes_gap))

    why = _finish_dot(data.get("why"))
    why = why.replace("пользователя", "").replace("запретах", "предпочтениях")
    if why:
        b.spacer()
        b.labeled_line("Почему", why)

    wear_with = [_finish_dot(x) for x in (data.get("wear_with") or []) if _clean_text(x)]
    if wear_with:
        b.section("Как носить:")
        b.line("\n".join(f"- {x}" for x in wear_with[:2]))

    return b.build_stripped()


def _pluralize_dative_items(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "вещи"
    return "вещам"


def zone_picker_screen():
    b = MessageBuilder()
    b.section(ui_label("delete", "Что удалить"))
    b.line("Выбери категорию.")
    return b.build_stripped()


def wardrobe_home_screen(total):
    b = MessageBuilder()
    b.section(f"👕 Мой шкаф · {total} {_pluralize_items(total)}")
    b.line("Выбери категорию:")
    return b.build_stripped()


def subcat_picker_screen(zone):
    b = MessageBuilder()
    b.section(_clean_text(zone))
    b.line("Выбери подкатегорию.")
    return b.build_stripped()


def category_screen(zone, items):
    b = MessageBuilder()
    b.section(f"👕 {_clean_text(zone)} · {len(items)} {_pluralize_items(len(items))}")
    if items:
        b.spacer()
        for index, item in enumerate(items, 1):
            b.line(f"• {_clean_text(_item_display(item))}")
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


def _success_item_title(item):
    name = _upper_first(_clean_text((item or {}).get("name")) or "Вещь")
    brand = _clean_text((item or {}).get("brand"))
    if brand and brand.casefold() not in name.casefold():
        return f"{name} {brand}"
    return name


def _success_item_details(item):
    """Короткие свойства для подтверждения сохранения, без словарных полей."""
    item = item or {}
    title = _success_item_title(item).casefold()
    details = []

    def add(value, *, is_color=False):
        value = _clean_text(value)
        color_stem = value.casefold()
        if is_color:
            for ending in ("ыми", "ими", "ого", "ему", "ому", "ые", "ие", "ый", "ий", "ая", "яя", "ое", "ее"):
                if color_stem.endswith(ending):
                    color_stem = color_stem[:-len(ending)]
                    break
        already_in_title = value.casefold() in title or (is_color and len(color_stem) >= 3 and color_stem in title)
        if value and not already_in_title and value.casefold() not in {
            detail.casefold() for detail in details
        }:
            details.append(value)

    # Цвет обычно уже часть естественного названия. Если нет — он остаётся полезной
    # характеристикой, но не повторяется.
    add(item.get("color"), is_color=True)
    add(item.get("length"))
    add(item.get("material"))
    fit = _clean_text(item.get("fit"))
    add({
        "свободная": "свободный крой",
        "прямая": "прямой крой",
        "приталенная": "приталенный крой",
    }.get(fit.casefold(), fit))

    warmth = _clean_text(item.get("warmth"))
    if warmth and warmth != "обычные":
        add("лёгкая ткань" if warmth == "лёгкие" and item.get("zone") == "Верх" else warmth)
    if item.get("rain_ok"):
        add("защита от дождя")
    if item.get("wind_ok"):
        add("защита от ветра")
    return details[:3]


def add_success(item):
    """Подтверждение после фактического сохранения одной вещи в шкаф."""
    b = MessageBuilder()
    b.line("✅ Вещь добавлена в «Мой шкаф»")
    b.spacer()
    b.bold(_success_item_title(item))
    details = _success_item_details(item)
    if details:
        b.text_line(" · " + " · ".join(details))
    return b.build_stripped()


def add_batch_success(items):
    b = MessageBuilder()
    b.line("✅ Вещи добавлены в «Мой шкаф»")
    for item in items or []:
        b.spacer()
        b.bold(_success_item_title(item))
        details = _success_item_details(item)
        if details:
            b.text_line(" · " + " · ".join(details))
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
