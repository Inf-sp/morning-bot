import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import ai
import provider_runtime
import service_monitor


def test_json_parser_accepts_fenced_json():
    raw = """```json
    {"ok": true, "value": 3}
    ```"""

    assert ai._parse_json_response(raw) == {"ok": True, "value": 3}


def test_restcountries_403_is_not_forced_into_auth(monkeypatch):
    kind, message = provider_runtime._friendly_error(
        "HTTP 403", status_code=403, provider="restcountries"
    )

    assert kind == "access_denied"
    assert message == "доступ запрещён"


def test_restcountries_probe_uses_public_search_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def json(self):
            return {"data": {"objects": []}}

    monkeypatch.setattr(service_monitor.config, "RESTCOUNTRIES_API_KEY", "restcountries-secret")
    monkeypatch.setattr(service_monitor.requests, "request", lambda method, url, **kwargs: captured.update({
        "method": method, "url": url, "kwargs": kwargs,
    }) or FakeResponse())
    monkeypatch.setattr(service_monitor.provider_runtime, "record_result", lambda *args, **kwargs: None)

    service_monitor.probe("restcountries")

    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.restcountries.com/countries/v5"
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer restcountries-secret"
    assert captured["kwargs"]["params"] == {"q": "Netherlands", "limit": 1}

