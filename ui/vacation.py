"""Тексты режима «✈️ В отпуске»: вход, возвращение и блок «Страна»."""
from .builder import MessageBuilder


def ask_city():
    b = MessageBuilder()
    b.section("✈️ В отпуске")
    b.spacer()
    b.line("Напиши город, куда едешь, и сроки, например «Cape Town до 20 октября».")
    b.spacer()
    b.italic("Погода, праздники, кухня, концерты и премьеры переключатся на эту страну, "
             "«Обучение» — на язык страны, а в «Моём дне» появятся факты о ней.")
    b.newline()
    return b.build_stripped()


def suitcase_trip_label(trip):
    """Кнопка поездки сохранённого чемодана на экране «В отпуске»: «🧳 Кейптаун · до 27 октября»."""
    from datetime import date
    from util import _MONTHS
    end = date.fromisoformat(trip["end"])
    return f"🧳 {trip['city']} · до {end.day} {_MONTHS[end.month - 1]}"


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
    b.italic("Вернуться раньше — кнопкой «Вернуться домой» под «Моим днём» или в Настройках.")
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
