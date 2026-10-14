from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import job_budget_cost_ledger_contract
from infra.controlplane import recursive_dry_run_planner
from infra.controlplane import recursive_planning_nonlive_admission
from infra.controlplane import recursive_planning_receipts


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
        "parent_job_id": "job_plan_root",
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
            "job_shape": "campaign_job",
            "truth_refs": _truth_refs("plan_root"),
            "node_event_refs": ["events.jsonl#node_root"],
            "dependency_node_ids": [],
            "budget_balance": _budget_balance(job_uid="job_plan_root", remaining_minor_units=1000),
            "campaign_phase_category": "planning",
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


def _campaign_entrypoint() -> dict:
    return {
        "entrypoint_kind": "campaign_phase",
        "parent_job_shape": "campaign_job",
        "parent_job_id": "job_plan_root",
        "binding_type": "ledger_uid",
        "binding_id": "PIS-20260409-153903Z-7C65",
        "campaign_id": "camp_plan_root",
        "phase_id": "phase_recursive_plan",
        "campaign_phase_category": "planning",
        "campaign_truth_refs": ["campaigns/camp_plan_root.json"],
    }


def test_recursive_planning_receipts_reconcile_graph_blocking_reasons_and_budget_from_durable_records(tmp_path: Path) -> None:
    plan_result = recursive_dry_run_planner.build_recursive_dry_run_plan(_base_request(), repo_root=tmp_path)
    admission = recursive_planning_nonlive_admission.materialize_recursive_planning_nonlive_admission(
        entrypoint=_campaign_entrypoint(),
        plan_result=plan_result,
        repo_root=tmp_path,
    )

    projection = recursive_planning_receipts.reconcile_plan_state("plan_recursive_demo_001", repo_root=tmp_path)
    latest_path = (
        tmp_path
        / "runtime"
        / "state"
        / "controlplane"
        / "recursive_planning"
        / "latest_by_plan"
        / f"{recursive_planning_receipts._plan_key('plan_recursive_demo_001')}.json"
    )

    assert plan_result["durable_receipts"]["submitted"]["receipt"]["status"] == "submitted"
    assert plan_result["durable_receipts"]["validation"]["receipt"]["status"] == "validated"
    assert admission["durable_receipt"]["status"] == "non_live_admitted"
    assert projection["projection_source"] == "durable_receipts_only"
    assert projection["status"] == "non_live_admitted"
    assert projection["planned_graph"]["node_count"] == 3
    assert projection["planned_graph"]["dependency_edge_count"] == 3
    assert projection["budget_depth_status"]["max_declared_depth"] == 1
    assert projection["blocking_reason_codes"] == ["recursive_planning_dry_run_only"]
    assert projection["budget_adaptive_receipt"]["selected_mode"] == "standard"
    assert projection["budget_adaptive_receipt"]["effective_continuation_decision"] == "continue"
    assert projection["budget_adaptive_reason_codes"] == []
    assert projection["planned_graph"]["budget_adaptive_receipt"]["wave_cap"] == 2
    assert projection["non_live_admission"]["campaign_context"]["phase_id"] == "phase_recursive_plan"
    assert latest_path.exists()


def test_recursive_planning_reconciliation_does_not_upgrade_from_transcript_only_summary(tmp_path: Path) -> None:
    recursive_planning_receipts.record_submitted_receipt(_base_request(), repo_root=tmp_path)
    transcript_dir = tmp_path / "runtime" / "state" / "controlplane" / "recursive_planning" / "transcript_summaries"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    (transcript_dir / "plan_recursive_demo_001.json").write_text(
        json.dumps({"plan_id": "plan_recursive_demo_001", "status": "validated", "summary": "optimistic transcript claim"}),
        encoding="utf-8",
    )

    projection = recursive_planning_receipts.reconcile_plan_state("plan_recursive_demo_001", repo_root=tmp_path)

    assert projection["status"] == "submitted"
    assert projection["validation_present"] is False
    assert projection["planned_graph"] == {}


def test_recursive_planning_receipts_capture_blocked_validation_without_claiming_admission(tmp_path: Path) -> None:
    raw = _base_request()
    raw["root_node"]["descendants"][0]["dependency_node_ids"] = ["node_missing"]

    plan_result = recursive_dry_run_planner.build_recursive_dry_run_plan(raw, repo_root=tmp_path)
    projection = recursive_planning_receipts.reconcile_plan_state("plan_recursive_demo_001", repo_root=tmp_path)

    assert plan_result["ok"] is False
    assert projection["status"] == "blocked"
    assert "missing_target_rejected" in projection["blocking_reason_codes"]
    assert projection["non_live_admission_present"] is False


def test_recursive_planning_receipts_project_advisory_budget_adaptive_downgrade_without_promoting_it_to_blocker(tmp_path: Path) -> None:
    raw = _base_request()
    raw["budget_adaptive_guidance"] = {
        "selected_mode": "campaign",
        "requested_builder_waves": 2,
        "candidate_continuation_decision": "stop",
        "continuation_rationale": "Stop early.",
        "advisory_only": True,
    }

    recursive_dry_run_planner.build_recursive_dry_run_plan(raw, repo_root=tmp_path)
    projection = recursive_planning_receipts.reconcile_plan_state("plan_recursive_demo_001", repo_root=tmp_path)

    assert projection["status"] == "validated"
    assert projection["budget_adaptive_receipt"]["policy_state"] == "downgraded"
    assert projection["budget_adaptive_receipt"]["effective_continuation_decision"] == "continue"
    assert projection["budget_adaptive_reason_codes"] == ["premature_convergence"]
    assert projection["blocking_reason_codes"] == []
