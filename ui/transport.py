import re
from datetime import datetime

from .builder import MessageBuilder, MessageSpec


def _time_label(value):
    """«2026-10-08T18:30:00+0200» → «18:30»; непонятное значение — пусто."""
    try:
        return datetime.strptime(str(value)[:16], "%Y-%m-%dT%H:%M").strftime("%H:%M")
    except ValueError:
        return ""


def _mentions(text, phrase, *, min_len) -> bool:
    """Упомянуто ли phrase в text: все значимые слова (по началу, чтобы падежи совпадали)."""
    text = str(text or "").casefold()
    words = [word for word in re.findall(r"\w+", str(phrase or "").casefold()) if len(word) >= min_len]
    return bool(words) and all(word[:min_len] in text for word in words)


def _route_line(b, route, text):
    """Участок жирным отдельной строкой («**Amsterdam Sloterdijk - Hoorn Kersenboogerd**»), через пустую строку —
    что случилось, с заглавной: длинное название участка не сливается с причиной."""
    b.bold(route)
    b.newline()
    b.spacer()
    text = str(text or "").strip()
    b.line(text[:1].upper() + text[1:])


def _cause_line(b, cause):
    """Отдельный блок «**Причина:** Неисправный мост.» — с заглавной и точкой."""
    cause = str(cause or "").strip().rstrip(".")
    b.spacer()
    b.labeled_line("Причина:", f"{cause[:1].upper()}{cause[1:]}.", lowercase=False)


def practical_lines(item, *, with_level=True) -> list:
    """Пункты для пассажира: «Поезда: Alkmaar – Hoorn — поездов нет или почти нет», «Вместо поездов: автобусы»,
    «Заложи: +30 мин», «Совет: …». Только то, что есть в данных NS."""
    lines = []
    sections = ", ".join(item.get("sections") or [])
    level = str(item.get("level") or "") if with_level else ""
    if sections and level:
        lines.append(f"Поезда: {sections} — {level}")
    elif sections:
        lines.append(f"Затронуты: {sections}")
    elif level:
        lines.append(level[:1].upper() + level[1:])
    if item.get("alternative"):
        lines.append(f"Вместо поездов: {_lower(item['alternative'])}")
    if item.get("extra_min"):
        lines.append(f"Заложи: {item['extra_min']} к дороге")
    if item.get("advice"):
        lines.append(f"Совет: {_lower(item['advice'])}")
    return [line.rstrip(".") + "." for line in lines]


def _lower(text) -> str:
    text = str(text or "").strip()
    return text[:1].lower() + text[1:] if text[:2] != text[:2].upper() else text


def route_label(titles, city) -> str:
    """Участки сбоя одной строкой к своему городу: «Amsterdam Centraal - Alkmaar», «Haarlem - Alkmaar» →
    «Amsterdam / Haarlem → Alkmaar». Город не найден в участке — участки как есть через « / »."""
    city_key = str(city or "").casefold()
    others, own = [], False
    for title in titles or ():
        for station in re.split(r"\s+[-–—]\s+", str(title or "")):
            station = re.sub(r"\s+(Centraal|CS)$", "", station.strip(), flags=re.I)
            if not station:
                continue
            if city_key and city_key in station.casefold():
                own = True
            elif station not in others:
                others.append(station)
    if own and others:
        return f"{' / '.join(others)} → {city}"
    return " / ".join(str(title).strip() for title in titles or () if str(title or "").strip())


def _sentence(text) -> str:
    text = str(text or "").strip().rstrip(".")
    return f"{text[:1].upper()}{text[1:]}." if text else ""


def _clock(moment) -> str:
    return moment.strftime("%H:%M") if hasattr(moment, "strftime") else ""


def bus_line(item) -> str:
    """«6 → Bergen 08:12» (+N мин, если опаздывает)."""
    delay = int(item.get("delay_min") or 0)
    text = f"{item.get('line')} → {item.get('destination')} {_clock(item.get('expected') or item.get('planned'))}"
    return text + (f" (+{delay} мин)" if delay >= 3 else "")


