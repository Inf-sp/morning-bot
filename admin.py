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
    active = tracking.active_count(1)
    llm_calls = _llm_today_count()
    errors = tracking.errors_today()
    if errors == 0:
        dot, txt = ui.OK, "всё работает"
    elif errors < 10:
        dot, txt = ui.WARN, "есть ошибки"
    else:
        dot, txt = ui.BAD, "много ошибок"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Пользователи", callback_data="set_admin_users"),
         InlineKeyboardButton("📊 Аналитика", callback_data="set_admin_analytics")],
        [InlineKeyboardButton("🤖 LLM", callback_data="set_admin_llm"),
         InlineKeyboardButton("📡 Сервисы", callback_data="set_admin_services")],
        [InlineKeyboardButton("📢 Рассылки", callback_data="set_admin_broadcasts"),
         InlineKeyboardButton("⚠️ Логи", callback_data="set_admin_logs")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="set_admin_tools")],
    ])
    msg = ui.home(dot, txt, active, llm_calls, errors)
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


# ================= АНАЛИТИКА =================

def _cost_recent(days):
    import ai
    cutoff = time.time() - days * DAY
    return [e for e in ai.get_cost_log() if e.get("ts", 0) >= cutoff]

def _llm_today_count():
    return len(_cost_recent(1))

def _top_sections(recent):
    by_mod = {}
    for e in recent:
        mod = e.get("module") or ""
        if mod and mod != "?":
            by_mod[mod] = by_mod.get(mod, 0) + e.get("tokens", 0)
    total = sum(by_mod.values())
    if not total:
        return []
    top = sorted(by_mod.items(), key=lambda x: -x[1])[:4]
    return [(_MOD_NAMES.get(m, m), round(t / total * 100)) for m, t in top]

def _avg_ms(recent):
    vals = [e.get("ms", 0) for e in recent if e.get("ms")]
    return round(sum(vals) / len(vals)) if vals else 0

async def send_analytics(bot, cid, period_days=1):
    recent = _cost_recent(period_days)
    label = {1: "сегодня", 7: "7 дней", 30: "30 дней"}.get(period_days, f"{period_days}д")
    m = {
        "active": tracking.active_count(max(1, period_days)),
        "active_7d": tracking.active_count(7),
        "messages": sum(r.get("count", 0) for r in _all_activity().values()),
        "llm": len(recent),
        "avg_ms": _avg_ms(recent),
        "errors": tracking.errors_today() if period_days == 1 else _errors_in(period_days),
        "broadcasts": 0,
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📆 Сегодня", callback_data="set_admin_analytics"),
         InlineKeyboardButton("7 дней", callback_data="set_admin_analytics_7"),
         InlineKeyboardButton("30 дней", callback_data="set_admin_analytics_30")],
        _back(),
    ])
    msg = ui.analytics(label, m, _top_sections(recent))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)

def _all_activity():
    try:
        return store._load(config.ACTIVITY_KEY) or {}
    except Exception:
        return {}

def _errors_in(days):
    cutoff = time.time() - days * DAY
    return sum(1 for e in tracking.get_errors(limit=200) if e.get("ts", 0) >= cutoff)


# ================= LLM =================

async def send_llm(bot, cid):
    import ai
    log = ai.get_cost_log()
    last = log[-1] if log else {}
    recent = _cost_recent(1)
    errs = tracking.get_errors(source="llm", limit=200)
    errs_today = sum(1 for e in errs if e.get("ts", 0) >= time.time() - DAY)
    status_dot, status_txt = (ui.OK, "работает") if not errs_today else (ui.WARN, "есть ошибки")
    models = sorted({e.get("model", "") for e in recent if e.get("model")})[:3]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Проверить", callback_data="set_admin_llmcheck"),
         InlineKeyboardButton("🕘 История", callback_data="set_admin_llm_hist")],
        [InlineKeyboardButton("💰 Расходы", callback_data="set_admin_cost")],
        _back(),
    ])
    msg = ui.llm(status_dot, status_txt, _when(last.get("ts", 0)), _avg_ms(recent),
                 errs_today, (last.get("provider") or "").capitalize(), models)
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


# ================= РАСХОДЫ =================

