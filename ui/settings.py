from .builder import MessageBuilder, MessageSpec
from .constants import PREFERENCES_LABEL, ui_label


def notifications():
    b = MessageBuilder()
    b.section(ui_label("broadcasts", "Уведомления"))
    b.line("На каждой кнопке — название и время того, что тебе придёт.")
    b.line("Нажми для включения/выключения. ✅ — включено.")
    return b.build_stripped()


def personalization():
    b = MessageBuilder()
    b.section(ui_label("personalization", "Персонализация"))
    b.line("Постоянные предпочтения — влияют на подбор образа, рецептов, кино и музыки.")
    return b.build_stripped()


def cuisines(current):
    b = MessageBuilder()
    b.section(PREFERENCES_LABEL)
    b.line("Выбери кухни, которые нравятся — подберу рецепт дня и блюда из холодильника с их учётом.")
    b.spacer()
    b.bold("Сейчас:")
    b.line(f" {current}")
    return b.build_stripped()


def city_input():
    return MessageSpec(text="📍 Напиши город — переключу.")


def wardrobe_item_input():
    b = MessageBuilder()
    b.text_line("Напиши вещь: тип + цвет + детали/бренд.\n")
    b.italic("Напр.: «Футболка белая Uniqlo» или «Шорты серые тонкие». Можно списком.")
    return b.build()


def wardrobe_style(styles):
    b = MessageBuilder()
    b.section("🧵 Стиль")
    b.spacer()
    b.labeled_line("Стиль", " · ".join(styles) if styles else "не выбран")
    b.line("Выбери до трёх стилей. Изменения сохраняются сразу.")
    return b.build_stripped()


def settings_home(city="", notifications_on=True):
    b = MessageBuilder()
    b.section(ui_label("settings", "Настройки"))
    b.spacer()
    b.line(f"📍 Город: {city or 'не выбран'}")
    b.line(f"🔔 Уведомления: {'включены' if notifications_on else 'выключены'}")
    return b.build_stripped()


def preferences_home():
    b = MessageBuilder()
    b.section(PREFERENCES_LABEL)
    b.line("Выбери, что учитывать в рекомендациях.")
    return b.build_stripped()


def lifehacks_home(total, records=None, page=0, total_pages=1):
    b = MessageBuilder()
    b.section("🦉 Лайфхаки")
    b.line(f"Всего: {total}")
    if records:
        b.spacer()
        for item in records:
            category = str(item.get("category") or "разное").capitalize()
            text = " ".join(str(item.get("text") or "").split())
            b.labeled_line(category, text[:180], lowercase=False)
    elif not total:
        b.spacer()
        b.line("Записей пока нет.")
    if total_pages > 1:
        b.spacer()
        b.line(f"Страница {page + 1} из {total_pages}")
    return b.build_stripped()


def lifehacks_list(title, records, page=0, total_pages=1):
    b = MessageBuilder()
    b.section(title)
    if not records:
        b.line("Записей пока нет.")
    else:
        for item in records:
            category = str(item.get("category") or "разное").capitalize()
            text = " ".join(str(item.get("text") or "").split())
            b.labeled_line(category, text[:180], lowercase=False)
    if total_pages > 1:
        b.spacer()
        b.line(f"Страница {page + 1} из {total_pages}")
    return b.build_stripped()


def lifehack_edit_input(text):
    b = MessageBuilder()
    b.section("✏️ Изменить лайфхак")
    b.labeled_line("Сейчас", text, lowercase=False)
    b.line("Напиши новую формулировку одним сообщением.")
    return b.build_stripped()


def lifehack_delete_confirm(text):
    b = MessageBuilder()
    b.section("❌ Удалить лайфхак?")
    b.line(text)
    return b.build_stripped()


def mydata_section(title, hint=""):
    b = MessageBuilder().section(title)
    if hint:
        b.line(hint)
    return b.build_stripped()


def admin_only():
    return MessageSpec(text="❌ Только для администратора.")
