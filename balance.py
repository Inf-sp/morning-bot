import asyncio
from datetime import datetime
import hashlib
import html
import logging
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store

_log = logging.getLogger(__name__)
import ai
import rerank
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
    back_keyboard as _back_kb,
    build_entity_card as _build_entity_card,
    clean_card_text as _clean_card_text,
    send_response as _send,
)

TZ = config.TZ

DOCTOR_INTRO = (
    f"{ui_label('doctor', 'Врач')}\n\n"
    "Дам общую справочную информацию о здоровье и лекарствах. Это не диагноз и не назначение — "
    "при тревожных симптомах обратись к специалисту.\n\n"
    "Опиши, что беспокоит, или спроси про лекарство."
)

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
        InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu"),
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
    if role == "doctor":
        return ("Ты помощник по здоровью. Это справочная информация, не диагноз. "
                "Не пиши так, будто ставишь диагноз — только вероятные причины, явно связанные "
                "с описанными признаками. Не добавляй советы без причины (например, не советуй "
                "измерить давление, если это не связано с симптомом) — каждый пункт должен "
                "объясняться конкретным признаком из описания. "
                "Отвечай кратко и верни строго валидный JSON без markdown:\n"
                "{\"title\":\"Разбор симптомов\","
                "\"summary\":\"1 короткое предложение: основная жалоба\","
                "\"causes\":\"1-2 предложения: вероятные причины, каждая связана с конкретным "
                "признаком из описания, без слова 'диагноз'\","
                "\"bullets\":[\"конкретное действие, оправданное симптомом\", \"...до 4\"],"
                "\"urgent\":\"признаки, при которых нужна экстренная помощь прямо сейчас (сильная "
                "внезапная боль, нарушение речи/зрения, слабость, онемение, спутанность сознания, "
                "судороги, высокая температура с ригидностью шеи, боль после травмы) — только если "
                "они уместны для этих симптомов, иначе пусто\","
                "\"plan\":\"когда стоит записаться к врачу в обычном порядке (повторяется, "
                "усиливается, стало необычным) — только если уместно, иначе пусто\","
                "\"final\":\"короткий итог ТОЛЬКО если он даёт одно чёткое решение и не повторяет "
                "предыдущие блоки, иначе пустая строка\"}")
    return "Ты полезный ассистент."

_MED_RE = ("лекарств", "таблет", "препарат", "доз", "мг ", " мг", "метилфенидат", "ибупрофен",
           "парацетамол", "антибиотик", "капл", "сироп", "мазь", "витамин", "пилюл", "concerta",
           "ritalin", "риталин", "медикамент", "побочк", "побочн", "как принимать")

def _is_med_question(text):
    t = (text or "").lower()
    return any(k in t for k in _MED_RE)

def _med_system():
    return ("Ты помощник по лекарствам. Это справочная информация, не назначение. "
            "Не подбирай дозировку и схему. Верни строго валидный JSON без markdown:\n"
            "{\"title\":\"Разбор лекарства\","
            "\"summary\":\"1 короткое предложение: о каком препарате вопрос\","
            "\"quote\":\"1-2 предложения: зачем применяют и что важно знать\","
            "\"bullets\":[\"частая побочка или риск\", \"когда обратиться к врачу\", \"что уточнить у врача\"],"
            "\"final\":\"короткий безопасный итог с точкой\"}")

def _doctor_candidates(symptoms):
    data = ai.llm_json(
        f"Пользователь описал: {symptoms}\nДай 6 коротких справочных тезисов (общая информация о возможных "
        "причинах/состояниях при таких симптомах; НЕ диагноз). JSON: {\"items\": [\"тезис\", ...]}", 900, tier="cheap")
    return [x for x in data.get("items", []) if isinstance(x, str) and x.strip()]

def _fallback_health_card(title, user_text):
    return {
        "title": title,
        "summary": f"Запрос: {_clean_card_text(user_text)[:160]}",
        "quote": "По описанию нельзя оценить заочно, но можно дать общие ориентиры.",
        "bullets": [
            "Следи за усилением симптомов, температурой, дыханием, болью и общим состоянием",
            "Не начинай лекарства и дозировки без инструкции врача или фармацевта",
        ],
        "final": "Это справочная информация, не диагноз и не назначение.",
    }

