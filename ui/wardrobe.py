from .builder import MessageBuilder
from .news import append_weekly_news
from .text import ru_plural
from wardrobe_model import public_zone_name, zone_of


def _lower_first(text):
    return text[:1].lower() + text[1:] if text else text


def _upper_first(text):
    """Поднимает первую букву названия, не меняя регистр остальной строки."""
    for index, char in enumerate(text or ""):
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1:]
    return text


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
    return f"{emoji} Надень сегодня" + (f" · {style}" if style else "")


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
    """Образ дня одним списком «Надень сегодня»: базовые вещи, затем дополнения.

    Погодная строка намеренно не показывается.

    look_data: {primary_style, items[{name, zone}], sock_recommendation,
                how_to_wear[], main_accent}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    primary_style = _clean_text(look_data.get("primary_style"))
    b.section(outfit_header(primary_style))

    slots = _outfit_slots(look_data.get("items") or [])
    items = [
        *slots["Верх"], *slots["Низ"], *slots["Обувь"], *slots["Верхняя одежда"],
        *(item for item in slots["Аксессуары"] if "носк" not in item.casefold()),
        *slots["Другое"],
    ]
    if items:
        b.spacer()
        for item in items:
            b.line(f"- {item}")

    selected_socks = next(
        (item for item in slots["Аксессуары"] if "носк" in item.casefold()), "",
    )
    sock_recommendation = _upper_first(
        _clean_text(look_data.get("sock_recommendation")) or selected_socks
    )
    main_accent = _finish_dot(
        look_data.get("main_accent")
        or (f"{sock_recommendation} поддержат обувь и соберут образ"
            if sock_recommendation else "")
    )
    if main_accent:
        b.spacer()
        b.text_line("💡 ")
        b.bold("Главный акцент:")
        b.text_line(f" {_lower_first(main_accent)}")
        b.newline()

    append_weekly_news(b, news)

    return b.build_stripped()


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
    names.extend(slots["Верхняя одежда"])
    names.extend(item for item in slots["Аксессуары"] if "носк" not in item.casefold())
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


def _outfits_word(n):
    return ru_plural(n, "образ", "образа", "образов")


def purchase_screen(data):
    """Экран 1 «💳 Что докупить»: разбор шкафа и самые полезные покупки.

    data: {total, outfits, strengths[], weaknesses[], picks[{name, gain}]}
    """
    data = data or {}
    b = MessageBuilder()
    b.title("💳 Что докупить")
    total = int(data.get("total") or 0)
    outfits = int(data.get("outfits") or 0)
    b.line(f"👔 Твой шкаф · {total} {_pluralize_items(total)} · {outfits} {_outfits_word(outfits)}")
    strengths = [_clean_text(x) for x in data.get("strengths") or [] if _clean_text(x)]
    weaknesses = [_clean_text(x) for x in data.get("weaknesses") or [] if _clean_text(x)]
    if strengths:
        b.labeled_line("Сильно", " · ".join(strengths))
    if weaknesses:
        b.labeled_line("Слабо", " · ".join(weaknesses))
    picks = [pick for pick in data.get("picks") or [] if _clean_text(pick.get("name"))]
    if picks:
        b.spacer()
        b.bold("🛒 Самое полезное сейчас")
        b.newline()
        for index, pick in enumerate(picks, 1):
            gain = int(pick.get("gain") or 0)
            suffix = f" · +{gain} {_outfits_word(gain)}" if gain > 0 else ""
            b.line(f"{index}. {_clean_text(pick['name'])}{suffix}")
    else:
        b.spacer()
        b.line("Явных пробелов нет — шкаф уже закрывает основные образы.")
    return b.build_stripped()


def purchase_small_wardrobe():
    b = MessageBuilder()
    b.title("💳 Что докупить")
    b.line("Добавь хотя бы 5 вещей — тогда разбор будет точным.")
    return b.build_stripped()


def purchase_card(data):
    """Экран 2: одна покупка с фактами шкафа.

    data: {name, why, before, after, outfits[[names]], tip}
    """
    data = data or {}
    b = MessageBuilder()
    b.title(f"🛒 {_clean_text(data.get('name')) or 'Вещь'}")
    why = _finish_dot(data.get("why"))
    if why:
        b.labeled_line("Почему тебе", why, lowercase=False)
    before, after = int(data.get("before") or 0), int(data.get("after") or 0)
    if after > before:
        b.spacer()
        b.line(f"Было {before} {_outfits_word(before)} → станет {after}")
    outfits = [
        " + ".join(_lower_first(_clean_text(name)) for name in outfit if _clean_text(name))
        for outfit in data.get("outfits") or []
    ]
    outfits = [outfit for outfit in outfits if outfit][:3]
    if outfits:
        b.spacer()
        b.bold("Готовые образы:")
        b.newline()
        for outfit in outfits:
            b.line(f"• {outfit}")
    tip = _finish_dot(data.get("tip"))
    if tip:
        b.spacer()
        b.line(f"💡 {tip}")
    return b.build_stripped()


def purchase_added(name, added=True):
    """Короткое подтверждение после «✅ Купил»."""
    b = MessageBuilder()
    if added:
        b.line(f"✅ «{_clean_text(name)}» — в шкафу. Пересчитал, что ещё пригодится.")
    else:
        b.line("Такая вещь уже есть в шкафу.")
    return b.build_stripped()


def _pluralize_dative_items(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "вещи"
    return "вещам"


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


def category_screen(zone, items, total=None):
    b = MessageBuilder()
    count = len(items) if total is None else total
    b.section(f"👕 {_clean_text(zone)} · {count} {_pluralize_items(count)}")
    if items:
        b.spacer()
        for item in items:
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
