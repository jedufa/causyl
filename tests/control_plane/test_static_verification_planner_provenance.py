from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
U8X9_UID = "PIS-20260413-161606Z-U8X9"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_u8x9_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{U8X9_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {U8X9_UID}")


def test_u8x9_meta_points_to_required_provenance_and_drift_contracts() -> None:
    meta = _load_json(_find_u8x9_dir() / "meta.json")

    expected = [
        "artifacts/planning/planner_provenance_contract_v1.json",
        "artifacts/planning/planner_drift_invalidation_contract_v1.json",
    ]
    if meta["status"] == "verified":
        expected.append("artifacts/verification/status_reconcile_verification.json")

    assert meta["required_evidence"] == expected


def test_u8x9_provenance_contract_requires_commit_selector_generator_and_upstream_hashes() -> None:
    contract = _load_json(
        _find_u8x9_dir() / "artifacts" / "planning" / "planner_provenance_contract_v1.json"
    )

    run_identity = contract["required_run_identity"]
    assert run_identity["must_fail_closed_when_missing"] is True
    assert run_identity["required_fields"] == [
        "repo_commit_sha",
        "selected_block_selector",
        "selected_block_fingerprint",
        "qualification_decision",
        "planning_mode",
        "planner_generator_version",
    ]

    artifact_provenance = contract["required_artifact_provenance"]
    assert artifact_provenance["source_run_identity_fields_must_repeat"] == [
        "repo_commit_sha",
        "selected_block_selector",
        "selected_block_fingerprint",
        "planning_mode",
        "planner_generator_version",
    ]
    assert set(artifact_provenance["minimum_authoritative_inputs"]) == {
        "qualification_output",
        "complex_block_extraction",
    }
    assert "freshest timestamp" in " ".join(artifact_provenance["forbidden_shortcuts"])


def test_u8x9_provenance_contract_marks_mixed_input_current_bundles_as_planner_defects() -> None:
    contract = _load_json(
        _find_u8x9_dir() / "artifacts" / "planning" / "planner_provenance_contract_v1.json"
    )

    defects = contract["mixed_input_defect_classification"]
    assert defects["classification"] == "planner_defect"
    assert set(defects["defect_triggers"]) == {
        "repo_commit_sha differs across artifacts in one reported current bundle",
        "selected_block_selector or selected_block_fingerprint differs across artifacts in one reported current bundle",
        "planner_generator_version differs across artifacts in one reported current bundle",
        "qualification output or extraction artifact content hash differs from the hashes recorded in downstream artifact provenance",
        "an artifact missing required provenance fields is reported as current or reusable",
    }
    assert "provenance_mismatch" in " ".join(defects["required_response"])


def test_u8x9_drift_contract_requires_regeneration_after_risk_structure_or_provenance_drift() -> None:
    contract = _load_json(
        _find_u8x9_dir() / "artifacts" / "planning" / "planner_drift_invalidation_contract_v1.json"
    )

    basis = contract["risk_map_currentness_basis"]
    assert basis["currentness_fields"] == [
        "repo_commit_sha",
        "selected_block_selector",
        "selected_block_fingerprint",
        "planning_mode",
        "planner_generator_version",
        "qualification_output_sha256",
        "complex_block_extraction_sha256",
    ]
    assert "timestamp alone" in " ".join(basis["must_not_infer_currentness_from"])

    drift_classes = {entry["id"]: entry for entry in contract["risk_structure_drift_classes"]}
    assert set(drift_classes) == {
        "boundary_selector_drift",
        "authority_map_drift",
        "truth_seam_drift",
        "blind_spot_drift",
        "generator_or_schema_drift",
    }

    assert set(contract["stale_invalidation_triggers"]) == {
        "repo_commit_sha changed",
        "selected_block_selector changed",
        "selected_block_fingerprint changed",
        "planning_mode changed",
        "qualification_output_sha256 changed",
        "complex_block_extraction_sha256 changed",
        "planner_generator_version changed",
        "review gate marked the prior risk map superseded by a newer accepted rerun",
    }

    decisions = {entry["decision"]: entry for entry in contract["reuse_decision_contract"]["allowed_decisions"]}
    assert set(decisions) == {"reuse_as_current", "reuse_for_audit_only", "regenerate_required"}
    assert "all currentness fields still match" in decisions["reuse_as_current"]["allowed_when"]
    assert "any stale_invalidation_trigger fired" in decisions["regenerate_required"]["allowed_when"]
    assert "upstream extraction hashes differ" in " ".join(contract["reuse_decision_contract"]["must_not_allow"])

    stale_reuse = contract["mixed_input_or_stale_reuse_classification"]
    assert stale_reuse["classification"] == "planner_defect"
    assert "different planner_generator_version values" in " ".join(stale_reuse["defect_examples"])
    assert "stale_invalidated" in " ".join(stale_reuse["required_system_behavior"])
