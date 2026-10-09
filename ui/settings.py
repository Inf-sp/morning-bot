from .builder import MessageBuilder, MessageSpec
from .constants import PREFERENCES_LABEL, ui_label


def notifications():
    b = MessageBuilder()
    b.section(ui_label("broadcasts", "Уведомления"))
    b.line("На кнопке — что придёт и когда.")
    b.line("Зелёные — включены, красные — выключены. Нажми, чтобы переключить.")
    return b.build_stripped()


def personalization():
    b = MessageBuilder()
    b.section(ui_label("personalization", "Персонализация"))
    b.line("Постоянные предпочтения — влияют на подбор образа, рецептов, кино и музыки.")
    return b.build_stripped()


def cuisines():
    b = MessageBuilder()
    b.section(PREFERENCES_LABEL)
    b.line("Выбери кухни, которые нравятся — подберу рецепт дня и блюда из холодильника с их учётом.")
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
    b.section("Стиль")
    b.spacer()
    b.labeled_line("Стиль", " · ".join(styles) if styles else "не выбран", lowercase=False)
    b.line("Выбери любые стили. Изменения сохраняются сразу.")
    return b.build_stripped()


def settings_home(city="", notifications=(), language=""):
    """Главный экран Настроек: город, язык обучения и список включённых уведомлений."""
    b = MessageBuilder()
    b.section(ui_label("settings", "Настройки"))
    b.spacer()
    b.line(f"📍 Город: {city or 'не выбран'}")
    if language:
        b.line(f"🧠 Язык обучения: {language[:1].upper()}{language[1:]}")
    notifications = [str(name).strip() for name in notifications or () if str(name).strip()]
    if not notifications:
        b.line("🔔 Уведомления: выключены")
        return b.build_stripped()
    b.line("🔔 Уведомления:")
    for name in notifications:
        b.line(f"- {name}")
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
