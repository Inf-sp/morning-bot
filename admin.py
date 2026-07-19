"""Логика админ-панели (§ docs/admin.md).

Собирает данные для каждого экрана из access/store/tracking/ai и отдаёт готовый
MessageSpec из ui.admin. Роутинг (settings.dispatch) делегирует сюда через send_*.

Все функции — async send_*(bot, cid); гард на владельца — в settings._admin_guard.
"""
import logging
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import access
import api_usage
import config
import provider_runtime
import service_monitor
from ui.constants import delete_label, ui_label
import store
import tracking
from ui import admin as ui

_log = logging.getLogger(__name__)

DAY = 86400
STALE_AFTER = 15 * 60
DB_SLOW_MS = 500

async def _show(bot, cid, msg, reply_markup=None, q=None):
    if q is not None and getattr(q, "message", None) is not None:
        try:
            await q.message.edit_text(text=msg.text, entities=msg.entities, reply_markup=reply_markup)
            return
        except Exception:
            pass
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=reply_markup)


def _hhmm(ts) -> str:
    try:
        return datetime.fromtimestamp(ts, config.TZ).strftime("%H:%M")
    except Exception:
        return "—"


def _updated_at(ts=None) -> str:
    moment = datetime.now(config.TZ) if ts is None else datetime.fromtimestamp(ts, config.TZ)
    return moment.strftime("%H:%M")


