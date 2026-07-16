"""Сценарий «😮‍💨 Мысли»: внешняя память, один следующий шаг и безопасный triage."""

import asyncio
from datetime import datetime, timedelta
import re
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import ai
import config
from repositories import UserListRepository
import secure
import settings
import store
import thoughts_knowledge
from ui import thoughts as thoughts_ui


THOUGHT_TYPES = {
    "practical_problem", "anxious_prediction", "emotion",
    "medical", "crisis", "unknown",
}
OPEN_STATUSES = {"open", "later"}
_CRISIS_EXTRA_RE = re.compile(
    r"(?i)(причинить вред (?:себе|другим|кому)|(?:хочу|могу|собираюсь|намерен|намерена).{0,20}"
    r"(?:убить|ударить|навредить|причинить вред)|убить (?:себя|его|е[её]|кого)|"
    r"не могу обеспечить.{0,30}безопасност|теряю контроль|не контролирую себя|"
    r"непосредственн.{0,20}опасност|тяж[её]л.{0,20}спутанност)"
)
_MEDICAL_RE = re.compile(
    r"(?i)(симптом|лекарств|таблет|препарат|дозиров|побочн|диагноз|схем.*лечен|"
    r"боль|одышк|не хватает воздуха|головокруж|обморок|температур|тошнит|рвот|"
    r"давлени|пульс|сердц|груд[ьи]|сыпь|кровотеч|метилфенидат|риталин|concerta)"
)
_PRACTICAL_RE = re.compile(
    r"(?i)(нужно|надо|должен|сделать|закончить|подготов|экзамен|работ|задач|позвон|"
    r"отправить|купить|записаться|убрать|разобрать|не успева)"
)
_ANXIOUS_RE = re.compile(
    r"(?i)(кажется|боюсь|вдруг|наверно|наверное|а если|точно всё|всё сделал неправильно|"
    r"ничего не успею|катастроф|случится)"
)
_EMOTION_RE = re.compile(
    r"(?i)(мне тревожно|мне страшно|я злюсь|мне грустно|не могу собраться|перегружен|"
    r"тяжело|паник|раздраж|устал|эмоци)"
)


def _repo(cid):
    return UserListRepository(config.THOUGHTS_KEY, cid)


def _now():
    return datetime.now(config.TZ)


def _today():
    return _now().strftime("%Y-%m-%d")


def _normalize_record(item):
    item = dict(item or {})
    changed = False
    if not item.get("id"):
        item["id"] = uuid.uuid4().hex
        changed = True
    if item.get("status") == "pending" or not item.get("status"):
        item["status"] = "open"
        changed = True
    if not item.get("date"):
        item["date"] = str(item.get("created_at", ""))[:10] or _today()
        changed = True
    defaults = {
        "type": "unknown",
        "confidence": 0.0,
        "urgency": "low",
        "can_be_actioned": False,
        "can_be_reviewed_later": True,
        "requires_safety_response": False,
    }
    for key, value in defaults.items():
        if key not in item:
            item[key] = value
            changed = True
    return item, changed


def records(cid):
    raw = _repo(cid).all()
    normalized = []
    changed = False
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        value, item_changed = _normalize_record(item)
        normalized.append(value)
        changed = changed or item_changed
    if changed or len(normalized) != len(raw):
        _repo(cid).save(normalized)
    return normalized


def _available_today(item, today):
    deferred_until = str(item.get("deferred_until") or "")
    return not deferred_until or deferred_until <= today


def open_records(cid):
    today = _today()
    return [
        item for item in records(cid)
        if item.get("status") in OPEN_STATUSES and _available_today(item, today)
    ]


def count_today(cid):
    today = _today()
    return sum(item.get("date") == today for item in records(cid))


def _update_record(cid, thought_id, **changes):
    def mutate(items):
        updated = []
        result = None
        for raw in items:
            item, _ = _normalize_record(raw)
            if item.get("id") == thought_id:
                item.update(changes)
                result = dict(item)
            updated.append(item)
        return updated, result

    return _repo(cid).mutate(mutate)


def _get_record(cid, thought_id):
    return next((item for item in records(cid) if item.get("id") == thought_id), None)


def _heuristic_classification(text):
    if secure.is_dangerous_med(text) or _CRISIS_EXTRA_RE.search(text):
        kind = "crisis"
    elif _MEDICAL_RE.search(text):
        kind = "medical"
    elif _EMOTION_RE.search(text):
        kind = "emotion"
    elif _ANXIOUS_RE.search(text):
        kind = "anxious_prediction"
    elif _PRACTICAL_RE.search(text):
        kind = "practical_problem"
    else:
        kind = "unknown"
    return {
        "type": kind,
        "confidence": 0.92 if kind in ("crisis", "medical") else 0.58,
        "urgency": "immediate" if kind == "crisis" else ("high" if kind == "medical" else "low"),
        "can_be_actioned": kind in ("practical_problem", "emotion"),
        "can_be_reviewed_later": kind not in ("crisis", "medical"),
        "requires_safety_response": kind == "crisis",
    }


