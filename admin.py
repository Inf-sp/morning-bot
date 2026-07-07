"""Логика админ-панели (§ docs/admin.md).

Собирает данные для каждого экрана из access/store/tracking/ai и отдаёт готовый
MessageSpec из ui.admin. Роутинг (settings.dispatch) делегирует сюда через send_*.

Все функции — async send_*(bot, cid); гард на владельца — в settings._admin_guard.
"""
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import access
import config
import store
import tracking
from ui import admin as ui


DAY = 86400

# человекочитаемые имена модулей LLM в терминах пользовательских разделов
_MOD_NAMES = {
    "wardrobe": "👕 Гардероб", "balance": "🚑 Здоровье", "food": "🥣 Готовка",
    "weather": "☀️ Мой день", "learning": "📚 Обучение", "leisure": "🍿 Досуг",
    "myday": "☀️ Мой день", "travel": "🧳 Поездки", "assistant": "💬 Чат",
    "content": "🍿 Досуг", "notes": "🎚️ Настройки",
}

_PROV_ORDER = [
    ("claude", "Claude", lambda: bool(config.ANTHROPIC_API_KEY)),
    ("openai", "OpenAI", lambda: bool(config.OPENAI_API_KEY)),
    ("gemini", "Gemini", lambda: True),
    ("openrouter", "OpenRouter", lambda: bool(config.OPENROUTER_API_KEY)),
    ("groq", "Groq", lambda: bool(config.GROQ_API_KEY)),
    ("cf", "Cloudflare", lambda: bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID)),
]


def _back(target="set_admin"):
    return [InlineKeyboardButton("◀️ Назад", callback_data=target)]


def _when(ts) -> str:
    """Компактное «X назад» / время из timestamp."""
    if not ts:
        return "—"
    delta = time.time() - ts
    if delta < 60:
        return "только что"
    if delta < 3600:
        return f"{int(delta // 60)} мин назад"
    if delta < DAY:
        return f"{int(delta // 3600)} ч назад"
    return f"{int(delta // DAY)} дн назад"


def _hhmm(ts) -> str:
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except Exception:
        return "—"


# ================= ДОМ =================

async def send_home(bot, cid):
    stats = _user_stats()
    usage = get_llm_usage_summary(1)
    issues = _collect_issues()
    errors = tracking.errors_today()
    if errors == 0:
        dot, txt = ui.OK, "всё работает"
    elif errors < 10:
        dot, txt = ui.WARN, "есть ошибки"
    else:
        dot, txt = ui.BAD, "много ошибок"
    next_title, next_when, next_reach = _next_broadcast()
    issues_label = ("⚠️ Проблемы" if issues else "🟢 Система")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Пользователи", callback_data="set_admin_users"),
         InlineKeyboardButton("🤖 LLM", callback_data="set_admin_llm")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="set_admin_broadcast"),
         InlineKeyboardButton(f"{issues_label} ({len(issues)})" if issues else issues_label,
                               callback_data="set_admin_issues")],
        [InlineKeyboardButton("🔄 Проверить всё", callback_data="set_admin_check_all")],
    ])
    top_issue = f"{issues[0][1]} · {issues[0][2]}" if issues else None
    msg = ui.home(
        system_dot=dot, system_text=txt,
        total_users=stats["total"], active_7d=stats["active_7d"],
        llm_calls_today=usage["calls"], llm_tokens_today=usage["tokens"],
        next_broadcast_title=next_title, next_broadcast_when=next_when,
        next_broadcast_reach=next_reach,
        issues_count=len(issues), top_issue=top_issue,
    )
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= ПОЛЬЗОВАТЕЛИ =================

def _user_stats():
    import onboarding_status as obs
    from settings import notif_on, NOTIF_TYPES
    cids = access.get_allowed_cids()
    onboarded = 0
    all_off = 0
    for c in cids:
        if all(obs.is_settled(c, s) for s in obs.SECTIONS):
            onboarded += 1
        if not any(notif_on(c, k) for k, _ in NOTIF_TYPES):
            all_off += 1
    total = len(cids)
    return {
        "total": total,
        "new_today": tracking.new_today(),
        "active_1d": tracking.active_count(1),
        "active_7d": tracking.active_count(7),
        "onboarded": onboarded,
        "not_onboarded": total - onboarded,
        "all_off": all_off,
        "avg_msgs": tracking.avg_messages(),
    }


