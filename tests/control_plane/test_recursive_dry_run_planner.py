from __future__ import annotations

from pathlib import Path

from infra.controlplane import job_budget_cost_ledger_contract
from infra.controlplane import recursive_dry_run_planner


def _budget_balance(*, job_uid: str, remaining_minor_units: int) -> dict:
    allocated_minor_units = remaining_minor_units + 100
    return job_budget_cost_ledger_contract.replay_job_budget_balance(
        [
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "allocation_uid": f"alloc_{job_uid}",
                "job_uid": job_uid,
                "recorded_at_utc": "2026-04-09T16:00:00Z",
                "amount_minor_units": allocated_minor_units,
                "unit": "usd_cents",
                "allocation_reason_code": "initial_grant",
            }
        ],
        [
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "cost_event_uid": f"cost_{job_uid}",
                "job_uid": job_uid,
                "recorded_at_utc": "2026-04-09T16:05:00Z",
                "amount_minor_units": 100,
                "unit": "usd_cents",
                "direction": "debit",
                "cost_reason_code": "usage",
            }
        ],
        job_uid=job_uid,
    )


def _truth_refs(label: str) -> dict:
    return {
        "ledger_uids": [f"PIS-{label}"],
        "campaign_ids": [f"camp_{label}"],
        "job_ids": [f"job_{label}"],
        "run_ids": [],
        "receipt_refs": [f"runtime/state/scheduled_jobs/receipts/{label}.json"],
        "continuation_packet_ids": [f"pkt_{label}"],
        "artifact_refs": [f"docs/contracts/{label}.md"],
    }


def _base_request() -> dict:
    return {
        "schema_version": "recursive_dry_run_plan_request_v1",
        "plan_id": "plan_recursive_demo_001",
        "root_plan_id": "plan_recursive_demo_001",
        "parent_plan_id": "",
        "parent_job_id": "sj_recursive_demo_001",
        "planning_mode": "dry_run_only",
        "root_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260409-153903Z-7C65"},
        "plan_truth_refs": _truth_refs("plan_root"),
        "plan_event_refs": [
            "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-154916Z-0853/events.jsonl"
        ],
        "budget_adaptive_guidance": {
            "selected_mode": "standard",
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "continue",
            "continuation_rationale": "One more bounded pass is still inside the mode envelope.",
            "continuation_packet_id": "pkt_plan_standard_001",
            "evidence_pack_refs": [],
            "advisory_only": False,
        },
        "root_node": {
            "node_id": "node_root",
            "job_shape": "fanout_job",
            "truth_refs": _truth_refs("node_root"),
            "node_event_refs": ["events.jsonl#node_root"],
            "dependency_node_ids": [],
            "budget_balance": _budget_balance(job_uid="job_root", remaining_minor_units=1000),
            "descendants": [
                {
                    "node_id": "node_child_a",
                    "job_shape": "verification_loop_job",
                    "truth_refs": _truth_refs("node_child_a"),
                    "node_event_refs": ["events.jsonl#node_child_a"],
                    "dependency_node_ids": [],
                    "requested_budget_minor_units": 250,
                    "parent_reserved_budget_minor_units": 100,
                    "minimum_parent_remaining_minor_units": 100,
                    "descendants": [],
                },
                {
                    "node_id": "node_child_b",
                    "job_shape": "verification_loop_job",
                    "truth_refs": _truth_refs("node_child_b"),
                    "node_event_refs": ["events.jsonl#node_child_b"],
                    "dependency_node_ids": ["node_child_a"],
                    "requested_budget_minor_units": 300,
                    "parent_reserved_budget_minor_units": 100,
                    "minimum_parent_remaining_minor_units": 100,
                    "descendants": [],
                },
            ],
        },
    }


def test_build_recursive_dry_run_plan_expands_nodes_edges_and_budget_allocations() -> None:
    result = recursive_dry_run_planner.build_recursive_dry_run_plan(_base_request())

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["plan_record"]["plan_status"] == "validated"
    assert [row["node_id"] for row in result["plan_record"]["nodes"]] == [
        "node_child_a",
        "node_child_b",
        "node_root",
    ]
    assert result["validation_report"]["live_queue_admission_allowed"] is False
    assert result["validation_report"]["budget_adaptive_receipt"]["selected_mode"] == "standard"
    assert result["validation_report"]["budget_adaptive_receipt"]["effective_continuation_decision"] == "continue"
    assert result["validation_report"]["budget_adaptive_receipt"]["convergence_posture"] == "bounded_convergence"
    assert result["plan_record"]["budget_adaptive_receipt"]["wave_cap"] == 2
    assert result["plan_record"]["budget_adaptive_receipt"]["continuation_packet_id"] == "pkt_plan_standard_001"
    assert result["validation_report"]["graph_summary"]["dependency_edge_count"] == 3
    edge_pairs = {
        (row["from_node_id"], row["to_node_id"])
        for row in result["plan_record"]["dependency_edges"]
    }
    assert edge_pairs == {
        ("node_child_a", "node_root"),
        ("node_child_b", "node_child_a"),
        ("node_child_b", "node_root"),
    }
    allocations = {row["node_id"]: row for row in result["validation_report"]["budget_allocations"]}
    assert allocations["node_root"]["allocated_minor_units"] == 1100
    assert allocations["node_child_a"]["allocated_minor_units"] == 250
    assert allocations["node_child_b"]["allocated_minor_units"] == 300
    assert allocations["node_child_b"]["parent_remaining_after_child_minor_units"] == 700


