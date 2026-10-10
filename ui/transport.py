from datetime import datetime

from .builder import MessageBuilder, MessageSpec


def _time_label(value):
    """«2026-10-08T18:30:00+0200» → «18:30»; непонятное значение — пусто."""
    try:
        return datetime.strptime(str(value)[:16], "%Y-%m-%dT%H:%M").strftime("%H:%M")
    except ValueError:
        return ""


def ns_alert(city, disruption) -> MessageSpec:
    """Предупреждение о сбое NS: заголовок, участок, причина, ожидание."""
    title = "Авария NS" if disruption.get("type") == "CALAMITY" else "Сбой NS"
    b = MessageBuilder()
    b.section(f"🚆 {title} · {city}")
    b.spacer()
    title_line, situation = disruption.get("title") or "", disruption.get("situation") or ""
    b.line(f"{title_line}: {situation}" if title_line and situation else title_line or situation)
    if disruption.get("cause"):
        b.line(f"Причина: {disruption['cause']}")
    tail = [part for part in (
        f"ожидается до {_time_label(disruption.get('until'))}" if _time_label(disruption.get("until")) else "",
        disruption.get("extra") or "",
    ) if part]
    if tail:
        joined = " · ".join(tail)
        b.line(joined[:1].upper() + joined[1:])
    return b.build_stripped()


def ns_restored(title) -> MessageSpec:
    b = MessageBuilder()
    b.line(f"✅ Движение {title} восстановлено" if title else "✅ Движение восстановлено")
    return b.build_stripped()


def flight_deal(item, details) -> MessageSpec:
    """Новая публикация авиадила: куда и за сколько; без разбора — заголовок источника."""
    b = MessageBuilder()
    b.section("✈️ Ошибочный тариф из Амстердама" if details.get("error_fare") else "✈️ Дешёвый билет из Амстердама")
    b.spacer()
    destination = details.get("destination") or ""
    b.line(f"Amsterdam → {destination}" if destination else item.get("title") or "")
    meta = [part for part in (
        f"от {details['price']}" if details.get("price") else "",
        details.get("trip") or "",
        item.get("source") or "",
    ) if part]
    if meta:
        b.line(" · ".join(meta))
    b.spacer()
    b.line("Такие цены держатся недолго — проверь даты и цену в публикации.")
    return b.build_stripped()
