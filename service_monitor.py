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
    if service == "openweather" and config.WEATHER_HARD_DAILY_LIMIT > 0:
        used = int(usage["requests_today"])
        total = int(config.WEATHER_HARD_DAILY_LIMIT)
        return max(total - used, 0), total
    if service == "gemini" and config.GEMINI_DAILY_LIMIT > 0:
        model_usage = api_usage.gemini_requests(config.GEMINI_MODEL)
        used = int(model_usage["used"])
        total = int(config.GEMINI_DAILY_LIMIT)
        return max(total - used, 0), total
    return None, None


def _usage_detail(service: str) -> str:
    usage = api_usage.service_usage(service)
    requests_today = int(usage["requests_today"])
    if service == "telegram":
        return "до 30 сообщений/с"
    if service in ("themealdb", "restcountries"):
        return "без дневной квоты"
    if service == "languagetool":
        return f"{_number(requests_today)} проверок сегодня"
    if service == "gemini":
        model_usage = api_usage.gemini_requests(config.GEMINI_MODEL)
        return f"{_number(model_usage['used'])} запросов сегодня · {config.GEMINI_MODEL}"
    if service == "azure_speech" and usage["characters_today"]:
        return f"{_number(usage['characters_today'])} символов сегодня"
    if service == "database":
        return "подключено"
    if service == "tavily" and usage["credits_month"]:
        return f"{_number(usage['credits_month'])} кредитов в этом месяце"
    return f"{_number(requests_today)} запросов сегодня"


def _status_detail(service: str, state: dict) -> str:
    if (
        state.get("status") not in (OK, UNKNOWN)
        and state.get("error_type") not in ("quota", "rate_limit")
    ):
        return str(state.get("last_error") or "сервис не ответил")
    remaining, total = _confirmed_quota(service, state)
    if remaining is not None and total is not None:
        if remaining <= 0:
            return "лимит исчерпан"
        return f"{_number(remaining)} из {_number(total)}"
    if state.get("status") not in (OK, UNKNOWN):
        return str(state.get("last_error") or "сервис не ответил")
    return _usage_detail(service)


def format_row(service: str, state: dict | None = None) -> str:
    spec = SPEC_BY_KEY[service]
    state = state or provider_runtime.get_state(service)
    status = state.get("status") if state.get("status") in _DOT else UNKNOWN
    if service == "cohere":
        usage = api_usage.cohere_requests()
        available = int(usage["remaining"])
        if available <= 0:
            return "🟡 Cohere · Везде · лимит исчерпан · используется GitHub Models"
        if not _configured(service):
            return "🔴 Cohere · Везде · API-ключ не настроен"
        if status in (WARNING, DOWN):
            return " · ".join([
                f"{_DOT[status]} {spec.label}", spec.category, _status_detail(service, state),
            ])
        dot = _DOT[WARNING if available <= 200 else OK]
        return f"{dot} Cohere · Везде · {_number(available)} из 1 000"
    if service == "google_books":
        usage = api_usage.google_books_requests()
        remaining = int(usage["remaining"])
        if remaining <= 0:
            return "🔴 Google Books · Книги · лимит исчерпан · используется Open Library"
        if not _configured(service):
            return "🔴 Google Books · Книги · API-ключ не настроен"
        if status in (WARNING, DOWN):
            return " · ".join([
                f"{_DOT[status]} {spec.label}", spec.category, _status_detail(service, state),
            ])
        dot = _DOT[WARNING if remaining <= 200 else OK]
        return f"{dot} Google Books · Книги · {_number(remaining)} из 1 000"
    remaining, total = _confirmed_quota(service, state)
    if status in (OK, UNKNOWN) and total and remaining is not None and remaining <= total * 0.2:
        status = WARNING
    parts = [f"{_DOT[status]} {spec.label}", spec.category, _status_detail(service, state)]
    fallback = str(state.get("fallback") or "")
    if fallback and fallback in SPEC_BY_KEY:
        parts.append(SPEC_BY_KEY[fallback].label)
    return " · ".join(parts)


def rows() -> list[str]:
    current = _load().get("services") or {}
    return [format_row(spec.key, current.get(spec.key)) for spec in SPECS]


def last_check_time() -> str:
    checks = [int(row.get("last_check") or 0) for row in provider_runtime.states()]
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
        "unsplash": ("GET", "https://api.unsplash.com/photos", {"headers": {"Authorization": f"Client-ID {config.UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"}, "params": {"per_page": 1}}),
        "restcountries": ("GET", "https://api.restcountries.com/countries/v5", {"headers": {"Authorization": f"Bearer {config.RESTCOUNTRIES_API_KEY}"}, "params": {"q": "Netherlands"}}),
        "telegram": ("GET", f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getMe", {}),
    }
    method, url, kwargs = probes[service]
    return method, url, {**common, **kwargs}


def probe(service: str) -> bool:
    started = time.monotonic()
    if service == "database":
        try:
            store._load("__service_monitor_health__")
        except Exception as exc:
            provider_runtime.record_result(
                service, False, error=str(exc) or type(exc).__name__,
                exception_type=type(exc).__name__,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return False
        provider_runtime.record_result(
            service, True, latency_ms=int((time.monotonic() - started) * 1000),
        )
        return True
    if not _configured(service):
        provider_runtime.record_result(
            service, False, status_code=401, error="not configured",
        )
        return False
    try:
        method, url, kwargs = _probe_request(service)
        if service == "cohere" and not api_usage.cohere_requests()["allowed"]:
            return False
        if service == "google_books" and not api_usage.google_books_requests()["allowed"]:
            return False
        try:
            response = requests.request(method, url, **kwargs)
        finally:
            if service == "cohere":
                api_usage.cohere_requests(consume=True)
            elif service == "google_books":
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
        )
        return ok
    except requests.Timeout as exc:
        provider_runtime.record_result(
            service, False, error="timeout", exception_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except requests.ConnectionError as exc:
        provider_runtime.record_result(
            service, False, error="network error", exception_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        provider_runtime.record_result(
            service, False, error=str(exc) or type(exc).__name__,
            exception_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return False


def check_all(*, force=False) -> None:
    now = int(time.time())
    current = _load().get("services") or {}
    results = {}
    due = []
    for spec in SPECS:
        state = current.get(spec.key) or {}
        last = int(state.get("last_check") or 0)
        retryable_failure = state.get("error_type") in (
            "temporary", "timeout", "network", "unknown", "response",
        )
        probe_every = min(spec.probe_every, 300) if retryable_failure else spec.probe_every
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
                    )
                    results[service] = False
    # A reserve is checked before selection and is shown only after its own
    # successful probe. Actual request routers also call activate_fallback.
    for spec in SPECS:
        if spec.key == "cohere" and not api_usage.cohere_requests()["allowed"]:
            continue
        state = provider_runtime.get_state(spec.key)
        needs_fallback = results.get(spec.key) is False or state.get("error_type") == "quota"
        if not needs_fallback:
            continue
        for fallback in spec.fallbacks:
            fallback_ok = results.get(fallback)
            if fallback_ok is None:
                fallback_ok = probe(fallback)
                results[fallback] = fallback_ok
            if fallback_ok is True:
                provider_runtime.activate_fallback(
                    spec.key, fallback, reason="monitor",
                )
                break
        else:
            if spec.fallbacks:
                state = provider_runtime.get_state(spec.key)
                if state.get("status") != UNKNOWN:
                    provider_runtime.record_unavailable_fallback(spec.key)


async def monitoring_job(_context) -> None:
    import asyncio
    await asyncio.to_thread(check_all)