def test_build_recursive_dry_run_plan_surfaces_advisory_budget_adaptive_downgrade_without_blocking() -> None:
    raw = _base_request()
    raw["budget_adaptive_guidance"] = {
        "selected_mode": "overnight",
        "requested_builder_waves": 2,
        "candidate_continuation_decision": "continue",
        "continuation_rationale": "Broaden before converging.",
        "advisory_only": True,
    }

    result = recursive_dry_run_planner.build_recursive_dry_run_plan(raw, repo_root=Path("."))

    receipt = result["validation_report"]["budget_adaptive_receipt"]
    warning_codes = {row["code"] for row in result["warnings"]}
    assert result["ok"] is True
    assert receipt["policy_state"] == "downgraded"
    assert receipt["effective_continuation_decision"] == "stop"
    assert receipt["reason_codes"] == ["proof_blind_continuation"]
    assert "budget_adaptive_advisory_receipt" in warning_codes


def test_build_recursive_dry_run_plan_rejects_ambiguous_parent_ownership() -> None:
    raw = _base_request()
    raw["root_node"]["descendants"][1]["descendants"] = [
        {
            "node_id": "node_child_a",
            "job_shape": "verification_loop_job",
            "truth_refs": _truth_refs("node_child_dup"),
            "node_event_refs": ["events.jsonl#node_child_dup"],
            "dependency_node_ids": [],
            "requested_budget_minor_units": 50,
            "parent_reserved_budget_minor_units": 10,
            "minimum_parent_remaining_minor_units": 10,
            "descendants": [],
        }
    ]
    raw["root_node"]["descendants"][1]["budget_balance"] = _budget_balance(job_uid="job_child_b", remaining_minor_units=200)

    result = recursive_dry_run_planner.build_recursive_dry_run_plan(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "ambiguous_parent_rejected" in codes
    assert result["plan_record"]["plan_status"] == "invalid"


def test_build_recursive_dry_run_plan_rejects_duplicate_descendant_under_same_parent() -> None:
    raw = _base_request()
    raw["root_node"]["descendants"].append(
        {
            "node_id": "node_child_a",
            "job_shape": "verification_loop_job",
            "truth_refs": _truth_refs("node_child_dup_same_parent"),
            "node_event_refs": ["events.jsonl#node_child_dup_same_parent"],
            "dependency_node_ids": [],
            "requested_budget_minor_units": 50,
            "parent_reserved_budget_minor_units": 10,
            "minimum_parent_remaining_minor_units": 10,
            "descendants": [],
        }
    )

    result = recursive_dry_run_planner.build_recursive_dry_run_plan(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "duplicate_descendant_rejected" in codes
    assert result["plan_record"]["plan_status"] == "invalid"


def test_build_recursive_dry_run_plan_rejects_missing_dependency_targets() -> None:
    raw = _base_request()
    raw["root_node"]["descendants"][0]["dependency_node_ids"] = ["node_missing"]

    result = recursive_dry_run_planner.build_recursive_dry_run_plan(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "missing_target_rejected" in codes
    assert "missing_target_rejected" in result["validation_report"]["non_launch_reason_codes"]


def test_build_recursive_dry_run_plan_blocks_non_advisory_budget_adaptive_rejection() -> None:
    raw = _base_request()
    raw["budget_adaptive_guidance"]["selected_mode"] = "micro"
    raw["budget_adaptive_guidance"]["requested_builder_waves"] = 2
    raw["budget_adaptive_guidance"]["continuation_packet_id"] = ""
    raw["budget_adaptive_guidance"]["evidence_pack_refs"] = []

    result = recursive_dry_run_planner.build_recursive_dry_run_plan(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert result["plan_record"]["plan_status"] == "invalid"
    assert "over_wave_cap" in codes
    assert "proof_blind_continuation" in codes


def test_build_recursive_dry_run_plan_rejects_structural_dependency_cycles() -> None:
    raw = _base_request()
    raw["root_node"]["descendants"][0]["dependency_node_ids"] = ["node_child_b"]

    result = recursive_dry_run_planner.build_recursive_dry_run_plan(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "dependency_cycle_detected" in codes
