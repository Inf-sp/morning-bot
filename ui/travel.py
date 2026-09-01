from .builder import MessageBuilder
from .news import append_weekly_news
from telegram import MessageEntity


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


def home_screen(idea, rebus=None, *, news=None):
    b = MessageBuilder()
    title = idea.get("transport_title") or ""
    # Заголовок: жирный, четвёртая по общему языку карточек разделов.
    b.text_line(f"{idea.get('emoji', '🗺️')} ")
    b.bold("Место дня" + (f" · {title}" if title else ""))
    b.newline()

    to = str(idea.get("to") or "")
    intro = str(idea.get("intro") or "")
    # Название места выделяем жирным, описание — рядом через тире.
    if to:
        b.bold(to)
        if intro and not intro.startswith(to):
            intro_low = intro[0].lower() + intro[1:] if intro else ""
            b.line(f" — {intro_low}")
        elif intro:
            b.line(f" — {intro}")
        else:
            b.newline()
    elif intro:
        b.line(intro)

    route = idea.get("route")
    if route:
        b.section("Что интересного:")
        for point in route:
            b.bullet(str(point))

    tip = idea.get("tip")
    if tip:
        tip_text = tip[0].upper() + tip[1:] if tip else ""
        b.spacer()
        b.labeled_line("💡 Полезно", tip_text, lowercase=False)

    if news:
        b.spacer()
        append_weekly_news(b, news)
    return b.build_stripped()


def visited_news_screen(items):
    b = MessageBuilder()
    b.title("💡 Что интересного")
    if not items:
        b.line("Добавь посещённые страны в «Мой чемодан» — здесь появятся интересные обновления.")
        return b.build_stripped()
    for item in items[:5]:
        b.spacer()
        b.bold(f"{item.get('flag', '')} {item.get('country', '')}".strip())
        b.newline()
        b.line(str(item.get("fact") or ""))
        if item.get("place"):
            b.bold(str(item["place"]))
            b.newline()
        for detail in item.get("details") or []:
            b.line(str(detail))
    return b.build_stripped()


def countries_screen(count, page, pages):
    b = MessageBuilder()
    b.title("🎚️ Мой чемодан")
    b.line(f"В чемодане: {count} {plural_countries(count)}.")
    if not count:
        b.spacer()
        b.line("Пока здесь пусто. Добавь страну, в которой уже был.")
    else:
        b.spacer()
        b.line("Выбери страну, чтобы посмотреть её карточку или убрать из чемодана.")
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


def travel_plan(plan, fallback_country):
    country = plan.get("title", fallback_country)
    b = MessageBuilder()
    b.text_line(f"{plan.get('flag', '')} "); b.bold(country); b.newline()
    if plan.get("about"):
        b.spacer(); b.line(plan["about"])
    if plan.get("fit"):
        b.spacer(); b.labeled_line("✨ Тебе подойдёт", plan["fit"])
    if plan.get("spots"):
        b.spacer(); b.bold("📍 Не пропусти"); b.newline()
        for item in plan["spots"]: b.bullet(str(item))
    if plan.get("best_time"):
        b.spacer(); b.labeled_line("☀️ Когда ехать", plan["best_time"])
    if plan.get("budget"):
        b.spacer(); b.labeled_line("💶 Бюджет", plan["budget"])
    if plan.get("languages"):
        b.spacer(); b.labeled_line("👩🏻‍🏫 Языки", " · ".join(plan["languages"]))
    if plan.get("lgbt"):
        b.spacer(); b.labeled_line("🏳️‍🌈 LGBTQ+", plan["lgbt"])
    return b.build_stripped()
