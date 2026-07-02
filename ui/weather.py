"""Погодные сообщения.

Намеренно НЕ мигрировано на компонентный API MessageBuilder (section/line/bullet/
warning/tip/divider/spacer): все функции здесь строят составные сообщения через
склейку HTML-фрагментов (список строк с тегами -> from_html), а не через
последовательные вызовы билдера. Причины:
  - `full_forecast`/`week_forecast`: заголовок + список периодов/дней + опциональный
    итог собираются в одну HTML-строку и парсятся разом — компонентные вызовы
    не дают того же контроля над структурой без потери читаемости.
  - `day_forecast`: принимает `alert` — уже готовый HTML-фрагмент, произведённый
    `storm_alert_html()` в другом месте (см. корневой weather.py), и встраивает
    его как есть в свои `lines` перед общим `from_html`. MessageBuilder не умеет
    принимать/сливать чужой HTML-фрагмент внутрь себя, поэтому здесь нельзя
    перейти на компоненты, не сломав это встраивание.
  - `storm_alert`/`storm_alert_html`: используют общий `_storm_alert_lines()`;
    `storm_alert_html` обязана возвращать сырой HTML-фрагмент (не MessageSpec) —
    это законтрактовано вызовом из `day_forecast` выше.
  - `city_not_found`/`city_changed`/`location_changed`: однострочные сообщения без
    структуры заголовок+контент — компоненты (section/warning/tip) здесь неуместны
    семантически, обычный MessageSpec с f-строкой проще и достаточен.
"""

from .builder import MessageSpec, from_html
from util import esc, cap_sentence


def full_forecast(header, periods, joke=""):
    lines = [f"<b>{esc(header)}</b>", ""]
    for period in periods:
        lines += [f"<b>{esc(period['label'])}:</b>", period["line"], ""]
    if joke:
        lines.append(esc(joke))
    return from_html("\n".join(lines).strip())


def day_forecast(header, main_lines, alert="", fact_title="", fact=""):
    lines = [f"<b>{esc(header)}</b>", ""]
    lines += list(main_lines or [])
    if alert:
        lines += ["", alert]
    elif fact:
        if fact_title:
            lines += ["", f"🌡️ <b>{esc(fact_title)}</b>", esc(fact)]
        else:
            lines += ["", esc(fact)]
    return from_html("\n".join(lines))


def week_forecast(rng, city, flag, groups, summary=""):
    lines = [f"<b>Ближайшая неделя • {esc(rng)} • {esc(city)} {flag}</b>", ""]
    for group in groups:
        lines.append(
            f"{group['icon']} {esc(group['label'])} — {esc(group['desc'])}, {group['temp']}"
        )
    if summary:
        lines += ["", "🌡️ <b>Метео-итог</b>", esc(_finish_sentence(cap_sentence(summary)))]
    return from_html("\n".join(lines).strip())


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
    return MessageSpec(text=f"😕 Не нашёл город: {raw}.\n\n🌍 Проверь написание и пришли название ещё раз.")


def city_changed(city, country=""):
    return MessageSpec(text=f"✅ Готово. Город переключён на {city}" + (f", {country}." if country else "."))


def location_changed(city, country=""):
    return MessageSpec(text=f"Готово. Ты находишься в городе {city}" + (f", {country}." if country else "."))
