"""Общая inline-навигация для коротких служебных и fallback-экранов."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def back_menu_keyboard(back="m_menu"):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад", callback_data=back),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ]])
