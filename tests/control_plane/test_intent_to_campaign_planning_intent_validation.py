from __future__ import annotations

import json

import pytest

from infra.controlplane import intent_to_campaign_planning_intent_validation as validation


def test_intent_to_campaign_planning_intent_validation_artifact_can_represent_full_readiness() -> None:
    artifact = validation.build_intent_to_campaign_planning_intent_validation_artifact(
        validation_case_utc="2026-04-11T14:30:00Z",
        dependency_ready=True,
    )

    assert validation.validate_intent_to_campaign_planning_intent_validation_artifact(artifact) == []
    assert artifact["uid"] == validation.VALIDATION_UID
    assert artifact["source_intent_uid"] == validation.SOURCE_INTENT_UID
    assert artifact["validation_outcome"] == "passed"
    assert artifact["validation_results"]["dependency_ledger_evidence_inspection"]["status"] == "passed"
    assert any(row["requirement_id"] == "advisory_handoff_only" and row["status"] == "proven" for row in artifact["requirement_coverage"])


def test_intent_to_campaign_planning_intent_validation_artifact_keeps_dependency_gap_visible() -> None:
    artifact = validation.build_intent_to_campaign_planning_intent_validation_artifact(
        validation_case_utc="2026-04-11T14:30:00Z",
        dependency_ready=False,
    )

    assert validation.validate_intent_to_campaign_planning_intent_validation_artifact(artifact) == []
    assert artifact["validation_outcome"] == "proof_gap"
    assert artifact["validation_results"]["dependency_ledger_evidence_inspection"]["status"] == "proof_gap"
    assert any(row["requirement_id"] == "family_dependency_readiness" and row["status"] == "unproven" for row in artifact["requirement_coverage"])
    assert {row["risk_type"] for row in artifact["residual_gaps"]} == {"validation_risk"}


def test_intent_to_campaign_planning_intent_validation_rejects_missing_required_scenario() -> None:
    artifact = validation.build_intent_to_campaign_planning_intent_validation_artifact(
        validation_case_utc="2026-04-11T14:30:00Z",
        dependency_ready=True,
    )
    artifact["scenario_results"] = [
        row for row in artifact["scenario_results"] if row["scenario_id"] != "negative_hidden_policy_injection_rejected"
    ]
    artifact["artifact_fingerprint"] = "stale"

    issues = validation.validate_intent_to_campaign_planning_intent_validation_artifact(artifact)

    assert any("missing required scenarios" in issue for issue in issues)
    assert any("artifact_fingerprint does not match payload" in issue for issue in issues)


def test_write_intent_to_campaign_planning_intent_validation_artifact_is_idempotent(tmp_path) -> None:
    artifact = validation.build_intent_to_campaign_planning_intent_validation_artifact(
        validation_case_utc="2026-04-11T14:30:00Z",
        dependency_ready=True,
    )

    path = validation.write_intent_to_campaign_planning_intent_validation_artifact(artifact, repo_root=tmp_path)
    second = validation.write_intent_to_campaign_planning_intent_validation_artifact(artifact, repo_root=tmp_path)

    assert second == path
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["artifact_fingerprint"] == artifact["artifact_fingerprint"]

    changed = dict(artifact)
    changed["summary"] = "changed"
    changed["artifact_fingerprint"] = "changed"
    with pytest.raises(ValueError, match="artifact"):
        validation.write_intent_to_campaign_planning_intent_validation_artifact(changed, repo_root=tmp_path)
