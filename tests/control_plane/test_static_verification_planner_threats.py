from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_planner_threats import ThreatModelError, compile_threat_matrix


REPO_ROOT = Path(__file__).resolve().parents[2]
P3S4_UID = "PIS-20260413-160256Z-P3S4"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_p3s4_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{P3S4_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {P3S4_UID}")


def test_p3s4_meta_points_to_required_threat_artifacts() -> None:
    meta = _load_json(_find_p3s4_dir() / "meta.json")

    assert meta["required_evidence"] == [
        "artifacts/planning/threat_matrix_schema_v1.json",
        "artifacts/planning/scenario_family_schema_v1.json",
    ]


def test_p3s4_schema_requires_grouped_prioritized_harsh_families() -> None:
    schema = _load_json(_find_p3s4_dir() / "artifacts" / "planning" / "threat_matrix_schema_v1.json")

    assert set(schema["required_threat_classes"]) == {
        "contradiction",
        "race_or_timing_window",
        "stale_observation",
        "restore_or_replay",
        "accumulation_or_overlap",
        "false_calm",
        "operator_misuse",
        "silent_paralysis",
    }
    assert schema["prioritization_contract"]["must_group_by_failure_class_not_code_shape"] is True
    assert schema["prioritization_contract"]["must_not_emit_unprioritized_flat_brainstorm"] is True
    assert schema["repetition_overlap_accumulation_contract"]["must_not_waive_by_default"] is True
    assert schema["relevance_contract"]["silent_paralysis_path_required_when_relevant"] is True
    assert schema["relevance_contract"]["unsafe_action_path_required_when_relevant"] is True


def test_p3s4_scenario_family_schema_requires_bug_yield_reachability_and_claim_attack_fields() -> None:
    schema = _load_json(_find_p3s4_dir() / "artifacts" / "planning" / "scenario_family_schema_v1.json")

    assert set(schema["required_fields"]) >= {
        "operator_claim_under_attack",
        "independent_records_that_can_disagree",
        "negative_control_plan",
        "disqualifying_incidents",
        "loophole_closure",
        "bug_yield_hypothesis",
        "production_reachability",
        "contamination_or_interference_notes",
        "repetition_overlap_accumulation_plan",
        "execution_readiness",
        "silent_paralysis_path",
        "unsafe_action_path",
    }
    assert schema["family_quality_rules"]["minimum_independent_records"] == 2


def test_p3s4_compiler_emits_the_expected_sample_output() -> None:
    p3s4_dir = _find_p3s4_dir()
    sample_input = _load_json(p3s4_dir / "artifacts" / "planning" / "threat_matrix_sample_input_v1.json")
    expected_output = _load_json(p3s4_dir / "artifacts" / "planning" / "threat_matrix_sample_output_v1.json")

    assert compile_threat_matrix(sample_input) == expected_output


def test_p3s4_compiler_fails_closed_when_stateful_family_lacks_repetition_plan() -> None:
    p3s4_dir = _find_p3s4_dir()
    sample_input = _load_json(p3s4_dir / "artifacts" / "planning" / "threat_matrix_sample_input_v1.json")
    candidate = sample_input["scenario_family_candidates"][0]
    candidate["repetition_overlap_accumulation_plan"] = ""

    with pytest.raises(ThreatModelError) as excinfo:
        compile_threat_matrix(sample_input)

    assert excinfo.value.code == "missing_repetition_overlap_accumulation_plan"


def test_p3s4_compiler_rejects_missing_silent_paralysis_path_when_relevant() -> None:
    p3s4_dir = _find_p3s4_dir()
    sample_input = _load_json(p3s4_dir / "artifacts" / "planning" / "threat_matrix_sample_input_v1.json")
    candidate = sample_input["scenario_family_candidates"][1]
    candidate["silent_paralysis_path"] = ""

    with pytest.raises(ThreatModelError) as excinfo:
        compile_threat_matrix(sample_input)

    assert excinfo.value.code == "missing_silent_paralysis_path"