async def send_cost(bot, cid, period_days=7):
    recent = _cost_recent(period_days)
    label = {7: "7 дней", 30: "30 дней"}.get(period_days, f"{period_days}д")
    if not recent:
        msg = ui.cost(label, 0, 0, 0, [], [])
        kb = InlineKeyboardMarkup([_back("set_admin_llm")])
        await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)
        return
    by_prov, by_mod, total = {}, {}, 0
    for e in recent:
        tok = e.get("tokens", 0)
        by_prov[e.get("provider") or "?"] = by_prov.get(e.get("provider") or "?", 0) + tok
        mod = e.get("module") or "?"
        by_mod[mod] = by_mod.get(mod, 0) + tok
        total += tok

    def pct(t):
        return round(t / total * 100) if total else 0

    providers = [(label_, cfg(), pct(by_prov.get(key, 0))) for key, label_, cfg in _PROV_ORDER]
    known = [(m, t) for m, t in by_mod.items() if m and m != "?"]
    modules = [(_MOD_NAMES.get(m, m), pct(t)) for m, t in sorted(known, key=lambda x: -x[1])[:5]]
    avg_tok = round(total / len(recent)) if recent else 0
    other = 30 if period_days != 30 else 7
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📆 {other} дней", callback_data=f"set_admin_cost_{other}")],
        _back("set_admin_llm"),
    ])
    msg = ui.cost(label, len(recent), total, avg_tok, providers, modules)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= СЕРВИСЫ =================

async def send_services(bot, cid):
    import asyncio
    rows = []

    # Telegram — уже подключены, если дошли сюда
    rows.append((ui.OK, "Telegram", "подключён"))

    # DB
    try:
        store._load("__health__")
        rows.append((ui.OK, "База данных", "OK"))
    except Exception as e:
        rows.append((ui.BAD, "База данных", str(e)[:40]))

    # Weather
    try:
        import weather
        s = store.get_settings(cid)
        await asyncio.to_thread(weather.fetch_weather, s["lat"], s["lon"], 1)
        rows.append((ui.OK, "Weather", "OK"))
    except Exception:
        rows.append((ui.BAD, "Weather", "недоступна"))

    # Внешние сервисы по наличию ключа (runtime-пинг — позже, §9)
    keyed = [
        ("Gemini", bool(config.GEMINI_API_KEY)),
        ("Claude", bool(config.ANTHROPIC_API_KEY)),
        ("Groq", bool(config.GROQ_API_KEY)),
        ("OpenAI", bool(config.OPENAI_API_KEY)),
        ("OpenRouter", bool(config.OPENROUTER_API_KEY)),
        ("Cloudflare", bool(config.CF_API_TOKEN and config.CF_ACCOUNT_ID)),
        ("TMDB", bool(config.TMDB_API_KEY)),
        ("Ticketmaster", bool(config.TICKETMASTER_API_KEY)),
        ("Tavily", bool(config.TAVILY_API_KEY)),
    ]
    for name, has_key in keyed:
        rows.append((ui.OK, name, "ключ задан") if has_key else (ui.OFF, name, "не настроен"))

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Проверить сейчас", callback_data="set_admin_services")],
        _back(),
    ])
    msg = ui.services(rows, _when(time.time()))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= РАССЫЛКИ =================

async def send_broadcasts(bot, cid):
    # Статистика доставки появится после broadcast-логирования (§9); пока показываем каркас.
    reach = len(access.get_allowed_cids())
    next_title, next_when = "☀️ Утренний бриф", "завтра 08:00"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Запустить сейчас", callback_data="set_admin_run_notif")],
        _back(),
    ])
    msg = ui.broadcasts(0, 0, 0, 0, next_title, next_when, reach)
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


# ================= ЛОГИ =================

_LOG_FILTERS = {"all": None, "llm": "llm", "service": "service", "broadcast": "broadcast"}
_FILTER_LABELS = {"all": "Все", "llm": "LLM", "service": "Сервисы", "broadcast": "Рассылки"}

async def send_logs(bot, cid, flt="all"):
    source = _LOG_FILTERS.get(flt)
    errs = tracking.get_errors(source=source, limit=12)
    rows = []
    for e in errs:
        dot = ui.BAD if e.get("source") in ("llm", "service") else ui.WARN
        rows.append((dot, _hhmm(e.get("ts", 0)), e.get("source", ""), e.get("msg", "")[:40]))
    chips = [InlineKeyboardButton(("• " if flt == k else "") + lbl, callback_data=f"set_admin_logs_{k}")
             for k, lbl in _FILTER_LABELS.items()]
    kb = InlineKeyboardMarkup([
        chips[:2], chips[2:],
        [InlineKeyboardButton("🧹 Очистить", callback_data="set_admin_logs_clear")],
        _back(),
    ])
    msg = ui.logs(rows, _FILTER_LABELS.get(flt, "Все"))
    await bot.send_message(chat_id=cid, text=msg.text, entities=msg.entities, reply_markup=kb)


async def clear_logs(bot, cid):
    tracking.clear_errors()
    await send_logs(bot, cid)
