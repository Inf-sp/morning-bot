"""Чистый UI выбора пользовательского экспорта."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .builder import MessageBuilder
from ui.navigation import nav_row


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
        nav_row("m_settings"),
    ])
