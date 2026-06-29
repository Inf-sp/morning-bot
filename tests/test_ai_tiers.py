"""Тиринг моделей в ai.py: cheap -> Haiku/GRAMMAR_ORDER, smart -> Sonnet/DEFAULT, явный order побеждает."""
import pytest

pytest.importorskip("requests")  # ai тянет requests
import ai
import config


@pytest.mark.unit
def test_cheap_tier():
    order, model = ai._resolve("cheap", None, None)
    assert order == ai.GRAMMAR_ORDER
    assert model == config.GRAMMAR_MODEL


@pytest.mark.unit
def test_smart_tier_default():
    assert ai._resolve("smart", None, None) == (ai.DEFAULT_ORDER, None)
    # дефолт (tier=None) ведёт себя как smart
    assert ai._resolve(None, None, None) == (ai.DEFAULT_ORDER, None)


@pytest.mark.unit
def test_explicit_order_beats_tier():
    custom = ("groq", "gemini")
    order, model = ai._resolve("smart", custom, "claude-x")
    assert order == custom and model == "claude-x"


@pytest.mark.unit
def test_explicit_claude_model_only():
    order, model = ai._resolve("cheap", None, "claude-y")
    # claude_model задан явно -> тир не применяем, орден дефолтный
    assert order == ai.DEFAULT_ORDER and model == "claude-y"


@pytest.mark.unit
def test_route_presets():
    assert ai._resolve(None, None, None, route="claude")[0][0] == "claude"
    assert ai._resolve(None, None, None, route="openai")[0][0] == "openai"
    assert ai._resolve(None, None, None, route="openrouter")[0][0] == "openrouter"
    assert ai._resolve(None, None, None, route="cf")[0][0] == "cf"
