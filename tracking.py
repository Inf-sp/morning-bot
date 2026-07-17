"""Слой трекинга для админ-панели (§9 docs/admin.md).

Два дешёвых rolling-примитива поверх KV-store, плюс агрегаты для UI:

- errors   — лог ошибок для экрана «Логи» {ts, source, kind, msg}
- activity — last_seen + счётчик на пользователя {cid: {last_ts, count, days}}

Все записи best-effort: трекинг НИКОГДА не должен ломать основной поток бота,
поэтому каждая точка входа обёрнута в try/except с молчаливым проглатыванием.
"""
import inspect
import os
import sys
import time
import traceback as traceback_module
import uuid
from datetime import datetime

import config
import store

_ERR_MAX = 200          # rolling-буфер ошибок
_ACT_DAYS_MAX = 40      # сколько последних дат активности хранить на юзера
DAY = 86400
_TOUCH_THROTTLE_SECONDS = 60
INACTIVITY_REMINDER_SECONDS = 72 * 3600
_last_touch = {}

_SECTION_BY_MODULE = {
    "myday": "Мой день", "weather": "Мой день", "weather_provider": "Мой день",
    "cooking": "Готовка", "recipe_generation": "Готовка", "spoonacular": "Готовка",
    "themealdb": "Готовка", "learning": "Обучение", "trainer": "Обучение",
    "learning_game": "Обучение", "learning_dictionary": "Обучение",
    "dictionary_import": "Обучение", "language_tool": "Обучение",
    "wardrobe": "Гардероб", "wardrobe_copy": "Гардероб",
    "research": "Поиск", "assistant": "Ассистент", "leisure_movies": "Кино",
    "tmdb": "Кино", "leisure_books": "Книги", "google_books": "Книги",
    "leisure_music": "Музыка", "leisure_concerts": "Концерты",
    "azure_speech": "Озвучка", "dictionary_tts": "Озвучка", "travel": "Поездка",
    "balance": "Здоровье", "thoughts": "Здоровье", "settings": "Настройки",
    "menu": "Меню", "bot_callbacks": "Меню", "bot": "Бот",
}

_SERVICE_NAMES = (
    "azure speech", "language tool", "languagetool", "spoonacular", "themealdb",
    "openweather", "ticketmaster", "google books", "rest countries", "firecrawl",
    "openrouter", "github models", "cloudflare", "zeroentropy", "cohere", "gemini",
    "groq", "tavily", "telegram", "tmdb", "pexels",
)

_SERVICE_BY_MODULE = {
    "weather": "OpenWeather", "weather_provider": "OpenWeather",
    "spoonacular": "Spoonacular", "themealdb": "TheMealDB",
    "language_tool": "LanguageTool", "azure_speech": "Azure Speech",
    "dictionary_tts": "Azure Speech", "tmdb": "TMDB",
    "leisure_movies": "TMDB", "google_books": "Google Books",
    "leisure_books": "Google Books", "leisure_concerts": "Ticketmaster",
    "rerank": "ZeroEntropy", "research": "Tavily", "ai": "несколько AI-сервисов",
}

_FALLBACK_BY_SERVICE = {
    "OpenWeather": "сохранённый прогноз", "Spoonacular": "TheMealDB",
    "TheMealDB": "шаблон без AI", "LanguageTool": "проверка в коде",
    "Azure Speech": "текстовая карточка", "TMDB": "Gemini",
    "Google Books": "Open Library", "Ticketmaster": "Tavily",
    "ZeroEntropy": "поиск в базе", "Tavily": "Firecrawl",
    "Gemini": "GitHub Models", "Cohere": "Gemini", "Groq": "GitHub Models",
}


def _today() -> str:
    return datetime.now(config.TZ).strftime("%Y-%m-%d")


# ================= ОШИБКИ =================

def _safe_text(value, limit):
    try:
        import secure
        return secure.redact(str(value or ""))[:limit]
    except Exception:
        return str(value or "")[:limit]


def _error_frame(exc):
    if exc is not None and getattr(exc, "__traceback__", None) is not None:
        frames = traceback_module.extract_tb(exc.__traceback__)
        if frames:
            frame = frames[-1]
            return os.path.basename(frame.filename), int(frame.lineno), frame.name
    frame = inspect.stack()[2]
    return os.path.basename(frame.filename), int(frame.lineno), frame.function


def _section_for(file_name, source=""):
    module = os.path.basename(str(file_name or "")).removesuffix(".py")
    if module in _SECTION_BY_MODULE:
        return _SECTION_BY_MODULE[module]
    return "Ассистент" if source == "llm" else "Система"


def _action_for(function, source=""):
    name = str(function or "").casefold()
    if source == "llm":
        return "не сформирован ответ"
    if any(part in name for part in ("send", "show", "open")):
        return "не открылся экран"
    if any(part in name for part in ("save", "add", "create", "update")):
        return "не сохранились данные"
    if any(part in name for part in ("fetch", "search", "load")):
        return "не загрузились данные"
    if any(part in name for part in ("generate", "build", "render")):
        return "не создалась карточка"
    return "не выполнилось действие"