def _num(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if n >= 10000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _today_errors(source=None) -> list:
    cutoff = time.time() - DAY
    return [e for e in tracking.get_errors(source=source, limit=200) if e.get("ts", 0) >= cutoff]


def _snapshot_service(snapshot, service):
    for svc in (snapshot or {}).get("services") or []:
        if svc.get("service") == service:
            return svc
    return {}


def _plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


def _system_summary(states):
    """Aggregate saved monitoring by user impact, without naming providers."""
    restricted = []
    unknown = []
    unavailable_functions = set()
    fallback_unavailable = False
    for state in states:
        service = state.get("service")
        if service in ("database", "telegram"):
            continue
        status = state.get("status")
        fallback = str(state.get("fallback") or "")
        if fallback or status == provider_runtime.WARNING:
            restricted.append(service)
            continue
        if status == provider_runtime.UNKNOWN:
            unknown.append(service)
            continue
        if status == provider_runtime.DOWN:
            spec = provider_runtime.SPEC_BY_KEY.get(service)
            if spec:
                unavailable_functions.update(spec.sections)
            fallback_unavailable = fallback_unavailable or state.get("error_type") == "fallback"

    if unavailable_functions:
        count = len(unavailable_functions)
        noun = _plural(count, "функция недоступна", "функции недоступны", "функций недоступны")
        line = f"{count} {noun}"
    elif restricted:
        count = len(restricted)
        noun = _plural(count, "сервис ограничен", "сервиса ограничены", "сервисов ограничены")
        line = f"{count} {noun}"
        if any(
            str(state.get("fallback") or "")
            for state in states if state.get("service") in restricted
        ):
            line += " · резерв включён"
    elif unknown:
        count = len(unknown)
        if count == 1:
            line = "статус сервиса не получен"
        else:
            noun = _plural(count, "сервиса", "сервисов", "сервисов")
            line = f"статус {count} {noun} не получен"
    else:
        line = "без ограничений"
    return {
        "line": line,
        "restricted": len(restricted),
        "unknown": len(unknown),
        "unavailable_functions": len(unavailable_functions),
        "fallback_unavailable": fallback_unavailable,
    }


def _database_health():
    """Run one cheap PostgreSQL query; storage fallback is not a healthy DB."""
    previous = provider_runtime.get_state("database")
    started = time.monotonic()
    try:
        connection = store._db()
        if connection is None:
            raise ConnectionError("database connection is not configured")
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        latency_ms = round((time.monotonic() - started) * 1000)
    except Exception as error:
        provider_runtime.record_result("database", False, error=type(error).__name__)
        return {"line": "подключение потеряно", "kind": "lost", "latency_ms": None}

    provider_runtime.record_result("database", True)
    if latency_ms >= DB_SLOW_MS:
        return {"line": "медленный ответ", "kind": "slow", "latency_ms": latency_ms}
    was_lost = bool(
        previous.get("last_check")
        and previous.get("status") in (provider_runtime.DOWN, provider_runtime.WARNING)
        and previous.get("last_error")
    )
    return {
        "line": "подключение восстановлено" if was_lost else "подключение стабильно",
        "kind": "restored" if was_lost else "stable",
        "latency_ms": latency_ms,
    }


def _error_signature(entry):
    return (
        str(entry.get("source") or ""), str(entry.get("kind") or ""),
        str(entry.get("file") or ""), str(entry.get("error") or entry.get("msg") or "")[:240],
    )


def _admin_state():
    try:
        return store._load(config.ADMIN_STATE_KEY) or {}
    except Exception:
        return {}


def _new_log_errors(cid):
    cutoff = time.time() - DAY
    errors = [
        entry for entry in reversed(tracking.get_errors(limit=200))
        if int(entry.get("ts") or 0) >= cutoff
    ]
    cursor = (_admin_state().get("log_cursors") or {}).get(str(cid)) or {}
    cursor_id = str(cursor.get("id") or "")
    if cursor_id:
        index = next((i for i, entry in enumerate(errors) if str(entry.get("id") or "") == cursor_id), None)
        unseen = errors[index + 1:] if index is not None else [
            entry for entry in errors if int(entry.get("ts") or 0) > int(cursor.get("ts") or 0)
        ]
    else:
        viewed_at = int(cursor.get("ts") or 0)
        unseen = [entry for entry in errors if int(entry.get("ts") or 0) > viewed_at]

    counts = {}
    for entry in errors:
        signature = _error_signature(entry)
        counts[signature] = counts.get(signature, 0) + 1
    critical = [
        entry for entry in unseen
        if str(entry.get("severity") or "").casefold() == "critical"
        or "critical" in str(entry.get("kind") or "").casefold()
        or counts.get(_error_signature(entry), 0) >= 3
    ]
    return {"entries": unseen, "count": len(unseen), "critical": len(critical)}


def _mark_logs_viewed(cid, errors):
    newest = errors[0] if errors else {}
    marker = {"ts": int(newest.get("ts") or time.time()), "id": str(newest.get("id") or "")}

    def mutate(state):
        state.setdefault("log_cursors", {})[str(cid)] = marker
        return state, None

    try:
        store.mutate_kv(config.ADMIN_STATE_KEY, mutate)
    except Exception:
        _log.warning("failed to save log cursor for admin %s", cid, exc_info=True)


# ================= ДОМ =================

async def send_home(bot, cid, q=None):
    states = provider_runtime.states()
    system = _system_summary(states)
    notif = _notification_stats(cid)
    database = _database_health()
    logs = _new_log_errors(cid)
    telegram = next((state for state in states if state.get("service") == "telegram"), {})
    telegram_down = telegram.get("status") == provider_runtime.DOWN
    latest_check = max((int(state.get("last_check") or 0) for state in states), default=0)
    stale = not latest_check or time.time() - latest_check > STALE_AFTER

    critical = any((
        system["unavailable_functions"], system["fallback_unavailable"],
        database["kind"] == "lost", telegram_down, notif["errors_today"], logs["critical"],
    ))
    limited = any((
        system["restricted"], system["unknown"], database["kind"] in ("slow", "restored"),
        telegram.get("status") in (provider_runtime.WARNING, provider_runtime.UNKNOWN),
        logs["count"], stale,
    ))
    if stale:
        dot, status_text = ui.UNKNOWN, "Состояние неизвестно"
    elif critical:
        dot, status_text = ui.BAD, "Требуется внимание"
    elif limited:
        dot, status_text = ui.WARN, "Работает с ограничениями"
    else:
        dot, status_text = ui.OK, "Всё работает"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛠 Система", callback_data="adm_api_ai"),
         InlineKeyboardButton(ui_label("users", "Пользователи"), callback_data="adm_users")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.home(
        status_dot=dot, status_text=status_text,
        updated_at=_updated_at(latest_check or time.time()), stale=stale,
    )
    await _show(bot, cid, msg, kb, q)


# ================= ПОЛЬЗОВАТЕЛИ =================

def _user_stats():
    from settings import notif_on, NOTIF_TYPES
    cids = access.get_allowed_cids()
    with_notifications = sum(1 for c in cids if any(notif_on(c, k) for k, _ in NOTIF_TYPES))
    now = datetime.now(config.TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    activities = tracking._all()
    allowed = {str(cid) for cid in cids}
    new_today = sum(
        1 for user_cid, record in activities.items()
        if str(user_cid) in allowed and float(record.get("first_ts") or 0) >= today_start
    )
    return {
        "total": len(cids),
        "new_today": new_today,
        "active_today": tracking.active_today_count(cids),
        "new_7d": sum(1 for r in activities.values() if r.get("first_ts", 0) >= time.time() - 7 * DAY),
        "active_7d": tracking.active_count(7),
        "with_notifications": with_notifications,
        "admins": sum(1 for c in cids if access.is_owner(c)),
        "active_invites": len(access.pending_invites()),
    }


_USERS_LIST_LIMIT = 15


def _users_list():
    """Все пользователи, отсортированные по свежести активности -> [(dot, name, last_seen)]."""
    cids = access.get_allowed_cids()
    rows = []
    for c in cids:
        ts = tracking.get_activity(c).get("last_ts", 0)
        prof = store.get_profile(c)
        name = prof.get("name") or f"ID {str(c)[:4]}…"
        rows.append((ts, tracking.churn_dot(c), name, tracking.human_last_seen(c)))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(dot, name, last_seen) for _ts, dot, name, last_seen in rows]


def _removable_users():
    """Пользователи, которых можно удалить из allowlist (без owner) -> [(cid, name, last_seen)]."""
    cids = [c for c in access.get_allowed_cids() if not access.is_owner(c)]
    rows = []
    for c in cids:
        ts = tracking.get_activity(c).get("last_ts", 0)
        prof = store.get_profile(c)
        name = prof.get("name") or f"ID {str(c)[:4]}…"
        rows.append((ts, c, name, tracking.human_last_seen(c)))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(cid, name, last_seen) for _ts, cid, name, last_seen in rows]


