from __future__ import annotations

from infra.controlplane import campaign_operator_summary as summary
from infra.controlplane import job_budget_cost_ledger_store as ledger_store

from .helpers.contracts import ScenarioContract, assert_scenario_contract


CONTRACT = ScenarioContract(
    scenario_id="campaign_execution_e2e",
    operator_job="Track a multi-phase campaign and understand whether the next phase is launchable, blocked, or waiting for review.",
    capability="Campaign operator summary turns phase records, gate decisions, runtime facts, and budget truth into one operator-facing campaign state.",
    durable_truth=(
        "campaign record",
        "phase gate decisions",
        "phase runtime facts",
        "budget ledger records",
        "campaign operator summary payload",
    ),
    manual_repair_eliminated="Reconstructing campaign readiness and next action manually from scattered phase receipts and gate artifacts.",
    primary_intent_sheets=("IS-10",),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "deterministic phase gate and receipt fixtures",
        "temp-repo budget ledger seeding",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "writing campaign projection records manually",
        "fabricating phase closeout state",
    ),
)


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


def _allocate(job_uid: str, amount: int, *, repo_root) -> None:
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


def _cost(job_uid: str, amount: int, *, repo_root) -> None:
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


def test_campaign_execution_e2e(first_wave_root) -> None:
    assert_scenario_contract(CONTRACT)
    _allocate("job_phase_a", 1_000, repo_root=first_wave_root)
    _cost("job_phase_a", 250, repo_root=first_wave_root)
    _allocate("job_phase_b", 600, repo_root=first_wave_root)
    _cost("job_phase_b", 100, repo_root=first_wave_root)

    launchable = summary.build_campaign_operator_summary(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[_gate("phase_a", "ready"), _gate("phase_b", "partial_ready")],
        phase_runtime_facts=[{"phase_id": "phase_a", "receipts": [_receipt("phase_a", "ok")]}, {"phase_id": "phase_b"}],
        repo_root=first_wave_root,
    )
    assert launchable["operator_readiness"] == "partial_ready"
    assert launchable["current_phase"]["phase_id"] == "phase_b"
    assert launchable["current_gate"]["verdict"] == "partial_ready"
    assert launchable["next_step_logic"]["kind"] == "launchable_phase"
    assert launchable["budget_posture"]["status"] == "ok"
    assert launchable["operator_evidence"]["focus_phase_id"] == "phase_b"

    waiting_review = summary.build_campaign_operator_summary(
        campaign_record=_campaign_record(),
        phase_gate_decisions=[
            _gate("phase_a", "waiting_review", proof_gaps=["operator signoff missing"]),
            _gate("phase_b", "ready"),
        ],
        phase_runtime_facts=[{"phase_id": "phase_a", "continuation_packet_ids": ["packet_phase_a_review"]}],
        repo_root=first_wave_root,
    )
    assert waiting_review["operator_readiness"] == "waiting_review"
    assert waiting_review["review_summary"]["count"] == 1
    assert waiting_review["proof_gaps"][0]["summary"] == "operator signoff missing"
    assert waiting_review["next_step_logic"]["kind"] == "review_gate"
