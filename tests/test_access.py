"""Тесты access.py: allowlist, инвайт-коды, owner-гард."""
import time
import pytest

import config
import store
import access


@pytest.fixture(autouse=True)
def _clean_store():
    """Чистый стор перед каждым тестом."""
    store._mem.pop(config.ALLOWED_CIDS_KEY, None)
    store._mem.pop(config.PENDING_INVITES_KEY, None)
    yield
    store._mem.pop(config.ALLOWED_CIDS_KEY, None)
    store._mem.pop(config.PENDING_INVITES_KEY, None)


# ---------- owner ----------

@pytest.mark.unit
def test_is_owner_matches_chat_id():
    assert access.is_owner(config.CHAT_ID)
    assert access.is_owner(str(config.CHAT_ID))


@pytest.mark.unit
def test_is_owner_rejects_other():
    assert not access.is_owner("9999999")
    assert not access.is_owner("")


# ---------- allowlist ----------

@pytest.mark.unit
def test_owner_always_allowed():
    # owner не нужно добавлять в список — он всегда допущен
    assert access.is_allowed(config.CHAT_ID)


@pytest.mark.unit
def test_unknown_user_not_allowed():
    assert not access.is_allowed("9999999")


@pytest.mark.unit
def test_allow_then_check():
    access.allow_user("42")
    assert access.is_allowed("42")


@pytest.mark.unit
def test_revoke_removes_access():
    access.allow_user("42")
    access.revoke_user("42")
    assert not access.is_allowed("42")


@pytest.mark.unit
def test_allow_idempotent():
    access.allow_user("42")
    access.allow_user("42")
    cids = access._load_allowed()
    assert cids.count("42") == 1


@pytest.mark.unit
def test_get_allowed_cids_includes_owner():
    access.allow_user("99")
    cids = access.get_allowed_cids()
    assert str(config.CHAT_ID) in cids
    assert "99" in cids


# ---------- инвайты ----------

@pytest.mark.unit
def test_create_invite_returns_8char_code():
    code = access.create_invite()
    assert len(code) == 8
    assert code in access.pending_invites()


@pytest.mark.unit
def test_use_invite_valid_grants_access():
    code = access.create_invite()
    result = access.use_invite(code, "55")
    assert result is True
    assert access.is_allowed("55")
    # код одноразовый — после использования его нет
    assert code not in access.pending_invites()


@pytest.mark.unit
def test_use_invite_invalid_code():
    result = access.use_invite("deadbeef", "55")
    assert result is False
    assert not access.is_allowed("55")


@pytest.mark.unit
def test_use_invite_expired_code():
    code = access.create_invite()
    # подмешиваем просроченный timestamp прямо в store
    invites = access._load_invites()
    invites[code] = time.time() - access._INVITE_TTL - 1
    access._save_invites(invites)
    result = access.use_invite(code, "55")
    assert result is False
    assert not access.is_allowed("55")


@pytest.mark.unit
def test_create_invite_purges_expired():
    # накидываем просроченный инвайт вручную
    invites = {"oldcode": time.time() - access._INVITE_TTL - 1}
    access._save_invites(invites)
    access.create_invite()
    # просроченный должен исчезнуть
    assert "oldcode" not in access.pending_invites()
