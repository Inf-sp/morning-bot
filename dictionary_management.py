"""Mutation flows for dictionary entries, kept outside the screen controller."""

import re
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import store
from dictionary_model import display_term, normalize_term_case
from ui.constants import delete_label
from ui.navigation import back_menu_keyboard
from ui import dictionary as dict_ui


def _dictionary():
    import learning_dictionary
    return learning_dictionary


def _loose_text(lang, word):
    value = re.sub(r"\s+", " ", str(word or "").strip()).rstrip(".").casefold()
    if lang == "nl":
        value = re.sub(r"^(de|het|een)\s+", "", value)
    elif lang == "en":
        value = re.sub(r"^(to|the|a|an)\s+", "", value)
    return value


async def confirm_delete_dict_entry(bot, cid, lang, term_key, q=None):
    dictionary = _dictionary()
    await dictionary._show_screen(
        bot, cid, "Точно удалить это из словаря?", None,
        InlineKeyboardMarkup([
            [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_dictdelok_{lang}_{term_key}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
             InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
        ]), q=q,
    )


async def confirm_delete_dict_entry_by_id(bot, cid, word_id, q=None):
    dictionary = _dictionary()
    entry = dictionary._entry_by_id(cid, word_id)
    if not entry:
        await dictionary.send_dict(bot, cid, q=q)
        return
    display = display_term(dictionary._entry_term(entry), entry.get("article") or "")
    await dictionary._show_screen(
        bot, cid, f"Удалить «{display}» из словаря?", None,
        InlineKeyboardMarkup([
            [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"a_dictdelokid_{word_id}")],
            [InlineKeyboardButton("Отмена", callback_data=f"a_dictlang_{dictionary._dict_lang(entry)}")],
        ]), q=q,
    )


async def confirm_delete_dict_category_entry(
    bot, cid, lang, category_index, page, word_id, q=None,
):
    """Подтверждает удаление и сохраняет позицию в перелистываемой категории."""
    dictionary = _dictionary()
    if not 0 <= category_index < len(dictionary._DICT_CATEGORY_ORDER):
        await dictionary.send_dict_lang(bot, cid, lang, q=q)
        return
    category = dictionary._DICT_CATEGORY_ORDER[category_index]
    entry = dictionary._entry_by_id(cid, word_id)
    if (not entry or dictionary._dict_lang(entry) != lang
            or dictionary._dictionary_category(entry) != category):
        await dictionary.send_dict_category(
            bot, cid, lang, category_index, page=page, q=q,
        )
        return
    display = display_term(dictionary._entry_term(entry), entry.get("article") or "")
    await dictionary._show_screen(
        bot, cid, f"Удалить «{display}» из словаря?", None,
        InlineKeyboardMarkup([
            [InlineKeyboardButton(
                delete_label("Удалить"),
                callback_data=(
                    f"a_dictcatdelok_{lang}_{category_index}_{page}_{word_id}"
                ),
            )],
            [InlineKeyboardButton(
                "Отмена",
                callback_data=f"a_dictcat_{lang}_{category_index}_{page}",
            )],
        ]),
        q=q,
    )


async def confirm_move_dict_entry_by_id(bot, cid, word_id, q=None):
    dictionary = _dictionary()
    entry = dictionary._entry_by_id(cid, word_id)
    if not entry:
        await dictionary.send_dict(bot, cid, q=q)
        return
    source_lang = dictionary._dict_lang(entry)
    target_lang = "en" if source_lang == "nl" else "nl"
    display = display_term(dictionary._entry_term(entry), entry.get("article") or "")
    target_title = "английский" if target_lang == "en" else "нидерландский"
    await dictionary._show_screen(
        bot, cid, f"Переместить «{display}» в {target_title} словарь?", None,
        InlineKeyboardMarkup([
            [InlineKeyboardButton("↔️ Переместить", callback_data=f"a_dictmoveok_{word_id}_{target_lang}")],
            [InlineKeyboardButton("Отмена", callback_data=f"a_dictlang_{source_lang}")],
        ]), q=q,
    )


async def move_dict_entry_by_id(bot, cid, word_id, target_lang, q=None):
    dictionary = _dictionary()
    if target_lang not in ("nl", "en"):
        return
    words = store.get_list(config.DICT_KEY, cid)
    entry = next((item for item in words if str(item.get("id") or "") == str(word_id)), None)
    if not entry:
        await dictionary.send_dict(bot, cid, q=q)
        return
    source_lang = dictionary._dict_lang(entry)
    if source_lang == target_lang:
        return
    loose = _loose_text(target_lang, dictionary._entry_term(entry))
    duplicate = next((item for item in words
                      if item is not entry and dictionary._dict_lang(item) == target_lang
                      and _loose_text(target_lang, dictionary._entry_term(item)) == loose), None)
    if duplicate:
        await dictionary._show_screen(
            bot, cid, "Такая запись уже есть в другом словаре.", None,
            dictionary._dict_manage_kb(target_lang), q=q,
        )
        return
    updated = dict(entry)
    updated["lang"] = target_lang
    updated["updated_at"] = datetime.now(config.TZ).isoformat()
    words[words.index(entry)] = updated
    store.set_list(config.DICT_KEY, cid, words)
    from dictionary_import import _dict_entry_message, _dict_saved_kb
    msg = _dict_entry_message(updated, status="updated")
    await dictionary._show_screen(
        bot, cid, msg.text, msg.entities,
        _dict_saved_kb(updated, show_dictionary=True), q=q, persistent_inline=True,
    )


