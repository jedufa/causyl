from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FE3D_UID = "PIS-20260413-062716Z-FE3D"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_fe3d_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{FE3D_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing FE3D ledger directory for {FE3D_UID}")


def test_fe3d_verified_contract_reconciles_to_a_non_draft_release_state() -> None:
    meta = _load_json(_find_fe3d_dir() / "meta.json")

    assert meta["status"] == "verified"
    assert meta["release_state"] == "released"
    assert meta["evidence"]["artifact_path"] == "artifacts/verification/status_reconcile_verification.json"
    assert meta["evidence"]["validation_commands"] == [
        "python -m py_compile tests/control_plane/test_scheduler_robustness_validation_contract.py",
        "pytest -q tests/control_plane/test_scheduler_robustness_validation_contract.py",
    ]


def test_fe3d_scorecard_keeps_machine_derived_blockers_in_lockstep_with_high_reliability_claim_blockers() -> None:
    sheet_dir = _find_fe3d_dir()
    scorecard = _load_json(sheet_dir / "artifacts" / "validation" / "robustness_scorecard_v1.json")
    blockers = _load_json(sheet_dir / "artifacts" / "validation" / "claim_blockers_v1.json")

    machine_fields = set(scorecard["machine_derived_fields"])
    hard_blockers = {entry["id"]: entry for entry in scorecard["hard_blockers"]}
    robust_claim_blockers = set(
        next(
            level["blocked_by"]
            for name, level in scorecard["claim_levels"].items()
            if name == "robust_enough_for_high_reliability_hosted_operation"
        )
    )
    canonical_blockers = {
        entry["id"]
        for entry in blockers["blockers"]
        if "robust_enough_for_high_reliability_hosted_operation" in entry["blocks"]
    }

    assert robust_claim_blockers == canonical_blockers
    assert set(hard_blockers) == canonical_blockers
    assert machine_fields >= {
        "duplicate_launch_count",
        "hidden_active_incident_count",
        "unreconciled_truth_divergence_count",
        "failed_negative_control_count",
        "major_failure_classes_unrun_count",
        "evidence_integrity_failure_count",
        "hosted_continuity_drill_failed_count",
    }
    assert all(entry["machine_derived"] is True for entry in hard_blockers.values())
    assert hard_blockers["hosted_continuity_drill_failed"]["evaluation_rule"] == (
        "hosted_continuity_drill_failed_count > 0"
    )


def test_fe3d_scenario_manifest_covers_all_major_failure_classes_once() -> None:
    sheet_dir = _find_fe3d_dir()
    scorecard = _load_json(sheet_dir / "artifacts" / "validation" / "robustness_scorecard_v1.json")
    manifest = _load_json(sheet_dir / "artifacts" / "validation" / "scenario_manifest_v1.json")

    scorecard_classes = {entry["id"] for entry in scorecard["major_failure_classes_v1"]}
    manifest_classes = {entry["failure_class_id"] for entry in manifest["major_failure_classes_v1"]}

    assert manifest_classes == scorecard_classes
    assert len(manifest["major_failure_classes_v1"]) == len(scorecard_classes)


def test_fe3d_verification_artifact_uses_real_validation_commands_and_seeded_smoke_inputs() -> None:
    verification = _load_json(
        _find_fe3d_dir() / "artifacts" / "verification" / "status_reconcile_verification.json"
    )

    assert verification["validation_commands"] == [
        "python -m py_compile tests/control_plane/test_scheduler_robustness_validation_contract.py",
        "pytest -q tests/control_plane/test_scheduler_robustness_validation_contract.py",
    ]
    assert all("..." not in command for command in verification["validation_commands"])
    assert verification["seeded_blocker_smoke"]["input_counts"] == {
        "duplicate_launch_count": 1,
        "hidden_active_incident_count": 0,
        "unreconciled_truth_divergence_count": 0,
        "failed_negative_control_count": 0,
        "major_failure_classes_unrun_count": 0,
        "evidence_integrity_failure_count": 0,
        "hosted_continuity_drill_failed_count": 0,
    }
    assert verification["seeded_blocker_smoke"]["derived_blockers"] == ["duplicate_launch"]
    assert verification["seeded_blocker_smoke"]["blocked_claim_levels"] == [
        "operationally_trustworthy",
        "robust_enough_for_high_reliability_hosted_operation",
    ]
