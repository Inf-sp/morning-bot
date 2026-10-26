"""Тексты режима «✈️ В отпуске»: вход, возвращение и блок «Страна»."""
from .builder import MessageBuilder
from .text import ru_plural

ROUTE_EXAMPLE = "«Кейптаун до 27 октября, далее Йоханнесбург до 3 ноября»"


def weather_line(found) -> str:
    """«днём от +16° до +34°, 3 дождливых дня, ветер до 8 м/с» (без дождя — «без дождя») или "". Число дней прогноза
    не пишется: оно меньше дней поездки, если поездка дальше горизонта прогноза."""
    if "tmax" not in (found or {}):
        return ""
    rainy = int(found.get("rainy") or 0)
    rain = f"{rainy} {ru_plural(rainy, 'дождливый день', 'дождливых дня', 'дождливых дней')}" if rainy else "без дождя"
    return f"днём от {found['tmin']:+d}° до {found['tmax']:+d}°, {rain}, ветер до {int(found.get('wind') or 0)} м/с"


def ask_city():
    b = MessageBuilder()
    b.section("✈️ В отпуске")
    b.spacer()
    b.line("Напиши город, куда едешь, и сроки, например «Cape Town до 20 октября». Несколько городов — маршрутом: "
           f"{ROUTE_EXAMPLE}.")
    b.spacer()
    b.italic("Погода, праздники, кухня, концерты и премьеры переключатся на эту страну, "
             "«Обучение» — на язык страны, а в «Моём дне» появятся факты о ней.")
    b.newline()
    return b.build_stripped()


def suitcase_trip_label(trip, route=""):
    """Кнопка поездки сохранённого чемодана на экране «В отпуске»: «🧳 Кейптаун · до 27 октября»; маршрут —
    «🧳 Кейптаун → Йоханнесбург · до 3 ноября»."""
    from datetime import date
    from util import _MONTHS
    end = date.fromisoformat(trip["end"])
    return f"🧳 {route or trip['city']} · до {end.day} {_MONTHS[end.month - 1]}"


def ask_city_in_country(country, flag=""):
    """Написана страна, а не город: какой город? Погода и всё остальное — по городу."""
    b = MessageBuilder()
    b.section(f"✈️ {country} {flag}".strip())
    b.spacer()
    b.line("Какой город? Напиши, например «Барселона» или «Барселона до 20 октября».")
    b.spacer()
    b.italic("Погода, кухня, концерты и праздники подбираются по городу.")
    b.newline()
    return b.build_stripped()


def ask_days(city, flag="", facts=()):
    """«✈️ Кейптаун 🇿🇦», коротко о стране, внизу курсивом — вопрос о дате возвращения."""
    b = MessageBuilder()
    b.section(f"✈️ {city} {flag}".strip())
    b.spacer()
    for label, value in facts:
        b.labeled_line(label, value, lowercase=False)
    if facts:
        b.spacer()
    b.italic("До какого дня отпуск? В этот день я сам верну домашний город. "
             "Можно написать дату в чат, например «20 октября» или «20.10».")
    b.newline()
    return b.build_stripped()


def route_not_understood():
    b = MessageBuilder()
    b.line(f"Не понял маршрут. Напиши города с датами переезда, например {ROUTE_EXAMPLE}.")
    return b.build_stripped()


def route_city_not_found(city):
    b = MessageBuilder()
    b.line(f"Не нашёл город «{city}». Напиши маршрут ещё раз, например {ROUTE_EXAMPLE}.")
    return b.build_stripped()


def until_not_understood():
    b = MessageBuilder()
    b.line("Не понял дату. Напиши, например, «20 октября», «20.10» или «на неделю» — или выбери кнопку выше.")
    return b.build_stripped()


def started(city, until="", home="", holidays=None):
    """holidays — [(«24 сентября», «Heritage Day — День наследия»)] в дни отпуска; None — источник недоступен."""
    b = MessageBuilder()
    b.section(f"✈️ Хорошего отпуска · {city}".rstrip(" ·"))
    b.spacer()
    parts = [f"Отпуск {until}" if until else "Отпуск без даты возвращения"]
    if home:
        parts.append(f"дома — {home}")
    b.line(" · ".join(parts) + ".")
    if holidays is not None:
        b.spacer()
        if holidays:
            b.bold("Праздники в эти дни:" if until else "Праздники в ближайший месяц:")
            b.newline()
            for day, name in holidays:
                b.bullet(f"{day} · {name}")
        else:
            b.line("Государственных праздников в эти дни нет." if until
                   else "В ближайший месяц государственных праздников нет.")
    b.spacer()
    b.italic("Вернуться раньше — кнопкой «Вернуться из отпуска» под «Моим днём» или в Настройках.")
    b.newline()
    return b.build_stripped()


def returned(home, automatic=False):
    b = MessageBuilder()
    b.section("🏠 С возвращением!")
    b.spacer()
    start = "Отпуск закончился, снова" if automatic else "Снова"
    b.line(f"{start} {home}: погода, кухня, концерты и обучение — как раньше.")
    return b.build_stripped()


def _number(value) -> str:
    return f"{int(value):,}".replace(",", " ")


