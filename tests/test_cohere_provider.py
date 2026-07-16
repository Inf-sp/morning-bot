import os

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
            "finish_reason": "COMPLETE",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": self._text}],
            },
            "usage": {
                "billed_units": {"input_tokens": 5, "output_tokens": 8},
            },
        }


def test_cohere_v2_chat_payload_and_json_mode(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai.config, "COHERE_API_KEY", "cohere-secret-key")
    monkeypatch.setattr(ai.config, "COHERE_MODEL", "command-a-plus-05-2026")

    def fake_post(url, headers, payload, timeout, name):
        captured.update({
            "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "name": name,
        })
        return FakeResponse('{"ok":true}')

    monkeypatch.setattr(ai, "_post", fake_post)
    result = ai._gen_cohere("Верни JSON", 500, 0.2, response_mode="json")

    assert result == '{"ok":true}'
    assert captured["url"] == "https://api.cohere.com/v2/chat"
    assert captured["headers"]["Authorization"] == "Bearer cohere-secret-key"
    assert captured["payload"]["model"] == "command-a-plus-05-2026"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "Верни JSON"}]
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_tokens"] == 500
    assert captured["timeout"] == 30
    assert captured["name"] == "cohere"


def test_cohere_plain_text_does_not_force_json(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai.config, "COHERE_API_KEY", "cohere-secret-key")

    def fake_post(_url, _headers, payload, _timeout, _name):
        captured["payload"] = payload
        return FakeResponse("Короткий ответ")

    monkeypatch.setattr(ai, "_post", fake_post)

    assert ai._gen_cohere("Ответь кратко", 100, 0.4) == "Короткий ответ"
    assert "response_format" not in captured["payload"]


def test_cohere_preserves_zero_temperature(monkeypatch):
    captured = {}
    monkeypatch.setattr(ai.config, "COHERE_API_KEY", "cohere-secret-key")

    def fake_post(_url, _headers, payload, _timeout, _name):
        captured["payload"] = payload
        return FakeResponse("Точный ответ")

    monkeypatch.setattr(ai, "_post", fake_post)

    assert ai._gen_cohere("Ответь точно", 100, 0.0) == "Точный ответ"
    assert captured["payload"]["temperature"] == 0.0


def test_module_routing_splits_cohere_and_gemini_categories():
    assert ai._resolve(None, None, module="learning")[0] == "cohere"
    assert ai._resolve(None, None, module="learning_dict_add")[0] == "cohere"
    assert ai._resolve(None, None, module="learning_game")[0] == "cohere"
    assert ai._resolve(None, None, module="thoughts")[0] == "cohere"
    assert ai._resolve(None, None, module="health")[0] == "cohere"
    assert ai._resolve(None, None, module="food") == (
        "gemini", "groq", "github_models", "openrouter",
    )
    assert ai._resolve("cheap", None, module="recipe_generation") == (
        "gemini", "groq", "github_models", "openrouter",
    )
    assert ai._resolve(None, None, module="wardrobe")[:2] == ("gemini", "cohere")
    assert ai._resolve(None, None, module="leisure")[:2] == ("gemini", "cohere")
    assert ai._resolve("cheap", None, module="leisure_movies")[:2] == ("gemini", "cohere")
    assert ai._resolve("cheap", None)[0] == "cohere"


def test_missing_cohere_key_falls_back_to_gemini(monkeypatch):
    calls = []
    monkeypatch.setattr(ai.config, "COHERE_API_KEY", "")
    monkeypatch.setattr(ai, "_gen_gemini", lambda *_args, **_kwargs: calls.append("gemini") or "готово")
    monkeypatch.setattr(ai, "_cache_get", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _name: None)

    result = ai.llm("Классифицируй", module="learning")

    assert result == "готово"
    assert calls == ["gemini"]


def test_cohere_key_is_redacted_from_logs(monkeypatch):
    monkeypatch.setattr(ai.config, "COHERE_API_KEY", "cohere-secret-key-123")

    assert "cohere-secret-key-123" not in secure.redact("token=cohere-secret-key-123")
