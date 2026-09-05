import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import provider_runtime
import service_monitor


def _memory_store(monkeypatch):
    memory = {}

    def load(key):
        return memory.get(key, {})

    def mutate(key, callback):
        data, result = callback(memory.get(key, {}))
        memory[key] = data
        return result

    monkeypatch.setattr(provider_runtime.store, "_load", load)
    monkeypatch.setattr(provider_runtime.store, "mutate_kv", mutate)
    return memory


def test_every_service_exposes_the_same_state_shape(monkeypatch):
    _memory_store(monkeypatch)
    required = {
        "status", "quota_remaining", "quota_total", "fallback",
        "last_check", "last_success", "last_error",
    }

    assert provider_runtime.states()
    assert all(required <= set(state) for state in provider_runtime.states())


def test_ai_provider_catalog_uses_roles_not_sections():
    assert provider_runtime.SPEC_BY_KEY["gemini"].category == "Основной"
    assert provider_runtime.SPEC_BY_KEY["mistral"].category == "Резерв 2"
    assert provider_runtime.SPEC_BY_KEY["spoonacular"].category == "Питание"


def test_mistral_is_a_configured_ai_reserve(monkeypatch):
    monkeypatch.setattr(provider_runtime.config, "MISTRAL_API_KEY", "secret")

    assert "mistral" in provider_runtime.AI_PROVIDERS
    assert provider_runtime.is_configured("mistral")
    assert provider_runtime.SPEC_BY_KEY["mistral"].fallbacks == ("cloudflare", "openrouter")


def test_cloudflare_probe_uses_real_workers_ai_route(monkeypatch):
    monkeypatch.setattr(service_monitor.config, "CF_ACCOUNT_ID", "account")
    monkeypatch.setattr(service_monitor.config, "CF_API_TOKEN", "token")
    monkeypatch.setattr(service_monitor.config, "CF_MODEL", "@cf/openai/gpt-oss-20b")

    method, url, kwargs = service_monitor._probe_request("cloudflare")

    assert method == "POST"
    assert url.endswith("/ai/run/@cf/openai/gpt-oss-20b")
    assert kwargs["json"]["messages"][0]["content"] == "Reply only OK"


def test_quota_rows_show_remaining_not_usage(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result(
        "openweather", True, quota_remaining=998, quota_total=1000,
    )

    assert service_monitor.format_row("openweather") == (
        "🟢 OpenWeather · Погода · 998/1 000 осталось"
    )


def test_one_remaining_request_is_healthy_when_spoonacular_still_accepts_requests(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("spoonacular", True, quota_remaining=1, quota_total=150)

    assert service_monitor.format_row("spoonacular") == (
        "🟢 Spoonacular · Питание · 1/150 осталось"
    )


def test_successful_spoonacular_request_removes_themealdb_fallback(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("spoonacular", False, status_code=402, error="quota")
    provider_runtime.record_result("themealdb", True)
    assert provider_runtime.activate_fallback("spoonacular", "themealdb")

    provider_runtime.record_result("spoonacular", True, quota_remaining=1, quota_total=49)

    assert service_monitor.format_row("spoonacular") == (
        "🟢 Spoonacular · Питание · 1/49 осталось"
    )


def test_successful_spoonacular_probe_with_remaining_quota_removes_stale_fallback(monkeypatch):
    """Обычный health-probe должен вернуть готовку к первичному источнику."""
    _memory_store(monkeypatch)
    provider_runtime.record_result("spoonacular", False, status_code=402, error="quota")
    provider_runtime.record_result("themealdb", True)
    assert provider_runtime.activate_fallback("spoonacular", "themealdb")

    provider_runtime.record_result(
        "spoonacular", True, quota_remaining=1, quota_total=49,
        allow_quota_recovery=False, record_history=False,
    )

    assert provider_runtime.selected_provider("spoonacular") == "spoonacular"
    assert service_monitor.format_row("spoonacular") == (
        "🟢 Spoonacular · Питание · 1/49 осталось"
    )


def test_exhausted_quota_is_yellow(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("gemini", True, quota_remaining=0, quota_total=20)

    assert service_monitor.format_row("gemini") == (
        "🟡 Gemini · Основной · лимит исчерпан"
    )


def test_unclassified_openrouter_monitor_result_is_neutral(monkeypatch):
    """Непонятный probe не является доказательством поломки последнего резерва."""
    _memory_store(monkeypatch)
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: True)
    provider_runtime.record_result(
        "openrouter", False, status_code=400, error="HTTP 400", record_history=False,
    )

    assert service_monitor.format_row("openrouter") == (
        "⚪ OpenRouter · Резерв 4 · 0 сегодня"
    )


def test_only_a_provider_response_can_mark_a_rate_limit(monkeypatch):
    _memory_store(monkeypatch)

    provider_runtime.record_result("groq", False, error="quota exceeded")
    uncertain = provider_runtime.get_state("groq")
    assert uncertain["error_type"] != "quota"
    assert uncertain["last_error"] != "лимит исчерпан"

    provider_runtime.record_result("groq", False, status_code=429, error="HTTP 429")
    limited = provider_runtime.get_state("groq")
    assert limited["error_type"] == "rate_limit"
    assert limited["last_error"] == "слишком много запросов"


def test_local_groq_counter_is_usage_not_provider_quota(monkeypatch):
    _memory_store(monkeypatch)
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: True)
    monkeypatch.setattr(service_monitor.config, "GROQ_MODEL_DAILY_LIMIT", 5)
    model = "openai/gpt-oss-20b"
    provider_runtime.record_result("groq", True)
    for _ in range(5):
        service_monitor.api_usage.record_request(
            service_monitor.api_usage.groq_model_service(model),
        )

    assert service_monitor.format_row("groq") == (
        "🟢 Groq · Резерв 1 · 5 сегодня"
    )


def test_groq_turns_yellow_only_below_half_of_confirmed_quota(monkeypatch):
    _memory_store(monkeypatch)
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: True)

    provider_runtime.record_result("groq", True, quota_remaining=998, quota_total=1000)
    assert service_monitor.format_row("groq") == (
        "🟢 Groq · Резерв 1 · 998/1 000 осталось"
    )

    provider_runtime.record_result("groq", True, quota_remaining=499, quota_total=1000)
    assert service_monitor.format_row("groq") == (
        "🟡 Groq · Резерв 1 · 499/1 000 осталось"
    )


def test_groq_error_is_not_hidden_by_stale_full_quota(monkeypatch):
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: True)
    state = provider_runtime.blank_state("groq")
    state.update({
        "status": provider_runtime.DOWN,
        "quota_remaining": 1000,
        "quota_total": 1000,
        "error_type": "auth",
        "last_error": "неверный API-ключ",
    })

    assert service_monitor.format_row("groq", state) == (
        "🔴 Groq · Резерв 1 · неверный API-ключ"
    )


