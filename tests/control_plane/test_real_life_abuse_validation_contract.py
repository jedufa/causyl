from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
G7S0_UID = "PIS-20260413-111001Z-G7S0"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_g7s0_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{G7S0_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing G7S0 ledger directory for {G7S0_UID}")


def test_g7s0_contract_keeps_parent_child_minimums_and_failure_classes_in_sync() -> None:
    sheet_dir = _find_g7s0_dir()
    scorecard = _load_json(sheet_dir / "artifacts" / "validation" / "abuse_scorecard_v1.json")
    manifest = _load_json(sheet_dir / "artifacts" / "validation" / "abuse_scenario_manifest_v1.json")

    child_contracts = {entry["child_uid"]: entry for entry in scorecard["child_execution_contracts_v1"]}
    failure_classes = {entry["required_child_uid"] for entry in scorecard["major_failure_classes_v1"]}
    manifest_children = {entry["planned_child_uid"] for entry in manifest["major_failure_classes_v1"]}

    assert manifest_children == failure_classes
    assert set(child_contracts) >= failure_classes
    assert child_contracts["PIS-20260413-110625Z-A1L4"]["minimum_scenario_count"] >= 8
    assert child_contracts["PIS-20260413-110627Z-C3N6"]["minimum_repetitions"] >= 3
    assert child_contracts["PIS-20260413-110626Z-B2M5"]["minimum_overlap_or_concurrency_volume"] >= 4


def test_g7s0_scorecard_and_claim_blockers_keep_machine_derived_hard_blockers_in_lockstep() -> None:
    sheet_dir = _find_g7s0_dir()
    scorecard = _load_json(sheet_dir / "artifacts" / "validation" / "abuse_scorecard_v1.json")
    blockers = _load_json(sheet_dir / "artifacts" / "validation" / "abuse_claim_blockers_v1.json")

    hard_blockers = {entry["id"]: entry for entry in scorecard["hard_blockers"]}
    safe_claim_blockers = set(scorecard["claim_levels"]["safe_for_unsupervised_operation_within_declared_bounds"]["blocked_by"])
    canonical = {entry["id"] for entry in blockers["blockers"]}

    assert set(hard_blockers) == canonical
    assert safe_claim_blockers == canonical
    assert all(entry["machine_derived"] is True for entry in hard_blockers.values())


def test_g7s0_evidence_integrity_and_negative_controls_forbid_theater_and_reused_evidence_overclaim() -> None:
    sheet_dir = _find_g7s0_dir()
    integrity = _load_json(
        sheet_dir / "artifacts" / "validation" / "abuse_evidence_integrity_contract_v1.json"
    )
    negative = _load_json(
        sheet_dir / "artifacts" / "validation" / "abuse_negative_control_manifest_v1.json"
    )

    integrity_failure_ids = {entry["id"] for entry in integrity["integrity_failure_rules"]}
    negative_ids = {entry["negative_control_id"] for entry in negative["required_negative_controls"]}

    assert "scenario_listed_but_unrun_without_claim_impact" in integrity_failure_ids
    assert "reused_evidence_presented_as_independent_confirmation" in integrity_failure_ids
    assert "NEGCTRL_ABUSE_UNRUN_SCENARIO_THEATER_SEED_V1" in negative_ids
    assert negative["policy"]["escaped_negative_control_blocks_safe_for_unsupervised_operation_within_declared_bounds"] is True


def test_g7s0_verified_contract_reconciles_to_non_draft_release_state() -> None:
    meta = _load_json(_find_g7s0_dir() / "meta.json")

    assert meta["status"] == "verified"
    assert meta["release_state"] == "released"
    assert meta["evidence"]["artifact_path"] == "artifacts/verification/status_reconcile_verification.json"
    assert meta["evidence"]["validation_commands"] == [
        "python -m py_compile tests/control_plane/test_real_life_abuse_validation_contract.py",
        "pytest -q tests/control_plane/test_real_life_abuse_validation_contract.py",
    ]