def _service_for(text, file_name=""):
    low = str(text or "").casefold().replace("_", " ")
    for name in _SERVICE_NAMES:
        if name in low:
            return (
                name.title()
                .replace("Languagetool", "LanguageTool")
                .replace("Themealdb", "TheMealDB")
                .replace("Github Models", "GitHub Models")
                .replace("Openweather", "OpenWeather")
                .replace("Openrouter", "OpenRouter")
                .replace("Firecrawl", "Firecrawl")
                .replace("Zeroentropy", "ZeroEntropy")
            )
    module = os.path.basename(str(file_name or "")).removesuffix(".py")
    return _SERVICE_BY_MODULE.get(module, "")


def log_error(source: str, msg: str, kind: str = "", *, section: str = "",
              action: str = "", service: str = "", fallback: str = "", exc=None) -> None:
    """Добавить безопасную диагностическую запись, не влияя на основной поток."""
    try:
        if exc is None:
            active = sys.exc_info()[1]
            exc = active if isinstance(active, BaseException) else None
        file_name, line, function = _error_frame(exc)
        error_type = type(exc).__name__ if exc is not None else ""
        raw_message = str(msg or exc or "Ошибка")
        error_text = f"{error_type}: {raw_message}" if error_type and not raw_message.startswith(f"{error_type}:") else raw_message
        if exc is not None:
            trace = "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__))
        else:
            trace = error_text
        service_name = _safe_text(service or _service_for(f"{kind} {raw_message}", file_name), 80)
        entry = {
            "ts": int(time.time()),
            "id": uuid.uuid4().hex[:12],
            "source": (source or "app")[:20],
            "kind": (kind or "")[:40],
            "msg": _safe_text(raw_message, 1000),
            "error": _safe_text(error_text, 1400),
            "section": _safe_text(section or _section_for(file_name, source), 40),
            "action": _safe_text(action or _action_for(function, source), 100),
            "traceback": _safe_text(trace, 7000),
            "file": _safe_text(file_name, 120),
            "line": int(line or 0),
            "function": _safe_text(function, 120),
            "service": service_name,
            "fallback": _safe_text(fallback or _FALLBACK_BY_SERVICE.get(service_name, ""), 80),
            "version": _safe_text(getattr(config, "APP_VERSION", ""), 40),
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
        now = time.time()
        if now - _last_touch.get(cid, 0) < _TOUCH_THROTTLE_SECONDS:
            return
        _last_touch[cid] = now
        data = store._load(config.ACTIVITY_KEY)
        rec = data.get(cid) or {"last_ts": 0, "count": 0, "days": [], "first_ts": int(now)}
        rec["last_ts"] = int(now)
        rec["inactivity_since_ts"] = int(now)
        rec.pop("inactivity_reminded_for_ts", None)
        rec.pop("inactivity_reminder_sent_ts", None)
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


def initialize_inactivity_tracking(cids, now=None) -> int:
    """Переносит существующую последнюю активность в состояние напоминаний.

    Благодаря ``last_ts`` давно неактивные пользователи попадают в первую
    рассылку сразу. Повторные запуски точку отсчёта не сдвигают.
    """
    try:
        now = int(time.time() if now is None else now)
        data = store._load(config.ACTIVITY_KEY) or {}
        changed = 0
        for cid in (str(value) for value in (cids or [])):
            rec = data.get(cid)
            if not rec or rec.get("inactivity_since_ts"):
                continue
            rec["inactivity_since_ts"] = int(rec.get("last_ts") or now)
            data[cid] = rec
            changed += 1
        if changed:
            store._save(config.ACTIVITY_KEY, data)
        return changed
    except Exception:
        return 0


def due_inactivity_reminders(cids, now=None) -> list[tuple[str, int]]:
    """Возвращает пользователей, неактивных 72 часа и ещё не уведомлённых."""
    now = int(time.time() if now is None else now)
    cutoff = now - INACTIVITY_REMINDER_SECONDS
    data = _all()
    due = []
    for cid in (str(value) for value in (cids or [])):
        rec = data.get(cid) or {}
        since_ts = int(rec.get("inactivity_since_ts") or 0)
        if not since_ts or since_ts > cutoff:
            continue
        if int(rec.get("inactivity_reminded_for_ts") or 0) == since_ts:
            continue
        due.append((cid, since_ts))
    return due


def mark_inactivity_reminded(cid, since_ts, sent_ts=None) -> bool:
    """Помечает цикл отправленным, только если с проверки не было активности."""
    try:
        cid = str(cid)
        data = store._load(config.ACTIVITY_KEY) or {}
        rec = data.get(cid) or {}
        if int(rec.get("inactivity_since_ts") or 0) != int(since_ts):
            return False
        rec["inactivity_reminded_for_ts"] = int(since_ts)
        rec["inactivity_reminder_sent_ts"] = int(time.time() if sent_ts is None else sent_ts)
        data[cid] = rec
        store._save(config.ACTIVITY_KEY, data)
        return True
    except Exception:
        return False


def active_count(days: int = 1) -> int:
    """Сколько пользователей были активны за последние `days` суток."""
    cutoff = time.time() - days * DAY
    return sum(1 for r in _all().values() if r.get("last_ts", 0) >= cutoff)


def active_today_count(cids=None, *, now=None) -> int:
    """Users with at least one action since local midnight."""
    current = datetime.now(config.TZ) if now is None else datetime.fromtimestamp(now, config.TZ)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    allowed = {str(cid) for cid in cids} if cids is not None else None
    return sum(
        1 for cid, record in _all().items()
        if (allowed is None or str(cid) in allowed) and float(record.get("last_ts") or 0) >= start
    )


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
