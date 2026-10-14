from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import job_shape_contract
from infra.controlplane import job_shape_lifecycle_contract as contract


def test_job_shape_lifecycle_contract_v1_exposes_expected_shape_binding() -> None:
    payload = contract.job_shape_lifecycle_contract_v1()

    assert payload.version == "job_shape_lifecycle_contract_v1"
    assert payload.uid == "PIS-20260406-053811Z-339F"
    assert payload.canonical_artifact_rel.as_posix() == "governance/job_shape_lifecycle_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_job_shape_lifecycle_recovery_matrix_v1.md"
    assert payload.canonical_shape_contract_rel.as_posix() == "governance/canonical_job_shape_contract_v1.json"
    assert payload.shape_names == job_shape_contract.CANONICAL_JOB_SHAPES


def test_job_shape_lifecycle_helper_reports_summary_buckets_and_recovery_classes() -> None:
    assert contract.summary_bucket_for_shape_state("verification_loop_job", "failed_verification") == "terminal_evidence_bearing"
    assert contract.summary_bucket_for_shape_state("fanout_job", "blocked") == "terminal_operator_actionable"
    assert contract.summary_bucket_for_shape_state("experiment_run_job", "running") == "active_progress"
    assert contract.recovery_class_for_shape("experiment_run_job") == "resume_from_durable_checkpoint_if_present"
    projection = contract.lifecycle_projection_for_shape_record("experiment_run_job", "timed_out")
    assert projection["canonical_state"] == "error"
    assert projection["summary_bucket"] == "terminal_operator_actionable"


def test_job_shape_lifecycle_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/job_shape_lifecycle_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_shape_contract_rel"] == contract.CANONICAL_SHAPE_CONTRACT_REL.as_posix()
    assert tuple(payload["shapes"].keys()) == job_shape_contract.CANONICAL_JOB_SHAPES
    assert payload["shapes"]["campaign_job"]["launch_policy"] == "forbidden_until_implemented"
    assert payload["shapes"]["experiment_run_job"]["interruption_state"] == "uncertain_interrupted"
    assert "blocked" in payload["shapes"]["experiment_run_job"]["operator_actionable_terminal_states"]
    assert payload["terminal_state_definitions"]["blocked"]["summary_class"] == "operator_actionable_terminal"
    assert any(
        row["canonical_state"] == "interrupted"
        for row in payload["shapes"]["verification_loop_job"]["current_repo_state_mappings"]
    )
    assert "abandoned" not in payload["shapes"]["experiment_run_job"]["terminal_states"]
    assert "abandoned" not in payload["shapes"]["experiment_run_job"]["operator_actionable_terminal_states"]


def test_job_shape_lifecycle_contract_validation_passes() -> None:
    assert contract.validate_job_shape_lifecycle_contract_v1() == ()


def test_job_shape_lifecycle_contract_validation_rejects_recovery_definition_drift(tmp_path: Path) -> None:
    payload = json.loads(Path("governance/job_shape_lifecycle_contract_v1.json").read_text(encoding="utf-8"))
    payload["recovery_class_definitions"]["resume_from_durable_checkpoint_if_present"] = "guess from history"

    contract_path = tmp_path / "governance" / "job_shape_lifecycle_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    issues = contract.validate_job_shape_lifecycle_contract_v1(repo_root=tmp_path)
    assert (
        "resume_from_durable_checkpoint_if_present definition drifted from the runtime helper contract"
        in issues
    )


def test_job_shape_lifecycle_contract_validation_rejects_conflicting_raw_state_mapping(tmp_path: Path) -> None:
    payload = json.loads(Path("governance/job_shape_lifecycle_contract_v1.json").read_text(encoding="utf-8"))
    payload["shapes"]["verification_loop_job"]["current_repo_state_mappings"].append(
        {
            "surface": "scheduled_job_receipt.status",
            "canonical_state": "blocked",
            "values": ["ok"],
        }
    )

    contract_path = tmp_path / "governance" / "job_shape_lifecycle_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    issues = contract.validate_job_shape_lifecycle_contract_v1(repo_root=tmp_path)
    assert (
        "verification_loop_job raw state ok on scheduled_job_receipt.status cannot map to both succeeded and blocked"
        in issues
    )


def test_job_shape_lifecycle_contract_validation_rejects_illegal_interruption_state(tmp_path: Path) -> None:
    payload = json.loads(Path("governance/job_shape_lifecycle_contract_v1.json").read_text(encoding="utf-8"))
    payload["shapes"]["fanout_job"]["interruption_state"] = "ghost_state"

    contract_path = tmp_path / "governance" / "job_shape_lifecycle_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    issues = contract.validate_job_shape_lifecycle_contract_v1(repo_root=tmp_path)
    assert "fanout_job interruption_state must be one of the shape's legal lifecycle states" in issues


def test_job_shape_lifecycle_contract_rejects_illegal_state_and_recovery_combinations() -> None:
    with pytest.raises(KeyError, match="illegal lifecycle state for verification_loop_job"):
        contract.assert_valid_shape_state_combination("verification_loop_job", "skipped")

    with pytest.raises(ValueError, match="illegal recovery class for experiment_run_job"):
        contract.assert_valid_shape_state_combination(
            "experiment_run_job",
            "running",
            recovery_class="rerun_from_last_iteration_boundary",
        )


def test_experiment_run_job_states_stay_aligned_with_run_record_lifecycle_contract() -> None:
    lifecycle = contract.lifecycle_for_shape("experiment_run_job")

    assert tuple(lifecycle["active_states"]) == ("launching", "running", "uncertain_interrupted")
    assert tuple(lifecycle["terminal_states"]) == (
        "succeeded",
        "failed",
        "skipped",
        "blocked",
        "deferred",
        "cancelled",
        "interrupted",
        "error",
    )
    assert "abandoned" not in lifecycle["terminal_states"]
