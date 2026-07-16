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


def _review_repo(cid):
    return UserListRepository(config.THOUGHT_REVIEWS_KEY, cid)


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


def _append_review_event(cid, event):
    review_id = event.get("id")

    def mutate(items):
        if review_id and any(item.get("id") == review_id for item in items if isinstance(item, dict)):
            return items, False
        return [*items, event], True

    return _review_repo(cid).mutate(mutate)


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


def _review_is_deferred_today(cid):
    return settings.get(cid, "_thoughts_review_later_date", "") == _today()


async def send_home(bot, cid, *, notice_title="", notice_body=""):
    cid = str(cid)
    store.pending_input[cid] = "thought"
    settings.set_(cid, "_thoughts_prompt_ts", _now().timestamp())
    settings.set_(cid, "_thoughts_capture_mode", "manual")
    opened = open_records(cid)
    msg = thoughts_ui.home(
        count_today(cid), opened,
        notice_title=notice_title, notice_body=notice_body)
    rows = []
    if opened and not _review_is_deferred_today(cid):
        rows.append([InlineKeyboardButton("✨ Разобрать мысли", callback_data="thought_review")])
    rows.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="m_balance"),
        InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu"),
    ])
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=InlineKeyboardMarkup(rows), transient=True)


async def send_inbox(bot, cid):
    # Старые кнопки «Что в голове» ведут на новый единый главный экран.
    await send_home(bot, cid)


def _clean_review_line(value):
    return " ".join(str(value or "").replace("?", ".").split()).strip()


def _fallback_review(items):
    practical = [
        _clean_review_line(item.get("text", "")).strip(" .")
        for item in items
        if item.get("type") == "practical_problem"
    ]
    actions = []
    for value in practical:
        value = re.sub(r"(?i)^(?:мне\s+)?(?:нужно|надо|не забыть)\s+", "", value).strip()
        if value:
            actions.append(value[:1].upper() + value[1:] + ".")
        if len(actions) == 2:
            break
    if len(actions) < 3:
        actions.append("Выбрать одну главную задачу на сегодня.")
    has_anxious = any(item.get("type") == "anxious_prediction" for item in items)
    return {
        "summary": "Сейчас в голове смешались задачи и тревожные предположения.",
        "actions": actions[:3],
        "reframe": (
            "Тревожная мысль пока не является фактом. Всё делать необязательно — достаточно закончить главное."
            if has_anxious else ""
        ),
    }


def _review_word_count(data):
    return len(" ".join([
        str(data.get("summary", "")),
        *[str(value) for value in data.get("actions", [])],
        str(data.get("reframe", "")),
    ]).split())


async def _build_review(items):
    unique_items = []
    seen_texts = set()
    for item in items:
        normalized = " ".join(str(item.get("text", "")).casefold().split()).strip(" .!?")
        if normalized and normalized not in seen_texts:
            seen_texts.add(normalized)
            unique_items.append(item)
    items = unique_items
    fallback = _fallback_review(items)
    combined = "\n".join(f"- {item.get('text', '')}" for item in items)
    kinds = ", ".join(item.get("type", "unknown") for item in items)
    guidance = []
    for kind in ("practical_problem", "anxious_prediction", "emotion"):
        found = await asyncio.to_thread(thoughts_knowledge.retrieve, combined, kind, 2)
        for value in found:
            if value not in guidance:
                guidance.append(value)
    prompt = (
        "Сделай единый компактный разбор активных записей для внешней памяти, не психотерапию. "
        "Объедини повторы. Найди только то, что требует действия. Предложи максимум три конкретных "
        "действия на 2–15 минут. Для всех тревожных предположений дай ровно одну спокойную формулировку, "
        "отделяющую предположение от факта, без ложных заверений. Не разбирай каждую запись отдельно, "
        "не ставь диагноз, не используй проценты и оценки, не задавай вопросов. Весь результат до 120 слов. "
        "Верни JSON {\"summary\":\"1-2 коротких предложения\",\"actions\":[\"до 3 действий\"],"
        "\"reframe\":\"одна формулировка или пусто\"}.\n"
        f"Скрытые типы: {kinds}.\n"
        f"Проверенные материалы:\n- " + "\n- ".join(guidance[:5]) + "\n"
        f"Активные записи:\n{secure.wrap_untrusted(combined, 'активные мысли пользователя')}"
    )
    try:
        data = await ai.allm_json(prompt, 600, module="health")
        actions = []
        for raw in data.get("actions", []) if isinstance(data.get("actions"), list) else []:
            action = _clean_review_line(raw)
            if action and action.casefold() not in {value.casefold() for value in actions}:
                actions.append(action)
        result = {
            "summary": _clean_review_line(data.get("summary")),
            "actions": actions[:3],
            "reframe": _clean_review_line(data.get("reframe")),
        }
        has_anxious = any(item.get("type") == "anxious_prediction" for item in items)
        if not has_anxious:
            result["reframe"] = ""
        rendered_words = len(thoughts_ui.review(
            result["summary"], result["actions"], result["reframe"]).text.split())
        if (not result["summary"] or not result["actions"]
                or (has_anxious and not result["reframe"])
                or _review_word_count(result) > 115 or rendered_words > 120):
            return fallback
        return result
    except Exception:
        return fallback


