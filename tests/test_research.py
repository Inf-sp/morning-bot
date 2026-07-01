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
def test_country_facts_uses_configured_restcountries_base(monkeypatch):
    research._CF_CACHE.clear()
    urls = []
    monkeypatch.setattr(research.config, "RESTCOUNTRIES_BASE_URL", "https://rest.test/v3.1/")

    def fake(url, *a, **k):
        urls.append(url)
        return _Resp(200, _DE)

    monkeypatch.setattr(research.requests, "get", fake)
    assert research.country_facts("Германия")["cc"] == "DE"
    assert urls == ["https://rest.test/v3.1/alpha/DE"]


@pytest.mark.unit
def test_country_facts_sends_optional_api_key(monkeypatch):
    research._CF_CACHE.clear()
    seen = {}
    monkeypatch.setattr(research.config, "RESTCOUNTRIES_API_KEY", "secret")

    def fake(url, *a, **k):
        seen["headers"] = k.get("headers")
        return _Resp(200, _DE)

    monkeypatch.setattr(research.requests, "get", fake)
    assert research.country_facts("Германия")["capital"] == "Berlin"
    assert seen["headers"] == {"Authorization": "Bearer secret", "X-API-Key": "secret"}


@pytest.mark.unit
def test_country_facts_falls_back_to_translation(monkeypatch):
    research._CF_CACHE.clear()
    urls = []

    def fake(url, *a, **k):
        urls.append(url)
        if "/translation/" in url:
            return _Resp(200, _DE)
        return _Resp(404, {})

    monkeypatch.setattr(research.requests, "get", fake)
    assert research.country_facts("Deutschland")["capital"] == "Berlin"
    assert any("/translation/Deutschland" in url for url in urls)


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


@pytest.mark.unit
def test_serpapi_search_normalizes_results(monkeypatch):
    class Resp:
        def json(self):
            return {"organic_results": [
                {"title": "Songkick", "link": "https://songkick.com/a", "snippet": "tour dates"},
                {"title": "No link"},
            ]}

    monkeypatch.setattr(research.config, "SERPAPI_API_KEY", "key")
    research._SERP_CACHE.clear()
    monkeypatch.setattr(research.requests, "get", lambda *a, **k: Resp())

    assert research.serpapi_search("artist concerts") == [
        {"title": "Songkick", "url": "https://songkick.com/a", "content": "tour dates"}
    ]
