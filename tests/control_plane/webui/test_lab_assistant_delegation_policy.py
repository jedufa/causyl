from __future__ import annotations

import pytest

from infra.webui import server_lab_assistant_delegation_policy as policy


def _raw_request(**overrides: object) -> dict:
    base = {
        "request_id": "delegate_raw_01",
        "surface": "workflow",
        "classification": "delegate",
        "reason_codes": ["proof_heavy"],
        "owner_ref": {"task_id": "task_01a2", "ledger_uid": "PIS-20260312-161622Z-01A2"},
        "summary": "Launch a workflow",
        "requested_action": "Start the bounded workflow",
        "expected_evidence": [{"kind": "run_log", "description": "workflow log", "required": True}],
        "next_decision": {"kind": "review_evidence", "summary": "Review returned evidence"},
        "mission_fit": {
            "status": "mission_aligned",
            "summary": "Work fits the laboratory mission boundary.",
            "source": "test_fixture",
            "requires_note": False,
        },
        "correlation_id": "corr_01a2",
        "launch_turn_id": "turn_01a2",
        "result_correlation_basis": "request_id",
        "idempotency_key": "idem_01a2",
        "launch_cause": "assistant_turn",
    }
    base.update(overrides)
    return base


def test_policy_matrix_covers_direct_delegate_blocked_and_waiting_paths() -> None:
    direct = policy.classify_answer_vs_delegate(
        {
            "summary": "answer a short local question",
            "estimated_duration_s": 20,
            "requires_new_evidence": False,
            "requires_external_action": False,
            "proof_heavy": False,
            "multi_step": False,
            "outlives_turn": False,
        }
    )
    assert direct["classification"] == "answer_directly"
    assert "short_local_work" in direct["reason_codes"]

    delegated = policy.classify_answer_vs_delegate(
        {
            "summary": "run a long proof-heavy workflow",
            "preferred_surface": "workflow",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "requires_external_action": True,
            "proof_heavy": True,
            "multi_step": True,
            "outlives_turn": True,
        }
    )
    assert delegated["classification"] == "delegate"
    assert "exceeds_direct_answer_window" in delegated["reason_codes"]
    assert "missing_required_evidence" in delegated["reason_codes"]
    assert "requires_tool_surface" in delegated["reason_codes"]

    blocked = policy.classify_answer_vs_delegate(
        {
            "summary": "delegate to an unofficial surface",
            "preferred_surface": "mailbox",
        }
    )
    assert blocked["classification"] == "blocked"
    assert blocked["reason_codes"] == ["unsupported_delegation_surface"]

    waiting = policy.classify_answer_vs_delegate(
        {
            "summary": "need operator decision before acting",
            "requires_operator_input": True,
            "preferred_surface": "ledger",
        }
    )
    assert waiting["classification"] == "waiting_on_operator"
    assert waiting["reason_codes"] == ["operator_input_required"]


def test_long_horizon_mission_fit_fails_closed_without_alignment_and_requires_partial_note() -> None:
    unclear = policy.classify_answer_vs_delegate(
        {
            "summary": "do the thing",
            "preferred_surface": "workflow",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )
    assert unclear["classification"] == "blocked"
    assert unclear["reason_codes"] == ["mission_fit_unclear_for_long_horizon_work"]
    assert unclear["mission_fit"]["status"] == "unclear"

    partial_without_note = policy.classify_answer_vs_delegate(
        {
            "summary": "help with a side request near the repo",
            "preferred_surface": "workflow",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
            "mission_alignment": "partially_aligned",
        }
    )
    assert partial_without_note["classification"] == "blocked"
    assert partial_without_note["reason_codes"] == ["mission_alignment_note_required"]

    partial_with_note = policy.classify_answer_vs_delegate(
        {
            "summary": "help with a side request near the repo",
            "preferred_surface": "workflow",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
            "mission_alignment": "partially_aligned",
            "mission_alignment_note": "Allowed because it unblocks the operator's current lab software work.",
        }
    )
    assert partial_with_note["classification"] == "delegate"
    assert partial_with_note["mission_fit"]["status"] == "partially_aligned"
    assert partial_with_note["mission_fit"]["note"] == "Allowed because it unblocks the operator's current lab software work."

    misaligned = policy.classify_answer_vs_delegate(
        {
            "summary": "organize a freeform multi-agent social behavior experiment",
            "preferred_surface": "workflow",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )
    assert misaligned["classification"] == "blocked"
    assert misaligned["reason_codes"] == ["mission_misaligned"]
    assert misaligned["mission_fit"]["status"] == "mission_misaligned"


def test_typed_delegation_request_validates_all_allowed_v1_surfaces() -> None:
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "collect proof through an external surface",
            "preferred_surface": "verifier",
            "estimated_duration_s": 90,
            "requires_new_evidence": True,
        }
    )
    assert classification["classification"] == "delegate"

    for surface in sorted(policy.ALLOWED_V1_SURFACES):
        request = policy.build_typed_delegation_request(
            classification=classification,
            surface=surface,
            owner_ref={"task_id": "task_01a2", "ledger_uid": "PIS-20260312-161622Z-01A2"},
            summary=f"Use the {surface} surface",
            requested_action=f"Run the {surface} action",
            expected_evidence=[
                {"kind": "status_artifact", "description": f"{surface} status record", "required": True}
            ],
            next_decision={"kind": "review_evidence", "summary": f"Review {surface} evidence", "owner": "assistant"},
            input_payload={"scope": "focused"},
        )
        assert request["surface"] == surface
        assert request["classification"] == "delegate"
        assert request["owner_ref"]["task_id"] == "task_01a2"
        assert request["expected_evidence"][0]["required"] is True
        assert request["next_decision"]["kind"] == "review_evidence"
        assert request["correlation_id"].startswith("corr_")
        assert request["launch_turn_id"].startswith("turn_")
        assert request["result_correlation_basis"] == "request_id"
        assert request["mission_fit"]["status"] == "mission_aligned"
        assert request["input_payload"]["mission_fit"]["status"] == "mission_aligned"


