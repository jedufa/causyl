from __future__ import annotations

from pathlib import Path

from infra.controlplane import campaign_lifecycle_reconciliation as reconciliation
from infra.controlplane import job_budget_cost_ledger_store as ledger_store


def _campaign_record() -> dict:
    return {
        "schema_version": "campaign_record_contract_v1",
        "campaign_id": "camp_20260409_reconcile",
        "parent_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
        "campaign_state": "active",
        "campaign_truth_refs": {
            "ledger_uids": ["PIS-20260409-121217Z-2A6A"],
            "job_ids": [],
            "run_ids": [],
            "receipt_refs": [],
            "continuation_packet_ids": [],
            "artifact_refs": [],
        },
        "record_event_refs": ["governance/ledger/patch_instruction_sheets/causyl/active/2026/04/demo/events.jsonl"],
        "phases": [
            {
                "phase_id": "phase_a",
                "label": "Phase A",
                "job_shape": "verification_loop_job",
                "state": "declared",
                "depends_on": [],
                "output_refs": [],
                "truth_refs": {
                    "ledger_uids": ["PIS-20260409-121602Z-L1P4"],
                    "job_ids": ["job_phase_a"],
                    "run_ids": [],
                    "receipt_refs": [],
                    "continuation_packet_ids": [],
                    "artifact_refs": [],
                },
                "phase_event_refs": [],
            },
            {
                "phase_id": "phase_b",
                "label": "Phase B",
                "job_shape": "verification_loop_job",
                "state": "declared",
                "depends_on": ["phase_a"],
                "output_refs": [],
                "truth_refs": {
                    "ledger_uids": ["PIS-20260409-121603Z-M2Q5"],
                    "job_ids": ["job_phase_b"],
                    "run_ids": [],
                    "receipt_refs": [],
                    "continuation_packet_ids": [],
                    "artifact_refs": [],
                },
                "phase_event_refs": [],
            },
        ],
    }


def _gate(phase_id: str, verdict: str, *, allowed: bool = True) -> dict:
    policy = "full_ready" if verdict == "ready" else "review_required"
    if verdict == "partial_ready":
        policy = "allowed_with_caveats"
    if verdict in {"blocked", "waiting_review"}:
        allowed = False
    return {
        "schema_version": "campaign_phase_gate_contract_v1",
        "campaign_id": "camp_20260409_reconcile",
        "phase_id": phase_id,
        "decision_id": f"decision_{phase_id}",
        "verdict": verdict,
        "evidence_refs": [f"governance/{phase_id}.json"],
        "caveats": ["needs_human_review"] if verdict == "partial_ready" else [],
        "proof_gaps": ["gap"] if verdict in {"blocked", "waiting_review"} else [],
        "downstream_unlock": {
            "policy": policy,
            "allowed": allowed,
            "unlocked_phase_ids": [phase_id] if allowed else [],
        },
        "handoff": {
            "summary": f"{phase_id} gate",
            "evidence_refs": [f"governance/{phase_id}.json"],
            "caveats": ["needs_human_review"] if verdict == "partial_ready" else [],
            "proof_gaps": ["gap"] if verdict in {"blocked", "waiting_review"} else [],
            "next_decision": {
                "decision_owner": "campaign_parent",
                "question": f"what next for {phase_id}?",
                "recommended_action": "await_review" if verdict == "waiting_review" else "launch_downstream",
            },
        },
    }


def _receipt(phase_id: str, status: str, *, attempt_count: int | None = None, max_attempts: int | None = None) -> dict:
    payload = {
        "receipt_id": f"{phase_id}_{status}",
        "job_id": f"job_{phase_id}",
        "run_id": f"run_{phase_id}_{status}",
        "status": status,
    }
    if attempt_count is not None or max_attempts is not None:
        payload["retry"] = {
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
        }
    return payload


