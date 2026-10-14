from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import campaign_phase_execution_contract as contract


def _base_gate(*, phase_id: str = "phase_launch_bindings") -> dict:
    return {
        "schema_version": "campaign_phase_gate_contract_v1",
        "campaign_id": "camp-20260409-bindings",
        "phase_id": phase_id,
        "decision_id": "gate-phase-launch-001",
        "verdict": "ready",
        "evidence_refs": ["governance/campaign_phase_gate_contract_v1.json"],
        "caveats": [],
        "proof_gaps": [],
        "downstream_unlock": {
            "policy": "full_ready",
            "allowed": True,
            "unlocked_phase_ids": [phase_id],
        },
        "handoff": {
            "summary": "The phase may launch with the recorded execution binding.",
            "evidence_refs": ["governance/campaign_phase_gate_contract_v1.json"],
            "caveats": [],
            "proof_gaps": [],
            "next_decision": {
                "decision_owner": "campaign_parent",
                "question": "Should the phase launch now?",
                "recommended_action": "launch_downstream",
            },
        },
    }


def _base_phase(*, job_shape: str = "verification_loop_job", job_kind: str = "build_verify_loop") -> dict:
    return {
        "phase_id": "phase_launch_bindings",
        "label": "Launch bindings",
        "job_shape": job_shape,
        "state": "declared",
        "depends_on": ["phase_define_contract"],
        "output_refs": [],
        "truth_refs": {
            "ledger_uids": ["PIS-20260409-121602Z-L1P4"],
            "job_ids": [],
            "run_ids": [],
            "receipt_refs": [],
            "continuation_packet_ids": [],
            "artifact_refs": [],
        },
        "phase_event_refs": [],
        "execution_binding": {
            "binding_id": "bind-001",
            "job_kind": job_kind,
            "instruction_refs": [
                "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-121602Z-L1P4__implement-campaign-phase-execution-bindings-and-launch-contract/pis.md"
            ],
            "durable_context_refs": [
                "governance/campaign_phase_execution_contract_v1.json",
            ],
            "expected_evidence": {
                "summary": "Record whether the phase launch produced the expected bounded work item.",
                "artifact_refs": ["governance/campaign_phase_execution_contract_v1.json"],
                "next_decision": {
                    "decision_owner": "campaign_parent",
                    "question": "Did the launched work satisfy the phase evidence target?",
                },
            },
        },
    }


def test_campaign_phase_execution_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.campaign_phase_execution_contract_v1()

    assert payload.version == "campaign_phase_execution_contract_v1"
    assert payload.uid == "PIS-20260409-121602Z-L1P4"
    assert payload.canonical_artifact_rel.as_posix() == "governance/campaign_phase_execution_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_campaign_phase_execution_v1.md"
    assert payload.supported_phase_job_kinds == ("single_turn", "build_verify_loop", "swarm_launch")


def test_campaign_phase_execution_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/campaign_phase_execution_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert tuple(payload["supported_phase_job_kinds"]) == contract.SUPPORTED_PHASE_JOB_KINDS
    assert tuple(payload["execution_binding_required_fields"]) == contract.EXECUTION_BINDING_REQUIRED_FIELDS
    assert tuple(payload["expected_evidence_required_fields"]) == contract.EXPECTED_EVIDENCE_REQUIRED_FIELDS


def test_validate_campaign_phase_execution_contract_v1_returns_no_issues() -> None:
    assert contract.validate_campaign_phase_execution_contract_v1() == ()


