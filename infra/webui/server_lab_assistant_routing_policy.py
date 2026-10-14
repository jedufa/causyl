"""D1E7 substrate routing policy for typed delegated requests."""

from __future__ import annotations

import copy
import datetime as _dt
import uuid
from typing import Any, Dict, List, Tuple

from infra.webui import server_lab_assistant_delegation_policy as _policy


ROUTING_POLICY_SCHEMA_VERSION = "lab_assistant_routing_policy_v1"
ROUTING_DECISION_SCHEMA_VERSION = "lab_assistant_routing_decision_v1"
ROUTING_DECISION_OWNER_UID = "PIS-20260312-224201Z-D1E7"
ROUTING_ALLOWED_SURFACES = {
    "execution_queue",
    "workflow",
    "scheduler",
    "verifier",
    "retrieval",
    "direct_turn_job",
}
SCHEDULER_ALLOWED_JOB_KINDS = {"single_turn", "build_verify_loop"}
SCHEDULER_MAX_ITERATIONS_LIMIT = 10
ROUTING_DECISION_OUTCOMES = {"direct", "delegate", "blocked", "deferred"}


class RoutingPolicyError(RuntimeError):
    """Raised when a typed request cannot be routed truthfully."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_bool(value: Any) -> bool:
    return bool(value)


def _clean_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _next_decision_version() -> int:
    return int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1_000_000)


def _normalize_owner_ref(row: Any) -> Dict[str, str]:
    return {
        "task_id": _clean_text(row.get("task_id") if isinstance(row, dict) else ""),
        "ledger_uid": _clean_text(row.get("ledger_uid") if isinstance(row, dict) else ""),
        "open_loop_id": _clean_text(row.get("open_loop_id") if isinstance(row, dict) else ""),
    }


def _constraint_flags(facts: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    for key in (
        "code_changes_expected",
        "proof_heavy",
        "verification_required",
        "retrieval_required",
        "multi_step",
        "cross_role",
        "outlives_turn",
        "read_only",
        "requires_commit_checkpoint",
        "requires_schedule",
        "needs_background_schedule",
        "scheduler_safe",
        "requires_detached_result_delivery",
    ):
        if facts.get(key):
            flags.append(key)
    if facts.get("needs_immediate_start"):
        flags.append("needs_immediate_start")
    mission_fit_status = _clean_text(facts.get("mission_fit_status"))
    if mission_fit_status:
        flags.append(f"mission_fit_{mission_fit_status}")
    if facts.get("queue_capacity", 0) and facts.get("queue_backlog", 0) >= facts.get("queue_capacity", 1):
        flags.append("queue_backpressured")
    return flags


def _expected_evidence_tags(request: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for row in list(request.get("expected_evidence") or []):
        if not isinstance(row, dict):
            continue
        kind = _clean_text(row.get("kind"))
        if kind and kind not in out:
            out.append(kind)
    return out


def _build_routing_decision(
    request: Dict[str, Any],
    facts: Dict[str, Any],
    *,
    selected_surface: str,
    summary: str,
    reason_codes: List[str],
    status: str,
    decision_outcome: str,
    expected_execution_contract: Dict[str, Any],
    rejection_status: str = "",
    rerouted: bool = False,
) -> Dict[str, Any]:
    return {
        "schema_version": ROUTING_DECISION_SCHEMA_VERSION,
        "decision_id": f"route_{uuid.uuid4().hex}",
        "decision_revision": _next_decision_version(),
        "created_utc": _utc_now_iso(),
        "request_id": _clean_text(request.get("request_id")),
        "correlation_id": _clean_text(request.get("correlation_id")),
        "launch_turn_id": _clean_text(request.get("launch_turn_id")),
        "owner_ref": _normalize_owner_ref(request.get("owner_ref")),
        "requested_surface": _clean_text(request.get("surface") or request.get("preferred_surface")),
        "selected_surface": selected_surface,
        "status": status,
        "decision_outcome": decision_outcome,
        "summary": summary,
        "reason_codes": _normalize_reason_codes(reason_codes),
        "rerouted": rerouted,
        "auditable_facts": facts,
        "expected_execution_contract": dict(expected_execution_contract),
        "rejection_status": rejection_status,
        "request_factors": {
            "code_changes_expected": bool(facts.get("code_changes_expected")),
            "verification_required": bool(facts.get("verification_required")),
            "proof_heavy": bool(facts.get("proof_heavy")),
            "multi_step": bool(facts.get("multi_step")),
            "cross_role": bool(facts.get("cross_role")),
            "outlives_turn": bool(facts.get("outlives_turn")),
            "read_only": bool(facts.get("read_only")),
            "requires_commit_checkpoint": bool(facts.get("requires_commit_checkpoint")),
            "mission_fit_status": _clean_text(facts.get("mission_fit_status")),
            "mission_fit_requires_note": bool(facts.get("mission_fit_requires_note")),
        },
        "constraint_flags": _constraint_flags(facts),
        "expected_evidence_tags": _expected_evidence_tags(request),
        "execution_surface_ownership": {"owner_uid": ROUTING_DECISION_OWNER_UID},
    }


def _normalize_reason_codes(values: List[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        code = _clean_text(value)
        if code and code not in out:
            out.append(code)
    return out


def _merged_routing_facts(request: Dict[str, Any], facts: Dict[str, Any] | None) -> Dict[str, Any]:
    request_payload = request.get("input_payload") if isinstance(request.get("input_payload"), dict) else {}
    payload_routing = request_payload.get("routing") if isinstance(request_payload.get("routing"), dict) else {}
    mission_fit = request.get("mission_fit") if isinstance(request.get("mission_fit"), dict) else {}
    merged = dict(payload_routing)
    if isinstance(facts, dict):
        merged.update(facts)
    queue_capacity = max(0, _clean_int(merged.get("queue_capacity"), default=1))
    queue_backlog = max(0, _clean_int(merged.get("queue_backlog"), default=0))
    scheduler_max_iterations = max(0, _clean_int(merged.get("scheduler_max_iterations"), default=0))
    workflow_step_count = max(0, _clean_int(merged.get("workflow_step_count"), default=0))
    normalized = {
        "code_changes_expected": _clean_bool(merged.get("code_changes_expected")),
        "read_only": _clean_bool(merged.get("read_only")),
        "proof_heavy": _clean_bool(merged.get("proof_heavy")),
        "verification_required": _clean_bool(merged.get("verification_required")),
        "retrieval_required": _clean_bool(merged.get("retrieval_required")),
        "multi_step": _clean_bool(merged.get("multi_step")),
        "cross_role": _clean_bool(merged.get("cross_role")),
        "outlives_turn": _clean_bool(merged.get("outlives_turn")),
        "needs_detached_result_delivery": _clean_bool(merged.get("needs_detached_result_delivery")),
        "requires_commit_checkpoint": _clean_bool(merged.get("requires_commit_checkpoint")),
        "requires_stop_on_failure": _clean_bool(merged.get("requires_stop_on_failure")),
        "requires_schedule": _clean_bool(merged.get("requires_schedule")),
        "needs_background_schedule": _clean_bool(merged.get("needs_background_schedule")),
        "needs_immediate_start": _clean_bool(merged.get("needs_immediate_start", True)),
        "scheduler_safe": _clean_bool(merged.get("scheduler_safe")),
        "queue_capacity": queue_capacity,
        "queue_backlog": queue_backlog,
        "workflow_step_count": workflow_step_count,
        "scheduler_job_kind": _clean_text(merged.get("scheduler_job_kind") or "single_turn").lower(),
        "scheduler_max_iterations": scheduler_max_iterations,
        "explicit_scheduler_justification": _clean_text(merged.get("explicit_scheduler_justification")),
        "schedule_mode": _clean_text(merged.get("schedule_mode")).lower(),
        "deferred_until_utc": _clean_text(merged.get("deferred_until_utc")),
        "max_runtime_seconds": max(0, _clean_int(merged.get("max_runtime_seconds"), default=0)),
        "mission_fit_status": _clean_text(mission_fit.get("status")),
        "mission_fit_note": _clean_text(mission_fit.get("note")),
        "mission_fit_summary": _clean_text(mission_fit.get("summary")),
        "mission_fit_source": _clean_text(mission_fit.get("source")),
        "mission_fit_requires_note": bool(mission_fit.get("requires_note")),
    }
    return normalized


def _default_contract(surface: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    if surface == "execution_queue":
        return {
            "stop_on_failure": True,
            "checkpoint_boundary": "per_queue_item",
            "commit_boundary": "after_success_when_enabled",
            "backpressure_mode": "reject_when_queue_full",
            "evidence_contract": "queue_state_and_workflow_artifacts",
            "code_changes_allowed": True,
            "read_only": False,
        }
    if surface == "workflow":
        return {
            "stop_on_failure": True,
            "checkpoint_boundary": "per_workflow_step",
            "commit_boundary": "workflow_owned",
            "backpressure_mode": "workflow_budget",
            "evidence_contract": "workflow_state_events_and_digest",
            "code_changes_allowed": True,
            "read_only": False,
        }
    if surface == "scheduler":
        return {
            "stop_on_failure": True,
            "checkpoint_boundary": "per_scheduled_run",
            "commit_boundary": "per_iteration_or_run_receipt",
            "backpressure_mode": "scheduler_due_and_overlap_gates",
            "evidence_contract": "scheduled_job_state_receipt_and_report",
            "code_changes_allowed": facts.get("scheduler_job_kind") == "build_verify_loop",
            "read_only": False,
            "scheduler_job_kind": facts.get("scheduler_job_kind"),
            "scheduler_max_iterations": int(facts.get("scheduler_max_iterations") or 0),
        }
    if surface == "verifier":
        return {
            "stop_on_failure": True,
            "checkpoint_boundary": "single_verification_request",
            "commit_boundary": "none",
            "backpressure_mode": "verifier_request_limits",
            "evidence_contract": "verification_status_and_result",
            "code_changes_allowed": False,
            "read_only": True,
        }
    if surface == "retrieval":
        return {
            "stop_on_failure": True,
            "checkpoint_boundary": "single_retrieval_request",
            "commit_boundary": "none",
            "backpressure_mode": "read_only_retrieval_limits",
            "evidence_contract": "retrieved_artifacts_or_transcripts",
            "code_changes_allowed": False,
            "read_only": True,
        }
    if surface == "direct_turn_job":
        return {
            "stop_on_failure": True,
            "checkpoint_boundary": "single_detached_turn",
            "commit_boundary": "none",
            "backpressure_mode": "detached_job_capacity",
            "evidence_contract": "detached_job_state_and_result",
            "code_changes_allowed": True,
            "read_only": False,
        }
    raise RoutingPolicyError(f"unsupported routed surface: {surface}")


def _reject_decision(request: Dict[str, Any], facts: Dict[str, Any], *, summary: str, reason_codes: List[str]) -> Dict[str, Any]:
    return _build_routing_decision(
        request,
        facts,
        selected_surface="",
        summary=summary,
        reason_codes=reason_codes,
        status="rejected",
        decision_outcome="blocked",
        expected_execution_contract={},
        rejection_status=_clean_text(request.get("status_on_reject") or "blocked"),
        rerouted=False,
    )


def _accept_decision(
    request: Dict[str, Any],
    facts: Dict[str, Any],
    *,
    selected_surface: str,
    summary: str,
    reason_codes: List[str],
) -> Dict[str, Any]:
    return _build_routing_decision(
        request,
        facts,
        selected_surface=selected_surface,
        summary=summary,
        reason_codes=reason_codes,
        status="accepted",
        decision_outcome="delegate",
        expected_execution_contract=_default_contract(selected_surface, facts),
        rerouted=selected_surface != request["surface"],
    )


def _mission_gate_rejection(request: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any] | None:
    mission_fit_status = _clean_text(facts.get("mission_fit_status"))
    mission_fit_note = _clean_text(facts.get("mission_fit_note"))
    mission_summary = _clean_text(facts.get("mission_fit_summary"))
    if mission_fit_status == "mission_misaligned":
        return _reject_decision(
            request,
            facts,
            summary=mission_summary or "Routing rejected because the work is mission-misaligned.",
            reason_codes=["mission_misaligned"],
        )
    if mission_fit_status == "unclear":
        return _reject_decision(
            request,
            facts,
            summary=mission_summary or "Routing rejected because mission fit is under-evidenced.",
            reason_codes=["mission_fit_unclear_for_long_horizon_work"],
        )
    if mission_fit_status == "partially_aligned" and not mission_fit_note:
        return _reject_decision(
            request,
            facts,
            summary=mission_summary or "Routing rejected because partial mission alignment requires an explicit note.",
            reason_codes=["mission_alignment_note_required"],
        )
    return None


def _route_scheduler(request: Dict[str, Any], facts: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    reasons = ["scheduled_execution_requested"]
    if not (facts["requires_schedule"] or facts["needs_background_schedule"] or facts["deferred_until_utc"]):
        return False, _reject_decision(
            request,
            facts,
            summary="Scheduler routing rejected because no actual schedule boundary was declared.",
            reason_codes=["scheduler_requires_declared_schedule_boundary"],
        )
    if facts["needs_immediate_start"]:
        return False, _reject_decision(
            request,
            facts,
            summary="Scheduler routing rejected because immediate execution should use queue, workflow, verifier, retrieval, or a direct turn-job.",
            reason_codes=["scheduler_not_for_immediate_execution"],
        )
    if not facts["scheduler_safe"]:
        return False, _reject_decision(
            request,
            facts,
            summary="Scheduler routing rejected because the request was not marked as scheduler-safe.",
            reason_codes=["scheduler_requires_explicit_safe_background_shape"],
        )
    kind = facts["scheduler_job_kind"]
    if kind not in SCHEDULER_ALLOWED_JOB_KINDS:
        return False, _reject_decision(
            request,
            facts,
            summary="Scheduler routing rejected because the requested scheduled job kind is unsupported.",
            reason_codes=["unsupported_scheduler_job_kind"],
        )
    if facts["code_changes_expected"] and kind != "build_verify_loop":
        return False, _reject_decision(
            request,
            facts,
            summary="Scheduler routing rejected because code-changing scheduled work must use an explicit build-verify loop shape.",
            reason_codes=["scheduler_code_change_requires_build_verify_loop"],
        )
    if kind == "build_verify_loop":
        max_iterations = int(facts["scheduler_max_iterations"] or 0)
        if max_iterations <= 0 or max_iterations > SCHEDULER_MAX_ITERATIONS_LIMIT:
            return False, _reject_decision(
                request,
                facts,
                summary="Scheduler routing rejected because build-verify loop max_iterations was missing or outside the durable safety limit.",
                reason_codes=["scheduler_max_iterations_out_of_bounds"],
            )
        if not facts["explicit_scheduler_justification"]:
            return False, _reject_decision(
                request,
                facts,
                summary="Scheduler routing rejected because code-changing scheduled work requires an explicit justification.",
                reason_codes=["scheduler_requires_explicit_code_change_justification"],
            )
        reasons.extend(["scheduler_build_verify_loop", "scheduler_iteration_budget_declared"])
    return True, _accept_decision(
        request,
        facts,
        selected_surface="scheduler",
        summary="Route to scheduler because the work is intentionally deferred background execution with an explicit scheduled contract.",
        reason_codes=reasons,
    )


def decide_short_or_delegated_route(request: Dict[str, Any], *, routing_facts: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Return a routing decision contract for direct/delegate/block/deferred evaluation.

    This helper intentionally reuses the 01A2 answer-vs-delegate policy. Use it when a caller has
    pre-route request facts and must persist a canonical outcome, including deferred/manual review states.
    """
    request_payload = request if isinstance(request, dict) else {}
    facts = {
        "summary": _clean_text(request_payload.get("summary")),
        "preferred_surface": _clean_text(request_payload.get("preferred_surface") or request_payload.get("surface")),
        "estimated_duration_s": _clean_int(request_payload.get("estimated_duration_s"), default=0),
        "requires_operator_input": bool(request_payload.get("requires_operator_input")),
        "unsafe_to_delegate": bool(request_payload.get("unsafe_to_delegate")),
        "requires_new_evidence": bool(request_payload.get("requires_new_evidence")),
        "requires_external_action": bool(request_payload.get("requires_external_action")),
        "proof_heavy": bool(request_payload.get("proof_heavy")),
        "multi_step": bool(request_payload.get("multi_step")),
        "outlives_turn": bool(request_payload.get("outlives_turn")),
        "code_changes_expected": bool(request_payload.get("code_changes_expected")),
        "read_only": bool(request_payload.get("read_only")),
        "verification_required": bool(request_payload.get("verification_required")),
        "retrieval_required": bool(request_payload.get("retrieval_required")),
    }
    if isinstance(routing_facts, dict):
        for key, value in routing_facts.items():
            if key not in facts:
                continue
            facts[key] = value
    classification = _policy.classify_answer_vs_delegate(facts)
    mission_fit = dict(classification.get("mission_fit") or {})
    if classification["classification"] != "answer_directly":
        facts["mission_fit_status"] = _clean_text(mission_fit.get("status"))
        facts["mission_fit_note"] = _clean_text(mission_fit.get("note"))
        facts["mission_fit_summary"] = _clean_text(mission_fit.get("summary"))
        facts["mission_fit_source"] = _clean_text(mission_fit.get("source"))
        facts["mission_fit_requires_note"] = bool(mission_fit.get("requires_note"))
    normalized_request = {
        "request_id": _clean_text(request_payload.get("request_id") or f"route_{uuid.uuid4().hex}"),
        "correlation_id": _clean_text(request_payload.get("correlation_id") or f"corr_{uuid.uuid4().hex}"),
        "launch_turn_id": _clean_text(request_payload.get("launch_turn_id") or f"turn_{uuid.uuid4().hex}"),
        "owner_ref": _normalize_owner_ref(request_payload.get("owner_ref")),
        "expected_evidence": request_payload.get("expected_evidence"),
        "surface": _clean_text(facts.get("preferred_surface")),
        "status_on_reject": _clean_text(request_payload.get("status_on_reject") or ("waiting_on_operator" if classification["classification"] == "waiting_on_operator" else "blocked")),
    }
    decision = classification["classification"]
    if decision == "answer_directly":
        return _build_routing_decision(
            normalized_request,
            facts=facts,
            selected_surface="",
            summary="Direct-answer policy path selected; no delegated execution substrate required.",
            reason_codes=list(classification.get("reason_codes") or []),
            status="accepted",
            decision_outcome="direct",
            expected_execution_contract={},
        )
    if decision == "waiting_on_operator":
        return _build_routing_decision(
            normalized_request,
            facts=facts,
            selected_surface="",
            summary="Routing deferred pending operator input.",
            reason_codes=list(classification.get("reason_codes") or []),
            status="rejected",
            decision_outcome="deferred",
            expected_execution_contract={},
            rejection_status="waiting_on_operator",
        )
    if decision == "blocked":
        return _build_routing_decision(
            normalized_request,
            facts=facts,
            selected_surface="",
            summary="Routing blocked by policy constraints.",
            reason_codes=list(classification.get("reason_codes") or []),
            status="rejected",
            decision_outcome="blocked",
            expected_execution_contract={},
            rejection_status="blocked",
        )
    if not _clean_text(facts.get("preferred_surface")):
        return _build_routing_decision(
            normalized_request,
            facts=facts,
            selected_surface="",
            summary="Routing blocked by policy constraints.",
            reason_codes=["preferred_surface_required_for_delegation", *(classification.get("reason_codes") or [])],
            status="rejected",
            decision_outcome="blocked",
            expected_execution_contract={},
            rejection_status="blocked",
        )
    return _build_routing_decision(
        normalized_request,
        facts=facts,
        selected_surface=_clean_text(facts.get("preferred_surface")),
        summary="Delegated execution policy selected by 01A2 gate.",
        reason_codes=list(classification.get("reason_codes") or []),
        status="accepted",
        decision_outcome="delegate",
        expected_execution_contract=_default_contract(_clean_text(facts.get("preferred_surface")), facts)
        if _clean_text(facts.get("preferred_surface")) in ROUTING_ALLOWED_SURFACES
        else {},
    )


