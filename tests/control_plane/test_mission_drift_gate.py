from __future__ import annotations

from infra.controlplane import mission_drift_gate


def test_assess_intent_mission_fit_allows_aligned_scope() -> None:
    result = mission_drift_gate.assess_intent_mission_fit(
        title="Operator evidence tooling",
        intent_markdown="# Intent Sheet\n\nImprove software development and tooling proof surfaces.\n",
    )

    assert result["ok"] is True
    assert result["status"] == "mission_aligned"
    assert result["classification"] == "aligned"
    assert "software development and tooling" in result["matched_aligned_boundaries"]
    assert result["matched_non_goals"] == []


def test_assess_intent_mission_fit_blocks_unrelated_scope_without_exact_non_goal_match() -> None:
    result = mission_drift_gate.assess_intent_mission_fit(
        title="Neighborhood bakery expansion",
        intent_markdown="# Intent Sheet\n\nOpen a second bakery location with better pastries and weekend seating.\n",
    )

    assert result["ok"] is False
    assert result["status"] == "mission_misaligned"
    assert result["classification"] == "mismatch"
    assert result["decision"] == "block"
    assert result["matched_aligned_boundaries"] == []
    assert result["matched_non_goals"] == []
    assert result["reason_codes"] == ["no_aligned_boundary_match"]


def test_assess_intent_mission_fit_blocks_explicit_non_goal_without_exception() -> None:
    result = mission_drift_gate.assess_intent_mission_fit(
        title="Autonomous lab director expansion",
        intent_markdown="# Intent Sheet\n\nPursue fully autonomous lab director behavior in the near term.\n",
    )

    assert result["ok"] is False
    assert result["status"] == "mission_misaligned"
    assert result["classification"] == "blocked"
    assert "fully autonomous lab director behavior in the near term" in result["matched_non_goals"]
    assert result["decision"] == "block"


def test_assess_intent_mission_fit_allows_explicit_exception_record() -> None:
    result = mission_drift_gate.assess_intent_mission_fit(
        title="Autonomous lab director boundary study",
        intent_markdown="# Intent Sheet\n\nEvaluate fully autonomous lab director behavior in the near term as a non-goal boundary test.\n",
        exception_record={
            "allowed": True,
            "reason": "Governance boundary exercise.",
            "approved_by": "operator",
        },
    )

    assert result["ok"] is True
    assert result["status"] == "partially_aligned"
    assert result["classification"] == "needs_exception"
    assert result["decision"] == "allow_with_exception"
    assert result["exception"]["approved_by"] == "operator"
