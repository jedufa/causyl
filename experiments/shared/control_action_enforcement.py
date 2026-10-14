"""Shared control-action eligibility and fail-closed enforcement helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .experiment_contract import _normalize_text
from .experiment_contract import _repo_root
from .experiment_control_policy import resolve_experiment_control_policy
from .experiment_records import read_run_record
from .experiment_records import summarize_run_record

CONTROL_ACTION_ENFORCEMENT_SCHEMA_VERSION = "control_action_enforcement_result_v1"
REQUEST_STATE_REQUESTED = "requested"
DECISION_STATE_ACCEPTED = "accepted"
DECISION_STATE_DENIED = "denied"
REALIZATION_STATE_PENDING = "pending"
REALIZATION_STATE_NOT_APPLICABLE = "not_applicable"

CANONICAL_CONTROL_ACTIONS = (
    "graceful_stop",
    "force_stop",
    "pause",
    "resume",
    "restart",
    "checkpoint",
    "branch_from_checkpoint",
)
NONTERMINAL_LIFECYCLE_STATES = frozenset({"launching", "running", "uncertain_interrupted"})
TERMINAL_LIFECYCLE_STATES = frozenset({"succeeded", "failed", "skipped", "cancelled"})


class ControlActionDeniedError(ValueError):
    """Raised when a control action is denied by policy or lifecycle truth."""

    def __init__(self, result: Mapping[str, Any]) -> None:
        self.result = dict(result)
        action = _normalize_text(result.get("action")) or "<unknown>"
        reason_code = _normalize_text(result.get("reason_code")) or "control_action_denied"
        reason_detail = _normalize_text(result.get("reason_detail"))
        message = f"{action} denied: {reason_code}"
        if reason_detail:
            message = f"{message} ({reason_detail})"
        super().__init__(message)


def _base_result(*, action: str, actor: str, requested_at_utc: str) -> Dict[str, Any]:
    return {
        "schema_version": CONTROL_ACTION_ENFORCEMENT_SCHEMA_VERSION,
        "ok": False,
        "action": action,
        "actor": actor,
        "requested_at_utc": requested_at_utc,
        "request_state": REQUEST_STATE_REQUESTED,
        "decision_state": DECISION_STATE_DENIED,
        "realization_state": REALIZATION_STATE_NOT_APPLICABLE,
        "reason_code": "",
        "reason_detail": "",
        "identity": {
            "project_id": "",
            "resolution_source": "",
            "run_id": "",
            "job_id": "",
        },
        "policy": {
            "effective_state": "",
            "rollout_state": "",
            "policy_source": "",
        },
        "lifecycle": {
            "state": "",
            "confidence": "",
            "terminal": False,
            "transition_reason": "",
            "latest_checkpoint_id": "",
            "resume_status": "",
            "checkpoint_count": 0,
        },
        "executor_enforcement_required": False,
    }


def _deny(result: Dict[str, Any], *, reason_code: str, reason_detail: str = "") -> Dict[str, Any]:
    result["ok"] = False
    result["decision_state"] = DECISION_STATE_DENIED
    result["realization_state"] = REALIZATION_STATE_NOT_APPLICABLE
    result["reason_code"] = str(reason_code or "").strip()
    result["reason_detail"] = str(reason_detail or "").strip()
    result["executor_enforcement_required"] = False
    return result


def _accept(result: Dict[str, Any], *, reason_code: str = "accepted_pending_execution", reason_detail: str = "") -> Dict[str, Any]:
    result["ok"] = True
    result["decision_state"] = DECISION_STATE_ACCEPTED
    result["realization_state"] = REALIZATION_STATE_PENDING
    result["reason_code"] = str(reason_code or "accepted_pending_execution").strip()
    result["reason_detail"] = str(reason_detail or "").strip()
    result["executor_enforcement_required"] = True
    return result


def _read_run_record_from_source(source: Any, *, repo_root: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    if isinstance(source, Mapping):
        run_record = source.get("run_record")
        if isinstance(run_record, Mapping):
            return dict(run_record), "run_record"
        run_record_path = _normalize_text(source.get("run_record_path"))
        if run_record_path:
            path = Path(run_record_path).expanduser()
            if not path.is_absolute():
                path = (repo_root / path).resolve()
            return read_run_record(path), "run_record"
    if isinstance(source, (str, Path)):
        text = _normalize_text(source)
        if text:
            path = Path(text).expanduser()
            if not path.is_absolute():
                path = (repo_root / path).resolve()
            if path.exists():
                target = path if path.name == "run_record.json" else path / "run_record.json"
                return read_run_record(target), "run_record"
    return None, ""


def _checkpoint_available(run_record: Mapping[str, Any]) -> bool:
    checkpoint = run_record.get("checkpoint") if isinstance(run_record.get("checkpoint"), Mapping) else {}
    return bool(
        _normalize_text(checkpoint.get("latest_checkpoint_id"))
        or _normalize_text(checkpoint.get("resume_from_checkpoint_id"))
        or int(checkpoint.get("checkpoint_count") or 0) > 0
    )


def _restart_anchor_available(run_record: Mapping[str, Any]) -> bool:
    launch = run_record.get("launch") if isinstance(run_record.get("launch"), Mapping) else {}
    return bool(_normalize_text(run_record.get("job_spec_hash")) and _normalize_text(launch.get("entry_module")))


def _lifecycle_precondition(action: str, run_record: Mapping[str, Any]) -> Tuple[bool, str, str]:
    summary = summarize_run_record(run_record)
    lifecycle_state = _normalize_text(summary.get("state")).lower()

    if lifecycle_state in TERMINAL_LIFECYCLE_STATES and action in {"graceful_stop", "force_stop", "pause", "checkpoint"}:
        return False, "run_already_terminal", f"run is already terminal: {lifecycle_state}"

    if action in {"graceful_stop", "force_stop"}:
        if lifecycle_state in NONTERMINAL_LIFECYCLE_STATES:
            return True, "", ""
        return False, "lifecycle_state_ineligible", f"{action} is only legal for nonterminal runs"

    if action == "pause":
        if lifecycle_state == "running":
            return True, "", ""
        return False, "lifecycle_state_ineligible", "pause requires lifecycle.state=running"

    if action == "checkpoint":
        if lifecycle_state == "running":
            return True, "", ""
        return False, "lifecycle_state_ineligible", "checkpoint requires lifecycle.state=running"

    if action == "resume":
        if lifecycle_state != "uncertain_interrupted":
            return False, "lifecycle_state_ineligible", "resume requires lifecycle.state=uncertain_interrupted"
        if _checkpoint_available(run_record):
            return True, "", ""
        return False, "resume_checkpoint_unavailable", "resume requires durable checkpoint lineage"

    if action == "restart":
        if lifecycle_state not in TERMINAL_LIFECYCLE_STATES | {"uncertain_interrupted"}:
            return False, "lifecycle_state_ineligible", "restart requires terminal or uncertain_interrupted lifecycle truth"
        if _restart_anchor_available(run_record):
            return True, "", ""
        return False, "restart_anchor_unavailable", "restart requires durable launch anchor"

    if action == "branch_from_checkpoint":
        if _checkpoint_available(run_record):
            return True, "", ""
        return False, "checkpoint_required", "branch_from_checkpoint requires a durable checkpoint"

    return False, "unknown_control_action", f"unknown control action: {action}"


def evaluate_control_action_request(
    action: str,
    source: Any = None,
    *,
    actor: str = "operator",
    requested_at_utc: str = "",
    project_id: str = "",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    normalized_action = _normalize_text(action)
    result = _base_result(
        action=normalized_action,
        actor=_normalize_text(actor) or "operator",
        requested_at_utc=_normalize_text(requested_at_utc),
    )

    if normalized_action not in CANONICAL_CONTROL_ACTIONS:
        return _deny(result, reason_code="unknown_control_action", reason_detail=f"unknown control action: {normalized_action}")

    policy_result = resolve_experiment_control_policy(source, project_id=project_id, repo_root=root)
    identity = policy_result.get("identity") if isinstance(policy_result.get("identity"), Mapping) else {}
    result["identity"] = {
        "project_id": _normalize_text(identity.get("project_id")).lower(),
        "resolution_source": _normalize_text(identity.get("resolution_source")),
        "run_id": _normalize_text(identity.get("run_id")),
        "job_id": _normalize_text(identity.get("job_id")),
    }

    if not bool(policy_result.get("ok")):
        errors = list(policy_result.get("errors") or [])
        first = errors[0] if errors else {}
        return _deny(
            result,
            reason_code=_normalize_text(first.get("code")) or "experiment_control_policy_resolution_failed",
            reason_detail=_normalize_text(first.get("message")) or "experiment control policy resolution failed",
        )

    effective_policy = policy_result.get("effective_policy") if isinstance(policy_result.get("effective_policy"), Mapping) else {}
    action_policy = (effective_policy.get("actions") if isinstance(effective_policy.get("actions"), Mapping) else {}).get(normalized_action)
    action_policy = action_policy if isinstance(action_policy, Mapping) else {}
    result["policy"] = {
        "effective_state": _normalize_text(action_policy.get("effective_state")).lower(),
        "rollout_state": _normalize_text(action_policy.get("rollout_state")).lower(),
        "policy_source": _normalize_text(action_policy.get("policy_source")),
    }

    if result["policy"]["effective_state"] == "unsupported":
        return _deny(result, reason_code="action_unsupported", reason_detail=f"{normalized_action} is declared unsupported")
    if result["policy"]["effective_state"] != "enabled":
        return _deny(result, reason_code="action_disabled_by_policy", reason_detail=f"{normalized_action} is not enabled by effective policy")
    if result["policy"]["rollout_state"] != "live":
        return _deny(result, reason_code="action_rollout_not_live", reason_detail=f"{normalized_action} rollout_state={result['policy']['rollout_state'] or 'hidden'}")

    try:
        run_record, resolution_source = _read_run_record_from_source(source, repo_root=root)
    except Exception as exc:
        return _deny(result, reason_code="run_record_unreadable", reason_detail=f"run lifecycle truth could not be read: {exc}")

    if run_record is None:
        return _deny(
            result,
            reason_code="missing_run_lifecycle_truth",
            reason_detail="control action enforcement requires durable run lifecycle truth",
        )

    summary = summarize_run_record(run_record)
    result["identity"]["resolution_source"] = resolution_source or result["identity"]["resolution_source"]
    result["identity"]["run_id"] = _normalize_text(summary.get("run_id")) or result["identity"]["run_id"]
    result["identity"]["job_id"] = _normalize_text(summary.get("job_id")) or result["identity"]["job_id"]
    result["identity"]["project_id"] = _normalize_text(summary.get("project_id")).lower() or result["identity"]["project_id"]
    result["lifecycle"] = {
        "state": _normalize_text(summary.get("state")).lower(),
        "confidence": _normalize_text(summary.get("confidence")).lower(),
        "terminal": bool(summary.get("terminal")),
        "transition_reason": _normalize_text(summary.get("transition_reason")),
        "latest_checkpoint_id": _normalize_text(summary.get("latest_checkpoint_id")),
        "resume_status": _normalize_text(summary.get("resume_status")),
        "checkpoint_count": int(summary.get("checkpoint_count") or 0),
    }
    if result["lifecycle"]["confidence"] not in {"durable", "reconciled", "uncertain"}:
        return _deny(result, reason_code="invalid_run_lifecycle_truth", reason_detail="run lifecycle truth confidence is invalid")

    allowed, denial_code, denial_detail = _lifecycle_precondition(normalized_action, run_record)
    if not allowed:
        return _deny(result, reason_code=denial_code, reason_detail=denial_detail)
    return _accept(result)


def enforce_control_action_request(
    action: str,
    source: Any = None,
    *,
    actor: str = "operator",
    requested_at_utc: str = "",
    project_id: str = "",
    repo_root: Optional[Path] = None,
    raise_on_denied: bool = True,
) -> Dict[str, Any]:
    result = evaluate_control_action_request(
        action,
        source,
        actor=actor,
        requested_at_utc=requested_at_utc,
        project_id=project_id,
        repo_root=repo_root,
    )
    if raise_on_denied and result.get("decision_state") == DECISION_STATE_DENIED:
        raise ControlActionDeniedError(result)
    return result


__all__ = [
    "CANONICAL_CONTROL_ACTIONS",
    "CONTROL_ACTION_ENFORCEMENT_SCHEMA_VERSION",
    "ControlActionDeniedError",
    "DECISION_STATE_ACCEPTED",
    "DECISION_STATE_DENIED",
    "REALIZATION_STATE_NOT_APPLICABLE",
    "REALIZATION_STATE_PENDING",
    "REQUEST_STATE_REQUESTED",
    "enforce_control_action_request",
    "evaluate_control_action_request",
]
