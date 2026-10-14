from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
T7W8_UID = "PIS-20260413-161605Z-T7W8"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_t7w8_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{T7W8_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {T7W8_UID}")


def test_t7w8_meta_points_to_the_required_block_boundary_contract() -> None:
    meta = _load_json(_find_t7w8_dir() / "meta.json")

    assert "artifacts/planning/block_boundary_qualification_contract_v1.json" in meta["required_evidence"]
    if meta["status"] == "verified":
        assert "artifacts/verification/status_reconcile_verification.json" in meta["required_evidence"]


def test_t7w8_contract_requires_accept_expand_reject_and_fail_closed_rejection() -> None:
    contract = _load_json(
        _find_t7w8_dir() / "artifacts" / "planning" / "block_boundary_qualification_contract_v1.json"
    )

    decisions = {entry["decision"]: entry for entry in contract["qualification_decisions"]}
    assert {"accept", "expand", "reject"} == set(decisions)
    assert "adjacent seams were reviewed" in " ".join(decisions["accept"]["allowed_when"])
    assert decisions["reject"]["must_fail_closed_before"] == [
        "truth_boundary_extraction",
        "scenario_generation",
        "scheduler_mapping",
    ]
    assert set(decisions["expand"]["required_output_fields"]) == {
        "proposed_expanded_boundary",
        "adjacent_seams_requiring_inclusion",
        "operator_claim_if_left_narrow",
        "why_the_original_boundary_would_misplan",
    }


def test_t7w8_contract_requires_adjacent_seam_review_and_misplanning_records() -> None:
    contract = _load_json(
        _find_t7w8_dir() / "artifacts" / "planning" / "block_boundary_qualification_contract_v1.json"
    )

    seam_review = contract["adjacent_truth_seam_review"]
    assert len(seam_review["must_scan_for"]) >= 5
    assert set(seam_review["required_per_seam_fields"]) == {
        "seam_id",
        "seam_kind",
        "relationship_to_selected_block",
        "included_in_boundary",
        "claims_affected",
        "reason_if_excluded",
        "misplanning_risk_if_excluded",
    }
    assert "independent record" in " ".join(seam_review["cannot_be_reported_as_optional_context_when"])

    misplanning = contract["misplanning_record_contract"]
    assert misplanning["required_when_decision_is_not_accept"] is True
    assert set(misplanning["required_fields"]) == {
        "operator_facing_claim_at_risk",
        "why_a_narrow_boundary_would_mislead_planning",
        "which_truth_seam_or_authority_was_missing_or_ambiguous",
        "what_kind_of_false_confidence_or_missed_scenario_would_follow",
    }


def test_t7w8_contract_rejection_rules_block_fake_boundaries() -> None:
    contract = _load_json(
        _find_t7w8_dir() / "artifacts" / "planning" / "block_boundary_qualification_contract_v1.json"
    )

    rules = {entry["id"]: entry for entry in contract["rejection_rules"]}
    assert {
        "authority_ambiguous",
        "prompt_shaped_not_risk_shaped",
        "adjacent_seam_missing",
        "operator_claim_unknown",
    } <= set(rules)
    assert "fabricate a clean truth model" in rules["authority_ambiguous"]["required_reason"]
    assert "theatrically serious but operationally weak scenarios" in rules["prompt_shaped_not_risk_shaped"][
        "required_reason"
    ]
    assert "understate" in rules["adjacent_seam_missing"]["required_reason"]
    assert "cannot tell whether the boundary is honest" in rules["operator_claim_unknown"]["required_reason"]
