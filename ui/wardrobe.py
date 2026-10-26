import re

from .builder import MessageBuilder
from .text import ru_plural
from util import _WEEKDAYS
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


# Эмодзи заголовка — по группе стиля (`outfit_group`), тонкие стили его не меняют.
_STYLE_EMOJI = {"Повседневный": "🧢", "Классический": "🧥"}

# Финальная строка образа о стиле — запасная, когда нет статистики шкафа.
_STYLE_ABOUT = {
    "Минимализм": "Минимализм — это нейтральные цвета, чистые линии и минимум деталей.",
    "Городской": "Городской стиль — это свободный крой, удобные кеды и утилитарные детали.",
    "Повседневный": "Повседневный стиль — это собранная базовая одежда без лишней спортивности.",
    "Скандинавский": "Скандинавский стиль — это спокойные оттенки, натуральные ткани и удобные слои.",
    "Классический": "Классический стиль — это строгие силуэты, рубашки и аккуратная обувь.",
    "Спортивный": "Спортивный стиль — это функциональные вещи и свобода движения.",
}


def style_about(primary_style="") -> str:
    """«Городской стиль — это …» или ""."""
    return _STYLE_ABOUT.get(_clean_text(primary_style), "")


_COLOR_PAIR_ENDINGS = (
    "спокойное сочетание, которое работает в любую погоду.",
    "сочетание, которое выглядит собранно без усилий.",
    "пара, которая не спорит между собой и легко носится весь день.",
)


def color_pair_line(first, second, variant=0) -> str:
    """«Оливковый и бежевый — спокойное сочетание, которое работает в любую погоду.»"""
    first, second = _clean_text(first), _clean_text(second)
    if not first or not second:
        return ""
    ending = _COLOR_PAIR_ENDINGS[variant % len(_COLOR_PAIR_ENDINGS)]
    return f"{first[:1].upper()}{first[1:]} и {second} — {ending}"


def outfit_header(primary_style="", trip_flag=""):
    """Единый заголовок образа: эмодзи отражает выбранный стиль; в отпуске — флаг страны и «в отпуске»."""
    style = _clean_text(primary_style)
    if trip_flag:
        return f"{trip_flag} Надень сегодня в отпуске" + (f" · {style}" if style else "")
    emoji = outfit_emoji(style)
    return f"{emoji} Надень сегодня" + (f" · {style}" if style else "")


def outfit_group(look_data) -> str:
    """Группа стиля образа для заголовка: «Повседневный» или «Классический» (старые образы — по стилю)."""
    look_data = look_data or {}
    return _clean_text(look_data.get("style_group")) or (
        "Классический" if _clean_text(look_data.get("primary_style")) == "Классический" else "Повседневный")


def outfit_emoji(primary_style=""):
    """Тот же эмодзи стиля для карточки гардероба и кратких сводок."""
    return _STYLE_EMOJI.get(_clean_text(primary_style), "🧢")


def empty_wardrobe():
    b = MessageBuilder()
    b.section("🧶 Гардероб")
    b.spacer()
    b.line("Добавь вещи один раз — дальше я буду собирать образ за тебя.")
    b.spacer()
    b.line("Пришли список всей своей одежды одним сообщением. Я сам разложу всё по шкафу.")
    return b.build_stripped()


