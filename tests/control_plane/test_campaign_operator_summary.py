from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import campaign_operator_summary as summary
from infra.controlplane import job_budget_cost_ledger_store as ledger_store


def _campaign_record() -> dict:
    return {
        "schema_version": "campaign_record_contract_v1",
        "campaign_id": "camp_20260409_summary",
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
                    "receipt_refs": ["runtime/receipts/phase_a.json"],
                    "continuation_packet_ids": [],
                    "artifact_refs": ["artifacts/phase_a.md"],
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


def _gate(phase_id: str, verdict: str, *, allowed: bool = True, proof_gaps: list[str] | None = None) -> dict:
    policy = "full_ready" if verdict == "ready" else "review_required"
    caveats = []
    if verdict == "partial_ready":
        policy = "allowed_with_caveats"
        caveats = ["handoff caveat"]
    if verdict in {"blocked", "waiting_review"}:
        allowed = False
    return {
        "schema_version": "campaign_phase_gate_contract_v1",
        "campaign_id": "camp_20260409_summary",
        "phase_id": phase_id,
        "decision_id": f"decision_{phase_id}",
        "verdict": verdict,
        "evidence_refs": [f"governance/{phase_id}.json"],
        "caveats": caveats,
        "proof_gaps": list(proof_gaps or []),
        "downstream_unlock": {
            "policy": policy,
            "allowed": allowed,
            "unlocked_phase_ids": [phase_id] if allowed else [],
        },
        "handoff": {
            "summary": f"{phase_id} gate summary",
            "evidence_refs": [f"governance/{phase_id}.json"],
            "caveats": caveats,
            "proof_gaps": list(proof_gaps or []),
            "next_decision": {
                "decision_owner": "campaign_parent",
                "question": f"what next for {phase_id}?",
                "recommended_action": "await_review" if verdict == "waiting_review" else "launch_downstream",
            },
        },
    }


def _receipt(phase_id: str, status: str) -> dict:
    return {
        "receipt_id": f"{phase_id}_{status}",
        "job_id": f"job_{phase_id}",
        "run_id": f"run_{phase_id}_{status}",
        "status": status,
    }


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


def test_campaign_operator_summary_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/campaign_operator_summary_contract_v1.json").read_text(encoding="utf-8"))
    assert payload["canonical_doc_rel"] == summary.CONTRACT_DOC_REL.as_posix()
    assert tuple(payload["operator_readiness_states"]) == summary.OPERATOR_READINESS_STATES
    assert tuple(payload["required_summary_fields"]) == summary.REQUIRED_SUMMARY_FIELDS


def test_validate_campaign_operator_summary_contract_v1_returns_no_issues() -> None:
    assert summary.validate_campaign_operator_summary_contract_v1() == ()


def test_campaign_operator_summary_surfaces_partial_ready_and_budget_posture(tmp_path: Path) -> None:
    _allocate("job_phase_a", 1_000, repo_root=tmp_path)
    _cost("job_phase_a", 250, repo_root=tmp_path)
    _allocate("job_phase_b", 600, repo_root=tmp_path)
    _cost("job_phase_b", 100, repo_root=tmp_path)

    result = summary.build_campaign_operator_summary(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[_gate("phase_a", "ready"), _gate("phase_b", "partial_ready")],
        phase_runtime_facts=[{"phase_id": "phase_a", "receipts": [_receipt("phase_a", "ok")]}, {"phase_id": "phase_b"}],
        repo_root=tmp_path,
    )

    assert result["operator_readiness"] == "partial_ready"
    assert result["current_phase"]["phase_id"] == "phase_b"
    assert result["current_gate"]["verdict"] == "partial_ready"
    assert result["next_step_logic"]["kind"] == "launchable_phase"
    assert result["budget_posture"]["status"] == "ok"
    assert result["operator_evidence"]["focus_phase_id"] == "phase_b"


def test_campaign_operator_summary_keeps_waiting_review_explicit(tmp_path: Path) -> None:
    _allocate("job_phase_a", 100, repo_root=tmp_path)
    _allocate("job_phase_b", 100, repo_root=tmp_path)

    result = summary.build_campaign_operator_summary(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[_gate("phase_a", "waiting_review", proof_gaps=["operator signoff missing"]), _gate("phase_b", "ready")],
        phase_runtime_facts=[{"phase_id": "phase_a", "continuation_packet_ids": ["packet_phase_a_review"]}],
        repo_root=tmp_path,
    )

    assert result["operator_readiness"] == "waiting_review"
    assert result["review_summary"]["count"] == 1
    assert result["proof_gaps"][0]["summary"] == "operator signoff missing"
    assert result["next_step_logic"]["kind"] == "review_gate"


def test_campaign_operator_summary_marks_ambiguity_as_uncertain(tmp_path: Path) -> None:
    _allocate("job_phase_a", 100, repo_root=tmp_path)
    _allocate("job_phase_b", 100, repo_root=tmp_path)
    record = _campaign_record()
    record["phases"][1]["truth_refs"]["job_ids"] = []

    result = summary.build_campaign_operator_summary(
        campaign_record=record,
        phase_gate_decisions=[_gate("phase_a", "ready"), _gate("phase_b", "ready")],
        phase_runtime_facts=[
            {"phase_id": "phase_a", "receipts": [_receipt("phase_a", "ok")]},
            {"phase_id": "phase_b", "continuation_packet_ids": ["packet_phase_b_running"]},
        ],
        repo_root=tmp_path,
    )

    assert result["operator_readiness"] == "uncertain"
    assert result["blocked_summary"]["count"] == 1
    assert any(item["kind"] == "ambiguity" for item in result["proof_gaps"])
