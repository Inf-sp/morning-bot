"""Настройки активного языка и уровней обучения."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import store
from ui import learning as learning_ui
from dictionary_seed_ui import LEVEL_LABELS, SEED_LEVELS as LEVELS
import rich_delivery
from ui.navigation import nav_row


def _code(language):
    if language in ("nl", "en"):
        return language
    return "nl" if language == "нидерландский" else "en"


def _active_language_code(cid):
    if not store.learning_is_enabled(cid):
        return ""
    code = store.get_learning_language(cid)
    if code in ("nl", "en"):
        return code
    import settings
    return _code(settings.study_lang(cid))


def _language_for_code(code):
    return "английский" if code == "en" else "нидерландский"


def active_language(cid):
    code = _active_language_code(cid)
    return _language_for_code(code) if code else "не изучаю"


def _language_display(language):
    if language in ("", "none", "не изучаю"):
        return "🚫 Не изучаю"
    flag = "🇳🇱" if _code(language) == "nl" else "🇬🇧"
    title = "Нидерландский" if _code(language) == "nl" else "Английский"
    return f"{flag} {title}"


def _level_label(level):
    return LEVEL_LABELS.get(level, LEVEL_LABELS["simple"])


def _suffix(back):
    if back == "a_dict":
        return "_dict_home"
    if back == "a_dictlang_active":
        return "_dict"
    if back == "m_settings":
        return "_settings"
    if back == "set_preferences":
        return "_prefs"
    return ""


def _language_menu_callback(back):
    if back == "a_dict":
        return "set_learning_dictionary"
    if back == "a_dictlang_active":
        return "set_learning_dict"
    if back == "m_settings":
        return "set_learning_global"
    if back == "set_preferences":
        return "set_pref_learning"
    return "set_learning"


def _reset_learning_caches(cid):
    import learning
    learning.reset_daily_material_cache(cid)
    import myday
    myday.reset_day_cache(cid)


# ================= НАСТРОЙКИ ОБУЧЕНИЯ =================
def learning_settings_kb(active_lang, active_level, back="set_home"):
    suffix = _suffix(back)
    active_code = {
        "nl": "nl", "нидерландский": "nl",
        "en": "en", "английский": "en",
    }.get(str(active_lang or "").strip().lower(), "")
    rows = [
        [InlineKeyboardButton(
            f"{'✅ ' if active_code == 'nl' else '□ '}🇳🇱 Нидерландский",
            callback_data=f"set_learning_language_nl{suffix}",
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if active_code == 'en' else '□ '}🇬🇧 Английский",
            callback_data=f"set_learning_language_en{suffix}",
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if not active_code else '□ '}🚫 Не изучаю",
            callback_data=f"set_learning_language_none{suffix}",
        )],
    ]
    rows.append(nav_row(back))
    return InlineKeyboardMarkup(rows)


def learning_level_kb(active_level, back="set_home"):
    suffix = _suffix(back)
    rows = [
        [InlineKeyboardButton(
            f"{'✅ ' if level == active_level else '□ '}{LEVEL_LABELS[level]}",
            callback_data=f"set_learning_level_{level}{suffix}",
        )]
        for level in LEVELS
    ]
    rows.append(nav_row(_language_menu_callback(back)))
    return InlineKeyboardMarkup(rows)


async def send_learning_settings(bot, cid, q=None, back="set_home"):
    active_code = _active_language_code(cid)
    active_lang = _language_for_code(active_code) if active_code else "не изучаю"
    active_level = store.get_level(cid, active_lang) if active_code else ""
    msg = learning_ui.learning_settings(
        _language_display(active_lang), _level_label(active_level) if active_level else "",
    )
    kb = learning_settings_kb(active_code, active_level, back)
    await rich_delivery.show(bot, cid, msg, reply_markup=kb, query=q)


async def send_learning_level_picker(bot, cid, code, q=None, back="set_home"):
    if code not in ("nl", "en"):
        await send_learning_settings(bot, cid, q=q, back=back)
        return
    language = _language_for_code(code)
    level = store.get_level(cid, language)
    msg = learning_ui.learning_level_settings(_language_display(language))
    kb = learning_level_kb(level, back)
    await rich_delivery.show(bot, cid, msg, reply_markup=kb, query=q)


async def send_levels(bot, cid, q=None, back="set_home"):
    code = _active_language_code(cid)
    if code:
        await send_learning_level_picker(bot, cid, code, q=q, back=back)
    else:
        await send_learning_settings(bot, cid, q=q, back=back)


async def handle_learning_settings_callback(bot, cid, q, data):
    dictionary_origin = (
        data == "set_learning_dict"
        or data == "toggle_learning_language_dict"
        or data.startswith("set_learning_language_") and data.endswith("_dict")
        or data.endswith("_dict") and data.startswith("set_learning_level_")
    )
    dictionary_home_origin = (
        data == "set_learning_dictionary"
        or data.endswith("_dict_home") and data.startswith("set_learning_language_")
        or data.endswith("_dict_home") and data.startswith("set_learning_level_")
    )
    settings_origin = (
        data == "set_learning_global"
        or data.startswith("set_learning_language_") and data.endswith("_settings")
        or data.endswith("_settings") and data.startswith("set_learning_level_")
    )
    preferences_origin = (
        data == "set_pref_learning"
        or data.startswith("set_learning_language_") and data.endswith("_prefs")
        or data.endswith("_prefs") and data.startswith("set_learning_level_")
    )
    back = ("a_dict" if dictionary_home_origin else
            ("a_dictlang_active" if dictionary_origin else
            ("set_preferences" if preferences_origin else
             ("m_settings" if settings_origin else "m_learn"))))
    if data in ("set_learning", "set_learning_dict", "set_learning_dictionary", "set_learning_global", "set_pref_learning"):
        await send_learning_settings(bot, cid, q=q, back=back)
        return
    if data in ("toggle_learning_language", "toggle_learning_language_dict"):
        old_code = _active_language_code(cid)
        new_code = "en" if old_code == "nl" else "nl"
        store.set_learning_language(cid, new_code)
        store.ensure_level(cid, _language_for_code(new_code), "simple")
        _reset_learning_caches(cid)
        await send_learning_level_picker(bot, cid, new_code, q=q, back=back)
        return
    if data.startswith("set_learning_language_"):
        code = data[len("set_learning_language_"):]
        if code.endswith("_dict"):
            code = code[:-len("_dict")]
        elif code.endswith("_dict_home"):
            code = code[:-len("_dict_home")]
        elif code.endswith("_settings"):
            code = code[:-len("_settings")]
        elif code.endswith("_prefs"):
            code = code[:-len("_prefs")]
        if code not in ("nl", "en", "none"):
            await send_learning_settings(bot, cid, q=q, back=back)
            return
        store.set_learning_language(cid, code)
        if code in ("nl", "en"):
            store.ensure_level(cid, _language_for_code(code), "simple")
        _reset_learning_caches(cid)
        if code in ("nl", "en"):
            await send_learning_level_picker(bot, cid, code, q=q, back=back)
        else:
            await send_learning_settings(bot, cid, q=q, back=back)
        return
    if data.startswith("set_learning_level_"):
        level = data[len("set_learning_level_"):]
        if level.endswith("_dict"):
            level = level[:-len("_dict")]
        elif level.endswith("_dict_home"):
            level = level[:-len("_dict_home")]
        elif level.endswith("_settings"):
            level = level[:-len("_settings")]
        elif level.endswith("_prefs"):
            level = level[:-len("_prefs")]
        if level in LEVELS:
            language = active_language(cid)
            if language == "не изучаю":
                await send_learning_settings(bot, cid, q=q, back=back)
                return
            old_level = store.get_level(cid, language)
            store.set_level(cid, language, level)
            _reset_learning_caches(cid)
            await send_learning_settings(bot, cid, q=q, back=back)
            if old_level != level:
                from dictionary_seed import offer_seed_for_level_change
                await offer_seed_for_level_change(bot, cid, language, level)
            return
        await send_learning_settings(bot, cid, q=q, back=back)