def _review_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Оставить на потом", callback_data="thought_review_later"),
         InlineKeyboardButton("Очистить мысли", callback_data="thought_review_clear")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="as_daycheck"),
         InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])


def _cached_review(cid):
    value = settings.get(cid, "_thoughts_review_cache", {})
    return value if isinstance(value, dict) else {}


async def _show_cached_review(bot, cid, q=None):
    cache = _cached_review(cid)
    result = cache.get("result") if isinstance(cache.get("result"), dict) else None
    if not result:
        await send_home(bot, cid)
        return
    msg = thoughts_ui.review(
        result.get("summary", ""), result.get("actions", []), result.get("reframe", ""))
    if q is not None:
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=_review_keyboard())
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_review_keyboard())


async def review_all(bot, cid, q=None):
    cid = str(cid)
    opened = open_records(cid)
    if not opened or _review_is_deferred_today(cid):
        await send_home(bot, cid)
        return
    result = await _build_review(opened)
    cache = {
        "id": uuid.uuid4().hex,
        "date": _today(),
        "created_at": _now().isoformat(),
        "thought_ids": [item["id"] for item in opened],
        "result": result,
    }
    settings.set_(cid, "_thoughts_review_cache", cache)
    store.last_source[cid] = "Здоровье · Мысли"
    msg = thoughts_ui.review(result["summary"], result["actions"], result["reframe"])
    store.last_answer[cid] = msg.text
    if q is not None:
        message_id = getattr(getattr(q, "message", None), "message_id", None)
        store.transient_message.pop(cid, None)
        store.clear_persisted_transient_message_id(cid, message_id)
        try:
            await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=_review_keyboard())
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=_review_keyboard())


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
    await send_home(
        bot, cid,
        notice_title="✅ Сохранено",
        notice_body="Больше не нужно держать это в голове.")


async def review_next(bot, cid):
    # Совместимость со старым именем функции: разбор теперь общий, не поштучный.
    await review_all(bot, cid)


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


def _review_items(cid, cache):
    wanted = set(cache.get("thought_ids") or [])
    return [item for item in records(cid) if item.get("id") in wanted]


async def _leave_review_for_later(bot, cid, q):
    cache = _cached_review(cid)
    if cache:
        event = {
            "id": cache.get("id") or uuid.uuid4().hex,
            "date": cache.get("date") or _today(),
            "created_at": cache.get("created_at") or _now().isoformat(),
            "outcome": "left_for_later",
            "thought_ids": list(cache.get("thought_ids") or []),
            "result": cache.get("result") or {},
        }
        _append_review_event(cid, event)
        for item in _review_items(cid, cache):
            _update_record(cid, item["id"], status="later")
    settings.set_(cid, "_thoughts_review_later_date", _today())
    settings.set_(cid, "_thoughts_evening_closed_date", _today())
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await send_home(
        bot, cid,
        notice_title="✅ Оставлено",
        notice_body="К мыслям можно вернуться позже.")


async def _confirm_clear_review(bot, cid, q):
    msg = thoughts_ui.clear_confirmation()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Да, очистить", callback_data="thought_review_clear_yes"),
        InlineKeyboardButton("Отмена", callback_data="thought_review_clear_cancel"),
    ]])
    try:
        await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
    except Exception:
        await bot.send_message(
            chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb, transient=True)


async def _clear_review(bot, cid, q):
    cache = _cached_review(cid)
    selected = _review_items(cid, cache) if cache else open_records(cid)
    type_counts = {}
    for item in selected:
        kind = item.get("type", "unknown")
        type_counts[kind] = type_counts.get(kind, 0) + 1
        _update_record(
            cid, item["id"], status="closed",
            completed_at=_now().isoformat(), closed_reason="review_clear")
    # После очистки сохраняется только обезличенный факт завершения, без текста
    # разбора и без идентификаторов мыслей.
    _append_review_event(cid, {
        "id": uuid.uuid4().hex,
        "date": _today(),
        "completed_at": _now().isoformat(),
        "outcome": "cleared",
        "record_count": len(selected),
        "type_counts": type_counts,
    })
    settings.set_(cid, "_thoughts_review_cache", {})
    settings.set_(cid, "_thoughts_evening_closed_date", _today())
    await _dismiss_message(bot, cid, q)
    await send_home(
        bot, cid,
        notice_title="✅ Мысли очищены",
        notice_body="Сейчас список пуст.")


async def handle_callback(bot, cid, q, data):
    cid = str(cid)
    if data == "thought_list":
        await send_inbox(bot, cid); return True
    if data == "thought_review":
        await review_all(bot, cid, q=q); return True
    if data == "thought_review_later":
        await _leave_review_for_later(bot, cid, q); return True
    if data == "thought_review_clear":
        await _confirm_clear_review(bot, cid, q); return True
    if data == "thought_review_clear_cancel":
        await _show_cached_review(bot, cid, q=q); return True
    if data == "thought_review_clear_yes":
        await _clear_review(bot, cid, q); return True
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
    # Старые поштучные кнопки больше не запускают отдельные разборы.
    if data == "thought_close_day" or data.startswith((
        "thought_other_", "thought_action_", "thought_done_",
        "thought_evening_", "thought_later_",
    )):
        await send_home(bot, cid)
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
