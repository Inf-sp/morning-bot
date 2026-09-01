from .builder import MessageBuilder
from .constants import ui_label
from .news import append_weekly_news
from wardrobe_model import public_zone_name, zone_of


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


_STYLE_EMOJI = {
    "Минимализм": "👕",
    "Городской": "🧢",
    "Повседневный": "👖",
    "Скандинавский": "🧥",
    "Классический": "👔",
    "Спортивный": "👟",
}


def outfit_header(primary_style=""):
    """Единый заголовок образа: эмодзи отражает выбранный стиль."""
    style = _clean_text(primary_style)
    emoji = outfit_emoji(style)
    return f"{emoji} Образ на сегодня" + (f" · {style}" if style else "")


def outfit_emoji(primary_style=""):
    """Тот же эмодзи стиля для карточки гардероба и кратких сводок."""
    return _STYLE_EMOJI.get(_clean_text(primary_style), "👕")


def empty_wardrobe():
    b = MessageBuilder()
    b.section("🧶 Гардероб")
    b.spacer()
    b.line("Добавь вещи один раз — дальше я буду собирать образ за тебя.")
    b.spacer()
    b.line("Пришли список всей своей одежды одним сообщением. Я сам разложу всё по шкафу.")
    return b.build_stripped()


def render_wardrobe_message(look_data, *, news=None):
    """Образ на сегодня: три базовые вещи и выбранные дополнения.

    Погодная строка намеренно не показывается.

    look_data: {primary_style, items[{name, zone}], sock_recommendation,
                how_to_wear[], main_accent}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    primary_style = _clean_text(look_data.get("primary_style"))
    b.section(outfit_header(primary_style))

    slots = _outfit_slots(look_data.get("items") or [])
    if any(slots.values()):
        b.spacer()
        b.bold("Надень:")
        b.newline()
        if slots["Верх"]:
            b.line(f"- {', '.join(slots['Верх'])}")
        if slots["Низ"]:
            b.line(f"- {', '.join(slots['Низ'])}")
        if slots["Обувь"]:
            b.line(f"- {', '.join(slots['Обувь'])}")

        sock_recommendation = _upper_first(_clean_text(look_data.get("sock_recommendation")))
        selected_socks = any("носк" in item.casefold() for item in slots["Аксессуары"])
        if sock_recommendation and not selected_socks:
            b.line(f"- {sock_recommendation}")

        extras = [
            *slots["Верхняя одежда"],
            *slots["Аксессуары"],
            *slots["Другое"],
        ]
        if extras:
            b.spacer()
            b.bold("Дополнительно:")
            b.newline()
            for item in extras:
                b.line(f"- {item}")

    main_accent = _finish_dot(look_data.get("main_accent"))
    if main_accent:
        b.spacer()
        b.text_line("💡 ")
        b.bold("Главный акцент:")
        b.text_line(f" {_lower_first(main_accent)}")
        b.newline()

    append_weekly_news(b, news)

    return b.build_stripped()


# Старое имя — на случай, если что-то ещё зовёт карточку образа по прежней сигнатуре.
look_message = render_wardrobe_message


def _item_display(it):
    if not isinstance(it, dict):
        return it
    return it.get("short_name") or it.get("name")


_OUTFIT_SLOTS = ("Верх", "Верхняя одежда", "Низ", "Обувь", "Аксессуары", "Другое")


def _outfit_slots(items):
    """Раскладывает уже выбранные вещи, не решая, что войдёт в образ."""
    grouped = {slot: [] for slot in _OUTFIT_SLOTS}
    for item in items:
        name = _upper_first(_clean_text(_item_display(item)))
        if not name:
            continue
        zone = _clean_text(item.get("zone")) if isinstance(item, dict) else ""
        slot = zone if zone in grouped else zone_of(name)
        grouped[slot if slot in grouped else "Другое"].append(name)
    return grouped


def outfit_item_names(look_data):
    """Все показанные в образе вещи в том же порядке, но одним списком."""
    look_data = look_data or {}
    slots = _outfit_slots(look_data.get("items") or [])
    names = [*slots["Верх"], *slots["Низ"], *slots["Обувь"]]
    sock_recommendation = _upper_first(
        _clean_text(look_data.get("sock_recommendation"))
    )
    selected_socks = any("носк" in item.casefold() for item in slots["Аксессуары"])
    if sock_recommendation and not selected_socks:
        names.append(sock_recommendation)
    names.extend(slots["Верхняя одежда"])
    names.extend(slots["Аксессуары"])
    names.extend(slots["Другое"])
    return names


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
    b.section("🧐 Проверка покупки")

    verdict = _clean_text(data.get("verdict"))
    if verdict:
        verdict_labels = {
            "брать": "Брать",
            "можно брать": "Можно брать",
            "скорее не брать": "Скорее не брать",
            "не брать": "Не брать",
        }
        verdict_text = verdict_labels.get(verdict.casefold(), _upper_first(verdict).rstrip("."))
        b.spacer()
        b.labeled_line("Вердикт", _finish_dot(verdict_text), lowercase=True)

    fits_count = data.get("fits_count")
    if isinstance(fits_count, int) and not isinstance(fits_count, bool) and fits_count >= 0:
        if fits_count == 0:
            b.labeled_line("Подойдёт", "ни с одной вещью из текущего шкафа")
        else:
            b.labeled_line("Подойдёт", f"к {fits_count} {_pluralize_dative_items(fits_count)} из шкафа")
    elif fits_count == "недостаточно данных":
        b.labeled_line("Подойдёт", "недостаточно данных")

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

    return b.build_stripped()


def purchase_suggestions_card(data):
    """Результат подбора новой вещи: цвет, причина и реальные сочетания."""
    data = data if isinstance(data, dict) else {}
    b = MessageBuilder()
    item = _clean_text(data.get("item")) or "Новая вещь"
    b.section(f"💳 Что докупить · {item}")

    headline = _finish_dot(data.get("headline"))
    if headline:
        b.spacer()
        b.line(headline)

    colors = [entry for entry in (data.get("colors") or []) if isinstance(entry, dict)]
    if colors:
        b.spacer()
        b.section("Лучшие цвета:")
        for entry in colors[:3]:
            color = _upper_first(_clean_text(entry.get("color")))
            reason = _finish_dot(entry.get("reason"))
            if color:
                b.line(f"• {color}" + (f" — {_lower_first(reason)}" if reason else ""))

    avoid = _finish_dot(data.get("avoid"))
    if avoid:
        b.spacer()
        b.labeled_line("Лучше пропустить", avoid)

    outfits = [_finish_dot(value) for value in (data.get("outfits") or []) if _clean_text(value)]
    if outfits:
        b.spacer()
        b.section("С чем носить:")
        b.line("\n".join(f"• {outfit}" for outfit in outfits[:3]))

    return b.build_stripped()


def purchase_recommendations_card(items):
    """Три пробела гардероба и приглашение уточнить покупку через чат."""
    b = MessageBuilder()
    b.section("💳 Что докупить")
    b.spacer()
    b.bold("Рекомендую добавить в гардероб:")
    b.newline()
    for item in list(items or [])[:3]:
        name = _clean_text(item.get("item"))
        if not name:
            continue
        b.text_line("• ")
        b.bold(name)
        meta = " · ".join(
            value for value in (
                _clean_text(item.get("category")),
                _clean_text(item.get("style")),
                _clean_text(item.get("season")),
            ) if value
        )
        if meta:
            b.text_line(f" ({meta})")
        reason = _finish_dot(item.get("reason"))
        if reason:
            b.text_line(f" · {_upper_first(reason)}")
        b.newline()

    b.spacer()
    b.line(
        "Напиши, что ищешь: например «худи», «осенние ботинки» или «рубашка для работы». "
        "Я учту твои вещи, цвета и стиль и покажу готовые сочетания."
    )
    return b.build_stripped()


def purchase_recommendation_card(item):
    """Одна конкретная недостающая вещь с проверенным товаром и покупкой."""
    item = item or {}
    b = MessageBuilder()
    b.section("💳 Что докупить")
    b.spacer()
    name = _clean_text(item.get("item")) or "Полезная вещь для гардероба"
    product_url = _clean_text(item.get("product_url"))
    if product_url:
        b.link(name, product_url)
    else:
        b.bold(name)
    b.newline()
    meta = " · ".join(
        value for value in (
            _clean_text(item.get("category")),
            _clean_text(item.get("style")),
            _clean_text(item.get("season")),
        ) if value
    )
    if meta:
        b.line(meta)
    reason = _finish_dot(item.get("reason"))
    if reason:
        b.spacer()
        b.line(_upper_first(reason))
    product_title = _clean_text(item.get("product_title"))
    product_price = _clean_text(item.get("product_price"))
    product_source = _clean_text(item.get("product_source"))
    if product_title:
        b.spacer()
        b.labeled_line("Конкретный вариант", product_title, lowercase=False)
    shop_meta = " · ".join(value for value in (product_price, product_source) if value)
    if shop_meta:
        b.line(shop_meta)
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


def wardrobe_home_screen(total, categories=None):
    b = MessageBuilder()
    b.title(f"🎚️ Мой шкаф · {total} {_pluralize_items(total)}")
    for category in categories or []:
        names = [
            _clean_text(_item_display(item))
            for item in (category.get("items") or [])
            if _clean_text(_item_display(item))
        ]
        if not names:
            continue
        b.bold(f"{_clean_text(category.get('zone'))}:")
        b.newline()
        b.line(", ".join(names))
        b.spacer()
    return b.build_stripped()


def subcat_picker_screen(zone):
    b = MessageBuilder()
    b.section(_clean_text(zone))
    b.line("Выбери подкатегорию.")
    return b.build_stripped()


def category_screen(zone, items, total=None):
    b = MessageBuilder()
    count = len(items) if total is None else total
    b.section(f"👕 {_clean_text(zone)} · {count} {_pluralize_items(count)}")
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


def _success_item_category(item):
    item = item or {}
    return _clean_text(item.get("zone"))


def _success_item_style(item):
    style = (item or {}).get("style")
    if isinstance(style, (list, tuple)):
        style = " · ".join(_clean_text(value) for value in style if _clean_text(value))
    return _upper_first(_clean_text(style))


def _success_item_metadata(builder, item):
    category = _success_item_category(item)
    if category:
        builder.newline()
        builder.text_line(f"Категория: {category}")
    style = _success_item_style(item)
    if style:
        builder.newline()
        builder.text_line(f"Стиль: {style}")


def add_success(item):
    """Подтверждение после фактического сохранения одной вещи в шкаф."""
    b = MessageBuilder()
    b.line("✅ Вещь добавлена в «🎚️ Мой шкаф»")
    b.spacer()
    b.bold(_success_item_title(item))
    details = _success_item_details(item)
    if details:
        b.text_line(" · " + " · ".join(details))
    _success_item_metadata(b, item)
    return b.build_stripped()


def add_batch_success(items):
    b = MessageBuilder()
    b.line("✅ Вещи добавлены в «🎚️ Мой шкаф»")
    for item in items or []:
        b.spacer()
        b.bold(_success_item_title(item))
        details = _success_item_details(item)
        if details:
            b.text_line(" · " + " · ".join(details))
        _success_item_metadata(b, item)
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
