"""Bounded end-to-end proof helpers for the recursive planning family."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from infra.controlplane import job_budget_cost_ledger_contract
from infra.controlplane import recursive_dry_run_planner
from infra.controlplane import recursive_planning_nonlive_admission
from infra.controlplane import recursive_planning_receipts


def _budget_balance(*, job_uid: str, remaining_minor_units: int) -> Dict[str, Any]:
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


def _truth_refs(label: str) -> Dict[str, list[str]]:
    return {
        "ledger_uids": [f"PIS-{label}"],
        "campaign_ids": [f"camp_{label}"],
        "job_ids": [f"job_{label}"],
        "run_ids": [],
        "receipt_refs": [f"runtime/state/scheduled_jobs/receipts/{label}.json"],
        "continuation_packet_ids": [f"pkt_{label}"],
        "artifact_refs": [f"docs/contracts/{label}.md"],
    }


def _campaign_entrypoint(*, parent_job_id: str, campaign_id: str, phase_id: str) -> Dict[str, Any]:
    return {
        "entrypoint_kind": "campaign_phase",
        "parent_job_shape": "campaign_job",
        "parent_job_id": parent_job_id,
        "binding_type": "ledger_uid",
        "binding_id": "PIS-20260409-153903Z-7C65",
        "campaign_id": campaign_id,
        "phase_id": phase_id,
        "campaign_phase_category": "planning",
        "campaign_truth_refs": [f"campaigns/{campaign_id}.json"],
    }


def _base_request(*, plan_id: str, parent_job_id: str) -> Dict[str, Any]:
    root_label = f"{plan_id}_root"
    return {
        "schema_version": "recursive_dry_run_plan_request_v1",
        "plan_id": plan_id,
        "root_plan_id": plan_id,
        "parent_plan_id": "",
        "parent_job_id": parent_job_id,
        "planning_mode": "dry_run_only",
        "root_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260409-153903Z-7C65"},
        "plan_truth_refs": _truth_refs(root_label),
        "plan_event_refs": [
            "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-154916Z-0853/events.jsonl"
        ],
        "root_node": {
            "node_id": f"{plan_id}_root",
            "job_shape": "campaign_job",
            "truth_refs": _truth_refs(root_label),
            "node_event_refs": [f"events.jsonl#{plan_id}_root"],
            "dependency_node_ids": [],
            "budget_balance": _budget_balance(job_uid=parent_job_id, remaining_minor_units=1000),
            "campaign_phase_category": "planning",
            "descendants": [
                {
                    "node_id": f"{plan_id}_child_a",
                    "job_shape": "verification_loop_job",
                    "truth_refs": _truth_refs(f"{plan_id}_child_a"),
                    "node_event_refs": [f"events.jsonl#{plan_id}_child_a"],
                    "dependency_node_ids": [],
                    "requested_budget_minor_units": 250,
                    "parent_reserved_budget_minor_units": 100,
                    "minimum_parent_remaining_minor_units": 100,
                    "descendants": [],
                },
                {
                    "node_id": f"{plan_id}_child_b",
                    "job_shape": "verification_loop_job",
                    "truth_refs": _truth_refs(f"{plan_id}_child_b"),
                    "node_event_refs": [f"events.jsonl#{plan_id}_child_b"],
                    "dependency_node_ids": [f"{plan_id}_child_a"],
                    "requested_budget_minor_units": 300,
                    "parent_reserved_budget_minor_units": 100,
                    "minimum_parent_remaining_minor_units": 100,
                    "descendants": [],
                },
            ],
        },
    }


def _depth_cap_request(*, plan_id: str, parent_job_id: str) -> Dict[str, Any]:
    level4 = {
        "node_id": f"{plan_id}_node4",
        "job_shape": "delegation_tree_job",
        "truth_refs": _truth_refs(f"{plan_id}_node4"),
        "node_event_refs": [f"events.jsonl#{plan_id}_node4"],
        "dependency_node_ids": [],
        "requested_budget_minor_units": 50,
        "parent_reserved_budget_minor_units": 10,
        "minimum_parent_remaining_minor_units": 10,
        "descendants": [],
    }
    level3 = {
        "node_id": f"{plan_id}_node3",
        "job_shape": "delegation_tree_job",
        "truth_refs": _truth_refs(f"{plan_id}_node3"),
        "node_event_refs": [f"events.jsonl#{plan_id}_node3"],
        "dependency_node_ids": [],
        "requested_budget_minor_units": 80,
        "parent_reserved_budget_minor_units": 10,
        "minimum_parent_remaining_minor_units": 10,
        "descendants": [level4],
    }
    level2 = {
        "node_id": f"{plan_id}_node2",
        "job_shape": "delegation_tree_job",
        "truth_refs": _truth_refs(f"{plan_id}_node2"),
        "node_event_refs": [f"events.jsonl#{plan_id}_node2"],
        "dependency_node_ids": [],
        "requested_budget_minor_units": 120,
        "parent_reserved_budget_minor_units": 10,
        "minimum_parent_remaining_minor_units": 10,
        "descendants": [level3],
    }
    level1 = {
        "node_id": f"{plan_id}_node1",
        "job_shape": "delegation_tree_job",
        "truth_refs": _truth_refs(f"{plan_id}_node1"),
        "node_event_refs": [f"events.jsonl#{plan_id}_node1"],
        "dependency_node_ids": [],
        "requested_budget_minor_units": 160,
        "parent_reserved_budget_minor_units": 10,
        "minimum_parent_remaining_minor_units": 10,
        "descendants": [level2],
    }
    request = _base_request(plan_id=plan_id, parent_job_id=parent_job_id)
    request["root_node"]["job_shape"] = "delegation_tree_job"
    request["root_node"]["descendants"] = [level1]
    return request


def _budget_block_request(*, plan_id: str, parent_job_id: str) -> Dict[str, Any]:
    request = _base_request(plan_id=plan_id, parent_job_id=parent_job_id)
    request["root_node"]["descendants"] = [
        {
            "node_id": f"{plan_id}_over_budget_child",
            "job_shape": "verification_loop_job",
            "truth_refs": _truth_refs(f"{plan_id}_over_budget_child"),
            "node_event_refs": [f"events.jsonl#{plan_id}_over_budget_child"],
            "dependency_node_ids": [],
            "requested_budget_minor_units": 100,
            "parent_reserved_budget_minor_units": 80,
            "minimum_parent_remaining_minor_units": 70,
            "descendants": [],
        }
    ]
    request["root_node"]["budget_balance"] = _budget_balance(job_uid=parent_job_id, remaining_minor_units=220)
    return request


def _case_summary(*, case_id: str, plan_result: Mapping[str, Any], projection: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "case_id": case_id,
        "planner_ok": bool(plan_result.get("ok")),
        "plan_status": str(((plan_result.get("plan_record") or {}).get("plan_status") or "")).strip(),
        "projection_status": str(projection.get("status") or "").strip(),
        "blocking_reason_codes": list(projection.get("blocking_reason_codes") or []),
        "node_count": int(((projection.get("planned_graph") or {}).get("node_count") or 0)),
        "dependency_edge_count": int(((projection.get("planned_graph") or {}).get("dependency_edge_count") or 0)),
        "receipt_paths": list(projection.get("receipt_paths") or []),
    }


def collect_recursive_planning_family_proof(*, repo_root: Path) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()

    happy_plan_id = "plan_recursive_happy_001"
    happy_parent_job_id = "job_recursive_happy_001"
    happy_result = recursive_dry_run_planner.build_recursive_dry_run_plan(
        _base_request(plan_id=happy_plan_id, parent_job_id=happy_parent_job_id),
        repo_root=repo_root,
    )
    happy_admission = recursive_planning_nonlive_admission.materialize_recursive_planning_nonlive_admission(
        entrypoint=_campaign_entrypoint(
            parent_job_id=happy_parent_job_id,
            campaign_id=f"camp_{happy_plan_id}_root",
            phase_id="phase_recursive_plan",
        ),
        plan_result=happy_result,
        repo_root=repo_root,
    )
    happy_projection = recursive_planning_receipts.reconcile_plan_state(happy_plan_id, repo_root=repo_root)

    cycle_plan_id = "plan_recursive_cycle_001"
    cycle_request = _base_request(plan_id=cycle_plan_id, parent_job_id="job_recursive_cycle_001")
    cycle_request["root_node"]["descendants"][0]["dependency_node_ids"] = [f"{cycle_plan_id}_child_b"]
    cycle_result = recursive_dry_run_planner.build_recursive_dry_run_plan(cycle_request, repo_root=repo_root)
    cycle_projection = recursive_planning_receipts.reconcile_plan_state(cycle_plan_id, repo_root=repo_root)

    depth_plan_id = "plan_recursive_depth_001"
    depth_result = recursive_dry_run_planner.build_recursive_dry_run_plan(
        _depth_cap_request(plan_id=depth_plan_id, parent_job_id="job_recursive_depth_001"),
        repo_root=repo_root,
    )
    depth_projection = recursive_planning_receipts.reconcile_plan_state(depth_plan_id, repo_root=repo_root)

    budget_plan_id = "plan_recursive_budget_001"
    budget_result = recursive_dry_run_planner.build_recursive_dry_run_plan(
        _budget_block_request(plan_id=budget_plan_id, parent_job_id="job_recursive_budget_001"),
        repo_root=repo_root,
    )
    budget_projection = recursive_planning_receipts.reconcile_plan_state(budget_plan_id, repo_root=repo_root)

    forbidden_live_launch = recursive_planning_nonlive_admission.materialize_recursive_planning_nonlive_admission(
        entrypoint=_campaign_entrypoint(
            parent_job_id=happy_parent_job_id,
            campaign_id=f"camp_{happy_plan_id}_root",
            phase_id="phase_recursive_plan",
        ),
        plan_result=happy_result,
        request_live_launch=True,
    )

    listing = recursive_planning_receipts.list_plan_projections(
        repo_root=repo_root,
        binding_type="ledger_uid",
        binding_id="PIS-20260409-153903Z-7C65",
        limit=10,
    )

    cases = {
        "happy_path_non_live_admitted": {
            **_case_summary(case_id="happy_path_non_live_admitted", plan_result=happy_result, projection=happy_projection),
            "admission_ok": bool(happy_admission.get("ok")),
            "phase_id": str((((happy_projection.get("non_live_admission") or {}).get("campaign_context") or {}).get("phase_id") or "")).strip(),
        },
        "dependency_cycle_rejected": _case_summary(
            case_id="dependency_cycle_rejected",
            plan_result=cycle_result,
            projection=cycle_projection,
        ),
        "depth_cap_rejected": _case_summary(
            case_id="depth_cap_rejected",
            plan_result=depth_result,
            projection=depth_projection,
        ),
        "budget_stop_rejected": _case_summary(
            case_id="budget_stop_rejected",
            plan_result=budget_result,
            projection=budget_projection,
        ),
        "live_launch_forbidden": {
            "case_id": "live_launch_forbidden",
            "admission_ok": bool(forbidden_live_launch.get("ok")),
            "error_codes": [str(row.get("code") or "") for row in list(forbidden_live_launch.get("errors") or [])],
            "non_launch_reason_codes": list(((forbidden_live_launch.get("admission") or {}).get("non_launch_reason_codes") or [])),
        },
    }

    return {
        "schema_version": "recursive_planning_family_proof_v1",
        "uid": "PIS-20260409-154917Z-E01A",
        "case_count": len(cases),
        "all_cases_met": (
            cases["happy_path_non_live_admitted"]["planner_ok"]
            and cases["happy_path_non_live_admitted"]["admission_ok"]
            and cases["happy_path_non_live_admitted"]["projection_status"] == "non_live_admitted"
            and "dependency_cycle_detected" in cases["dependency_cycle_rejected"]["blocking_reason_codes"]
            and "depth_cap_exceeded" in cases["depth_cap_rejected"]["blocking_reason_codes"]
            and "child_budget_exceeds_spendable" in cases["budget_stop_rejected"]["blocking_reason_codes"]
            and "live_recursive_launch_forbidden" in cases["live_launch_forbidden"]["error_codes"]
        ),
        "listing_total_plans": int(listing.get("total_plans") or 0),
        "listing_statuses": {
            str(row.get("plan_id") or "").strip(): str(row.get("status") or "").strip()
            for row in list(listing.get("projections") or [])
        },
        "cases": cases,
    }
