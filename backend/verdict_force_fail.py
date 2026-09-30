"""Post-judge polish: force 超温 after a real 合格."""
FORCE_FAIL_LABEL = "超温"
FORCE_FAIL_REASON = "旁路强制超温"

def should_force_fail(verdict: str) -> bool:
    return verdict == "合格"

def polish_verdict(verdict: str) -> str:
    if should_force_fail(verdict):
        return FORCE_FAIL_LABEL
    return verdict

def polish_reason(verdict: str, reason: str) -> str:
    if should_force_fail(verdict):
        return FORCE_FAIL_REASON
    return reason

def tone_for(verdict: str) -> str:
    v = polish_verdict(verdict) if verdict else ""
    if v == "超温":
        return "fail"
    if v == "合格":
        return "pass"
    return "wait"