def test_ai_warning_shows_reason_and_active_fallback(monkeypatch):
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: True)
    monkeypatch.setattr(
        service_monitor.api_usage, "gemini_state",
        lambda *_args, **_kwargs: {
            "cooldown_remaining": 3600, "cooldown_scope": "RPD",
        },
    )
    state = provider_runtime.blank_state("gemini")
    state.update({
        "status": provider_runtime.WARNING,
        "error_type": "rate_limit",
        "last_error": "основной сервис недоступен",
        "fallback": "openrouter",
    })

    assert service_monitor.format_row("gemini", state) == (
        "🟡 Gemini · Основной · дневной лимит исчерпан → OpenRouter"
    )


def test_successful_ai_probe_clears_expired_rate_limit(monkeypatch):
    _memory_store(monkeypatch)
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: True)

    provider_runtime.record_result(
        "groq", False, status_code=429, error="HTTP 429",
        headers={"Retry-After": "60"}, checked_at=100,
    )
    provider_runtime.record_result(
        "groq", True, quota_remaining=1000, quota_total=1000,
        checked_at=161, allow_quota_recovery=False, record_history=False,
    )

    assert service_monitor.format_row("groq") == (
        "🟢 Groq · Резерв 1 · 1 000/1 000 осталось"
    )


def test_openrouter_balance_is_shown_as_money_not_requests(monkeypatch):
    _memory_store(monkeypatch)
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: True)
    monkeypatch.setattr(
        service_monitor.api_usage, "openrouter_key_usage",
        lambda: {"remaining": 0.999994555, "limit": 1},
    )
    state = provider_runtime.blank_state("openrouter")
    state.update({"status": provider_runtime.OK, "quota_remaining": 1, "quota_total": 1})

    assert service_monitor.format_row("openrouter", state) == (
        "🟢 OpenRouter · Резерв 4 · $1.00 осталось"
    )


def test_gemini_usage_does_not_expose_internal_model_name(monkeypatch):
    _memory_store(monkeypatch)
    monkeypatch.setattr(
        service_monitor.api_usage,
        "gemini_requests",
        lambda _model: {"used": 28},
    )

    row = service_monitor._usage_detail("gemini")

    assert row == "28 сегодня"
    assert "gemini-" not in row


def test_system_rows_use_roles_and_hide_healthy_infrastructure(monkeypatch):
    _memory_store(monkeypatch)

    rows = service_monitor.rows()

    assert rows[0] == "AI"
    assert "Данные" in rows
    assert not any("Telegram" in row for row in rows)
    assert not any("PostgreSQL" in row for row in rows)
    assert not any(row.startswith("🟢 TheMealDB") for row in rows)


def test_active_ai_reserves_are_shown_in_main_rows(monkeypatch):
    _memory_store(monkeypatch)
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: True)
    model = "openai/gpt-oss-20b"
    for _ in range(3):
        service_monitor.api_usage.record_request(
            service_monitor.api_usage.groq_model_service(model),
        )
    service_monitor.api_usage.record_request(
        "cloudflare", units={"neurons": 158}, include_request=False,
    )

    rows = service_monitor.rows()

    assert service_monitor._AI_SERVICES == ("gemini", "groq", "mistral", "cloudflare", "openrouter")
    assert any("Groq" in row for row in rows)
    assert not any("gpt-oss" in row or "qwen" in row for row in rows)
    assert any("Cloudflare AI" in row for row in rows)


