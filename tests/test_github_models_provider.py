import os

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import ai
import secure


class FakeResponse:
    headers = {}

    def __init__(self, text):
        self._text = text

    def json(self):
        return {
            "choices": [{
                "message": {"role": "assistant", "content": self._text},
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 8},
        }


class _ErrorResponse:
    status_code = 400
    headers = {}
    text = '{"error":{"message":"Failed to validate JSON. See failed_generation"}}'


def test_github_models_chat_payload_and_json_mode(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai.config, "GITHUB_MODELS_TOKEN", "github-models-secret")
    monkeypatch.setattr(ai.config, "GITHUB_MODELS_MODEL", "openai/gpt-4.1-mini")

    def fake_post(url, headers, payload, timeout, name, **_kwargs):
        captured.update({
            "url": url,
            "headers": headers,
            "payload": payload,
            "timeout": timeout,
            "name": name,
        })
        return FakeResponse('{"ok":true}')

    monkeypatch.setattr(ai, "_post", fake_post)

    result = ai._gen_github_models("Верни JSON", 500, 0.0, response_mode="json")

    assert result == '{"ok":true}'
    assert captured["url"] == "https://models.github.ai/inference/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer github-models-secret"
    assert captured["headers"]["Accept"] == "application/vnd.github+json"
    assert captured["headers"]["X-GitHub-Api-Version"] == "2026-03-10"
    assert captured["payload"]["model"] == "openai/gpt-4.1-mini"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "Верни JSON"}]
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["temperature"] == 0.0
    assert captured["payload"]["max_tokens"] == 500
    assert captured["timeout"] == 30
    assert captured["name"] == "github_models"


def test_groq_retries_json_validation_failure_without_json_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(ai.config, "GROQ_API_KEY", "groq-secret")

    def fake_post(_url, _headers, payload, _timeout, name, **_kwargs):
        calls.append(payload)
        if payload.get("response_format"):
            raise ai.LLMProviderError(
                name, "Failed to validate JSON. See failed_generation",
                status_code=400, error_type="http_error",
            )
        return FakeResponse('{"ok":true}')

    monkeypatch.setattr(ai, "_post", fake_post)

    result = ai._gen_groq("Верни JSON", 500, 0.0, response_mode="json", provider="groq_standard")

    assert result == '{"ok":true}'
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]


def test_groq_json_validation_rejection_does_not_create_service_incident(monkeypatch):
    usage_calls = []
    monkeypatch.setattr(ai.requests, "post", lambda *_args, **_kwargs: _ErrorResponse())
    monkeypatch.setattr(
        ai.api_usage, "record_request",
        lambda service, ok=True, **kwargs: usage_calls.append((service, ok, kwargs)),
    )

    with pytest.raises(ai.LLMProviderError):
        ai._post(
            "https://example.test", {}, {}, 1, "groq_standard",
            suppress_json_validation_failure=True,
        )

    assert usage_calls == [
        ("groq", False, {
            "status_code": 400,
            "error": "HTTP 400",
            "latency_ms": pytest.approx(0, abs=1000),
            "headers": {},
            "monitor_result": False,
        }),
    ]


def test_model_tiers_use_the_requested_provider_chain():
    assert ai._resolve(None, None, module="learning") == (
        "groq_standard", "github_models", "cf", "openrouter",
    )
    assert ai._resolve(None, None, module="food") == (
        "gemini", "groq_complex", "openrouter",
    )
    assert ai.CHAT_ORDER == ("groq_standard", "github_models", "cf", "openrouter")


def test_food_tries_openrouter_after_three_unavailable_providers(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_reorder_for_cooldown", lambda order: order)

    def unavailable(name):
        def fail(*_args, **_kwargs):
            calls.append(name)
            raise RuntimeError(f"{name} unavailable")
        return fail

    monkeypatch.setattr(ai, "_gen_gemini", unavailable("gemini"))
    monkeypatch.setattr(ai, "_gen_groq", unavailable("groq"))
    monkeypatch.setattr(
        ai, "_openrouter_plain_text_fallback",
        lambda *_args, **_kwargs: calls.append("openrouter") or '{"ok":true}',
    )

    result = ai.llm_json(
        "Верни результат", module="food", fallback_allowed=True,
        privacy_level="personal", allow_personal_openrouter=True,
    )

    assert result == {"ok": True}
    assert calls == ["gemini", "groq", "openrouter"]


def test_github_models_supports_chat_history(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai.config, "GITHUB_MODELS_TOKEN", "github-models-secret")

    def fake_post(url, headers, payload, timeout, name, **_kwargs):
        captured.update({
            "url": url,
            "headers": headers,
            "payload": payload,
            "timeout": timeout,
            "name": name,
        })
        return FakeResponse("Ответ с учётом истории")

    monkeypatch.setattr(ai, "_post", fake_post)
    history = [
        {"role": "user", "content": "Привет"},
        {"role": "assistant", "content": "Привет!"},
        {"role": "user", "content": "Продолжим"},
    ]

    result = ai._chat("github_models", history, "Системная инструкция")

    assert result == "Ответ с учётом истории"
    assert captured["payload"]["messages"] == [
        {"role": "system", "content": "Системная инструкция"},
        *history,
    ]
    assert captured["name"] == "github_models"


def test_missing_github_models_token_falls_back_to_gemini(monkeypatch):
    calls = []
    monkeypatch.setattr(ai.config, "GITHUB_MODELS_TOKEN", "")
    monkeypatch.setattr(
        ai, "_gen_gemini", lambda *_args, **_kwargs: calls.append("gemini") or "готово",
    )
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)

    result = ai.llm("Ответь", order=("github_models", "gemini"))

    assert result == "готово"
    assert calls == ["gemini"]


def test_github_models_token_is_redacted(monkeypatch):
    monkeypatch.setattr(
        ai.config, "GITHUB_MODELS_TOKEN", "github-models-secret-token-123",
    )

    message = secure.redact("token=github-models-secret-token-123")

    assert "github-models-secret-token-123" not in message
