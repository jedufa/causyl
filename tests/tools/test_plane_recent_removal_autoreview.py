from tools.bin import plane_recent_removal_autoreview as mod


def test_parse_decision_approve():
    parsed = mod._parse_decision("DECISION: APPROVE\nREASON: safe bounded reuse\n")
    assert parsed["decision"] == "APPROVE"
    assert parsed["reason"] == "safe bounded reuse"


def test_parse_decision_block():
    parsed = mod._parse_decision("DECISION: BLOCK\nREASON: ambiguous overlap\n")
    assert parsed["decision"] == "BLOCK"
    assert parsed["reason"] == "ambiguous overlap"