async def del_dict_entry_by_id(bot, cid, word_id, page=None, q=None):
    dictionary = _dictionary()
    words = dictionary.normalize_user_dictionary(cid)
    removed = next((item for item in words if str(item.get("id") or "") == str(word_id)), None)
    if removed:
        store.set_list(config.DICT_KEY, cid, [item for item in words if item is not removed])
    msg = dict_ui.dict_deleted(removed)
    lang = dictionary._dict_lang(removed) if removed else dictionary._active_language_code(cid)
    if page is not None:
        await dictionary._show_screen(
            bot, cid, msg.text, msg.entities, back_menu_keyboard(f"a_dictedit_{lang}_{page}"), q=q)
        return
    await dictionary._show_screen(bot, cid, msg.text, msg.entities, dictionary._dict_manage_kb(lang), q=q)


async def del_dict_category_entry(
    bot, cid, lang, category_index, page, word_id, q=None,
):
    """Удаляет текущую карточку и остаётся в той же категории."""
    dictionary = _dictionary()
    if not 0 <= category_index < len(dictionary._DICT_CATEGORY_ORDER):
        await dictionary.send_dict_lang(bot, cid, lang, q=q)
        return
    category = dictionary._DICT_CATEGORY_ORDER[category_index]
    words = dictionary.normalize_user_dictionary(cid)
    removed = next((
        item for item in words
        if (str(item.get("id") or "") == str(word_id)
            and dictionary._dict_lang(item) == lang
            and dictionary._dictionary_category(item) == category)
    ), None)
    if removed is None:
        await dictionary.send_dict_category(
            bot, cid, lang, category_index, page=page, q=q,
        )
        return
    remaining = [
        item for item in words
        if item is not removed
    ]
    store.set_list(config.DICT_KEY, cid, remaining)
    category_entries = [
        item for item in dictionary._dict_lang_entries(cid, lang)
        if dictionary._dictionary_category(item) == category
    ]
    next_page = min(max(0, int(page)), max(0, len(category_entries) - 1))
    await dictionary.send_dict_category(
        bot, cid, lang, category_index, page=next_page, q=q,
    )


async def del_dict_entry_by_term(bot, cid, lang, term_key, page=None, q=None):
    dictionary = _dictionary()
    words = dictionary.normalize_user_dictionary(cid)
    removed = None
    kept = []
    for item in words:
        if (dictionary._dict_lang(item) == lang
                and dictionary._dict_entry_matches_key(item, lang, term_key)
                and removed is None):
            removed = item
            continue
        kept.append(item)
    if removed:
        store.set_list(config.DICT_KEY, cid, kept)
    msg = dict_ui.dict_deleted(removed)
    if page is not None:
        await dictionary._show_screen(
            bot, cid, msg.text, msg.entities, back_menu_keyboard(f"a_dictedit_{lang}_{page}"), q=q)
        return
    await dictionary._show_screen(bot, cid, msg.text, msg.entities, dictionary._dict_manage_kb(lang), q=q)


async def del_word(bot, cid, index):
    dictionary = _dictionary()
    words = dictionary.normalize_user_dictionary(cid)
    removed = ""
    removed_lang = None
    if index < len(words):
        removed_item = words.pop(index)
        removed = normalize_term_case(
            dictionary._entry_term(removed_item), dictionary._kind_of(dictionary._entry_term(removed_item)))
        removed_lang = dictionary._dict_lang(removed_item)
        store.set_list(config.DICT_KEY, cid, words)
    lang = removed_lang or dictionary._active_language_code(cid)
    msg = dict_ui.dict_deleted(removed or "")
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=dictionary._dict_manage_kb(lang),
    )


def dict_entry_view_kb(entry, page, term_key):
    dictionary = _dictionary()
    lang = dictionary._dict_lang(entry)
    word_id = str(entry.get("id") or "")
    delete_row = ([[InlineKeyboardButton(
        delete_label("Удалить"), callback_data=f"a_dictviewdelid_{page}_{word_id}")]]
        if word_id else [])
    return InlineKeyboardMarkup(dictionary._dict_tts_row(entry) + delete_row + [
        [InlineKeyboardButton("🎚️ Мой словарь", callback_data=f"a_dictlang_{lang}_keep")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"a_dictlang_{lang}"),
         InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])
