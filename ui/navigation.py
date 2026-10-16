"""Общая inline-навигация для коротких служебных и fallback-экранов."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def nav_row(back="m_menu"):
    return [
        InlineKeyboardButton("⬅️ Назад", callback_data=back),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]


def notif_row():
    """Под каждой плановой рассылкой: «Настроить уведомления» (экран уведомлений новым сообщением) | «Главная».
    Кнопки «Отключить уведомления» под рассылками нет — всё отключается в настройках уведомлений."""
    return [
        InlineKeyboardButton("🎚️ Настроить уведомления", callback_data="set_notif_new"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]


def back_menu_keyboard(back="m_menu"):
    return InlineKeyboardMarkup([nav_row(back)])
