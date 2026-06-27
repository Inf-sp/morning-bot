"""Онбординг нового пользователя: имя → город → языки → уровень → готово."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import store
from util import esc

# in-memory состояние онбординга (сбрасывается при рестарте, это нормально)
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

def is_onboarding(cid) -> bool:
    return str(cid) in _ob


async def start(bot, cid):
    _ob[str(cid)] = {"step": "name", "langs": []}
    store.pending_input[str(cid)] = "onboard_name"
    await bot.send_message(
        chat_id=cid,
        text=(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Давай познакомимся — это займёт меньше минуты, и бот сразу будет знать тебя.\n\n"
            "Как тебя зовут?"
        ),
        parse_mode="HTML",
    )


async def handle_name(bot, cid, text: str):
    name = text.strip()[:50]
    prof = store.get_profile(cid)
    prof["name"] = name
    store.set_profile(cid, prof)
    _ob[str(cid)]["step"] = "city"
    store.pending_input[str(cid)] = "onboard_city"
    await bot.send_message(
        chat_id=cid,
        text=(
            f"Приятно познакомиться, <b>{esc(name)}</b>! 🙌\n\n"
            "🌍 Из какого ты города? Напишу текстом — настрою погоду и контекст для советов."
        ),
        parse_mode="HTML",
    )


async def handle_city(bot, cid, text: str):
    import weather as _wx
    # set_city_text сама отправляет подтверждение/ошибку
    await _wx.set_city_text(bot, cid, text)
    _ob[str(cid)]["step"] = "lang"
    store.pending_input.pop(str(cid), None)
    await bot.send_message(
        chat_id=cid,
        text="🌐 Какие языки изучаешь? Настрою тренажёр и грамматику.",
        reply_markup=_LANG_KB,
    )


async def handle_callback(bot, cid, q, data: str):
    st = _ob.get(str(cid), {})

    if data.startswith("ob_lang_"):
        choice = data[len("ob_lang_"):]
        if choice == "skip":
            await _finish(bot, cid)
            return
        if choice == "both":
            st["langs"] = ["nl", "en"]
        else:
            st["langs"] = [choice]
        st["step"] = "lvl"
        st["lvl_queue"] = list(st["langs"])
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
        if queue:
            await _ask_next_level(bot, cid, q)
        else:
            await _finish(bot, cid)
        return

    if data == "ob_done":
        await _finish(bot, cid)


async def _ask_next_level(bot, cid, q):
    st = _ob.get(str(cid), {})
    queue = st.get("lvl_queue", [])
    if not queue:
        await _finish(bot, cid); return
    code = queue[0]
    flag = "🇳🇱" if code == "nl" else "🇬🇧"
    lang = "нидерландского" if code == "nl" else "английского"
    try:
        await q.edit_message_text(
            f"{flag} Какой у тебя уровень {lang}?",
            reply_markup=_lvl_kb(code),
        )
    except Exception:
        await bot.send_message(
            chat_id=cid,
            text=f"{flag} Какой у тебя уровень {lang}?",
            reply_markup=_lvl_kb(code),
        )


async def _finish(bot, cid):
    import menu
    _ob.pop(str(cid), None)
    store.pending_input.pop(str(cid), None)
    await bot.send_message(
        chat_id=cid,
        text=menu.WELCOME,
        parse_mode="HTML",
        reply_markup=menu.MAIN_KB,
    )
