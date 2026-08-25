"""Чистый UI выбора пользовательского экспорта."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .builder import MessageBuilder


def export_choice():
    b = MessageBuilder()
    b.section("📤 Экспорт данных")
    b.line("Что сохранить в файл?")
    msg = b.build()
    msg.text = msg.text.rstrip("\n")
    return msg


def export_choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Всё", callback_data="as_export_all")],
        [InlineKeyboardButton("📤 Мой шкаф", callback_data="as_export_wardrobe")],
        [InlineKeyboardButton("📤 Мой холодильник", callback_data="as_export_fridge")],
        [InlineKeyboardButton("📤 Мой словарь", callback_data="as_export_dictionary")],
        [InlineKeyboardButton("📤 Любимое", callback_data="as_export_favorites")],
        [InlineKeyboardButton("📤 Поездки", callback_data="as_export_travel")],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="m_settings"),
            InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
        ],
    ])
