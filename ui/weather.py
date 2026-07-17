"""Погодные сообщения.

`full_forecast`/`week_forecast`/`day_forecast` собраны на компонентном API
MessageBuilder (section/line/warning/embed) — единый визуальный язык бота.
Все три сохраняют исходный формат "заголовок, пустая строка, контент": после
`section(header)` идёт явный `newline()`, потому что сами компоненты не
добавляют отступ ПОСЛЕ себя (только ПЕРЕД следующим блоком) — без этого
`newline()` контент прилипал бы к заголовку.

`day_forecast` получает `alert` как уже готовую HTML-строку (её производит
`storm_alert_html()` в корневом weather.py) и сама конвертирует её через
`from_html()` в MessageSpec, чтобы встроить через `embed()` — так сигнатура
`day_forecast(header, main_lines, alert=...)` не меняется, а вызывающему коду
не нужно ничего знать про builder.

Остаются вне компонентов:
  - `storm_alert`/`storm_alert_html`: используют общий `_storm_alert_lines()`;
    `storm_alert_html` обязана возвращать сырой HTML-фрагмент (не MessageSpec) —
    это законтрактовано вызовом из `day_forecast` выше и прямым использованием
    в корневом weather.py.
  - `city_not_found`/`city_changed`/`location_changed`: однострочные сообщения
    без структуры заголовок+контент — это не "HTML в строгом смысле", просто
    f-строка в MessageSpec; компоненты (section/warning/tip) тут семантически
    не нужны, разбивать нечего.
"""

from .builder import MessageBuilder, MessageSpec, from_html
from .constants import ui_label
from util import cap_sentence


def weather_warning(events, when="", advice=None):
    """Новый формат погодного предупреждения: события → когда → что сделать.

    events — строки событий (с эмодзи); when — интервал/период; advice — список
    рекомендаций (2–4). Всё уже отобрано и обрезано вызывающим кодом.
    """
    b = MessageBuilder()
    b.section("⚠️ Погодное предупреждение")
    b.newline()
    for ev in events:
        b.line(ev)
    if when:
        b.section(ui_label("when", "Когда:"))
        b.line(when)
    if advice:
        b.section(ui_label("action", "Что сделать:"))
        for a in advice:
            b.bullet(a)
    return b.build_stripped()


def full_forecast(header, periods, joke=""):
    b = MessageBuilder()
    b.section(header)
    b.newline()
    for period in periods:
        b.section(f"{period['label']}:")
        b.line(period["line"])
        b.newline()
    if joke:
        b.line(joke)
    return b.build_stripped()


def day_forecast(header, main_lines, alert="", fact_title="", fact=""):
    b = MessageBuilder()
    b.section(header)
    b.newline()
    for line in main_lines or []:
        b.line(line)
    if alert:
        b.embed(from_html(alert))
    elif fact:
        if fact_title:
            b.warning(fact_title, emoji="🌡️")
            b.line(fact)
        else:
            b.spacer()
            b.line(fact)
    return b.build_stripped()


def week_forecast(rng, city, overview, days, advice):
    b = MessageBuilder()
    b.bold(f"На неделю · {rng} · {city} 📍")
    b.newline()
    b.spacer()
    b.line(overview)
    b.spacer()
    for day in days:
        b.line(f"{day['abbrev']} · {day['icon']} {day['tmax']:+.0f}°")
    b.spacer()
    b.line(f"💡 {_finish_sentence(cap_sentence(advice))}")
    return b.build_stripped()


def _finish_sentence(text):
    text = (text or "").strip()
    if text and text[-1] not in ".!?…":
        return text + "."
    return text


def _storm_alert_lines(reasons, wind_ms, is_nl=False):
    lines = ["⚠️ <b>Штормовое предупреждение</b>" + (" (Code Geel)" if is_nl else ""), ""]
    if "wind" in reasons:
        lines.append(f"Ожидаются шквалы до {wind_ms:.0f} м/с. Закрепи велосипед, убери лёгкие предметы с балкона.")
        if is_nl:
            lines.append("Высокий риск задержек и отмен поездов NS - ветки на путях парализуют движение. Проверь приложение NS.")
        else:
            lines.append("Возможны задержки транспорта из-за ветра. Заложи время на дорогу.")
    if "rain" in reasons:
        if is_nl:
            lines.append("Сильный дождь и риск подтоплений. Сверься с Buienradar перед выходом.")
        else:
            lines.append("Сильный дождь и риск подтоплений. Проверь прогноз осадков перед выходом.")
    if "snow" in reasons:
        lines.append("Снег и гололёд. Осторожно на дорогах, заложи время на дорогу.")
    return lines


def storm_alert(reasons, wind_ms, is_nl=False):
    return from_html("\n".join(_storm_alert_lines(reasons, wind_ms, is_nl)))


def storm_alert_html(reasons, wind_ms, is_nl=False):
    """HTML-фрагмент штормового предупреждения — для встраивания внутрь day_forecast()."""
    return "\n".join(_storm_alert_lines(reasons, wind_ms, is_nl))


def city_not_found(raw):
    return MessageSpec(text=f"Не нашёл город: {raw}.\n\nПроверь написание и пришли название ещё раз.")


def city_changed(city, country=""):
    return MessageSpec(text=f"✅ Готово. Город переключён на {city}" + (f", {country}." if country else "."))


def location_changed(city, country=""):
    return MessageSpec(text=f"Готово. Ты находишься в городе {city}" + (f", {country}." if country else "."))
