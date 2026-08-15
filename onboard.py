"""Онбординг нового пользователя: имя → город → язык → уровень → готово."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
from ui import onboarding as onboarding_ui

# in-memory кеш шага (быстрый доступ; _onboard_step в профиле — персистентный бэкап)
_ob: dict = {}

_LANG_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🇳🇱 Нидерландский", callback_data="ob_lang_nl"),
     InlineKeyboardButton("🇬🇧 Английский",    callback_data="ob_lang_en")],
    [InlineKeyboardButton("Пропустить",       callback_data="ob_lang_skip")],
])

def _lvl_kb(code: str) -> InlineKeyboardMarkup:
    levels = [
        ("simple", "🔽 Простой (A1 - A2)"),
        ("hard", "🔼 Сложный (B1+)"),
    ]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"ob_lvl_{code}_{level}")]
        for level, label in levels
    ])


def _save_step(cid, step: str | None):
    """Персистируем шаг в профиле — выживает при рестарте бота."""
    def change(profile):
        if step:
            profile["_onboard_step"] = step
        else:
            profile.pop("_onboard_step", None)
        return profile, None

    store.mutate_profile(cid, change)


def get_text_step(cid) -> str | None:
    """Возвращает шаг онбординга, требующий текстового ввода ('name' или 'city').
    Проверяет сначала in-memory, потом профиль (на случай рестарта)."""
    st = _ob.get(str(cid), {})
    step = st.get("step") or store.get_profile(cid).get("_onboard_step")
    return step if step in ("name", "city") else None


async def start(bot, cid):
    _ob[str(cid)] = {"step": "name"}
    _save_step(cid, "name")
    store.pending_input[str(cid)] = "onboard_name"
    msg = onboarding_ui.onboard_start()
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        transient=True,
    )


async def handle_name(bot, cid, text: str):
    name = text.strip()[:50]
    store.mutate_profile(cid, lambda profile: ({**profile, "name": name}, None))
    _ob.setdefault(str(cid), {})["step"] = "city"
    _save_step(cid, "city")
    store.pending_input[str(cid)] = "onboard_city"
    msg = onboarding_ui.onboard_name_saved(name)
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        transient=True,
    )


async def handle_city(bot, cid, text: str):
    import weather as _wx
    await _wx.set_city_text(bot, cid, text, show_brief=False)
    _ob.setdefault(str(cid), {})["step"] = "lang"
    _save_step(cid, None)          # текстовый ввод больше не нужен
    store.pending_input.pop(str(cid), None)
    msg = onboarding_ui.onboard_language_question()
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        reply_markup=_LANG_KB,
        transient=True,
    )


async def handle_callback(bot, cid, q, data: str):
    st = _ob.get(str(cid), {})

    if data.startswith("ob_lang_"):
        choice = data[len("ob_lang_"):]
        if choice == "skip":
            store.set_learning_language(cid, "none")
            await _finish(bot, cid)
            return
        if choice not in ("nl", "en"):
            return
        import settings as _s
        st["language"] = choice
        store.set_learning_language(cid, choice)
        _s.set_(cid, "study_lang", "нидерландский" if choice == "nl" else "английский")
        st["step"] = "lvl"
        _ob[str(cid)] = st
        await _ask_level(bot, cid, q, choice)
        return

    if data.startswith("ob_lvl_"):
        _, _, code, level = data.split("_")
        lang = "нидерландский" if code == "nl" else "английский"
        store.set_level(cid, lang, level)
        if st.get("language") not in (None, code):
            return
        await _finish(bot, cid)
        return


async def _ask_level(bot, cid, q, code):
    msg = onboarding_ui.onboard_level_question(code)
    try:
        edited = await q.edit_message_text(
            msg.text,
            reply_markup=_lvl_kb(code),
        )
        marker = getattr(bot, "mark_transient_message", None)
        if marker:
            marker(cid, getattr(edited, "message_id", None) or q.message.message_id)
    except Exception:
        await bot.send_message(
            chat_id=cid,
            text=msg.text,
            reply_markup=_lvl_kb(code),
            transient=True,
        )


async def _finish(bot, cid):
    import menu
    _ob.pop(str(cid), None)
    _save_step(cid, None)
    store.pending_input.pop(str(cid), None)
    store.mutate_profile(cid, lambda profile: (
        {**profile, menu.REPLY_KB_REMOVED_FLAG: True}, None,
    ))
    msg = menu.welcome_for(cid)
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=menu.main_menu_kb(),
        transient=True,
    )
