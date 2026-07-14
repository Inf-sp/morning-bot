"""Общая доставка AI-карточек без зависимости от предметных разделов."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import store
import util
import verify
from ui import balance as balance_ui


def keyboard(rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(title, callback_data=callback) for title, callback in row]
        for row in rows
    ])


def clean_card_text(value):
    return balance_ui.clean_card_text(value)


def build_entity_card(title, summary="", quote="", bullets=None, final="",
                      bullet_label="Рекомендации:", emoji=""):
    message = balance_ui.entity_card(
        title, summary, quote, bullets, final, bullet_label, emoji=emoji)
    return message.text, message.entities


def answer_keyboard(cont_label="Продолжить", cont_callback="chat_retry", depth=True):
    rows = []
    if cont_label and cont_callback:
        rows.append([(cont_label, cont_callback)])
    if depth:
        rows.append([("Короче", "ans_short"), ("Глубже", "ans_deep")])
    rows.append([("⭐️ Сохранить", "as_fav")])
    rows.append([("⬅️ Назад", "m_close"), ("🏠 Меню", "m_menu")])
    return keyboard(rows)


def back_keyboard():
    return keyboard([[("⬅️ Назад", "m_close"), ("🏠 Меню", "m_menu")]])


async def send_response(bot, cid, text, kb=None, surface="card"):
    text = (text or "").strip() or "Пусто, попробуй ещё раз."
    text, warnings = verify.grade_text(text, surface)
    for warning in warnings:
        print(f"[verify] {surface}: {warning}")
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Ассистент")
    store.last_surface[str(cid)] = surface
    rendered = util.tg_html(text)
    chunks = [rendered[i:i + 4000] for i in range(0, len(rendered), 4000)]
    for index, chunk in enumerate(chunks):
        markup = (kb if kb is not None else answer_keyboard()) if index == len(chunks) - 1 else None
        try:
            await bot.send_message(
                chat_id=cid, text=chunk, parse_mode="HTML", reply_markup=markup)
        except Exception:
            await bot.send_message(chat_id=cid, text=chunk, reply_markup=markup)
