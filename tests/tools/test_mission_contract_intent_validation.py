from __future__ import annotations

import json
from pathlib import Path

from tools.mission_contract_intent_validation import (
    build_validation_report,
    validate_validation_report,
    write_validation_report,
)


def test_mission_contract_intent_validation_builds_proof_gap_report_with_real_success_and_negative_controls(
    tmp_path: Path,
) -> None:
    report = build_validation_report(
        repo_root=tmp_path,
        report_path=Path("artifacts/intent_validation/mission_contract_intent_e2e.json"),
        implementation_repo_root=Path.cwd(),
        now_fn=lambda: "2026-05-01T15:20:00Z",
    )

    path = write_validation_report(
        report,
        repo_root=tmp_path,
        report_path=Path("artifacts/intent_validation/mission_contract_intent_e2e.json"),
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    scenario_by_id = {row["scenario_id"]: row for row in loaded["scenario_results"]}

    assert validate_validation_report(loaded) == []
    assert loaded["schema_version"] == "mission_contract_intent_validation_v1"
    assert loaded["uid"] == "PIS-20260501-114506Z-F6L8"
    assert loaded["validation_outcome"] == "proof_gap"
    assert loaded["maximum_supported_claim"] == (
        "mission_contract_projection_and_drift_gates_exercised_but_family_dependencies_unverified"
    )
    assert scenario_by_id["positive_loaded_mission_projects_consistently"]["outcome"] == "passed"
    assert scenario_by_id["positive_loaded_mission_projects_consistently"]["observed"]["issues"] == []
    assert scenario_by_id["negative_missing_claim_ceiling_blocks_projection"]["outcome"] == "passed"
    assert scenario_by_id["negative_missing_claim_ceiling_blocks_projection"]["observed"]["failure_class"] == "mission_contract_invalid"
    assert scenario_by_id["negative_off_mission_intent_rejected"]["outcome"] == "passed"
    assert "explicit_non_goal_match" in scenario_by_id["negative_off_mission_intent_rejected"]["observed"]["reason_codes"]
    assert scenario_by_id["negative_seeded_projection_contradiction_detected"]["outcome"] == "passed"
    assert "operator_surface_projection_mismatch" in scenario_by_id["negative_seeded_projection_contradiction_detected"]["observed"]["issue_codes"]
    assert any(row["requirement_id"] == "family_dependency_readiness" and row["status"] == "unproven" for row in loaded["requirement_coverage"])
    assert loaded["artifact_fingerprint"] == report["artifact_fingerprint"]


def test_mission_contract_intent_validation_can_represent_full_dependency_readiness(tmp_path: Path) -> None:
    report = build_validation_report(
        repo_root=tmp_path,
        report_path=Path("artifacts/intent_validation/mission_contract_intent_e2e.json"),
        implementation_repo_root=Path.cwd(),
        now_fn=lambda: "2026-05-01T15:21:00Z",
        force_dependency_ready=True,
    )

    assert validate_validation_report(report) == []
    assert report["validation_outcome"] == "passed"
    assert report["maximum_supported_claim"] == "assembled_mission_contract_projection_validated"
    assert report["residual_gaps"] == []
    assert all(row["status"] == "proven" for row in report["requirement_coverage"])


def test_mission_contract_intent_validation_rejects_missing_negative_scenario_and_stale_fingerprint(tmp_path: Path) -> None:
    report = build_validation_report(
        repo_root=tmp_path,
        report_path=Path("artifacts/intent_validation/mission_contract_intent_e2e.json"),
        implementation_repo_root=Path.cwd(),
        now_fn=lambda: "2026-05-01T15:22:00Z",
    )
    report["scenario_results"] = [
        row for row in report["scenario_results"] if row["scenario_id"] != "negative_seeded_projection_contradiction_detected"
    ]
    report["artifact_fingerprint"] = "stale"

    issues = validate_validation_report(report)

    assert any("missing required negative scenarios" in issue for issue in issues)
    assert any("artifact_fingerprint does not match payload" in issue for issue in issues)


def test_mission_contract_intent_validation_allows_restart_safe_rerun_to_same_path(tmp_path: Path) -> None:
    report_path = Path("artifacts/intent_validation/mission_contract_intent_e2e.json")
    first_report = build_validation_report(
        repo_root=tmp_path,
        report_path=report_path,
        implementation_repo_root=Path.cwd(),
        now_fn=lambda: "2026-05-01T15:23:00Z",
    )
    second_report = build_validation_report(
        repo_root=tmp_path,
        report_path=report_path,
        implementation_repo_root=Path.cwd(),
        now_fn=lambda: "2026-05-01T15:24:00Z",
    )

    first_path = write_validation_report(first_report, repo_root=tmp_path, report_path=report_path)
    second_path = write_validation_report(second_report, repo_root=tmp_path, report_path=report_path)
    loaded = json.loads(second_path.read_text(encoding="utf-8"))

    assert first_path == second_path
    assert loaded["validation_case_utc"] == "2026-05-01T15:24:00Z"
    assert loaded["artifact_fingerprint"] == second_report["artifact_fingerprint"]
    assert validate_validation_report(loaded) == []
