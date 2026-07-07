import os

os.environ.setdefault("TELEGRAM_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")

import balance


def test_single_recipe_enables_personal_json_openrouter(monkeypatch):
    seen = {}

    monkeypatch.setattr(balance, "_my_recipe_pref", lambda cid: "")
    monkeypatch.setattr(balance, "_leftover_recent", lambda cid: [])
    monkeypatch.setattr(balance.settings, "priority_context", lambda cid: "")
    monkeypatch.setattr(balance.settings, "cuisine_context", lambda cid: "")

    def fake_llm_json(prompt, max_tokens, **kwargs):
        seen.update(kwargs)
        return {
            "name": "Омлет",
            "time": "10 мин",
            "servings": "1 порц.",
            "ingredients": "яйца, соль",
            "steps": ["Взбей яйца", "Пожарь омлет"],
            "full": "Омлет",
        }

    monkeypatch.setattr(balance.ai, "llm_json", fake_llm_json)

    balance._gen_recipe("завтрак", cid="1")

    assert seen["module"] == "food"
    assert seen["fallback_allowed"] is True
    assert seen["privacy_level"] == "personal"
    assert seen["allow_personal_openrouter"] is True


def test_recipe_batch_enables_personal_json_openrouter(monkeypatch):
    seen = {}

    monkeypatch.setattr(balance, "_my_recipe_pref", lambda cid: "")
    monkeypatch.setattr(balance.settings, "priority_context", lambda cid: "")
    monkeypatch.setattr(balance.settings, "cuisine_context", lambda cid: "")

    def fake_llm_json(prompt, max_tokens, **kwargs):
        seen.update(kwargs)
        return {"recipes": [{"name": "Тост", "time": "5 мин"}]}

    monkeypatch.setattr(balance.ai, "llm_json", fake_llm_json)

    items = balance._gen_recipe_batch("завтрак", cid="1", season_hint="")

    assert items == [{"name": "Тост", "time": "5 мин"}]
    assert seen["module"] == "food"
    assert seen["fallback_allowed"] is True
    assert seen["privacy_level"] == "personal"
    assert seen["allow_personal_openrouter"] is True
