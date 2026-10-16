import re

from .builder import MessageBuilder
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


def outfit_header(primary_style=""):
    """Единый заголовок образа: эмодзи отражает выбранный стиль."""
    style = _clean_text(primary_style)
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

    look_data: {primary_style, items[{name, zone}], sock_recommendation,
                how_to_wear[], weather_reason}
    """
    look_data = look_data or {}
    b = MessageBuilder()
    primary_style = _clean_text(look_data.get("primary_style"))
    b.section(outfit_header(outfit_group(look_data)))

    slots = _outfit_slots(look_data.get("items") or [])
    items = outfit_item_names(look_data)
    selected_socks = next(
        (item for item in slots["Аксессуары"] if "носк" in item.casefold()), "",
    )
    # Носки — последним пунктом списка. Кэш прошлой версии хранил «Жёлтые — пояснение».
    socks = _upper_first(_clean_text(look_data.get("sock_recommendation") or selected_socks))
    if " — " in socks:
        socks = f"{socks.split(' — ', 1)[0]} носки"
    if socks:
        items.append(socks)
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


def suitcase_screen(data, saved=False):
    """«🧳 Чемодан · Кейптаун · 20–27 октября · 8 дней»: погода поездки (дальше прогноза — «Обычно в эти дни»), вещи
    шкафа по зонам («Футболки и рубашки: …»), курсивом совет. Пустой шкаф — базовый набор с пометкой; saved —
    подтверждённый чемодан из «Мой шкаф»."""
    data = data or {}
    b = MessageBuilder()
    days = int(data.get("days") or 0)
    head = " · ".join(part for part in ("🧳 Чемодан", data.get("place"), _trip_dates(data.get("start"), data.get("end")),
                                       f"{days} {ru_plural(days, 'день', 'дня', 'дней')}" if days else "") if part)
    b.title(head)
    if saved:
        b.line("✅ Сохранён — открывается из «Мой шкаф».")
        b.spacer()
    if data.get("weather"):
        weather = _finish_dot(data["weather"])
        b.line(f"Обычно в эти дни: {weather}" if data.get("typical") else _upper_first(weather))
        b.spacer()
    groups = data.get("groups") or []
    if data.get("basic") and groups:
        b.line("В шкафу пока нет вещей — вот базовый набор под эту погоду:")
    for zone, names in groups:
        b.labeled_line(public_zone_name(zone), ", ".join(_lower_first(_clean_text(name)) for name in names),
                       lowercase=False)
    if not groups:
        b.line("Под эту поездку из шкафа ничего не подошло — добавь вещи в «Настроить шкаф».")
    if str(data.get("tip") or "").strip():
        b.spacer()
        b.italic(_finish_dot(data["tip"]))
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
    b.italic("Например: «Кейптаун, 20–27 октября» или «Барселона на неделю с 5 ноября».")
    b.newline()
    return b.build_stripped()


def suitcase_city_not_found(city):
    b = MessageBuilder()
    b.line(f"Не нашёл город «{_clean_text(city)}». Напиши его ещё раз вместе с датами.")
    return b.build_stripped()
