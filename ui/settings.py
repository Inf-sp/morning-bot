from .builder import MessageBuilder, MessageSpec
from .constants import PREFERENCES_LABEL, ui_label


def notifications(next_line=""):
    b = MessageBuilder()
    b.section(ui_label("broadcasts", "Уведомления"))
    b.line("На кнопке — что придёт и когда.")
    b.line("Зелёные — включены, без цвета — выключены. Нажми, чтобы выбрать время или выключить.")
    if next_line:
        b.spacer()
        b.line(next_line)
    return b.build_stripped()


def myday_blocks():
    b = MessageBuilder()
    b.section("☀️ Мой день")
    b.line("Выбери, что показывать в сводке. Зелёные — показываются, без цвета — скрыты.")
    return b.build_stripped()


def notification_kind(label, *, has_time=True):
    """Экран одной рассылки: что приходит и когда."""
    b = MessageBuilder()
    b.section(f"🔔 {label}")
    b.line("Выбери, присылать ли и в какое время." if has_time else "Приходит сразу, когда есть повод.")
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
    b.line("Ниже — какие приёмы пищи готовишь и ограничения в еде.")
    return b.build_stripped()


def city_input():
    return MessageSpec(text="📍 Напиши город — переключу.")


def wardrobe_item_input():
    b = MessageBuilder()
    b.text_line("Напиши вещь: тип + цвет + детали/бренд.\n")
    b.italic("Напр.: «Футболка белая Uniqlo» или «Шорты серые тонкие». Можно списком.")
    return b.build()


def wardrobe_style(groups):
    """Экран «Стиль»: две группы с описанием; выбор — переключателями под экраном.

    groups — [(название, описание)].
    """
    b = MessageBuilder()
    b.section("Стиль")
    b.spacer()
    b.line("Выбери стили, которые тебе близки. Изменения сохраняются сразу.")
    for label, about in groups:
        b.spacer()
        b.bold(label)
        b.newline()
        b.line(about)
    return b.build_stripped()


def settings_home(city="", language="", summary=""):
    """Главный экран Настроек: жирные подписи «Город:», «Язык обучения:», «Уведомления:»."""
    b = MessageBuilder()
    b.section(ui_label("settings", "Настройки"))
    b.spacer()
    b.labeled_line("Город", city or "не выбран", lowercase=False)
    if language:
        b.labeled_line("Язык обучения", f"{language[:1].upper()}{language[1:]}", lowercase=False)
    if summary:
        b.labeled_line("Уведомления", summary)
    b.spacer()
    b.italic("Здесь общие параметры бота. Свои данные разделов (шкаф, холодильник, словарь, "
             "библиотека) — кнопкой «Настроить» внутри раздела.")
    b.newline()
    return b.build_stripped()


def settings_trip(trip="", home="", language="", summary=""):
    """Настройки в отпуске — только то, что верно в поездке: «Отпуск:», «Дома:», «Язык в поездке:» (если
    «Обучение» на языке страны), «Уведомления:» без рассылок на паузе."""
    b = MessageBuilder()
    b.section(f"{ui_label('settings', 'Настройки')} в отпуске")
    b.spacer()
    b.labeled_line("Отпуск", trip or "—", lowercase=False)
    if home:
        b.labeled_line("Дома", home, lowercase=False)
    if language:
        b.labeled_line("Язык в поездке", language, lowercase=False)
    if summary:
        b.labeled_line("Уведомления", summary)
    b.spacer()
    b.italic("В отпуске здесь только то, что нужно в поездке. Предпочтения, город и данные разделов вернутся "
             "после отпуска.")
    b.newline()
    return b.build_stripped()


def preferences_home(summary=()):
    """Все предпочтения из разделов: «Кухни: Итальянская, Японская»; ниже кнопки для изменения."""
    b = MessageBuilder()
    b.section(PREFERENCES_LABEL)
    b.spacer()
    for label, value in summary:
        b.labeled_line(label, value, lowercase=False)
    if not summary:
        b.line("Выбери, что учитывать в рекомендациях.")
    return b.build_stripped()


def mydata_section(title, hint=""):
    b = MessageBuilder().section(title)
    if hint:
        b.line(hint)
    return b.build_stripped()


def admin_only():
    return MessageSpec(text="❌ Только для администратора.")


def my_data():
    b = MessageBuilder()
    b.section("🗂 Мои данные")
    b.spacer()
    b.italic("Шкаф, холодильник, словарь и библиотека — поправь, не выходя из отпуска.")
    return b.build_stripped()
