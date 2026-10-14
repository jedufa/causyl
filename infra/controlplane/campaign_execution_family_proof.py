"""End-to-end proof helpers for the first-release campaign execution family."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping

from infra.controlplane import campaign_lifecycle_reconciliation
from infra.controlplane import campaign_operator_summary
from infra.controlplane import campaign_phase_execution_contract
from infra.controlplane import campaign_phase_gate_contract
from infra.controlplane import campaign_record_contract
from infra.controlplane import job_budget_cost_ledger_store


SCHEMA_VERSION = "campaign_execution_family_proof_v1"
PROOF_CHILD_UID = "PIS-20260409-121605Z-P4S7"
PARENT_UID = "PIS-20260409-121217Z-2A6A"
CHILD_UIDS = (
    "PIS-20260409-121600Z-J8M2",
    "PIS-20260409-121601Z-K9N3",
    "PIS-20260409-121602Z-L1P4",
    "PIS-20260409-121603Z-M2Q5",
    "PIS-20260409-121604Z-N3R6",
)
ANTI_PATTERN_BANS = (
    "immortal_coordinator_memory_without_durable_phase_records",
    "phase_progress_claimed_without_gate_or_receipt_truth",
    "partial_ready_claimed_as_full_ready",
    "invalid_handoff_or_recursive_phase_launch_claims",
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _base_record(*, phase_job_kind: str = "single_turn", phase_job_shape: str = "verification_loop_job") -> Dict[str, Any]:
    return {
        "schema_version": "campaign_record_contract_v1",
        "campaign_id": "camp_20260409_family_proof",
        "parent_binding": {"binding_type": "ledger_uid", "binding_id": PARENT_UID},
        "campaign_state": "active",
        "campaign_truth_refs": {
            "ledger_uids": [PARENT_UID],
            "job_ids": [],
            "run_ids": [],
            "receipt_refs": [],
            "continuation_packet_ids": [],
            "artifact_refs": [],
        },
        "record_event_refs": [
            "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
            "PIS-20260409-121217Z-2A6A__campaign-execution-contract/events.jsonl"
        ],
        "phases": [
            {
                "phase_id": "phase_define_contract",
                "label": "Define contract",
                "job_shape": "verification_loop_job",
                "state": "declared",
                "depends_on": [],
                "output_refs": ["governance/campaign_record_contract_v1.json"],
                "truth_refs": {
                    "ledger_uids": ["PIS-20260409-121600Z-J8M2"],
                    "job_ids": ["job_phase_define_contract"],
                    "run_ids": [],
                    "receipt_refs": [],
                    "continuation_packet_ids": [],
                    "artifact_refs": ["docs/contracts/contract_campaign_record_schema_v1.md"],
                },
                "phase_event_refs": [],
            },
            {
                "phase_id": "phase_launch_execution",
                "label": "Launch execution",
                "job_shape": phase_job_shape,
                "state": "declared",
                "depends_on": ["phase_define_contract"],
                "output_refs": ["governance/campaign_operator_summary_contract_v1.json"],
                "truth_refs": {
                    "ledger_uids": ["PIS-20260409-121602Z-L1P4", "PIS-20260409-121604Z-N3R6"],
                    "job_ids": ["job_phase_launch_execution"],
                    "run_ids": [],
                    "receipt_refs": [],
                    "continuation_packet_ids": [],
                    "artifact_refs": ["docs/contracts/contract_campaign_phase_execution_v1.md"],
                },
                "phase_event_refs": [],
                "execution_binding": {
                    "binding_id": "bind_phase_launch_execution_001",
                    "job_kind": phase_job_kind,
                    "instruction_refs": [
                        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
                        "PIS-20260409-121602Z-L1P4__implement-campaign-phase-execution-bindings-and-launch-contract/pis.md"
                    ],
                    "durable_context_refs": [
                        "governance/campaign_phase_execution_contract_v1.json",
                    ],
                    "expected_evidence": {
                        "summary": "Capture bounded phase receipts and summary evidence for the campaign proof child.",
                        "artifact_refs": [
                            "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
                            "PIS-20260409-121605Z-P4S7__prove-campaign-execution-contract-end-to-end/"
                            "artifacts/verification/campaign_execution_family_proof.md"
                        ],
                        "next_decision": {
                            "decision_owner": "campaign_parent",
                            "question": "Did the launched phase produce durable receipt and summary truth?",
                        },
                    },
                },
            },
        ],
    }


def _gate(
    phase_id: str,
    verdict: str,
    *,
    allowed: bool = True,
    caveats: List[str] | None = None,
    proof_gaps: List[str] | None = None,
    unlocked_phase_ids: List[str] | None = None,
) -> Dict[str, Any]:
    normalized_verdict = _normalize_text(verdict).lower()
    unlock_allowed = allowed
    if normalized_verdict in {"blocked", "waiting_review"}:
        unlock_allowed = False
    policy = "full_ready"
    if normalized_verdict == "partial_ready":
        policy = "allowed_with_caveats"
    elif normalized_verdict == "waiting_review":
        policy = "review_required"
    elif normalized_verdict == "blocked":
        policy = "none"
    return {
        "schema_version": "campaign_phase_gate_contract_v1",
        "campaign_id": "camp_20260409_family_proof",
        "phase_id": phase_id,
        "decision_id": f"decision_{phase_id}_{normalized_verdict}",
        "verdict": normalized_verdict,
        "evidence_refs": [f"governance/{phase_id}.json"],
        "caveats": list(caveats or []),
        "proof_gaps": list(proof_gaps or []),
        "downstream_unlock": {
            "policy": policy,
            "allowed": unlock_allowed,
            "unlocked_phase_ids": list(unlocked_phase_ids or ([phase_id] if unlock_allowed else [])),
        },
        "handoff": {
            "summary": f"{phase_id} gate handoff",
            "evidence_refs": [f"governance/{phase_id}.json"],
            "caveats": list(caveats or []),
            "proof_gaps": list(proof_gaps or []),
            "next_decision": {
                "decision_owner": "campaign_parent",
                "question": f"What should happen next for {phase_id}?",
                "recommended_action": "await_review" if normalized_verdict == "waiting_review" else "launch_downstream",
            },
        },
    }


def _receipt(phase_id: str, status: str, *, attempt_count: int | None = None, max_attempts: int | None = None) -> Dict[str, Any]:
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
    job_budget_cost_ledger_store.append_allocation_record(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "allocation_uid": f"{job_uid}_alloc_{amount}",
            "job_uid": job_uid,
            "recorded_at_utc": "2026-04-09T12:00:00Z",
            "amount_minor_units": amount,
            "unit": "usd_cents",
            "allocation_reason_code": "initial_grant",
        },
        repo_root=repo_root,
    )


def _cost(job_uid: str, amount: int, *, repo_root: Path) -> None:
    job_budget_cost_ledger_store.append_cost_event_record(
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


def _happy_path_case(*, repo_root: Path) -> Dict[str, Any]:
    record = _base_record()
    record_validation = campaign_record_contract.validate_campaign_record(record)
    gate_prepare = _gate("phase_define_contract", "ready")
    gate_execute = _gate("phase_launch_execution", "ready")
    gate_validation = campaign_phase_gate_contract.validate_phase_gate_decision(gate_execute)
    launch = campaign_phase_execution_contract.materialize_phase_launch_contract(
        campaign_id=record["campaign_id"],
        parent_binding=record["parent_binding"],
        phase=record["phases"][1],
        gate_decision=gate_execute,
    )
    _allocate("job_phase_define_contract", 900, repo_root=repo_root)
    _cost("job_phase_define_contract", 200, repo_root=repo_root)
    _allocate("job_phase_launch_execution", 800, repo_root=repo_root)
    _cost("job_phase_launch_execution", 300, repo_root=repo_root)
    reconciliation = campaign_lifecycle_reconciliation.reconcile_campaign_lifecycle(
        campaign_record=record,
        phase_gate_decisions=[gate_prepare, gate_execute],
        phase_runtime_facts=[
            {"phase_id": "phase_define_contract", "receipts": [_receipt("phase_define_contract", "ok")]},
            {"phase_id": "phase_launch_execution", "receipts": [_receipt("phase_launch_execution", "ok")]},
        ],
        repo_root=repo_root,
    )
    summary = campaign_operator_summary.build_campaign_operator_summary(
        campaign_record=record,
        phase_gate_decisions=[gate_prepare, gate_execute],
        phase_runtime_facts=[
            {"phase_id": "phase_define_contract", "receipts": [_receipt("phase_define_contract", "ok")]},
            {"phase_id": "phase_launch_execution", "receipts": [_receipt("phase_launch_execution", "ok")]},
        ],
        repo_root=repo_root,
    )
    return {
        "case_id": "happy_path_bounded_campaign_completion",
        "record_ok": record_validation["ok"],
        "gate_ok": gate_validation["ok"],
        "launch_ok": launch["ok"],
        "reconciled_campaign_state": reconciliation["reconciled_campaign_state"],
        "operator_readiness": summary["operator_readiness"],
        "terminal_phase_ids": list(reconciliation["terminal_phase_ids"]),
        "launch_contract_id": str((launch.get("launch_contract") or {}).get("launch_contract_id") or ""),
    }


def _partial_ready_case(*, repo_root: Path) -> Dict[str, Any]:
    record = _base_record()
    gate_prepare = _gate("phase_define_contract", "ready")
    gate_execute = _gate(
        "phase_launch_execution",
        "partial_ready",
        caveats=["operator review still required for full completion"],
    )
    gate_validation = campaign_phase_gate_contract.validate_phase_gate_decision(gate_execute)
    launch = campaign_phase_execution_contract.materialize_phase_launch_contract(
        campaign_id=record["campaign_id"],
        parent_binding=record["parent_binding"],
        phase=record["phases"][1],
        gate_decision=gate_execute,
    )
    _allocate("job_phase_define_contract", 500, repo_root=repo_root)
    _cost("job_phase_define_contract", 100, repo_root=repo_root)
    _allocate("job_phase_launch_execution", 400, repo_root=repo_root)
    reconciliation = campaign_lifecycle_reconciliation.reconcile_campaign_lifecycle(
        campaign_record=record,
        phase_gate_decisions=[gate_prepare, gate_execute],
        phase_runtime_facts=[
            {"phase_id": "phase_define_contract", "receipts": [_receipt("phase_define_contract", "ok")]},
            {"phase_id": "phase_launch_execution"},
        ],
        repo_root=repo_root,
    )
    summary = campaign_operator_summary.build_campaign_operator_summary(
        campaign_record=record,
        phase_gate_decisions=[gate_prepare, gate_execute],
        phase_runtime_facts=[
            {"phase_id": "phase_define_contract", "receipts": [_receipt("phase_define_contract", "ok")]},
            {"phase_id": "phase_launch_execution"},
        ],
        repo_root=repo_root,
    )
    return {
        "case_id": "partial_ready_caveated_launch_remains_explicit",
        "gate_ok": gate_validation["ok"],
        "launch_ok": launch["ok"],
        "reconciled_campaign_state": reconciliation["reconciled_campaign_state"],
        "launchable_phase_ids": list(reconciliation["launchable_phase_ids"]),
        "operator_readiness": summary["operator_readiness"],
        "next_step_kind": str((summary.get("next_step_logic") or {}).get("kind") or ""),
    }


def _blocked_gate_case(*, repo_root: Path) -> Dict[str, Any]:
    record = _base_record()
    gate_prepare = _gate("phase_define_contract", "ready")
    gate_execute = _gate("phase_launch_execution", "blocked", proof_gaps=["handoff evidence missing"])
    launch = campaign_phase_execution_contract.materialize_phase_launch_contract(
        campaign_id=record["campaign_id"],
        parent_binding=record["parent_binding"],
        phase=record["phases"][1],
        gate_decision=gate_execute,
    )
    _allocate("job_phase_define_contract", 500, repo_root=repo_root)
    _allocate("job_phase_launch_execution", 500, repo_root=repo_root)
    reconciliation = campaign_lifecycle_reconciliation.reconcile_campaign_lifecycle(
        campaign_record=record,
        phase_gate_decisions=[gate_prepare, gate_execute],
        phase_runtime_facts=[
            {"phase_id": "phase_define_contract", "receipts": [_receipt("phase_define_contract", "ok")]},
            {"phase_id": "phase_launch_execution"},
        ],
        repo_root=repo_root,
    )
    return {
        "case_id": "blocked_gate_prevents_phase_progression",
        "launch_ok": launch["ok"],
        "launch_error_codes": sorted({str(row.get("code") or "") for row in list(launch.get("errors") or []) if row}),
        "reconciled_campaign_state": reconciliation["reconciled_campaign_state"],
        "blocked_phase_ids": list(reconciliation["blocked_phase_ids"]),
    }


def _restart_review_case(*, repo_root: Path) -> Dict[str, Any]:
    record = _base_record()
    gate_prepare = _gate("phase_define_contract", "ready")
    gate_execute = _gate(
        "phase_launch_execution",
        "waiting_review",
        proof_gaps=["operator signoff missing"],
    )
    _allocate("job_phase_define_contract", 500, repo_root=repo_root)
    _allocate("job_phase_launch_execution", 500, repo_root=repo_root)
    reconciliation = campaign_lifecycle_reconciliation.reconcile_campaign_lifecycle(
        campaign_record=record,
        phase_gate_decisions=[gate_prepare, gate_execute],
        phase_runtime_facts=[
            {"phase_id": "phase_define_contract", "receipts": [_receipt("phase_define_contract", "ok")]},
            {"phase_id": "phase_launch_execution", "continuation_packet_ids": ["packet_phase_launch_execution_review"]},
        ],
        repo_root=repo_root,
    )
    summary = campaign_operator_summary.build_campaign_operator_summary(
        campaign_record=record,
        phase_gate_decisions=[gate_prepare, gate_execute],
        phase_runtime_facts=[
            {"phase_id": "phase_define_contract", "receipts": [_receipt("phase_define_contract", "ok")]},
            {"phase_id": "phase_launch_execution", "continuation_packet_ids": ["packet_phase_launch_execution_review"]},
        ],
        repo_root=repo_root,
    )
    return {
        "case_id": "restart_reconciliation_keeps_review_gate_visible",
        "reconciled_campaign_state": reconciliation["reconciled_campaign_state"],
        "review_phase_ids": list(reconciliation["review_phase_ids"]),
        "operator_readiness": summary["operator_readiness"],
        "next_step_kind": str((summary.get("next_step_logic") or {}).get("kind") or ""),
    }


def _invalid_claim_case() -> Dict[str, Any]:
    invalid_handoff_gate = _gate("phase_launch_execution", "partial_ready")
    invalid_handoff_gate["caveats"] = []
    invalid_handoff_gate["handoff"]["caveats"] = []
    invalid_handoff_gate["handoff"]["evidence_refs"] = []
    bad_gate = campaign_phase_gate_contract.validate_phase_gate_decision(invalid_handoff_gate)

    recursive_record = _base_record(phase_job_kind="swarm_launch", phase_job_shape="fanout_job")
    recursive_phase = dict(recursive_record["phases"][1])
    recursive_phase["execution_binding"] = dict(recursive_phase["execution_binding"])
    recursive_phase["execution_binding"]["fanout"] = {
        "mode": "supervised",
        "child_job_kind": "fanout",
    }
    binding = campaign_phase_execution_contract.materialize_phase_launch_contract(
        campaign_id=recursive_record["campaign_id"],
        parent_binding=recursive_record["parent_binding"],
        phase=recursive_phase,
        gate_decision=_gate("phase_launch_execution", "ready"),
    )
    return {
        "case_id": "invalid_handoff_and_recursive_launch_claims_fail_closed",
        "gate_ok": bad_gate["ok"],
        "gate_error_codes": sorted({str(row.get("code") or "") for row in list(bad_gate.get("errors") or []) if row}),
        "launch_ok": binding["ok"],
        "launch_error_messages": sorted({str(row.get("message") or "") for row in list(binding.get("errors") or []) if row}),
    }


def collect_campaign_execution_family_proof(*, repo_root: Path) -> Dict[str, Any]:
    root = Path(repo_root)
    cases = [
        _happy_path_case(repo_root=root / "happy_path"),
        _partial_ready_case(repo_root=root / "partial_ready"),
        _blocked_gate_case(repo_root=root / "blocked_gate"),
        _restart_review_case(repo_root=root / "restart_review"),
        _invalid_claim_case(),
    ]
    passing_case_ids = [
        str(case.get("case_id") or "")
        for case in cases
        if (
            (case.get("case_id") == "happy_path_bounded_campaign_completion" and case.get("launch_ok") and case.get("reconciled_campaign_state") == "completed")
            or (case.get("case_id") == "partial_ready_caveated_launch_remains_explicit" and case.get("launch_ok") and case.get("operator_readiness") == "partial_ready")
            or (case.get("case_id") == "blocked_gate_prevents_phase_progression" and not case.get("launch_ok") and case.get("reconciled_campaign_state") == "blocked")
            or (case.get("case_id") == "restart_reconciliation_keeps_review_gate_visible" and case.get("reconciled_campaign_state") == "review")
            or (case.get("case_id") == "invalid_handoff_and_recursive_launch_claims_fail_closed" and not case.get("gate_ok") and not case.get("launch_ok"))
        )
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "proof_child_uid": PROOF_CHILD_UID,
        "parent_uid": PARENT_UID,
        "child_uids": list(CHILD_UIDS),
        "anti_pattern_bans": list(ANTI_PATTERN_BANS),
        "case_count": len(cases),
        "all_cases_passed": len(passing_case_ids) == len(cases),
        "passing_case_ids": passing_case_ids,
        "cases": cases,
    }
