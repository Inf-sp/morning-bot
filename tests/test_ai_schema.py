import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import pytest

import ai
import provider_runtime

PHRASES = ai.schema_list("phrases", {"text": "string", "ru": "string", "say": "string"}, ("text", "ru"))


def test_schema_errors_cover_required_types_items_and_numeric_text():
    assert ai.schema_errors({"phrases": [{"text": "Dankie", "ru": "Спасибо"}]}, PHRASES) == []
    assert ai.schema_errors({"phrases": [{"text": "Dankie"}]}, PHRASES) == ["$.phrases[0].ru: нет поля"]
    assert ai.schema_errors({"phrases": "Dankie"}, PHRASES) == ["$.phrases: ожидался array"]
    assert ai.schema_errors({}, PHRASES) == ["$.phrases: нет поля"]
    choice = ai.schema_object({"choice": "integer"}, ("choice",))
    assert ai.schema_errors({"choice": "2"}, choice) == []  # число строкой — разделы приводят его сами
    assert ai.schema_errors({"choice": True}, choice) == ["$.choice: ожидался integer"]
    assert ai.schema_errors({"choice": "два"}, choice) == ["$.choice: ожидался integer"]


@pytest.fixture
def providers(monkeypatch):
    provider_runtime._ai_breaker.clear()
    monkeypatch.setattr(ai, "_cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_a, **_k: None)
    monkeypatch.setattr(ai, "_reorder_for_monitor", lambda order: order)
    monkeypatch.setattr(ai, "_gemini_cooldown_error", lambda: None)
    monkeypatch.setattr(ai, "_reserve_gemini_for_action", lambda: True)
    monkeypatch.setattr(provider_runtime, "is_configured", lambda _p: True)
    monkeypatch.setattr(provider_runtime, "activate_fallback", lambda *_a, **_k: False)
    seen = {}

    def gemini(*_a, **_k):
        seen["gemini_schema"] = ai._schema_for("gemini")
        return '{"phrases": [{"text": "Dankie"}]}'  # нет обязательного «ru» — не по схеме

    def groq(*_a, **_k):
        seen["groq_format"] = ai._openai_json_format("groq")
        return '{"phrases": [{"text": "Dankie", "ru": "Спасибо", "say": "данки"}]}'

    monkeypatch.setattr(ai, "_gen_gemini", gemini)
    monkeypatch.setattr(ai, "_gen_groq", groq)
    yield seen
    provider_runtime._ai_breaker.clear()
    ai._SCHEMA_UNSUPPORTED.clear()


def test_answer_not_matching_schema_goes_to_next_provider_which_gets_the_schema(providers):
    data = ai.llm_json("q", order=("gemini", "groq"), schema=PHRASES, budget_seconds=10)
    assert data["phrases"][0]["ru"] == "Спасибо"
    assert providers["gemini_schema"] == PHRASES
    assert providers["groq_format"] == {"type": "json_schema",
                                        "json_schema": {"name": "answer", "schema": PHRASES, "strict": False}}
    assert ai._AI_SCHEMA.get() is None  # схема не протекает в следующие вызовы


def test_provider_that_rejects_schema_gets_plain_json_from_now_on():
    payload = {"generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": PHRASES}}
    body = 'Invalid JSON payload received. Unknown name "responseJsonSchema" at \'generation_config\''
    try:
        plain = ai._without_schema("gemini", 400, body, payload)
        assert plain == {"generationConfig": {"responseMimeType": "application/json"}}
        assert "gemini" in ai._SCHEMA_UNSUPPORTED
        token = ai._AI_SCHEMA.set(PHRASES)
        try:
            assert ai._schema_for("gemini") is None and ai._schema_for("groq") == PHRASES
        finally:
            ai._AI_SCHEMA.reset(token)
        groq = {"response_format": {"type": "json_schema", "json_schema": {}}}
        assert ai._without_schema("groq", 400, "json_schema is not supported", groq) == {
            "response_format": {"type": "json_object"}}
        # Сбой генерации (модель не уложилась в JSON) — не отказ от схемы.
        assert ai._without_schema("cerebras", 400, "Failed to validate JSON against schema", groq) is None
        assert ai._without_schema("cerebras", 500, "schema", groq) is None
    finally:
        ai._SCHEMA_UNSUPPORTED.clear()


def test_real_payloads_carry_the_schema(monkeypatch):
    sent = []

    class Resp:
        headers = {}

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}],
                    "choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(ai, "_post", lambda url, headers, payload, *_a, **_k: sent.append(payload) or Resp())
    monkeypatch.setattr(ai, "_gemini_cooldown_error", lambda: None)
    monkeypatch.setattr(ai.api_usage, "seconds_until_gemini_slot", lambda **_k: 0)
    monkeypatch.setattr(ai.api_usage, "record_request", lambda *_a, **_k: None)
    monkeypatch.setattr(ai.config, "GROQ_API_KEY", "k")
    token = ai._AI_SCHEMA.set(PHRASES)
    try:
        ai._gen_gemini("q", 100, 0.5, "json")
        ai._gen_groq("q", 100, 0.5, "json")
        ai._gen_gemini("q", 100, 0.5, "plain_text")  # не JSON-режим — без схемы
    finally:
        ai._AI_SCHEMA.reset(token)
    assert sent[0]["generationConfig"]["responseJsonSchema"] == PHRASES
    assert sent[1]["response_format"]["type"] == "json_schema"
    assert "responseJsonSchema" not in sent[2]["generationConfig"]
