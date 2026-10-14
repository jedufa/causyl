from __future__ import annotations

import json

import pytest

from infra.controlplane import research_evidence_pack_intent_validation as validation


def test_research_evidence_pack_intent_validation_artifact_maps_intent_and_negative_paths() -> None:
    artifact = validation.build_research_evidence_pack_intent_validation_artifact(
        validation_case_utc="2026-04-10T12:41:11Z",
        dependency_ready=True,
    )

    assert validation.validate_research_evidence_pack_intent_validation_artifact(artifact) == []
    assert artifact["uid"] == "PIS-20260410-102637Z-B40E"
    assert artifact["source_intent_uid"] == "IS-20260403-212914Z-5E8C"
    assert artifact["validation_outcome"] == "passed"
    assert artifact["validation_inputs"] == {
        "pack_ok": True,
        "compendium_ok": True,
        "campaign_handoff_ok": True,
        "phase_gate_handoff_ok": True,
        "continuation_handoff_ok": True,
        "reporting_artifact_count": 2,
    }
    assert artifact["coverage_summary"]["scenario_outcome_counts"] == {"passed": 7}
    assert {row["status"] for row in artifact["requirement_coverage"]} == {"proven"}
    assert artifact["residual_gaps"] == []

    scenario_ids = {row["scenario_id"] for row in artifact["scenario_results"]}
    assert {
        "negative_unattributed_summary_rejected",
        "negative_flattened_contradiction_rejected",
        "negative_missing_restart_link_rejected",
        "negative_raw_note_body_rejected",
    }.issubset(scenario_ids)


def test_research_evidence_pack_intent_validation_rejects_missing_negative_scenario() -> None:
    artifact = validation.build_research_evidence_pack_intent_validation_artifact(
        validation_case_utc="2026-04-10T12:41:11Z",
        dependency_ready=True,
    )
    artifact["scenario_results"] = [
        row for row in artifact["scenario_results"] if row["scenario_id"] != "negative_raw_note_body_rejected"
    ]
    artifact["artifact_fingerprint"] = "stale"

    issues = validation.validate_research_evidence_pack_intent_validation_artifact(artifact)

    assert any("missing required negative scenarios" in issue for issue in issues)
    assert any("artifact_fingerprint does not match payload" in issue for issue in issues)


def test_write_research_evidence_pack_intent_validation_artifact_is_idempotent(tmp_path) -> None:
    artifact = validation.build_research_evidence_pack_intent_validation_artifact(
        validation_case_utc="2026-04-10T12:41:11Z",
        dependency_ready=True,
    )

    path = validation.write_research_evidence_pack_intent_validation_artifact(artifact, repo_root=tmp_path)
    second = validation.write_research_evidence_pack_intent_validation_artifact(artifact, repo_root=tmp_path)

    assert second == path
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["artifact_fingerprint"] == artifact["artifact_fingerprint"]

    changed = dict(artifact)
    changed["summary"] = "changed"
    changed["artifact_fingerprint"] = "changed"
    with pytest.raises(ValueError, match="artifact_fingerprint"):
        validation.write_research_evidence_pack_intent_validation_artifact(changed, repo_root=tmp_path)


def test_research_evidence_pack_intent_validation_keeps_dependency_gap_visible() -> None:
    artifact = validation.build_research_evidence_pack_intent_validation_artifact(
        validation_case_utc="2026-04-10T12:41:11Z",
        dependency_ready=False,
    )

    assert validation.validate_research_evidence_pack_intent_validation_artifact(artifact) == []
    assert artifact["validation_outcome"] == "proof_gap"
    assert any(row["status"] == "unproven" for row in artifact["requirement_coverage"])
    assert artifact["residual_gaps"]