def _last_active_user():
    """Самый недавно активный пользователь -> (dot, name, city, action, when) | None."""
    best_cid, best_ts = None, 0
    for c in access.get_allowed_cids():
        ts = tracking.get_activity(c).get("last_ts", 0)
        if ts > best_ts:
            best_cid, best_ts = c, ts
    if not best_cid:
        return None
    prof = store.get_profile(best_cid)
    settings = store.get_settings(best_cid)
    name = prof.get("name") or f"ID {str(best_cid)[:4]}…"
    city = settings.get("city", "")
    action = store.last_source.get(str(best_cid), "")
    return (tracking.churn_dot(best_cid), name, city, action, tracking.human_last_seen(best_cid))


async def send_users(bot, cid):
    stats = _user_stats()
    last_user = _last_active_user()
    rows = []
    for uid in access.get_allowed_cids():
        prof = store.get_profile(uid)
        name = prof.get("name", "") or str(uid)
        dot = tracking.churn_dot(uid)
        if access.is_owner(uid):
            continue
        rows.append([InlineKeyboardButton(f"❌ {dot} {name}", callback_data=f"set_admin_revoke_{uid}")])
    rows.append([InlineKeyboardButton("🔗 Создать инвайт", callback_data="set_admin_invite")])
    rows.append(_back())
    msg = ui.users(stats, last_user)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(rows))


# ================= LLM =================

def _cost_recent(days):
    import ai
    cutoff = time.time() - days * DAY
    return [e for e in ai.get_cost_log() if e.get("ts", 0) >= cutoff]

def _avg_ms(recent):
    vals = [e.get("ms", 0) for e in recent if e.get("ms")]
    return round(sum(vals) / len(vals)) if vals else 0

def get_llm_usage_summary(period_days=1):
    """Расходы/нагрузка LLM за период — данные, без Telegram-разметки."""
    recent = _cost_recent(period_days)
    total = sum(e.get("tokens", 0) for e in recent)
    by_prov = {}
    for e in recent:
        prov = e.get("provider") or "?"
        by_prov[prov] = by_prov.get(prov, 0) + e.get("tokens", 0)
    providers = []
    if total:
        for key, label, _cfg in _PROV_ORDER:
            tok = by_prov.get(key, 0)
            if tok:
                providers.append((label, round(tok / total * 100)))
        providers.sort(key=lambda x: -x[1])
    return {
        "calls": len(recent),
        "tokens": total,
        "avg_tokens": round(total / len(recent)) if recent else 0,
        "providers": providers,
    }

def _llm_today_count():
    return get_llm_usage_summary(1)["calls"]


async def send_llm(bot, cid):
    import ai
    log = ai.get_cost_log()
    last = log[-1] if log else {}
    usage = get_llm_usage_summary(1)
    errs = tracking.get_errors(source="llm", limit=200)
    errs_today = sum(1 for e in errs if e.get("ts", 0) >= time.time() - DAY)
    status_dot, status_txt = (ui.OK, "работает") if not errs_today else (ui.WARN, "есть ошибки")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Проверить", callback_data="set_admin_llmcheck"),
         InlineKeyboardButton("🕘 История", callback_data="set_admin_llmhistory")],
        _back(),
    ])
    msg = ui.llm(status_dot, status_txt, _when(last.get("ts", 0)), _avg_ms(_cost_recent(1)),
                 errs_today, usage["calls"], usage["tokens"], usage["providers"])
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_llm_check(bot, cid):
    import ai
    probes = [("Claude", "claude"), ("OpenAI", "openai"), ("OpenRouter", "openrouter"),
              ("Cloudflare", "cf"), ("Gemini", "gemini"), ("Groq", "groq")]
    results = []
    for label, route in probes:
        try:
            await ai.allm("Ответь одним словом: ok", 10, 0.0, route=route, module="admin")
            results.append((label, True, ""))
        except Exception as e:
            results.append((label, False, str(e).split(": ", 1)[-1][:60]))
    kb = InlineKeyboardMarkup([_back("set_admin_llm")])
    msg = ui.llm_check(results)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_llm_history(bot, cid):
    import ai
    rows = []
    for e in reversed(ai.get_cost_log()[-12:]):
        rows.append((_hhmm(e.get("ts", 0)), (e.get("provider") or "?").capitalize(),
                     _MOD_NAMES.get(e.get("module", ""), e.get("module", "")), e.get("ok", True)))
    kb = InlineKeyboardMarkup([_back("set_admin_llm")])
    msg = ui.llm_history(rows)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= ПРОБЛЕМЫ =================

