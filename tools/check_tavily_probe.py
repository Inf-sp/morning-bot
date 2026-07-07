import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-secret")

import requests

import admin
import config
from ui import admin as admin_ui


class _Resp:
    def __init__(self, status_code, reason="error"):
        self.status_code = status_code
        self.reason = reason
        self.text = "raw body with api_key=test-tavily-secret"


def _tavily_row():
    rows = admin._external_api_probe_results()
    return next(row for row in rows if row[0] == "Tavily")


def _render_tavily(row):
    return admin_ui.api_check([row]).text


def test_401():
    requests.post = lambda *a, **kw: _Resp(401, "Unauthorized")
    text = _render_tavily(_tavily_row())
    assert "🔴 Tavily: ключ отсутствует, неверный или отозван" in text
    print("ok: Tavily 401 is friendly and non-LLM")


def test_429():
    requests.post = lambda *a, **kw: _Resp(429, "Too Many Requests")
    text = _render_tavily(_tavily_row())
    assert "🟠 Tavily: лимит запросов исчерпан" in text
    print("ok: Tavily 429 is orange rate limit")


def test_timeout():
    def _timeout(*args, **kwargs):
        raise requests.exceptions.Timeout("Read timed out")

    requests.post = _timeout
    text = _render_tavily(_tavily_row())
    assert "🟠 Tavily: сервис временно не ответил" in text
    print("ok: Tavily timeout is orange temporary failure")


def test_generic_safe():
    requests.post = lambda *a, **kw: _Resp(500, "Server error")
    text = _render_tavily(_tavily_row())
    assert "HTTP 500" in text
    assert "HTTPError: Server error" in text
    assert "endpoint: https://api.tavily.com/search" in text
    assert "configured: yes" in text
    assert config.TAVILY_API_KEY not in text
    assert "api_key=" not in text
    assert "raw body" not in text
    assert "LLM:" not in text
    print("ok: Tavily generic error is safe")


if __name__ == "__main__":
    test_401()
    test_429()
    test_timeout()
    test_generic_safe()
    print("ok: Tavily admin probe diagnostics")
