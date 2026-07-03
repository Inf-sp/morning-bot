import pytest

import balance
import settings


@pytest.mark.unit
def test_toggle_cuisine_adds_and_removes():
    cid = "cuisine-test-1"
    assert settings.cuisines(cid) == []

    import asyncio
    asyncio.run(settings.toggle_cuisine(_NoopBot(), cid, "asian"))
    assert settings.cuisines(cid) == ["asian"]

    asyncio.run(settings.toggle_cuisine(_NoopBot(), cid, "asian"))
    assert settings.cuisines(cid) == []


@pytest.mark.unit
def test_toggle_cuisine_ignores_unknown_key():
    cid = "cuisine-test-2"
    import asyncio
    asyncio.run(settings.toggle_cuisine(_NoopBot(), cid, "not-a-real-cuisine"))
    assert settings.cuisines(cid) == []


@pytest.mark.unit
def test_cuisine_context_lists_selected_labels():
    cid = "cuisine-test-3"
    settings.set_(cid, "cuisines", ["asian", "italian"])
    ctx = settings.cuisine_context(cid)
    assert "🥢 Азиатская" in ctx
    assert "🍝 Итальянская" in ctx


@pytest.mark.unit
def test_cuisine_context_empty_when_nothing_selected():
    cid = "cuisine-test-4"
    assert settings.cuisine_context(cid) == ""


@pytest.mark.unit
def test_gen_recipe_prompt_includes_cuisine_and_leftover_avoid(monkeypatch):
    cid = "cuisine-test-5"
    settings.set_(cid, "cuisines", ["russian"])
    balance._leftover_remember(cid, "Гречка с грибами")

    captured = {}

    def fake_llm_json(prompt, *a, **kw):
        captured["prompt"] = prompt
        return {"name": "Тест"}

    monkeypatch.setattr(balance.ai, "llm_json", fake_llm_json)
    balance._gen_recipe("обед", cid=cid)

    assert "🇷🇺 Русская" in captured["prompt"]
    assert "Гречка с грибами" in captured["prompt"]


@pytest.mark.unit
def test_gen_leftovers_recipe_prompt_includes_cuisine(monkeypatch):
    cid = "cuisine-test-6"
    settings.set_(cid, "cuisines", ["italian"])

    captured = {}

    def fake_llm_json(prompt, *a, **kw):
        captured["prompt"] = prompt
        return {"name": "Тест"}

    monkeypatch.setattr(balance.ai, "llm_json", fake_llm_json)
    balance._gen_leftovers_recipe("паста, помидоры", cid=cid)

    assert "🍝 Итальянская" in captured["prompt"]


class _NoopBot:
    async def send_message(self, *a, **kw):
        return None
