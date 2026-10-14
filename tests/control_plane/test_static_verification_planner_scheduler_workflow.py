from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
W1Z2_UID = "PIS-20260413-161608Z-W1Z2"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_w1z2_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{W1Z2_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {W1Z2_UID}")


def test_w1z2_meta_points_to_the_required_scheduler_workflow_contract() -> None:
    meta = _load_json(_find_w1z2_dir() / "meta.json")

    assert meta["required_evidence"] == [
        "artifacts/planning/planner_scheduler_workflow_contract_v1.json",
    ]


def test_w1z2_contract_defines_fail_closed_admission_and_deterministic_selection() -> None:
    contract = _load_json(
        _find_w1z2_dir() / "artifacts" / "planning" / "planner_scheduler_workflow_contract_v1.json"
    )

    admission = contract["scheduler_admission_contract"]
    assert set(admission["required_inputs"]) >= {
        "qualified_block_boundary",
        "qualification_decision",
        "planning_mode",
        "operator_request_fingerprint",
        "repo_commit_sha",
        "planner_generator_version",
    }
    assert admission["must_not_infer_from_hidden_memory"] is True
    assert "qualification_decision is not accept or expand" in admission["must_fail_closed_when"]

    selection = contract["deterministic_block_selection"]
    assert selection["selected_block_fingerprint_fields"] == [
        "selected_boundary_summary",
        "included_truth_seams",
        "planning_mode",
    ]
    assert "must not silently widen or narrow the block" in " ".join(selection["selection_rules"])


def test_w1z2_contract_defines_dedupe_idempotence_and_retry_boundaries() -> None:
    contract = _load_json(
        _find_w1z2_dir() / "artifacts" / "planning" / "planner_scheduler_workflow_contract_v1.json"
    )

    dedupe = contract["dedupe_and_idempotence"]
    assert dedupe["dedupe_key_fields"] == [
        "operator_request_fingerprint",
        "selected_block_fingerprint",
        "planning_mode",
        "repo_commit_sha",
        "planner_generator_version",
    ]
    assert "one active or resumable planner run per dedupe key" == dedupe["dedupe_scope"]
    assert "same logical run identity" in " ".join(dedupe["idempotent_write_rules"])

    retry_policy = contract["retry_policy"]
    budget = retry_policy["retry_budget_contract"]
    assert budget["max_scheduler_retries_per_dedupe_key"] == 1
    assert "retryable execution faults only" == budget["budget_scope"]
    assert "qualification contradiction that prevents an honest selected boundary" in (
        retry_policy["terminal_failure_classes"]
    )


def test_w1z2_contract_defines_partial_failure_states_and_stale_artifact_invalidation() -> None:
    contract = _load_json(
        _find_w1z2_dir() / "artifacts" / "planning" / "planner_scheduler_workflow_contract_v1.json"
    )

    states = set(contract["run_states"])
    assert {
        "queued",
        "running",
        "completed_current",
        "failed_retryable",
        "failed_terminal",
        "blocked_waiting_for_operator",
        "stale_invalidated",
        "superseded",
    } <= states

    partial_failure = {entry["state"]: entry for entry in contract["partial_failure_and_resume"]["partial_failure_states"]}
    assert set(partial_failure) == {
        "failed_retryable",
        "blocked_waiting_for_operator",
        "stale_invalidated",
    }
    assert set(contract["partial_failure_and_resume"]["must_not_resume_when"]) == {
        "selected_block_fingerprint changed",
        "planning_mode changed",
        "repo_commit_sha changed",
        "planner_generator_version changed",
    }

    artifact_rules = contract["artifact_currentness_and_output_locations"]
    assert artifact_rules["logical_locations"]["current_manifest_pointer"].endswith("current_manifest.json")
    assert set(artifact_rules["stale_invalidation_triggers"]) == {
        "repo_commit_sha changed",
        "selected_block_fingerprint changed",
        "planning_mode changed",
        "planner_generator_version changed",
        "review gate marked the prior run superseded by a newer accepted rerun",
    }
    assert "stale_invalidated from failed_terminal" in " ".join(artifact_rules["stale_invalidation_rules"])
