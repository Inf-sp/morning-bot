from .builder import MessageBuilder


def plural_countries(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "страна"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "страны"
    return "стран"


def visited_summary(n):
    verb = "Посещена" if abs(int(n)) % 10 == 1 and abs(int(n)) % 100 != 11 else "Посещено"
    return f"{verb} {n} {plural_countries(n)}"


def home_screen(idea, visited_count):
    b = MessageBuilder()
    b.text_line(f"{idea['emoji']} ")
    b.bold(f"Поездка на сегодня · {idea['transport_title']}")
    b.newline()
    b.spacer()
    b.line(idea["intro"])
    b.spacer()
    b.bold(f"{idea['from']} → {idea['to']}")
    b.newline()
    b.spacer()
    b.bold("Маршрут:")
    b.newline()
    for item in idea.get("route", [])[:3]:
        b.bullet(str(item).replace(" = ", " → "))
    b.spacer()
    b.line(f"Прогресс: посещено {visited_count} {plural_countries(visited_count)}")
    b.spacer()
    b.line(f"💡 Полезно: {idea['tip']}")
    return b.build_stripped()


def countries_screen(count, page, pages):
    b = MessageBuilder()
    b.title("🗺️ Мои страны")
    b.line(f"{count} {plural_countries(count)} уже в твоей истории путешествий.")
    if not count:
        b.spacer()
        b.line("Пока здесь пусто. Добавь страну, в которой уже был.")
    else:
        b.spacer()
        b.line("Выбери страну, чтобы посмотреть её карточку или удалить из списка.")
    return b.build_stripped()


def visited_country_card(data):
    b = MessageBuilder()
    b.text_line(f"{data.get('flag', '')} ")
    b.bold(data.get("country_name", ""))
    b.newline()
    for key, label in (
        ("description", ""),
        ("highlight", "✨ Чем запоминается"),
        ("languages", "👩🏻‍🏫 Языки"),
        ("currency", "💰 Валюта"),
        ("main_nuance", "⚠️ Главный нюанс"),
        ("fact", "🔍 Факт"),
    ):
        value = data.get(key)
        if not value:
            continue
        if isinstance(value, list):
            value = ", ".join(value)
        b.spacer()
        if label:
            b.labeled_line(label, str(value))
        else:
            b.line(str(value))
    return b.build_stripped()


def country_card(data):
    b = MessageBuilder()
    b.text_line(f"{data.get('flag', '')} ")
    b.bold(data.get("country", ""))
    b.newline()
    if data.get("about"):
        b.spacer(); b.line(data["about"])
    if data.get("for_what"):
        b.spacer(); b.labeled_line("✨ Ради чего ехать", data["for_what"])
    if data.get("langs"):
        b.spacer(); b.labeled_line("👩🏻‍🏫 Языки", data["langs"])
    if data.get("note"):
        b.spacer(); b.labeled_line("⚠️ Главный нюанс", data["note"])
    if data.get("fact"):
        b.spacer(); b.labeled_line("🔍 Факт", data["fact"])
    return b.build_stripped()


def travel_plan(plan, fallback_country):
    country = plan.get("title", fallback_country)
    b = MessageBuilder()
    b.text_line(f"{plan.get('flag', '')} "); b.bold(country); b.newline()
    if plan.get("about"):
        b.spacer(); b.line(plan["about"])
    for key, title in (("why", "Почему тебе подойдёт"), ("budget", "Бюджет"), ("spots", "Не пропусти")):
        if plan.get(key):
            b.spacer(); b.bold(title); b.newline()
            for item in plan[key]: b.bullet(str(item))
    if plan.get("best_time"):
        b.spacer(); b.labeled_line("Лучшее время", plan["best_time"])
    if plan.get("lgbt"):
        b.spacer(); b.labeled_line("LGBTQ+", plan["lgbt"])
    if plan.get("fact"):
        b.spacer(); b.labeled_line("🔍 Факт", plan["fact"])
    return b.build_stripped()


def transport_screen(current):
    b = MessageBuilder()
    b.title("*️⃣ Выбрать транспорт")
    b.line("Можно выбрать несколько вариантов.")
    b.spacer()
    b.labeled_line("Сейчас", current or "не выбран")
    return b.build_stripped()
