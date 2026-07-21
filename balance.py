from datetime import datetime
import hashlib
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store

_log = logging.getLogger(__name__)
import ai
import verify
import secure
from ui import balance as balance_ui
from ui import food as food_ui
from ui.constants import CUISINE_EMOJI, ui_label
import settings
import menu
import thoughts
from response_delivery import (
    answer_keyboard as _ans_kb,
    send_response as _send,
)

TZ = config.TZ

HEALTH_PRINCIPLES = (
    ("sleep", "Сон и восстановление"),
    ("movement", "Движение каждый день"),
    ("nutrition", "Регулярное питание"),
    ("calm", "Меньше перегруза"),
    ("screen", "Меньше экрана"),
    ("outdoors", "Больше свежего воздуха"),
)
_HEALTH_PRINCIPLE_LABELS = dict(HEALTH_PRINCIPLES)


def health_principles(cid):
    saved = settings.get(cid, "health_principles", [])
    if not isinstance(saved, list):
        return []
    return [key for key in saved if key in _HEALTH_PRINCIPLE_LABELS]


def _mark_transient_edit(bot, cid, message):
    marker = getattr(bot, "mark_transient_message", None)
    if marker is not None:
        marker(cid, getattr(message, "message_id", None))


async def send_health_principles(bot, cid, q=None):
    selected = set(health_principles(cid))
    buttons = [
        InlineKeyboardButton(
            ("✅ " if key in selected else "") + label,
            callback_data=f"as_health_principle_{key}",
        )
        for key, label in HEALTH_PRINCIPLES
    ]
    rows = [[button] for button in buttons]
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_balance"),
        InlineKeyboardButton("#️⃣ Главная", callback_data="m_menu"),
    ])
    msg = balance_ui.health_principles(len(selected))
    kb = InlineKeyboardMarkup(rows)
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            _mark_transient_edit(bot, cid, q.message)
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        reply_markup=kb,
        transient=True,
    )


async def toggle_health_principle(bot, cid, key, q=None):
    if key not in _HEALTH_PRINCIPLE_LABELS:
        await send_health_principles(bot, cid, q=q)
        return
    selected = health_principles(cid)
    if key in selected:
        selected = [item for item in selected if item != key]
    else:
        selected.append(key)
    settings.set_(cid, "health_principles", selected)
    await send_health_principles(bot, cid, q=q)

# ---------- Фокус на сегодня ----------
_FOCUS_PHRASES = (
    *(('emotion', text) for text in (
        "Это раздражение, не угроза.", "Пауза - победа.", "Чужие эмоции - не моя ответственность.",
        "Остановись. Выдохни. Потом действуй.", "Это состояние пройдёт.",
        "Представь, что все чудаки.", "Нежелательная мысль → «Отмена» три раза.",
    )),
    *(('action', text) for text in (
        "Сейчас не вся жизнь. Сейчас один шаг.", "Мне не нужно идеально. Мне нужно начать.",
        "Я не ленивый. Мой мозг так работает.", "Я делаю лучшее из возможного сегодня.",
        "От чего наполняешься - то и монетизируй.", "Риск важнее идеала.",
        "Скука - мой криптонит. Создаю интерес сам.", "Скромности мало. Мир продвигает видимых.",
        "Требовать своих прав - здоровое, не наглость.", "Сделано лучше идеального. Закрой и выложи.",
        "Застрял - уменьши шаг, не бросай задачу.", "Действие гасит тревогу быстрее, чем анализ.",
        "Дискомфорт нового = вход в индустрию, а не стоп.",
    )),
    *(('values', text) for text in (
        "Не пропускай зло дальше себя.", "Фокус на хорошем, благодарность за мелочи.",
        "Уважай границы, говори открыто.",
        "Любовь важна, но не единственное. Цени поддержку, создавай воспоминания.",
        "Родители - взрослые. Ты не отвечаешь за их чувства.", "Не все споры стоят нервов.",
        "Перемены открывают возможности.", "Окружение влияет - ищи своё, не терпи.",
        "Книги - радость и рост.", "Путешествия важнее материального.",
        "Избавляйся от лишнего, освобождай место новому.",
        "Баланс работа / отдых / движение - необходим.", "Переключайся, но не убегай.",
    )),
)

_FOCUS_GUIDANCE = {
    "emotion": (
        ("Назови чувство одним словом и сделай три спокойных выдоха.",
         "Отложи ответ на пять минут, если эмоция ещё сильная."),
        "Короткая пауза отделяет чувство от действия и помогает ответить спокойнее.",
    ),
    "action": (
        ("Выбери не больше трёх задач на сегодня.",
         "Начни с одной задачи, которую можно сделать за 5–10 минут."),
        "Короткий список снижает перегрузку и помогает быстрее перейти от мыслей к действию.",
    ),
    "values": (
        ("Выбери одно решение, которое сегодня поддержит эту мысль.",
         "Сделай небольшой конкретный шаг до конца дня."),
        "Связь принципа с одним действием помогает не оставлять важное только намерением.",
    ),
}


