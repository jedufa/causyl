"""Shared operator-snapshot copy helpers.

This module holds the purely textual/status-copy seams used by the operator
snapshot. It keeps copy policy separate from the larger projection assembly so
future stabilization edits do not require touching the whole snapshot builder.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def status_copy_from_status(raw_status: Any, *, operator_state: str = "") -> Dict[str, str]:
    status = _clean_text(raw_status).lower()
    facing = _clean_text(operator_state).upper()
    if status in {"host_down_unreachable", "unreachable"}:
        return {
            "copy_key": "host_down",
            "label": "Host down",
            "detail": "Live execution truth is unavailable until the backend reconnects.",
        }
    if status in {"waiting_on_operator", "needs_input"}:
        return {
            "copy_key": "waiting_on_operator",
            "label": "Waiting on operator",
            "detail": "Progress is paused until the operator provides the next required input.",
        }
    if status == "waiting_on_verification":
        return {
            "copy_key": "waiting_on_verification",
            "label": "Waiting on verification",
            "detail": "Work is paused until verification evidence is reviewed or produced.",
        }
    if status in {"waiting_on_job", "scheduled", "waiting"}:
        return {
            "copy_key": "waiting_on_background_work",
            "label": "Waiting on background work",
            "detail": "A background workflow or queued run is still in progress or not yet started.",
        }
    if status in {"running", "active"} or facing == "RUNNING":
        return {
            "copy_key": "running_background_work",
            "label": "Running in background",
            "detail": "Execution is actively running on a background surface.",
        }
    if status == "retrying" or facing == "RETRYING":
        return {
            "copy_key": "retrying",
            "label": "Retrying",
            "detail": "The system is attempting a bounded retry instead of claiming completion.",
        }
    if status == "blocked_question":
        return {
            "copy_key": "blocked_question",
            "label": "Blocked question",
            "detail": "A blocking question still needs a concrete answer before work can continue.",
        }
    if status == "evidence_missing":
        return {
            "copy_key": "evidence_missing",
            "label": "Evidence missing",
            "detail": "Completion cannot be claimed because the required evidence is missing.",
        }
    if status == "ambiguous_artifact":
        return {
            "copy_key": "artifact_ambiguous",
            "label": "Artifact ambiguous",
            "detail": "Available artifacts are ambiguous, so the result cannot be trusted yet.",
        }
    if status in {"interrupted", "interrupted_recovering"}:
        return {
            "copy_key": "interrupted_recovering",
            "label": "Interrupted and recovering",
            "detail": "Previous work was interrupted and continuity is still being reconciled.",
        }
    if status in {"stalled", "stale"} or facing == "STALLED":
        return {
            "copy_key": "stalled_or_stale",
            "label": "Stalled or stale",
            "detail": "Freshness or progress is stale enough that calm success language would be dishonest.",
        }
    if status in {"degraded", "partial_restore_degraded"} or facing == "DEGRADED":
        return {
            "copy_key": "degraded_unknown",
            "label": "Degraded",
            "detail": "A degraded runtime condition is present, so broader completion claims are unsafe.",
        }
    if status == "failed" or facing == "FAILED":
        return {
            "copy_key": "failed",
            "label": "Failed",
            "detail": "The latest run failed and needs corrective action before continuing.",
        }
    if status == "blocked" or facing == "BLOCKED":
        return {
            "copy_key": "blocked",
            "label": "Blocked",
            "detail": "Progress is blocked and needs intervention before work can continue.",
        }
    if status in {"completed", "done"} or facing == "COMPLETED":
        return {
            "copy_key": "completed",
            "label": "Completed",
            "detail": "The task is completed according to the current durable record.",
        }
    return {
        "copy_key": "unknown",
        "label": "Unknown",
        "detail": "State is mixed or insufficiently specific for a stronger operator-facing claim.",
    }


def overall_status_copy(
    *,
    operator_state: str,
    backend_projection_state: str,
    stale_sources: List[str],
    truth_conflicts: List[Dict[str, Any]],
    blocked_tasks: List[Dict[str, Any]],
    active_tasks: List[Dict[str, Any]],
    completed_tasks: List[Dict[str, Any]],
) -> Dict[str, str]:
    state = _clean_text(operator_state).lower()
    backend_state = _clean_text(backend_projection_state).lower()
    if backend_state == "unreachable":
        return status_copy_from_status("host_down_unreachable")
    if truth_conflicts:
        return {
            "copy_key": "truth_conflict",
            "label": "Truth conflict",
            "detail": "Durable sources disagree, so completion claims are unsafe until the conflict is resolved.",
        }
    if backend_state == "degraded":
        return {
            "copy_key": "degraded_runtime",
            "label": "Degraded runtime",
            "detail": "The backend is reachable but degraded enough that completion claims would be too optimistic.",
        }
    if backend_state == "stale" or stale_sources:
        return {
            "copy_key": "stale_projection",
            "label": "Snapshot stale",
            "detail": "Freshness markers are stale, so the current snapshot is advisory rather than fully current.",
        }
    if state == "blocked" or blocked_tasks:
        return status_copy_from_status("blocked")
    if backend_state == "reconnecting":
        return {
            "copy_key": "reconnecting",
            "label": "Recovering connection",
            "detail": "The backend is reconnecting or recovering, so continuity is not settled yet.",
        }
    if state == "interrupted_recovering":
        return status_copy_from_status("interrupted_recovering")
    if state == "waiting_on_operator":
        return status_copy_from_status("waiting_on_operator")
    if state == "waiting_on_verification":
        return status_copy_from_status("waiting_on_verification")
    if state == "retrying":
        return status_copy_from_status("retrying")
    if state in {"waiting_on_job", "running", "scheduled", "waiting", "active"}:
        if state in {"running", "active"}:
            return status_copy_from_status("running")
        return status_copy_from_status("waiting_on_job")
    if state == "idle" and not active_tasks:
        all_completed_have_evidence = all(bool(task.get("evidence_backed_completion")) for task in completed_tasks)
        if completed_tasks and all_completed_have_evidence:
            return {
                "copy_key": "completed_with_evidence",
                "label": "Completed with evidence",
                "detail": "Completed work is backed by durable evidence, so success language is allowed.",
            }
        return {
            "copy_key": "idle",
            "label": "Idle",
            "detail": "No active work is currently projected in the durable snapshot.",
        }
    return status_copy_from_status(state)
