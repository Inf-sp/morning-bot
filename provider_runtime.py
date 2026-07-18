"""Authoritative provider catalog, health state and fallback transitions.

Product modules report request outcomes here. Usage metering and background
probes are adapters around this state; neither owns provider availability.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

import config
import store

_log = logging.getLogger(__name__)

UNKNOWN = "unknown"
OK = "ok"
WARNING = "warning"
DOWN = "down"
DOT = {UNKNOWN: "⚪", OK: "🟢", WARNING: "🟡", DOWN: "🔴"}
HISTORY_LIMIT = 300


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    sections: tuple[str, ...]
    fallbacks: tuple[str, ...] = ()
    probe_every: int = 300

    @property
    def category(self) -> str:
        sections = tuple(dict.fromkeys(self.sections))
        return sections[0] if len(sections) == 1 else "Везде"


# Fallbacks are directed. Firecrawl never points back to Tavily, so a failed
# search cannot bounce forever between the two providers.
SPECS = (
    ProviderSpec("cohere", "Cohere", ("Обучение", "Ассистент"), ("github_models", "gemini")),
    ProviderSpec("gemini", "Gemini", ("Готовка", "Обучение", "Ассистент"), ("github_models", "groq", "openrouter")),
    ProviderSpec("github_models", "GitHub Models", ("Готовка", "Обучение", "Ассистент"), ("openrouter",)),
    ProviderSpec("groq", "Groq", ("Готовка", "Обучение", "Ассистент"), ("github_models", "openrouter")),
    ProviderSpec("openrouter", "OpenRouter", ("Готовка",), ()),
    ProviderSpec("cloudflare", "Cloudflare AI", ("Ассистент",), ("github_models",)),
    ProviderSpec("openweather", "OpenWeather", ("Мой день", "Гардероб"), ()),
    ProviderSpec("tavily", "Tavily", ("Поиск", "Поездка", "Концерты"), ("firecrawl",)),
    ProviderSpec("firecrawl", "Firecrawl", ("Поиск",), (), 300),
    ProviderSpec("tmdb", "TMDB", ("Кино",), ()),
    ProviderSpec("google_books", "Google Books", ("Книги",), (), 86400),
    ProviderSpec("languagetool", "LanguageTool", ("Обучение",), ("gemini",)),
    ProviderSpec("spoonacular", "Spoonacular", ("Готовка",), ("themealdb",), 3600),
    ProviderSpec("themealdb", "TheMealDB", ("Готовка",), ()),
    ProviderSpec("azure_speech", "Azure Speech", ("Озвучка",), ()),
    ProviderSpec("ticketmaster", "Ticketmaster", ("Концерты",), ("tavily",)),
    ProviderSpec("zeroentropy", "ZeroEntropy", ("Здоровье",), (), 3600),
    ProviderSpec("pexels", "Pexels", ("Изображения",), ()),
    ProviderSpec("restcountries", "REST Countries", ("Поездка",), ("tavily",)),
    ProviderSpec("telegram", "Telegram", ("Мой день", "Готовка", "Обучение"), ()),
    ProviderSpec("database", "PostgreSQL", ("Мой день", "Готовка", "Обучение"), ()),
)
SPEC_BY_KEY = {spec.key: spec for spec in SPECS}
LABELS = {spec.key: spec.label for spec in SPECS}
AI_PROVIDERS = {"cohere", "gemini", "github_models", "groq", "openrouter", "cloudflare"}


def is_configured(provider: str) -> bool:
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
    return bool(values.get(provider))


def blank_state(_provider: str = "") -> dict:
    return {
        "status": UNKNOWN,
        "quota_remaining": None,
        "quota_total": None,
        "fallback": "",
        "last_check": None,
        "last_success": None,
        "last_error": "",
        "error_type": "",
        "incident_id": "",
        "incident_started_at": None,
    }


def _template() -> dict:
    return {"services": {spec.key: blank_state(spec.key) for spec in SPECS}, "history": []}


def normalise_state(data) -> dict:
    if not isinstance(data, dict):
        data = _template()
    services = data.setdefault("services", {})
    for spec in SPECS:
        current = services.setdefault(spec.key, {})
        for key, value in blank_state(spec.key).items():
            current.setdefault(key, value)
    data.setdefault("history", [])
    return data


def load_state() -> dict:
    try:
        return normalise_state(store._load(config.SERVICE_MONITOR_KEY))
    except Exception:
        return _template()


def quota_from_headers(headers) -> tuple[int | None, int | None]:
    values = {str(key).casefold(): value for key, value in dict(headers or {}).items()}
    pairs = (
        ("x-ratelimit-remaining", "x-ratelimit-limit"),
        ("x-ratelimit-remaining-requests", "x-ratelimit-limit-requests"),
        ("ratelimit-remaining", "ratelimit-limit"),
    )
    for remaining_key, total_key in pairs:
        try:
            remaining = int(float(values[remaining_key]))
            total = int(float(values[total_key]))
        except (KeyError, TypeError, ValueError):
            continue
        if total >= 0 and 0 <= remaining <= total:
            return remaining, total
    try:
        remaining = int(float(values["x-api-quota-left"]))
        used = int(float(values["x-api-quota-used"]))
        if remaining >= 0 and used >= 0:
            return remaining, remaining + used
    except (KeyError, TypeError, ValueError):
        pass
    return None, None


def google_error_details(response) -> str:
    """Compact Google API error fields without storing the response body."""
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
    text = " | ".join(
        str(value).strip() for value in values if str(value or "").strip()
    )
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


def _friendly_error(error="", status_code=None, provider="") -> tuple[str, str]:
    if provider == "google_books":
        return _google_books_error(error, status_code)
    raw = str(error or "").strip()
    low = raw.casefold().replace("_", " ")
    code = int(status_code or 0)
    if provider == "spoonacular" and code == 402:
        return "quota", "дневной лимит исчерпан"
    if provider == "tavily" and code == 432:
        return "quota", "лимит тарифа исчерпан"
    if code == 429 or any(value in low for value in ("rate limit", "too many requests")):
        return "quota", "лимит исчерпан"
    if any(value in low for value in ("quota exceeded", "quota exhausted")):
        return "quota", "лимит исчерпан"
    if code in (401, 403) or any(value in low for value in ("unauthorized", "forbidden", "invalid api key")):
        return "auth", "ошибка авторизации"
    if code in (408, 504) or any(value in low for value in ("timeout", "timed out")):
        return "timeout", "сервис не ответил"
    if any(value in low for value in ("connection", "network", "dns", "name resolution")):
        return "network", "ошибка сети"
    if code >= 500:
        return "temporary", "временная ошибка"
    if code >= 400:
        return "unknown", "не удалось определить статус"
    return "unknown", "не удалось определить статус"


def _append_history(
    data: dict, provider: str, text: str, now: int, *, event_type="status",
    incident_id="", status_code=None, exception_type="", message="",
    latency_ms=None, started_at=None, recovered_at=None, fallback_target="",
) -> None:
    history_rows = data.setdefault("history", [])
    last = history_rows[-1] if history_rows else {}
    if (
        last.get("service") == provider
        and last.get("event_type") == event_type
        and last.get("incident_id") == incident_id
        and last.get("text") == text
    ):
        return
    history_rows.append({
        "ts": now,
        "service": provider,
        "text": text,
        "event_type": event_type,
        "incident_id": incident_id,
        "status_code": int(status_code) if status_code else None,
        "exception_type": str(exception_type or "")[:80],
        "message": str(message or "")[:240],
        "latency_ms": int(latency_ms) if latency_ms is not None else None,
        "started_at": int(started_at or now),
        "recovered_at": int(recovered_at) if recovered_at else None,
        "fallback_target": str(fallback_target or "")[:40],
    })
    data["history"] = history_rows[-HISTORY_LIMIT:]


def _update_incident(data: dict, incident_id: str, **values) -> None:
    if not incident_id:
        return
    for event in reversed(data.get("history") or []):
        if event.get("event_type") == "error" and event.get("incident_id") == incident_id:
            event.update({key: value for key, value in values.items() if value is not None})
            return


def record_result(
    provider: str, ok: bool, *, status_code=None, error="", headers=None,
    quota_remaining=None, quota_total=None, checked_at=None, latency_ms=None,
    exception_type="", allow_quota_recovery=True,
) -> None:
    """Record a real request or probe through one health transition."""
    if provider not in SPEC_BY_KEY:
        return
    now = int(checked_at or time.time())
    header_remaining, header_total = quota_from_headers(headers)
    remaining = quota_remaining if quota_remaining is not None else header_remaining
    total = quota_total if quota_total is not None else header_total

    def mutate(data):
        data = normalise_state(data)
        state = data["services"][provider]
        old_status = state["status"]
        old_fallback = state.get("fallback") or ""
        old_error = state.get("last_error") or ""
        old_error_type = state.get("error_type") or ""
        incident_id = str(state.get("incident_id") or "")
        incident_started_at = int(state.get("incident_started_at") or now)
        state["last_check"] = now
        if ok and not allow_quota_recovery and old_error_type in ("quota", "rate_limit"):
            return data, None
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
            if quota_empty:
                if not incident_id:
                    incident_id = f"{provider}-{now}-{uuid.uuid4().hex[:8]}"
                    incident_started_at = now
                    _append_history(
                        data, provider, f"{SPEC_BY_KEY[provider].label}: лимит исчерпан.", now,
                        event_type="error", incident_id=incident_id,
                        message="лимит исчерпан", latency_ms=latency_ms,
                        started_at=incident_started_at,
                    )
                state["incident_id"] = incident_id
                state["incident_started_at"] = incident_started_at
            elif incident_id:
                _append_history(
                    data, provider, f"{SPEC_BY_KEY[provider].label} восстановлен.", now,
                    event_type="recovery", incident_id=incident_id,
                    started_at=incident_started_at, recovered_at=now,
                )
                _update_incident(data, incident_id, recovered_at=now)
                if old_fallback:
                    _append_history(
                        data, provider, f"{SPEC_BY_KEY[provider].label}: резерв отключён.", now,
                        event_type="system", incident_id=incident_id,
                        started_at=incident_started_at, recovered_at=now,
                    )
                state["incident_id"] = ""
                state["incident_started_at"] = None
            elif old_status != state["status"]:
                _append_history(
                    data, provider, f"{SPEC_BY_KEY[provider].label} работает.", now,
                    event_type="status",
                )
        else:
            kind, friendly = _friendly_error(error, status_code, provider)
            already_unavailable = (
                old_status == DOWN
                and not old_fallback
                and old_error == "резерв недоступен"
                and old_error_type == "fallback"
            )
            if not already_unavailable:
                state["error_type"], state["last_error"] = kind, friendly
                has_fallback = bool(state.get("fallback"))
                state["status"] = WARNING if has_fallback or kind in (
                    "quota", "rate_limit", "timeout", "temporary", "unknown",
                ) else DOWN
                if not incident_id:
                    incident_id = f"{provider}-{now}-{uuid.uuid4().hex[:8]}"
                    incident_started_at = now
                    state["incident_id"] = incident_id
                    state["incident_started_at"] = incident_started_at
                    _append_history(
                        data, provider, f"{SPEC_BY_KEY[provider].label}: {friendly}.", now,
                        event_type="error", incident_id=incident_id,
                        status_code=status_code, exception_type=exception_type,
                        message=str(error or friendly), latency_ms=latency_ms,
                        started_at=incident_started_at,
                    )
            for source, source_state in data["services"].items():
                if source_state.get("fallback") != provider:
                    continue
                source_state["fallback"] = ""
                source_state["status"] = DOWN
                source_state["last_error"] = "резерв недоступен"
                source_state["error_type"] = "fallback"
                source_incident = str(source_state.get("incident_id") or "")
                _append_history(
                    data, source, f"{SPEC_BY_KEY[source].label}: резерв недоступен.", now,
                    event_type="system", incident_id=source_incident,
                    started_at=source_state.get("incident_started_at") or now,
                )
        return data, None

    try:
        store.mutate_kv(config.SERVICE_MONITOR_KEY, mutate)
    except Exception:
        _log.exception("provider state write failed for %s", provider)


def _would_cycle(source: str, target: str, providers: dict) -> bool:
    seen = {source}
    current = target
    while current:
        if current in seen:
            return True
        seen.add(current)
        current = str((providers.get(current) or {}).get("fallback") or "")
    return False


def activate_fallback(provider: str, target: str, *, reason="") -> bool:
    """Persist only a fallback that has actually answered successfully."""
    if provider not in SPEC_BY_KEY or target not in SPEC_BY_KEY:
        return False
    if target not in SPEC_BY_KEY[provider].fallbacks and not (
        provider in AI_PROVIDERS and target in AI_PROVIDERS
    ):
        return False
    changed = {"value": False}
    now = int(time.time())

    def mutate(data):
        data = normalise_state(data)
        providers = data["services"]
        target_state = providers[target]
        if target_state.get("status") != OK or not target_state.get("last_success"):
            return data, None
        if _would_cycle(provider, target, providers):
            return data, None
        state = providers[provider]
        if state.get("fallback") == target:
            return data, None
        state["fallback"] = target
        state["status"] = WARNING
        if state.get("error_type") == "fallback":
            state["last_error"] = "основной сервис недоступен"
        incident_id = str(state.get("incident_id") or "")
        _append_history(
            data, provider,
            f"{SPEC_BY_KEY[provider].label}: переключение на {SPEC_BY_KEY[target].label}.", now,
            event_type="fallback", incident_id=incident_id,
            started_at=state.get("incident_started_at") or now,
            fallback_target=target,
        )
        _update_incident(data, incident_id, fallback_target=target)
        changed["value"] = True
        return data, None

    try:
        store.mutate_kv(config.SERVICE_MONITOR_KEY, mutate)
    except Exception:
        return False
    return changed["value"]


def selected_provider(provider: str) -> str:
    state = get_state(provider)
    fallback = str(state.get("fallback") or "")
    return fallback if fallback in SPEC_BY_KEY else provider


def get_state(provider: str) -> dict:
    return dict((load_state().get("services") or {}).get(provider) or blank_state(provider))


def states() -> list[dict]:
    data = load_state().get("services") or {}
    return [
        {"service": spec.key, **dict(data.get(spec.key) or blank_state(spec.key))}
        for spec in SPECS
    ]


def history(limit=50) -> list[dict]:
    return list(reversed((load_state().get("history") or [])[-max(0, int(limit)):]))


def clear_history() -> None:
    def mutate(data):
        data = normalise_state(data)
        data["history"] = []
        return data, None

    store.mutate_kv(config.SERVICE_MONITOR_KEY, mutate)


def record_unavailable_fallback(provider: str) -> None:
    if provider not in SPEC_BY_KEY:
        return
    now = int(time.time())

    def mutate(data):
        data = normalise_state(data)
        state = data["services"][provider]
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
        _append_history(
            data, provider, f"{SPEC_BY_KEY[provider].label}: резерв недоступен.", now,
            event_type="system", incident_id=state.get("incident_id") or "",
            started_at=state.get("incident_started_at") or now,
        )
        return data, None

    store.mutate_kv(config.SERVICE_MONITOR_KEY, mutate)


def validate_fallback_graph() -> list[str]:
    errors, visiting, visited = [], set(), set()

    def visit(provider):
        if provider in visiting:
            errors.append(f"{provider}: fallback cycle")
            return
        if provider in visited:
            return
        visiting.add(provider)
        for target in SPEC_BY_KEY[provider].fallbacks:
            if target not in SPEC_BY_KEY:
                errors.append(f"{provider}: unknown fallback {target}")
                continue
            visit(target)
        visiting.remove(provider)
        visited.add(provider)

    for spec in SPECS:
        visit(spec.key)
    return errors


assert not validate_fallback_graph(), validate_fallback_graph()
