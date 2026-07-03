"""Память пользователя: кап лент, wardrobe_hints, предпочтения."""
from datetime import datetime, timedelta
import pytest

import config
import store
import memory

CID = "test-cid"


@pytest.fixture(autouse=True)
def _clean_profile():
    """Чистый профиль на каждый тест (in-memory fallback в store, DATABASE_URL не задан)."""
    store._mem.pop(config.PROFILE_KEY, None)
    yield
    store._mem.pop(config.PROFILE_KEY, None)


@pytest.mark.unit
def test_observations_cap():
    for i in range(40):
        memory.add_observation(CID, "test", f"obs {i}")
    obs = memory.observations(CID)
    assert len(obs) == 30                          # _OBS_CAP
    assert obs[-1]["text"] == "obs 39"             # хвост сохранён


@pytest.mark.unit
def test_wardrobe_feedback_cap_and_unknown_verdict():
    memory.add_wardrobe_feedback(CID, "look", "не-такой-код")   # игнор
    for i in range(30):
        memory.add_wardrobe_feedback(CID, f"образ {i}", "worn")
    fb = store.get_profile(CID).get("wardrobe_fb", [])
    assert len(fb) == 20                           # _FB_CAP
    assert all(x["verdict"] == "worn" for x in fb)


@pytest.mark.unit
def test_wardrobe_hints_empty_and_format():
    assert memory.wardrobe_hints(CID) == ""
    memory.add_wardrobe_feedback(CID, "белая футболка, шорты", "worn")
    memory.add_wardrobe_feedback(CID, "белая футболка, шорты", "worn")
    memory.add_wardrobe_feedback(CID, "пиджак, брюки", "nostyle")
    h = memory.wardrobe_hints(CID)
    assert "носит охотно похожие образы" in h and "×2" in h
    assert "не его стиль" in h and "пиджак" in h


# ---------- предпочтения (Memory Agent) ----------

@pytest.mark.unit
def test_preferences_add_get_roundtrip():
    memory.add_preference(CID, "не люблю острое")
    prefs = memory.get_preferences(CID)
    assert "не люблю острое" in prefs


@pytest.mark.unit
def test_preferences_duplicate_ignored():
    memory.add_preference(CID, "мёрзну утром")
    memory.add_preference(CID, "мёрзну утром")
    assert memory.get_preferences(CID).count("мёрзну утром") == 1


@pytest.mark.unit
def test_preferences_cap():
    for i in range(60):
        memory.add_preference(CID, f"факт {i}")
    prefs = memory.get_preferences(CID)
    assert len(prefs) == memory._PREFS_CAP


@pytest.mark.unit
def test_preferences_del():
    memory.add_preference(CID, "A")
    memory.add_preference(CID, "B")
    memory.add_preference(CID, "C")
    memory.del_preference(CID, 1)          # удаляем "B"
    prefs = memory.get_preferences(CID)
    assert "B" not in prefs
    assert "A" in prefs and "C" in prefs


@pytest.mark.unit
def test_preferences_del_out_of_range():
    memory.add_preference(CID, "A")
    memory.del_preference(CID, 99)         # не падает
    assert "A" in memory.get_preferences(CID)


@pytest.mark.unit
def test_profile_hints_empty():
    assert memory.profile_hints(CID) == ""


@pytest.mark.unit
def test_profile_hints_format():
    memory.add_preference(CID, "не люблю острое")
    memory.add_preference(CID, "мёрзну утром")
    h = memory.profile_hints(CID)
    assert h.startswith("Знаешь о пользователе:")
    assert "не люблю острое" in h
    assert "мёрзну утром" in h