def _allocate(job_uid: str, amount: int, *, repo_root: Path) -> None:
    ledger_store.append_allocation_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "allocation_uid": f"{job_uid}_alloc_1",
            "job_uid": job_uid,
            "recorded_at_utc": "2026-04-09T12:00:00Z",
            "amount_minor_units": amount,
            "unit": "usd_cents",
            "allocation_reason_code": "initial_grant",
        },
        repo_root=repo_root,
    )


def _cost(job_uid: str, amount: int, *, repo_root: Path) -> None:
    ledger_store.append_cost_event_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "cost_event_uid": f"{job_uid}_cost_{amount}",
            "job_uid": job_uid,
            "recorded_at_utc": "2026-04-09T12:10:00Z",
            "amount_minor_units": amount,
            "unit": "usd_cents",
            "direction": "debit",
            "cost_reason_code": "usage",
        },
        repo_root=repo_root,
    )


def test_reconcile_campaign_advances_only_from_durable_success_and_budget_rollup(tmp_path: Path) -> None:
    _allocate("job_phase_a", 1_000, repo_root=tmp_path)
    _cost("job_phase_a", 250, repo_root=tmp_path)
    _allocate("job_phase_b", 600, repo_root=tmp_path)
    _cost("job_phase_b", 100, repo_root=tmp_path)

    result = reconciliation.reconcile_campaign_lifecycle(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[_gate("phase_a", "ready"), _gate("phase_b", "ready")],
        phase_runtime_facts=[
            {"phase_id": "phase_a", "receipts": [_receipt("phase_a", "ok")]},
            {"phase_id": "phase_b"},
        ],
        repo_root=tmp_path,
    )

    assert result["reconciled_campaign_state"] == "active"
    assert result["launchable_phase_ids"] == ["phase_b"]
    assert result["blocked_phase_ids"] == []
    assert result["budget_rollup"]["status"] == "ok"
    assert result["budget_rollup"]["allocated_minor_units"] == 1_600
    assert result["budget_rollup"]["spent_minor_units"] == 350
    assert result["budget_rollup"]["remaining_minor_units"] == 1_250
    phase_a = next(row for row in result["phases"] if row["phase_id"] == "phase_a")
    assert phase_a["reconciled_state"] == "succeeded"


def test_reconcile_campaign_fails_closed_on_duplicate_phase_receipts(tmp_path: Path) -> None:
    _allocate("job_phase_a", 100, repo_root=tmp_path)
    _allocate("job_phase_b", 100, repo_root=tmp_path)

    result = reconciliation.reconcile_campaign_lifecycle(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[_gate("phase_a", "ready"), _gate("phase_b", "ready")],
        phase_runtime_facts=[
            {
                "phase_id": "phase_a",
                "receipts": [
                    _receipt("phase_a", "ok"),
                    _receipt("phase_a", "failed"),
                ],
            }
        ],
        repo_root=tmp_path,
    )

    phase_a = next(row for row in result["phases"] if row["phase_id"] == "phase_a")
    assert phase_a["reconciled_state"] == "blocked"
    assert "conflicting_receipt_states" in phase_a["reasons"]
    assert result["reconciled_campaign_state"] == "blocked"
    assert result["launchable_phase_ids"] == []
    assert result["blocked_phase_ids"] == ["phase_a", "phase_b"]


def test_reconcile_campaign_keeps_waiting_review_explicit_after_restart(tmp_path: Path) -> None:
    _allocate("job_phase_a", 100, repo_root=tmp_path)
    _allocate("job_phase_b", 100, repo_root=tmp_path)

    result = reconciliation.reconcile_campaign_lifecycle(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[_gate("phase_a", "waiting_review"), _gate("phase_b", "ready")],
        phase_runtime_facts=[
            {"phase_id": "phase_a", "continuation_packet_ids": ["packet_phase_a_review"]},
        ],
        repo_root=tmp_path,
    )

    phase_a = next(row for row in result["phases"] if row["phase_id"] == "phase_a")
    assert phase_a["reconciled_state"] == "waiting_review"
    assert phase_a["retry_boundary"]["state"] == "halted_review"
    assert result["reconciled_campaign_state"] == "review"
    assert result["review_phase_ids"] == ["phase_a"]
    assert result["launchable_phase_ids"] == []


