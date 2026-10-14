from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import job_budget_cost_ledger_contract
from infra.controlplane import recursive_spawn_policy_contract as contract


def _parent_budget_balance(*, remaining_minor_units: int = 900) -> dict:
    allocated_minor_units = remaining_minor_units + 100
    return job_budget_cost_ledger_contract.replay_job_budget_balance(
        [
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "allocation_uid": "alloc_recursive_seed_001",
                "job_uid": "job_recursive_parent_001",
                "recorded_at_utc": "2026-04-09T15:00:00Z",
                "amount_minor_units": allocated_minor_units,
                "unit": "usd_cents",
                "allocation_reason_code": "initial_grant",
            }
        ],
        [
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "cost_event_uid": "cost_recursive_seed_001",
                "job_uid": "job_recursive_parent_001",
                "recorded_at_utc": "2026-04-09T15:05:00Z",
                "amount_minor_units": 100,
                "unit": "usd_cents",
                "direction": "debit",
                "cost_reason_code": "usage",
            }
        ],
        job_uid="job_recursive_parent_001",
    )


def _base_request() -> dict:
    return {
        "parent_job_shape": "fanout_job",
        "child_job_shape": "verification_loop_job",
        "planning_mode": "dry_run_only",
        "campaign_phase_category": "",
        "ancestry_path": ["node_fanout_root"],
        "ancestor_job_shapes": ["fanout_job"],
        "parent_budget_balance": _parent_budget_balance(),
        "reserved_budget_minor_units": 150,
        "child_budget_request_minor_units": 300,
        "minimum_parent_remaining_minor_units": 100,
    }


def test_recursive_spawn_policy_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.recursive_spawn_policy_contract_v1()

    assert payload.version == "recursive_spawn_policy_contract_v1"
    assert payload.uid == "PIS-20260409-154915Z-5F24"
    assert payload.canonical_artifact_rel.as_posix() == "governance/recursive_spawn_policy_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_recursive_spawn_policy_v1.md"
    assert payload.planning_parent_shapes == ("fanout_job", "campaign_job", "delegation_tree_job")
    assert payload.allowed_campaign_phase_categories == ("planning", "repair", "review")


def test_recursive_spawn_policy_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/recursive_spawn_policy_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert payload["job_shape_relationship_contract_rel"] == contract.JOB_SHAPE_RELATIONSHIP_CONTRACT_REL.as_posix()
    assert payload["budget_ledger_contract_rel"] == contract.BUDGET_LEDGER_CONTRACT_REL.as_posix()
    assert tuple(payload["planning_mode_vocab"]) == contract.PLANNING_MODE_VOCAB
    assert tuple(payload["planning_parent_shapes"]) == contract.PLANNING_PARENT_SHAPES
    assert payload["proposed_child_depth_caps"] == contract.PROPOSED_CHILD_DEPTH_CAPS
    assert tuple(payload["stop_condition_codes"]) == contract.STOP_CONDITION_CODES


def test_validate_recursive_spawn_policy_contract_v1_returns_no_issues() -> None:
    assert contract.validate_recursive_spawn_policy_contract_v1() == ()


def test_evaluate_recursive_spawn_request_accepts_planning_legal_budgeted_child() -> None:
    result = contract.evaluate_recursive_spawn_request(_base_request())

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["relationship"]["legality"] == "allowed"
    assert result["proposed_child_depth"] == 1
    assert result["budget_decision"]["spendable_for_child_minor_units"] == 650
    assert result["budget_decision"]["propagated_child_allocation_minor_units"] == 300
    assert result["budget_decision"]["parent_remaining_after_child_minor_units"] == 600
    assert result["budget_decision"]["budget_source_contract_uid"] == job_budget_cost_ledger_contract.UID


def test_evaluate_recursive_spawn_request_rejects_parent_without_planning_authority() -> None:
    raw = _base_request()
    raw["parent_job_shape"] = "verification_loop_job"
    raw["ancestor_job_shapes"] = ["verification_loop_job"]

    result = contract.evaluate_recursive_spawn_request(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "parent_shape_not_planning_eligible" in codes
    assert "relationship_forbidden" in codes


def test_evaluate_recursive_spawn_request_rejects_depth_cap_and_ancestry_cycle_hits() -> None:
    raw = _base_request()
    raw["parent_job_shape"] = "delegation_tree_job"
    raw["child_job_shape"] = "delegation_tree_job"
    raw["ancestry_path"] = ["node_root", "node_branch", "node_root"]
    raw["ancestor_job_shapes"] = ["delegation_tree_job", "delegation_tree_job", "delegation_tree_job"]

    result = contract.evaluate_recursive_spawn_request(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "ancestry_cycle_detected" in codes
    assert "depth_cap_exceeded" not in codes


def test_evaluate_recursive_spawn_request_rejects_over_depth_for_child_shape() -> None:
    raw = _base_request()
    raw["parent_job_shape"] = "delegation_tree_job"
    raw["child_job_shape"] = "delegation_tree_job"
    raw["ancestry_path"] = ["node_root", "node_branch", "node_leaf", "node_parent"]
    raw["ancestor_job_shapes"] = [
        "delegation_tree_job",
        "delegation_tree_job",
        "delegation_tree_job",
        "delegation_tree_job",
    ]

    result = contract.evaluate_recursive_spawn_request(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "depth_cap_exceeded" in codes


def test_evaluate_recursive_spawn_request_rejects_over_budget_requests_with_explicit_reason() -> None:
    raw = _base_request()
    raw["parent_budget_balance"] = _parent_budget_balance(remaining_minor_units=220)
    raw["reserved_budget_minor_units"] = 80
    raw["child_budget_request_minor_units"] = 100
    raw["minimum_parent_remaining_minor_units"] = 70

    result = contract.evaluate_recursive_spawn_request(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "child_budget_exceeds_spendable" in codes
    assert "child_budget_exceeds_spendable" in result["non_launch_reason_codes"]


def test_evaluate_recursive_spawn_request_rejects_campaign_phase_outside_planning_categories() -> None:
    raw = _base_request()
    raw["parent_job_shape"] = "campaign_job"
    raw["child_job_shape"] = "fanout_job"
    raw["campaign_phase_category"] = "execution"
    raw["ancestor_job_shapes"] = ["campaign_job"]

    result = contract.evaluate_recursive_spawn_request(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "campaign_phase_not_planning_eligible" in codes
