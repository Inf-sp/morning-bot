import os

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import ai
import config
import country_catalog
import research


def test_json_parser_accepts_fenced_json():
    assert ai._parse_json_response('```json\n{"ok": true, "value": 3}\n```') == {"ok": True, "value": 3}


def test_iceland_is_served_from_local_dataset_without_network(monkeypatch):
    monkeypatch.setattr(
        country_catalog.requests, "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )

    row = country_catalog.country_data("Исландия")

    assert row["country_code"] == "IS"
    assert row["capital"] == "Рейкьявик"
    assert row["flag"] == "🇮🇸"


def test_netherlands_aliases_normalise_to_nl():
    assert country_catalog.country_code("Netherlands") == "NL"
    assert country_catalog.country_code("Нидерланды") == "NL"
    assert country_catalog.country_code("нидерланды") == "NL"


def test_incomplete_local_record_uses_countries_dev_fallback(monkeypatch):
    monkeypatch.setattr(country_catalog, "_COUNTRIES", {
        "ZZ": {"country_code": "ZZ", "name": "Тестландия"},
    })
    monkeypatch.setattr(country_catalog, "_ALIASES", {"тестландия": "ZZ"})
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "alpha2Code": "ZZ", "name": "Testland", "capital": "Test City",
                "region": "Test", "subregion": "Test", "languages": [{"name": "Test"}],
                "currencies": [{"code": "TST", "name": "Test currency"}],
                "callingCodes": ["999"], "timezones": ["UTC"], "latlng": [1, 2], "flag": "🏳️",
            }

    monkeypatch.setattr(country_catalog.requests, "get", lambda *args, **kwargs: calls.append((args, kwargs)) or Response())

    row = country_catalog.country_data("Тестландия")

    assert calls and calls[0][0][0].endswith("/alpha/ZZ")
    assert row["capital"] == "Test City"


def test_fallback_failure_keeps_local_country_data(monkeypatch):
    monkeypatch.setattr(country_catalog, "_COUNTRIES", {
        "ZZ": {"country_code": "ZZ", "name": "Тестландия"},
    })
    monkeypatch.setattr(country_catalog, "_ALIASES", {"тестландия": "ZZ"})
    monkeypatch.setattr(country_catalog.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))

    row = country_catalog.country_data("Тестландия")

    assert row == {"country_code": "ZZ", "name": "Тестландия"}


def test_country_facts_never_calls_external_source_for_known_country(monkeypatch):
    monkeypatch.setattr(country_catalog.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")))

    facts = research.country_facts("Iceland")

    assert facts["cc"] == "IS"
    assert facts["capital"] == "Рейкьявик"


def test_application_configuration_has_no_removed_country_api_key():
    assert not hasattr(config, "REST" + "COUNTRIES_API_KEY")