def bus_alert(stop, events) -> MessageSpec:
    """«🔴 Сбой автобусов», «**Остановка:** Station Alkmaar», пункты: «• 6 → Bergen 08:12 — отменён», «• 165 → Hoorn
    08:27 — опаздывает на 15 мин (по плану 08:12)», «• Сообщение перевозчика: …»."""
    b = MessageBuilder()
    b.section("🔴 Сбой автобусов")
    b.spacer()
    b.labeled_line("Остановка", stop, lowercase=False)
    for event in events:
        if event["kind"] == "cancel":
            b.line(f"• {event['line']} → {event['destination']} {_clock(event['planned'])} — отменён")
        elif event["kind"] == "delay":
            b.line(f"• {event['line']} → {event['destination']} {_clock(event['expected'])} — опаздывает на "
                   f"{event['delay_min']} мин (по плану {_clock(event['planned'])})")
        else:
            b.line(f"• {_sentence(event['text'])}")
    return b.build_stripped()


def ns_alert(city, disruption, titles=None, buses=None) -> MessageSpec:
    """Сбой NS коротко: «🔴 Сбой NS», участки к городу, причина, ожидаемое восстановление."""
    title = "Авария NS" if disruption.get("type") == "CALAMITY" else "Сбой NS"
    b = MessageBuilder()
    b.section(f"🔴 {title}")  # город — в строке участка, как у восстановления
    b.spacer()
    route = route_label(titles or [disruption.get("title") or ""], city)
    if route:
        b.labeled_line("Маршрут", route, lowercase=False)
    cause = _sentence(disruption.get("cause"))
    if cause:
        b.labeled_line("Причина", cause)  # после подписи — со строчной: «неисправность сигнализации.»
    until = _time_label(disruption.get("until"))
    if until:
        b.labeled_line("Ожидаемое восстановление", f"{until}.", lowercase=False)
    if not cause and not until and disruption.get("situation"):  # причины и срока нет — что пишет NS
        b.line(_sentence(disruption["situation"]))
    if buses:  # замена поездам — ближайшие автобусы от вокзала (OVapi)
        b.labeled_line("Автобусы от вокзала", " · ".join(bus_line(item) for item in buses), lowercase=False)
    return b.build_stripped()


def ns_restored(title, *, since="", until="", cause="", city="", titles=None) -> MessageSpec:
    """«🟢 Движение восстановлено» (без города — он в строке участка), участки и сколько длился сбой."""
    b = MessageBuilder()
    b.section("🟢 Движение восстановлено")  # город — уже в строке участка
    b.spacer()
    route = route_label(titles or ([title] if title else []), city)
    if route:
        b.labeled_line("Маршрут", route, lowercase=False)
    else:
        b.line("Поезда снова ходят.")
    start, end = _time_label(since), _time_label(until)
    if start and end and start != end:
        b.labeled_line("Сбой длился", f"{start} - {end}.", lowercase=False)
    return b.build_stripped()


def flight_deal(item, details) -> MessageSpec:
    """Новая публикация авиадила: куда и за сколько; без разбора — заголовок источника."""
    b = MessageBuilder()
    b.section("✈️ Ошибочный тариф из Амстердама" if details.get("error_fare") else "✈️ Дешёвый билет из Амстердама")
    b.spacer()
    destination = details.get("destination") or ""
    if destination:
        b.labeled_line("Куда", f"Amsterdam → {destination}", lowercase=False)
    else:
        b.line(item.get("title") or "")
    if details.get("price"):
        b.labeled_line("Цена", f"от {details['price']}", lowercase=False)
    if details.get("trip"):
        b.labeled_line("Билет", details["trip"])
    if item.get("source"):
        b.labeled_line("Источник", item["source"], lowercase=False)
    b.spacer()
    b.line("Такие цены держатся недолго — проверь даты и цену в публикации.")
    return b.build_stripped()
