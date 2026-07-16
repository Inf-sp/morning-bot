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
import service_monitor
from ui.constants import delete_label, ui_label
import store
import tracking
from ui import admin as ui

_log = logging.getLogger(__name__)

DAY = 86400


def _back(target="set_admin"):
    return [InlineKeyboardButton("⬅️ Назад", callback_data=target), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")]


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
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except Exception:
        return "—"


def _updated_at() -> str:
    return datetime.now(config.TZ).strftime("%H:%M")


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


def _store_health_line():
    try:
        store._load("__health__")
    except Exception as e:
        return f"{ui.BAD} Данные · {str(e)[:42]}"
    return f"{ui.OK} Данные · работает"


def _bad_services(snapshot):
    return [s for s in snapshot.get("services", []) if s.get("status") == "bad"]


# ================= ДОМ =================

async def send_home(bot, cid, q=None):
    stats = _user_stats()
    snapshot = api_usage.snapshot()
    bad = _bad_services(snapshot)
    notif = _notification_stats(cid)
    notif_bad = notif["errors_today"] > 0
    dot, txt = (ui.BAD, "Есть проблема") if (bad or notif_bad) else (ui.OK, "Всё работает")
    api_line = f"{len(bad)} недоступно" if bad else "OK · лимиты в норме"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛠 Система", callback_data="adm_api_ai")],
        [InlineKeyboardButton(ui_label("users", "Пользователи"), callback_data="adm_users")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="set_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.home(
        system_dot=dot,
        system_text=txt,
        system_line=api_line,
        notif_line=f"OK · ошибок {notif['errors_today']}" if not notif_bad else f"ошибок {notif['errors_today']}",
        users_line=f"{stats['total']} · активны {stats['active_7d']} · новых {stats['new_7d']}",
        data_line="OK" if _store_health_line().startswith(ui.OK) else "ошибка",
        updated_at=_updated_at(),
    )
    await _show(bot, cid, msg, kb, q)


# ================= ПОЛЬЗОВАТЕЛИ =================

def _user_stats():
    from settings import notif_on, NOTIF_TYPES
    cids = access.get_allowed_cids()
    with_notifications = sum(1 for c in cids if any(notif_on(c, k) for k, _ in NOTIF_TYPES))
    cutoff = time.time() - 7 * DAY
    activities = tracking._all()
    new_7d = sum(1 for r in activities.values() if r.get("first_ts", 0) >= cutoff)
    return {
        "total": len(cids),
        "new_7d": new_7d,
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
    cutoff = time.time() - DAY
    errors = [
        e for e in tracking.get_errors(limit=200)
        if e.get("ts", 0) >= cutoff
        and (e.get("source") == "broadcast" or str(e.get("kind", "")).startswith("notif"))
    ]
    return {
        "sent_today": sent_today,
        "errors_today": len(errors),
        "active_types": active_types,
    }


# ================= API И AI (единый экран, § docs/admin.md) =================

async def send_api_ai(bot, cid, q=None):
    # This screen never calls providers. The independent monitor has already
    # classified errors, selected real fallbacks and prepared display rows.
    rows = service_monitor.rows()

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Логи", callback_data="adm_logs")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="adm_home"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")],
    ])
    msg = ui.api_ai(rows, service_monitor.last_check_time())
    await _show(bot, cid, msg, kb, q)


async def clear_cache(bot, cid, q=None):
    import research
    import tracking
    import util
    import weather
    util._TTL_CACHE.clear()
    weather._WX_CACHE.clear()
    research._CF_CACHE.clear()
    research._WDF_CACHE.clear()
    research._GSR_CACHE.clear()
    tracking.clear_errors()
    await send_logs(bot, cid, q)


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


async def clear_logs(bot, cid, q=None):
    tracking.clear_errors()
    service_monitor.clear_history()
    await send_logs(bot, cid, q)


async def send_logs(bot, cid, q=None):
    cutoff = time.time() - DAY
    errors = [e for e in tracking.get_errors(limit=200) if e.get("ts", 0) >= cutoff]
    monitor_events = [e for e in service_monitor.history(limit=200) if e.get("ts", 0) >= cutoff]
    combined = [
        (int(entry.get("ts") or 0), _compact_log_row(entry)) for entry in errors
    ] + [
        (
            int(entry.get("ts") or 0),
            f"{_hhmm(entry.get('ts', 0))} · Система · {entry.get('text', '')}",
        )
        for entry in monitor_events
    ]
    combined.sort(key=lambda item: item[0], reverse=True)
    rows = [row for _ts, row in combined[:5]]
    buttons = []
    if combined:
        buttons.append([InlineKeyboardButton(delete_label("Очистить логи"), callback_data="adm_logs_clear")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="adm_system"), InlineKeyboardButton("#️⃣ Меню", callback_data="m_menu")])
    kb = InlineKeyboardMarkup(buttons)
    msg = ui.logs(rows, len(combined), _updated_at())
    await _show(bot, cid, msg, kb, q)