async def send_users(bot, cid, q=None):
    stats = _user_stats()
    users_list = _users_list()
    rows = [
        [InlineKeyboardButton(ui_label("invite", "Инвайт"), callback_data="adm_invite")],
    ]
    if _removable_users():
        rows.append([InlineKeyboardButton(delete_label("Удалить пользователя"), callback_data="adm_user_del")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = ui.users(stats, users_list[:_USERS_LIST_LIMIT], len(users_list), _updated_at())
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


async def send_user_delete_list(bot, cid, q=None):
    removable = _removable_users()
    rows = [
        [InlineKeyboardButton(f"{name} · {last_seen}", callback_data=f"adm_user_delconfirm_{u_cid}")]
        for u_cid, name, last_seen in removable
    ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_users"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    msg = ui.user_delete_list(removable)
    await _show(bot, cid, msg, InlineKeyboardMarkup(rows), q)


async def send_user_delete_confirm(bot, cid, target_cid, q=None):
    prof = store.get_profile(target_cid)
    name = prof.get("name") or f"ID {str(target_cid)[:4]}…"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(delete_label("Удалить"), callback_data=f"adm_user_delok_{target_cid}"),
         InlineKeyboardButton("Отмена", callback_data="adm_user_del")],
    ])
    msg = ui.user_delete_confirm(name)
    await _show(bot, cid, msg, kb, q)


async def do_user_delete(bot, cid, target_cid, q=None):
    if access.is_owner(target_cid):
        await send_user_delete_list(bot, cid, q)
        return
    access.revoke_user(target_cid)
    await send_user_delete_list(bot, cid, q)


