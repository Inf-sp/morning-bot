"""Background provider probes and compact admin rendering.

Authoritative catalog, health transitions and fallback state live in
``provider_runtime``. This module only adapts probes and usage data to it.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import requests

import api_usage
import config
import provider_runtime
import storage_driver
import store

ServiceSpec = provider_runtime.ProviderSpec
SPECS = provider_runtime.SPECS
SPEC_BY_KEY = provider_runtime.SPEC_BY_KEY
UNKNOWN = provider_runtime.UNKNOWN
OK = provider_runtime.OK
WARNING = provider_runtime.WARNING
DOWN = provider_runtime.DOWN
_DOT = provider_runtime.DOT
_configured = provider_runtime.is_configured
_blank = provider_runtime.blank_state
_load = provider_runtime.load_state
_quota_from_headers = provider_runtime.quota_from_headers

_AI_SERVICES = ("groq", "gemini", "cloudflare", "openrouter")
_DATA_SERVICES = (
    "openweather", "firecrawl", "tavily", "tmdb", "google_books", "youtube", "languagetool",
    "spoonacular", "azure_speech", "ticketmaster", "pexels", "unsplash",
)
_DATA_CATEGORIES = {
    "openweather": "Погода",
    "firecrawl": "Поиск",
    "tavily": "Поиск",
    "tmdb": "Кино",
    "google_books": "Книги",
    "youtube": "Музыка",
    "languagetool": "Обучение",
    "spoonacular": "Готовка",
    "azure_speech": "Озвучка",
    "ticketmaster": "Концерты",
    "pexels": "Фото",
    "unsplash": "Фото",
}
_AI_ROLES = {
    "gemini": "Сложные задачи",
    "cloudflare": "Резерв",
    "openrouter": "Последний резерв",
}
_GROQ_MODELS = (
    ("simple", config.GROQ_SIMPLE_MODEL, "Основной"),
    ("standard", config.GROQ_STANDARD_MODEL, "Основной"),
    ("complex", config.GROQ_COMPLEX_MODEL, "Резерв"),
)


def _number(value) -> str:
    return f"{int(value):,}".replace(",", " ")


def _confirmed_quota(service: str, state: dict) -> tuple[int | None, int | None]:
    remaining, total = state.get("quota_remaining"), state.get("quota_total")
    if remaining is not None and total is not None:
        return int(remaining), int(total)
    usage = api_usage.service_usage(service)
    header_remaining, header_total = _quota_from_headers(usage.get("headers"))
    if header_remaining is not None and header_total is not None:
        return header_remaining, header_total
    return None, None


def _usage_detail(service: str) -> str:
    usage = api_usage.service_usage(service)
    requests_today = int(usage["requests_today"])
    if service == "telegram":
        return "ошибка отправки"
    if service == "themealdb":
        return "резервный источник"
    if service == "languagetool":
        return f"{_number(requests_today)} проверок сегодня"
    if service == "gemini":
        model_usage = api_usage.gemini_requests(config.GEMINI_MODEL)
        return f"{_number(model_usage['used'])} сегодня"
    if service.startswith("groq_model:"):
        return f"{_number(requests_today)} сегодня"
    if service == "cloudflare":
        return f"{_number(usage['neurons_today'])} нейронов сегодня"
    if service == "openrouter":
        return f"{_number(requests_today)} сегодня"
    if service == "azure_speech" and usage["characters_today"]:
        return f"{_number(usage['characters_today'])} символов сегодня"
    if service == "database":
        return "подключено"
    if service == "tavily" and usage["credits_month"]:
        return f"{_number(usage['credits_month'])} кредитов сегодня"
    return f"{_number(requests_today)} сегодня"


def _quota_text(remaining: int, total: int) -> str:
    if remaining <= 0:
        return "лимит исчерпан"
    return f"{_number(remaining)}/{_number(total)} осталось"


def _status_detail(service: str, state: dict) -> str:
    if state.get("error_type") == "fallback":
        return str(state.get("last_error") or "резерв недоступен")
    if service == "tavily" and provider_runtime.tavily_monthly_quota_exhausted():
        return "лимит исчерпан"
    if service == "tavily":
        budget = api_usage.tavily_budget()
        mode = " · экономный режим" if budget["mode"] == "economy" else ""
        return (f"{_number(budget['remaining'])}/{_number(budget['total'])} осталось"
                f"{mode} · ≈ {budget['daily_budget']} в день")
    if (
        state.get("status") not in (OK, UNKNOWN)
        and state.get("error_type") not in ("quota", "rate_limit")
    ):
        return str(state.get("last_error") or "сервис не ответил")
    remaining, total = _confirmed_quota(service, state)
    if remaining is not None and total is not None:
        if remaining <= 0:
            return "лимит исчерпан"
        return _quota_text(remaining, total)
    if state.get("status") not in (OK, UNKNOWN):
        return str(state.get("last_error") or "сервис не ответил")
    return _usage_detail(service)


def format_row(service: str, state: dict | None = None) -> str:
    spec = SPEC_BY_KEY[service]
    state = state or provider_runtime.get_state(service)
    status = state.get("status") if state.get("status") in _DOT else UNKNOWN
    if service == "groq":
        return _format_groq_row(state)
    if service in ("gemini", "cloudflare", "openrouter"):
        return _format_ai_row(service, state)
    if service == "google_books":
        usage = api_usage.google_books_requests()
        remaining = int(usage["remaining"])
        if remaining <= 0:
            return "🔴 Google Books · Книги · лимит исчерпан → Open Library"
        if not _configured(service):
            return "🔴 Google Books · Книги · API-ключ не настроен"
        if status in (WARNING, DOWN):
            return " · ".join([
                f"{_DOT[status]} {spec.label}", spec.category, _status_detail(service, state),
            ])
        return f"🟢 Google Books · Книги · {_number(remaining)}/1 000 осталось"
    remaining, total = _confirmed_quota(service, state)
    parts = [f"{_DOT[status]} {spec.label}"]
    category = _DATA_CATEGORIES.get(service, spec.category)
    if category:
        parts.append(category)
    parts.append(_status_detail(service, state))
    fallback = str(state.get("fallback") or "")
    if fallback and fallback in SPEC_BY_KEY:
        parts[-1] = f"{parts[-1]} → {SPEC_BY_KEY[fallback].label}"
    return " · ".join(parts)


def _format_groq_row(state: dict | None = None) -> str:
    """Одна пользовательская строка Groq без раскрытия внутренних моделей."""
    state = state or provider_runtime.get_state("groq")
    if not _configured("groq"):
        return "🔴 Groq · Основной · API-ключ не настроен"
    remaining, total = _confirmed_quota("groq", state)
    status = state.get("status") if state.get("status") in _DOT else UNKNOWN
    if (status in (OK, UNKNOWN) and remaining is not None and total
            and int(remaining) * 2 < int(total)):
        status = WARNING
    if remaining is not None and total is not None:
        detail = _quota_text(remaining, total)
    else:
        used = sum(
            int(api_usage.service_usage(api_usage.groq_model_service(model)).get("requests_today") or 0)
            for model in {model for _kind, model, _role in _GROQ_MODELS}
        )
        detail = f"{_number(used)} сегодня"
    return f"{_DOT[status]} Groq · Основной · {detail}"


def _format_ai_row(service: str, state: dict | None = None) -> str:
    state = state or provider_runtime.get_state(service)
    label = SPEC_BY_KEY[service].label
    role = _AI_ROLES[service]
    status = state.get("status") if state.get("status") in _DOT else UNKNOWN
    if not _configured(service):
        return f"🔴 {label} · {role} · API-ключ не настроен"
    # Неопределённый результат фонового probe — не подтверждённая поломка
    # сервиса. Показываем нейтральное состояние, пока не придёт реальная
    # ошибка (авторизация, лимит, сеть или 5xx) либо успешная проверка.
    if state.get("error_type") == "unknown":
        status = UNKNOWN
    quota_remaining, quota_total = _confirmed_quota(service, state)
    if service == "gemini":
        detail = _usage_detail(service)
    else:
        detail = (
            _quota_text(quota_remaining, quota_total)
            if quota_remaining is not None and quota_total is not None
            else _usage_detail(service)
        )
    if service == "gemini" and quota_remaining is not None and quota_remaining <= 0:
        detail = "лимит исчерпан"
    if (status in (DOWN, WARNING)
            and state.get("error_type") not in ("quota", "rate_limit", "unknown")):
        detail = str(state.get("last_error") or detail)
    return f"{_DOT[status]} {label} · {role} · {detail}"


def rows() -> list[str]:
    current = _load().get("services") or {}
    out = ["AI"]
    for service in ("groq", "gemini", "cloudflare", "openrouter"):
        state = current.get(service) or provider_runtime.get_state(service)
        if service == "groq":
            out.append(_format_groq_row(state))
        else:
            out.append(format_row(service, state))
    out.append("Данные")
    for service in _DATA_SERVICES:
        state = current.get(service) or provider_runtime.get_state(service)
        out.append(format_row(service, state))
    for service in ("telegram", "database"):
        state = current.get(service) or provider_runtime.get_state(service)
        if state.get("status") not in (OK, UNKNOWN):
            label = "PostgreSQL" if service == "database" else "Telegram"
            detail = "нет подключения" if service == "database" else "ошибка отправки"
            out.append(f"🔴 {label} · {detail}")
    return out


def last_check_time() -> str:
    checks = [int(row.get("last_check") or 0) for row in provider_runtime.states()]
    ts = max(checks, default=0)
    return datetime.fromtimestamp(ts, config.TZ).strftime("%H:%M") if ts else "—"


def _probe_request(service: str):
    """Return a declarative minimal request. No status rules live here."""
    common = {"timeout": 15}
    probes = {
        "gemini": ("GET", "https://generativelanguage.googleapis.com/v1beta/models", {"params": {"key": config.GEMINI_API_KEY, "pageSize": 1}}),
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
        "pexels": ("GET", "https://api.pexels.com/v1/curated", {"headers": {"Authorization": config.PEXELS_API_KEY}, "params": {"per_page": 1}}),
        "unsplash": ("GET", "https://api.unsplash.com/photos", {"headers": {"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"}, "params": {"per_page": 1}}),
        "telegram": ("GET", f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getMe", {}),
    }
    method, url, kwargs = probes[service]
    return method, url, {**common, **kwargs}


def probe(service: str) -> bool:
    started = time.monotonic()
    if service == "tavily" and provider_runtime.tavily_monthly_quota_exhausted():
        return False
    if service == "database":
        try:
            if not storage_driver.ping():
                raise storage_driver.StorageUnavailableError("PostgreSQL ping returned no row")
        except Exception as exc:
            provider_runtime.record_result(
                service, False, error=str(exc) or type(exc).__name__,
                exception_type=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
                record_history=False,
            )
            return False
        provider_runtime.record_result(
            service, True, latency_ms=int((time.monotonic() - started) * 1000),
            record_history=False,
        )
        return True
    if not _configured(service):
        provider_runtime.record_result(
            service, False, status_code=401, error="not configured",
            record_history=False,
        )
        return False
    try:
        method, url, kwargs = _probe_request(service)
        if service == "google_books" and not api_usage.google_books_requests()["allowed"]:
            return False
        try:
            response = requests.request(method, url, **kwargs)
        finally:
            if service == "google_books":
                api_usage.google_books_requests(consume=True)
        ok = 200 <= response.status_code < 300
        error = ""
        if not ok:
            error = (
                provider_runtime.google_error_details(response)
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
        provider_runtime.record_result(
            service, ok, status_code=response.status_code,
            error=error, headers=response.headers,
            quota_remaining=remaining, quota_total=total,
            latency_ms=int((time.monotonic() - started) * 1000),
            allow_quota_recovery=False,
            record_history=False,
        )
        return ok
    except requests.Timeout as exc:
        provider_runtime.record_result(
            service, False, error="timeout", exception_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
            record_history=False,
        )
    except requests.ConnectionError as exc:
        provider_runtime.record_result(
            service, False, error="network error", exception_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
            record_history=False,
        )
    except Exception as exc:
        provider_runtime.record_result(
            service, False, error=str(exc) or type(exc).__name__,
            exception_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
            record_history=False,
        )
    return False


def check_all(*, force=False) -> None:
    now = int(time.time())
    current = _load().get("services") or {}
    results = {}
    due = []
    for spec in SPECS:
        # YouTube search costs quota. Its status is updated by real lookups only,
        # never by a diagnostic request from the monitor.
        if spec.key == "youtube":
            continue
        if spec.key == "tavily" and provider_runtime.tavily_monthly_quota_exhausted(now):
            continue
        state = current.get(spec.key) or {}
        last = int(state.get("last_check") or 0)
        retryable_failure = state.get("error_type") in (
            "temporary", "timeout", "network", "unknown", "response",
        )
        # Недоступный внешний сервис не должен получать новый тяжёлый probe каждые
        # пять минут: это создаёт шум в админке и съедает лимиты именно во время сбоя.
        probe_every = min(spec.probe_every, 1800) if retryable_failure else spec.probe_every
        if not force and last and now - last < probe_every:
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
                    provider_runtime.record_result(
                        service, False, error=type(exc).__name__,
                        record_history=False,
                    )
                    results[service] = False
    # Probe results are diagnostic only. A reserve becomes selected only after
    # it has answered a real feature request in the AI router.


async def monitoring_job(_context) -> None:
    import asyncio
    await asyncio.to_thread(check_all)