def health_focus(cid):
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    digest = hashlib.sha256(f"{cid}:{day}".encode()).digest()
    kind, phrase = _FOCUS_PHRASES[int.from_bytes(digest[:4], "big") % len(_FOCUS_PHRASES)]
    steps, tip = _FOCUS_GUIDANCE[kind]
    return {"phrase": phrase, "steps": steps, "tip": tip}


async def send_health_focus(bot, cid):
    text, entities, kb = menu.menu_screen("m_balance", cid)
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb, transient=True)


async def send_motiv_push(bot, cid):
    """Совместимость старого действия ассистента: открывает новый экран Здоровья."""
    await send_health_focus(bot, cid)


# ---------- роли ----------
def _role_system(role):
    if role == "state":
        return ("Ты спокойный помощник по состоянию, фокусу и мотивации ( психотерапевт). "
                "Выслушай, разложи ситуацию на 1-3 конкретных шага, поддержи коротко. Без воды, с эмодзи. "
        )
    return "Ты полезный ассистент."

async def handle_role(bot, cid, role, text):
    if role == "medicine":
        import medicine
        await medicine.answer(bot, cid, text); return
    if role == "doctor":
        import doctor
        await doctor.answer(bot, cid, text); return
    if secure.is_dangerous_med(text):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health", back="m_balance"); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        route = "gemini"
        out = await ai.allm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7, route=route)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_balance"); return
    store.last_action[str(cid)] = ("role", role, text)
    cont = ("✨ Ещё совет", "chat_retry") if role == "state" else ("Продолжить", "chat_retry")
    await _send(bot, cid, out, kb=_ans_kb(*cont), surface="chat" if role == "state" else "card")


# ---------- 😮‍💨 Мысли ----------
async def send_daycheck(bot, cid):
    await thoughts.send_home(bot, cid)

async def send_evening_review(bot, cid):
    return await thoughts.send_evening_close(bot, cid)

async def worry_clear_all(bot, cid):
    # Совместимость со старыми Telegram-сообщениями: историческая кнопка
    # «Очистить всё» больше не выполняет массовое удаление.
    await thoughts.send_inbox(bot, cid)

async def save_worries(bot, cid, text):
    await thoughts.capture(bot, cid, text)


# ---------- роутер кнопок Баланса ----------
async def handle_callback(bot, cid, q, data):
    if data == "as_health_principles":
        await send_health_principles(bot, cid, q=q); return
    if data.startswith("as_health_principle_"):
        await toggle_health_principle(
            bot, cid, data[len("as_health_principle_"):], q=q)
        return
    # мысли
    if data == "as_daycheck":
        await send_daycheck(bot, cid); return
    # Совместимость со старыми сообщениями с кнопкой «Мотивация».
    if data == "as_motiv":
        await send_health_focus(bot, cid)
        return
    # врач
    if data == "as_medicine":
        import medicine
        await medicine.send_prompt(bot, cid); return
    if data == "as_doctor":
        import doctor
        await doctor.send_prompt(bot, cid); return



# ---------- «Продолжить» / «Ещё раз» ----------
async def retry(bot, cid, status=None):
    la = store.last_action.get(str(cid))
    if la and la[0] == "role":
        await handle_role(bot, cid, la[1], la[2]); return
    hist = list(store.chat_history.get(str(cid), []))
    if not hist:
        await bot.send_message(chat_id=cid, text="Нет предыдущего запроса."); return
    if hist[-1]["role"] == "assistant":
        hist = hist[:-1]
    await bot.send_chat_action(chat_id=cid, action="typing")
    nudge = hist + [{"role": "user", "content": "Продолжи мысль или дай более полезный вариант."}]
    try:
        answer = await ai.achat_chain(nudge, cid)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_balance"); return
    hist.append({"role": "assistant", "content": answer})
    store.chat_history[str(cid)] = hist[-10:]
    await _send(bot, cid, answer, surface="chat")


# ---------- «Короче / Глубже» (переписать последний ответ) ----------
async def reword(bot, cid, mode):
    prev = (store.last_answer.get(str(cid)) or "").strip()
    if not prev:
        await bot.send_message(chat_id=cid, text="Нет ответа, который можно переписать."); return
    surface = store.last_surface.get(str(cid), "card")
    if mode == "short":
        how, tier = "короче и без воды, оставь только суть", "cheap"
    else:
        how, tier = "подробнее и глубже, добавь полезные детали и нюансы", "smart"
    await bot.send_chat_action(chat_id=cid, action="typing")
    prompt = (f"Перепиши этот ответ {how}. Сохрани смысл и тот же язык. "
              "Формат - Telegram HTML: подзаголовки <b>...</b>, пункты с «• », без markdown (без *, #, `).\n\n"
              f"Текст:\n{secure.wrap_untrusted(prev, 'предыдущий ответ')}")
    try:
        out = await ai.allm(prompt, 1200, 0.6, tier=tier)
    except Exception as e:
        await verify.safe_error(bot, cid, e, back="m_balance"); return
    await _send(bot, cid, out, surface=surface)
