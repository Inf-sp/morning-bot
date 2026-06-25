"""Контракт-тесты: реестр скиллов непротиворечив, аудит callback'ов без нарушений."""
import pytest
import skills
import verify


@pytest.mark.unit
def test_skills_registry_consistent():
    assert skills.SKILLS, "реестр не пустой"
    for name, s in skills.SKILLS.items():
        assert s.name == name
        assert s.surface in verify.SURFACES, f"{name}: неизвестный surface {s.surface}"
        assert s.fallback, f"{name}: пустой fallback"
        assert isinstance(s.entrypoints, tuple)


@pytest.mark.unit
def test_named_skills_present():
    expected = {"morning_brief", "adhd_unstuck", "wardrobe_feedback",
                "language_micro_lesson", "evening_review", "health_triage_safe",
                "travel_recommender"}
    assert expected <= set(skills.SKILLS)


@pytest.mark.integration
def test_no_unhandled_callbacks():
    """Каждый литеральный callback_data должен где-то обрабатываться (advisory)."""
    unhandled = verify.audit_callbacks()
    assert unhandled == [], f"необработанные callback'и: {unhandled}"
