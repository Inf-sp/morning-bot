import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import requests

import http_pool
import routing


def test_routing_check_reports_buttons_without_handler(monkeypatch, capsys):
    assert routing.unhandled_buttons() == []  # все кнопки проекта кто-то обрабатывает
    monkeypatch.setattr(routing, "button_callbacks", lambda: [("zz_nobody_handles", "x.py:1", False),
                                                              ("a_concerts_", "y.py:2", True), ("noop", "z.py:3", False)])
    assert routing.main() == 1
    out = capsys.readouterr().out
    assert "✗ zz_nobody_handles — x.py:1" in out and "a_concerts_" not in out  # f-строка с живым обработчиком


def test_pool_sends_all_requests_through_one_session_without_cookies(monkeypatch):
    seen = []
    try:
        http_pool.install()
        http_pool.install()  # повторно — без двойной обёртки
        shared = http_pool.session()
        monkeypatch.setattr(shared, "request", lambda method, url, **kw: seen.append((method, url, kw)) or "resp")
        assert requests.get("https://example.invalid/a", timeout=3) == "resp"
        assert requests.post("https://example.invalid/b", json={}, timeout=3) == "resp"
        assert requests.request("put", "https://example.invalid/c") == "resp"
        assert [(m.lower(), u) for m, u, _ in seen] == [
            ("get", "https://example.invalid/a"), ("post", "https://example.invalid/b"), ("put", "https://example.invalid/c")]
        assert seen[0][2]["timeout"] == 3
        assert tuple(shared.cookies.get_policy().allowed_domains()) == ()  # cookie не копятся между API
        assert shared.get_adapter("https://x").poolmanager.connection_pool_kw["maxsize"] == http_pool.POOL_PER_HOST
    finally:
        http_pool.uninstall()
    assert requests.api.request is not http_pool._pooled_request