def test_materialize_phase_launch_contract_for_verification_loop_preserves_bundle_and_expected_evidence() -> None:
    result = contract.materialize_phase_launch_contract(
        campaign_id="camp-20260409-bindings",
        parent_binding={"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
        phase=_base_phase(job_shape="verification_loop_job", job_kind="single_turn"),
        gate_decision=_base_gate(),
    )

    assert result["ok"] is True
    launch_contract = result["launch_contract"]
    assert launch_contract["launch_job_kind"] == "single_turn"
    assert launch_contract["launch_job_shape"] == "verification_loop_job"
    assert launch_contract["required_context_bundle"] == "lab_core_v1"
    assert launch_contract["effective_context_refs"][:3] == [
        "governance/agents/prompts/_all_codex.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
    ]
    assert launch_contract["durable_delta_refs"] == [
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-121602Z-L1P4__implement-campaign-phase-execution-bindings-and-launch-contract/pis.md",
        "governance/campaign_phase_execution_contract_v1.json",
    ]
    assert launch_contract["expected_evidence"]["next_decision"]["decision_owner"] == "campaign_parent"
    assert launch_contract["integrator_owner"] == "campaign_phase_integrator"


def test_materialize_phase_launch_contract_rejects_under_specified_or_mismatched_binding() -> None:
    phase = _base_phase(job_shape="verification_loop_job", job_kind="fanout")
    phase["execution_binding"]["expected_evidence"] = {}

    result = contract.materialize_phase_launch_contract(
        campaign_id="camp-20260409-bindings",
        parent_binding={"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
        phase=phase,
        gate_decision=_base_gate(),
    )

    assert result["ok"] is False
    messages = {row["message"] for row in result["errors"]}
    assert "execution_binding.job_kind must map to the declared phase job_shape" in messages
    assert "execution_binding.expected_evidence.summary is required" in messages


def test_materialize_phase_launch_contract_rejects_gate_decision_with_mismatched_ids() -> None:
    for gate_field, wrong_value, expected_message in (
        ("campaign_id", "camp-20260409-other", "gate decision campaign_id must match the launch request campaign_id"),
        ("phase_id", "phase_other", "gate decision phase_id must match the launch request phase_id"),
    ):
        gate = _base_gate()
        gate[gate_field] = wrong_value

        result = contract.materialize_phase_launch_contract(
            campaign_id="camp-20260409-bindings",
            parent_binding={"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
            phase=_base_phase(),
            gate_decision=gate,
        )

        assert result["ok"] is False
        assert any(row["message"] == expected_message for row in result["errors"])


def test_materialize_phase_launch_contract_supports_supervised_fanout_and_rejects_recursive_children() -> None:
    phase = _base_phase(job_shape="fanout_job", job_kind="swarm_launch")
    phase["execution_binding"]["fanout"] = {
        "mode": "supervised",
        "child_job_kind": "build_verify_loop",
    }

    success = contract.materialize_phase_launch_contract(
        campaign_id="camp-20260409-bindings",
        parent_binding={"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
        phase=phase,
        gate_decision=_base_gate(),
    )
    assert success["ok"] is True
    assert success["launch_contract"]["launch_payload"]["fanout"]["child_job_shape"] == "verification_loop_job"

    phase["execution_binding"]["fanout"]["child_job_kind"] = "fanout"
    blocked = contract.materialize_phase_launch_contract(
        campaign_id="camp-20260409-bindings",
        parent_binding={"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
        phase=phase,
        gate_decision=_base_gate(),
    )
    assert blocked["ok"] is False
    assert any("illegal parent-child job-shape relationship in v1: fanout_job -> fanout_job" in row["message"] for row in blocked["errors"])


def test_materialize_phase_launch_contract_fails_closed_without_gate_unlock_or_binding() -> None:
    phase = _base_phase()
    phase.pop("execution_binding")
    gate = _base_gate()
    gate["downstream_unlock"]["unlocked_phase_ids"] = ["other_phase"]

    result = contract.materialize_phase_launch_contract(
        campaign_id="camp-20260409-bindings",
        parent_binding={"binding_type": "ledger_uid", "binding_id": "PIS-20260409-121217Z-2A6A"},
        phase=phase,
        gate_decision=gate,
    )

    assert result["ok"] is False
    assert any(row["field"] == "execution_binding" for row in result["errors"])
    assert any(row["code"] == "gate_not_launchable" for row in result["errors"])
