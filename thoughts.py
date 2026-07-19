"""Сценарий «😮‍💨 Мысли»: внешняя память, один следующий шаг и безопасный triage."""

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
from ui import thoughts as thoughts_ui
from ui.constants import delete_label


THOUGHT_TYPES = {
    "practical_problem", "anxious_prediction", "emotion",
    "medical", "crisis", "unknown",
}
OPEN_STATUSES = {"open", "later"}
CAPTURE_PENDING_KINDS = {"worry", "thought", "thought_reminder"}
_CAPTURE_STATE_KEY = "_thoughts_capture_state"
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


def capture_state(cid):
    value = settings.get(str(cid), _CAPTURE_STATE_KEY, {})
    return value if isinstance(value, dict) else {}


def capture_waiting(cid):
    return capture_state(cid).get("status") in ("implicit_wait", "explicit_wait")


def activate_capture(cid, *, source, explicit=False):
    cid = str(cid)
    state = {
        "status": "explicit_wait" if explicit else "implicit_wait",
        "source": str(source or "manual"),
        "activated_at": _now().isoformat(),
    }
    settings.set_(cid, _CAPTURE_STATE_KEY, state)
    current = store.pending_input.get(cid)
    if current is None or current in CAPTURE_PENDING_KINDS:
        store.pending_input[cid] = "thought" if source == "manual" else "thought_reminder"
    settings.set_(cid, "_thoughts_prompt_ts", _now().timestamp())
    settings.set_(cid, "_thoughts_capture_mode", source)
    return state


def cancel_capture(cid, *, clear_pending=True):
    cid = str(cid)
    settings.set_(cid, _CAPTURE_STATE_KEY, {"status": "idle"})
    settings.set_(cid, "_thoughts_prompt_ts", 0)
    settings.set_(cid, "_worry_prompt_ts", 0)
    if clear_pending and store.pending_input.get(cid) in CAPTURE_PENDING_KINDS:
        store.pending_input.pop(cid, None)
_ANXIOUS_RE = re.compile(
    r"(?i)(кажется|боюсь|вдруг|наверно|наверное|а если|точно всё|всё сделал неправильно|"
    r"ничего не успею|катастроф|случится)"
)
_EMOTION_RE = re.compile(
    r"(?i)(мне тревожно|мне страшно|я злюсь|мне грустно|не могу собраться|перегружен|"
    r"тяжело|паник|раздраж|устал|эмоци)"
)
_DECISION_RE = re.compile(
    r"(?i)(уже (?:решил|решила|решено|выбрал|выбрала|договорил|запланировал|запланировала)|"
    r"договорились|запланирован[аоы]?|решение принято|поедем|встречаемся)"
)
_BANNED_REVIEW_ADVICE = (
    "записать мысль",
    "сменить обстановку",
    "вернуться к текущему делу",
    "выбрать действие на 5–15 минут",
    "выбрать действие на 5-15 минут",
    "отложить мысль до отдельного времени",
)
_GENERIC_STEP_WORDS = {
    "важн", "дело", "дела", "мысл", "сдел", "сейчас", "одно", "один",
    "текущ", "следующ", "действ", "задач", "сегодня", "просто", "нужно",
}


def _repo(cid):
    return UserListRepository(config.THOUGHTS_KEY, cid)


def _review_repo(cid):
    return UserListRepository(config.THOUGHT_REVIEWS_KEY, cid)


def _now():
    return datetime.now(config.TZ)


def _today():
    return _now().strftime("%Y-%m-%d")


def _navigation_row():
    return [
        InlineKeyboardButton("⬅️ Назад", callback_data="m_balance"),
        InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu"),
    ]


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


async def send_home(
    bot, cid, *, cleared=False, capture_source="manual", explicit=False,
    wait_for_input=True,
):
    cid = str(cid)
    opened = open_records(cid)
    msg = thoughts_ui.cleared_home() if cleared else thoughts_ui.home(opened)
    rows = []
    if opened and not cleared:
        rows.append([InlineKeyboardButton("🧐 Разобрать мысли", callback_data="thought_review")])
    rows.append(_navigation_row())
    kb = InlineKeyboardMarkup(rows)
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=kb, transient=True)
    if wait_for_input:
        activate_capture(cid, source=capture_source, explicit=explicit)


async def send_inbox(bot, cid):
    # Старые кнопки «Что в голове» ведут на новый единый главный экран.
    await send_home(bot, cid)


def _clean_review_line(value):
    return " ".join(str(value or "").replace("?", ".").split()).strip()


def _word_count(value):
    return len(str(value or "").split())


def _short_reference(value, limit=8):
    words = _clean_review_line(value).strip(" .").split()
    text = " ".join(words[:limit])
    return text + ("…" if len(words) > limit else "")


