"""2DB7 operator-visible assistant snapshot.

This module builds one read-only projection across the existing ADD9/2848/BD05/
DF0C/5437 substrates. It does not create a new mutable truth store.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.webui import server_lab_assistant_delegation_bindings as _bindings
from infra.webui import server_lab_assistant_execution_proof as _execution_proof
from infra.webui import server_lab_assistant_execution_truth_ownership as _truth_ownership
from infra.webui import server_lab_assistant_host as _host
from infra.webui import server_lab_assistant_self_check as _self_check
from infra.webui import server_lab_assistant_recovery as _recovery
from infra.webui import server_lab_assistant_state as _state
from infra.webui import server_lab_assistant_status_taxonomy as _status_taxonomy
from infra.webui import server_lab_assistant_task_register as _register
from infra.webui import server_lab_assistant_status_diagnostics as _status_diag
from infra.webui import server_lab_assistant_operator_snapshot_copy as _snapshot_copy
from infra.webui import server_verification_policy as _verify_policy
from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger
from infra.controlplane import scheduled_jobs as _scheduled_jobs
from infra.controlplane import continuation_context_attachment as _continuation_attachment
from infra.controlplane import plane_mission_contract as _plane_mission_contract


SNAPSHOT_SCHEMA_VERSION = "lab_assistant_operator_snapshot_v1"
STATUS_CONTRACT_SCHEMA_VERSION = "lab_assistant_status_projection_contract_v1"
EXECUTION_SURFACE_SCHEMA_VERSION = "lab_assistant_execution_surface_projection_v1"
PROJECTION_CONTRACT_SCHEMA_VERSION = "lab_assistant_projection_contract_v1"
COMMAND_CENTER_SCHEMA_VERSION = "lab_assistant_command_center_v1"
WORK_ITEM_SCHEMA_VERSION = "lab_assistant_work_item_v1"
_STALE_AFTER_S = {
    "host_health": 300,
    "assistant_state": 900,
    "task_register": 900,
    "recovery": 900,
    "delegated_task": 1800,
}

_BLOCKED_TASK_STATUSES = {
    "blocked",
    "failed",
    "evidence_missing",
    "ambiguous_artifact",
}

_INTERRUPTED_TASK_STATUSES = {
    "interrupted",
    "interrupted_recovering",
}

_ACTIVE_TASK_WAIT_STATUSES = {
    "active",
    "running",
    "waiting",
    "waiting_on_job",
    "waiting_on_verification",
    "scheduled",
    "retrying",
}

_OPERATOR_STATE_PRECEDENCE = [
    "FAILED",
    "BLOCKED",
    "NEEDS_INPUT",
    "STALLED",
    "DEGRADED",
    "RETRYING",
    "RUNNING",
    "SCHEDULED",
    "WAITING",
    "COMPLETED",
]

def _status_copy_from_status(raw_status: Any, *, operator_state: str = "") -> Dict[str, str]:
    return _snapshot_copy.status_copy_from_status(raw_status, operator_state=operator_state)


def _task_state_copy(task: Dict[str, Any]) -> Dict[str, str]:
    raw_status = _clean_text(task.get("status")).lower()
    current_run_state = _clean_text(task.get("current_run_state")).lower()
    operator_state = _task_operator_facing_state(task)
    if raw_status in {
        "waiting_on_verification",
        "waiting_on_operator",
        "evidence_missing",
        "ambiguous_artifact",
        "blocked",
        "failed",
        "retrying",
        "scheduled",
        "waiting_on_job",
        "waiting",
        "running",
        "active",
        "stalled",
        "stale",
        "interrupted",
        "interrupted_recovering",
        "completed",
        "done",
    }:
        return _status_copy_from_status(raw_status, operator_state=operator_state)
    if current_run_state:
        return _status_copy_from_status(current_run_state, operator_state=operator_state)
    return _status_copy_from_status(operator_state, operator_state=operator_state)


def _overall_status_copy(
    *,
    operator_state: str,
    backend_projection_state: str,
    stale_sources: List[str],
    truth_conflicts: List[Dict[str, Any]],
    blocked_tasks: List[Dict[str, Any]],
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
) -> Dict[str, str]:
    return _snapshot_copy.overall_status_copy(
        operator_state=operator_state,
        backend_projection_state=backend_projection_state,
        stale_sources=stale_sources,
        truth_conflicts=truth_conflicts,
        blocked_tasks=blocked_tasks,
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
    )


def _mission_surface_projection(mission_contract: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(mission_contract.get("ok")):
        return {
            "status": "degraded",
            "headline": "Mission contract unavailable",
            "summary": _clean_text(mission_contract.get("summary")) or "Canonical plane mission contract could not be loaded.",
            "success_model": [
                "Operator success criteria are unavailable until the canonical mission contract loads cleanly."
            ],
            "non_goals": [],
            "claim_ceiling_summary": "",
            "refresh_scope_summary": "",
            "source_fingerprint": "",
            "source_of_truth": "mission_contract_failure",
        }
    operator_projection = dict(mission_contract.get("operator_projection") or {})
    return {
        "status": "ok",
        "headline": _clean_text(operator_projection.get("headline")),
        "summary": _clean_text(operator_projection.get("activity_vs_mission_failure_warning")),
        "success_model": [
            _clean_text(row)
            for row in list(operator_projection.get("operator_success_model") or [])
            if _clean_text(row)
        ],
        "non_goals": [
            _clean_text(row)
            for row in list(operator_projection.get("explicit_non_goals") or [])
            if _clean_text(row)
        ],
        "claim_ceiling_summary": _clean_text(operator_projection.get("claim_ceiling_summary")),
        "refresh_scope_summary": _clean_text(operator_projection.get("refresh_scope_summary")),
        "source_fingerprint": _clean_text(operator_projection.get("source_fingerprint")),
        "source_of_truth": "plane_mission_contract_v1",
    }


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso_utc(value: Any) -> Optional[_dt.datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _age_s(*, now: _dt.datetime, value: Any) -> Optional[int]:
    parsed = _parse_iso_utc(value)
    if parsed is None:
        return None
    delta = now - parsed
    return max(0, int(delta.total_seconds()))


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _completed_residue_only(
    *,
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    open_loops: List[Dict[str, Any]],
) -> bool:
    return bool(
        not active_tasks
        and completed_tasks
        and not open_loops
        and all(bool(task.get("evidence_backed_completion")) for task in completed_tasks)
    )


def _quiescent_completed_task_ids(completed_tasks: List[Dict[str, Any]]) -> set[str]:
    return {
        _clean_text(task.get("task_id"))
        for task in completed_tasks
        if _clean_text(task.get("task_id"))
        and _clean_text(task.get("status")).lower() in {"completed", "done"}
        and bool(task.get("evidence_backed_completion"))
    }


def _is_operator_residue_loop(loop: Dict[str, Any], *, quiescent_completed_task_ids: set[str]) -> bool:
    linked_task_id = _clean_text(loop.get("linked_task_id"))
    kind = _clean_text(loop.get("kind")).lower()
    return bool(
        linked_task_id
        and linked_task_id in quiescent_completed_task_ids
        and kind in {"blocked_question", "waiting_on_verification"}
    )


def _operator_effective_recovery_snapshot(
    *,
    recovery_snapshot: Dict[str, Any],
    raw_tasks: List[Dict[str, Any]],
    visible_tasks: List[Dict[str, Any]],
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    open_loops: List[Dict[str, Any]],
    generated_utc: str,
) -> Dict[str, Any]:
    hidden_helper_only = bool(raw_tasks) and not visible_tasks and not open_loops and all(
        _register.is_internal_helper_task(dict(task)) for task in raw_tasks
    )
    completed_residue_only = _completed_residue_only(
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        open_loops=open_loops,
    )
    if not hidden_helper_only and not completed_residue_only:
        return dict(recovery_snapshot or {})
    normalized = dict(recovery_snapshot or {})
    if _clean_text(normalized.get("status")).lower() in {"waiting_on_job", "interrupted_recovering"}:
        normalized["status"] = "ready"
        normalized["reconciled_utc"] = _clean_text(normalized.get("reconciled_utc")) or _clean_text(generated_utc)
        normalized["summary"] = (
            "Internal helper residue is hidden from operator recovery state."
            if hidden_helper_only
            else "Evidence-backed completion residue is treated as quiescent operator state."
        )
    return normalized


def _operator_effective_assistant_state(
    *,
    assistant_state: Dict[str, Any],
    raw_tasks: List[Dict[str, Any]],
    visible_tasks: List[Dict[str, Any]],
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    open_loops: List[Dict[str, Any]],
) -> Dict[str, Any]:
    hidden_helper_only = bool(raw_tasks) and not visible_tasks and not open_loops and all(
        _register.is_internal_helper_task(dict(task)) for task in raw_tasks
    )
    completed_residue_only = _completed_residue_only(
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        open_loops=open_loops,
    )
    if not hidden_helper_only and not completed_residue_only:
        return dict(assistant_state or {})
    normalized = dict(assistant_state or {})
    if _clean_text(normalized.get("current_state")).lower() in {"waiting_on_job", "interrupted_recovering"}:
        normalized["current_state"] = "idle"
        normalized["state_reason"] = (
            "Internal helper residue is hidden from operator state."
            if hidden_helper_only
            else "Evidence-backed completion residue is treated as quiescent operator state."
        )
    return normalized


def _safe_relative_path(value: Any) -> str:
    path = _clean_text(value)
    if not path:
        return ""
    candidate = Path(path)
    if candidate.is_absolute():
        return ""
    normalized = candidate.as_posix()
    if normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _canonical_writeback_projection(marker: Dict[str, Any]) -> Dict[str, Any]:
    value = dict(marker or {})
    if not value:
        return {}
    return {
        "schema_version": _clean_text(value.get("schema_version")),
        "assimilation_status": _clean_text(value.get("assimilation_status")),
        "canonical_task_status": _clean_text(value.get("canonical_task_status")),
        "latest_delegation_status": _clean_text(value.get("latest_delegation_status")),
        "decision_reason": _clean_text(value.get("decision_reason")),
        "decision_summary": _clean_text(value.get("decision_summary")),
        "delegation_request_id": _clean_text(value.get("delegation_request_id")),
        "correlation_id": _clean_text(value.get("correlation_id")),
        "surface": _clean_text(value.get("surface")),
        "subsystem_id": _clean_text(value.get("subsystem_id")),
        "detached_job_id": _clean_text(value.get("detached_job_id")),
        "evidence_status": _clean_text(value.get("evidence_status")),
        "evidence_refs": [_clean_text(row) for row in list(value.get("evidence_refs") or []) if _clean_text(row)],
        "result_fingerprint": _clean_text(value.get("result_fingerprint")),
    }


def _effective_writeback_projection(
    *,
    marker: Dict[str, Any],
    effective_status: str,
    evidence_refs: List[str],
    evidence_status: str,
    latest_summary: str,
    latest: Dict[str, Any],
) -> Dict[str, Any]:
    projection = _canonical_writeback_projection(marker)
    status_value = _clean_text(effective_status).lower()
    if projection and _clean_text(projection.get("assimilation_status")) == "successful_assimilation":
        return projection
    if status_value not in {"completed", "done"} or not evidence_refs:
        return projection
    merged_refs = list(
        dict.fromkeys(
            [
                _clean_text(row)
                for row in list(projection.get("evidence_refs") or []) + list(evidence_refs or [])
                if _clean_text(row)
            ]
        )
    )
    return {
        "schema_version": _clean_text(projection.get("schema_version")) or "lab_assistant_task_writeback_marker_v1",
        "assimilation_status": "successful_assimilation",
        "canonical_task_status": _clean_text(effective_status) or "completed",
        "latest_delegation_status": _clean_text(projection.get("latest_delegation_status")) or status_value,
        "decision_reason": "terminal_worker_evidence",
        "decision_summary": _clean_text(projection.get("decision_summary")) or _clean_text(latest_summary),
        "delegation_request_id": _clean_text(projection.get("delegation_request_id")) or _clean_text(latest.get("delegation_request_id")),
        "correlation_id": _clean_text(projection.get("correlation_id")) or _clean_text(latest.get("correlation_id")),
        "surface": _clean_text(projection.get("surface")) or _clean_text(latest.get("surface")),
        "subsystem_id": _clean_text(projection.get("subsystem_id")) or _clean_text(latest.get("subsystem_id")),
        "detached_job_id": _clean_text(projection.get("detached_job_id")) or _clean_text(latest.get("detached_job_id")),
        "evidence_status": "present" if merged_refs else (_clean_text(projection.get("evidence_status")) or _clean_text(evidence_status)),
        "evidence_refs": merged_refs,
        "result_fingerprint": _clean_text(projection.get("result_fingerprint")),
    }


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _freshness_marker(
    *,
    source: str,
    updated_utc: Any,
    now: _dt.datetime,
    max_age_s: int,
    missing: bool = False,
    force_stale: bool = False,
    note: str = "",
) -> Dict[str, Any]:
    age = _age_s(now=now, value=updated_utc)
    stale = bool(missing or force_stale or age is None or age > int(max_age_s))
    return {
        "source": source,
        "updated_utc": str(updated_utc or ""),
        "age_s": age,
        "stale": stale,
        "max_age_s": int(max_age_s),
        "reason": _clean_text(note) or ("missing_timestamp" if age is None else ""),
    }


def _task_projection(repo_root: Path, task: Dict[str, Any], *, now: _dt.datetime) -> Dict[str, Any]:
    task_id = _clean_text(task.get("task_id"))
    intake = dict(task.get("intake") or {})
    scope_contract = dict(task.get("scope_contract") or {})
    acceptance_contract = dict(task.get("acceptance_contract") or {})
    delegation_refs = list(task.get("delegation_refs") or [])
    if not delegation_refs:
        latest = {
            "status": _clean_text(task.get("status") or "active"),
            "summary": _clean_text(task.get("next_expected_action")),
            "evidence_status": "none",
            "evidence_refs": [],
            "next_decision": {
                "kind": "assistant_continue",
                "summary": _clean_text(task.get("next_expected_action")),
                "owner": "assistant",
            },
        }
        status_row = {
            "delegated": False,
            "delegation_count": 0,
            "delegation_refs": [],
            "delegation_summaries": [],
        }
    else:
        try:
            status_row = _bindings.synthesize_task_status(
                repo_root,
                task_id=task_id,
                task_row=task,
                persist_reconciliation=False,
            )
            latest = dict(status_row.get("latest") or {})
        except Exception as exc:
            latest = {
                "status": "interrupted_recovering",
                "summary": f"Could not synthesize delegated state: {exc}",
                "evidence_status": "missing_native_record",
                "evidence_refs": [],
                "next_decision": {
                    "kind": "assistant_continue",
                    "summary": "Inspect native subsystem state before claiming progress.",
                    "owner": "assistant",
                },
            }
            status_row = {
                "delegated": bool(task.get("delegation_refs")),
                "delegation_count": len(delegation_refs),
                "delegation_refs": delegation_refs,
                "delegation_summaries": [],
            }
    evidence_refs = [str(item) for item in list(latest.get("evidence_refs") or []) if str(item).strip()]
    effective_status = _clean_text(latest.get("status") or task.get("status") or "active")
    evidence_backed_completion = bool(effective_status == "completed" and evidence_refs)
    task_status_contract = _task_status_contract(
        task_id=task_id,
        task=task,
        register_status=_clean_text(task.get("status")),
        latest_status_row=latest,
        latest_status=effective_status,
    )
    current_run = _task_current_run(task)
    current_run_id = _clean_text(current_run.get("run_id"))
    current_run_state = _clean_text(current_run.get("run_state"))
    current_run_attention_state = _clean_text(current_run.get("attention_state"))
    run_checkpoint = _project_run_checkpoint(current_run)
    run_resume_decision = _project_run_resume_decision(current_run)
    run_intervention = _project_run_intervention(current_run)
    assumptions = list(scope_contract.get("assumptions") or [])
    constraints = [row for row in list(scope_contract.get("constraints") or []) if bool(row.get("active"))]
    non_goals = [row for row in list(scope_contract.get("non_goals") or []) if bool(row.get("active"))]
    current_scope = dict(scope_contract.get("current_scope") or {})
    acceptance_targets = [row for row in list(acceptance_contract.get("acceptance_targets") or []) if bool(row.get("active"))]
    proof_targets = list(acceptance_contract.get("proof_targets") or [])
    review_gates = list(acceptance_contract.get("review_gates") or [])
    completion_state = dict(acceptance_contract.get("completion_state") or {})
    decomposition_contract = dict(task.get("decomposition_contract") or {})
    plan_summary = dict(decomposition_contract.get("plan_summary") or {})
    replanning = dict(decomposition_contract.get("replanning") or {})
    pending_proof = [
        row for row in proof_targets if bool(row.get("required")) and str(row.get("status") or "").strip() not in {"satisfied", "waived"}
    ]
    pending_review = [row for row in review_gates if str(row.get("status") or "").strip() not in {"approved", "waived"}]
    done_stage = "planned"
    if bool(completion_state.get("operator_approved")):
        done_stage = "operator_approved"
    elif bool(completion_state.get("evidence_complete")):
        done_stage = "evidence_complete"
    elif bool(completion_state.get("implementation_complete")):
        done_stage = "implementation_complete"
    freshness = _freshness_marker(
        source=f"task:{task_id or 'unknown'}",
        updated_utc=task.get("updated_utc") or task.get("created_utc"),
        now=now,
        max_age_s=_STALE_AFTER_S["delegated_task"],
        missing=not bool(task),
    )
    writeback_projection = _effective_writeback_projection(
        marker=dict(task.get("delegated_outcome_writeback") or {}),
        effective_status=effective_status,
        evidence_refs=evidence_refs,
        evidence_status=_clean_text(latest.get("evidence_status")),
        latest_summary=_clean_text(latest.get("summary")),
        latest=latest,
    )
    return {
        "task_id": task_id,
        "title": _clean_text(task.get("title")),
        "ledger_uid": _clean_text(task.get("ledger_uid")),
        "kind": _clean_text(task.get("kind")),
        "owner": _clean_text(task.get("owner")),
        "status": effective_status,
        "register_status": _clean_text(task.get("status")),
        "delegated": bool(status_row.get("delegated")),
        "delegation_count": int(status_row.get("delegation_count") or 0),
        "waiting_reason": _clean_text(task.get("blocked_reason") or latest.get("summary")),
        "next_expected_action": _clean_text(task.get("next_expected_action")),
        "next_decision": dict(latest.get("next_decision") or {}),
        "evidence_status": _clean_text(latest.get("evidence_status")),
        "evidence_refs": evidence_refs,
        "evidence_backed_completion": evidence_backed_completion,
        "delegation_refs": list(status_row.get("delegation_refs") or []),
        "delegation_summaries": list(status_row.get("delegation_summaries") or []),
        "status_contract": task_status_contract,
        "delegated_outcome_writeback": writeback_projection,
        "current_run_id": current_run_id,
        "current_run_state": current_run_state,
        "current_run_attention_state": current_run_attention_state,
        "run_checkpoint": run_checkpoint,
        "run_resume_decision": run_resume_decision,
        "run_intervention": run_intervention,
        "intake": {
            "readiness": _clean_text(intake.get("readiness")),
            "work_class": _clean_text(intake.get("work_class")),
            "summary": _clean_text(intake.get("summary")),
            "confidence": float(intake.get("confidence") or 0.0) if intake else 0.0,
            "missing_information": [
                {
                    "field": _clean_text(row.get("field")),
                    "question": _clean_text(row.get("question")),
                    "reason": _clean_text(row.get("reason")),
                    "required": bool(row.get("required")),
                }
                for row in list(intake.get("missing_information") or [])
            ],
        },
        "scope_contract": {
            "assumption_count": len(assumptions),
            "confirmed_assumption_count": len([row for row in assumptions if bool(row.get("operator_approved"))]),
            "provisional_assumption_count": len(
                [row for row in assumptions if str(row.get("approval_state") or "").strip() in {"provisional", "needs_operator_confirmation"}]
            ),
            "constraint_count": len(constraints),
            "non_goal_count": len(non_goals),
            "current_scope_summary": _clean_text(current_scope.get("summary")),
            "assumptions": [
                {
                    "assumption_id": _clean_text(row.get("assumption_id")),
                    "statement": _clean_text(row.get("statement")),
                    "kind": _clean_text(row.get("kind")),
                    "approval_state": _clean_text(row.get("approval_state")),
                    "operator_approved": bool(row.get("operator_approved")),
                    "blocking": bool(row.get("blocking")),
                }
                for row in assumptions
            ],
            "constraints": [
                {
                    "constraint_id": _clean_text(row.get("constraint_id")),
                    "statement": _clean_text(row.get("statement")),
                    "scope": _clean_text(row.get("scope")),
                }
                for row in constraints
            ],
            "non_goals": [
                {
                    "non_goal_id": _clean_text(row.get("non_goal_id")),
                    "statement": _clean_text(row.get("statement")),
                }
                for row in non_goals
            ],
        },
        "acceptance_contract": {
            "acceptance_target_count": len(acceptance_targets),
            "proof_target_count": len(proof_targets),
            "pending_proof_target_count": len(pending_proof),
            "review_gate_count": len(review_gates),
            "pending_review_gate_count": len(pending_review),
            "done_stage": done_stage,
            "implementation_complete": bool(completion_state.get("implementation_complete")),
            "evidence_complete": bool(completion_state.get("evidence_complete")),
            "operator_approved": bool(completion_state.get("operator_approved")),
            "summary": _clean_text(completion_state.get("summary")),
            "acceptance_targets": [
                {
                    "target_id": _clean_text(row.get("target_id")),
                    "statement": _clean_text(row.get("statement")),
                    "target_type": _clean_text(row.get("target_type")),
                    "milestone_id": _clean_text(row.get("milestone_id")),
                    "active": bool(row.get("active")),
                }
                for row in acceptance_targets
            ],
            "proof_targets": [
                {
                    "proof_id": _clean_text(row.get("proof_id")),
                    "statement": _clean_text(row.get("statement")),
                    "proof_class": _clean_text(row.get("proof_class")),
                    "required": bool(row.get("required")),
                    "blocking": bool(row.get("blocking")),
                    "status": _clean_text(row.get("status")),
                }
                for row in proof_targets
            ],
            "review_gates": [
                {
                    "gate_id": _clean_text(row.get("gate_id")),
                    "statement": _clean_text(row.get("statement")),
                    "reviewer_role": _clean_text(row.get("reviewer_role")),
                    "applies_at": _clean_text(row.get("applies_at")),
                    "blocking": bool(row.get("blocking")),
                    "status": _clean_text(row.get("status")),
                }
                for row in review_gates
            ],
        },
        "decomposition_contract": {
            "replanning_state": _clean_text(replanning.get("state") or "settled"),
            "replanning_summary": _clean_text(replanning.get("summary")),
            "current_revision_id": _clean_text(decomposition_contract.get("current_revision_id")),
            "invalidated_revision_id": _clean_text(replanning.get("invalidated_revision_id")),
            "invalidated_plan_version": int(replanning.get("invalidated_plan_version") or 0),
            "mode": _clean_text(decomposition_contract.get("mode")),
            "plan_version": int(decomposition_contract.get("plan_version") or 0) if decomposition_contract else 0,
            "milestone_count": int(plan_summary.get("milestone_count") or 0),
            "checkpoint_count": int(plan_summary.get("checkpoint_count") or 0),
            "dependency_edge_count": int(plan_summary.get("dependency_edge_count") or 0),
            "fanout_ready_count": int(plan_summary.get("fanout_ready_count") or 0),
            "forced_serial_count": int(plan_summary.get("forced_serial_count") or 0),
            "fanout_group_count": int(plan_summary.get("fanout_group_count") or 0),
            "direct_summary": _clean_text(plan_summary.get("direct_summary")),
            "active_milestone_id": _clean_text(plan_summary.get("active_milestone_id")),
            "active_checkpoint_id": _clean_text(plan_summary.get("active_checkpoint_id")),
            "artifact_ref": _clean_text(decomposition_contract.get("artifact_ref")),
            "linked_assumption_ids": [_clean_text(item) for item in list(decomposition_contract.get("linked_assumption_ids") or [])],
            "linked_acceptance_target_ids": [
                _clean_text(item) for item in list(decomposition_contract.get("linked_acceptance_target_ids") or [])
            ],
            "fanout_policy": _clean_text(dict(decomposition_contract.get("dependency_mapping") or {}).get("fanout_policy")),
            "dependency_summary": _clean_text(dict(decomposition_contract.get("dependency_mapping") or {}).get("summary")),
        },
        "freshness": freshness,
        "updated_utc": _clean_text(task.get("updated_utc")),
    }


def _task_projection_dedup_preference(task: Dict[str, Any]) -> tuple[int, int, int, int, float, str]:
    task_id = _clean_text(task.get("task_id"))
    title = _clean_text(task.get("title"))
    status = _clean_text(task.get("status")).lower()
    is_non_intake = not task_id.startswith("task_intake_")
    if title.startswith("[Scheduled Advisory Backend Request]"):
        is_non_intake = False
    completed = status in {"completed", "done"}
    evidence_backed_completion = bool(task.get("evidence_backed_completion"))
    delegated = bool(task.get("delegated"))
    updated = _parse_iso_utc(task.get("updated_utc"))
    updated_ts = float(updated.timestamp()) if updated is not None else 0.0
    return (
        1 if is_non_intake else 0,
        1 if not completed else 0,
        1 if evidence_backed_completion else 0,
        1 if delegated else 0,
        updated_ts,
        task_id,
    )


def _dedupe_projected_tasks(projected_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    passthrough: List[Dict[str, Any]] = []
    for task in projected_tasks:
        ledger_uid = _clean_text(task.get("ledger_uid"))
        task_id = _clean_text(task.get("task_id"))
        if not ledger_uid:
            if task_id and task_id not in {_clean_text(row.get("task_id")) for row in passthrough}:
                passthrough.append(task)
            continue
        existing = by_key.get(ledger_uid)
        if existing is None or _task_projection_dedup_preference(task) > _task_projection_dedup_preference(existing):
            by_key[ledger_uid] = task
    deduped = list(by_key.values()) + passthrough
    deduped.sort(key=lambda row: (_clean_text(row.get("updated_utc")), _clean_text(row.get("task_id"))), reverse=True)
    return deduped


def _overall_state(
    *,
    host_error: str,
    host_health: Dict[str, Any],
    assistant_state: Dict[str, Any],
    recovery_snapshot: Dict[str, Any],
    active_tasks: List[Dict[str, Any]],
    open_loops: List[Dict[str, Any]],
) -> str:
    if host_error:
        return "host_down_unreachable"
    host_status = _clean_text(host_health.get("status")).lower()
    recovery_status = _clean_text(recovery_snapshot.get("status")).lower()
    assistant_value = _clean_text(assistant_state.get("current_state")).lower()
    task_statuses = {_clean_text(task.get("status")).lower() for task in active_tasks}
    non_operator_blocked_statuses = {
        _status_taxonomy.task_status(task)
        for task in active_tasks
        if _status_taxonomy.task_blocked_for_non_operator_reason(task)
    }
    open_loop_kinds = {
        _clean_text(loop.get("kind")).lower()
        for loop in open_loops
        if _clean_text(loop.get("status")).lower() != "resolved"
    }
    waiting_on_operator = bool(
        any(_status_taxonomy.task_waiting_on_operator(task) for task in active_tasks)
        or any(_status_taxonomy.open_loop_waiting_on_operator(loop) for loop in open_loops)
        or assistant_value == _status_taxonomy.TASK_STATUS_WAITING_ON_OPERATOR
    )

    blocked_task_statuses = task_statuses.intersection(_BLOCKED_TASK_STATUSES.difference({"blocked"}))

    if host_status == "blocked":
        return "blocked"
    if recovery_status in {"blocked", "partial_restore"}:
        return "blocked"
    if blocked_task_statuses:
        return "blocked"
    if non_operator_blocked_statuses.intersection({"blocked"}):
        return "blocked"
    if assistant_value == "blocked":
        return "blocked"
    if recovery_status == "interrupted_recovering" or assistant_value == "interrupted_recovering":
        return "interrupted_recovering"
    if task_statuses.intersection(_INTERRUPTED_TASK_STATUSES):
        return "interrupted_recovering"
    if any(_status_taxonomy.open_loop_blocked_question(loop) for loop in open_loops):
        return "blocked"
    if waiting_on_operator:
        return "waiting_on_operator"
    if task_statuses.intersection({"waiting_on_verification"}) or "waiting_on_verification" in open_loop_kinds:
        return "waiting_on_verification"
    if task_statuses.intersection(_ACTIVE_TASK_WAIT_STATUSES):
        return "waiting_on_job"
    if active_tasks:
        return "waiting_on_job"
    if open_loop_kinds.difference({"deferred_idea"}):
        return "blocked"
    return "idle"


def _truth_conflicts(
    *,
    host_error: str,
    host_health: Dict[str, Any],
    recovery_snapshot: Dict[str, Any],
    stale_sources: List[str],
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    open_loops: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []

    host_mode = _clean_text(host_health.get("mode")).lower()
    if (host_error or host_mode == "host_down_unreachable") and (active_tasks or completed_tasks or open_loops):
        conflicts.append(
            {
                "kind": "host_unreachable_with_open_work",
                "field": "task_status_projection",
                "task_id": "",
                "canonical_value": "",
                "projected_value": "",
                "summary": "Host is unreachable while durable work remains open; live runtime truth cannot be confirmed.",
            }
        )
    if "task_register" in stale_sources and (active_tasks or open_loops):
        conflicts.append(
            {
                "kind": "stale_task_register_with_open_work",
                "field": "task_status_projection",
                "task_id": "",
                "canonical_value": "",
                "projected_value": "",
                "summary": "Task register freshness is stale while open work exists; treat completion claims as unproven.",
            }
        )
    for task in active_tasks + completed_tasks:
        register_status = _clean_text(task.get("register_status")).lower()
        projected_status = _clean_text(task.get("status")).lower()
        current_run_state = _clean_text(task.get("current_run_state")).lower()
        if register_status in {"completed", "done"} and projected_status in _BLOCKED_TASK_STATUSES.union(_INTERRUPTED_TASK_STATUSES):
            conflicts.append(
                {
                    "kind": "register_completed_but_projection_incomplete",
                    "field": "status",
                    "task_id": _clean_text(task.get("task_id")),
                    "canonical_value": register_status,
                    "projected_value": projected_status,
                    "summary": (
                        f"Task {task.get('task_id') or 'unknown'} says completed in register "
                        f"but delegated/recovery truth reports {projected_status or 'incomplete'}."
                    ),
                }
            )
        if current_run_state in {"failed", "stale", "conflict", "blocked"} and projected_status not in _BLOCKED_TASK_STATUSES.union(
            _INTERRUPTED_TASK_STATUSES
        ):
            conflicts.append(
                {
                    "kind": "canonical_run_blocked_but_projection_active",
                    "field": "task_run_state",
                    "task_id": _clean_text(task.get("task_id")),
                    "canonical_value": current_run_state,
                    "projected_value": projected_status,
                    "summary": (
                        f"Task {task.get('task_id') or 'unknown'} has canonical run state "
                        f"{current_run_state or 'unknown'} while the operator projection still shows {projected_status or 'active'}."
                    ),
                }
            )
    recovery_status = _clean_text(recovery_snapshot.get("status")).lower()
    if recovery_status in {"blocked", "interrupted_recovering"} and completed_tasks and not active_tasks:
        conflicts.append(
            {
                "kind": "recovery_not_settled_with_only_completed_tasks",
                "field": "recovery_status",
                "task_id": "",
                "canonical_value": "settled",
                "projected_value": recovery_status,
                "summary": "Recovery state is not settled while task projection appears complete; preserve recovery ambiguity.",
            }
        )
    return conflicts



def _status_language(
    *,
    operator_state: str,
    backend_projection_state: str,
    stale_sources: List[str],
    truth_conflicts: List[Dict[str, Any]],
    active_tasks: List[Dict[str, Any]],
    blocked_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    state = _clean_text(operator_state).lower() or "unknown"
    backend_state = _clean_text(backend_projection_state).lower() or "unknown"
    freshness = "fresh" if not stale_sources else "stale"
    copy = _overall_status_copy(
        operator_state=operator_state,
        backend_projection_state=backend_projection_state,
        stale_sources=stale_sources,
        truth_conflicts=truth_conflicts,
        blocked_tasks=blocked_tasks,
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
    )

    if backend_state == "unreachable":
        return {
            "state": "unreachable",
            "headline": "Backend unreachable",
            "tone": "danger",
            "freshness": freshness,
            "success_claim_allowed": False,
            **copy,
        }
    if truth_conflicts:
        return {
            "state": "conflicted",
            "headline": "Conflicting durable truth detected",
            "tone": "warning",
            "freshness": freshness,
            "success_claim_allowed": False,
            **copy,
        }
    if backend_state == "degraded":
        return {
            "state": "degraded",
            "headline": "Backend degraded; completion claims are unsafe",
            "tone": "danger",
            "freshness": freshness,
            "success_claim_allowed": False,
            **copy,
        }
    if backend_state == "stale" or stale_sources:
        return {
            "state": "stale",
            "headline": "Snapshot is stale; completion may be outdated",
            "tone": "warning",
            "freshness": freshness,
            "success_claim_allowed": False,
            **copy,
        }
    if state in {"blocked"} or blocked_tasks:
        return {
            "state": "blocked",
            "headline": "Blocked work requires action",
            "tone": "danger",
            "freshness": freshness,
            "success_claim_allowed": False,
            **copy,
        }
    if backend_state == "reconnecting":
        return {
            "state": "reconnecting",
            "headline": "Backend reconnecting; continuity still settling",
            "tone": "warning",
            "freshness": freshness,
            "success_claim_allowed": False,
            **copy,
        }
    if state in {"interrupted_recovering"}:
        return {
            "state": "interrupted",
            "headline": "Interrupted work is recovering",
            "tone": "warning",
            "freshness": freshness,
            "success_claim_allowed": False,
            **copy,
        }
    if state in {"waiting_on_job", "waiting_on_verification", "waiting_on_operator", "running", "scheduled", "waiting", "active", "retrying"}:
        return {
            "state": "waiting",
            "headline": "Work is waiting on an external step",
            "tone": "info",
            "freshness": freshness,
            "success_claim_allowed": False,
            **copy,
        }
    if state == "idle" and not active_tasks:
        all_completed_have_evidence = all(bool(task.get("evidence_backed_completion")) for task in completed_tasks)
        if completed_tasks and all_completed_have_evidence:
            return {
                "state": "successful",
                "headline": "Completed with durable evidence",
                "tone": "ok",
                "freshness": freshness,
                "success_claim_allowed": True,
                **copy,
            }
        return {
            "state": "idle",
            "headline": "No active work",
            "tone": "info",
            "freshness": freshness,
            "success_claim_allowed": bool(not completed_tasks),
            **copy,
        }
    return {
        "state": "unknown",
        "headline": "State is unknown or mixed",
        "tone": "warning",
        "freshness": freshness,
        "success_claim_allowed": False,
        **copy,
    }


def _backend_projection_truth(
    *,
    host_error: str,
    host_health: Dict[str, Any],
    recovery_snapshot: Dict[str, Any],
    stale_sources: List[str],
    truth_conflicts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    host_mode = _clean_text(host_health.get("mode")).lower()
    host_status = _clean_text(host_health.get("status")).lower()
    recovery_status = _clean_text(recovery_snapshot.get("status")).lower()
    stale = bool(stale_sources)
    conflict = bool(truth_conflicts)

    unreachable = bool(host_error or host_mode == "host_down_unreachable")
    reconnecting = bool(not unreachable and recovery_status == "interrupted_recovering")
    degraded = bool(
        not unreachable
        and not reconnecting
        and (host_status in {"blocked", "degraded"} or recovery_status in {"blocked", "partial_restore"} or conflict)
    )

    if unreachable:
        state = "unreachable"
        summary = "Backend is unreachable; the console cannot confirm live execution truth."
    elif reconnecting:
        state = "reconnecting"
        summary = "Backend is reachable but reconnecting/recovering; continuity is not settled yet."
    elif degraded:
        state = "degraded"
        summary = "Backend is reachable but degraded or ambiguous; avoid completion claims."
    elif stale:
        state = "stale"
        summary = "Backend appears reachable but projection freshness is stale; cached truth is advisory."
    else:
        state = "reachable"
        summary = "Backend is reachable and projection freshness is current."

    reasons: List[str] = []
    if host_error:
        reasons.append(f"host_error:{type(host_error).__name__}")
    if stale:
        reasons.append("stale_projection:" + ",".join(sorted({_clean_text(row) for row in stale_sources if _clean_text(row)})))
    if conflict:
        reasons.append("truth_conflicts_present")
    if recovery_status in {"blocked", "partial_restore", "interrupted_recovering"}:
        reasons.append(f"recovery_status:{recovery_status}")
    if host_status in {"blocked", "degraded"}:
        reasons.append(f"host_status:{host_status}")

    return {
        "schema_version": "backend_projection_truth_v1",
        "canonical_execution_owner": "execution_backend",
        "projection_owner": "web_console_projection",
        "state": state,
        "backend_reachable": bool(not unreachable),
        "projection_stale": stale,
        "uses_cached_projection": bool(stale or unreachable),
        "truth_conflicts_present": conflict,
        "host_status": host_status,
        "host_mode": host_mode,
        "recovery_status": recovery_status,
        "summary": summary,
        "reasons": reasons,
    }


def _execution_reference_complete(row: Dict[str, Any]) -> bool:
    return all(
        [
            bool(_clean_text(row.get("execution_id"))),
            bool(_clean_text(row.get("execution_ref"))),
            bool(_clean_text(row.get("binding_ref"))),
            bool(_clean_text(row.get("job_uri"))),
            bool(_clean_text(row.get("trace_id"))),
        ]
    )


def _task_runs_as_index(task: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("run_id") or "").strip(): dict(row) for row in list(task.get("task_runs") or []) if str(row.get("run_id") or "").strip()}


def _task_current_run(task: Dict[str, Any]) -> Dict[str, Any]:
    runs = [dict(row) for row in list(task.get("task_runs") or []) if isinstance(row, dict)]
    if not runs:
        return {}
    requested_run_id = _clean_text(task.get("current_run_id"))
    if requested_run_id:
        run = _task_runs_as_index(task).get(requested_run_id)
        if isinstance(run, dict) and run:
            return run
    current = None
    try:
        current = max(
            runs,
            key=lambda row: (
                int(row.get("revision") or -1),
                str(row.get("updated_utc") or row.get("created_utc") or ""),
                str(row.get("run_id") or ""),
            ),
        )
    except Exception:
        current = runs[0] if runs else {}
    return dict(current or {})


def _project_run_checkpoint(run_row: Dict[str, Any]) -> Dict[str, Any]:
    checkpoint = dict(run_row.get("checkpoint") or {}) if isinstance(run_row, dict) else {}
    if not checkpoint:
        return {}
    return {
        "schema_version": _clean_text(checkpoint.get("schema_version")),
        "checkpoint_id": _clean_text(checkpoint.get("checkpoint_id")),
        "checkpoint_status": _clean_text(checkpoint.get("checkpoint_status")),
        "checkpoint_utc": _clean_text(checkpoint.get("checkpoint_utc")),
        "checkpoint_note": _clean_text(checkpoint.get("checkpoint_note")),
        "checkpoint_source": _clean_text(checkpoint.get("checkpoint_source")),
        "checkpoint_revision": int(checkpoint.get("checkpoint_revision") or 0),
        "progress_percent": checkpoint.get("progress_percent"),
        "progress_summary": _clean_text(checkpoint.get("progress_summary")),
        "evidence_refs": [str(row) for row in list(checkpoint.get("evidence_refs") or []) if str(row).strip()],
    }


def _project_run_resume_decision(run_row: Dict[str, Any]) -> Dict[str, Any]:
    decision = dict(run_row.get("resume_decision") or {}) if isinstance(run_row, dict) else {}
    if not decision:
        return {}
    return {
        "schema_version": _clean_text(decision.get("schema_version")),
        "decision": _clean_text(decision.get("decision")),
        "decision_reason": _clean_text(decision.get("decision_reason")),
        "decision_summary": _clean_text(decision.get("decision_summary")),
        "decision_utc": _clean_text(decision.get("decision_utc")),
        "decision_source": _clean_text(decision.get("decision_source")),
        "checkpoint_id": _clean_text(decision.get("checkpoint_id")),
        "evidence_refs": [str(row) for row in list(decision.get("evidence_refs") or []) if str(row).strip()],
    }


def _project_run_intervention(run_row: Dict[str, Any]) -> Dict[str, Any]:
    interventions = [dict(row) for row in list(run_row.get("interventions") or []) if isinstance(row, dict)]
    if not interventions:
        return {}
    latest = max(
        interventions,
        key=lambda row: (
            _clean_text(row.get("requested_utc")),
            _clean_text(row.get("resolved_utc")),
            _clean_text(row.get("intervention_id")),
        ),
    )
    return {
        "schema_version": _clean_text(latest.get("schema_version")),
        "intervention_id": _clean_text(latest.get("intervention_id")),
        "action": _clean_text(latest.get("action")),
        "requested_utc": _clean_text(latest.get("requested_utc")),
        "requested_by": _clean_text(latest.get("requested_by")),
        "reason": _clean_text(latest.get("reason")),
        "outcome_state": _clean_text(latest.get("outcome_state")),
        "outcome_reason": _clean_text(latest.get("outcome_reason")),
        "resolved_utc": _clean_text(latest.get("resolved_utc")),
        "supersedes_intervention_id": _clean_text(latest.get("supersedes_intervention_id")),
        "conflict_with_intervention_id": _clean_text(latest.get("conflict_with_intervention_id")),
        "evidence_refs": [str(row) for row in list(latest.get("evidence_refs") or []) if str(row).strip()],
    }


def _run_state_to_reference_status(run_state: str) -> str:
    normalized = _clean_text(run_state).lower()
    if normalized in {"requested", "waiting"}:
        return "execution_ref_unresolved"
    if normalized in {"active", "running", "completed"}:
        return "execution_ref_active"
    if normalized in {"failed", "stale"}:
        return "execution_ref_stale"
    if normalized in {"conflict", "blocked"}:
        return "execution_ref_conflict"
    return "execution_ref_unresolved"


def _task_status_contract(
    *,
    task_id: str,
    task: Dict[str, Any],
    register_status: str,
    latest_status_row: Dict[str, Any],
    latest_status: str,
) -> Dict[str, Any]:
    canonical_status = _clean_text(register_status)
    delegated_status = _clean_text(latest_status_row.get("status") or latest_status)
    latest_summary = _clean_text(latest_status_row.get("summary") or "")
    current_run = _task_current_run(task)
    current_run_id = _clean_text(current_run.get("run_id"))
    current_run_state = _clean_text(current_run.get("run_state"))
    sources = [
        {
            "source": "BD05.task_register",
            "owner": "bd05_task_register",
            "value": register_status,
            "provenance": {
                "path": f"task_register.active_tasks[{_clean_text(task_id)}].status",
                "schema": "lab_assistant_task_register_v1",
            },
            "quality": "canonical",
        },
    ]
    if current_run_id:
        sources.append(
            {
                "source": "BD05.task_runs",
                "owner": "bd05_task_register",
                "value": current_run_state,
                "provenance": {
                    "path": f"task_register.active_tasks[{_clean_text(task_id)}].task_runs[{current_run_id}].run_state",
                    "schema": "lab_assistant_task_run_record_v1",
                    "run_id": current_run_id,
                },
                "quality": "canonical",
            }
        )

    synthesis_error = _clean_text(latest_status_row.get("evidence_status") or "")
    delegated_available = bool(list(task.get("delegation_refs") or []))
    sources.append(
        {
            "source": "DF0C.delegation_bindings",
            "owner": "execution_backend",
            "value": delegated_status,
            "provenance": {
                "path": f"delegation_bindings.synthesize_task_status({task_id})",
                "schema": "lab_assistant_task_delegation_status_v1",
                "deviation_reason": synthesis_error,
            },
            "quality": "derived",
            "available": str(delegated_available).lower(),
        }
    )
    projection_sources = [row.get("execution_reconciliation") for row in [dict(latest_status_row)] if isinstance(row.get("execution_reconciliation"), dict)]
    if projection_sources:
        projection = dict(projection_sources[0])
        sources.append(
            {
                "source": "execution_reconciliation.report",
                "owner": "execution_backend",
                "value": _clean_text(projection.get("projection", {}).get("status") or ""),
                "provenance": {
                    "path": "execution_reconciliation.record_task_reconciliation_report",
                    "reconciliation_id": _clean_text(projection.get("reconciliation_id")),
                    "schema": "lab_assistant_execution_reconciliation_v1",
                },
                "quality": "derived",
            }
        )

    effective_status = _clean_text(latest_status or delegated_status)
    has_mismatch = canonical_status.lower() != effective_status.lower() if canonical_status else False
    contract_state = "fresh"
    unknown_reasons: list[str] = []
    if not canonical_status:
        contract_state = "unknown"
        unknown_reasons.append("missing_canonical_status_field")
    if has_mismatch:
        contract_state = "degraded" if contract_state == "fresh" else "unknown"

    return {
        "schema_version": "lab_assistant_task_status_projection_v1",
        "task_id": task_id,
        "contract_state": contract_state,
        "effective_status": effective_status,
        "canonical_status": canonical_status,
        "current_run_id": current_run_id,
        "current_run_state": current_run_state,
        "latest_status_summary": latest_summary,
        "source_precedence": ["canonical_task", "canonical_task_run", "delegation_bindings", "reconciliation"],
        "sources": sources,
        "mismatch": has_mismatch,
        "mismatch_reason": "projected_status_differs_from_task_register" if has_mismatch else "",
        "unknown_reasons": unknown_reasons,
    }


def _execution_surface_projection(*, task_register: Dict[str, Any], now_utc: str) -> Dict[str, Any]:
    tasks = [
        dict(task)
        for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks")
        for task in list(task_register.get(bucket_name) or [])
        if isinstance(task, dict)
    ]
    projections: list[Dict[str, Any]] = []
    surface_counts: Dict[str, int] = {}
    missing_or_unknown = 0

    for task in tasks:
        task_id = _clean_text(task.get("task_id"))
        run_row = _task_current_run(task)
        has_run = bool(run_row)
        ownership = dict(task.get("execution_surface_ownership") or {})
        has_ownership = bool(ownership)
        selected_surface = _clean_text(run_row.get("surface") or ownership.get("selected_surface") or "other")
        reference_status = _clean_text(
            _run_state_to_reference_status(str(run_row.get("run_state") or ""))
            if has_run
            else str(ownership.get("reference_status") or "execution_ref_unresolved")
        )
        if has_run:
            provenance_canonical = "bd05_task_run_record"
            attention_state = _clean_text(run_row.get("attention_state"))
            run_state = _clean_text(run_row.get("run_state") or "requested")
            run_evidence_refs = [str(value) for value in list(run_row.get("evidence_refs") or []) if str(value).strip()]
            run_writer = _clean_text(run_row.get("writer"))
            reference_status_reason = _clean_text(run_row.get("run_status_source"))
            owner_uid = _clean_text(ownership.get("owner_uid"))
        else:
            provenance_canonical = "bd05_task_register"
            attention_state = ""
            run_state = ""
            run_evidence_refs = []
            run_writer = ""
            reference_status_reason = _clean_text(ownership.get("reference_status_reason"))
            owner_uid = _clean_text(ownership.get("owner_uid"))
        surface_counts[selected_surface] = int(surface_counts.get(selected_surface, 0)) + 1

        if not has_run and not has_ownership:
            missing_or_unknown += 1

        reference_row = run_row if has_run else ownership
        reference_complete = _execution_reference_complete(reference_row) if (has_run or has_ownership) else False
        contract_state = "fresh"
        unknown_reasons: list[str] = []
        if not has_ownership and not has_run:
            contract_state = "unknown"
            unknown_reasons.append("execution_surface_projection_missing")
        elif has_run:
            if run_state in {"requested", "waiting"} and not reference_complete:
                contract_state = "degraded"
                unknown_reasons.append("run_record_missing_required_execution_refs")
        elif reference_status == "execution_ref_active" and not reference_complete:
            contract_state = "degraded"
            unknown_reasons.append("active_status_without_all_required_refs")

        if reference_status in {"execution_ref_stale", "execution_ref_conflict"}:
            contract_state = "degraded"
            reason = _clean_text(ownership.get("reference_status_reason") or "")
            if reason:
                unknown_reasons.append(reason)
            else:
                unknown_reasons.append(f"{reference_status}")
        if has_run and has_ownership:
            ownership_surface = _clean_text(ownership.get("selected_surface"))
            ownership_execution_id = _clean_text(ownership.get("execution_id"))
            if (
                (ownership_surface and ownership_surface != selected_surface)
                or (
                    ownership_execution_id
                    and ownership_execution_id != _clean_text(run_row.get("execution_id") or run_row.get("run_id"))
                )
            ):
                contract_state = "degraded"
                unknown_reasons.append("ownership_run_disagreement")
        run_intervention = _project_run_intervention(run_row)
        intervention_outcome = _clean_text(run_intervention.get("outcome_state")).lower()
        if intervention_outcome in {"blocked", "conflict"}:
            contract_state = "degraded"
            unknown_reasons.append(f"intervention_{intervention_outcome}")

        projection = {
            "task_id": task_id,
            "task_title": _clean_text(task.get("title")),
            "selected_surface": selected_surface,
            "owner_uid": owner_uid,
            "reference_status": reference_status,
            "reference_status_reason": reference_status_reason,
            "execution_id": _clean_text(run_row.get("execution_id") or ownership.get("execution_id")),
            "execution_ref": _clean_text(run_row.get("execution_ref") or ownership.get("execution_ref")),
            "binding_ref": _clean_text(run_row.get("binding_ref") or ownership.get("binding_ref")),
            "job_uri": _clean_text(run_row.get("job_uri") or ownership.get("job_uri")),
            "trace_id": _clean_text(run_row.get("trace_id") or ownership.get("trace_id")),
            "reference_revision": int(run_row.get("revision") or ownership.get("reference_revision") or 0),
            "schema_version": _clean_text(
                run_row.get("schema_version") or ownership.get("schema_version") or "lab_assistant_task_run_record_v1"
            ),
            "run_state": run_state,
            "attention_state": attention_state,
            "current_run_id": _clean_text(run_row.get("run_id")),
            "run_evidence_refs": run_evidence_refs,
            "run_writer": run_writer,
            "run_status_source": _clean_text(run_row.get("run_status_source")),
            "run_checkpoint": _project_run_checkpoint(run_row),
            "run_resume_decision": _project_run_resume_decision(run_row),
            "run_intervention": run_intervention,
            "provenance": {
                "source": "BD05.task_register.task_runs[current_run_id]" if has_run else "BD05.task_register.execution_surface_ownership",
                "canonical_owner": provenance_canonical,
            },
            "reference_complete": bool(reference_complete),
            "contract_state": contract_state,
            "unknown_reasons": unknown_reasons,
            "generated_utc": now_utc,
        }
        projections.append(projection)

    return {
        "schema_version": EXECUTION_SURFACE_SCHEMA_VERSION,
        "generated_utc": now_utc,
        "canonical_owner": "bd05_task_register",
        "source": "task_register.active_tasks[n].execution_surface_ownership",
        "state": "unknown" if missing_or_unknown else ("degraded" if any(row["contract_state"] == "degraded" for row in projections) else "fresh"),
        "missing_or_unknown_task_count": int(missing_or_unknown),
        "degraded_task_count": int(
            len([row for row in projections if _clean_text(row.get("contract_state")).lower() == "degraded"])
        ),
        "surface_counts": {key: int(value) for key, value in surface_counts.items()},
        "tasks": projections,
    }


def _operator_facing_state(raw_status: Any) -> str:
    status = _clean_text(raw_status).lower()
    if status == "failed":
        return "FAILED"
    if status in {"blocked", "evidence_missing", "ambiguous_artifact", "interrupted", "interrupted_recovering", "host_down_unreachable"}:
        return "BLOCKED"
    if status in {"needs_input", "waiting_on_operator"}:
        return "NEEDS_INPUT"
    if status in {"stalled", "stale"}:
        return "STALLED"
    if status in {"degraded", "conflicted", "partial_restore_degraded"}:
        return "DEGRADED"
    if status == "retrying":
        return "RETRYING"
    if status in {"active", "running", "waiting_on_job", "waiting_on_verification"}:
        return "RUNNING"
    if status == "scheduled":
        return "SCHEDULED"
    if status == "waiting":
        return "WAITING"
    if status in {"completed", "done"}:
        return "COMPLETED"
    if not status:
        return "BLOCKED"
    return status.upper()


def _task_operator_facing_state(task: Dict[str, Any]) -> str:
    raw_status = _clean_text(task.get("status")).lower()
    current_run_state = _clean_text(task.get("current_run_state")).lower()
    delegated = bool(task.get("delegated"))
    checkpoint_note = _clean_text(dict(task.get("run_checkpoint") or {}).get("checkpoint_note")).lower()
    native_statuses = {
        _clean_text(row.get("native_status")).lower()
        for row in list(task.get("delegation_summaries") or [])
        if isinstance(row, dict) and _clean_text(row.get("native_status"))
    }

    if current_run_state:
        if (
            current_run_state in {"active", "running"}
            and checkpoint_note == "execution_surface_bound"
            and native_statuses.intersection({"queued", "scheduled", "requested", "waiting", "starting", "idle", "open"})
        ):
            return "WAITING"
        if current_run_state in {"active", "running"}:
            return "RUNNING"
        if current_run_state in {"requested", "waiting"}:
            return "WAITING"
        if current_run_state == "retrying":
            return "RETRYING"
        return _operator_facing_state(current_run_state)

    if raw_status in {"waiting_on_job", "waiting_on_verification"}:
        if "running" in native_statuses:
            return "RUNNING"
        if native_statuses.intersection({"queued", "scheduled", "requested", "waiting", "starting", "idle", "open"}):
            return "WAITING"
        if delegated:
            return "WAITING"

    if raw_status == "active" and delegated and not current_run_state:
        if "running" in native_statuses:
            return "RUNNING"
        if native_statuses:
            return "WAITING"

    return _operator_facing_state(raw_status)


def _state_priority(raw_status: Any) -> int:
    state = _operator_facing_state(raw_status)
    try:
        return _OPERATOR_STATE_PRECEDENCE.index(state)
    except ValueError:
        return len(_OPERATOR_STATE_PRECEDENCE)


def _task_reason(task: Dict[str, Any]) -> str:
    return _clean_text(
        task.get("waiting_reason")
        or dict(task.get("run_checkpoint") or {}).get("progress_summary")
        or dict(task.get("run_resume_decision") or {}).get("reason")
        or dict(task.get("run_intervention") or {}).get("reason")
        or dict(task.get("next_decision") or {}).get("summary")
        or task.get("next_expected_action")
    )


def _task_next_action_contract(task: Dict[str, Any]) -> Dict[str, Any]:
    state = _task_operator_facing_state(task)
    next_decision = dict(task.get("next_decision") or {})
    intake = dict(task.get("intake") or {})
    missing_information = list(intake.get("missing_information") or [])
    next_type = _clean_text(next_decision.get("kind")) or "assistant_continue"
    prompt = _clean_text(next_decision.get("summary") or task.get("next_expected_action"))
    blocking = state in {"BLOCKED", "NEEDS_INPUT"}
    input_schema: Dict[str, Any] | None = None

    if state == "NEEDS_INPUT" and missing_information:
        next_type = "provide_input"
        prompt = prompt or "Provide the missing input so work can continue."
        input_schema = {
            "required_fields": [
                {
                    "field": _clean_text(row.get("field")),
                    "question": _clean_text(row.get("question")),
                    "reason": _clean_text(row.get("reason")),
                }
                for row in missing_information
                if _clean_text(row.get("field")) or _clean_text(row.get("question"))
            ]
        }
    elif state == "BLOCKED" and _clean_text(task.get("status")).lower() in {"failed", "evidence_missing", "ambiguous_artifact"}:
        next_type = "retry"
        prompt = prompt or "Retry after fixing the blocking issue."
    elif state in {"RUNNING", "SCHEDULED", "WAITING"}:
        next_type = next_type or "wait"
        prompt = prompt or "Wait for the current run to reach the next checkpoint."
        blocking = False

    return {
        "type": next_type,
        "prompt": prompt,
        "input_schema": input_schema,
        "blocking": blocking,
    }


def _task_allowed_actions(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    state = _task_operator_facing_state(task)
    raw_status = _clean_text(task.get("status")).lower()
    delegated = bool(task.get("delegated"))
    current_run_state = _clean_text(task.get("current_run_state")).lower()
    actions = [
        {
            "type": "provide_input",
            "allowed": state == "NEEDS_INPUT",
            "reason_if_blocked": "" if state == "NEEDS_INPUT" else "This task is not currently waiting on operator input.",
            "risk_level": "low",
            "idempotent": False,
        },
        {
            "type": "retry",
            "allowed": state in {"BLOCKED", "FAILED"} or raw_status in {"failed", "evidence_missing", "ambiguous_artifact"},
            "reason_if_blocked": "" if (state in {"BLOCKED", "FAILED"} or raw_status in {"failed", "evidence_missing", "ambiguous_artifact"}) else "Retry is only available for blocked or failed work.",
            "risk_level": "medium",
            "idempotent": False,
        },
        {
            "type": "cancel",
            "allowed": delegated and current_run_state in {"running", "requested", "waiting", "retrying"},
            "reason_if_blocked": "" if (delegated and current_run_state in {"running", "requested", "waiting", "retrying"}) else "Cancel is only available while delegated work is actively in flight.",
            "risk_level": "high",
            "idempotent": True,
        },
        {
            "type": "rebind",
            "allowed": raw_status in {"interrupted", "interrupted_recovering"} or current_run_state in {"stale", "conflict"},
            "reason_if_blocked": "" if (raw_status in {"interrupted", "interrupted_recovering"} or current_run_state in {"stale", "conflict"}) else "Rebind is only available when continuity is interrupted or execution truth conflicts.",
            "risk_level": "medium",
            "idempotent": True,
        },
    ]
    return actions


def _task_stage(task: Dict[str, Any]) -> Dict[str, Any]:
    checkpoint = dict(task.get("run_checkpoint") or {})
    progress_summary = _clean_text(checkpoint.get("progress_summary"))
    progress_percent = checkpoint.get("progress_percent")
    stage_label = ""
    if progress_summary and progress_percent not in {None, ""}:
        stage_label = f"{progress_summary} ({int(progress_percent)}%)"
    elif progress_summary:
        stage_label = progress_summary
    else:
        run_state = _clean_text(task.get("current_run_state"))
        stage_label = run_state.replace("_", " ").title() if run_state else ""
    return {
        "name": _clean_text(task.get("current_run_state") or task.get("status")),
        "label": stage_label,
        "progress": int(progress_percent) if progress_percent not in {None, ""} else None,
    }


def _task_execution_surface_label(task: Dict[str, Any]) -> str:
    if not bool(task.get("delegated")):
        return ""
    state = _task_operator_facing_state(task)
    lifecycle = "Running" if state == "RUNNING" else ("Scheduled" if state in {"SCHEDULED", "WAITING", "RETRYING"} else "Attention")
    return f"{lifecycle} (Pipeline)"


def _maybe_int(value: Any) -> int | None:
    try:
        if value in {None, ""}:
            return None
        return int(value)
    except Exception:
        return None


def _confidence_projection(task: Dict[str, Any]) -> Dict[str, Any]:
    intake = dict(task.get("intake") or {})
    if "confidence" not in intake or intake.get("confidence") in {None, ""}:
        return {
            "score": None,
            "label": "",
            "warn": False,
        }
    score = float(intake.get("confidence") or 0.0)
    if score <= 0.0 and not list(intake.get("missing_information") or []) and not _clean_text(intake.get("readiness")):
        return {
            "score": None,
            "label": "",
            "warn": False,
        }
    if score >= 0.75:
        label = "HIGH"
    elif score >= 0.5:
        label = "MEDIUM"
    else:
        label = "LOW"
    return {
        "score": round(score, 2),
        "label": label,
        "warn": label == "LOW",
    }


def _timing_projection(task: Dict[str, Any]) -> Dict[str, Any]:
    escalation_in_s = _maybe_int(task.get("escalation_in_s"))
    stalled_threshold_s = _maybe_int(task.get("stalled_threshold_s"))
    interpreted = ""
    if escalation_in_s is not None:
        if escalation_in_s >= 3600:
            interpreted = f"Escalates in {max(1, escalation_in_s // 3600)}h"
        elif escalation_in_s >= 60:
            interpreted = f"Escalates in {max(1, escalation_in_s // 60)}m"
        else:
            interpreted = f"Escalates in {max(1, escalation_in_s)}s"
    return {
        "stalled_threshold_s": stalled_threshold_s,
        "escalation_in_s": escalation_in_s,
        "interpreted_label": interpreted,
    }


def _retry_projection(task: Dict[str, Any]) -> Dict[str, Any]:
    used = _maybe_int(task.get("retry_used"))
    limit = _maybe_int(task.get("retry_limit"))
    if used is not None and limit is not None:
        label = f"Retry {used}/{limit}"
    elif used is not None and used > 0:
        label = f"Retry {used}"
    else:
        label = ""
    return {
        "used": used,
        "limit": limit,
        "label": label,
    }


def _action_consequence(action_type: str) -> str:
    normalized = _clean_text(action_type)
    if normalized == "provide_input":
        return "Work stays blocked until the missing input arrives."
    if normalized == "retry":
        return "A new attempt starts and the current failure state may be replaced."
    if normalized == "cancel":
        return "In-flight delegated work is asked to stop if the substrate accepts cancellation."
    if normalized == "rebind":
        return "Execution truth is reattached without claiming new progress."
    if normalized == "wait":
        return "No operator mutation is applied while the plane waits for the next checkpoint."
    return ""


def _recommended_action_contract(task: Dict[str, Any]) -> Dict[str, Any]:
    next_action = _task_next_action_contract(task)
    allowed_actions = {str(row.get("type") or ""): row for row in _task_allowed_actions(task)}
    action_type = _clean_text(next_action.get("type"))
    allowed = allowed_actions.get(action_type) or {}
    return {
        "type": action_type,
        "prompt": _clean_text(next_action.get("prompt")),
        "recommended": bool(action_type and bool(allowed.get("allowed"))),
        "risk_level": _clean_text(allowed.get("risk_level")) or ("medium" if bool(next_action.get("blocking")) else "low"),
        "consequence": _action_consequence(action_type),
    }


def _artifact_preview(task: Dict[str, Any]) -> Dict[str, Any]:
    refs = [_safe_relative_path(row) for row in list(task.get("evidence_refs") or []) if _safe_relative_path(row)]
    return {
        "count": len(refs),
        "items": refs[:3],
    }


def _work_item_metadata(task: Dict[str, Any]) -> List[str]:
    metadata: List[str] = []
    confidence = _confidence_projection(task)
    timing = _timing_projection(task)
    retry = _retry_projection(task)
    artifacts = _artifact_preview(task)
    if confidence["label"]:
        metadata.append(f"Confidence {confidence['label']}")
    if timing["interpreted_label"]:
        metadata.append(str(timing["interpreted_label"]))
    if retry["label"]:
        metadata.append(str(retry["label"]))
    if artifacts["count"]:
        metadata.append(f"{artifacts['count']} artifact{'s' if artifacts['count'] != 1 else ''}")
    return metadata


def _command_center_projection(
    *,
    generated_utc: str,
    freshness_seconds: int,
    confidence: str,
    headline: str,
    top_notification: Dict[str, Any] | None,
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    cards = []
    for task in active_tasks:
        state = _task_operator_facing_state(task)
        cards.append(
            {
                "task_id": _clean_text(task.get("task_id")),
                "title": _clean_text(task.get("title")),
                "state": state,
                "state_copy_key": _task_state_copy(task)["copy_key"],
                "state_label": _task_state_copy(task)["label"],
                "state_detail": _task_state_copy(task)["detail"],
                "reason": _task_reason(task) or "No reason recorded.",
                "next_action": _clean_text(_task_next_action_contract(task).get("prompt")) or "No next action recorded.",
                "stage": _task_stage(task),
                "execution_surface": _task_execution_surface_label(task),
                "metadata": _work_item_metadata(task),
                "confidence": _confidence_projection(task),
                "recommended_action": _recommended_action_contract(task),
                "artifact_preview": _artifact_preview(task),
            }
        )

    needs_attention = sorted(
        [row for row in cards if row["state"] not in {"RUNNING", "SCHEDULED", "WAITING", "RETRYING"}],
        key=lambda row: (_state_priority(row.get("state")), row.get("title") or row.get("task_id")),
    )[:5]
    running = [row for row in cards if row["state"] == "RUNNING"]
    scheduled = [row for row in cards if row["state"] in {"SCHEDULED", "WAITING", "RETRYING"}]

    return {
        "schema_version": COMMAND_CENTER_SCHEMA_VERSION,
        "headline": headline,
        "snapshot_meta": {
            "generated_at": generated_utc,
            "freshness_seconds": int(freshness_seconds),
            "confidence": confidence,
        },
        "intake": {
            "title": "Start work",
            "prompt": "Send a new request through the plane.",
            "entrypoint": "/api/agents/send",
        },
        "needs_attention": {
            "title": "Needs Attention",
            "count": len(needs_attention),
            "items": needs_attention,
        },
        "running": {
            "title": "Running",
            "count": len(running),
            "items": running,
        },
        "scheduled": {
            "title": "Scheduled",
            "count": len(scheduled),
            "items": scheduled,
        },
        "completed": {
            "title": "Completed",
            "count": len(completed_tasks),
            "collapsed": True,
        },
        "top_notification": dict(top_notification or {}),
    }


def _work_item_projection(task: Dict[str, Any]) -> Dict[str, Any]:
    next_action = _task_next_action_contract(task)
    confidence = _confidence_projection(task)
    timing = _timing_projection(task)
    retry = _retry_projection(task)
    recommended_action = _recommended_action_contract(task)
    artifacts = _artifact_preview(task)
    return {
        "schema_version": WORK_ITEM_SCHEMA_VERSION,
        "task_id": _clean_text(task.get("task_id")),
        "stable_title": _clean_text(task.get("title")),
        "state": _task_operator_facing_state(task),
        "state_copy_key": _task_state_copy(task)["copy_key"],
        "state_label": _task_state_copy(task)["label"],
        "state_detail": _task_state_copy(task)["detail"],
        "reason": _task_reason(task),
        "next_action": next_action,
        "recommended_action": recommended_action,
        "allowed_actions": _task_allowed_actions(task),
        "stage": _task_stage(task),
        "execution_surface": _task_execution_surface_label(task),
        "confidence": confidence,
        "timing": timing,
        "retry": retry,
        "causal_chain": list(task.get("causal_chain") or []),
        "display_depth": _maybe_int(task.get("display_depth")) or 0,
        "origin_intent": _clean_text(dict(task.get("intake") or {}).get("summary")),
        "history_summary": _clean_text(task.get("delegated_outcome_writeback", {}).get("decision_summary")),
        "previous_attempts": max(0, int(task.get("delegation_count") or 0) - 1),
        "details": {
            "task_id": _clean_text(task.get("task_id")),
            "run_id": _clean_text(task.get("current_run_id")),
            "logs": [],
            "artifacts": artifacts["items"] if artifacts["items"] else [_safe_relative_path(row) for row in list(task.get("evidence_refs") or []) if _safe_relative_path(row)],
        },
    }


def _snapshot_status_contract(
    *,
    operator_state: str,
    assistant_current_state: str,
    backend_projection_state: str,
    recovery_snapshot_status: str,
    stale_sources: List[str],
    truth_conflicts: List[Dict[str, Any]],
    task_register_present: bool,
    task_status_contracts: List[Dict[str, Any]],
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    execution_surface: Dict[str, Any],
    evaluated_utc: str,
) -> Dict[str, Any]:
    missing_sources: list[str] = []
    if not bool(task_register_present):
        missing_sources.append("task_register_snapshot_missing")
    if any(_clean_text(row.get("contract_state")).lower() == "unknown" for row in task_status_contracts):
        missing_sources.append("task_status_projection_unknown")
    if _clean_text(execution_surface.get("state")).lower() == "unknown":
        missing_sources.append("execution_surface_projection_unknown")

    contract_state = "fresh"
    if missing_sources:
        contract_state = "unknown"
    elif truth_conflicts or stale_sources:
        contract_state = "degraded"

    fields: list[Dict[str, Any]] = [
        {
            "field": "operator_state",
            "effective_value": operator_state,
            "source_order": [
                "backend_projection.state",
                "assistant_state.current_state",
                "recovery_snapshot.status",
            ],
            "sources": [
                {"source": "backend_projection.state", "value": _clean_text(backend_projection_state), "owner": "web_console_projection"},
                {
                    "source": "assistant_state.current_state",
                    "value": _clean_text(assistant_current_state),
                    "owner": "assistant_state",
                },
                {
                    "source": "recovery_snapshot.status",
                    "value": _clean_text(recovery_snapshot_status),
                    "owner": "recovery_snapshot",
                },
            ],
        },
        {
            "field": "task_status_projection",
            "effective_value": "mixed",
            "source_order": [
                "task_register.status",
                "task_register.task_runs[current_run_id].run_state",
                "delegation_bindings.synthesis",
                "execution_reconciliation",
            ],
            "samples": [
                {
                    "task_id": row.get("task_id"),
                    "canonical": row.get("canonical_status"),
                    "current_run_id": row.get("current_run_id"),
                    "current_run_state": row.get("current_run_state"),
                    "effective": row.get("effective_status"),
                    "contract_state": row.get("contract_state"),
                    "mismatch": bool(row.get("mismatch")),
                }
                for row in task_status_contracts[:10]
            ],
        },
        {
            "field": "execution_surface_projection",
            "effective_value": _clean_text(execution_surface.get("state")) or "unknown",
            "source_order": [
                "task_register.task_runs[current_run_id]",
                "task_register.execution_surface_ownership",
            ],
            "samples": [
                {
                    "task_id": row.get("task_id"),
                    "selected_surface": row.get("selected_surface"),
                    "run_state": row.get("run_state"),
                    "reference_status": row.get("reference_status"),
                    "contract_state": row.get("contract_state"),
                }
                for row in list(execution_surface.get("tasks") or [])[:10]
            ],
        },
    ]

    conflicts = []
    for row in truth_conflicts:
        conflicts.append(
            {
                "kind": str(row.get("kind")),
                "field": _clean_text(row.get("field") or "status"),
                "task_id": _clean_text(row.get("task_id")),
                "canonical_value": _clean_text(row.get("canonical_value")),
                "projected_value": _clean_text(row.get("projected_value")),
                "summary": _clean_text(row.get("summary")),
            }
        )

    return {
        "schema_version": STATUS_CONTRACT_SCHEMA_VERSION,
        "evaluated_utc": evaluated_utc,
        "state": contract_state,
        "stale_sources": sorted({_clean_text(row) for row in stale_sources if _clean_text(row)}),
        "missing_sources": sorted({_clean_text(row) for row in missing_sources if _clean_text(row)}),
        "source_precedence": [
            "task_register -> delegation_bindings -> execution_reconciliation -> backend_projection",
            "execution_surface contract participates in execution_surface.* fields",
        ],
        "fields": fields,
        "task_count": len(task_status_contracts),
        "active_task_count": len(active_tasks),
        "completed_task_count": len(completed_tasks),
        "degraded_reasons": (
            [f"backend:{_clean_text(r)}" for r in stale_sources if _clean_text(r)] + [f"conflict:{_clean_text(r.get('kind'))}" for r in truth_conflicts]
        ),
        "truth_conflicts": conflicts,
    }


def _snapshot_projection_contract(
    *,
    generated_utc: str,
    backend_projection: Dict[str, Any],
    status_contract: Dict[str, Any],
    task_register_projection: Dict[str, Any],
) -> Dict[str, Any]:
    backend_state = _clean_text(backend_projection.get("state")).lower() or "unknown"
    status_contract_state = _clean_text(status_contract.get("state")).lower() or "unknown"
    return {
        "schema_version": PROJECTION_CONTRACT_SCHEMA_VERSION,
        "evaluated_utc": generated_utc,
        "default_operator_projection": {
            "api_path": "/api/admin/lab_assistant/snapshot",
            "canonical_for_operator_use": True,
            "legacy_endpoints_role": "diagnostic_only",
        },
        "ownership": {
            "canonical_truth_owner": "BD05.task_register",
            "projection_owner": "web_console_projection",
        },
        "field_contracts": [
            {
                "field": "operator_state",
                "projection_field": "operator_state",
                "provenance_field": "status_contract.fields[operator_state]",
                "source_order": [
                    "backend_projection.state",
                    "assistant_state.current_state",
                    "recovery_snapshot.status",
                ],
            },
            {
                "field": "task_status_projection",
                "projection_field": "active_tasks[].status / completed_tasks[].status",
                "provenance_field": "status_contract.fields[task_status_projection]",
                "source_order": [
                    "task_register.status",
                    "task_register.task_runs[current_run_id].run_state",
                    "delegation_bindings.synthesis",
                    "execution_reconciliation",
                ],
            },
            {
                "field": "execution_surface_projection",
                "projection_field": "execution_surface",
                "provenance_field": "status_contract.fields[execution_surface_projection]",
                "source_order": [
                    "task_register.task_runs[current_run_id]",
                    "task_register.execution_surface_ownership",
                ],
            },
            {
                "field": "task_register_freshness",
                "projection_field": "task_register.snapshot_projection",
                "provenance_field": "freshness.markers[source=task_register]",
                "source_order": [
                    "task_register.write_generation",
                    "task_register.snapshot_projection.refresh_generation",
                    "task_register.summary_cache.generated_utc",
                ],
            },
        ],
        "cache_display_rules": {
            "cached_truth_must_be_marked": True,
            "cached_truth_may_render_when_state_in": ["stale", "reconnecting", "unreachable"],
            "cached_truth_must_not_be_presented_as_live": True,
            "degraded_truth_requires_explicit_marker": True,
        },
        "projection_state": {
            "backend_state": backend_state,
            "status_contract_state": status_contract_state,
            "task_register_snapshot_stale": bool(task_register_projection.get("stale")),
            "uses_cached_projection": bool(backend_projection.get("uses_cached_projection")),
            "cached_truth_must_be_marked": bool(
                backend_projection.get("uses_cached_projection")
                or backend_state in {"stale", "reconnecting", "unreachable"}
            ),
        },
    }


def _build_operator_summary(
    *,
    repo_root: Path,
    generated_utc: str,
    operator_state: str,
    backend_projection: Dict[str, Any],
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
    open_loops: List[Dict[str, Any]],
    delegated_work: List[Dict[str, Any]],
    stale_sources: List[str],
    truth_conflicts: List[Dict[str, Any]],
    mission_contract: Dict[str, Any],
) -> Dict[str, Any]:
    interrupted_tasks = [
        task
        for task in active_tasks
        if _clean_text(task.get("status")).lower() in _INTERRUPTED_TASK_STATUSES
    ]
    blocked_tasks = [
        task
        for task in active_tasks
        if _clean_text(task.get("status")).lower() in _BLOCKED_TASK_STATUSES
    ]
    blocked_loops = [loop for loop in open_loops if _clean_text(loop.get("kind")).lower() == "blocked_question"]
    active_work = [
        task
        for task in active_tasks
        if _clean_text(task.get("status")).lower() not in _BLOCKED_TASK_STATUSES.union(_INTERRUPTED_TASK_STATUSES)
    ]

    active_items = [
        {
            "task_id": _clean_text(task.get("task_id")),
            "title": _clean_text(task.get("title")),
            "status": _clean_text(task.get("status")),
            "summary": _clean_text(task.get("waiting_reason") or task.get("next_expected_action")),
            "state_copy_key": _task_state_copy(task)["copy_key"],
            "state_label": _task_state_copy(task)["label"],
        }
        for task in active_work
    ]
    blocked_items = [
        {
            "task_id": _clean_text(task.get("task_id")),
            "title": _clean_text(task.get("title")),
            "status": _clean_text(task.get("status")),
            "summary": _clean_text(task.get("waiting_reason") or task.get("next_expected_action")),
            "state_copy_key": _task_state_copy(task)["copy_key"],
            "state_label": _task_state_copy(task)["label"],
        }
        for task in blocked_tasks
    ]
    blocked_items.extend(
        {
            "task_id": _clean_text(task.get("task_id")),
            "title": _clean_text(task.get("title")),
            "status": _clean_text(task.get("status")),
            "summary": _clean_text(task.get("waiting_reason") or task.get("next_expected_action")),
        }
        for task in interrupted_tasks
    )
    blocked_items.extend(
        {
            "task_id": _clean_text(loop.get("linked_task_id")),
            "title": _clean_text(loop.get("note") or loop.get("kind")),
            "status": _clean_text(loop.get("kind")) or "blocked_question",
            "summary": _clean_text(loop.get("note") or "Blocked question requires operator resolution."),
            "state_copy_key": _status_copy_from_status(_clean_text(loop.get("kind")) or "blocked_question")["copy_key"],
            "state_label": _status_copy_from_status(_clean_text(loop.get("kind")) or "blocked_question")["label"],
        }
        for loop in blocked_loops
    )
    next_actions = [
        {
            "task_id": _clean_text(task.get("task_id")),
            "title": _clean_text(task.get("title")),
            "owner": _clean_text(dict(task.get("next_decision") or {}).get("owner")),
            "action": _clean_text(
                dict(task.get("next_decision") or {}).get("summary")
                or dict(task.get("next_decision") or {}).get("kind")
                or task.get("next_expected_action")
            ),
        }
        for task in active_tasks
        if _clean_text(
            dict(task.get("next_decision") or {}).get("summary")
            or dict(task.get("next_decision") or {}).get("kind")
            or task.get("next_expected_action")
        )
    ]
    for loop in open_loops:
        next_actions.append(
            {
                "task_id": _clean_text(loop.get("linked_task_id")),
                "title": "",
                "owner": "operator" if _clean_text(loop.get("kind")).lower() in {"waiting_on_operator", "blocked_question"} else "",
                "action": _clean_text(loop.get("note") or loop.get("kind")),
            }
        )

    evidence_items: List[Dict[str, Any]] = []
    for task in active_tasks + completed_tasks:
        for evidence_ref in list(task.get("evidence_refs") or []):
            evidence_items.append(
                {
                    "task_id": _clean_text(task.get("task_id")),
                    "status": _clean_text(task.get("status")),
                    "evidence_ref": _clean_text(evidence_ref),
                }
            )
    for row in delegated_work:
        for evidence_ref in list(row.get("evidence_refs") or []):
            evidence_items.append(
                {
                    "task_id": _clean_text(row.get("task_id")),
                    "status": _clean_text(row.get("status")),
                    "evidence_ref": _clean_text(evidence_ref),
                }
            )
    for task in blocked_tasks:
        blocked_status = _clean_text(task.get("status")).lower()
        evidence_status = _clean_text(task.get("evidence_status")).lower()
        if blocked_status in {"evidence_missing", "ambiguous_artifact"} or evidence_status in {
            "missing_native_record",
            "missing_evidence",
            "missing",
        }:
            evidence_items.append(
                {
                    "task_id": _clean_text(task.get("task_id")),
                    "status": "missing_evidence",
                    "evidence_ref": "",
                }
            )

    status_language = _status_language(
        operator_state=operator_state,
        backend_projection_state=_clean_text(backend_projection.get("state")),
        stale_sources=stale_sources,
        truth_conflicts=truth_conflicts,
        active_tasks=active_tasks,
        blocked_tasks=blocked_tasks,
        completed_tasks=completed_tasks,
    )

    freshness_summary = (
        "fresh"
        if not stale_sources
        else f"stale:{','.join(sorted({_clean_text(source) for source in stale_sources if _clean_text(source)}))}"
    )
    notifications = _operator_notifications(
        repo_root=repo_root,
        now_utc=generated_utc,
        operator_state=operator_state,
        backend_projection=backend_projection,
        truth_conflicts=truth_conflicts,
        stale_sources=stale_sources,
        active_tasks=active_tasks,
        blocked_tasks=blocked_tasks,
        open_loops=open_loops,
    )
    mission_surface = _mission_surface_projection(mission_contract)

    return {
        "schema_version": "operator_summary_v1",
        "backend_projection_state": _clean_text(backend_projection.get("state")),
        "status_language": status_language,
        "freshness_summary": freshness_summary,
        "mission_surface": mission_surface,
        "notifications": notifications,
        "truth_conflicts": truth_conflicts,
        "sections": [
            {
                "section_id": "mission_contract",
                "priority": 0,
                "title": "Mission Contract",
                "count": len(list(mission_surface.get("success_model") or [])) + len(list(mission_surface.get("non_goals") or [])),
                "items": [
                    {
                        "kind": "mission_promise",
                        "headline": _clean_text(mission_surface.get("headline")),
                        "summary": _clean_text(mission_surface.get("summary")),
                        "status": _clean_text(mission_surface.get("status")),
                    },
                    *[
                        {
                            "kind": "success_model",
                            "summary": _clean_text(row),
                            "status": _clean_text(mission_surface.get("status")),
                        }
                        for row in list(mission_surface.get("success_model") or [])
                    ],
                    *[
                        {
                            "kind": "non_goal",
                            "summary": _clean_text(row),
                            "status": _clean_text(mission_surface.get("status")),
                        }
                        for row in list(mission_surface.get("non_goals") or [])
                    ],
                ],
            },
            {
                "section_id": "active_work",
                "priority": 1,
                "title": "Active Work",
                "count": len(active_items),
                "items": active_items,
            },
            {
                "section_id": "blocked_work",
                "priority": 2,
                "title": "Blocked Work",
                "count": len(blocked_items),
                "items": blocked_items,
            },
            {
                "section_id": "next_actions",
                "priority": 3,
                "title": "Next Actions",
                "count": len(next_actions),
                "items": next_actions,
            },
            {
                "section_id": "evidence",
                "priority": 4,
                "title": "Evidence",
                "count": len(evidence_items),
                "items": evidence_items,
            },
        ],
    }


def _operator_notifications(
    *,
    repo_root: Path,
    now_utc: str,
    operator_state: str,
    backend_projection: Dict[str, Any],
    truth_conflicts: List[Dict[str, Any]],
    stale_sources: List[str],
    active_tasks: List[Dict[str, Any]],
    blocked_tasks: List[Dict[str, Any]],
    open_loops: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    backend_state = _clean_text(backend_projection.get("state")).lower()
    state = _clean_text(operator_state).lower()
    has_failed_or_evidence_block = any(
        _clean_text(task.get("status")).lower() in {"failed", "evidence_missing", "ambiguous_artifact"}
        for task in blocked_tasks
    )
    has_blocked_question = any(_clean_text(loop.get("kind")).lower() == "blocked_question" for loop in open_loops)

    if backend_state == "unreachable":
        out.append(
            {
                "kind": "unreachable_server",
                "severity": "danger",
                "headline": "Server unreachable",
                "message": "Live execution truth is unavailable. Cached state is advisory until the server reconnects.",
                "completion_claim_allowed": False,
            }
        )
    elif backend_state == "stale" or stale_sources:
        out.append(
            {
                "kind": "stale_data",
                "severity": "warning",
                "headline": "Projection is stale",
                "message": "Freshness markers are out of date. Treat completion status as provisional until durable state refreshes.",
                "completion_claim_allowed": False,
            }
        )
    elif truth_conflicts:
        out.append(
            {
                "kind": "truth_conflict",
                "severity": "warning",
                "headline": "Durable sources disagree",
                "message": "Do not treat this as complete until conflicts are resolved in native durable records.",
                "completion_claim_allowed": False,
            }
        )
    elif state == "interrupted_recovering" or backend_state == "reconnecting":
        out.append(
            {
                "kind": "resumed_recovery",
                "severity": "warning",
                "headline": "Recovery in progress",
                "message": "Work resumed after interruption. Completion is unproven until recovery settles and evidence lands.",
                "completion_claim_allowed": False,
            }
        )

    if has_failed_or_evidence_block:
        out.append(
            {
                "kind": "blocked_or_failed_work",
                "severity": "danger",
                "headline": "Blocked or failed work needs action",
                "message": "One or more tasks are failed or evidence-blocked. No completion is claimed.",
                "completion_claim_allowed": False,
            }
        )
    elif has_blocked_question or state == "blocked":
        out.append(
            {
                "kind": "blocked_or_failed_work",
                "severity": "danger",
                "headline": "Blocked work needs action",
                "message": "One or more blocked questions still need resolution before progress can continue.",
                "completion_claim_allowed": False,
            }
        )
    elif state in {"waiting_on_job", "waiting_on_verification", "waiting_on_operator", "running", "scheduled", "waiting", "active", "retrying"} and active_tasks:
        out.append(
            {
                "kind": "long_running_reassurance",
                "severity": "info",
                "headline": "Work still in progress",
                "message": "Long-running work is still active or waiting on an external step. No completion is claimed yet.",
                "completion_claim_allowed": False,
            }
        )

    out.extend(_job_budget_notifications(repo_root=repo_root, now_utc=now_utc))

    return out


def _job_budget_notifications(*, repo_root: Path, now_utc: str) -> List[Dict[str, Any]]:
    try:
        jobs = _scheduled_jobs.list_jobs(repo_root=repo_root, now_utc=now_utc)
    except Exception:
        return []
    budget_rows: List[Dict[str, Any]] = []
    for row in jobs:
        budget = dict(row.get("budget_status") or {})
        if not bool(budget.get("present")):
            continue
        status = _clean_text(budget.get("status")).lower()
        attention_state = _clean_text(budget.get("attention_state")).lower()
        if status not in {"ledger_missing", "recovery_blocked", "review_required", "exhausted", "warning"}:
            continue
        budget_rows.append(
            {
                "job_id": _clean_text(row.get("job_id")),
                "status": status,
                "attention_state": attention_state or "warning",
                "summary": _clean_text(budget.get("operator_summary")),
            }
        )
    if not budget_rows:
        return []

    blocked = [row for row in budget_rows if row["attention_state"] == "danger"]
    warned = [row for row in budget_rows if row["attention_state"] != "danger"]
    out: List[Dict[str, Any]] = []
    if blocked:
        preview = ", ".join(row["job_id"] for row in blocked[:2])
        suffix = "" if len(blocked) <= 2 else f", and {len(blocked) - 2} more"
        out.append(
            {
                "kind": "job_budget_attention",
                "severity": "danger",
                "headline": "Scheduled jobs blocked by budget",
                "message": f"{len(blocked)} scheduled job(s) in the plane are blocked by durable budget state: {preview}{suffix}.",
                "completion_claim_allowed": False,
                "jobs": blocked,
            }
        )
    if warned:
        preview = ", ".join(row["job_id"] for row in warned[:2])
        suffix = "" if len(warned) <= 2 else f", and {len(warned) - 2} more"
        out.append(
            {
                "kind": "job_budget_attention",
                "severity": "warning",
                "headline": "Scheduled jobs need budget follow-up",
                "message": f"{len(warned)} scheduled job(s) in the plane have budget warnings: {preview}{suffix}.",
                "completion_claim_allowed": False,
                "jobs": warned,
            }
        )
    return out


def _resource_concurrency_notification(resource_concurrency: Dict[str, Any]) -> Dict[str, Any] | None:
    status = _clean_text(resource_concurrency.get("status")).lower()
    waiting_claim_count = int(resource_concurrency.get("waiting_claim_count") or 0)
    saturated_resource_count = int(resource_concurrency.get("saturated_resource_count") or 0)
    recovered_resource_count = int(resource_concurrency.get("recovered_resource_count") or 0)
    degraded_reasons = [str(row).strip() for row in list(resource_concurrency.get("degraded_reasons") or []) if str(row).strip()]
    highlights = [
        dict(row)
        for row in list(resource_concurrency.get("highlights") or [])
        if isinstance(row, dict)
    ]
    if status == "degraded":
        return {
            "kind": "shared_resource_projection_degraded",
            "severity": "warning",
            "headline": "Shared-resource projection degraded",
            "message": degraded_reasons[0] if degraded_reasons else "Durable resource occupancy could not be projected cleanly.",
            "completion_claim_allowed": False,
        }
    if waiting_claim_count > 0 or saturated_resource_count > 0:
        lead = highlights[0] if highlights else {}
        lead_message = _clean_text(lead.get("message"))
        lead_headline = _clean_text(lead.get("headline"))
        recovery_suffix = f" {recovered_resource_count} recent recovery interventions recorded." if recovered_resource_count > 0 else ""
        return {
            "kind": "shared_resource_contention",
            "severity": "warning",
            "headline": "Shared resources are contended",
            "message": (
                (
                    f"{lead_headline}: {lead_message}."
                    if lead_headline and lead_message
                    else f"{saturated_resource_count} saturated resources and {waiting_claim_count} waiting claims are currently projected from durable ledger truth."
                )
                +
                f"{recovery_suffix}"
            ).strip(),
            "completion_claim_allowed": False,
        }
    return None


def build_operator_snapshot(repo_root: Path, *, now_utc: Optional[str] = None) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    now = _parse_iso_utc(now_utc) or _parse_iso_utc(_utc_now_iso()) or _dt.datetime.now(_dt.timezone.utc)
    generated_utc = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    host_health: Dict[str, Any]
    host_error = ""
    try:
        host_health = _host.host_health_snapshot(repo_root, initialize_if_missing=False)
    except Exception as exc:
        host_health = {
            "schema_version": "lab_assistant_host_health_v1",
            "utc": "",
            "status": "",
            "mode": "host_down_unreachable",
            "blocked": True,
            "summary": f"Host health snapshot unavailable: {exc}",
            "reasons": [f"host_unreachable:{type(exc).__name__}"],
            "last_boot_id": "",
            "last_boot_utc": "",
            "last_shutdown_clean": False,
            "surfaces": [],
        }
        host_error = str(exc)

    try:
        state_snapshot = _state.read_assistant_state_snapshot(repo_root, initialize_if_missing=False)
    except Exception as exc:
        state_snapshot = {
            "schema_version": "lab_assistant_state_snapshot_v1",
            "utc": "",
            "ok": False,
            "error": str(exc),
            "assistant_state": None,
            "host_health": host_health,
            "precedence_samples": {},
        }
    assistant_state = dict(state_snapshot.get("assistant_state") or {})

    try:
        register_snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    except Exception as exc:
        integrity = getattr(_register, "task_register_integrity_snapshot", lambda _root: {})(repo_root)
        register_snapshot = {
            "schema_version": "lab_assistant_task_register_snapshot_v1",
            "utc": "",            "ok": False,
            "error": str(exc),
            "task_register": None,
            "summary_cache": None,
            "integrity": integrity,
            "assistant_state": assistant_state or None,
            "host_health": host_health,
        }
    task_register = dict(register_snapshot.get("task_register") or {})
    summary_cache = dict(register_snapshot.get("summary_cache") or {})
    register_integrity = dict(register_snapshot.get("integrity") or {})
    register_integrity_degraded = bool(register_integrity.get("degraded"))
    canonical_register_integrity = dict(register_integrity.get("canonical_register") or {})
    summary_cache_integrity = dict(register_integrity.get("summary_cache") or {})

    recovery_snapshot = _recovery.read_recovery_snapshot(repo_root)
    mission_contract = _plane_mission_contract.read_plane_mission_contract(repo_root=repo_root)
    raw_tasks = [
        dict(task)
        for bucket_name in ("active_tasks", "residue_tasks", "completed_tasks")
        for task in list(task_register.get(bucket_name) or [])
    ]
    hidden_task_ids = {
        _clean_text(task.get("task_id"))
        for task in raw_tasks
        if _register.is_internal_helper_task(dict(task)) and _clean_text(task.get("task_id"))
    }

    projected_tasks = [
        _task_projection(repo_root, dict(task), now=now)
        for task in raw_tasks
        if not _register.is_internal_helper_task(dict(task))
    ]
    projected_tasks = _dedupe_projected_tasks(projected_tasks)
    active_tasks = [
        task
        for task in projected_tasks
        if _clean_text(task.get("status")).lower() not in {"completed", "done"}
    ]
    completed_tasks = [
        task
        for task in projected_tasks
        if _clean_text(task.get("status")).lower() in {"completed", "done"}
    ]
    quiescent_completed_task_ids = _quiescent_completed_task_ids(completed_tasks)
    open_loops = [
        {
            "loop_id": _clean_text(loop.get("loop_id")),
            "kind": _clean_text(loop.get("kind")),
            "status": _clean_text(loop.get("status")),
            "linked_task_id": _clean_text(loop.get("linked_task_id")),
            "linked_ledger_uid": _clean_text(loop.get("linked_ledger_uid")),
            "note": _clean_text(loop.get("note")),
            "next_review_utc": _clean_text(loop.get("next_review_utc")),
            "updated_utc": _clean_text(loop.get("updated_utc")),
        }
        for loop in list(task_register.get("open_loops") or [])
        if _clean_text(loop.get("status")).lower() != "resolved"
        and _clean_text(loop.get("linked_task_id")) not in hidden_task_ids
        and not _is_operator_residue_loop(loop, quiescent_completed_task_ids=quiescent_completed_task_ids)
    ]
    recovery_snapshot = _operator_effective_recovery_snapshot(
        recovery_snapshot=recovery_snapshot,
        raw_tasks=raw_tasks,
        visible_tasks=projected_tasks,
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        open_loops=open_loops,
        generated_utc=generated_utc,
    )
    assistant_state = _operator_effective_assistant_state(
        assistant_state=assistant_state,
        raw_tasks=raw_tasks,
        visible_tasks=projected_tasks,
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        open_loops=open_loops,
    )
    assistant_state_name = _clean_text(assistant_state.get("current_state")).lower()
    recovery_status = _clean_text(recovery_snapshot.get("status")).lower()
    completed_residue_only = _completed_residue_only(
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        open_loops=open_loops,
    )
    quiescent_register = bool(task_register) and not active_tasks and not open_loops and (not completed_tasks or completed_residue_only)
    quiescent_idle_surfaces = (
        quiescent_register
        and assistant_state_name in {"idle", "assistant_idle"}
        and recovery_status == "ready"
    )

    host_marker = _freshness_marker(
        source="host_health",
        updated_utc=host_health.get("utc") or host_health.get("last_boot_utc"),
        now=now,
        max_age_s=_STALE_AFTER_S["host_health"],
        missing=bool(host_error),
        note="host health unavailable" if host_error else "",
    )
    task_register_projection = dict(register_snapshot.get("snapshot_projection") or {})
    task_register_projection_stale = bool(task_register_projection.get("stale"))

    state_marker = _freshness_marker(
        source="assistant_state",
        updated_utc=generated_utc if quiescent_idle_surfaces else assistant_state.get("last_written_utc"),
        now=now,
        max_age_s=_STALE_AFTER_S["assistant_state"],
        missing=not bool(assistant_state),
        note="quiescent_idle_assistant" if quiescent_idle_surfaces else "",
    )
    if register_integrity_degraded and str(canonical_register_integrity.get("status") or "") not in {"", "ok", "missing"}:
        register_note = "task_register_corrupt_or_partial"
    elif str(summary_cache_integrity.get("status") or "") not in {"", "ok", "missing"}:
        register_note = "summary_cache_corrupt_or_partial"
    elif bool(task_register.get("summary_stale")):
        register_note = "summary_cache_stale"
    elif task_register_projection_stale:
        register_note = "snapshot_projection_stale"
    elif quiescent_register:
        register_note = "quiescent_register"
    else:
        register_note = ""
    register_marker = _freshness_marker(
        source="task_register",
        updated_utc=(            generated_utc
            if quiescent_register and not bool(task_register.get("summary_stale"))
            else summary_cache.get("generated_utc") or task_register.get("last_written_utc")
        ),
        now=now,
        max_age_s=_STALE_AFTER_S["task_register"],
        missing=not bool(task_register),
        force_stale=bool(task_register.get("summary_stale")) or task_register_projection_stale or register_integrity_degraded,
        note=register_note,
    )
    recovery_marker = _freshness_marker(
        source="recovery",        updated_utc=generated_utc if quiescent_idle_surfaces else recovery_snapshot.get("reconciled_utc"),
        now=now,
        max_age_s=_STALE_AFTER_S["recovery"],
        missing=not quiescent_idle_surfaces and not bool(_clean_text(recovery_snapshot.get("reconciled_utc"))),
        note="quiescent_idle_recovery" if quiescent_idle_surfaces else _clean_text(recovery_snapshot.get("status")) or "",
    )

    overall_state = _overall_state(
        host_error=host_error,
        host_health=host_health,
        assistant_state=assistant_state,
        recovery_snapshot=recovery_snapshot,
        active_tasks=active_tasks,
        open_loops=open_loops,
    )
    stale_sources = [row["source"] for row in (host_marker, state_marker, register_marker, recovery_marker) if bool(row.get("stale"))]

    delegated_work: List[Dict[str, Any]] = []
    for task in active_tasks + completed_tasks:
        delegated_outcome_writeback = _canonical_writeback_projection(task.get("delegated_outcome_writeback") or {})
        for summary in list(task.get("delegation_summaries") or []):
            delegated_work.append(
                {
                    "task_id": str(task.get("task_id") or ""),
                    "task_title": str(task.get("title") or ""),
                    "delegation_request_id": _clean_text(summary.get("delegation_request_id")),
                    "surface": _clean_text(summary.get("surface")),
                    "subsystem_id": _clean_text(summary.get("subsystem_id")),
                    "status": _clean_text(summary.get("status")),
                    "native_status": _clean_text(summary.get("native_status")),
                    "evidence_status": _clean_text(summary.get("evidence_status")),
                    "evidence_refs": list(summary.get("evidence_refs") or []),
                    "summary": _clean_text(summary.get("summary")),
                    "delegated_outcome_writeback": delegated_outcome_writeback,
                }
            )
    truth_conflicts = _truth_conflicts(
        host_error=host_error,
        host_health=host_health,
        recovery_snapshot=recovery_snapshot,
        stale_sources=stale_sources,
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        open_loops=open_loops,
    )
    if str(canonical_register_integrity.get("status") or "") not in {"", "ok", "missing"}:
        truth_conflicts.append(
            {
                "kind": "task_register_corruption",
                "field": "task_register_integrity",
                "task_id": "",
                "canonical_value": str(canonical_register_integrity.get("status") or ""),
                "projected_value": "degraded_projection",
                "summary": "Canonical task-register state is malformed or partially written; operator projection is degraded and completion is unproven.",
            }
        )
    elif str(summary_cache_integrity.get("status") or "") not in {"", "ok", "missing"}:
        truth_conflicts.append(
            {
                "kind": "task_register_summary_cache_corruption",
                "field": "task_register_integrity",
                "task_id": "",
                "canonical_value": str(summary_cache_integrity.get("status") or ""),
                "projected_value": "rebuilt_summary_projection",
                "summary": "Summary cache is malformed or partially written; projection was rebuilt from canonical task-register truth and remains degraded until the cache is rewritten.",
            }
        )
    backend_projection = _backend_projection_truth(
        host_error=host_error,
        host_health=host_health,
        recovery_snapshot=recovery_snapshot,
        stale_sources=stale_sources,
        truth_conflicts=truth_conflicts,
    )
    self_check = _self_check.refresh_default_checks_if_needed(repo_root)
    execution_surface = _execution_surface_projection(
        task_register=task_register,
        now_utc=now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    runtime_resource_policy = _scheduled_jobs.current_runtime_resource_policy(repo_root=repo_root)
    resource_concurrency = _resource_claim_ledger.resource_concurrency_snapshot(
        repo_root=repo_root,
        runtime_policy=runtime_resource_policy,
    )
    status_contract = _snapshot_status_contract(
        operator_state=overall_state,
        assistant_current_state=_clean_text(assistant_state.get("current_state")),
        backend_projection_state=_clean_text(backend_projection.get("state")),
        recovery_snapshot_status=_clean_text(recovery_snapshot.get("status")),
        stale_sources=stale_sources,
        truth_conflicts=truth_conflicts,
        task_register_present=bool(task_register),
        task_status_contracts=[dict(task.get("status_contract") or {}) for task in projected_tasks],
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        execution_surface=execution_surface,
        evaluated_utc=now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    projection_contract = _snapshot_projection_contract(
        generated_utc=generated_utc,
        backend_projection=backend_projection,
        status_contract=status_contract,
        task_register_projection=task_register_projection,
    )
    operator_summary = _build_operator_summary(
        repo_root=repo_root,
        generated_utc=generated_utc,
        operator_state=overall_state,
        backend_projection=backend_projection,
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
        open_loops=open_loops,
        delegated_work=delegated_work,
        stale_sources=stale_sources,
        truth_conflicts=truth_conflicts,
        mission_contract=mission_contract,
    )
    continuation_inspection = [
        _continuation_attachment.inspect_continuation_state_for_binding(
            {"type": "task_id", "id": _clean_text(task.get("task_id"))},
            task_register=task_register,
            repo_root=repo_root,
            as_of_utc=generated_utc,
        )
        for task in active_tasks + completed_tasks
        if _clean_text(task.get("task_id"))
    ]

    # C1J2: apply verification policy claim gate (fail-closed by default).
    _temp_for_policy = {
        "freshness": {"stale": bool(stale_sources)},
        "operator_summary": {"truth_conflicts": truth_conflicts},
        "delegation_proof": _execution_proof.read_latest_report(
            repo_root, now_utc=now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        ),
        "active_tasks": active_tasks,
        "completed_tasks": completed_tasks,
    }
    _gate = _verify_policy.evaluate_claim_gate(_temp_for_policy)
    # Tighten success claim allowance with the policy outcome.
    try:
        current = bool(dict(operator_summary.get("status_language") or {}).get("success_claim_allowed"))
        operator_summary["status_language"]["success_claim_allowed"] = bool(current and bool(_gate.get("allowed")))
    except Exception:
        pass
    resource_notification = _resource_concurrency_notification(resource_concurrency)
    if resource_notification is not None:
        operator_summary["notifications"] = list(operator_summary.get("notifications") or []) + [resource_notification]

    if (
        str(self_check.get("mode") or "") in {"self_check_blocked", "self_check_degraded"}
        or bool(self_check.get("failed_checks"))
        or bool(self_check.get("stale_checks"))
    ):
        self_check_notification = {
            "kind": "self_check_degraded",
            "severity": "danger" if str(self_check.get("status") or "") == "blocked" else "warning",
            "headline": "Self-check blocked" if str(self_check.get("status") or "") == "blocked" else "Self-check degraded",
            "message": _clean_text(self_check.get("summary")) or "Continuous self-check is degraded, so operational readiness is not fully proven.",
            "completion_claim_allowed": False,
        }
        operator_summary["notifications"] = [self_check_notification] + list(operator_summary.get("notifications") or [])
        status_language = dict(operator_summary.get("status_language") or {})
        if str(status_language.get("state") or "") in {"idle", "successful"}:
            blocked = str(self_check.get("status") or "") == "blocked"
            status_language["state"] = "blocked" if blocked else "degraded"
            status_language["headline"] = "Self-check blocked; readiness is not proven" if blocked else "Self-check degraded; readiness is not proven"
            status_language["tone"] = "danger" if blocked else "warning"
            status_language["success_claim_allowed"] = False
            status_language["copy_key"] = "self_check_blocked" if blocked else "self_check_degraded"
            status_language["label"] = "Self-check blocked" if blocked else "Self-check degraded"
            status_language["detail"] = _clean_text(self_check.get("summary")) or "Operational readiness is not fully proven."
            operator_summary["status_language"] = status_language

    freshness_seconds = max((int(row.get("age_s") or 0) for row in (host_marker, state_marker, register_marker, recovery_marker)), default=0)
    if stale_sources:
        freshness_confidence = "STALE"
    elif _clean_text(backend_projection.get("state")).lower() in {"degraded", "reconnecting", "unreachable"} or truth_conflicts:
        freshness_confidence = "DEGRADED"
    else:
        freshness_confidence = "HIGH"

    command_center = _command_center_projection(
        generated_utc=generated_utc,
        freshness_seconds=freshness_seconds,
        confidence=freshness_confidence,
        headline=_clean_text(dict(operator_summary.get("status_language") or {}).get("headline")),
        top_notification=(list(operator_summary.get("notifications") or []) or [None])[0],
        active_tasks=active_tasks,
        completed_tasks=completed_tasks,
    )
    work_items = [_work_item_projection(task) for task in active_tasks + completed_tasks]

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_utc": generated_utc,
        "utc": generated_utc,
        "operator_state": overall_state,
        "summary": _clean_text(recovery_snapshot.get("summary") or assistant_state.get("state_reason") or host_health.get("summary")),
        "assistant_identity": {
            "assistant_id": _clean_text(assistant_state.get("assistant_id")),
            "assistant_label": _clean_text(assistant_state.get("assistant_label")),
        },
        "assistant_state": assistant_state,
        "host_health": host_health,
        "self_check": self_check,
        "task_register": {
            "register_id": _clean_text(task_register.get("register_id")),
            "write_generation": int(task_register.get("write_generation") or 0),
            "summary_stale": bool(task_register.get("summary_stale")),
            "integrity": register_integrity,
            "snapshot_projection": task_register_projection,
        },
        "active_tasks": active_tasks,        "open_loops": open_loops,
        "delegated_work": delegated_work,
        "completed_tasks": completed_tasks,
        "recovery": {
            "status": _clean_text(recovery_snapshot.get("status")),
            "summary": _clean_text(recovery_snapshot.get("summary")),
            "reconciled_utc": _clean_text(recovery_snapshot.get("reconciled_utc")),
            "continuity_proven": bool(recovery_snapshot.get("continuity_proven")),
            "degraded_reasons": list(recovery_snapshot.get("degraded_reasons") or []),
            "surface_gaps": list(recovery_snapshot.get("surface_gaps") or []),
            "task_outcomes": list(recovery_snapshot.get("task_outcomes") or []),
            "recovery_note_path": _clean_text(recovery_snapshot.get("recovery_note_path")),
            "idempotency_journal_path": _clean_text(recovery_snapshot.get("idempotency_journal_path")),
        },
        "delegation_proof": _temp_for_policy["delegation_proof"],
        "execution_surface": execution_surface,
        "status_contract": status_contract,
        "projection_contract": projection_contract,
        "execution_truth_ownership": _truth_ownership.ownership_snapshot(),
        "backend_projection": backend_projection,
        "resource_concurrency": resource_concurrency,
        "mission_contract": mission_contract,
        "mission_surface": _mission_surface_projection(mission_contract),
        "continuation_inspection": continuation_inspection,
        "freshness": {
            "generated_at": generated_utc,
            "freshness_seconds": int(freshness_seconds),
            "confidence": freshness_confidence,
            "stale": bool(stale_sources),
            "stale_sources": stale_sources,
            "markers": [host_marker, state_marker, register_marker, recovery_marker],
        },
        "command_center": command_center,
        "work_items": work_items,
        "diagnostic_boundary": _status_diag.diagnostic_boundary_snapshot(
            now_utc=generated_utc
        ),
        # Expose the explicit claim gate for operator/verifier surfaces.
        "verification_claim_gate": _gate,
        "operator_summary": operator_summary,
    }
