from .builder import MessageSpec
from util import esc


def notifications():
    return MessageSpec(
        text="🔔 <b>Уведомления</b>\n\nНажми для включения/выключения. 🟢 — включено.",
        parse_mode="HTML",
    )


def priorities(current):
    return MessageSpec(
        text=(
            "🎯 <b>Приоритеты</b>\n\n"
            "Выбери, на что боту обращать больше внимания в брифе, советах и рекомендациях.\n\n"
            f"<b>Сейчас:</b> {esc(current)}"
        ),
        parse_mode="HTML",
    )


def body_profile(profile_line):
    return MessageSpec(
        text=(
            "🎚️ <b>Мои параметры</b>\n\n"
            "Бот использует эти данные при подборе образа и оценке покупок — "
            "чтобы советы по размеру и силуэту подходили именно тебе.\n\n"
            f"<b>Сейчас сохранено:</b>\n{profile_line}\n\n"
            "<b>Напиши одним сообщением:</b>\n"
            "рост, размеры одежды, обуви и брюк, а также стиль одежды.\n\n"
            "<i>Пример: рост 178 см, размер M/L, обувь EU 43, брюки W32 L32. "
            "Стиль: тёмные оттенки, оверсайз, минимум принтов.</i>"
        ),
        parse_mode="HTML",
    )


def style_pick():
    return MessageSpec(
        text="🎨 <b>Стиль одежды</b>\n\nВыбери из предложенных или опиши своими словами — бот учтёт при подборе образа:",
        parse_mode="HTML",
    )


def settings_home():
    return MessageSpec(
        text="🎚️ <b>Настройки</b>\n\nНастройте бота под себя и управляйте личными данными.",
        parse_mode="HTML",
    )


def leisure_settings():
    return MessageSpec(
        text="🍿 <b>Настройки досуга</b>\n\nКино, страны, артисты и книги для рекомендаций.",
        parse_mode="HTML",
    )