def _imperative_from_thought(value):
    text = _clean_review_line(value).strip(" .")
    lower = text.casefold()
    if "кучу дел" in lower or "много дел" in lower:
        return "До запланированной поездки выбери одно дело, которое действительно нужно закончить сегодня."
    patterns = (
        (r"(?i)^(?:мне\s+)?(?:нужно|надо|не забыть)\s+купить\s+(.+)$", "Купи {}."),
        (r"(?i)^(?:мне\s+)?(?:нужно|надо|не забыть)\s+заказать\s+(.+)$", "Закажи {}."),
        (r"(?i)^(?:мне\s+)?(?:нужно|надо|не забыть)\s+ответить\s+(.+)$", "Ответь {}."),
        (r"(?i)^(?:мне\s+)?(?:нужно|надо|не забыть)\s+позвонить\s+(.+)$", "Позвони {}."),
        (r"(?i)^(?:мне\s+)?(?:нужно|надо|не забыть)\s+отправить\s+(.+)$", "Отправь {}."),
        (r"(?i)^(?:мне\s+)?(?:нужно|надо|не забыть)\s+закончить\s+(.+)$", "Закончи {}."),
    )
    for pattern, template in patterns:
        match = re.match(pattern, text)
        if match:
            return template.format(match.group(1).strip(" ."))
    reference = _short_reference(text, 10)
    return f"Сейчас доведи до конкретного результата только это дело: «{reference}»."


def _fallback_review(items):
    decisions = [item for item in items if _DECISION_RE.search(item.get("text", ""))]
    anxious = [item for item in items if item.get("type") == "anxious_prediction"]
    practical = [
        item for item in items
        if item.get("type") == "practical_problem" and item not in decisions
    ]
    categories = []
    if practical:
        categories.append("дела, которые требуют действия")
    if anxious:
        categories.append("тревожное ощущение срочности")
    if decisions:
        categories.append("уже принятые решения")
    summary = "Смешались " + ", ".join(categories or ["разные мысли, которым нужен порядок"]) + "."
    analysis = []
    if decisions:
        ref = _short_reference(decisions[0].get("text", ""))
        analysis.append(f"«{ref}» уже решено — повторно принимать это решение не нужно.")
    if anxious:
        ref = _short_reference(anxious[0].get("text", ""))
        analysis.append(f"В «{ref}» чувствуется срочность, но конкретный срок пока не указан.")
    if practical:
        ref = _short_reference(practical[0].get("text", ""))
        analysis.append(f"Реального действия сейчас требует «{ref}».")
    if not analysis and items:
        ref = _short_reference(items[0].get("text", ""))
        analysis.append(f"Содержание мысли «{ref}» пока не указывает на отдельное срочное действие.")
    if practical:
        next_step = _imperative_from_thought(practical[0].get("text", ""))
    elif decisions:
        next_step = f"Следуй уже принятому решению: «{_short_reference(decisions[0].get('text', ''), 10)}»."
    else:
        next_step = (
            f"Проверь реальный срок для «{_short_reference(items[0].get('text', ''), 8)}», "
            "прежде чем считать это срочным."
        )
    return {
        "summary": summary,
        "analysis": analysis[:3],
        "next_step": next_step,
    }


def _review_has_banned_advice(data):
    combined = " ".join([
        str(data.get("summary", "")),
        *[str(value) for value in data.get("analysis", [])],
        str(data.get("next_step", "")),
    ]).casefold()
    return any(value in combined for value in _BANNED_REVIEW_ADVICE)


def _content_stems(value):
    stems = set()
    for word in re.findall(r"[a-zа-яё0-9]+", str(value or "").casefold()):
        if len(word) < 4:
            continue
        stem = word[:5]
        if not any(stem.startswith(generic) or generic.startswith(stem) for generic in _GENERIC_STEP_WORDS):
            stems.add(stem)
    return stems


def _step_is_content_specific(next_step, items):
    source = " ".join(str(item.get("text", "")) for item in items)
    return bool(_content_stems(next_step) & _content_stems(source))


