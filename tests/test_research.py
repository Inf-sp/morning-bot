"""Research-first слой: country_facts (REST Countries), wiki_fact, facts_block/grounded, кеш."""
import pytest

pytest.importorskip("requests")
import research


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


_DE = [{"cca2": "DE", "capital": ["Berlin"], "languages": {"deu": "German"},
        "region": "Europe", "currencies": {"EUR": {"name": "Euro"}}}]


@pytest.mark.integration
def test_country_facts_parses(monkeypatch):
    research._CF_CACHE.clear()
    monkeypatch.setattr(research.requests, "get", lambda *a, **k: _Resp(200, _DE))
    d = research.country_facts("Германия")
    assert d["cc"] == "DE"
    assert d["capital"] == "Berlin"
    assert d["languages"] == ["German"]
    assert d["region"] == "Europe"
    assert d["currency"] == "EUR"


@pytest.mark.integration
def test_country_facts_error_returns_empty(monkeypatch):
    research._CF_CACHE.clear()
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(research.requests, "get", boom)
    assert research.country_facts("Германия") == {}


@pytest.mark.integration
def test_country_facts_cached(monkeypatch):
    research._CF_CACHE.clear()
    calls = {"n": 0}
    def fake(*a, **k):
        calls["n"] += 1
        return _Resp(200, _DE)
    monkeypatch.setattr(research.requests, "get", fake)
    research.country_facts("Германия")
    research.country_facts("Германия")
    assert calls["n"] == 1, "второй вызов должен браться из кеша"


@pytest.mark.unit
def test_wiki_fact_picks_sentence(monkeypatch):
    long = ("Берлин - столица Германии и один из крупнейших городов Европы. "
            "Город известен своей историей и культурой на протяжении веков.")
    monkeypatch.setattr(research, "wiki_summary", lambda title, lang: long if lang == "ru" else "")
    out = research.wiki_fact("Берлин")
    assert out and out.endswith(".")


@pytest.mark.unit
def test_wiki_fact_empty(monkeypatch):
    monkeypatch.setattr(research, "wiki_summary", lambda *a, **k: "")
    monkeypatch.setattr(research, "_wiki_ru_title", lambda *a, **k: "")
    assert research.wiki_fact("Нету") == ""


@pytest.mark.unit
def test_facts_block_and_grounded():
    d = {"cc": "DE", "capital": "Berlin", "languages": ["German"], "region": "Europe", "currency": "EUR"}
    block = research.facts_block(d)
    assert "столица: Berlin" in block and "валюта: EUR" in block
    assert research.grounded(d) is True
    assert research.grounded({}) is False