def _population(value) -> str:
    value = float(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".replace(".", ",").replace(",0", "") + " млн"
    if value >= 1000:
        return f"{value / 1000:.0f} тыс."
    return _number(value)


def _hours(diff) -> str:
    if diff is None:
        return ""
    if not diff:
        return "как в Амстердаме"
    value = f"{abs(diff):g}".replace(".", ",")
    return f"на {value} ч {'больше' if diff > 0 else 'меньше'}, чем в Амстердаме"


def country_lines(facts, guide=None) -> list:
    """[(подпись, значение)] для блока «Страна»; пустые факты пропускаются."""
    country = facts.get("country") or {}
    guide = guide or {}
    lines = []
    if country.get("capital"):
        lines.append(("Столица", country["capital"]))
    population = facts.get("population") or {}
    if population.get("value"):
        year = f" ({population['year']})" if population.get("year") else ""
        lines.append(("Население", _population(population["value"]) + year))
    if country.get("area"):
        lines.append(("Площадь", f"{_number(country['area'])} км²"))
    languages = [guide.get("languages_ru", {}).get(name, name) for name in country.get("languages") or []]
    if languages:  # все языки страны: у ЮАР их 11 — «и ещё 9» ничего не говорит
        lines.append(("Языки", ", ".join(languages)))
    if country.get("currency"):
        currency = country["currency"] + (f" ({country['symbol']})" if country.get("symbol") else "")
        rate = facts.get("rate")
        if rate:
            symbol = country.get("symbol") or country["currency"]
            currency += f" · 1 € = {rate:.2f}".replace(".", ",") + f" {symbol}"
        lines.append(("Валюта", currency))
    if _hours(facts.get("hours")):
        lines.append(("Время", _hours(facts.get("hours"))))
    if country.get("calling"):
        lines.append(("Телефонный код", country["calling"]))
    tips = guide.get("tips") or {}  # памятка путешественника — только то, что AI знает наверняка
    for label, key in (("Экстренный номер", "emergency"), ("Розетки", "plugs"), ("Чаевые", "tipping"),
                       ("Вода из-под крана", "water")):
        if str(tips.get(key) or "").strip():
            lines.append((label, tips[key]))
    return lines


def _hours_phrase(diff) -> str:
    """«на 1 час больше, чем в Амстердаме» / «как в Амстердаме»; нет данных — ""."""
    if diff is None:
        return ""
    if not diff:
        return "как в Амстердаме"
    value = abs(diff)
    number = f"{value:g}".replace(".", ",")
    if value != int(value):
        word = "часа"
    else:
        n = int(value)
        word = "час" if n % 10 == 1 and n % 100 != 11 else "часа" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) \
            else "часов"
    return f"на {number} {word} {'больше' if diff > 0 else 'меньше'}, чем в Амстердаме"


def trip_brief(facts, guide=None, brief=None):
    """Досуг в отпуске: («Коротко о стране» [строки], «Полезно знать» [строки]) — «Столица - Претория»,
    «Валюта - ранд (ZAR) · 1 € ≈ 18,58 R», «Полиция: 10111»…; пустые факты пропускаются."""
    country = facts.get("country") or {}
    guide, brief = guide or {}, brief or {}
    about = []
    capital = brief.get("capital_ru") or country.get("capital")
    if capital:
        about.append(f"Столица - {capital}")
    population = facts.get("population") or {}
    if population.get("value"):
        year = f" ({population['year']})" if population.get("year") else ""
        about.append(f"Население {_population(population['value'])}{year}")
    if country.get("area"):
        about.append(f"Площадь {_number(country['area'])} км²")
    if country.get("currency"):
        code = country["currency"]
        currency = f"{brief['currency_ru']} ({code})" if brief.get("currency_ru") else code
        rate = facts.get("rate")
        if rate:
            symbol = country.get("symbol") or code
            currency += f" · 1 € ≈ {rate:.2f}".replace(".", ",") + f" {symbol}"
        about.append(f"Валюта - {currency}")
    hours = _hours_phrase(facts.get("hours"))
    if hours:
        about.append(f"Время {hours}")
    if country.get("calling"):
        about.append(f"Телефонный код {country['calling']}")
    useful = []
    numbers = [(label, brief.get(key)) for label, key in (("Экстренный номер", "emergency"), ("Полиция", "police"),
                                                          ("Скорая помощь", "ambulance")) if brief.get(key)]
    if numbers:
        useful.extend(f"{label}: {value}" for label, value in numbers)
    elif str((guide.get("tips") or {}).get("emergency") or "").strip():  # без разбора по службам — строкой гида
        useful.append(f"Экстренный номер: {guide['tips']['emergency']}")
    plugs = str((guide.get("tips") or {}).get("plugs") or "").strip()
    if plugs:
        useful.append(f"Розетки: {plugs.replace(', ', ' · ')}")
    return about, useful


def holiday_line(holiday) -> str:
    """«День свободы · 27 апреля · В этот день в 1994 году…» — праздник дня внизу Досуга в отпуске; нет — ""."""
    holiday = holiday or {}
    return " · ".join(part for part in (holiday.get("name"), holiday.get("date"), holiday.get("about")) if part)
