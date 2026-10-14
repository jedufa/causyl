from __future__ import annotations

import pytest

from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_routing_policy as routing


def _classification(surface: str) -> dict:
    return policy.classify_answer_vs_delegate(
        {
            "summary": f"delegate via {surface}",
            "preferred_surface": surface if surface in policy.ALLOWED_V1_SURFACES else "workflow",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )


def _request(surface: str, *, summary: str | None = None) -> dict:
    return policy.build_typed_delegation_request(
        classification=_classification(surface),
        surface=surface,
        owner_ref={"task_id": "d1e7", "ledger_uid": "PIS-20260312-224201Z-D1E7"},
        summary=summary or f"Use {surface}",
        requested_action=f"launch {surface}",
        expected_evidence=[{"kind": "status_artifact", "description": f"{surface} evidence", "required": True}],
        next_decision={"kind": "review_evidence", "summary": f"Review {surface} evidence"},
        correlation_id=f"corr_{surface}",
        launch_turn_id=f"turn_{surface}",
        idempotency_key=f"idem_{surface}",
        launch_cause="scheduled_build",
    )


def _request_with_classification(surface: str, classification: dict, *, summary: str | None = None) -> dict:
    return policy.build_typed_delegation_request(
        classification=classification,
        surface=surface,
        owner_ref={"task_id": "d1e7", "ledger_uid": "PIS-20260312-224201Z-D1E7"},
        summary=summary or f"Use {surface}",
        requested_action=f"launch {surface}",
        expected_evidence=[{"kind": "status_artifact", "description": f"{surface} evidence", "required": True}],
        next_decision={"kind": "review_evidence", "summary": f"Review {surface} evidence"},
        correlation_id=f"corr_{surface}_custom",
        launch_turn_id=f"turn_{surface}_custom",
        idempotency_key=f"idem_{surface}_custom",
        launch_cause="scheduled_build",
    )


def test_routing_matrix_selects_first_class_surfaces_and_records_contracts() -> None:
    retrieval = routing.route_typed_delegation_request(
        _request("retrieval"),
        routing_facts={"retrieval_required": True, "read_only": True},
    )
    assert retrieval["ok"] is True
    assert retrieval["decision"]["selected_surface"] == "retrieval"
    assert retrieval["decision"]["expected_execution_contract"]["read_only"] is True

    verifier = routing.route_typed_delegation_request(
        _request("verifier"),
        routing_facts={"verification_required": True, "proof_heavy": True},
    )
    assert verifier["ok"] is True
    assert verifier["decision"]["selected_surface"] == "verifier"
    assert "proof_request_without_code_changes" in verifier["decision"]["reason_codes"]

    workflow = routing.route_typed_delegation_request(
        _request("workflow"),
        routing_facts={"multi_step": True, "cross_role": True, "workflow_step_count": 3, "code_changes_expected": True},
    )
    assert workflow["ok"] is True
    assert workflow["decision"]["selected_surface"] == "workflow"
    assert workflow["decision"]["expected_execution_contract"]["checkpoint_boundary"] == "per_workflow_step"

    direct_turn = routing.route_typed_delegation_request(
        _request("direct_turn_job"),
        routing_facts={"outlives_turn": True, "needs_detached_result_delivery": True, "needs_immediate_start": True},
    )
    assert direct_turn["ok"] is True
    assert direct_turn["decision"]["selected_surface"] == "direct_turn_job"
    assert direct_turn["request"]["surface"] == "direct_turn_job"


def test_routing_reroutes_linear_workflow_to_queue_and_multistep_queue_to_workflow() -> None:
    queue = routing.route_typed_delegation_request(
        _request("workflow"),
        routing_facts={"code_changes_expected": True, "workflow_step_count": 1, "needs_immediate_start": True},
    )
    assert queue["ok"] is True
    assert queue["decision"]["selected_surface"] == "execution_queue"
    assert queue["decision"]["rerouted"] is True
    assert "workflow_overkill_for_linear_job" in queue["decision"]["reason_codes"]
    assert queue["request"]["surface"] == "execution_queue"

    workflow = routing.route_typed_delegation_request(
        _request("execution_queue"),
        routing_facts={"code_changes_expected": True, "multi_step": True, "workflow_step_count": 4},
    )
    assert workflow["ok"] is True
    assert workflow["decision"]["selected_surface"] == "workflow"
    assert workflow["decision"]["rerouted"] is True
    assert "queue_cannot_represent_multi_step_flow" in workflow["decision"]["reason_codes"]


def test_scheduler_requires_explicit_safe_background_shape_and_budget() -> None:
    rejected = routing.route_typed_delegation_request(
        _request("scheduler"),
        routing_facts={
            "requires_schedule": True,
            "needs_immediate_start": False,
            "scheduler_safe": False,
            "scheduler_job_kind": "single_turn",
        },
    )
    assert rejected["ok"] is False
    assert rejected["decision"]["status"] == "rejected"
    assert rejected["decision"]["reason_codes"] == ["scheduler_requires_explicit_safe_background_shape"]

    build_verify = routing.route_typed_delegation_request(
        _request("scheduler"),
        routing_facts={
            "requires_schedule": True,
            "needs_background_schedule": True,
            "needs_immediate_start": False,
            "scheduler_safe": True,
            "code_changes_expected": True,
            "scheduler_job_kind": "build_verify_loop",
            "scheduler_max_iterations": 4,
            "explicit_scheduler_justification": "overnight bounded build+verify loop",
        },
    )
    assert build_verify["ok"] is True
    assert build_verify["decision"]["selected_surface"] == "scheduler"
    assert build_verify["decision"]["expected_execution_contract"]["scheduler_max_iterations"] == 4


def test_scheduler_rejects_code_changing_default_shape_and_queue_rejects_backpressure() -> None:
    scheduler = routing.route_typed_delegation_request(
        _request("scheduler"),
        routing_facts={
            "requires_schedule": True,
            "needs_immediate_start": False,
            "scheduler_safe": True,
            "code_changes_expected": True,
            "scheduler_job_kind": "single_turn",
        },
    )
    assert scheduler["ok"] is False
    assert scheduler["decision"]["reason_codes"] == ["scheduler_code_change_requires_build_verify_loop"]

    queue = routing.route_typed_delegation_request(
        _request("execution_queue"),
        routing_facts={"code_changes_expected": True, "queue_backlog": 2, "queue_capacity": 2},
    )
    assert queue["ok"] is False
    assert queue["decision"]["reason_codes"] == ["execution_queue_backpressure"]


def test_verifier_and_retrieval_reject_code_changing_misuse() -> None:
    verifier = routing.route_typed_delegation_request(
        _request("verifier"),
        routing_facts={"verification_required": True, "code_changes_expected": True},
    )
    assert verifier["ok"] is False
    assert verifier["decision"]["reason_codes"] == ["verifier_read_only_only"]

    retrieval = routing.route_typed_delegation_request(
        _request("retrieval"),
        routing_facts={"retrieval_required": True, "read_only": True, "code_changes_expected": True},
    )
    assert retrieval["ok"] is False
    assert retrieval["decision"]["reason_codes"] == ["retrieval_read_only_only"]


def test_direct_turn_job_reroutes_when_commit_boundary_or_multistep_truth_is_needed() -> None:
    queue = routing.route_typed_delegation_request(
        _request("direct_turn_job"),
        routing_facts={"requires_commit_checkpoint": True, "queue_capacity": 3, "queue_backlog": 0},
    )
    assert queue["ok"] is True
    assert queue["decision"]["selected_surface"] == "execution_queue"
    assert "direct_turn_job_lacks_commit_boundary" in queue["decision"]["reason_codes"]

    workflow = routing.route_typed_delegation_request(
        _request("direct_turn_job"),
        routing_facts={"multi_step": True, "workflow_step_count": 3},
    )
    assert workflow["ok"] is True
    assert workflow["decision"]["selected_surface"] == "workflow"
    assert "direct_turn_job_single_slice_only" in workflow["decision"]["reason_codes"]


def test_out_of_scope_surface_fails_closed_for_d1e7() -> None:
    request = _request("ledger")
    routed = routing.route_typed_delegation_request(request)
    assert routed["ok"] is False
    assert routed["decision"]["rejection_status"] == "blocked"
    assert routed["decision"]["reason_codes"] == ["surface_out_of_scope_for_d1e7"]


def test_routed_request_carries_durable_routing_decision_payload() -> None:
    routed = routing.route_typed_delegation_request(
        _request("workflow"),
        routing_facts={"code_changes_expected": True, "workflow_step_count": 1},
    )
    assert routed["ok"] is True
    decision_payload = routed["request"]["input_payload"]["routing_decision"]
    assert decision_payload["selected_surface"] == "execution_queue"
    assert decision_payload["schema_version"] == routing.ROUTING_DECISION_SCHEMA_VERSION
    assert decision_payload["expected_execution_contract"]["checkpoint_boundary"] == "per_queue_item"
    assert decision_payload["mission_fit"]["status"] == "mission_aligned"


def test_routing_policy_snapshot_exposes_safety_limits() -> None:
    snapshot = routing.routing_policy_snapshot()
    assert snapshot["schema_version"] == routing.ROUTING_POLICY_SCHEMA_VERSION
    assert snapshot["scheduler_max_iterations_limit"] == 10
    assert snapshot["allowed_surfaces"] == sorted(routing.ROUTING_ALLOWED_SURFACES)


def test_decide_short_or_delegated_route_emits_direct_and_deferred_paths_with_contract_payload() -> None:
    direct = routing.decide_short_or_delegated_route(
        {
            "summary": "quick clarification",
            "estimated_duration_s": 20,
            "expected_evidence": [{"kind": "status_artifact", "description": "clarification evidence", "required": True}],
            "preferred_surface": "",
        }
    )
    assert direct["decision_outcome"] == "direct"
    assert direct["status"] == "accepted"
    assert direct["selected_surface"] == ""
    assert direct["expected_execution_contract"] == {}
    assert direct["expected_evidence_tags"] == ["status_artifact"]
    assert direct["constraint_flags"] == []

    deferred = routing.decide_short_or_delegated_route(
        {
            "summary": "needs confirmation before starting",
            "requires_operator_input": True,
        }
    )
    assert deferred["decision_outcome"] == "deferred"
    assert deferred["status"] == "rejected"
    assert deferred["rejection_status"] == "waiting_on_operator"
    assert deferred["selected_surface"] == ""


def test_decide_short_or_delegated_route_blocks_ambiguous_delegate_request() -> None:
    blocked = routing.decide_short_or_delegated_route(
        {
            "summary": "evidence is required but no target surface is available",
            "requires_new_evidence": True,
            "estimated_duration_s": 120,
        }
    )
    assert blocked["decision_outcome"] == "blocked"
    assert blocked["status"] == "rejected"
    assert blocked["rejection_status"] == "blocked"
    assert blocked["selected_surface"] == ""
    assert "preferred_surface_required_for_delegation" in blocked["reason_codes"]


def test_decide_short_or_delegated_route_emits_delegate_outcome_with_ownership_and_constraints() -> None:
    delegated = routing.decide_short_or_delegated_route(
        {
            "summary": "run long proof-heavy workflow",
            "preferred_surface": "workflow",
            "estimated_duration_s": 180,
            "proof_heavy": True,
            "expected_evidence": [{"kind": "run_log", "description": "workflow log", "required": True}],
        }
    )
    assert delegated["decision_outcome"] == "delegate"
    assert delegated["status"] == "accepted"
    assert delegated["selected_surface"] == "workflow"
    assert delegated["request_factors"]["proof_heavy"] is True
    assert "proof_heavy" in delegated["constraint_flags"]
    assert delegated["expected_evidence_tags"] == ["run_log"]
    assert delegated["execution_surface_ownership"]["owner_uid"] == routing.ROUTING_DECISION_OWNER_UID
    assert delegated["request_factors"]["mission_fit_status"] == "mission_aligned"


def test_route_rejects_partially_aligned_or_unclear_mission_fit_before_scheduler_admission() -> None:
    partial = policy.classify_answer_vs_delegate(
        {
            "summary": "software-adjacent side request",
            "preferred_surface": "scheduler",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
            "mission_alignment": "partially_aligned",
            "mission_alignment_note": "Allowed only if recorded explicitly.",
        }
    )
    request = _request_with_classification("scheduler", partial)
    request["mission_fit"]["note"] = ""
    request["input_payload"]["mission_fit"]["note"] = ""
    rejected_partial = routing.route_typed_delegation_request(
        request,
        routing_facts={
            "requires_schedule": True,
            "needs_immediate_start": False,
            "scheduler_safe": True,
            "scheduler_job_kind": "build_verify_loop",
            "scheduler_max_iterations": 2,
            "explicit_scheduler_justification": "mission gate test",
            "code_changes_expected": True,
        },
    )
    assert rejected_partial["ok"] is False
    assert rejected_partial["decision"]["reason_codes"] == ["mission_alignment_note_required"]

    unclear = policy.classify_answer_vs_delegate(
        {
            "summary": "do the thing",
            "preferred_surface": "scheduler",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )
    assert unclear["classification"] == "blocked"
    blocked = routing.decide_short_or_delegated_route(
        {
            "summary": "do the thing",
            "preferred_surface": "scheduler",
            "estimated_duration_s": 180,
            "requires_new_evidence": True,
            "outlives_turn": True,
        }
    )
    assert blocked["decision_outcome"] == "blocked"
    assert blocked["reason_codes"] == ["mission_fit_unclear_for_long_horizon_work"]
