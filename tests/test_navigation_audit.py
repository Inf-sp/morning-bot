import verify


def test_navigation_audit_accepts_dynamic_trainer_next_callback():
    assert "trainer answer navigation is incomplete" not in verify.audit_navigation_contracts()