async def _build_review(items):
    fallback = _fallback_review(items)
    combined = "\n".join(f"- {item.get('text', '')}" for item in items)
    kinds = ", ".join(item.get("type", "unknown") for item in items)
    now = _now()
    prompt = (
        "Профессионально и кратко разбери текущие мысли пользователя с учётом их конкретного содержания. "
        "Раздели тревоги и уже принятые решения, покажи, что реально требует действия, и сними ложное "
        "ощущение срочности без утверждения, что тревога необоснованна. Не повторяй исходные мысли полностью. "
        "Предложи ровно один конкретный следующий шаг. Не давай универсальных советов: «записать мысль», "
        "«сменить обстановку», «вернуться к текущему делу», «выбрать действие на 5–15 минут», "
        "«отложить мысль до отдельного времени». Не предлагай снова записывать уже записанные мысли. "
        "Не задавай вопросов, не ставь диагноз и не используй психологические оценки.\n"
        "Верни только валидный JSON: {\"summary\":\"до 20 слов\",\"analysis\":[\"от 1 до 3 пунктов, "
        "каждый до 20 слов\"],\"next_step\":\"ровно одно конкретное действие\"}.\n"
        f"Текущая локальная дата: {now.strftime('%Y-%m-%d')}. "
        f"Локальное время: {now.strftime('%H:%M %Z')}. Количество мыслей: {len(items)}.\n"
        f"Скрытые типы: {kinds}.\n"
        f"Активные записи:\n{secure.wrap_untrusted(combined, 'активные мысли пользователя')}"
    )
    try:
        data = await ai.allm_json(prompt, 600, module="health")
        analysis = []
        raw_analysis = data.get("analysis", []) if isinstance(data.get("analysis"), list) else []
        for raw in raw_analysis:
            item = _clean_review_line(raw)
            if item and item.casefold() not in {value.casefold() for value in analysis}:
                analysis.append(item)
        result = {
            "summary": _clean_review_line(data.get("summary")),
            "analysis": analysis[:3],
            "next_step": _clean_review_line(data.get("next_step")),
        }
        invalid = (
            not result["summary"]
            or _word_count(result["summary"]) > 20
            or not (1 <= len(result["analysis"]) <= 3)
            or any(_word_count(value) > 20 for value in result["analysis"])
            or not result["next_step"]
            or _word_count(result["next_step"]) > 35
            or ";" in result["next_step"]
            or len(re.findall(r"[^.!?]+[.!?]+", result["next_step"])) > 1
            or " и " in result["next_step"].casefold()
            or not _step_is_content_specific(result["next_step"], items)
            or _review_has_banned_advice(result)
        )
        if invalid:
            return fallback
        return result
    except Exception:
        return fallback


def _review_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🕒 Оставить на потом", callback_data="thought_review_later")],
        [InlineKeyboardButton("❌ Удалить мысли", callback_data="thought_review_clear")],
        _navigation_row(),
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
    analysis = result.get("analysis")
    if not isinstance(analysis, list):
        analysis = result.get("actions", [])
    next_step = result.get("next_step") or result.get("reframe", "")
    msg = thoughts_ui.review(result.get("summary", ""), analysis, next_step)
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
    if not opened:
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
    msg = thoughts_ui.review(result["summary"], result["analysis"], result["next_step"])
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


def _clean_thought_text(text):
    value = " ".join(str(text or "").split()).strip()
    value = re.sub(r"^[•\-–—]\s*", "", value).strip()
    if value and value[0].isalpha():
        value = value[0].upper() + value[1:]
    return value


def _split_input(text, split_commas=False):
    # Одно сообщение всегда сохраняется как одна мысль.
    value = _clean_thought_text(text)
    return [value] if value else []


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
    cancel_capture(cid)
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
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👩🏻‍⚕️ Врач", callback_data="as_doctor")],
            _navigation_row(),
        ])
        await bot.send_message(
            chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return
    await send_home(bot, cid, wait_for_input=False)


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
    settings.set_(cid, "_thoughts_evening_closed_date", _today())
    await _dismiss_message(bot, cid, q)
    settings.set_(cid, "_thoughts_review_cache", {})
    await send_home(bot, cid)


async def _confirm_clear_review(bot, cid, q):
    msg = thoughts_ui.clear_confirmation()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(delete_label("Да, очистить"), callback_data="thought_review_clear_yes")],
        [
            InlineKeyboardButton("⬅️ Назад", callback_data="thought_review_clear_cancel"),
            InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu"),
        ],
    ])
    try:
        await q.message.edit_text(msg.text, entities=msg.entities, reply_markup=kb)
    except Exception:
        await bot.send_message(
            chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb, transient=True)


async def _clear_review(bot, cid, q):
    cache = _cached_review(cid)
    selected = _review_items(cid, cache) if cache else []
    if not selected:
        settings.set_(cid, "_thoughts_review_cache", {})
        await _dismiss_message(bot, cid, q)
        await send_home(bot, cid)
        return
    type_counts = {}
    selected_ids = {item.get("id") for item in selected}
    for item in selected:
        kind = item.get("type", "unknown")
        type_counts[kind] = type_counts.get(kind, 0) + 1
    if selected_ids:
        _repo(cid).mutate(lambda items: (
            [item for item in items if not isinstance(item, dict) or item.get("id") not in selected_ids],
            None,
        ))
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
    await send_home(bot, cid, cleared=True)


async def handle_callback(bot, cid, q, data):
    cid = str(cid)
    if data == "thought_capture":
        await _dismiss_message(bot, cid, q)
        await send_home(bot, cid, capture_source="reminder", explicit=True)
        return True
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
        cancel_capture(cid)
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
    msg = thoughts_ui.day_reminder()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Выгрузить тревоги", callback_data="thought_capture")],
        [InlineKeyboardButton("Всё спокойно", callback_data="thought_calm")],
    ])
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=kb, transient=True)
    activate_capture(cid, source="reminder")
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
        InlineKeyboardButton("🧐 Разобрать мысли", callback_data="thought_review"),
        InlineKeyboardButton("Оставить до завтра", callback_data="thought_tomorrow"),
    ]])
    await bot.send_message(
        chat_id=cid, text=msg.text, entities=msg.entities,
        reply_markup=kb, transient=True)
    return True
