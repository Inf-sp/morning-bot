"""fetch_weather кеширует ответ в пределах TTL - второй вызов не ходит в сеть."""
import pytest

pytest.importorskip("requests")
pytest.importorskip("telegram")   # weather.py импортирует telegram
import weather


class _FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


@pytest.mark.integration
def test_fetch_weather_cached(monkeypatch):
    calls = {"n": 0}

    def fake_get(*a, **k):
        calls["n"] += 1
        return _FakeResp()

    monkeypatch.setattr(weather.requests, "get", fake_get)
    weather._WX_CACHE.clear()

    a = weather.fetch_weather(52.37, 4.90, 2)
    b = weather.fetch_weather(52.37, 4.90, 2)   # тот же ключ -> из кеша
    assert a == b == {"ok": True}
    assert calls["n"] == 1, "второй вызов должен браться из кеша"


@pytest.mark.integration
def test_fetch_weather_expired(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(weather.requests, "get",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or _FakeResp()))
    monkeypatch.setattr(weather, "_WX_TTL", -1)   # кеш всегда протухший
    weather._WX_CACHE.clear()

    weather.fetch_weather(0, 0, 2)
    weather.fetch_weather(0, 0, 2)
    assert calls["n"] == 2, "при протухшем TTL должны быть два сетевых вызова"