async def classify(text):
    heuristic = _heuristic_classification(text)
    if heuristic["type"] in ("crisis", "medical"):
        return heuristic
    prompt = (
        "Классифицируй запись пользователя для компактного органайзера мыслей. "
        "Это не диагноз и не психотерапия. Не объясняй решение. "
        "Верни JSON: {\"type\": один из practical_problem, anxious_prediction, emotion, "
        "medical, crisis, unknown, \"confidence\": число 0..1, \"urgency\": low|medium|high|immediate, "
        "\"can_be_actioned\": bool, \"can_be_reviewed_later\": bool, "
        "\"requires_safety_response\": bool}. Медицинские симптомы/лекарства всегда medical; "
        "риск вреда себе или другим всегда crisis.\n\n"
        f"Запись: {secure.wrap_untrusted(text, 'мысль пользователя')}"
    )
    try:
        data = await ai.allm_json(prompt, 260, module="health")
    except Exception:
        return heuristic
    kind = str(data.get("type") or "").strip()
    if kind not in THOUGHT_TYPES:
        return heuristic
    # Защитные локальные правила имеют приоритет над ошибочной модельной разметкой.
    if _MEDICAL_RE.search(text) and kind != "crisis":
        kind = "medical"
    try:
        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "type": kind,
        "confidence": confidence,
        "urgency": str(data.get("urgency") or "low"),
        "can_be_actioned": bool(data.get("can_be_actioned")),
        "can_be_reviewed_later": bool(data.get("can_be_reviewed_later", kind not in ("crisis", "medical"))),
        "requires_safety_response": bool(data.get("requires_safety_response", kind == "crisis")),
    }


def _fallback_scenario(kind, force_action=False):
    if kind == "practical_problem":
        return {
            "title": "🎯 Начнём с малого",
            "body": "Выбери только один небольшой результат на сейчас.",
            "action": "Первый шаг: открыть нужные материалы и отметить один пункт.",
            "question": "Что важнее всего закончить сегодня?",
        }
    if kind == "anxious_prediction":
        return {
            "title": "😌 Пока это мысль, а не факт",
            "body": "Не нужно сейчас доказывать обратное.",
            "action": "Что поможет сейчас: записать один факт, который уже известен.",
            "question": "",
        }
    if kind == "emotion":
        return {
            "title": "😮‍💨 Сейчас тяжело собраться",
            "body": "Начнём с одного действия на две минуты.",
            "action": "Поставь короткий таймер и убери один предмет." if force_action else "",
            "question": "",
        }
    return {
        "title": "😮‍💨 Мысль сохранена",
        "body": "Сейчас её не обязательно решать.",
        "action": "",
        "question": "",
    }


def _scenario_word_count(data):
    return len(" ".join(str(data.get(key, "")) for key in ("title", "body", "action", "question")).split())


async def _build_scenario(record, *, force_action=False, avoid_actions=()):
    kind = record.get("type", "unknown")
    fallback = _fallback_scenario(kind, force_action=force_action)
    guidance = await asyncio.to_thread(
        thoughts_knowledge.retrieve, record.get("text", ""), kind, 3)
    prompt = (
        "Сформируй короткий ответ для органайзера мыслей, не для терапии. До 45 слов целиком, "
        "один заголовок, максимум два коротких абзаца, не больше одного вопроса и ровно одно действие. "
        "Без диагнозов, психологических терминов, заверений что всё будет хорошо и без просьбы рассказать подробнее. "
        "Действие должно занимать 2–15 минут. Не предлагай дыхание при медицинских симптомах. "
        "Верни JSON {\"title\":\"...\",\"body\":\"...\",\"action\":\"...\",\"question\":\"...\"}.\n"
        f"Тип: {kind}. Нужен новый конкретный шаг: {bool(force_action)}. "
        f"Не повторять действия: {list(avoid_actions)}.\n"
        f"Проверенные материалы:\n- " + "\n- ".join(guidance) + "\n"
        f"Мысль: {secure.wrap_untrusted(record.get('text', ''), 'мысль пользователя')}"
    )
    try:
        data = await ai.allm_json(prompt, 320, module="health")
        result = {
            # Поля модели всегда превращаем в одну строку: структуру карточки
            # задаёт только наш рендер, поэтому лишние заголовки и абзацы невозможны.
            key: " ".join(str(data.get(key) or "").split())
            for key in ("title", "body", "action", "question")
        }
        if not result["title"] or _scenario_word_count(result) > 45:
            return fallback
        if sum(value.count("?") for value in result.values()) > 1:
            result["question"] = ""
        if kind != "emotion" or force_action:
            if not result["action"]:
                return fallback
        else:
            result["action"] = ""
        return result
    except Exception:
        return fallback


