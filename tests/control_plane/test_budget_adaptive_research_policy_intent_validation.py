from __future__ import annotations

import json

import pytest

from infra.controlplane import budget_adaptive_research_policy_intent_validation as validation


def test_budget_adaptive_research_policy_intent_validation_artifact_maps_mode_boundaries_and_review_gate() -> None:
    artifact = validation.build_budget_adaptive_research_policy_intent_validation_artifact(
        validation_case_utc="2026-04-11T09:46:33Z",
        review_ready=False,
    )

    assert validation.validate_budget_adaptive_research_policy_intent_validation_artifact(artifact) == []
    assert artifact["uid"] == "PIS-20260411-081502Z-C6F3"
    assert artifact["source_intent_uid"] == "IS-20260403-212914Z-AA86"
    assert artifact["validation_outcome"] == "proof_gap"
    assert artifact["validation_inputs"]["policy_contract_ok"] is True
    assert artifact["validation_inputs"]["dependency_children_ready"] is True
    assert artifact["validation_inputs"]["specialized_review_ready"] is False
    assert artifact["coverage_summary"]["scenario_outcome_counts"] == {"passed": 7}
    assert any(row["requirement_id"] == "specialized_review_gate" and row["status"] == "unproven" for row in artifact["requirement_coverage"])
    assert any("specialized_verifier_review_pending" in gap for gap in artifact["residual_gaps"])


def test_budget_adaptive_research_policy_intent_validation_artifact_can_represent_full_readiness() -> None:
    artifact = validation.build_budget_adaptive_research_policy_intent_validation_artifact(
        validation_case_utc="2026-04-11T09:46:33Z",
        review_ready=True,
    )

    assert validation.validate_budget_adaptive_research_policy_intent_validation_artifact(artifact) == []
    assert artifact["validation_outcome"] == "passed"
    assert artifact["residual_gaps"] == []
    assert all(row["status"] == "passed" for row in artifact["validation_results"].values())
    assert any(row["requirement_id"] == "specialized_review_gate" and row["status"] == "proven" for row in artifact["requirement_coverage"])
    assert artifact["coverage_summary"]["scenario_outcome_counts"] == {"passed": 7}
    review_scenario = next(
        row for row in artifact["scenario_results"] if row["scenario_id"] == "negative_specialized_review_missing_keeps_gate_draft"
    )
    assert review_scenario["outcome"] == "passed"
    assert "Specialized verifier review is now durable" in review_scenario["observations"][0]


def test_budget_adaptive_research_policy_intent_validation_rejects_missing_required_scenario() -> None:
    artifact = validation.build_budget_adaptive_research_policy_intent_validation_artifact(
        validation_case_utc="2026-04-11T09:46:33Z",
        review_ready=False,
    )
    artifact["scenario_results"] = [
        row for row in artifact["scenario_results"] if row["scenario_id"] != "negative_campaign_premature_convergence_rejected"
    ]
    artifact["artifact_fingerprint"] = "stale"

    issues = validation.validate_budget_adaptive_research_policy_intent_validation_artifact(artifact)

    assert any("missing required scenarios" in issue for issue in issues)
    assert any("artifact_fingerprint does not match payload" in issue for issue in issues)


def test_write_budget_adaptive_research_policy_intent_validation_artifact_is_idempotent(tmp_path) -> None:
    artifact = validation.build_budget_adaptive_research_policy_intent_validation_artifact(
        validation_case_utc="2026-04-11T09:46:33Z",
        review_ready=False,
    )

    path = validation.write_budget_adaptive_research_policy_intent_validation_artifact(artifact, repo_root=tmp_path)
    second = validation.write_budget_adaptive_research_policy_intent_validation_artifact(artifact, repo_root=tmp_path)

    assert second == path
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["artifact_fingerprint"] == artifact["artifact_fingerprint"]

    changed = dict(artifact)
    changed["summary"] = "changed"
    changed["artifact_fingerprint"] = "changed"
    with pytest.raises(ValueError, match="artifact_fingerprint"):
        validation.write_budget_adaptive_research_policy_intent_validation_artifact(changed, repo_root=tmp_path)
