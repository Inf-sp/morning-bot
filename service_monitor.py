"""Unified, persistent service health monitoring.

The admin UI deliberately knows nothing about individual providers.  Every
external call and every background probe is reduced to the same state model;
this module also owns fallback selection and status history.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
import requests

import config
import store

_log = logging.getLogger(__name__)

UNKNOWN = "unknown"
OK = "ok"
WARNING = "warning"
DOWN = "down"
_DOT = {UNKNOWN: "⚪", OK: "🟢", WARNING: "🟡", DOWN: "🔴"}
_HISTORY_LIMIT = 300


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    label: str
    sections: tuple[str, ...]
    fallbacks: tuple[str, ...] = ()
    probe_every: int = 300

    @property
    def category(self) -> str:
        sections = tuple(dict.fromkeys(self.sections))
        return sections[0] if len(sections) == 1 else "Везде"


# Fallbacks are directed.  In particular, Firecrawl never points back to
# Tavily, so a failed search cannot bounce forever between the two providers.
SPECS = (
    ServiceSpec("cohere", "Cohere", ("Обучение", "Ассистент"), ("gemini", "github_models")),
    ServiceSpec("gemini", "Gemini", ("Готовка", "Обучение", "Ассистент"), ("github_models", "groq", "openrouter")),
    ServiceSpec("github_models", "GitHub Models", ("Готовка", "Обучение", "Ассистент"), ("openrouter",)),
    ServiceSpec("groq", "Groq", ("Готовка", "Обучение", "Ассистент"), ("github_models", "openrouter")),
    ServiceSpec("openrouter", "OpenRouter", ("Готовка",), ()),
    ServiceSpec("cloudflare", "Cloudflare AI", ("Ассистент",), ("github_models",)),
    ServiceSpec("openweather", "OpenWeather", ("Мой день", "Гардероб"), ()),
    ServiceSpec("tavily", "Tavily", ("Поиск", "Поездка", "Концерты"), ("firecrawl",)),
    ServiceSpec("firecrawl", "Firecrawl", ("Поиск",), (), 300),
    ServiceSpec("tmdb", "TMDB", ("Кино",), ()),
    ServiceSpec("google_books", "Google Books", ("Книги",), (), 86400),
    ServiceSpec("languagetool", "LanguageTool", ("Обучение",), ("gemini",)),
    ServiceSpec("spoonacular", "Spoonacular", ("Готовка",), ("themealdb",), 3600),
    ServiceSpec("themealdb", "TheMealDB", ("Готовка",), ()),
    ServiceSpec("azure_speech", "Azure Speech", ("Озвучка",), ()),
    ServiceSpec("ticketmaster", "Ticketmaster", ("Концерты",), ("tavily",)),
    ServiceSpec("zeroentropy", "ZeroEntropy", ("Здоровье",), (), 3600),
    ServiceSpec("pexels", "Pexels", ("Изображения",), ()),
    ServiceSpec("restcountries", "REST Countries", ("Поездка",), ("tavily",)),
    ServiceSpec("telegram", "Telegram", ("Мой день", "Готовка", "Обучение"), ()),
    ServiceSpec("database", "PostgreSQL", ("Мой день", "Готовка", "Обучение"), ()),
)
SPEC_BY_KEY = {spec.key: spec for spec in SPECS}
_AI_SERVICES = {"cohere", "gemini", "github_models", "groq", "openrouter", "cloudflare"}


def _configured(service: str) -> bool:
    values = {
        "cohere": config.COHERE_API_KEY,
        "gemini": config.GEMINI_API_KEY,
        "github_models": config.GITHUB_MODELS_TOKEN,
        "groq": config.GROQ_API_KEY,
        "openrouter": config.OPENROUTER_API_KEY,
        "cloudflare": config.CF_API_TOKEN and config.CF_ACCOUNT_ID,
        "openweather": config.WEATHER_API_KEY,
        "tavily": config.TAVILY_API_KEY,
        "firecrawl": config.FIRECRAWL_API_KEY,
        "tmdb": config.TMDB_API_KEY,
        "google_books": config.GOOGLE_BOOKS_API_KEY,
        "languagetool": config.LANGUAGETOOL_API_URL,
        "spoonacular": config.SPOONACULAR_API_KEY,
        "themealdb": config.THEMEALDB_API_KEY,
        "azure_speech": config.AZURE_SPEECH_KEY and config.AZURE_SPEECH_REGION,
        "ticketmaster": config.TICKETMASTER_API_KEY,
        "zeroentropy": config.ZEROENTROPY_API_KEY,
        "pexels": config.PEXELS_API_KEY,
        "restcountries": config.RESTCOUNTRIES_API_KEY,
        "telegram": config.TELEGRAM_TOKEN,
        "database": config.DATABASE_URL,
    }
    return bool(values.get(service))


def _blank(service: str) -> dict:
    return {
        "status": UNKNOWN,
        "quota_remaining": None,
        "quota_total": None,
        "fallback": "",
        "last_check": None,
        "last_success": None,
        "last_error": "",
        "error_type": "",
    }


def _template() -> dict:
    return {"services": {spec.key: _blank(spec.key) for spec in SPECS}, "history": []}


def _normalise(data) -> dict:
    if not isinstance(data, dict):
        data = _template()
    services = data.setdefault("services", {})
    for spec in SPECS:
        current = services.setdefault(spec.key, {})
        for key, value in _blank(spec.key).items():
            current.setdefault(key, value)
    data.setdefault("history", [])
    return data


def _load() -> dict:
    try:
        return _normalise(store._load(config.SERVICE_MONITOR_KEY))
    except Exception:
        return _template()


def google_error_details(response) -> str:
    """Compact Google API error fields without storing the full response body."""
    try:
        payload = response.json() if response.content else {}
    except (TypeError, ValueError):
        return f"HTTP {getattr(response, 'status_code', 0) or '?'}"
    error = payload.get("error") if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return f"HTTP {getattr(response, 'status_code', 0) or '?'}"
    values = [error.get("code"), error.get("status"), error.get("message")]
    for item in error.get("errors") or []:
        if isinstance(item, dict):
            values.extend((item.get("reason"), item.get("message")))
    text = " | ".join(str(value).strip() for value in values if str(value or "").strip())
    return text or f"HTTP {getattr(response, 'status_code', 0) or '?'}"


def _google_books_error(error="", status_code=None) -> tuple[str, str]:
    raw = str(error or "").strip()
    low = raw.casefold().replace("_", " ")
    code = int(status_code or 0)
    if "not configured" in low:
        return "auth", "API-ключ не настроен"
    if code == 429:
        return "rate_limit", "слишком много запросов"
    if code == 403 and any(marker in low for marker in (
        "quota exceeded", "quotaexceeded", "daily limit", "dailylimitexceeded",
        "rate limit exceeded", "ratelimitexceeded", "resource exhausted",
    )):
        return "quota", "дневной лимит исчерпан"
    if any(marker in low for marker in (
        "invalid api key", "api key invalid", "api key not valid", "keyinvalid",
        "bad api key",
    )):
        return "auth", "неверный API-ключ"
    if code == 403 and any(marker in low for marker in (
        "accessnotconfigured", "api not enabled", "has not been used",
        "is disabled", "service disabled",
    )):
        return "api_disabled", "API не включён"
    if code == 403 or any(marker in low for marker in (
        "request denied", "permission denied", "access denied",
    )):
        return "access_denied", "доступ запрещён"
    if 500 <= code <= 599:
        return "temporary", "сервис Google недоступен"
    if any(marker in low for marker in (
        "timeout", "timed out", "connection", "network", "dns", "name resolution",
    )):
        return "network", "нет соединения"
    if code == 400:
        return "request", "ошибка запроса"
    if "invalid json" in low:
        return "response", "некорректный ответ Google"
    return "unknown", "не удалось проверить доступ"


def _friendly_error(error="", status_code=None, service="") -> tuple[str, str]:
    if service == "google_books":
        return _google_books_error(error, status_code)
    raw = str(error or "").strip()
    low = raw.casefold().replace("_", " ")
    code = int(status_code or 0)
    if code == 429 or any(x in low for x in ("rate limit", "too many requests")):
        return "quota", "лимит исчерпан"
    if any(x in low for x in ("quota exceeded", "quota exhausted")):
        return "quota", "лимит исчерпан"
    if code in (401, 403) or any(x in low for x in ("unauthorized", "forbidden", "invalid api key")):
        return "auth", "ошибка авторизации"
    if code in (408, 504) or any(x in low for x in ("timeout", "timed out")):
        return "timeout", "сервис не ответил"
    if any(x in low for x in ("connection", "network", "dns", "name resolution")):
        return "network", "ошибка сети"
    if code >= 500:
        return "temporary", "временная ошибка"
    if code >= 400:
        return "unknown", "не удалось определить статус"
    return "unknown", "не удалось определить статус"


def _quota_from_headers(headers) -> tuple[int | None, int | None]:
    values = {str(k).casefold(): v for k, v in dict(headers or {}).items()}
    pairs = (
        ("x-ratelimit-remaining", "x-ratelimit-limit"),
        ("ratelimit-remaining", "ratelimit-limit"),
        ("x-api-quota-left", "x-api-quota-request"),
    )
    for remaining_key, total_key in pairs:
        try:
            remaining = int(float(values[remaining_key]))
            total = int(float(values[total_key]))
        except (KeyError, TypeError, ValueError):
            continue
        if total >= 0 and 0 <= remaining <= total:
            return remaining, total
    return None, None


def _append_history(data: dict, service: str, text: str, now: int) -> None:
    history = data.setdefault("history", [])
    last = history[-1] if history else {}
    if last.get("service") == service and last.get("text") == text:
        return
    history.append({"ts": now, "service": service, "text": text})
    data["history"] = history[-_HISTORY_LIMIT:]


def record_result(service: str, ok: bool, *, status_code=None, error="", headers=None,
                  quota_remaining=None, quota_total=None, checked_at=None) -> None:
    """Record a real API result or a probe using the common state transition."""
    if service not in SPEC_BY_KEY:
        return
    now = int(checked_at or time.time())
    header_remaining, header_total = _quota_from_headers(headers)
    remaining = quota_remaining if quota_remaining is not None else header_remaining
    total = quota_total if quota_total is not None else header_total

    def mutate(data):
        data = _normalise(data)
        state = data["services"][service]
        old_status = state["status"]
        old_fallback = state.get("fallback") or ""
        old_error = state.get("last_error") or ""
        old_error_type = state.get("error_type") or ""
        state["last_check"] = now
        if remaining is not None and total is not None:
            try:
                remaining_i, total_i = int(remaining), int(total)
                if total_i >= 0 and 0 <= remaining_i <= total_i:
                    state["quota_remaining"], state["quota_total"] = remaining_i, total_i
            except (TypeError, ValueError):
                pass
        if ok:
            quota_empty = state.get("quota_remaining") == 0 and state.get("quota_total") is not None
            state.update({
                "status": WARNING if quota_empty else OK,
                "last_success": now,
                "last_error": "лимит исчерпан" if quota_empty else "",
                "error_type": "quota" if quota_empty else "",
                "fallback": "",
            })
            if old_status != state["status"]:
                message = (
                    f"{SPEC_BY_KEY[service].label}: лимит исчерпан."
                    if quota_empty else f"{SPEC_BY_KEY[service].label} работает."
                )
                _append_history(data, service, message, now)
            if old_fallback:
                _append_history(
                    data, service,
                    f"{SPEC_BY_KEY[service].label}: резерв отключён.", now,
                )
        else:
            kind, friendly = _friendly_error(error, status_code, service)
            already_unavailable = (
                old_status == DOWN
                and not old_fallback
                and old_error == "резерв недоступен"
                and old_error_type == "fallback"
            )
            if not already_unavailable:
                state["error_type"], state["last_error"] = kind, friendly
                has_fallback = bool(state.get("fallback"))
                # A transient failure is yellow while a route can still recover.  A
                # hard failure without an active/available reserve is red.
                state["status"] = WARNING if has_fallback or kind in (
                    "quota", "rate_limit", "timeout", "temporary", "unknown",
                ) else DOWN
                if old_status != state["status"] or old_error != friendly:
                    _append_history(data, service, f"{SPEC_BY_KEY[service].label}: {friendly}.", now)
            # A reserve that has just failed is no longer a real reserve. Any
            # primary pointing to it becomes red until another candidate is
            # checked and selected.
            for source, source_state in data["services"].items():
                if source_state.get("fallback") != service:
                    continue
                source_state["fallback"] = ""
                source_state["status"] = DOWN
                source_state["last_error"] = "резерв недоступен"
                source_state["error_type"] = "fallback"
                _append_history(
                    data, source,
                    f"{SPEC_BY_KEY[source].label}: резерв недоступен.", now,
                )
        return data, None

    try:
        store.mutate_kv(config.SERVICE_MONITOR_KEY, mutate)
    except Exception:
        _log.exception("service monitor state write failed for %s", service)


def _would_cycle(source: str, target: str, services: dict) -> bool:
    seen = {source}
    current = target
    while current:
        if current in seen:
            return True
        seen.add(current)
        current = str((services.get(current) or {}).get("fallback") or "")
    return False


def activate_fallback(service: str, target: str, *, reason="") -> bool:
    """Persist only a fallback that has actually answered successfully."""
    if service not in SPEC_BY_KEY or target not in SPEC_BY_KEY:
        return False
    if target not in SPEC_BY_KEY[service].fallbacks and not (
        service in _AI_SERVICES and target in _AI_SERVICES
    ):
        return False
    changed = {"value": False}
    now = int(time.time())

    def mutate(data):
        data = _normalise(data)
        states = data["services"]
        target_state = states[target]
        # A successful request just recorded by the caller is the proof that
        # switching is real. Stale or unchecked reserves are never advertised.
        if target_state.get("status") != OK or not target_state.get("last_success"):
            return data, None
        if _would_cycle(service, target, states):
            return data, None
        state = states[service]
        if state.get("fallback") == target:
            return data, None
        state["fallback"] = target
        state["status"] = WARNING
        if state.get("error_type") == "fallback":
            state["last_error"] = "основной сервис недоступен"
        _append_history(
            data, service,
            f"{SPEC_BY_KEY[service].label}: переключение на {SPEC_BY_KEY[target].label}.", now,
        )
        changed["value"] = True
        return data, None

    try:
        store.mutate_kv(config.SERVICE_MONITOR_KEY, mutate)
    except Exception:
        return False
    return changed["value"]


def selected_service(service: str) -> str:
    state = get_state(service)
    fallback = str(state.get("fallback") or "")
    return fallback if fallback in SPEC_BY_KEY else service


def get_state(service: str) -> dict:
    return dict((_load().get("services") or {}).get(service) or _blank(service))


def states() -> list[dict]:
    data = _load().get("services") or {}
    return [{"service": spec.key, **dict(data.get(spec.key) or _blank(spec.key))} for spec in SPECS]


def history(limit=50) -> list[dict]:
    return list(reversed((_load().get("history") or [])[-max(0, int(limit)):]))


def clear_history() -> None:
    def mutate(data):
        data = _normalise(data)
        data["history"] = []
        return data, None
    store.mutate_kv(config.SERVICE_MONITOR_KEY, mutate)


def _number(value) -> str:
    return f"{int(value):,}".replace(",", " ")


def _status_detail(state: dict) -> str:
    remaining, total = state.get("quota_remaining"), state.get("quota_total")
    if remaining is not None and total is not None:
        remaining = int(remaining)
        if remaining <= 0:
            return "лимит исчерпан"
        if remaining == 1:
            return "остался 1 запрос"
        return f"осталось {_number(remaining)} из {_number(total)}"
    if state.get("status") == UNKNOWN:
        return "лимит неизвестен"
    if state.get("status") != OK:
        return str(state.get("last_error") or "не удалось определить статус")
    return "лимит неизвестен"


def format_row(service: str, state: dict | None = None) -> str:
    spec = SPEC_BY_KEY[service]
    state = state or get_state(service)
    status = state.get("status") if state.get("status") in _DOT else UNKNOWN
    parts = [f"{_DOT[status]} {spec.label}", spec.category]
    if service != "google_books" or status != OK:
        parts.append(_status_detail(state))
    fallback = str(state.get("fallback") or "")
    if fallback and fallback in SPEC_BY_KEY:
        parts.append(SPEC_BY_KEY[fallback].label)
    elif status == DOWN and spec.fallbacks:
        parts[-1] = "резерв недоступен"
    return " · ".join(parts)


def rows() -> list[str]:
    current = _load().get("services") or {}
    return [format_row(spec.key, current.get(spec.key)) for spec in SPECS]


def last_check_time() -> str:
    checks = [int(row.get("last_check") or 0) for row in states()]
    ts = max(checks, default=0)
    return datetime.fromtimestamp(ts, config.TZ).strftime("%H:%M") if ts else "—"


def _probe_request(service: str):
    """Return a declarative minimal request. No status rules live here."""
    common = {"timeout": 15}
    probes = {
        "gemini": ("GET", "https://generativelanguage.googleapis.com/v1beta/models", {"params": {"key": config.GEMINI_API_KEY, "pageSize": 1}}),
        "cohere": ("GET", "https://api.cohere.com/v1/models", {"headers": {"Authorization": f"Bearer {config.COHERE_API_KEY}"}, "params": {"page_size": 1}}),
        "github_models": ("GET", "https://models.github.ai/catalog/models", {"headers": {"Authorization": f"Bearer {config.GITHUB_MODELS_TOKEN}"}}),
        "groq": ("GET", "https://api.groq.com/openai/v1/models", {"headers": {"Authorization": f"Bearer {config.GROQ_API_KEY}"}}),
        "openrouter": ("GET", "https://openrouter.ai/api/v1/key", {"headers": {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}}),
        "cloudflare": ("GET", f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/models/search", {"headers": {"Authorization": f"Bearer {config.CF_API_TOKEN}"}, "params": {"per_page": 1}}),
        "openweather": ("GET", "https://api.openweathermap.org/data/2.5/weather", {"params": {"q": "Amsterdam", "appid": config.WEATHER_API_KEY}}),
        "tavily": ("GET", "https://api.tavily.com/usage", {"headers": {"Authorization": f"Bearer {config.TAVILY_API_KEY}"}}),
        "firecrawl": ("GET", "https://api.firecrawl.dev/v2/team/credit-usage", {"headers": {"Authorization": f"Bearer {config.FIRECRAWL_API_KEY}"}}),
        "tmdb": ("GET", "https://api.themoviedb.org/3/configuration", {"params": {"api_key": config.TMDB_API_KEY}}),
        "google_books": ("GET", "https://www.googleapis.com/books/v1/volumes", {"params": {"q": "1984", "maxResults": 1, "printType": "books", "projection": "lite", "key": config.GOOGLE_BOOKS_API_KEY}}),
        "languagetool": ("POST", f"{config.LANGUAGETOOL_API_URL}/check", {"data": {"text": "Dit is goed.", "language": "nl-NL"}}),
        "spoonacular": ("GET", "https://api.spoonacular.com/food/ingredients/search", {"params": {"query": "apple", "number": 1, "apiKey": config.SPOONACULAR_API_KEY}}),
        "themealdb": ("GET", f"https://www.themealdb.com/api/json/v1/{config.THEMEALDB_API_KEY}/lookup.php", {"params": {"i": "52772"}}),
        "azure_speech": ("GET", f"https://{config.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/voices/list", {"headers": {"Ocp-Apim-Subscription-Key": config.AZURE_SPEECH_KEY}}),
        "ticketmaster": ("GET", "https://app.ticketmaster.com/discovery/v2/events.json", {"params": {"apikey": config.TICKETMASTER_API_KEY, "size": 1}}),
        "zeroentropy": ("POST", "https://api.zeroentropy.dev/v1/models/rerank", {"headers": {"Authorization": f"Bearer {config.ZEROENTROPY_API_KEY}", "Content-Type": "application/json"}, "json": {"model": "zerank-2", "query": "test", "documents": ["test"], "top_n": 1, "latency": "fast"}}),
        "pexels": ("GET", "https://api.pexels.com/v1/curated", {"headers": {"Authorization": config.PEXELS_API_KEY}, "params": {"per_page": 1}}),
        "restcountries": ("GET", "https://api.restcountries.com/countries/v5", {"headers": {"Authorization": f"Bearer {config.RESTCOUNTRIES_API_KEY}"}, "params": {"q": "Netherlands"}}),
        "telegram": ("GET", f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getMe", {}),
    }
    method, url, kwargs = probes[service]
    return method, url, {**common, **kwargs}


def probe(service: str) -> bool:
    if service == "database":
        try:
            store._load("__service_monitor_health__")
        except Exception as exc:
            record_result(service, False, error=type(exc).__name__)
            return False
        record_result(service, True)
        return True
    if not _configured(service):
        record_result(service, False, status_code=401, error="not configured")
        return False
    try:
        method, url, kwargs = _probe_request(service)
        response = requests.request(method, url, **kwargs)
        ok = 200 <= response.status_code < 300
        error = ""
        if not ok:
            error = (
                google_error_details(response)
                if service == "google_books"
                else f"HTTP {response.status_code}"
            )
        remaining = total = None
        if service == "firecrawl" and ok:
            payload = response.json() if response.content else {}
            remaining = payload.get("remainingCredits")
            if remaining is None:
                remaining = payload.get("remaining_credits")
            total = payload.get("totalCredits")
            if total is None:
                total = payload.get("total_credits")
        elif service == "openrouter" and ok:
            payload = (response.json() or {}).get("data") or {}
            limit = payload.get("limit")
            used = payload.get("usage")
            if limit is not None and used is not None:
                total, remaining = int(limit), max(0, int(limit) - int(used))
        elif service == "tavily" and ok:
            payload = (response.json() or {}).get("key") or {}
            used, limit = payload.get("usage"), payload.get("limit")
            if limit is not None and used is not None:
                total, remaining = int(limit), max(0, int(limit) - int(used))
        record_result(
            service, ok, status_code=response.status_code,
            error=error, headers=response.headers,
            quota_remaining=remaining, quota_total=total,
        )
        return ok
    except requests.Timeout:
        record_result(service, False, error="timeout")
    except requests.ConnectionError:
        record_result(service, False, error="network error")
    except Exception as exc:
        record_result(service, False, error=type(exc).__name__)
    return False


def check_all(*, force=False) -> None:
    now = int(time.time())
    current = _load().get("services") or {}
    results = {}
    due = []
    for spec in SPECS:
        last = int((current.get(spec.key) or {}).get("last_check") or 0)
        if not force and last and now - last < spec.probe_every:
            continue
        due.append(spec.key)
    # One slow provider must not delay all other statuses past the five-minute
    # monitoring window. State writes remain atomic through store.mutate_kv.
    if due:
        with ThreadPoolExecutor(max_workers=min(8, len(due))) as pool:
            futures = {pool.submit(probe, service): service for service in due}
            for future in as_completed(futures):
                service = futures[future]
                try:
                    results[service] = bool(future.result())
                except Exception as exc:
                    record_result(service, False, error=type(exc).__name__)
                    results[service] = False
    # A reserve is checked before selection and is shown only after its own
    # successful probe. Actual request routers also call activate_fallback.
    for spec in SPECS:
        state = get_state(spec.key)
        needs_fallback = results.get(spec.key) is False or state.get("error_type") == "quota"
        if not needs_fallback:
            continue
        for fallback in spec.fallbacks:
            fallback_ok = results.get(fallback)
            if fallback_ok is None:
                fallback_ok = probe(fallback)
                results[fallback] = fallback_ok
            if fallback_ok is True:
                activate_fallback(spec.key, fallback, reason="monitor")
                break
        else:
            if spec.fallbacks:
                state = get_state(spec.key)
                if state.get("status") != UNKNOWN:
                    record_unavailable_fallback(spec.key)


def record_unavailable_fallback(service: str) -> None:
    if service not in SPEC_BY_KEY:
        return
    now = int(time.time())
    def mutate(data):
        data = _normalise(data)
        state = data["services"][service]
        if (
            state.get("status") == DOWN
            and not state.get("fallback")
            and state.get("last_error") == "резерв недоступен"
            and state.get("error_type") == "fallback"
        ):
            return data, None
        state["status"] = DOWN
        state["fallback"] = ""
        state["last_error"] = "резерв недоступен"
        state["error_type"] = "fallback"
        _append_history(data, service, f"{SPEC_BY_KEY[service].label}: резерв недоступен.", now)
        return data, None
    store.mutate_kv(config.SERVICE_MONITOR_KEY, mutate)


async def monitoring_job(_context) -> None:
    import asyncio
    await asyncio.to_thread(check_all)


def validate_fallback_graph() -> list[str]:
    errors, visiting, visited = [], set(), set()

    def visit(service):
        if service in visiting:
            errors.append(f"{service}: fallback cycle")
            return
        if service in visited:
            return
        visiting.add(service)
        for target in SPEC_BY_KEY[service].fallbacks:
            if target not in SPEC_BY_KEY:
                errors.append(f"{service}: unknown fallback {target}")
                continue
            visit(target)
        visiting.remove(service)
        visited.add(service)

    for spec in SPECS:
        visit(spec.key)
    return errors


assert not validate_fallback_graph(), validate_fallback_graph()
