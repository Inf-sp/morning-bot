import asyncio
from datetime import datetime
import html
import logging
import re
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import config
import store

_log = logging.getLogger(__name__)
import ai
import rerank
import util
import verify
import secure
from ui import balance as balance_ui
from ui import food as food_ui
from ui.constants import CUISINE_EMOJI, ui_label
import settings
import menu
from response_delivery import (
    answer_keyboard as _ans_kb,
    back_keyboard as _back_kb,
    build_entity_card as _build_entity_card,
    clean_card_text as _clean_card_text,
    keyboard as _kb,
    send_response as _send,
)

TZ = config.TZ

DOCTOR_INTRO = (
    f"{ui_label('doctor', 'Врач')}\n\n"
    "Дам общую справочную информацию о здоровье и лекарствах. Это не диагноз и не назначение — "
    "при тревожных симптомах обратись к специалисту.\n\n"
    "Опиши, что беспокоит, или спроси про лекарство."
)

# ---------- СДВГ / Следующий шаг ----------
def _lagom_text(item) -> str:
    """Текст принципа: элемент может быть строкой (старый формат) или
    {"id":..., "value": строка} (после захода в удаление, см. store.ensure_list_ids_via)."""
    if isinstance(item, dict):
        return str(item.get("value", "")).strip()
    return str(item or "").strip()


def _pick_lagom(cid) -> str:
    """Берёт один неиспользованный Лагом-принцип, при исчерпании — сбрасывает счётчик."""
    import memory
    items = memory.get_lagom(cid)
    if not items:
        return ""
    seen = store.get_list(config.MOTIV_LAGOM_SEEN_KEY, cid)
    unused = [i for i in range(len(items)) if i not in seen]
    if not unused:
        seen = []
        unused = list(range(len(items)))
        store.set_list(config.MOTIV_LAGOM_SEEN_KEY, cid, [])
    import random
    idx = random.choice(unused)
    seen.append(idx)
    store.set_list(config.MOTIV_LAGOM_SEEN_KEY, cid, seen)
    return _lagom_text(items[idx])

def _gen_motiv(cid):
    import random
    lagom = _pick_lagom(cid)
    angles = ["физическое действие", "ограничение", "мини-ритуал", "перезагрузку", "один микрошаг"]
    angle = random.choice(angles)
    lagom_ctx = f"Принцип лагома пользователя: «{lagom}»\n" if lagom else ""
    prompt = (
        f"{lagom_ctx}"
        f"Предложи {angle} на основе этого принципа. "
        "Без философии и клише. Конкретно, коротко, на русском. "
        "Верни JSON (без markdown):\n"
        '{"steps":["конкретное действие или ограничение","ещё одно если нужно"],'
        '"why":"1-2 предложения: зачем это работает прямо сейчас",'
        '"now":"одно самое первое конкретное действие прямо сейчас, 3-6 слов, без вступления"}'
    )
    try:
        d = ai.llm_json(prompt, 300, tier="smart")
        steps = [str(s).strip() for s in (d.get("steps") or []) if str(s).strip()]
        why = str(d.get("why", "")).strip()
        now = str(d.get("now", "")).strip()
    except Exception:
        steps = ["Встань и пройди круг по комнате"]
        why = "Движение быстро снижает внутренний шум и помогает начать с малого"
        now = ""
    lagom_full = lagom if lagom else "Один шаг лучше идеального плана."
    final = f"Сейчас: {now}" if now else "Сделай первый шаг сейчас, без подготовки."
    return _build_entity_card(
        "Мотивация",
        lagom_full,
        why,
        steps,
        final,
        bullet_label="Что сделать:",
        emoji="⚡",
    )


async def send_motiv_push(bot, cid):
    """09:00 — плановая мотивация (без 'Секунду...')."""
    out, entities = _gen_motiv(cid)
    store.last_source[str(cid)] = "Баланс · Мотивация"
    store.last_answer[str(cid)] = out
    await bot.send_message(chat_id=cid, text=out, entities=entities)


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
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health")
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
        await verify.safe_send(bot, cid, secure.CRISIS_MSG, surface="health"); return
    await bot.send_chat_action(chat_id=cid, action="typing")
    try:
        route = "gemini"
        out = await ai.allm(_role_system(role) + "\n\nЗапрос пользователя:\n" + text, 1500, 0.7, route=route)
    except Exception as e:
        await verify.safe_error(bot, cid, e); return
    store.last_action[str(cid)] = ("role", role, text)
    cont = ("✨ Ещё совет", "chat_retry") if role == "state" else ("Продолжить", "chat_retry")
    await _send(bot, cid, out, kb=_ans_kb(*cont), surface="chat" if role == "state" else "card")


