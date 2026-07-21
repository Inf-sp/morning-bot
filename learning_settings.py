"""Настройки активного языка и уровней обучения."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import store
from ui import learning as learning_ui
from dictionary_seed_ui import LEVEL_LABELS, SEED_LEVELS as LEVELS


def _code(language):
    if language in ("nl", "en"):
        return language
    return "nl" if language == "нидерландский" else "en"


def _active_language_code(cid):
    code = store.get_learning_language(cid)
    if code in ("nl", "en"):
        return code
    import settings
    return _code(settings.study_lang(cid))


def _language_for_code(code):
    return "английский" if code == "en" else "нидерландский"


def active_language(cid):
    return _language_for_code(_active_language_code(cid))


def _language_display(language):
    flag = "🇳🇱" if _code(language) == "nl" else "🇬🇧"
    title = "Нидерландский" if _code(language) == "nl" else "Английский"
    return f"{flag} {title}"


def _level_label(level):
    return LEVEL_LABELS.get(level, "Средний")
# ================= НАСТРОЙКИ ОБУЧЕНИЯ =================
def learning_settings_kb(active_lang, active_level, back="set_home"):
    row = []
    for level in LEVELS:
        mark = "✅ " if level == active_level else ""
        row.append(InlineKeyboardButton(f"{mark}{LEVEL_LABELS[level]}", callback_data=f"set_learning_level_{level}"))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇳🇱 Нидерландский" if _code(active_lang) == "nl" else "🇬🇧 Английский", callback_data="toggle_learning_language")],
        row,
        [InlineKeyboardButton("⬅️ Назад", callback_data=back), InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu")],
    ])


async def send_learning_settings(bot, cid, q=None, back="set_home"):
    active_lang = active_language(cid)
    active_level = store.get_level(cid, active_lang)
    msg = learning_ui.learning_settings(_language_display(active_lang), _level_label(active_level))
    kb = learning_settings_kb(active_lang, active_level, back)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_levels(bot, cid, q=None, back="set_home"):
    await send_learning_settings(bot, cid, q=q, back=back)


async def handle_learning_settings_callback(bot, cid, q, data):
    back = "m_learn"
    if data == "set_learning":
        await send_learning_settings(bot, cid, q=q, back=back)
        return
    if data == "toggle_learning_language":
        old_code = _active_language_code(cid)
        new_code = "en" if old_code == "nl" else "nl"
        store.set_learning_language(cid, new_code)
        store.ensure_level(cid, _language_for_code(old_code), "medium")
        store.ensure_level(cid, _language_for_code(new_code), "medium")
        prof = store.get_profile(cid)
        prof.pop("_myday_seed_prompted", None)
        store.set_profile(cid, prof)
        await send_learning_settings(bot, cid, q=q, back=back)
        return
    if data.startswith("set_learning_level_"):
        level = data[len("set_learning_level_"):]
        if level in LEVELS:
            language = active_language(cid)
            old_level = store.get_level(cid, language)
            store.set_level(cid, language, level)
            await send_learning_settings(bot, cid, q=q, back=back)
            if old_level != level:
                from dictionary_seed import offer_seed_for_level_change
                await offer_seed_for_level_change(bot, cid, language, level)
            return
        await send_learning_settings(bot, cid, q=q, back=back)


SYSTEM_TOPICS = {
    "нидерландский": {
        "A1": [
            "Порядок слов (SVO)",
            "Артикли de/het",
            "Спряжение глаголов в настоящем",
            "Отрицание niet/geen",
            "Вопросительные предложения",
            "Личные местоимения",
            "Множественное число существительных",
            "Числительные и время",
            "Притяжательные местоимения",
            "Предлоги места",
        ],
        "A2": [
            "Perfectum (voltooide tijd)",
            "Инверсия",
            "Разделяемые глаголы",
            "Er-конструкции",
            "Степени сравнения прилагательных",
            "Imperfectum (onvoltooid verleden)",
            "Придаточные с dat/omdat",
            "Возвратные глаголы (zich)",
            "Предлоги времени",
            "Сочинительные союзы",
        ],
        "B1": [
            "Страдательный залог (passief)",
            "Косвенная речь",
            "Придаточные с omdat/want",
            "Модальные глаголы (moeten/mogen/kunnen)",
            "Относительные местоимения (die/dat/wie/wat)",
            "Futurum (zullen/gaan)",
            "Условные предложения с als",
            "Отделяемые и неотделяемые приставки",
            "Плюсквамперфект",
            "Инфинитивные обороты с te",
        ],
    },
    "английский": {
        "A1": [
            "Present Simple",
            "Артикли a/an/the",
            "Вопросы с do/does",
            "Отрицание don't/doesn't",
            "There is/are",
            "Личные и притяжательные местоимения",
            "Множественное число существительных",
            "Предлоги места (in/on/at/under)",
            "Числительные и время",
            "Глагол to be",
        ],
        "A2": [
            "Present Continuous",
            "Past Simple",
            "Going to (планы)",
            "Модальные can/must/should",
            "Степени сравнения прилагательных",
            "Past Continuous",
            "Future Simple (will)",
            "Предлоги времени (in/on/at/since/for)",
            "Союзы but/because/so/although",
            "Вопросительные слова (who/what/where/when/why/how)",
        ],
        "B1": [
            "Present Perfect",
            "Passive Voice",
            "Reported Speech",
            "Conditionals 1 & 2",
            "Придаточные времени и условия",
            "Past Perfect",
            "Модальные could/would/might",
            "Герундий и инфинитив",
            "Относительные придаточные (who/which/that)",
            "Фразовые глаголы (phrasal verbs)",
        ],
    },
}
