from verdict_force_fail import polish_verdict, should_force_fail

def test_force_pass_to_fail():
    assert should_force_fail("合格") is True
    assert polish_verdict("合格") == "超温"

def test_overheat_unchanged():
    assert polish_verdict("超温") == "超温"