def _choose_non_scheduler_surface(request: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    requested = request["surface"]
    queue_saturated = facts["queue_capacity"] > 0 and facts["queue_backlog"] >= facts["queue_capacity"]
    if requested == "retrieval" or (facts["retrieval_required"] and facts["read_only"] and not facts["code_changes_expected"]):
        if facts["code_changes_expected"]:
            return _reject_decision(
                request,
                facts,
                summary="Retrieval routing rejected because retrieval must stay read-only.",
                reason_codes=["retrieval_read_only_only"],
            )
        return _accept_decision(
            request,
            facts,
            selected_surface="retrieval",
            summary="Route to retrieval because the request is evidence inspection, not execution.",
            reason_codes=["retrieval_first_class_surface", "read_only_evidence_request"],
        )
    if requested == "verifier" or (facts["verification_required"] and not facts["code_changes_expected"] and not facts["multi_step"]):
        if facts["code_changes_expected"]:
            return _reject_decision(
                request,
                facts,
                summary="Verifier routing rejected because verifier work cannot be the code-changing substrate.",
                reason_codes=["verifier_read_only_only"],
            )
        return _accept_decision(
            request,
            facts,
            selected_surface="verifier",
            summary="Route to verifier because the request is focused proof work with no code changes.",
            reason_codes=["verifier_first_class_surface", "proof_request_without_code_changes"],
        )
    if requested == "workflow":
        if not facts["multi_step"] and not facts["cross_role"] and facts["workflow_step_count"] <= 1:
            if queue_saturated:
                return _reject_decision(
                    request,
                    facts,
                    summary="Workflow reroute rejected because the simpler queue path is saturated and workflow would only hide backpressure.",
                    reason_codes=["execution_queue_backpressure", "workflow_overkill_for_linear_job"],
                )
            return _accept_decision(
                request,
                facts,
                selected_surface="execution_queue",
                summary="Reroute to execution queue because the requested workflow is only a single linear bounded job.",
                reason_codes=["workflow_overkill_for_linear_job", "execution_queue_matches_linear_checkpointed_work"],
            )
        return _accept_decision(
            request,
            facts,
            selected_surface="workflow",
            summary="Route to workflow because the work is multi-step or cross-role and needs workflow checkpoints.",
            reason_codes=["workflow_matches_multi_step_shape"],
        )
    if requested == "execution_queue":
        if facts["multi_step"] or facts["cross_role"] or facts["workflow_step_count"] > 1:
            return _accept_decision(
                request,
                facts,
                selected_surface="workflow",
                summary="Reroute to workflow because queue execution cannot honestly represent the requested multi-step flow.",
                reason_codes=["queue_cannot_represent_multi_step_flow", "workflow_matches_multi_step_shape"],
            )
        if queue_saturated:
            return _reject_decision(
                request,
                facts,
                summary="Execution queue routing rejected because the queue is at declared capacity.",
                reason_codes=["execution_queue_backpressure"],
            )
        return _accept_decision(
            request,
            facts,
            selected_surface="execution_queue",
            summary="Route to execution queue because the work is a bounded linear job with stop-on-failure semantics.",
            reason_codes=["execution_queue_matches_linear_checkpointed_work"],
        )
    if requested == "direct_turn_job":
        if facts["multi_step"] or facts["cross_role"]:
            return _accept_decision(
                request,
                facts,
                selected_surface="workflow",
                summary="Reroute to workflow because a direct detached turn-job cannot truthfully own a multi-step or multi-role flow.",
                reason_codes=["direct_turn_job_single_slice_only", "workflow_matches_multi_step_shape"],
            )
        if facts["requires_commit_checkpoint"]:
            if queue_saturated:
                return _reject_decision(
                    request,
                    facts,
                    summary="Direct turn-job routing rejected because the request needs queue commit/checkpoint semantics and the queue is full.",
                    reason_codes=["execution_queue_backpressure", "direct_turn_job_lacks_commit_boundary"],
                )
            return _accept_decision(
                request,
                facts,
                selected_surface="execution_queue",
                summary="Reroute to execution queue because the request needs durable stop-on-failure and commit checkpoints.",
                reason_codes=["direct_turn_job_lacks_commit_boundary", "execution_queue_matches_linear_checkpointed_work"],
            )
        return _accept_decision(
            request,
            facts,
            selected_surface="direct_turn_job",
            summary="Route to direct detached turn-job because the work is a single bounded slice that should outlive the current turn without queue or workflow overhead.",
            reason_codes=["detached_single_slice_work"],
        )
    if requested not in ROUTING_ALLOWED_SURFACES:
        return _reject_decision(
            request,
            facts,
            summary="Routing rejected because the requested surface is outside the D1E7 routing policy boundary.",
            reason_codes=["surface_out_of_scope_for_d1e7"],
        )
    raise RoutingPolicyError(f"unhandled routed surface: {requested}")


def route_typed_delegation_request(request: Dict[str, Any], *, routing_facts: Dict[str, Any] | None = None) -> Dict[str, Any]:
    normalized_request = _policy.validate_typed_delegation_request(request)
    facts = _merged_routing_facts(normalized_request, routing_facts)
    mission_rejection = _mission_gate_rejection(normalized_request, facts)
    if mission_rejection is not None:
        return {
            "ok": False,
            "decision": mission_rejection,
            "request": {},
        }
    if normalized_request["surface"] == "scheduler":
        decision_ok, decision = _route_scheduler(normalized_request, facts)
    else:
        decision = _choose_non_scheduler_surface(normalized_request, facts)
        decision_ok = decision["status"] == "accepted"
    routed_request = {}
    if decision_ok:
        routed_request = copy.deepcopy(normalized_request)
        routed_request["surface"] = decision["selected_surface"]
        payload = dict(routed_request.get("input_payload") or {})
        payload["routing_decision"] = {
            "schema_version": decision["schema_version"],
            "decision_id": decision["decision_id"],
            "decision_outcome": decision["decision_outcome"],
            "decision_revision": decision["decision_revision"],
            "execution_surface_ownership": dict(decision["execution_surface_ownership"]),
            "selected_surface": decision["selected_surface"],
            "summary": decision["summary"],
            "reason_codes": list(decision["reason_codes"]),
            "request_factors": dict(decision["request_factors"]),
            "mission_fit": {
                "status": _clean_text((normalized_request.get("mission_fit") or {}).get("status")),
                "note": _clean_text((normalized_request.get("mission_fit") or {}).get("note")),
                "summary": _clean_text((normalized_request.get("mission_fit") or {}).get("summary")),
                "source": _clean_text((normalized_request.get("mission_fit") or {}).get("source")),
                "requires_note": bool((normalized_request.get("mission_fit") or {}).get("requires_note")),
            },
            "constraint_flags": list(decision["constraint_flags"]),
            "expected_evidence_tags": list(decision["expected_evidence_tags"]),
            "expected_execution_contract": dict(decision["expected_execution_contract"]),
        }
        routed_request["input_payload"] = payload
        routed_request = _policy.validate_typed_delegation_request(routed_request)
    return {
        "ok": decision_ok,
        "decision": decision,
        "request": routed_request,
    }


def routing_policy_snapshot() -> Dict[str, Any]:
    return {
        "schema_version": ROUTING_POLICY_SCHEMA_VERSION,
        "generated_utc": _utc_now_iso(),
        "allowed_surfaces": sorted(ROUTING_ALLOWED_SURFACES),
        "scheduler_allowed_job_kinds": sorted(SCHEDULER_ALLOWED_JOB_KINDS),
        "scheduler_max_iterations_limit": SCHEDULER_MAX_ITERATIONS_LIMIT,
        "notes": [
            "D1E7 chooses a substrate after 01A2 has already decided that the work should be delegated.",
            "Scheduler is not the default code-changing substrate; code-changing scheduled work requires an explicit build-verify-loop shape.",
            "Workflow is for multi-step or cross-role execution; simple bounded linear jobs should route to execution_queue.",
            "Verifier and retrieval remain first-class read-only surfaces.",
            "Direct turn-jobs are only for one detached execution slice, not for full workflows.",
        ],
    }