def _scenario_keyboard(record, *, action_offered=False):
    thought_id = record["id"]
    kind = record.get("type")
    if kind == "practical_problem" or action_offered:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Другой шаг", callback_data=f"thought_other_{thought_id}")],
            [InlineKeyboardButton("✅ Готово", callback_data=f"thought_done_{thought_id}"),
             InlineKeyboardButton("Оставить до вечера", callback_data=f"thought_evening_{thought_id}")],
        ])
    if kind == "anxious_prediction":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Готово", callback_data=f"thought_done_{thought_id}"),
            InlineKeyboardButton("Оставить до вечера", callback_data=f"thought_evening_{thought_id}"),
        ]])
    if kind == "emotion":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Предложить действие", callback_data=f"thought_action_{thought_id}"),
            InlineKeyboardButton("Оставить на потом", callback_data=f"thought_later_{thought_id}"),
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Готово", callback_data=f"thought_done_{thought_id}"),
        InlineKeyboardButton("Оставить на потом", callback_data=f"thought_later_{thought_id}"),
    ]])


async def send_home(bot, cid):
    cid = str(cid)
    store.pending_input[cid] = "thought"
    settings.set_(cid, "_thoughts_prompt_ts", _now().timestamp())
    settings.set_(cid, "_thoughts_capture_mode", "manual")
    msg = thoughts_ui.home(count_today(cid), len(open_records(cid)))
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Что в голове", callback_data="thought_list")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="m_balance"),
         InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=kb, transient=True)


async def send_inbox(bot, cid):
    cid = str(cid)
    all_records = records(cid)
    today_items = [item for item in all_records if item.get("date") == _today()]
    opened = open_records(cid)
    msg = thoughts_ui.inbox(today_items, len(opened))
    rows = []
    if opened:
        rows.append([InlineKeyboardButton("Разобрать мысли", callback_data="thought_review")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="as_daycheck")])
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def _send_scenario(bot, cid, record, *, force_action=False, q=None):
    history = list(record.get("action_history") or [])
    data = await _build_scenario(record, force_action=force_action, avoid_actions=history)
    action = data.get("action", "")
    if action and action not in history:
        history.append(action)
        record = _update_record(cid, record["id"], action_history=history) or record
    msg = thoughts_ui.scenario(**data)
    kb = _scenario_keyboard(record, action_offered=force_action)
    store.last_source[str(cid)] = "Здоровье · Мысли"
    store.last_answer[str(cid)] = msg.text
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


def _split_input(text, split_commas=False):
    raw = str(text or "")
    if not split_commas and not re.search(r"[\n;]", raw):
        return [raw] if raw.strip() else []
    parts = [part.strip() for part in re.split(r"[\n;]+", raw) if part.strip()]
    if split_commas and len(parts) == 1 and "," in parts[0]:
        comma_parts = [part.strip() for part in parts[0].split(",") if part.strip()]
        if len(comma_parts) > 1 and all(len(part) >= 4 for part in comma_parts):
            parts = comma_parts
    return parts