async def send_invite(bot, cid, q=None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Создать", callback_data="adm_invite_create")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.invite_prompt()
    await _show(bot, cid, msg, kb, q)


async def create_invite(bot, cid, q=None):
    code = access.create_invite()
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={code}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.invite_created(link)
    if q is not None and getattr(q, "message", None) is not None:
        try:
            await q.message.edit_text(
                text=msg.text,
                entities=msg.entities,
                disable_web_page_preview=True,
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    await bot.send_message(
        chat_id=cid,
        text=msg.text,
        entities=msg.entities,
        disable_web_page_preview=True,
        reply_markup=kb,
    )


async def send_welcome(bot, cid, q=None):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить", callback_data="adm_welcome_edit"),
         InlineKeyboardButton("Предпросмотр", callback_data="adm_welcome_preview")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_users"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.welcome_admin()
    await _show(bot, cid, msg, kb, q)


def _notification_stats(cid):
    from settings import notif_on, NOTIF_TYPES
    active_types = sum(1 for kind, _label in NOTIF_TYPES if notif_on(cid, kind))
    snapshot = api_usage.snapshot()
    telegram = _snapshot_service(snapshot, "telegram")
    sent_today = int(telegram.get("day_messages") or 0)
    failed_today = int(telegram.get("day_failures") or 0)
    cutoff = time.time() - DAY
    errors = [
        e for e in tracking.get_errors(limit=200)
        if e.get("ts", 0) >= cutoff
        and (e.get("source") == "broadcast" or str(e.get("kind", "")).startswith("notif"))
    ]
    return {
        "sent_today": sent_today,
        "errors_today": max(failed_today, len(errors)),
        "active_types": active_types,
    }


# ================= API И AI (единый экран, § docs/admin.md) =================

async def send_api_ai(bot, cid, q=None):
    # This screen never calls providers. The independent monitor has already
    # classified errors, selected real fallbacks and prepared display rows.
    rows = service_monitor.rows()

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Ошибки", callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.api_ai(rows, service_monitor.last_check_time())
    await _show(bot, cid, msg, kb, q)

def _log_error_text(entry):
    if entry.get("error"):
        return str(entry["error"])
    kind = str(entry.get("kind") or "Ошибка")
    msg = str(entry.get("msg") or "")
    if ":" in kind and kind.rsplit(":", 1)[-1].strip().endswith("Error"):
        kind = kind.rsplit(":", 1)[-1].strip()
    return f"{kind}: {msg}" if msg and not msg.startswith(f"{kind}:") else (msg or kind)


def _log_location(entry):
    file_name = str(entry.get("file") or "")
    line = int(entry.get("line") or 0)
    if not file_name:
        kind = str(entry.get("kind") or "")
        candidate = kind.split(":", 1)[0].strip()
        if candidate and candidate not in ("app", "llm", "service"):
            file_name = candidate if candidate.endswith(".py") else f"{candidate}.py"
    return file_name or "—", line


def _compact_log_row(entry):
    file_name, line = _log_location(entry)
    section = str(entry.get("section") or tracking._section_for(file_name, entry.get("source")))
    action = str(entry.get("action") or tracking._action_for(entry.get("function"), entry.get("source")))
    error = " ".join(_log_error_text(entry).split())[:170]
    location = f"{file_name}:{line}" if line else file_name
    return f"{_hhmm(entry.get('ts', 0))} · {section} · {action} · {error} · {location}"


def _monitor_error_row(entry):
    service = str(entry.get("service") or "")
    spec = provider_runtime.SPEC_BY_KEY.get(service)
    label = spec.label if spec else service or "Сервис"
    message = str(entry.get("text") or entry.get("message") or "Ошибка").strip().rstrip(".")
    if message.startswith(f"{label}:"):
        message = message.split(":", 1)[1].strip()
    details = []
    status_code = entry.get("status_code")
    if status_code:
        details.append(f"HTTP {status_code}")
    exception_type = str(entry.get("exception_type") or "")
    if exception_type:
        details.append(exception_type)
    latency_ms = entry.get("latency_ms")
    if latency_ms is not None:
        details.append(f"{int(latency_ms)} мс")
    fallback = str(entry.get("fallback_target") or "")
    fallback_spec = provider_runtime.SPEC_BY_KEY.get(fallback)
    if fallback_spec:
        details.append(f"резерв {fallback_spec.label}")
    recovered_at = int(entry.get("recovered_at") or 0)
    started_at = int(entry.get("started_at") or entry.get("ts") or 0)
    if recovered_at:
        duration = max(0, recovered_at - started_at)
        details.append(f"восстановлен за {max(1, round(duration / 60))} мин")
    suffix = f" · {' · '.join(details)}" if details else ""
    return f"{_hhmm(entry.get('ts', 0))} · Система · {label}: {message}{suffix}"


def _collapse_monitor_errors(entries, window_seconds=600):
    """Сворачивает одинаковые сбои фоновой проверки в один видимый инцидент."""
    groups = []
    for entry in sorted(entries, key=lambda item: int(item.get("ts") or 0), reverse=True):
        key = (
            str(entry.get("service") or ""),
            str(entry.get("text") or entry.get("message") or "").strip().casefold(),
            int(entry.get("status_code") or 0),
            str(entry.get("exception_type") or ""),
            str(entry.get("fallback_target") or ""),
        )
        ts = int(entry.get("ts") or 0)
        match = next((group for group in groups
                      if group["key"] == key and group["latest_ts"] - ts <= window_seconds), None)
        if match is None:
            groups.append({"key": key, "latest_ts": ts, "entry": entry, "count": 1})
        else:
            match["count"] += 1
    return [(group["entry"], group["count"]) for group in groups]


async def clear_logs(bot, cid, q=None):
    tracking.clear_errors()
    provider_runtime.clear_history()
    await send_logs(bot, cid, q)


async def send_logs(bot, cid, q=None):
    cutoff = time.time() - DAY
    errors = [e for e in tracking.get_errors(limit=200) if e.get("ts", 0) >= cutoff]
    monitor_errors = [
        entry for entry in provider_runtime.history(limit=200)
        if entry.get("ts", 0) >= cutoff and entry.get("event_type") == "error"
    ]
    combined = [
        (int(entry.get("ts") or 0), _compact_log_row(entry)) for entry in errors
    ] + [
        (
            int(entry.get("ts") or 0),
            _monitor_error_row(entry) + (f" · повторилось {count} раз" if count > 1 else ""),
        )
        for entry, count in _collapse_monitor_errors(monitor_errors)
    ]
    combined.sort(key=lambda item: item[0], reverse=True)
    rows = [row for _ts, row in combined]
    buttons = []
    if combined:
        buttons.append([InlineKeyboardButton(delete_label("Очистить ошибки"), callback_data="adm_logs_clear")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_system"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(buttons)
    msg = ui.logs(rows, len(rows), _updated_at())
    await _show(bot, cid, msg, kb, q)
    _mark_logs_viewed(cid, errors)
