from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
M1Q2_UID = "PIS-20260413-160254Z-M1Q2"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_m1q2_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{M1Q2_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {M1Q2_UID}")


def test_m1q2_meta_points_to_the_required_contract_artifacts() -> None:
    meta = _load_json(_find_m1q2_dir() / "meta.json")

    assert meta["required_evidence"] == [
        "artifacts/planning/verification_planner_contract_v1.json",
        "artifacts/planning/verification_claim_ceilings_v1.json",
        "artifacts/verification/status_reconcile_verification.json",
    ]
    assert meta["status"] == "verified"
    assert meta["evidence"]["artifact"] == "artifacts/verification/status_reconcile_verification.json"


def test_m1q2_planner_contract_requires_unrun_scope_to_reduce_claims() -> None:
    contract = _load_json(
        _find_m1q2_dir() / "artifacts" / "planning" / "verification_planner_contract_v1.json"
    )

    mandatory_inputs = {entry["id"] for entry in contract["mandatory_inputs_before_scenario_generation"]}
    assert mandatory_inputs >= {
        "dut_boundary_summary",
        "truth_authorities_and_precedence",
        "operator_facing_claims_under_attack",
        "independent_records_that_can_disagree",
        "ambiguity_windows_or_missing_window_note",
        "mutation_points_and_side_effects",
        "existing_validation_and_blind_spot_summary",
    }

    listed_unrun = contract["coverage_rules"]["listed_but_unrun_scenarios"]
    assert listed_unrun == {
        "classification": "uncovered_scope",
        "counts_as_coverage": False,
        "must_record_claim_impact": True,
        "must_not_be_reported_as_neutral_bookkeeping": True,
    }
    assert "negative_control_plan" in contract["coverage_rules"]["required_plan_fields"]
    assert "disqualifying_incidents" in contract["coverage_rules"]["required_plan_fields"]


def test_m1q2_claim_ceilings_block_runtime_overclaim_when_planner_evidence_is_soft() -> None:
    ceilings = _load_json(
        _find_m1q2_dir() / "artifacts" / "planning" / "verification_claim_ceilings_v1.json"
    )

    rules = {entry["id"]: entry for entry in ceilings["ceiling_rules"]}
    assert {
        "planning_only_evidence",
        "unrun_scenarios_present",
        "missing_negative_controls",
        "missing_independent_truth_comparison",
        "missing_repetition_or_overlap_plan",
        "missing_bounded_ambiguity_window",
        "missing_scheduler_ready_contract_proof",
    } <= set(rules)

    assert rules["planning_only_evidence"]["maximum_supported_claim"] == "planning_quality_improved_only"
    assert "runtime_robustness" in rules["planning_only_evidence"]["forbids"]
    assert rules["unrun_scenarios_present"]["maximum_supported_claim"] == "planning_quality_improved_only"
    assert "coverage_complete" in rules["unrun_scenarios_present"]["forbids"]
    assert "source_of_truth_correctness" in rules["missing_independent_truth_comparison"]["forbids"]
    assert "operator_truth_correctness" in rules["missing_bounded_ambiguity_window"]["forbids"]
    assert rules["missing_scheduler_ready_contract_proof"]["maximum_supported_claim"] == (
        "scenario_pack_structurally_ready"
    )
    assert "scheduler_execution_proven" in rules["missing_scheduler_ready_contract_proof"]["forbids"]


def test_m1q2_planner_contract_explicitly_forbids_treating_planning_as_runtime_proof() -> None:
    contract = _load_json(
        _find_m1q2_dir() / "artifacts" / "planning" / "verification_planner_contract_v1.json"
    )

    boundary = contract["planner_claim_boundary"]
    assert boundary["must_state_maximum_supported_claim_using_planner_output_alone"] is True
    assert boundary["default_maximum_claim_without_runtime_execution"] == "planning_quality_improved_only"
    assert set(boundary["planning_output_alone_never_proves"]) >= {
        "runtime_robustness",
        "operator_truth_correctness",
        "source_of_truth_correctness",
        "dut_health_improved",
        "repair_loop_effectiveness",
    }