def test_reconcile_campaign_marks_retry_exhaustion_blocked(tmp_path: Path) -> None:
    _allocate("job_phase_a", 100, repo_root=tmp_path)
    _allocate("job_phase_b", 100, repo_root=tmp_path)

    result = reconciliation.reconcile_campaign_lifecycle(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[_gate("phase_a", "ready"), _gate("phase_b", "ready")],
        phase_runtime_facts=[
            {
                "phase_id": "phase_a",
                "receipts": [_receipt("phase_a", "failed", attempt_count=2, max_attempts=2)],
            }
        ],
        repo_root=tmp_path,
    )

    phase_a = next(row for row in result["phases"] if row["phase_id"] == "phase_a")
    assert phase_a["retry_boundary"]["state"] == "exhausted"
    assert phase_a["reconciled_state"] == "failed"
    assert result["reconciled_campaign_state"] == "failed"


def test_reconcile_campaign_budget_rollup_blocks_on_missing_job_refs_for_in_flight_phase(tmp_path: Path) -> None:
    record = _campaign_record()
    record["phases"][1]["truth_refs"]["job_ids"] = []

    _allocate("job_phase_a", 100, repo_root=tmp_path)

    result = reconciliation.reconcile_campaign_lifecycle(
        campaign_record=record,
        phase_gate_decisions=[_gate("phase_a", "ready"), _gate("phase_b", "ready")],
        phase_runtime_facts=[
            {"phase_id": "phase_a", "receipts": [_receipt("phase_a", "ok")]},
            {"phase_id": "phase_b", "continuation_packet_ids": ["packet_phase_b_running"]},
        ],
        repo_root=tmp_path,
    )

    phase_b = next(row for row in result["phases"] if row["phase_id"] == "phase_b")
    assert phase_b["budget"]["status"] == "missing_job_refs"
    assert phase_b["reconciled_state"] == "blocked"
    assert "budget_missing_job_refs" in phase_b["reasons"]
    assert result["budget_rollup"]["status"] == "blocked_missing_job_refs"
    assert result["reconciled_campaign_state"] == "blocked"


def test_phase_state_blocks_duplicate_receipt_ids() -> None:
    result = reconciliation._phase_state(
        _campaign_record()["phases"][0],
        gate=_gate("phase_a", "ready"),
        receipts=[
            _receipt("phase_a", "ok") | {"receipt_id": "receipt_dup"},
            _receipt("phase_a", "ok") | {"receipt_id": "receipt_dup"},
        ],
        continuation_packet_ids=[],
        budget={
            "status": "ok",
            "job_ids": ["job_phase_a"],
            "unit": "usd_cents",
            "allocated_minor_units": 100,
            "spent_minor_units": 0,
            "remaining_minor_units": 100,
            "follow_up_statuses": [],
        },
    )

    assert result["reconciled_state"] == "blocked"
    assert result["progress_state"] == "blocked"
    assert "duplicate_receipts" in result["reasons"]


def test_reconcile_campaign_blocks_launchable_phase_when_dependency_not_terminal_success(tmp_path: Path) -> None:
    _allocate("job_phase_a", 100, repo_root=tmp_path)
    _allocate("job_phase_b", 100, repo_root=tmp_path)

    result = reconciliation.reconcile_campaign_lifecycle(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[_gate("phase_a", "ready"), _gate("phase_b", "ready")],
        phase_runtime_facts=[],
        repo_root=tmp_path,
    )

    phase_a = next(row for row in result["phases"] if row["phase_id"] == "phase_a")
    phase_b = next(row for row in result["phases"] if row["phase_id"] == "phase_b")
    assert phase_a["progress_state"] == "launchable"
    assert phase_b["reconciled_state"] == "blocked"
    assert phase_b["progress_state"] == "blocked"
    assert "dependency_not_terminal_success" in phase_b["reasons"]
