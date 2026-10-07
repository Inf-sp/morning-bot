"""Общая inline-навигация для коротких служебных и fallback-экранов."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def nav_row(back="m_menu"):
    return [
        InlineKeyboardButton("⬅️ Назад", callback_data=back),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]


def back_menu_keyboard(back="m_menu"):
    return InlineKeyboardMarkup([nav_row(back)])
