"""Онбординг нового пользователя: имя → город → языки → уровень → приоритеты → готово."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
from ui import onboarding as onboarding_ui

# in-memory кеш шага (быстрый доступ; _onboard_step в профиле — персистентный бэкап)
_ob: dict = {}

_LANG_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🇳🇱 Нидерландский", callback_data="ob_lang_nl"),
     InlineKeyboardButton("🇬🇧 Английский",    callback_data="ob_lang_en")],
    [InlineKeyboardButton("Оба языка",          callback_data="ob_lang_both")],
    [InlineKeyboardButton("⏭ Пропустить",       callback_data="ob_lang_skip")],
])

def _lvl_kb(code: str) -> InlineKeyboardMarkup:
    levels = ["A1", "A2", "B1", "B2", "C1"]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(l, callback_data=f"ob_lvl_{code}_{l}") for l in levels]
    ])


def _prio_kb(cid) -> InlineKeyboardMarkup:
    import settings as _s
    selected = set(_s.priorities(cid))
    buttons = [
        InlineKeyboardButton(
            ("✅ " if key in selected else "⬜ ") + label,
            callback_data=f"ob_prio_{key}",
        )
        for key, label in _s.PRIORITY_OPTIONS
    ]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("Готово", callback_data="ob_prio_done")])
    return InlineKeyboardMarkup(rows)


def _save_step(cid, step: str | None):
    """Персистируем шаг в профиле — выживает при рестарте бота."""
    prof = store.get_profile(cid)
    if step:
        prof["_onboard_step"] = step
    else:
        prof.pop("_onboard_step", None)
    store.set_profile(cid, prof)


def get_text_step(cid) -> str | None:
    """Возвращает шаг онбординга, требующий текстового ввода ('name' или 'city').
    Проверяет сначала in-memory, потом профиль (на случай рестарта)."""
    st = _ob.get(str(cid), {})
    step = st.get("step") or store.get_profile(cid).get("_onboard_step")
    return step if step in ("name", "city") else None


async def start(bot, cid):
    _ob[str(cid)] = {"step": "name", "langs": []}
    _save_step(cid, "name")
    store.pending_input[str(cid)] = "onboard_name"
    msg = onboarding_ui.onboard_start()
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
    )


async def handle_name(bot, cid, text: str):
    name = text.strip()[:50]
    prof = store.get_profile(cid)
    prof["name"] = name
    store.set_profile(cid, prof)
    _ob.setdefault(str(cid), {})["step"] = "city"
    _save_step(cid, "city")
    store.pending_input[str(cid)] = "onboard_city"
    msg = onboarding_ui.onboard_name_saved(name)
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
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
    )


async def handle_callback(bot, cid, q, data: str):
    st = _ob.get(str(cid), {})

    if data.startswith("ob_lang_"):
        choice = data[len("ob_lang_"):]
        if choice == "skip":
            await _ask_priorities(bot, cid, q)
            return
        import settings as _s
        if choice == "both":
            st["langs"] = ["nl", "en"]
            _s.set_(cid, "study_lang", "нидерландский")
        else:
            st["langs"] = [choice]
            _s.set_(cid, "study_lang", "нидерландский" if choice == "nl" else "английский")
        st["step"] = "lvl"
        st["lvl_queue"] = list(st["langs"])
        _ob[str(cid)] = st
        await _ask_next_level(bot, cid, q)
        return

    if data.startswith("ob_lvl_"):
        _, _, code, level = data.split("_")
        lang = "нидерландский" if code == "nl" else "английский"
        store.set_level(cid, lang, level)
        queue = st.get("lvl_queue", [])
        if queue and queue[0] == code:
            queue.pop(0)
        st["lvl_queue"] = queue
        _ob[str(cid)] = st
        if queue:
            await _ask_next_level(bot, cid, q)
        else:
            await _ask_priorities(bot, cid, q)
        return

    if data.startswith("ob_prio_"):
        key = data[len("ob_prio_"):]
        if key == "done":
            await _finish(bot, cid)
            return
        import settings as _s
        valid = {k for k, _ in _s.PRIORITY_OPTIONS}
        if key in valid:
            selected = _s.priorities(cid)
            if key in selected:
                selected = [k for k in selected if k != key]
            else:
                selected.append(key)
            _s.set_(cid, "priorities", selected)
            if key == "quiet" and key in selected:
                for kind, _label in _s.NOTIF_TYPES:
                    if kind not in ("morning_brief", "weather_warn"):
                        _s.set_(cid, f"notif_{kind}", False)
        await _ask_priorities(bot, cid, q)
        return


async def _ask_next_level(bot, cid, q):
    st = _ob.get(str(cid), {})
    queue = st.get("lvl_queue", [])
    if not queue:
        await _finish(bot, cid); return
    code = queue[0]
    msg = onboarding_ui.onboard_level_question(code)
    try:
        await q.edit_message_text(
            msg.text,
            reply_markup=_lvl_kb(code),
        )
    except Exception:
        await bot.send_message(
            chat_id=cid,
            text=msg.text,
            reply_markup=_lvl_kb(code),
        )


async def _ask_priorities(bot, cid, q=None):
    st = _ob.setdefault(str(cid), {})
    st["step"] = "prio"
    _ob[str(cid)] = st
    msg = onboarding_ui.onboard_priorities_question()
    kb = _prio_kb(cid)
    if q is not None:
        try:
            await q.edit_message_text(msg.text, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, reply_markup=kb)


async def _finish(bot, cid):
    import learning
    _ob.pop(str(cid), None)
    _save_step(cid, None)
    store.pending_input.pop(str(cid), None)
    await learning.send_seed_intro(bot, cid)