# ---------- Дневник тревоги ----------
async def send_daycheck(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)   # фикс: ответ не уйдёт в Обратный перевод
    store.game_state.pop(cid, None)
    worries = store.get_list(config.WORRIES_KEY, cid)
    msg = balance_ui.worries_diary(worries)
    store.pending_input[cid] = "worry"
    # _worry_prompt_ts НЕ ставим здесь: это только страховка на случай рестарта
    # бота между плановым уведомлением "Дневная разгрузка" и ответом пользователя
    # (см. bot.py). При ручном открытии раздела pending_input и так переживёт
    # обычную работу бота — окно по времени тут только ловит несвязанные сообщения.
    rows = []
    if worries:
        rows.append([InlineKeyboardButton("❌ Очистить все тревоги", callback_data="worry_clearall")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="m_close"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))

async def send_evening_review(bot, cid):
    cid = str(cid)
    store.challenge_state.pop(cid, None)
    store.game_state.pop(cid, None)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    all_worries = store.get_list(config.WORRIES_KEY, cid)
    worries = [w for w in all_worries if w.get("date", today) == today]
    if not worries:
        # Вечерний разбор разбирает записанные за день тревоги — если их не было,
        # разбирать нечего, и плановое уведомление не приходит вовсе.
        return
    wlist = "\n".join(f"- {w['text']}" for w in worries)
    analysis_failed = False
    cache = store.get_profile(cid).get("evening_review_cache") or {}
    if cache.get("date") == today and cache.get("worries") == wlist:
        d = {"items": cache.get("items") or [], "summary": cache.get("summary") or "",
             "principle": cache.get("principle") or ""}
    else:
        try:
            d = await ai.allm_json(
                "Ты спокойный психолог. Разбери тревоги человека с СДВГ по-доброму, на русском.\n"
                "Нужно коротко, без медицинских назначений и без длинной поддержки.\n"
                "Для каждой тревоги раздели факт (что реально известно) и предположение (что додумано, "
                "не подтверждено) — коротко, до 15 слов каждое.\n"
                "Итог дня - 1-2 коротких предложения: сколько тревог из записанных подтвердились фактами, "
                "а сколько оказались предположениями.\n"
                "Principle - одна короткая обобщающая мысль-принцип на будущее (не банальность, без совета "
                "\"дышите глубже\"), до 12 слов.\n"
                'Верни JSON: {"items":[{"worry":"тревога как есть","fact":"коротко","assumption":"коротко"}],'
                '"summary":"короткий итог, до 30 слов","principle":"короткая мысль"}\n\n'
                f"Тревоги:\n{wlist}", 800, module="balance")
        except Exception as e:
            _log.warning("send_evening_review: LLM failed, analysis empty: %s", e)
            d = {}
            analysis_failed = True
        if not analysis_failed:
            prof = store.get_profile(cid)
            prof["evening_review_cache"] = {
                "date": today, "worries": wlist,
                "items": d.get("items") or [], "summary": (d.get("summary") or "").strip(),
                "principle": (d.get("principle") or "").strip(),
            }
            store.set_profile(cid, prof)
    items = d.get("items") or []
    summary = (d.get("summary") or "").strip()
    principle = (d.get("principle") or "").strip()
    msg = balance_ui.evening_review(worries, items, summary, principle, analysis_failed=analysis_failed)
    rows = [
        [InlineKeyboardButton("❌ Очистить все тревоги", callback_data="worry_clearall")],
    ]
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=InlineKeyboardMarkup(rows))

async def worry_clear_all(bot, cid):
    cid = str(cid)
    store.set_list(config.WORRIES_KEY, cid, [])
    msg = balance_ui.worries_cleared()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="m_balance"), InlineKeyboardButton("🏠 Меню", callback_data="m_menu")]])
    await bot.send_message(chat_id=cid, text=msg.text, reply_markup=kb)

async def save_worries(bot, cid, text):
    cid = str(cid)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    new = [{"text": w.strip(), "status": "pending", "date": today} for w in text.split("\n") if w.strip()]
    existing = store.get_list(config.WORRIES_KEY, cid)
    store.set_list(config.WORRIES_KEY, cid, existing + new)
    msg = balance_ui.worries_saved(len(new))
    await bot.send_message(chat_id=cid, text=msg.text)


_MOTIV_KB = _kb([[("✨ Ещё мотивации", "as_motiv")], [("⬅️ Назад", "m_balance"), ("🏠 Меню", "m_menu")]])


# ---------- роутер кнопок Баланса ----------
async def handle_callback(bot, cid, q, data):
    # дневник тревоги
    if data == "as_daycheck":
        await send_daycheck(bot, cid); return
    # мотивация
    if data == "as_motiv":
        status = await util.StatusManager.start_inline(q, bot=bot, cid=cid, stages=util.StatusManager.TOPIC_STAGES["health"])
        try:
            out, entities = _gen_motiv(cid)
        except Exception as e:
            await status.stop(delete=False)
            await verify.safe_error(bot, cid, e); return
        store.last_source[str(cid)] = "Баланс · Мотивация"
        store.last_answer[str(cid)] = out
        store.last_surface[str(cid)] = "card"
        await bot.send_message(chat_id=cid, text=out, entities=entities, reply_markup=_MOTIV_KB)
        await status.stop(delete=False)
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
        await verify.safe_error(bot, cid, e); return
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
        await verify.safe_error(bot, cid, e); return
    await _send(bot, cid, out, surface=surface)
