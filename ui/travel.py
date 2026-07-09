from .builder import MessageBuilder
from .constants import ui_label


def _pluralize_countries(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "страна"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "страны"
    return "стран"


def home_screen(visited_count, fav_count, plan_count):
    """Главный экран раздела «Путешествия»: польза, сколько стран посещено/в
    любимых/в планах. Тот же визуальный паттерн, что у Гардероба и Кино (home_screen)."""
    b = MessageBuilder()
    b.text_line("✈️ ")
    b.bold("Путешествия")
    b.newline()
    b.spacer()
    b.line("Подбираю страны и направления под твой вкус — и помогаю собрать план поездки.")

    b.spacer()
    if visited_count <= 0 and fav_count <= 0 and plan_count <= 0:
        b.line("Пока пусто — начни с подбора новой страны.")
    else:
        if visited_count > 0:
            b.line(f"🌍 Посещено {visited_count} {_pluralize_countries(visited_count)}")
        if fav_count > 0:
            b.line(f"❤️ В любимых {fav_count} {_pluralize_countries(fav_count)}")
        if plan_count > 0:
            b.line(f"{ui_label('routes', 'Маршрутов')} {plan_count}")

    return b.build_stripped()


def country_card(data):
    """Составная карточка (условные блоки) -> MessageBuilder."""
    b = MessageBuilder()
    b.text_line(f"{data.get('flag', '')} ")
    b.bold(data.get("country", ""))
    b.newline()
    if data.get("about"):
        b.spacer()
        b.line(data["about"])
    if data.get("for_what"):
        b.spacer()
        b.text_line("🎯 ")
        b.bold("Ради чего ехать:")
        b.line(f" {data['for_what']}")
    if data.get("langs"):
        b.spacer()
        b.text_line("🗣️ ")
        b.bold("Язык:")
        b.line(f" {data['langs']}")
    if data.get("note"):
        b.spacer()
        b.text_line("⚠️ ")
        b.bold("Главный нюанс:")
        b.line(f" {data['note']}")
    if data.get("fact"):
        b.spacer()
        b.text_line("🔎 ")
        b.bold("Факт:")
        b.line(f" {data['fact']}")
    return b.build_stripped()


def travel_plan(plan, fallback_country):
    """Текст плана путешествия персистируется как (text, entities) в NOTES_KEY (bucket='plan')
    и режется на chunks через util.chunk_text_with_entities в settings.plan_view — компоненты
    подходят так же, как для остальных карточек этого файла."""
    country = plan.get("title", fallback_country)
    b = MessageBuilder()
    b.text_line(f"{plan.get('flag', '')} ")
    b.bold(country)
    b.newline()
    if plan.get("about"):
        b.spacer()
        b.line(plan["about"])
    if plan.get("why"):
        b.spacer()
        b.text_line("🎯 ")
        b.bold("Почему тебе подойдёт")
        b.newline()
        for w in plan["why"]:
            b.bullet(str(w))
    if plan.get("best_time"):
        b.spacer()
        b.text_line("📅 ")
        b.bold("Лучшее время")
        b.newline()
        b.line(plan["best_time"])
    if plan.get("budget"):
        b.spacer()
        b.text_line("💰 ")
        b.bold("Бюджет")
        b.newline()
        for item in plan["budget"]:
            b.bullet(str(item))
    if plan.get("spots"):
        b.spacer()
        b.text_line("📸 ")
        b.bold("Не пропусти")
        b.newline()
        for spot in plan["spots"]:
            b.bullet(str(spot))
    if plan.get("lgbt"):
        b.spacer()
        b.text_line("🏳️‍🌈 ")
        b.bold("LGBTQ+")
        b.newline()
        b.line(plan["lgbt"])
    if plan.get("fact"):
        b.spacer()
        b.text_line("🍲 ")
        b.bold("Интересный факт")
        b.newline()
        b.line(plan["fact"])
    return b.build_stripped()