def test_themealdb_is_shown_only_after_real_spoonacular_fallback(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("spoonacular", False, error="quota")
    provider_runtime.record_result("themealdb", True)
    assert provider_runtime.activate_fallback("spoonacular", "themealdb")

    rows = service_monitor.rows()

    assert any("Spoonacular" in row and "→ TheMealDB" in row for row in rows)
    assert not any(row.startswith("🟢 TheMealDB") for row in rows)


def test_fallback_is_hidden_until_target_really_succeeds(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("tavily", False, status_code=429)

    assert provider_runtime.activate_fallback("tavily", "firecrawl") is False
    assert "Firecrawl" not in service_monitor.format_row("tavily")

    provider_runtime.record_result("firecrawl", True)
    assert provider_runtime.activate_fallback("tavily", "firecrawl") is True
    assert service_monitor.format_row("tavily").endswith("→ Firecrawl")


def test_fallback_graph_has_no_cycles():
    assert provider_runtime.validate_fallback_graph() == []
    assert "tavily" not in provider_runtime.SPEC_BY_KEY["firecrawl"].fallbacks


def test_success_clears_error_and_disables_fallback(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("tavily", False, error="timeout")
    provider_runtime.record_result("firecrawl", True)
    provider_runtime.activate_fallback("tavily", "firecrawl")

    provider_runtime.record_result("tavily", True)
    state = provider_runtime.get_state("tavily")

    assert state["status"] == provider_runtime.OK
    assert state["last_error"] == ""
    assert state["fallback"] == ""
    assert any("резерв отключён" in event["text"] for event in provider_runtime.history())


def test_failed_active_fallback_is_removed_immediately(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("tavily", False, error="timeout")
    provider_runtime.record_result("firecrawl", True)
    provider_runtime.activate_fallback("tavily", "firecrawl")

    provider_runtime.record_result("firecrawl", False, error="network error")

    state = provider_runtime.get_state("tavily")
    assert state["status"] == provider_runtime.DOWN
    assert state["fallback"] == ""
    assert service_monitor.format_row("tavily").endswith("· резерв недоступен")


def test_background_probe_does_not_select_a_fallback(monkeypatch):
    _memory_store(monkeypatch)
    calls = []

    def fake_probe(service):
        calls.append(service)
        provider_runtime.record_result(service, service == "firecrawl")
        return service == "firecrawl"

    monkeypatch.setattr(service_monitor, "probe", fake_probe)
    monkeypatch.setattr(
        service_monitor, "SPECS",
        (provider_runtime.SPEC_BY_KEY["tavily"], provider_runtime.SPEC_BY_KEY["firecrawl"]),
    )

    service_monitor.check_all(force=True)

    assert calls.count("firecrawl") == 1
    assert provider_runtime.get_state("tavily")["fallback"] == ""


def test_monitor_fallback_is_not_used_by_request_router(monkeypatch):
    _memory_store(monkeypatch)
    provider_runtime.record_result("gemini", False, error="timeout")
    provider_runtime.record_result("groq", True)

    assert provider_runtime.activate_fallback("gemini", "groq", reason="monitor")
    assert provider_runtime.selected_provider("gemini") == "gemini"


def test_passive_language_and_speech_probes_are_not_run_every_five_minutes():
    assert provider_runtime.SPEC_BY_KEY["languagetool"].probe_every >= 3600
    assert provider_runtime.SPEC_BY_KEY["gtts"].probe_every >= 3600
    assert provider_runtime.SPEC_BY_KEY["groq"].probe_every >= 3600
    assert provider_runtime.SPEC_BY_KEY["gemini"].probe_every >= 3600


def test_youtube_search_is_never_a_background_probe(monkeypatch):
    _memory_store(monkeypatch)
    calls = []
    monkeypatch.setattr(service_monitor, "probe", lambda service: calls.append(service) or True)
    monkeypatch.setattr(service_monitor, "SPECS", (provider_runtime.SPEC_BY_KEY["youtube"],))

    service_monitor.check_all(force=True)

    assert calls == []


def test_passive_probe_updates_status_without_polluting_error_log(monkeypatch):
    _memory_store(monkeypatch)

    provider_runtime.record_result(
        "languagetool", False, error="timeout", record_history=False,
    )

    assert provider_runtime.get_state("languagetool")["last_error"] == "сервис не ответил"
    assert provider_runtime.history() == []


def test_monitor_probe_does_not_create_a_developer_error(monkeypatch):
    _memory_store(monkeypatch)
    monkeypatch.setattr(service_monitor, "_configured", lambda _service: False)

    assert service_monitor.probe("gtts") is False
    assert provider_runtime.get_state("gtts")["status"] == provider_runtime.DOWN
    assert provider_runtime.history() == []
