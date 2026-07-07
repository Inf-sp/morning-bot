"""Слой трекинга для админ-панели (§9 docs/admin.md).

Два дешёвых rolling-примитива поверх KV-store, плюс агрегаты для UI:

- errors   — лог ошибок для экрана «Логи» {ts, source, kind, msg}
- activity — last_seen + счётчик на пользователя {cid: {last_ts, count, days}}

Все записи best-effort: трекинг НИКОГДА не должен ломать основной поток бота,
поэтому каждая точка входа обёрнута в try/except с молчаливым проглатыванием.
"""
import time
from datetime import datetime, timezone

import config
import store

_ERR_MAX = 200          # rolling-буфер ошибок
_ACT_DAYS_MAX = 40      # сколько последних дат активности хранить на юзера
DAY = 86400


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ================= ОШИБКИ =================

def log_error(source: str, msg: str, kind: str = "") -> None:
    """Добавить ошибку в rolling-лог. source: 'llm'|'service'|'broadcast'|'app', kind: код/тип."""
    try:
        entry = {
            "ts": int(time.time()),
            "source": (source or "app")[:20],
            "kind": (kind or "")[:40],
            "msg": (msg or "")[:200],
        }
        buf = store._load(config.ERROR_LOG_KEY).get("log", [])
        buf.append(entry)
        store._save(config.ERROR_LOG_KEY, {"log": buf[-_ERR_MAX:]})
    except Exception:
        pass


def get_errors(source: str = None, limit: int = 20) -> list:
    """Последние ошибки (свежие первыми), опционально по источнику."""
    try:
        buf = store._load(config.ERROR_LOG_KEY).get("log", [])
    except Exception:
        return []
    if source:
        buf = [e for e in buf if e.get("source") == source]
    return list(reversed(buf[-limit:]))


def clear_errors() -> None:
    try:
        store._save(config.ERROR_LOG_KEY, {"log": []})
    except Exception:
        pass


def errors_today() -> int:
    """Число ошибок за последние сутки."""
    try:
        cutoff = time.time() - DAY
        buf = store._load(config.ERROR_LOG_KEY).get("log", [])
        return sum(1 for e in buf if e.get("ts", 0) >= cutoff)
    except Exception:
        return 0


# ================= АКТИВНОСТЬ =================

def touch(cid) -> None:
    """Отметить активность пользователя: обновить last_seen, счётчик и список дней.

    Дёшево: одна запись на юзера, дни — усечённый список последних дат."""
    try:
        cid = str(cid)
        data = store._load(config.ACTIVITY_KEY)
        rec = data.get(cid) or {"last_ts": 0, "count": 0, "days": [], "first_ts": int(time.time())}
        rec["last_ts"] = int(time.time())
        rec["count"] = rec.get("count", 0) + 1
        rec.setdefault("first_ts", rec["last_ts"])
        today = _today()
        days = rec.get("days", [])
        if not days or days[-1] != today:
            days.append(today)
            rec["days"] = days[-_ACT_DAYS_MAX:]
        data[cid] = rec
        store._save(config.ACTIVITY_KEY, data)
    except Exception:
        pass


def _all() -> dict:
    try:
        return store._load(config.ACTIVITY_KEY) or {}
    except Exception:
        return {}


def get_activity(cid) -> dict:
    """Запись активности одного пользователя или {}."""
    return _all().get(str(cid), {})


def active_count(days: int = 1) -> int:
    """Сколько пользователей были активны за последние `days` суток."""
    cutoff = time.time() - days * DAY
    return sum(1 for r in _all().values() if r.get("last_ts", 0) >= cutoff)


def new_today() -> int:
    """Сколько пользователей впервые появились сегодня (по first_ts)."""
    cutoff = time.time() - DAY
    return sum(1 for r in _all().values() if r.get("first_ts", 0) >= cutoff)


def avg_messages() -> float:
    """Среднее число действий на пользователя среди тех, у кого есть активность."""
    recs = list(_all().values())
    if not recs:
        return 0.0
    total = sum(r.get("count", 0) for r in recs)
    return round(total / len(recs), 1)


def active_days_30(cid) -> int:
    """Сколько уникальных дней пользователь был активен за последние 30 суток."""
    days = get_activity(cid).get("days", [])
    cutoff = (datetime.now(timezone.utc).timestamp() - 30 * DAY)
    cnt = 0
    for d in days:
        try:
            ts = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            if ts >= cutoff:
                cnt += 1
        except Exception:
            continue
    return cnt


# ================= ФОРМАТИРОВАНИЕ (единый компонент для 3 мест) =================

def human_last_seen(cid) -> str:
    """Единая строка «последнего входа» (§3 DOCS). Три формулировки по свежести.

    Показывается в карточке пользователя, поиске и списке — один источник истины."""
    rec = get_activity(cid)
    ts = rec.get("last_ts", 0)
    if not ts:
        return "Не заходил"
    delta = time.time() - ts
    if delta < DAY:
        if delta < 3600:
            mins = max(1, int(delta // 60))
            return f"Последний вход: {mins} мин назад"
        hrs = int(delta // 3600)
        return f"Последний вход: {hrs} ч назад"
    days = int(delta // DAY)
    if days <= 14:
        return f"Последняя активность: {days} дн назад"
    return f"Не заходил: {days} дн"


def churn_dot(cid) -> str:
    """🟢/🟡/🔴 по свежести активности (для сигнала оттока)."""
    ts = get_activity(cid).get("last_ts", 0)
    if not ts:
        return "🔴"
    days = (time.time() - ts) / DAY
    if days <= 3:
        return "🟢"
    if days <= 14:
        return "🟡"
    return "🔴"
