import os

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import ai


@pytest.fixture(autouse=True)
def isolated_ai_store(monkeypatch):
    mem = {}

    def load(key):
        return mem.get(key, {})

    def save(key, data):
        mem[key] = data

    monkeypatch.setattr(ai.store, "_load", load)
    monkeypatch.setattr(ai.store, "_save", save)
    return mem


def test_paid_providers_removed_from_normal_orders():
    for order in (ai.DEFAULT_ORDER, ai.CHAT_ORDER, ai.GRAMMAR_ORDER, ai.LEISURE_ORDER):
        assert "openrouter" not in order
        assert "openai" not in order
    for order in ai.PROVIDER_ORDER.values():
        assert "openrouter" not in order
        assert "openai" not in order


def test_public_plain_text_temporary_error_uses_openrouter(monkeypatch):
    calls = {"fallback": 0}

    def fail_gemini(prompt, max_tokens, temperature):
        raise ai.LLMProviderError("gemini", "gemini 503", status_code=503, temporary=True)

    def fallback(prompt, max_tokens, temperature, origin_provider, reason):
        calls["fallback"] += 1
        return "Короткий нейтральный публичный ответ."

    monkeypatch.setattr(ai, "_gen_gemini", fail_gemini)
    monkeypatch.setattr(ai, "_openrouter_plain_text_fallback", fallback)

    out = ai.llm(
        "Публичная справка о погоде.",
        order=("gemini",),
        fallback_allowed=True,
        privacy_level="public",
        response_mode="plain_text",
    )

    assert out == "Короткий нейтральный публичный ответ."
    assert calls["fallback"] == 1


def test_personal_context_does_not_use_openrouter(monkeypatch):
    calls = {"fallback": 0}

    def fail_gemini(prompt, max_tokens, temperature):
        raise ai.LLMProviderError("gemini", "gemini 503", status_code=503, temporary=True)

    monkeypatch.setattr(ai, "_gen_gemini", fail_gemini)
    monkeypatch.setattr(ai, "_openrouter_plain_text_fallback",
                        lambda *args: calls.__setitem__("fallback", calls["fallback"] + 1))

    with pytest.raises(Exception):
        ai.llm(
            "Личный профиль пользователя.",
            order=("gemini",),
            fallback_allowed=True,
            privacy_level="personal",
            response_mode="plain_text",
        )

    assert calls["fallback"] == 0


def test_json_mode_does_not_use_openrouter(monkeypatch):
    calls = {"fallback": 0}

    def fail_gemini(prompt, max_tokens, temperature):
        raise ai.LLMProviderError("gemini", "gemini 503", status_code=503, temporary=True)

    monkeypatch.setattr(ai, "_gen_gemini", fail_gemini)
    monkeypatch.setattr(ai, "_openrouter_plain_text_fallback",
                        lambda *args: calls.__setitem__("fallback", calls["fallback"] + 1))

    with pytest.raises(Exception):
        ai.llm_json("JSON: {\"ok\": true}", order=("gemini",))

    assert calls["fallback"] == 0


def test_non_temporary_error_does_not_use_openrouter(monkeypatch):
    calls = {"fallback": 0}

    def fail_gemini(prompt, max_tokens, temperature):
        raise ai.LLMProviderError("gemini", "gemini 401", status_code=401, temporary=False)

    monkeypatch.setattr(ai, "_gen_gemini", fail_gemini)
    monkeypatch.setattr(ai, "_openrouter_plain_text_fallback",
                        lambda *args: calls.__setitem__("fallback", calls["fallback"] + 1))

    with pytest.raises(Exception):
        ai.llm(
            "Публичная справка.",
            order=("gemini",),
            fallback_allowed=True,
            privacy_level="public",
            response_mode="plain_text",
        )

    assert calls["fallback"] == 0


def test_openrouter_failure_returns_local_fallback(monkeypatch):
    def fail_gemini(prompt, max_tokens, temperature):
        raise ai.LLMProviderError("gemini", "gemini 503", status_code=503, temporary=True)

    monkeypatch.setattr(ai, "_gen_gemini", fail_gemini)
    monkeypatch.setattr(ai, "_openrouter_plain_text_fallback", lambda *args: None)

    with pytest.raises(Exception) as exc:
        ai.llm(
            "Публичная справка.",
            order=("gemini",),
            fallback_allowed=True,
            privacy_level="public",
            response_mode="plain_text",
        )

    assert str(exc.value) == ai.LOCAL_FALLBACK_TEXT


def test_fallback_stats_do_not_store_prompt(monkeypatch, isolated_ai_store):
    ai._log_openrouter_fallback("gemini", "http_error", True, status_code=503, latency_ms=123)

    rows = isolated_ai_store[ai.OPENROUTER_FALLBACK_STATS_KEY]["log"]
    assert rows[0]["provider"] == "openrouter"
    assert "prompt" not in rows[0]
    assert "response" not in rows[0]
    assert ai.get_openrouter_fallback_stats()["success"] == 1
