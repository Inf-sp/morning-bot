import asyncio
import os

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import ai


class _Response:
    def __init__(self, lines, *, headers=None):
        self._lines = list(lines)
        self.headers = headers or {}
        self.closed = False
        self.decode_unicode = None

    def iter_lines(self, decode_unicode=True):
        self.decode_unicode = decode_unicode
        return iter(self._lines)

    def close(self):
        self.closed = True


def test_sse_parser_collects_content_and_accepts_done_marker():
    response = _Response([
        ": keepalive",
        'data: {"choices":[{"delta":{"content":"Привет"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ])

    assert list(ai._iter_sse_deltas(response, "groq_standard")) == ["Привет", "!"]


def test_sse_parser_decodes_utf8_bytes_without_relying_on_http_charset():
    response = _Response([
        'data: {"choices":[{"delta":{"content":"Привет"},"finish_reason":"stop"}]}'.encode("utf-8"),
        b"data: [DONE]",
    ])

    assert list(ai._iter_sse_deltas(response, "groq_standard")) == ["Привет"]
    assert response.decode_unicode is False


def test_sse_parser_rejects_incomplete_or_error_event():
    with pytest.raises(ai.LLMProviderError, match="before completion"):
        list(ai._iter_sse_deltas(_Response([
            'data: {"choices":[{"delta":{"content":"часть"}}]}',
        ]), "groq_standard"))

    with pytest.raises(ai.LLMProviderError, match="overloaded"):
        list(ai._iter_sse_deltas(_Response([
            'data: {"error":{"message":"overloaded"}}',
        ]), "groq_standard"))


def test_chat_stream_sends_openai_sse_payload_for_groq(monkeypatch):
    captured = {}
    emitted = []
    monkeypatch.setattr(ai.config, "GROQ_API_KEY", "secret")

    def stream(url, headers, payload, timeout, provider, emit, **kwargs):
        captured.update({
            "url": url, "headers": headers, "payload": payload,
            "timeout": timeout, "provider": provider, **kwargs,
        })
        emit("готово")
        return "готово"

    monkeypatch.setattr(ai, "_stream_openai_chat", stream)
    result = ai._chat_stream(
        ai.GROQ_STANDARD,
        [{"role": "user", "content": "Привет"}],
        "system",
        emitted.append,
        timeout_cap=2,
    )

    assert result == "готово"
    assert emitted == ["готово"]
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Accept"] == "text/event-stream"
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["model"] == ai.config.GROQ_STANDARD_MODEL
    assert captured["usage_service"] == ai.api_usage.groq_model_service(ai.config.GROQ_STANDARD_MODEL)


def test_stream_route_falls_back_before_first_delta(monkeypatch):
    calls = []
    deltas = []
    monkeypatch.setattr(ai, "CHAT_ORDER", (ai.GROQ_STANDARD, "github_models"))
    monkeypatch.setattr(ai, "_record_ai_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai, "_log_cost", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai.provider_runtime, "activate_fallback", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai, "_log_free_chat_route", lambda **kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _provider: None)
    monkeypatch.setattr(ai, "_mark_cooldown", lambda *args, **kwargs: None)

    def stream(provider, _history, _system, emit, **_kwargs):
        calls.append(provider)
        if provider == ai.GROQ_STANDARD:
            raise ai.LLMProviderError(provider, "timeout", temporary=True)
        emit("резерв ответил")
        return "резерв ответил"

    monkeypatch.setattr(ai, "_chat_stream", stream)

    assert ai._chat_chain_stream_impl([], emit=deltas.append) == "резерв ответил"
    assert calls == [ai.GROQ_STANDARD, "github_models"]
    assert deltas == ["резерв ответил"]


def test_stream_route_does_not_mix_providers_after_visible_delta(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "CHAT_ORDER", (ai.GROQ_STANDARD, "github_models"))
    monkeypatch.setattr(ai, "_record_ai_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai, "_log_free_chat_route", lambda **kwargs: None)
    monkeypatch.setattr(ai, "_provider_is_unavailable", lambda _provider: None)
    monkeypatch.setattr(ai, "_mark_cooldown", lambda *args, **kwargs: None)

    def stream(provider, _history, _system, emit, **_kwargs):
        calls.append(provider)
        emit("Начало ответа")
        raise ai._PartialStreamError(
            ai.LLMProviderError(provider, "socket closed", temporary=True),
            "Начало ответа",
        )

    monkeypatch.setattr(ai, "_chat_stream", stream)

    with pytest.raises(ai.StreamOutputInterrupted):
        ai._chat_chain_stream_impl([], emit=lambda _delta: None)
    assert calls == [ai.GROQ_STANDARD]


def test_stream_transport_accounts_once_and_closes_response(monkeypatch):
    response = _Response([
        'data: {"choices":[{"delta":{"content":"Да"},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ], headers={"x-test": "1"})
    recorded = []

    def post(*_args, **_kwargs):
        def record(ok, **kwargs):
            recorded.append((ok, kwargs))
        return response, record

    monkeypatch.setattr(ai, "_stream_post", post)
    out = ai._stream_openai_chat("url", {}, {}, 1, "groq_standard", lambda _delta: None)

    assert out == "Да"
    assert recorded == [(True, {"headers_": {"x-test": "1"}})]
    assert response.closed is True


def test_async_stream_bridge_keeps_delta_order(monkeypatch):
    def stream(_history, _cid, emit=None, budget_seconds=None):
        emit("первая ")
        emit("вторая")
        return "первая вторая"

    monkeypatch.setattr(ai, "chat_chain_stream", stream)
    seen = []

    async def on_delta(delta):
        seen.append(delta)

    async def consume():
        return await ai.achat_chain_stream(
            [], "42", on_delta=on_delta, budget_seconds=2,
        )

    assert asyncio.run(consume()) == "первая вторая"
    assert seen == ["первая ", "вторая"]