_ISSUES_CACHE = {}


def _collect_issues():
    """Реальные проблемы из error-лога за сегодня (без внешних пингов) — дёшево, для дома/списка."""
    errs = tracking.get_errors(limit=50)
    cutoff = time.time() - DAY
    rows = []
    for e in errs:
        if e.get("ts", 0) < cutoff:
            continue
        dot = ui.BAD if e.get("source") in ("llm", "service") else ui.WARN
        rows.append((dot, e.get("source", "?"), f"{e.get('msg', '')[:50]} · {_when(e.get('ts', 0))}"))
    return rows


async def _collect_issues_with_probes(cid):
    """То же самое + активный health-check БД/Weather — для «Проверить всё»."""
    rows = _collect_issues()
    try:
        store._load("__health__")
    except Exception as e:
        rows.append((ui.BAD, "База данных", str(e)[:40]))
    try:
        import asyncio
        import weather
        s = store.get_settings(cid)
        await asyncio.to_thread(weather.fetch_weather, s["lat"], s["lon"], 1)
    except Exception:
        rows.append((ui.BAD, "Weather", f"недоступна · {_when(time.time())}"))
    return rows


async def send_issues(bot, cid, with_probes=False):
    rows = await _collect_issues_with_probes(cid) if with_probes else _collect_issues()
    kb_rows = []
    for i, (dot, name, detail) in enumerate(rows):
        kb_rows.append([InlineKeyboardButton(f"{dot} {name}", callback_data=f"set_admin_issue_{i}")])
    kb_rows.append([InlineKeyboardButton("🔄 Проверить всё", callback_data="set_admin_check_all"),
                     InlineKeyboardButton("🧹 Очистить кэш", callback_data="set_admin_cache_clear")])
    kb_rows.append(_back())
    _ISSUES_CACHE[cid] = rows
    msg = ui.issues(rows, _when(time.time()))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities,
                           reply_markup=InlineKeyboardMarkup(kb_rows))


async def send_issue_detail(bot, cid, idx):
    rows = _ISSUES_CACHE.get(cid, [])
    if idx >= len(rows):
        await send_issues(bot, cid)
        return
    dot, name, detail = rows[idx]
    kb = InlineKeyboardMarkup([_back("set_admin_issues")])
    msg = ui.issue_detail(_when(time.time()), name, dot, detail)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def clear_cache(bot, cid):
    import research
    import util
    import weather
    util._TTL_CACHE.clear()
    weather._WX_CACHE.clear()
    research._CF_CACHE.clear()
    research._WDF_CACHE.clear()
    research._GSR_CACHE.clear()
    await send_issues(bot, cid)


async def check_all(bot, cid):
    await send_issues(bot, cid, with_probes=True)


# ================= РАССЫЛКА =================

def _next_broadcast():
    """Ближайшая плановая рассылка: (title, when, reach). Сейчас — утренний бриф."""
    reach = len(access.get_allowed_cids())
    return "☀️ Утренний дайджест", "завтра, 08:00", reach


async def send_broadcast(bot, cid):
    next_title, next_when, reach = _next_broadcast()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧪 Тест себе", callback_data="set_admin_broadcast_test"),
         InlineKeyboardButton("▶️ Отправить сейчас", callback_data="set_admin_broadcast_send")],
        _back(),
    ])
    msg = ui.broadcast(next_title, next_when, reach)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def send_broadcast_confirm(bot, cid):
    _, _, reach = _next_broadcast()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Отправить", callback_data="set_admin_broadcast_confirm"),
         InlineKeyboardButton("✖️ Отмена", callback_data="set_admin_broadcast_cancel")],
    ])
    msg = ui.broadcast_confirm(reach)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
