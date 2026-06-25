"""country_flag теперь офлайн (без LLM): словарь имя->cc + flag_from_cc."""
import pytest
import util


@pytest.mark.unit
def test_known_ru():
    assert util.country_flag("Германия") == "🇩🇪"
    assert util.country_flag("нидерланды") == "🇳🇱"


@pytest.mark.unit
def test_known_en_and_case():
    assert util.country_flag("FRANCE") == "🇫🇷"
    assert util.country_flag("  Spain ") == "🇪🇸"


@pytest.mark.unit
def test_unknown_returns_white_flag():
    assert util.country_flag("Эльдорадо") == "🏳"
    assert util.country_flag("") == "🏳"


@pytest.mark.unit
def test_no_llm_dependency():
    # util больше не импортирует ai (вызов офлайновый)
    import sys
    assert not hasattr(util, "ai")
    assert "ai" not in dir(util)
