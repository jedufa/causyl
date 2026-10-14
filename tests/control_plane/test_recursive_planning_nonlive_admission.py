from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import job_budget_cost_ledger_contract
from infra.controlplane import recursive_dry_run_planner
from infra.controlplane import recursive_planning_nonlive_admission as admission
from infra.controlplane import scheduled_jobs as sched


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
                }
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


def test_materialize_recursive_planning_nonlive_admission_preserves_campaign_context() -> None:
    plan_result = recursive_dry_run_planner.build_recursive_dry_run_plan(_base_request())

    result = admission.materialize_recursive_planning_nonlive_admission(
        entrypoint=_campaign_entrypoint(),
        plan_result=plan_result,
    )

    assert result["ok"] is True
    payload = result["admission"]
    assert payload["scheduler_admission"]["launch_allowed"] is False
    assert payload["scheduler_admission"]["nonlaunch_reason"] == "recursive_planning_dry_run_only"
    assert payload["campaign_context"]["campaign_id"] == "camp_plan_root"
    assert payload["campaign_context"]["phase_id"] == "phase_recursive_plan"
    assert payload["campaign_context"]["campaign_truth_refs"] == [
        "camp_plan_root",
        "campaigns/camp_plan_root.json",
    ]
    assert payload["compiled_work_order"]["status"] == "compiled_ready"
    assert payload["queue_law"]["admission_gate_reason"] == (
        "successful dry-run validation is evidence only and never grants live recursive launch permission"
    )


def test_materialize_recursive_planning_nonlive_admission_rejects_invalid_plan_and_live_launch_attempt() -> None:
    raw = _base_request()
    raw["root_node"]["descendants"][0]["dependency_node_ids"] = ["node_missing"]
    plan_result = recursive_dry_run_planner.build_recursive_dry_run_plan(raw)

    result = admission.materialize_recursive_planning_nonlive_admission(
        entrypoint=_campaign_entrypoint(),
        plan_result=plan_result,
        request_live_launch=True,
    )

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "plan_report_not_validated" in codes
    assert "live_recursive_launch_forbidden" in codes
    assert "missing_target_rejected" in result["admission"]["non_launch_reason_codes"]
    assert result["compiled_work_order"]["status"] == "fallback_legacy"
    assert "missing_target_rejected" in result["compiled_work_order"]["non_compiled_reason_codes"]


def test_materialize_recursive_planning_nonlive_admission_rejects_illegal_campaign_phase_category() -> None:
    plan_result = recursive_dry_run_planner.build_recursive_dry_run_plan(_base_request())
    entrypoint = _campaign_entrypoint()
    entrypoint["campaign_phase_category"] = "execution"

    result = admission.materialize_recursive_planning_nonlive_admission(
        entrypoint=entrypoint,
        plan_result=plan_result,
    )

    assert result["ok"] is False
    assert any(row["code"] == "campaign_phase_not_planning_eligible" for row in result["errors"])


def test_record_nonlaunch_decision_persists_recursive_planning_admission(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    plan_result = recursive_dry_run_planner.build_recursive_dry_run_plan(_base_request())
    monkeypatch.setattr(
        sched,
        "_repo_guard_preflight",
        lambda **kwargs: {"blocked": True, "reason": "recursive_planning_dry_run_only"},
    )

    sched.save_job(
        {
            "job_id": "recursive_planning_nonlaunch_001",
            "enabled": True,
            "job_kind": "single_turn",
            "role": "generalist",
            "binding": {"type": "ledger_uid", "id": "PIS-20260409-154916Z-2B1D"},
            "prompt": "Persist recursive planning non-launch evidence.",
            "schedule": {"mode": "once", "run_at": "2026-04-09T17:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
            "prompt_meta": {
                "recursive_planning_nonlive_admission": {
                    "nonlaunch_reason": "recursive_planning_dry_run_only",
                    "entrypoint": _campaign_entrypoint(),
                    "plan_result": plan_result,
                }
            },
        }
    )

    decision = sched.execute_job(
        "recursive_planning_nonlaunch_001",
        repo_root=tmp_path,
        trigger="manual",
    )

    assert decision["reason"] == "recursive_planning_dry_run_only"
    receipt = json.loads(Path(decision["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["recursive_planning_admission"]["campaign_context"]["phase_id"] == "phase_recursive_plan"
    assert receipt["recursive_planning_admission"]["campaign_context"]["campaign_id"] == "camp_plan_root"
    assert receipt["recursive_planning_admission"]["plan_projection"]["plan_id"] == "plan_recursive_demo_001"
    assert receipt["recursive_planning_admission"]["compiled_work_order"]["status"] == "compiled_emitted"
    assert Path(receipt["recursive_planning_admission"]["compiled_work_order"]["compiled_artifact_rel"]).name == "compiled_work_order_envelope.json"