def render_wardrobe_message(look_data):
    """Образ дня в едином стиле с Готовкой: погода → вещи → строка о стиле.

    Погода — одной строкой-причиной сверху и только когда она повлияла на выбор.

    look_data: {primary_style, items[{name, zone}], how_to_wear[], weather_reason}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    primary_style = _clean_text(look_data.get("primary_style"))
    b.section(outfit_header(outfit_group(look_data), look_data.get("trip_flag") or ""))

    # Носки бот не советует — это решение пользователя (сохранённые раньше рекомендации не показываются).
    items = outfit_item_names(look_data)
    weather_reason = _clean_text(look_data.get("weather_reason"))
    # Старые сохранённые образы хранят строку со значком погоды.
    for icon in ("☂️", "💨", "🌡️", "☀️"):
        weather_reason = weather_reason.removeprefix(icon).strip()
    if weather_reason:
        b.spacer()
        b.line(weather_reason)

    if items:
        b.spacer()
        for item in items:
            b.line(f"- {item}")

    # Нижняя строка курсивом — статистика шкафа: «Твой шкаф · 37 вещей · 528 образов».
    # Без статистики (старые данные) — прежняя финальная фраза о стиле.
    stats = look_data.get("stats") or {}
    closing = (wardrobe_stats_line(stats.get("items"), stats.get("outfits")) if stats.get("items")
               else _clean_text(look_data.get("closing")) or _STYLE_ABOUT.get(primary_style, ""))
    if closing:
        b.spacer()
        b.italic(closing)
        b.newline()

    return b.build_stripped()


def _item_display(it):
    if not isinstance(it, dict):
        return it
    return it.get("short_name") or it.get("name")


_OUTFIT_SLOTS = ("Верх", "Кофты", "Верхняя одежда", "Низ", "Обувь", "Аксессуары", "Другое")


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
    # Футболка, поверх неё кофта, затем низ и обувь.
    names = [*slots["Верх"], *slots["Кофты"], *slots["Низ"], *slots["Обувь"]]
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


def wardrobe_stats_line(items, outfits) -> str:
    items, outfits = int(items or 0), int(outfits or 0)
    return f"Твой шкаф · {items} {_pluralize_items(items)} · {outfits} {_outfits_word(outfits)}"


def _outfits_word(n):
    return ru_plural(n, "образ", "образа", "образов")


def wardrobe_home_screen(total):
    b = MessageBuilder()
    b.title(f"🎚️ Мой шкаф · {total} {_pluralize_items(total)}")
    b.italic("Нажми на категорию, чтобы посмотреть вещи или удалить лишнее.")
    b.newline()
    return b.build_stripped()


def category_screen(zone, items, total=None):
    b = MessageBuilder()
    count = len(items) if total is None else total
    b.section(f"{_clean_text(zone)} · {count} {_pluralize_items(count)}")
    return b.build_stripped()


def item_card(item):
    item = item or {}
    b = MessageBuilder()
    b.section(_clean_text(item.get("name")) or "Вещь")
    b.spacer()
    b.labeled_line("Категория", public_zone_name(item.get("zone")))
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
    return _clean_text(public_zone_name(item.get("zone"))) if item.get("zone") else ""


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




def _trip_dates(start, end) -> str:
    """«20–27 октября», «30 октября – 3 ноября»."""
    from datetime import date
    from util import _MONTHS
    try:
        first, last = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    except ValueError:
        return ""
    if first == last:
        return f"{first.day} {_MONTHS[first.month - 1]}"
    if first.month == last.month:
        return f"{first.day}–{last.day} {_MONTHS[last.month - 1]}"
    return f"{first.day} {_MONTHS[first.month - 1]} – {last.day} {_MONTHS[last.month - 1]}"


def _suitcase_count(zone, names) -> str:
    count = len(names)
    return (f"{count} {ru_plural(count, 'пара', 'пары', 'пар')}" if zone == "Обувь"
            else f"{count} {_pluralize_items(count)}")


def _weather_block(b: MessageBuilder, data) -> None:
    """Погода поездки и совет. Один город — одной строкой: «Днём от +16° до +34°, 3 дождливых дня, ветер до 8 м/с.
    Днём до +34° — …»; маршрут — абзац на город («**Кейптаун**, 19–26 октября: днём от …»), совет — своим абзацем."""
    from .vacation import weather_line
    tip = _finish_dot(_upper_first(_clean_text(data.get("tip")))) if _clean_text(data.get("tip")) else ""
    legs = [leg for leg in data.get("legs_wx") or [] if "tmax" in leg]
    if len(legs) > 1:
        for leg in legs:
            b.spacer()
            b.bold(_clean_text(leg.get("city")))
            b.text_line(f", {_trip_dates(leg.get('start'), leg.get('end'))}: "
                        f"{'обычно ' if leg.get('typical') else ''}{weather_line(leg)}.")
            b.newline()
        if tip:
            b.spacer()
            b.line(tip)
        return
    weather = weather_line(data.get("wx") or {}) or _clean_text(data.get("weather"))
    if weather:
        weather = f"Обычно в эти дни: {_finish_dot(weather)}" if data.get("typical") else _finish_dot(
            _upper_first(weather))
    line = " ".join(part for part in (weather, tip) if part)
    if line:
        b.line(line)


def suitcase_screen(data, saved=False, trip=False):
    """Чек-лист чемодана: «🧳 Чемодан · ЮАР · 19 октября – 4 ноября · 17 дней» (страна поездки, а не город),
    погода поездки и конкретный совет одной строкой (маршрут — абзац на город, город жирным), категории — «Обувь
    (2 пары): белые кеды, …» абзацем на категорию; «Не хватает: …» — чего под погоду нет в шкафу; курсивом итог. Пустой шкаф —
    базовый набор с пометкой. Строки «сохранён» нет: что чемодан сохранён, видно по его кнопкам."""
    data = data or {}
    b = MessageBuilder()
    days = int(data.get("days") or 0)
    head = " · ".join(part for part in ("🧳 Чемодан в отпуске" if trip else "🧳 Чемодан", data.get("place"), _trip_dates(data.get("start"), data.get("end")),
                                       f"{days} {ru_plural(days, 'день', 'дня', 'дней')}" if days else "") if part)
    b.title(head)
    _weather_block(b, data)
    groups = data.get("groups") or []
    basic = bool(data.get("basic"))
    if basic and groups:
        b.spacer()
        b.line("В шкафу пока нет вещей — вот базовый набор под эту погоду:")
    for zone, names in groups:  # категория — абзацем: «Обувь (2 пары): белые кеды, …»
        listed = ", ".join(_lower_first(_clean_text(name)) for name in names)
        label = public_zone_name(zone) if basic else f"{public_zone_name(zone)} ({_suitcase_count(zone, names)})"
        b.spacer()
        b.labeled_line(label, listed, lowercase=False)
    if not groups:
        b.spacer()
        b.line("Под эту поездку из шкафа ничего не подошло — добавь вещи в «Настроить шкаф».")
    gaps = [_clean_text(gap) for gap in data.get("gaps") or [] if _clean_text(gap)]
    if gaps and not basic:
        b.spacer()
        b.line(f"Не хватает: {', '.join(gaps)} — в шкафу таких нет.")
    if groups and not basic:
        shoes = sum(len(names) for zone, names in groups if zone == "Обувь")
        things = sum(len(names) for zone, names in groups if zone != "Обувь")
        total = " и ".join(part for part in (
            f"{things} {_pluralize_items(things)}" if things else "",
            f"{shoes} {ru_plural(shoes, 'пара', 'пары', 'пар')} обуви" if shoes else "") if part)
        b.spacer()
        b.italic(f"Всего в списке: {total}")
        b.newline()
    return b.build_stripped()


def _suitcase_head(title, data):
    days = int((data or {}).get("days") or 0)
    return " · ".join(part for part in (title, (data or {}).get("place"),
                                       f"{days} {ru_plural(days, 'день', 'дня', 'дней')}" if days else "") if part)


def suitcase_edit_screen(data):
    """«Что не берёшь?» (старая кнопка «Пересобрать» из истории чата): вещи чемодана кнопками — нажал, и вещь уходит
    сразу; «Добавить ещё вещь» — из шкафа."""
    b = MessageBuilder()
    b.title(_suitcase_head("🧳 Что не берёшь?", data))
    b.line("Нажми на вещь, которую не берёшь, — уберу её сразу. Чтобы взять другую, нажми «Добавить ещё вещь».")
    return b.build_stripped()


def suitcase_add_screen(data):
    """Категории шкафа для «Добавить ещё вещь»."""
    b = MessageBuilder()
    b.title(_suitcase_head("🧳 Добавить в чемодан", data))
    b.line("Выбери категорию — покажу вещи из шкафа.")
    return b.build_stripped()


def suitcase_add_zone_screen(data, zone):
    """Вещи одной категории: зелёные — уже в чемодане; нажми, чтобы положить или убрать."""
    b = MessageBuilder()
    b.title(_suitcase_head(f"🧳 {public_zone_name(zone)}", data))
    b.line("Зелёные — уже в чемодане. Нажми на вещь, чтобы положить её или убрать.")
    return b.build_stripped()


_HOT_LOOK, _COOL_LOOK, _WINDY_LOOK = 24, 17, 8  # как у подбора образа дня поездки (wardrobe_suitcase._day_context)


def _day_condition(look) -> str:
    """Погода дня со значком: «🌧️ Дождь», «☀️ Жарко», «⛅ Тепло и сухо»; только по прогнозу на неделю (дальше —
    "", дождя и ветра там ещё не знаем)."""
    tmax, wind = look.get("tmax"), look.get("wind") or 0
    if tmax is None or look.get("kind") not in (None, "", "forecast"):
        return ""
    parts = ["жарко" if tmax >= _HOT_LOOK else "прохладно" if tmax < _COOL_LOOK else "",
             "дождь" if look.get("rain") else "", "ветрено" if wind >= _WINDY_LOOK else ""]
    text = ", ".join(part for part in parts if part) or ("тепло и сухо" if tmax >= _COOL_LOOK else "сухо")
    icon = str(look.get("icon") or "").strip()
    return f"{icon} {_upper_first(text)}".strip()


def suitcase_day_card(data, look, index, total, today=""):
    """Образ одного дня поездки: «🇿🇦 Образы в отпуске · ЮАР · 17 дней» (страна, не город); жирным день недели
    (поездка дольше недели — «· день N»; «· сегодня»; город этого дня), пометка об условиях, если она влияет на выбор,
    вещи списком «- …», как в «Надень сегодня» (носков нет — их выбирает пользователь); курсивом «Твой чемодан ·
    12 вещей · 17 образов».
    Дат и температуры нет. Листание — кнопками."""
    from datetime import date
    data, look = data or {}, look or {}
    days = int(data.get("days") or total or 0)
    b = MessageBuilder()
    b.title(" · ".join(part for part in (f"{data.get('flag') or '👕'} Образы в отпуске", data.get("place"),
                                         f"{days} {ru_plural(days, 'день', 'дня', 'дней')}" if days else "") if part))
    try:
        title = _WEEKDAYS[date.fromisoformat(str(look.get("date"))).weekday()]
    except ValueError:
        title = ""
    title = " · ".join(part for part in (title, f"день {index + 1}" if total > 7 or not title else "",
                                        "сегодня" if look.get("date") == today else "",
                                        _clean_text(look.get("city") or data.get("city"))) if part)
    b.bold(_upper_first(title))
    b.newline()
    condition = _day_condition(look)
    if condition:
        b.line(condition)
    b.spacer()
    items = [_upper_first(_clean_text(name)) for name in look.get("items") or [] if _clean_text(name)]
    for item in items:
        b.line(f"- {item}")
    if not items:
        b.line("В шкафу нет вещей для образа.")
    packed = int(data.get("items") or 0)
    if packed:
        b.spacer()
        b.italic(f"Твой чемодан · {packed} {_pluralize_items(packed)} · {total} {_outfits_word(total)}")
        b.newline()
    return b.build_stripped()


def suitcase_ask(not_understood=False):
    """Вопрос перед сборкой чемодана дома: город и даты одной строкой."""
    b = MessageBuilder()
    b.title("🧳 Собрать чемодан")
    if not_understood:
        b.line("Не понял даты поездки.")
        b.spacer()
    b.line("Куда и когда едешь? Напиши город и даты — посмотрю погоду и соберу чемодан из твоего шкафа.")
    b.spacer()
    b.italic("Например: «Кейптаун, 20–27 октября», «Барселона на неделю с 5 ноября» или маршрутом — «Кейптаун "
             "19–27 октября, далее Йоханнесбург до 3 ноября».")
    b.newline()
    return b.build_stripped()


def suitcase_city_not_found(city):
    b = MessageBuilder()
    b.line(f"Не нашёл город «{_clean_text(city)}». Напиши его ещё раз вместе с датами.")
    return b.build_stripped()


def edit_item_prompt(item):
    """«Изменить вещь»: что сейчас записано и как описать заново."""
    item = item or {}
    b = MessageBuilder()
    b.section("✏️ Изменить вещь")
    b.spacer()
    b.labeled_line("Сейчас", _upper_first(_clean_text(item.get("name")) or "вещь"), lowercase=False)
    if item.get("color"):
        b.labeled_line("Цвет", item["color"], lowercase=False)
    b.spacer()
    b.line("Опиши вещь заново одним сообщением — она заменит прежнюю.")
    b.italic("Например: голубая хлопковая футболка.")
    return b.build_stripped()