def test_typed_delegation_request_fails_closed_when_required_contract_fields_are_missing() -> None:
    with pytest.raises(policy.DelegationPolicyError, match="expected_evidence"):
        policy.validate_typed_delegation_request(_raw_request(expected_evidence=[]))

    with pytest.raises(policy.DelegationPolicyError, match="next_decision"):
        policy.validate_typed_delegation_request(_raw_request(next_decision={}))

    with pytest.raises(policy.DelegationPolicyError, match="unsupported delegation surface"):
        policy.validate_typed_delegation_request(_raw_request(surface="untyped_mailbox"))

    with pytest.raises(policy.DelegationPolicyError, match="correlation_id is required"):
        policy.validate_typed_delegation_request(_raw_request(correlation_id=""))

    with pytest.raises(policy.DelegationPolicyError, match="launch_turn_id is required"):
        policy.validate_typed_delegation_request(_raw_request(launch_turn_id=""))


def test_request_and_result_envelopes_round_trip_across_supported_and_reserved_surfaces() -> None:
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": "delegate for round-trip proof",
            "preferred_surface": "workflow",
            "estimated_duration_s": 90,
            "requires_new_evidence": True,
        }
    )
    assert classification["classification"] == "delegate"

    for surface in sorted(policy.ALLOWED_ENVELOPE_SURFACES | {"queue"}):
        request = policy.build_typed_delegation_request(
            classification=classification,
            surface=surface,
            owner_ref={"task_id": "task_a3f2", "ledger_uid": "PIS-20260312-224201Z-A3F2"},
            summary=f"Run {surface}",
            requested_action=f"launch {surface}",
            expected_evidence=[{"kind": "status_artifact", "description": f"{surface} evidence", "required": True}],
            next_decision={"kind": "review_evidence", "summary": f"Review {surface} evidence"},
            correlation_id=f"corr_{surface}",
            launch_turn_id=f"turn_{surface}",
            idempotency_key=f"idem_{surface}",
            result_correlation_basis="request_id",
            launch_cause="scheduled_build",
        )
        round_tripped_request = policy.validate_typed_delegation_request(dict(request))
        assert round_tripped_request == request
        if surface == "queue":
            assert request["surface"] == "execution_queue"

        result = policy.build_typed_delegation_result(
            request=request,
            status="partial",
            summary=f"{surface} returned partial proof",
            native_status="running",
            evidence_status="pending",
            evidence_refs=[],
            artifact_refs=["runtime/state/demo.json"],
            next_decision={"kind": "assistant_continue", "summary": f"Wait for {surface} evidence"},
            worker_ref=f"{request['surface']}:demo",
            producer="test",
        )
        round_tripped_result = policy.validate_typed_delegation_result(dict(result))
        assert round_tripped_result == result
        assert result["surface"] == request["surface"]
        assert result["status"] == "partial"
        assert result["correlation_id"] == request["correlation_id"]
        assert result["launch_turn_id"] == request["launch_turn_id"]


def test_result_envelope_normalizes_status_aliases_and_rejects_invalid_correlation_basis() -> None:
    request = policy.validate_typed_delegation_request(_raw_request(surface="queue"))
    assert request["surface"] == "execution_queue"

    normalized = policy.validate_typed_delegation_result(
        {
            "result_id": "result_a3f2",
            "request_id": request["request_id"],
            "surface": "queue",
            "status": "timeout",
            "summary": "The queue run timed out",
            "native_status": "timeout",
            "evidence_status": "missing_result",
            "evidence_refs": [],
            "artifact_refs": [],
            "next_decision": {"kind": "assistant_continue", "summary": "Inspect timeout"},
            "correlation_id": request["correlation_id"],
            "launch_turn_id": request["launch_turn_id"],
            "detached_job_id": "job_a3f2",
            "result_correlation_basis": "detached_job_id",
            "owner_ref": request["owner_ref"],
        }
    )
    assert normalized["surface"] == "execution_queue"
    assert normalized["status"] == "timed_out"

    with pytest.raises(policy.DelegationPolicyError, match="detached_job_id basis requires detached_job_id"):
        policy.validate_typed_delegation_result(
            {
                "result_id": "result_a3f2_missing_job",
                "request_id": request["request_id"],
                "surface": "workflow",
                "status": "completed",
                "summary": "workflow finished",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": [],
                "artifact_refs": [],
                "next_decision": {"kind": "completion_check", "summary": "Check completion"},
                "correlation_id": request["correlation_id"],
                "launch_turn_id": request["launch_turn_id"],
                "result_correlation_basis": "detached_job_id",
                "owner_ref": request["owner_ref"],
            }
        )