def _fallback_doctor_card(user_text):
    return {
        "title": "Разбор симптомов",
        "summary": f"Запрос: {_clean_card_text(user_text)[:160]}",
        "causes": "По описанию нельзя оценить заочно, но можно дать общие ориентиры.",
        "bullets": [
            "Следи за усилением симптомов, температурой, дыханием, болью и общим состоянием",
        ],
        "urgent": "Состояние быстро ухудшается или симптомы выраженные.",
        "final": "",
    }

async def _send_health_card(bot, cid, data, kb=None):
    text, entities = _build_entity_card(
        data.get("title") or "Разбор симптомов",
        data.get("summary") or "",
        data.get("quote") or "",
        data.get("bullets") or [],
        data.get("final") or "Это справочная информация, не диагноз и не назначение.",
        bullet_label=data.get("bullet_label") or "Рекомендации:",
    )
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Здоровье")
    store.last_surface[str(cid)] = "health"
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb)

async def _send_doctor_card(bot, cid, data, kb=None):
    """Разбор симптомов — отдельный формат от лекарств: возможные причины
    связаны с признаками, срочный/плановый сценарий разделены (см. ui/balance.py doctor_card)."""
    msg = balance_ui.doctor_card(data)
    text, entities = msg.text, msg.entities
    store.last_answer[str(cid)] = text
    store.last_source.setdefault(str(cid), "Здоровье")
    store.last_surface[str(cid)] = "health"
    await bot.send_message(chat_id=cid, text=text, entities=entities, reply_markup=kb)

async def doctor_answer(bot, cid, symptoms):
    if secure.is_dangerous_med(symptoms):
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health", back="m_balance")
        return
    await bot.send_chat_action(chat_id=cid, action="typing")
    safe_symptoms = secure.wrap_untrusted(symptoms, "симптомы пользователя")
    if _is_med_question(symptoms):
        prompt = f"{_med_system()}\n\nВопрос про лекарство: {safe_symptoms}"
        try:
            d = await ai.allm_json(prompt, 900, route="gemini", module="health")
        except Exception as e:
            _log.warning("doctor medicine AI failed, using fallback: %r", e, exc_info=True)
            d = _fallback_health_card("Разбор лекарства", symptoms)
        store.last_source[str(cid)] = "Здоровье · Лекарство"
        store.last_action[str(cid)] = ("role", "doctor", symptoms)
        await _send_health_card(bot, cid, d, kb=_ans_kb(None, None, depth=False))
        return
    passages = []
    try:
        cands = await asyncio.to_thread(_doctor_candidates, symptoms)
        ranked = rerank.rerank(symptoms, cands, top_n=3)
        passages = [t for t, _ in ranked]
    except Exception:
        passages = []
    base = _role_system("doctor")
    if passages:
        ctx = "\n".join(f"- {p}" for p in passages)
        prompt = f"{base}\n\nНаиболее релевантные тезисы (по симптомам):\n{ctx}\n\nСимптомы: {safe_symptoms}"
    else:
        prompt = f"{base}\n\nСимптомы: {safe_symptoms}"
    try:
        d = await ai.allm_json(prompt, 900, route="gemini", module="health")
    except Exception as e:
        _log.warning("doctor symptoms AI failed, using fallback: %r", e, exc_info=True)
        d = _fallback_doctor_card(symptoms)
    store.last_source[str(cid)] = "Здоровье · Врач"
    store.last_action[str(cid)] = ("role", "doctor", symptoms)
    await _send_doctor_card(bot, cid, d, kb=_ans_kb(None, None, depth=False))

async def handle_role(bot, cid, role, text):
    if role == "doctor":
        await doctor_answer(bot, cid, text); return
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
    if data == "as_doctor":
        store.pending_input[str(cid)] = "role_doctor"
        await bot.send_message(chat_id=cid, text=DOCTOR_INTRO, reply_markup=_back_kb()); return



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
