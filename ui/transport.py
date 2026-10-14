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
    """«**Alkmaar – Den Helder:** текст» — участок жирным."""
    b.bold(f"{route}:")
    b.line(f" {text}")


def _cause_line(b, cause):
    """Отдельный блок «**Причина:** Неисправный мост.» — с заглавной и точкой."""
    cause = str(cause or "").strip().rstrip(".")
    b.spacer()
    b.labeled_line("Причина:", f"{cause[:1].upper()}{cause[1:]}.", lowercase=False)


def ns_alert(city, disruption) -> MessageSpec:
    """Предупреждение о сбое NS: что случилось, причина, «Ожидается до …» — блоками через пустую строку.

    Участок («Alkmaar - Hoorn:») и «Причина:» не повторяются, если текст NS их уже называет.
    """
    title = "Авария NS" if disruption.get("type") == "CALAMITY" else "Сбой NS"
    b = MessageBuilder()
    b.section(f"🚆 {title} · {city}")
    b.spacer()
    title_line, situation = disruption.get("title") or "", disruption.get("situation") or ""
    stations = [part.strip() for part in re.split(r"\s+[-–—]\s+", title_line) if part.strip()]
    if situation and title_line and not any(station.casefold() in situation.casefold() for station in stations):
        _route_line(b, title_line, situation)
    else:
        b.line(situation or title_line)
    cause = disruption.get("cause") or ""
    if cause and not _mentions(situation, cause, min_len=5):
        _cause_line(b, cause)
    tail = [part for part in (
        f"ожидается до {_time_label(disruption.get('until'))}" if _time_label(disruption.get("until")) else "",
        disruption.get("extra") or "",
    ) if part]
    if tail:
        b.spacer()
        joined = " · ".join(tail)
        b.line(joined[:1].upper() + joined[1:])
    return b.build_stripped()


def ns_restored(title, *, since="", until="", cause="") -> MessageSpec:
    """«✅ Движение восстановлено», участок, причина и сколько длился сбой — блоками через пустую строку."""
    b = MessageBuilder()
    b.section("✅ Движение восстановлено")
    b.spacer()
    if title:
        _route_line(b, title, "поезда снова ходят.")
    else:
        b.line("Поезда снова ходят.")
    if str(cause or "").strip():
        _cause_line(b, cause)
    start, end = _time_label(since), _time_label(until)
    if start and end and start != end:
        b.spacer()
        b.line(f"Сбой длился с {start} до {end}")
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