async def capture(bot, cid, text, *, split_commas=False):
    cid = str(cid)
    values = _split_input(text, split_commas=split_commas)
    if not values:
        await send_home(bot, cid)
        return
    now = _now()
    new_records = [{
        "id": uuid.uuid4().hex,
        "text": value,
        "created_at": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "status": "open",
        "type": "unknown",
        "confidence": 0.0,
        "urgency": "low",
        "can_be_actioned": False,
        "can_be_reviewed_later": True,
        "requires_safety_response": False,
    } for value in values]
    _repo(cid).mutate(lambda items: (items + new_records, None))
    settings.set_(cid, "_thoughts_last_added_at", now.timestamp())
    settings.set_(cid, "_worry_prompt_ts", 0)
    ack = thoughts_ui.saved(count_today(cid))
    await bot.send_message(
        chat_id=cid, text=ack.text, entities=ack.entities, transient=True)

    classified = []
    for record in new_records:
        result = await classify(record["text"])
        status = "routed" if result["type"] in ("crisis", "medical") else "open"
        updated = _update_record(cid, record["id"], status=status, **result) or {**record, **result, "status": status}
        classified.append(updated)

    crisis = next((item for item in classified if item.get("type") == "crisis"), None)
    if crisis:
        settings.set_(cid, "_thoughts_safety_date", _today())
        await bot.send_message(chat_id=cid, text=secure.CRISIS_MSG)
        return
    medical = next((item for item in classified if item.get("type") == "medical"), None)
    if medical:
        settings.set_(cid, "_thoughts_safety_date", _today())
        msg = thoughts_ui.medical()
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Спросить врача", callback_data="as_doctor")
        ]])
        await bot.send_message(
            chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return
    actionable = next((item for item in classified if item.get("type") != "unknown"), None)
    if actionable:
        await _send_scenario(bot, cid, actionable)


async def review_next(bot, cid):
    opened = open_records(cid)
    if not opened:
        await send_inbox(bot, cid)
        return
    await _send_scenario(bot, cid, opened[0])


async def _dismiss_message(bot, cid, q):
    try:
        await q.message.delete()
    except Exception:
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    message_id = getattr(getattr(q, "message", None), "message_id", None)
    store.transient_message.pop(str(cid), None)
    if store.last_inline_message.get(str(cid)) == message_id:
        store.last_inline_message.pop(str(cid), None)
    store.clear_persisted_transient_message_id(cid, message_id)


async def handle_callback(bot, cid, q, data):
    cid = str(cid)
    if data == "thought_list":
        await send_inbox(bot, cid); return True
    if data == "thought_review":
        await review_next(bot, cid); return True
    if data == "thought_calm":
        settings.set_(cid, "_thoughts_calm_date", _today())
        store.pending_input.pop(cid, None)
        await _dismiss_message(bot, cid, q); return True
    if data == "thought_tomorrow":
        tomorrow = (_now() + timedelta(days=1)).strftime("%Y-%m-%d")
        for item in open_records(cid):
            _update_record(cid, item["id"], status="open", deferred_until=tomorrow)
        settings.set_(cid, "_thoughts_evening_closed_date", _today())
        await _dismiss_message(bot, cid, q); return True
    if data == "thought_close_day":
        settings.set_(cid, "_thoughts_evening_closed_date", _today())
        await _dismiss_message(bot, cid, q); return True

    prefixes = {
        "thought_other_": "other",
        "thought_action_": "action",
        "thought_done_": "done",
        "thought_evening_": "evening",
        "thought_later_": "later",
    }
    selected = next(((prefix, name) for prefix, name in prefixes.items() if data.startswith(prefix)), None)
    if not selected:
        return False
    prefix, name = selected
    thought_id = data[len(prefix):]
    record = _get_record(cid, thought_id)
    if not record:
        await send_inbox(bot, cid); return True
    if name in ("other", "action"):
        await _send_scenario(bot, cid, record, force_action=True, q=q); return True
    if name == "done":
        _update_record(cid, thought_id, status="done", completed_at=_now().isoformat())
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        msg = thoughts_ui.completed()
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("Закрыть день", callback_data="thought_close_day")
        ]])
        await bot.send_message(
            chat_id=cid, text=msg.text, entities=msg.entities,
            reply_markup=kb, transient=True)
        return True
    if name in ("evening", "later"):
        _update_record(cid, thought_id, status="later")
        msg = thoughts_ui.scenario(
            "😌 Оставлено на потом",
            "Мысль сохранена. Вернёмся к ней во время вечернего закрытия.")
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=None)
        except Exception:
            await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities)
        return True
    return False


async def send_day_reminder(bot, cid):
    cid = str(cid)
    now = _now()
    if not settings.notif_on(cid, "checkin_day"):
        return False
    if settings.get(cid, "_thoughts_calm_date", "") == _today():
        return False
    if settings.get(cid, "_thoughts_safety_date", "") == _today():
        return False
    last_added = settings.get(cid, "_thoughts_last_added_at", 0) or 0
    try:
        if now.timestamp() - float(last_added) < 2 * 60 * 60:
            return False
    except (TypeError, ValueError):
        pass
    store.pending_input[cid] = "thought_reminder"
    settings.set_(cid, "_thoughts_prompt_ts", now.timestamp())
    settings.set_(cid, "_thoughts_capture_mode", "reminder")
    msg = thoughts_ui.day_reminder()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Всё спокойно", callback_data="thought_calm")
    ]])
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=kb, transient=True)
    return True


async def send_evening_close(bot, cid):
    cid = str(cid)
    if not settings.notif_on(cid, "checkin_eve"):
        return False
    if settings.get(cid, "_thoughts_evening_closed_date", "") == _today():
        return False
    opened = open_records(cid)
    if not opened:
        return False
    msg = thoughts_ui.evening(len(opened))
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Разобрать мысли", callback_data="thought_review"),
        InlineKeyboardButton("Оставить до завтра", callback_data="thought_tomorrow"),
    ]])
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=kb, transient=True)
    return True
