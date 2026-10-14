"""Shared scheduler projection contract helpers for operator-facing surfaces in the plane."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple


CONTRACT_ARTIFACT_REL = Path("governance/scheduler_projection_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_scheduler_projection_v1.md")
SCHEMA_VERSION = "scheduler_projection_contract_v1"
UID = "PIS-20260408-181100Z-D1E1"

STATUS_VOCABULARY: Tuple[str, ...] = (
    "active",
    "blocked",
    "queued",
    "idle",
    "completed_once",
    "failed",
    "ambiguous",
)
CONSUMER_SURFACES: Tuple[str, ...] = (
    "watch",
    "api_list",
    "api_detail",
    "next_job_summary",
)
NEXT_CANDIDATE_BUCKETS: Tuple[str, ...] = (
    "runnable_now",
    "scheduled_future",
    "none",
)
FAILED_CANONICAL_STATES = frozenset(
    {
        "failed",
        "failed_verification",
        "proof_gap",
        "skipped",
        "abandoned",
        "cancelled",
        "interrupted",
        "error",
    }
)
BLOCKED_CANONICAL_STATES = frozenset({"blocked", "deferred"})
ACTIVE_CANONICAL_STATES = frozenset(
    {
        "queued",
        "launching",
        "running",
        "running_iteration",
        "awaiting_verifier",
        "awaiting_single_repair_pass",
        "launching_children",
        "waiting_on_children",
        "uncertain_interrupted",
    }
)
RUNNABLE_DUE_REASONS = frozenset({"run_now_requested", "due", "due_failed_slot_reusable"})
FUTURE_DUE_REASONS = frozenset({"not_due_yet", "retry_backoff"})


@dataclass(frozen=True)
class SchedulerProjectionContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    statuses: Tuple[str, ...]
    consumer_surfaces: Tuple[str, ...]
    next_candidate_buckets: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_scheduler_projection_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def scheduler_projection_contract_v1() -> SchedulerProjectionContractV1:
    return SchedulerProjectionContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        statuses=STATUS_VOCABULARY,
        consumer_surfaces=CONSUMER_SURFACES,
        next_candidate_buckets=NEXT_CANDIDATE_BUCKETS,
    )


def _provenance_entry(source_path: str, value: Any, *, strength: str, reason_code: str) -> Dict[str, Any]:
    return {
        "source_path": str(source_path),
        "value": value,
        "strength": str(strength),
        "reason_code": str(reason_code),
    }


def project_scheduler_job_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), Mapping) else {}
    due = row.get("due") if isinstance(row.get("due"), Mapping) else {}
    lifecycle = row.get("lifecycle_projection") if isinstance(row.get("lifecycle_projection"), Mapping) else {}
    schedule = row.get("schedule") if isinstance(row.get("schedule"), Mapping) else {}

    job_id = str(row.get("job_id") or "").strip()
    display_title = str(row.get("display_title") or job_id or "scheduled job").strip()
    enabled = bool(row.get("enabled"))
    active_run_id = str(state.get("active_run_id") or "").strip()
    last_status = str(state.get("last_status") or "").strip()
    last_nonlaunch_reason = str(state.get("last_nonlaunch_reason") or "").strip()
    last_reconciliation_reason = str(state.get("last_reconciliation_reason") or "").strip()
    run_now_requested_utc = str(state.get("run_now_requested_utc") or "").strip()
    due_reason = str(due.get("reason") or "").strip()
    due_eligible = bool(due.get("eligible"))
    due_utc = str(due.get("due_utc") or "").strip()
    schedule_mode = str(schedule.get("mode") or "").strip().lower()
    run_at = str(schedule.get("run_at") or "").strip()
    lifecycle_recognized = bool(lifecycle.get("recognized"))
    canonical_state = str(lifecycle.get("canonical_state") or "").strip()

    provenance = []
    if active_run_id:
        provenance.append(
            _provenance_entry("state.active_run_id", active_run_id, strength="durable_runtime_claim", reason_code="active_run")
        )
    if run_now_requested_utc:
        provenance.append(
            _provenance_entry(
                "state.run_now_requested_utc",
                run_now_requested_utc,
                strength="durable_operator_intent",
                reason_code="run_now_requested",
            )
        )
    if last_status:
        provenance.append(
            _provenance_entry("state.last_status", last_status, strength="persisted_scheduler_state", reason_code="last_status")
        )
    if lifecycle_recognized or canonical_state:
        provenance.append(
            _provenance_entry(
                "lifecycle_projection.canonical_state",
                canonical_state,
                strength="derived_from_durable_scheduler_truth",
                reason_code="lifecycle_projection",
            )
        )
    if due_reason:
        provenance.append(
            _provenance_entry("due.reason", due_reason, strength="derived_scheduler_eligibility", reason_code="due_reason")
        )
    if last_nonlaunch_reason:
        provenance.append(
            _provenance_entry(
                "state.last_nonlaunch_reason",
                last_nonlaunch_reason,
                strength="persisted_nonlaunch_marker",
                reason_code="nonlaunch_reason",
            )
        )

    ambiguity_reasons = []
    if active_run_id and canonical_state and canonical_state not in ACTIVE_CANONICAL_STATES:
        ambiguity_reasons.append("active_run_conflicts_with_terminal_lifecycle")
    if last_status.lower() == "running" and not active_run_id:
        ambiguity_reasons.append("running_status_without_active_run")
    if bool(state.get("active_claim_present")) and not active_run_id:
        ambiguity_reasons.append("active_claim_without_active_run")
    if schedule_mode == "once" and canonical_state == "succeeded" and due_eligible and due_reason != "run_now_requested":
        ambiguity_reasons.append("completed_once_conflicts_with_due_eligibility")
    if last_status and not lifecycle_recognized and canonical_state:
        ambiguity_reasons.append("unrecognized_lifecycle_projection")

    if ambiguity_reasons:
        projected_status = "ambiguous"
        projection_reason = ambiguity_reasons[0]
    elif active_run_id or due_reason == "active_run" or canonical_state in ACTIVE_CANONICAL_STATES:
        projected_status = "active"
        projection_reason = "active_run_present"
    elif (
        due_reason.startswith("dependency_")
        or due_reason == "terminal_no_auto_retry"
        or canonical_state in BLOCKED_CANONICAL_STATES
        or last_status.lower() == "blocked"
        or bool(last_nonlaunch_reason)
    ):
        projected_status = "blocked"
        projection_reason = (
            due_reason
            if due_reason.startswith("dependency_") or due_reason == "terminal_no_auto_retry"
            else last_nonlaunch_reason or canonical_state or due_reason or last_status or "blocked"
        )
    elif due_eligible and due_reason in RUNNABLE_DUE_REASONS:
        projected_status = "queued"
        projection_reason = due_reason
    elif schedule_mode == "once" and (
        due_reason == "already_executed"
        or last_reconciliation_reason == "already_executed"
        or canonical_state == "succeeded"
    ):
        projected_status = "completed_once"
        projection_reason = due_reason or last_reconciliation_reason or "completed_once"
    elif canonical_state in FAILED_CANONICAL_STATES:
        projected_status = "failed"
        projection_reason = canonical_state
    else:
        projected_status = "idle"
        projection_reason = due_reason or ("disabled" if not enabled else "awaiting_schedule")

    next_candidate_bucket = "none"
    if projected_status == "queued":
        next_candidate_bucket = "runnable_now"
    elif projected_status == "idle" and enabled and (due_reason in FUTURE_DUE_REASONS or (schedule_mode == "once" and run_at)):
        next_candidate_bucket = "scheduled_future"

    next_due_utc = due_utc or run_at
    return {
        "schema_version": "scheduler_projection_row_v1",
        "job_id": job_id,
        "display_title": display_title,
        "enabled": enabled,
        "schedule_mode": schedule_mode,
        "run_at": run_at,
        "projected_status": projected_status,
        "projection_reason": projection_reason,
        "canonical_state": canonical_state,
        "due_reason": due_reason,
        "due_eligible": due_eligible,
        "next_candidate_bucket": next_candidate_bucket,
        "next_due_utc": next_due_utc,
        "durable_conflict": bool(ambiguity_reasons),
        "ambiguity_reasons": list(ambiguity_reasons),
        "provenance": provenance,
        "source_row": dict(row),
    }


def project_scheduler_rows(rows: Sequence[Mapping[str, Any]]) -> Tuple[Dict[str, Any], ...]:
    return tuple(project_scheduler_job_row(row) for row in rows)


def _next_sort_key(projection: Mapping[str, Any]) -> tuple[Any, ...]:
    due_reason = str(projection.get("due_reason") or "").strip()
    if due_reason == "run_now_requested":
        priority = 0
    elif due_reason == "due":
        priority = 1
    elif due_reason == "due_failed_slot_reusable":
        priority = 2
    elif due_reason == "retry_backoff":
        priority = 3
    else:
        priority = 4
    return (
        priority,
        str(projection.get("next_due_utc") or "9999-12-31T23:59:59Z"),
        str(projection.get("job_id") or ""),
    )


def select_next_scheduler_job(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any] | None:
    projections = project_scheduler_rows(rows)
    runnable = [row for row in projections if str(row.get("next_candidate_bucket") or "") == "runnable_now"]
    if runnable:
        return min(runnable, key=_next_sort_key)
    future = [row for row in projections if str(row.get("next_candidate_bucket") or "") == "scheduled_future"]
    if future:
        return min(future, key=_next_sort_key)
    return None


def validate_scheduler_projection_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_scheduler_projection_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain scheduler_projection_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260408-181100Z-D1E1")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion scheduler projection docs contract")

    statuses = payload.get("status_vocabulary")
    if not isinstance(statuses, dict):
        issues.append("status_vocabulary must be a mapping")
    elif tuple(statuses.keys()) != STATUS_VOCABULARY:
        issues.append("status_vocabulary must list the canonical scheduler projection states in order")

    surfaces = payload.get("consumer_surfaces")
    if tuple(surfaces or ()) != CONSUMER_SURFACES:
        issues.append("consumer_surfaces must cover watch, api_list, api_detail, and next_job_summary in order")

    buckets = payload.get("next_candidate_buckets")
    if tuple(buckets or ()) != NEXT_CANDIDATE_BUCKETS:
        issues.append("next_candidate_buckets must remain runnable_now, scheduled_future, none")

    provenance = payload.get("provenance_requirements")
    if not isinstance(provenance, dict):
        issues.append("provenance_requirements must be a mapping")
    else:
        required_fields = tuple(provenance.get("required_fields") or ())
        if required_fields != ("source_path", "value", "strength", "reason_code"):
            issues.append("provenance required_fields must remain source_path, value, strength, reason_code")

    selection = payload.get("next_selection_policy")
    if not isinstance(selection, dict):
        issues.append("next_selection_policy must be a mapping")
    else:
        runnable_order = tuple(selection.get("runnable_priority_order") or ())
        if runnable_order != ("run_now_requested", "due", "due_failed_slot_reusable"):
            issues.append("next_selection_policy runnable_priority_order must prefer run_now_requested before due")
        excluded_statuses = tuple(selection.get("excluded_statuses") or ())
        if excluded_statuses != ("active", "blocked", "completed_once", "failed", "ambiguous"):
            issues.append("next_selection_policy excluded_statuses must fail closed on non-runnable states")

    return tuple(issues)
