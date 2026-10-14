from __future__ import annotations

import json

import pytest

from infra.controlplane import scientific_campaign_method_template_intent_validation as validation


def test_scientific_campaign_method_template_intent_validation_artifact_keeps_runtime_gap_visible() -> None:
    artifact = validation.build_scientific_campaign_method_template_intent_validation_artifact(
        validation_case_utc="2026-04-10T21:20:00Z",
        dependency_ready=False,
        runtime_handoff_ready=False,
    )

    assert validation.validate_scientific_campaign_method_template_intent_validation_artifact(artifact) == []
    assert artifact["uid"] == "PIS-20260410-193416Z-5A5A"
    assert artifact["source_intent_uid"] == "IS-20260403-212914Z-62FB"
    assert artifact["validation_outcome"] == "proof_gap"
    assert artifact["coverage_summary"]["scenario_outcome_counts"]["passed"] == 10
    assert [row["command_id"] for row in artifact["validation_commands"]] == [
        "reference_template_contract_validation",
        "negative_boundary_fixture_validation",
        "dependency_ledger_evidence_inspection",
        "runtime_handoff_surface_inspection",
    ]
    assert artifact["validation_results"]["dependency_ledger_evidence_inspection"]["status"] == "proof_gap"
    assert artifact["validation_results"]["runtime_handoff_surface_inspection"]["status"] == "proof_gap"
    assert any(row["requirement_id"] == "assembled_runtime_handoff" and row["status"] == "unproven" for row in artifact["requirement_coverage"])
    assert {row["risk_type"] for row in artifact["residual_gaps"]} == {"feature_risk", "validation_risk"}


def test_scientific_campaign_method_template_intent_validation_artifact_can_represent_full_readiness() -> None:
    artifact = validation.build_scientific_campaign_method_template_intent_validation_artifact(
        validation_case_utc="2026-04-10T21:20:00Z",
        dependency_ready=True,
        runtime_handoff_ready=True,
    )

    assert validation.validate_scientific_campaign_method_template_intent_validation_artifact(artifact) == []
    assert artifact["validation_outcome"] == "passed"
    assert artifact["residual_gaps"] == []
    assert all(row["status"] == "proven" for row in artifact["requirement_coverage"])
    assert all(row["status"] == "passed" for row in artifact["validation_results"].values())


def test_scientific_campaign_method_template_intent_validation_rejects_missing_negative_scenario() -> None:
    artifact = validation.build_scientific_campaign_method_template_intent_validation_artifact(
        validation_case_utc="2026-04-10T21:20:00Z",
        dependency_ready=False,
        runtime_handoff_ready=False,
    )
    artifact["scenario_results"] = [
        row for row in artifact["scenario_results"] if row["scenario_id"] != "negative_inline_budget_policy_rejected"
    ]
    artifact["artifact_fingerprint"] = "stale"

    issues = validation.validate_scientific_campaign_method_template_intent_validation_artifact(artifact)

    assert any("missing required negative scenarios" in issue for issue in issues)
    assert any("artifact_fingerprint does not match payload" in issue for issue in issues)


def test_scientific_campaign_method_template_intent_validation_rejects_missing_validation_result() -> None:
    artifact = validation.build_scientific_campaign_method_template_intent_validation_artifact(
        validation_case_utc="2026-04-10T21:20:00Z",
        dependency_ready=False,
        runtime_handoff_ready=False,
    )
    artifact["validation_results"].pop("runtime_handoff_surface_inspection")
    artifact["artifact_fingerprint"] = "stale"

    issues = validation.validate_scientific_campaign_method_template_intent_validation_artifact(artifact)

    assert any("validation_results missing command_ids: runtime_handoff_surface_inspection" in issue for issue in issues)
    assert any("artifact_fingerprint does not match payload" in issue for issue in issues)


def test_write_scientific_campaign_method_template_intent_validation_artifact_is_idempotent(tmp_path) -> None:
    artifact = validation.build_scientific_campaign_method_template_intent_validation_artifact(
        validation_case_utc="2026-04-10T21:20:00Z",
        dependency_ready=False,
        runtime_handoff_ready=False,
    )

    path = validation.write_scientific_campaign_method_template_intent_validation_artifact(artifact, repo_root=tmp_path)
    second = validation.write_scientific_campaign_method_template_intent_validation_artifact(artifact, repo_root=tmp_path)

    assert second == path
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["artifact_fingerprint"] == artifact["artifact_fingerprint"]

    changed = dict(artifact)
    changed["summary"] = "changed"
    changed["artifact_fingerprint"] = "changed"
    with pytest.raises(ValueError, match="artifact_fingerprint"):
        validation.write_scientific_campaign_method_template_intent_validation_artifact(changed, repo_root=tmp_path)
