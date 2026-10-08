import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import pytest

import ai
import provider_runtime


class _NoStore:
    def mutate_kv(self, *_a, **_k):
        return None

    def _load(self, *_a, **_k):
        return None


@pytest.fixture
def providers(monkeypatch):
    """Настоящая цепочка ai.llm; провайдеры подменены, возвращает (calls, behaviour)."""
    calls, behaviour = [], {}
    provider_runtime._ai_breaker.clear()
    monkeypatch.setattr(ai, "_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_gemini_cooldown_error", lambda: None)
    monkeypatch.setattr(ai, "_reserve_gemini_for_action", lambda: True)
    monkeypatch.setattr(provider_runtime, "is_configured", lambda _p: True)
    monkeypatch.setattr(provider_runtime, "activate_fallback", lambda *_a, **_k: False)
    monkeypatch.setattr(provider_runtime, "store", _NoStore())

    def fake(name):
        def run(*_a, **_k):
            calls.append(name)
            result = behaviour.get(name, "ok")
            if isinstance(result, Exception):
                raise result
            return result
        return run

    monkeypatch.setattr(ai, "_gen_gemini", fake("gemini"))
    monkeypatch.setattr(ai, "_gen_cerebras", fake("cerebras"))
    monkeypatch.setattr(ai, "_gen_groq", fake("groq"))
    monkeypatch.setattr(ai, "_gen_cf", fake("cf"))
    monkeypatch.setattr(ai, "_openrouter_plain_text_fallback", fake("openrouter"))
    yield calls, behaviour
    provider_runtime._ai_breaker.clear()


def _error(name, error_type):
    return ai.LLMProviderError(name, f"{name} {error_type}", temporary=True, error_type=error_type)


def test_default_order_is_gemini_groq_cf_openrouter():
    assert ai.AI_ORDER == ("gemini", "cerebras", "groq", "cf", "openrouter")


def test_each_failed_provider_hands_over_to_the_next_in_order(providers):
    calls, behaviour = providers
    behaviour.update({
        "gemini": _error("gemini", "ReadTimeout"),
        "cerebras": _error("cerebras", "http_error"),
        "groq": _error("groq", "rate_limit"),
        "cf": _error("cf", "temporary"),
        "openrouter": "резерв",
    })

    assert ai.llm("q", module="food", fallback_allowed=True, budget_seconds=30) == "резерв"
    assert calls == ["gemini", "cerebras", "groq", "cf", "openrouter"]


def test_first_working_provider_answers_and_later_ones_are_not_called(providers):
    calls, behaviour = providers
    behaviour["gemini"] = _error("gemini", "rate_limit")

    assert ai.llm("q", module="food", fallback_allowed=True, budget_seconds=30) == "ok"
    assert calls == ["gemini", "cerebras"]


def test_empty_answer_moves_to_the_next_provider(providers):
    calls, behaviour = providers
    behaviour["gemini"] = ""

    assert ai.llm("q", module="food", budget_seconds=30) == "ok"
    assert calls == ["gemini", "cerebras"]


def test_unconfigured_provider_is_skipped_without_a_call(providers, monkeypatch):
    calls, _behaviour = providers
    monkeypatch.setattr(provider_runtime, "is_configured", lambda p: p != "gemini")

    assert ai.llm("q", module="food", budget_seconds=30) == "ok"
    assert calls == ["cerebras"]


def test_all_providers_down_raises_after_trying_every_one(providers):
    calls, behaviour = providers
    for name in ("gemini", "cerebras", "groq", "cf"):
        behaviour[name] = _error(name, "temporary")
    behaviour["openrouter"] = ""

    with pytest.raises(Exception):
        ai.llm("q", module="food", fallback_allowed=True, budget_seconds=30)
    assert calls == ["gemini", "cerebras", "groq", "cf", "openrouter"]


def test_legacy_unknown_status_text_is_not_shown_in_admin(monkeypatch):
    monkeypatch.setattr(provider_runtime, "load_state", lambda: {"services": {
        "groq": {"status": "warning", "last_error": "не удалось определить статус"},
    }})

    assert provider_runtime.get_state("groq")["last_error"] == "сервис не ответил"
    assert all(state.get("last_error") != "не удалось определить статус"
               for state in provider_runtime.states())
    assert provider_runtime._friendly_error("boom", 418, "groq")[1] == "ошибка запроса"
    assert provider_runtime._friendly_error("boom", None, "groq")[1] == "сервис не ответил"


class _Response:
    def __init__(self, status, text="{}"):
        self.status_code, self.text, self.headers = status, text, {}

    def json(self):
        return {}


def _fake_post(monkeypatch, gone):
    calls = []

    def post(url, json=None, **_kwargs):
        calls.append((url, json))
        missing = gone in url or (json or {}).get("model") == gone
        return _Response(404, '{"error":{"code":"model_not_found"}}') if missing else _Response(200)

    monkeypatch.setattr(ai, "_MODEL_OVERRIDES", {})
    monkeypatch.setattr(ai.requests, "post", post)
    monkeypatch.setattr(ai.api_usage, "record_request", lambda *_a, **_k: None)
    monkeypatch.setattr(ai.api_usage, "gemini_requests", lambda *_a, **_k: None)
    return calls


def test_gemini_404_switches_to_latest_alias_with_new_thinking_format(monkeypatch):
    calls = _fake_post(monkeypatch, "gemini-2.5-flash")
    payload = {"generationConfig": {"thinkingConfig": {"thinkingBudget": 0}}}

    response = ai._post("https://g/v1beta/models/gemini-2.5-flash:generateContent", {}, payload, 5, "gemini")

    assert response.status_code == 200
    url, sent = calls[-1]
    assert url == "https://g/v1beta/models/gemini-flash-latest:generateContent"
    assert sent["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert ai._resolve_model("gemini-2.5-flash") == "gemini-flash-latest"


def test_groq_missing_model_switches_to_stable_fallback(monkeypatch):
    calls = _fake_post(monkeypatch, "qwen/qwen3.6-27b")

    response = ai._post("https://api.groq.com/x", {}, {"model": "qwen/qwen3.6-27b"}, 5, "groq_standard")

    assert response.status_code == 200
    assert calls[-1][1]["model"] == "openai/gpt-oss-120b"
    assert ai._resolve_model("qwen/qwen3.6-27b") == "openai/gpt-oss-120b"


def test_gemini_thinking_format_matches_model_generation():
    assert ai._gemini_thinking("gemini-2.5-flash") == {"thinkingBudget": 0}
    assert ai._gemini_thinking("gemini-3.8-flash") == {"thinkingLevel": "low"}


def test_cerebras_missing_model_switches_to_fallback(monkeypatch):
    calls = _fake_post(monkeypatch, "gpt-oss-120b")

    response = ai._post("https://api.cerebras.ai/v1/chat/completions", {}, {"model": "gpt-oss-120b"}, 5, "cerebras")

    assert response.status_code == 200
    assert calls[-1][1]["model"] == "llama3.1-8b"


def test_cerebras_key_is_redacted_and_provider_registered(monkeypatch):
    monkeypatch.setattr(ai.config, "CEREBRAS_API_KEY", "csk-secret-123")
    assert "csk-secret-123" not in ai.secure.redact("auth csk-secret-123")
    assert "cerebras" in provider_runtime.AI_PROVIDERS
    assert provider_runtime.is_configured("cerebras")
