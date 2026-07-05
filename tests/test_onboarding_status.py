"""Статусы онбординга разделов: переходы и ленивая миграция легаси-полей."""
import pytest

import config
import store
import onboarding_status as obs

CID = "obs-test-cid"


@pytest.fixture(autouse=True)
def _clean_profile():
    store._mem.pop(config.PROFILE_KEY, None)
    yield
    store._mem.pop(config.PROFILE_KEY, None)


@pytest.mark.unit
def test_default_status_is_not_started():
    assert obs.get(CID, "wardrobe") == obs.NOT_STARTED
    assert not obs.is_settled(CID, "wardrobe")
    assert not obs.is_skipped(CID, "wardrobe")


@pytest.mark.unit
def test_set_and_read_status():
    obs.set_status(CID, "leisure", obs.SKIPPED)
    assert obs.get(CID, "leisure") == obs.SKIPPED
    assert obs.is_settled(CID, "leisure")
    assert obs.is_skipped(CID, "leisure")


@pytest.mark.unit
def test_completed_is_settled_but_not_skipped():
    obs.set_status(CID, "cooking", obs.COMPLETED)
    assert obs.is_settled(CID, "cooking")
    assert not obs.is_skipped(CID, "cooking")


@pytest.mark.unit
def test_invalid_status_raises():
    with pytest.raises(ValueError):
        obs.set_status(CID, "health", "bogus")


@pytest.mark.unit
def test_legacy_flag_migrates_to_completed():
    # Легаси булево поле firstvisit v1.
    store.set_profile(CID, {"_fv_wardrobe": True})
    assert obs.get(CID, "wardrobe") == obs.COMPLETED


@pytest.mark.unit
def test_legacy_balance_migrates_to_both_health_and_cooking():
    store.set_profile(CID, {"_fv_balance": True})
    assert obs.get(CID, "health") == obs.COMPLETED
    assert obs.get(CID, "cooking") == obs.COMPLETED


@pytest.mark.unit
def test_explicit_status_overrides_legacy_flag():
    store.set_profile(CID, {"_fv_wardrobe": True})
    obs.set_status(CID, "wardrobe", obs.SKIPPED)
    # Явно проставленный статус побеждает легаси-флаг.
    assert obs.get(CID, "wardrobe") == obs.SKIPPED


@pytest.mark.unit
def test_set_status_preserves_other_profile_fields():
    store.set_profile(CID, {"diet_prefs": "без мяса"})
    obs.set_status(CID, "cooking", obs.COMPLETED)
    prof = store.get_profile(CID)
    assert prof["diet_prefs"] == "без мяса"
    assert prof["onboarding"]["cooking"] == obs.COMPLETED
