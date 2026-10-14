from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_planner_generation import PlannerGenerationError, generate_planner_output, select_output_mode


REPO_ROOT = Path(__file__).resolve().parents[2]
Q4T5_UID = "PIS-20260413-160257Z-Q4T5"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_q4t5_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{Q4T5_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {Q4T5_UID}")


def test_q4t5_meta_points_to_required_generation_schemas() -> None:
    meta = _load_json(_find_q4t5_dir() / "meta.json")

    assert meta["required_evidence"] == [
        "artifacts/planning/test_pack_output_schema_v1.json",
        "artifacts/planning/pis_family_output_schema_v1.json",
    ]


def test_q4t5_test_pack_schema_requires_scenario_level_evidence_and_claim_ceiling() -> None:
    schema = _load_json(_find_q4t5_dir() / "artifacts" / "planning" / "test_pack_output_schema_v1.json")

    assert set(schema["required_per_scenario_fields"]) >= {
        "scenario_id",
        "execution_class",
        "required_evidence",
        "negative_control",
        "disqualifying_incident",
        "claim_ceiling",
        "anti_gentle_bans",
    }
    assert schema["quality_rules"]["mode_selection_must_be_deterministic"] is True


def test_q4t5_pis_family_schema_requires_one_dominant_failure_story_per_child() -> None:
    schema = _load_json(_find_q4t5_dir() / "artifacts" / "planning" / "pis_family_output_schema_v1.json")

    assert set(schema["required_per_child_fields"]) >= {
        "child_key",
        "dominant_failure_story",
        "bounded_proof_target",
        "explicit_split_rationale",
        "max_scenario_breadth",
        "required_evidence",
        "claim_blockers",
        "anti_gentle_bans",
    }
    assert schema["quality_rules"]["one_dominant_failure_story_per_child"] is True


def test_q4t5_generator_emits_expected_dense_pack_output() -> None:
    q4t5_dir = _find_q4t5_dir()
    sample_input = _load_json(q4t5_dir / "artifacts" / "planning" / "generation_dense_input_v1.json")
    expected_output = _load_json(q4t5_dir / "artifacts" / "planning" / "generation_dense_output_v1.json")

    assert select_output_mode(sample_input)["mode"] == "dense_test_pack"
    assert generate_planner_output(sample_input) == expected_output


def test_q4t5_generator_emits_expected_child_pis_family_output() -> None:
    q4t5_dir = _find_q4t5_dir()
    sample_input = _load_json(q4t5_dir / "artifacts" / "planning" / "generation_family_input_v1.json")
    expected_output = _load_json(q4t5_dir / "artifacts" / "planning" / "generation_family_output_v1.json")

    assert select_output_mode(sample_input)["mode"] == "child_pis_family"
    assert generate_planner_output(sample_input) == expected_output


def test_q4t5_generator_rejects_missing_scenario_examples() -> None:
    q4t5_dir = _find_q4t5_dir()
    sample_input = _load_json(q4t5_dir / "artifacts" / "planning" / "generation_dense_input_v1.json")
    sample_input["scenario_families"][0]["scenario_examples"] = []

    with pytest.raises(PlannerGenerationError) as excinfo:
        generate_planner_output(sample_input)

    assert excinfo.value.code == "scenario_examples_missing"
