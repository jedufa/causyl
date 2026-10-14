from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_planner_extraction import ExtractionError, extract_complex_block


REPO_ROOT = Path(__file__).resolve().parents[2]
N2R3_UID = "PIS-20260413-160255Z-N2R3"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_n2r3_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{N2R3_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {N2R3_UID}")


def test_n2r3_meta_points_to_the_required_extraction_schema() -> None:
    meta = _load_json(_find_n2r3_dir() / "meta.json")

    assert meta["required_evidence"] == [
        "artifacts/planning/complex_block_extraction_schema_v1.json",
        "artifacts/planning/complex_block_extraction_sample_output_v1.json",
    ]


def test_n2r3_schema_requires_boundary_qualification_and_fail_closed_behavior() -> None:
    schema = _load_json(
        _find_n2r3_dir() / "artifacts" / "planning" / "complex_block_extraction_schema_v1.json"
    )

    gate = schema["qualification_gate"]
    assert gate["source_contract_uid"] == "PIS-20260413-161605Z-T7W8"
    assert gate["allowed_qualification_decisions"] == ["accept", "expand"]
    assert gate["reject_blocks_extraction"] is True

    fail_closed = {entry["id"]: entry for entry in schema["fail_closed_when"]}
    assert {
        "boundary_still_ambiguous_after_qualification",
        "truth_authorities_cannot_be_named",
        "major_truth_seam_has_fewer_than_two_independent_records_and_no_explicit_unknown_note",
        "claimed_invariants_or_operator_claims_cannot_be_stated",
    } == set(fail_closed)
    assert "must not invent an authority graph" in fail_closed["truth_authorities_cannot_be_named"]["reason"]


def test_n2r3_schema_demands_truth_seam_records_and_local_vs_broader_boundary_split() -> None:
    schema = _load_json(
        _find_n2r3_dir() / "artifacts" / "planning" / "complex_block_extraction_schema_v1.json"
    )

    sections = {entry["id"]: entry for entry in schema["required_output_sections"]}
    seams = sections["major_truth_seams"]
    assert seams["minimum_independent_records_per_major_truth_seam"] == 2
    assert set(seams["required_per_seam_fields"]) == {
        "seam_id",
        "seam_kind",
        "local_code_shape",
        "broader_truth_seam",
        "truth_authorities",
        "authority_precedence_note",
        "operator_claims_affected",
        "independent_records",
        "ambiguity_window",
        "intentionally_excluded_adjacent_seams",
        "why_the_seam_is_in_scope",
    }
    assert seams["independent_record_contract"]["must_not_fabricate_second_record_when_missing"] is True


def test_n2r3_schema_captures_blind_spots_exclusions_and_claim_ceiling() -> None:
    schema = _load_json(
        _find_n2r3_dir() / "artifacts" / "planning" / "complex_block_extraction_schema_v1.json"
    )

    sections = {entry["id"]: entry for entry in schema["required_output_sections"]}
    assert set(sections["existing_tests_and_blind_spots"]["required_fields"]) == {
        "existing_tests_reviewed",
        "likely_blind_spots",
        "why_current_tests_may_miss_them",
        "blind_spot_claim_impact",
        "missing_negative_controls_or_absence_note",
    }
    assert set(schema["intentional_exclusion_contract"]["required_fields"]) == {
        "seam_id",
        "reason_excluded_for_this_child_scope",
        "what_claim_or_recovery_story_it_could_still_change",
        "which_sibling_or_later_phase_owns_it_if_known",
    }
    claim_boundary = schema["extraction_claim_boundary"]
    assert claim_boundary["default_maximum_supported_claim"] == "risk_structure_extracted_for_planning"
    assert set(claim_boundary["extraction_output_alone_never_proves"]) >= {
        "runtime_robustness",
        "operator_truth_correctness",
        "source_of_truth_correctness",
        "scheduler_execution_readiness",
        "dut_health_improved",
    }


def test_n2r3_extractor_emits_the_expected_sample_output() -> None:
    n2r3_dir = _find_n2r3_dir()
    sample_input = _load_json(
        n2r3_dir / "artifacts" / "planning" / "complex_block_extraction_sample_input_v1.json"
    )
    expected_output = _load_json(
        n2r3_dir / "artifacts" / "planning" / "complex_block_extraction_sample_output_v1.json"
    )

    assert extract_complex_block(sample_input) == expected_output


def test_n2r3_extractor_fails_closed_on_ambiguous_boundary() -> None:
    n2r3_dir = _find_n2r3_dir()
    sample_input = _load_json(
        n2r3_dir / "artifacts" / "planning" / "complex_block_extraction_sample_input_v1.json"
    )
    sample_input["qualified_boundary_summary"]["decision"] = "reject"

    with pytest.raises(ExtractionError) as excinfo:
        extract_complex_block(sample_input)

    assert excinfo.value.code == "boundary_still_ambiguous_after_qualification"


def test_n2r3_extractor_rejects_major_truth_seam_with_only_one_record() -> None:
    n2r3_dir = _find_n2r3_dir()
    sample_input = _load_json(
        n2r3_dir / "artifacts" / "planning" / "complex_block_extraction_sample_input_v1.json"
    )
    sample_input["major_truth_seams"][0]["independent_records"] = [
        sample_input["major_truth_seams"][0]["independent_records"][0]
    ]

    with pytest.raises(ExtractionError) as excinfo:
        extract_complex_block(sample_input)

    assert excinfo.value.code == (
        "major_truth_seam_has_fewer_than_two_independent_records_and_no_explicit_unknown_note"
    )
