from .builder import MessageBuilder
from .constants import ui_label


def _pluralize_countries(n):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return "страна"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return "страны"
    return "стран"


def home_screen(visited_count, plan_count, facts=None):
    """Главный экран раздела «Поездки»: сколько стран посещено/в планах и несколько
    фактов о посещённых странах пользователя. Тот же паттерн, что у Гардероба и Кино."""
    b = MessageBuilder()
    b.text_line("✈️ ")
    b.bold("Поездки")
    b.newline()
    b.spacer()
    b.line("Подбираю страны и направления под твой вкус — и помогаю собрать план поездки.")

    b.spacer()
    if visited_count <= 0 and plan_count <= 0:
        b.line("Пока пусто — начни с подбора новой страны.")
    else:
        if visited_count > 0:
            b.line(f"🧳 Посещено {visited_count} {_pluralize_countries(visited_count)}")
        if plan_count > 0:
            b.line(f"{ui_label('routes', 'Маршрутов')} {plan_count}")

    facts = facts or []
    if facts:
        b.spacer()
        b.bold("Знаешь ли ты?")
        b.newline()
        for fact in facts:
            b.spacer()
            b.bold(fact["title"])
            b.newline()
            b.line(fact["text"])

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
        b.bold(ui_label("reason", "Ради чего ехать:"))
        b.line(f" {data['for_what']}")
    if data.get("langs"):
        b.spacer()
        b.bold(ui_label("spoken_language", "Язык:"))
        b.line(f" {data['langs']}")
    if data.get("note"):
        b.spacer()
        b.text_line("⚠️ ")
        b.bold("Главный нюанс:")
        b.line(f" {data['note']}")
    if data.get("fact"):
        b.spacer()
        b.text_line("🔍 ")
        b.bold(ui_label("interesting", "Факт:"))
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
        b.bold(ui_label("recommendation", "Почему тебе подойдёт"))
        b.newline()
        for w in plan["why"]:
            b.bullet(str(w))
    if plan.get("best_time"):
        b.spacer()
        b.bold(ui_label("best_time", "Лучшее время"))
        b.newline()
        b.line(plan["best_time"])
    if plan.get("budget"):
        b.spacer()
        b.bold(ui_label("budget", "Бюджет"))
        b.newline()
        for item in plan["budget"]:
            b.bullet(str(item))
    if plan.get("spots"):
        b.spacer()
        b.bold(ui_label("dont_miss", "Не пропусти"))
        b.newline()
        for spot in plan["spots"]:
            b.bullet(str(spot))
    if plan.get("lgbt"):
        b.spacer()
        b.bold(ui_label("lgbtq", "LGBTQ+"))
        b.newline()
        b.line(plan["lgbt"])
    if plan.get("fact"):
        b.spacer()
        b.bold(ui_label("interesting", "Интересный факт"))
        b.newline()
        b.line(plan["fact"])
    return b.build_stripped()


# ================= ИНТЕРЕСНЫЕ ФАКТЫ О СТРАНЕ =================

def facts_prompt_screen():
    b = MessageBuilder()
    b.text_line("🧭 ")
    b.bold("О какой стране рассказать?")
    b.newline()
    b.spacer()
    b.line("Напиши название страны на русском, английском или нидерландском.")
    b.spacer()
    b.line("Пример:")
    b.quote("Япония")
    return b.build_stripped()


def facts_card(country_name, facts):
    """Факты списком: жирное короткое название, с новой строки сам факт, без
    канцеляризмов и без списка источников (см. docs/travel.md, «Интересные факты»)."""
    b = MessageBuilder()
    b.text_line("🧭 ")
    b.bold(f"Интересные факты о {country_name}")
    b.newline()
    b.spacer()
    for fact in facts:
        b.bold(fact["title"])
        b.newline()
        b.line(fact["text"])
        b.spacer()
    return b.build_stripped()


def facts_not_found_screen():
    b = MessageBuilder()
    b.text_line("🌍 ")
    b.bold("Не нашёл такую страну")
    b.newline()
    b.spacer()
    b.line("Проверь название или напиши его по-другому.")
    b.spacer()
    b.line("Например: Япония, Japan или Nederland.")
    return b.build_stripped()


def facts_search_unavailable_screen():
    b = MessageBuilder()
    b.text_line("⚠️ ")
    b.bold("Не удалось найти факты")
    b.newline()
    b.spacer()
    b.line("Сервис поиска сейчас недоступен. Попробуй ещё раз позже.")
    return b.build_stripped()


def facts_exhausted_screen():
    b = MessageBuilder()
    b.text_line("🧭 ")
    b.bold("Больше сильных фактов не нашлось")
    b.newline()
    b.spacer()
    b.line("Я уже показал самые интересные и хорошо подтверждённые факты об этой стране.")
    return b.build_stripped()


def facts_not_found_for_country_screen():
    b = MessageBuilder()
    b.text_line("🧭 ")
    b.bold("Не нашлось сильных фактов")
    b.newline()
    b.spacer()
    b.line("Не получилось найти достаточно подтверждённых фактов об этой стране. Попробуй ещё раз или выбери другую страну.")
    return b.build_stripped()
