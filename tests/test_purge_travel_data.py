import importlib.util
from pathlib import Path

import config
import settings
import storage_driver

_SPEC = importlib.util.spec_from_file_location(
    "purge_travel_data", Path(__file__).resolve().parent.parent / "scripts" / "purge_travel_data.py",
)
purge = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(purge)


def _seed(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "")
    memory = {
        config.SAVED_COUNTRIES_KEY: {"1": ["IS", "NL"], "2": ["PT"]},
        config.TRAVEL_DISLIKE_KEY: {"1": ["FR"]},
        config.TRAVEL_IDEA_KEY: {"1": {"idea": {"to": "Утрехт"}}},
        config.TRAVEL_COUNTRY_CARDS_KEY: {"IS": {"name": "Исландия"}},
        settings.SETTINGS_KEY: {"1": {"city": "Алкмар", "travel_country_codes_migrated": True}},
        config.RECOMMENDATION_STOPLIST_KEY: {"1": [
            {"type": "country", "value": "Франция"}, {"type": "movie", "value": "Фильм"},
        ]},
        config.MONTHLY_REBUSES_CACHE_KEY: {"travel": {"items": []}, "movies": {"items": []}},
        config.CATEGORY_NEWS_CACHE_KEY: {"categories": {"travel": {}, "food": {"items": []}}},
        config.FAVORITE_MOVIES_KEY: {"1": ["Фильм"]},
    }
    monkeypatch.setattr(storage_driver, "_memory", memory)
    monkeypatch.setattr(storage_driver, "_read_cache", {})
    return memory


def test_dry_run_prints_counts_without_values_and_keeps_data(monkeypatch, capsys):
    memory = _seed(monkeypatch)
    before = repr(memory)

    assert purge.main([]) == 0

    out = capsys.readouterr().out
    assert f"key {config.SAVED_COUNTRIES_KEY}: entries: 2" in out
    assert "type=country: 1" in out
    assert "dry-run" in out
    assert "Исландия" not in out and "IS" not in out.split() and "Утрехт" not in out
    assert repr(memory) == before


def test_apply_removes_only_travel_data(monkeypatch, capsys):
    memory = _seed(monkeypatch)

    assert purge.main(["--apply"]) == 0

    for key in (config.SAVED_COUNTRIES_KEY, config.TRAVEL_DISLIKE_KEY,
                config.TRAVEL_IDEA_KEY, config.TRAVEL_COUNTRY_CARDS_KEY):
        assert key not in memory
    assert memory[settings.SETTINGS_KEY] == {"1": {"city": "Алкмар"}}
    assert memory[config.RECOMMENDATION_STOPLIST_KEY] == {"1": [{"type": "movie", "value": "Фильм"}]}
    assert memory[config.MONTHLY_REBUSES_CACHE_KEY] == {"movies": {"items": []}}
    assert memory[config.CATEGORY_NEWS_CACHE_KEY] == {"categories": {"food": {"items": []}}}
    assert memory[config.FAVORITE_MOVIES_KEY] == {"1": ["Фильм"]}
    capsys.readouterr()
    assert purge.main([]) == 0
    assert "nothing to purge" in capsys.readouterr().out
