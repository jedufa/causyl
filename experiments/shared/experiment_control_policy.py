"""Shared experiment control policy resolver and operator summary surface."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import threading
from typing import Any, Dict, List, Mapping, Optional

from .control_action_policy import control_action_policy_path
from .control_action_policy import read_control_action_policy
from .control_action_policy import resolve_effective_control_action_policy
from .experiment_contract import experiment_contract_path
from .experiment_contract import _normalize_text
from .experiment_contract import _repo_root
from .experiment_contract import read_experiment_contract
from .experiment_records import read_run_record
from .experiment_records import summarize_run_record


EXPERIMENT_CONTROL_POLICY_RESOLUTION_SCHEMA_VERSION = "experiment_control_policy_resolution_v1"
EXPERIMENT_CONTROL_POLICY_SUMMARY_SCHEMA_VERSION = "experiment_control_policy_summary_v1"
EXPERIMENT_CONTROL_POLICY_REFRESH_SCHEMA_VERSION = "experiment_control_policy_refresh_v1"

ERROR_CODES = {
    "experiment_identity_unresolved": "experiment_identity_unresolved",
    "run_record_unreadable": "run_record_unreadable",
    "experiment_contract_unavailable": "experiment_contract_unavailable",
    "control_action_policy_unavailable": "control_action_policy_unavailable",
    "control_action_policy_conflict": "control_action_policy_conflict",
}


_POLICY_CACHE_LOCK = threading.RLock()
_POLICY_CACHE: Dict[tuple[str, str], Dict[str, Any]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cache_key(*, project_id: str, repo_root: Path) -> tuple[str, str]:
    return (str(repo_root.resolve()), _normalize_text(project_id).lower())


def _path_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _path_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


def _build_snapshot_metadata(*, project_id: str, repo_root: Path, contract_path: Path, policy_path: Path) -> Dict[str, Any]:
    return {
        "project_id": _normalize_text(project_id).lower(),
        "repo_root": str(repo_root.resolve()),
        "contract_path": str(contract_path.resolve()),
        "policy_path": str(policy_path.resolve()),
        "contract_mtime": _path_mtime(contract_path),
        "policy_mtime": _path_mtime(policy_path),
        "contract_digest": _path_digest(contract_path),
        "policy_digest": _path_digest(policy_path),
    }


def _snapshot_drift_reason(metadata: Mapping[str, Any]) -> str:
    contract_path = Path(str(metadata.get("contract_path") or ""))
    policy_path = Path(str(metadata.get("policy_path") or ""))
    cached_contract_mtime = metadata.get("contract_mtime")
    cached_policy_mtime = metadata.get("policy_mtime")
    cached_contract_digest = _normalize_text(metadata.get("contract_digest"))
    cached_policy_digest = _normalize_text(metadata.get("policy_digest"))
    live_contract_mtime = _path_mtime(contract_path) if str(contract_path) else None
    live_policy_mtime = _path_mtime(policy_path) if str(policy_path) else None
    live_contract_digest = _path_digest(contract_path) if str(contract_path) else ""
    live_policy_digest = _path_digest(policy_path) if str(policy_path) else ""
    if live_contract_digest != cached_contract_digest or live_contract_mtime != cached_contract_mtime:
        return "experiment_contract_changed_since_last_refresh"
    if live_policy_digest != cached_policy_digest or live_policy_mtime != cached_policy_mtime:
        return "control_action_policy_changed_since_last_refresh"
    return ""


def _refresh_state_from_entry(entry: Mapping[str, Any], *, project_id: str) -> Dict[str, Any]:
    metadata = entry.get("snapshot_metadata") if isinstance(entry.get("snapshot_metadata"), Mapping) else {}
    last_error = entry.get("last_error") if isinstance(entry.get("last_error"), Mapping) else {}
    stale_reason = _snapshot_drift_reason(metadata)
    status = str(entry.get("status") or "").strip() or "uncached"
    stale = False
    if stale_reason:
        stale = True
        if status == "fresh":
            status = "stale_snapshot"
    elif status == "reload_failed":
        stale = True
    return {
        "schema_version": EXPERIMENT_CONTROL_POLICY_REFRESH_SCHEMA_VERSION,
        "project_id": _normalize_text(project_id).lower(),
        "status": status,
        "stale": stale,
        "stale_reason": stale_reason or _normalize_text(last_error.get("reason")),
        "last_refresh_attempt_utc": _normalize_text(entry.get("last_refresh_attempt_utc")),
        "last_refresh_success_utc": _normalize_text(entry.get("last_refresh_success_utc")),
        "last_refresh_failure_utc": _normalize_text(entry.get("last_refresh_failure_utc")),
        "last_error": dict(last_error) if last_error else {},
        "snapshot_metadata": dict(metadata),
        "effective_scope": "future_work_started_after_refresh",
    }


def _apply_refresh_state(result: Mapping[str, Any], refresh_state: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(result))
    payload["policy_refresh_state"] = dict(refresh_state)
    contract_projection = payload.get("contract_projection")
    if isinstance(contract_projection, dict):
        contract_projection["control_action_policy_refresh_state"] = dict(refresh_state)
    return payload


def _snapshot_override_from_source(source: Any) -> Dict[str, Any] | None:
    if not isinstance(source, Mapping):
        return None
    snapshot = source.get("control_action_policy_snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    if not bool(snapshot.get("ok")):
        return None
    if not isinstance(snapshot.get("effective_policy"), Mapping):
        return None
    return deepcopy(dict(snapshot))


def _error(*, code: str, field: str, message: str) -> Dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _identity_from_run_record_path(path: Path) -> Dict[str, Any]:
    record = read_run_record(path)
    summary = summarize_run_record(record)
    project_id = _normalize_text(record.get("project_id")).lower()
    return {
        "project_id": project_id,
        "resolution_source": "run_record",
        "run_id": _normalize_text(record.get("run_id")),
        "job_id": _normalize_text(record.get("job_id")),
        "run_summary": summary,
    }


def _identity_from_mapping(obj: Mapping[str, Any]) -> Dict[str, Any]:
    if isinstance(obj.get("run_record"), Mapping):
        record = dict(obj.get("run_record") or {})
        project_id = _normalize_text(record.get("project_id")).lower()
        if project_id:
            return {
                "project_id": project_id,
                "resolution_source": "run_record",
                "run_id": _normalize_text(record.get("run_id")),
                "job_id": _normalize_text(record.get("job_id")),
                "run_summary": summarize_run_record(record),
            }
    if _normalize_text(obj.get("run_record_path")):
        return {"run_record_path": _normalize_text(obj.get("run_record_path"))}
    project_run = obj.get("project_run") if isinstance(obj.get("project_run"), Mapping) else {}
    if _normalize_text(project_run.get("project_id")):
        return {
            "project_id": _normalize_text(project_run.get("project_id")).lower(),
            "resolution_source": "scheduled_job_input",
            "run_id": "",
            "job_id": _normalize_text(obj.get("job_id")),
            "run_summary": {},
        }
    project = obj.get("project") if isinstance(obj.get("project"), Mapping) else {}
    if _normalize_text(project.get("project_id")):
        return {
            "project_id": _normalize_text(project.get("project_id")).lower(),
            "resolution_source": "project_reference",
            "run_id": "",
            "job_id": "",
            "run_summary": {},
        }
    if _normalize_text(obj.get("project_id")):
        return {
            "project_id": _normalize_text(obj.get("project_id")).lower(),
            "resolution_source": "project_reference",
            "run_id": "",
            "job_id": _normalize_text(obj.get("job_id")),
            "run_summary": {},
        }
    return {}


def _resolve_identity(source: Any, *, project_id: str, repo_root: Path) -> Dict[str, Any]:
    explicit = _normalize_text(project_id).lower()
    if explicit:
        return {
            "project_id": explicit,
            "resolution_source": "project_reference",
            "run_id": "",
            "job_id": "",
            "run_summary": {},
        }
    if isinstance(source, Mapping):
        candidate = _identity_from_mapping(source)
        if _normalize_text(candidate.get("run_record_path")):
            path = Path(str(candidate["run_record_path"])).expanduser()
            if not path.is_absolute():
                path = (repo_root / path).resolve()
            return _identity_from_run_record_path(path)
        if _normalize_text(candidate.get("project_id")):
            return candidate
        return {}
    if isinstance(source, (str, Path)):
        text = _normalize_text(source)
        if not text:
            return {}
        path = Path(text).expanduser()
        if path.exists():
            return _identity_from_run_record_path(path if path.name == "run_record.json" else path / "run_record.json")
        return {
            "project_id": text.lower(),
            "resolution_source": "project_reference",
            "run_id": "",
            "job_id": "",
            "run_summary": {},
        }
    return {}


def summarize_effective_control_policy(effective_policy: Mapping[str, Any]) -> Dict[str, Any]:
    actions = effective_policy.get("actions") if isinstance(effective_policy.get("actions"), Mapping) else {}
    action_rows: List[Dict[str, Any]] = []
    available_actions: List[str] = []
    blocked_actions: List[str] = []
    unsupported_actions: List[str] = []
    staged_actions: List[str] = []
    hidden_actions: List[str] = []
    live_actions: List[str] = []

    for action in sorted(actions.keys()):
        row = actions.get(action) if isinstance(actions.get(action), Mapping) else {}
        effective_state = _normalize_text(row.get("effective_state")).lower()
        rollout_state = _normalize_text(row.get("rollout_state")).lower()
        note = _normalize_text(row.get("note"))
        item = {
            "action": str(action),
            "effective_state": effective_state,
            "rollout_state": rollout_state,
            "policy_source": _normalize_text(row.get("policy_source")),
            "note": note,
            "available": effective_state == "enabled",
        }
        action_rows.append(item)
        if effective_state == "enabled":
            available_actions.append(str(action))
        elif effective_state == "unsupported":
            unsupported_actions.append(str(action))
        else:
            blocked_actions.append(str(action))
        if rollout_state == "staged":
            staged_actions.append(str(action))
        elif rollout_state == "hidden":
            hidden_actions.append(str(action))
        elif rollout_state == "live":
            live_actions.append(str(action))

    headline = (
        f"{len(available_actions)} available, "
        f"{len(blocked_actions)} blocked, "
        f"{len(unsupported_actions)} unsupported"
    )
    operator_lines = [
        f"{row['action']}: {row['effective_state']}/{row['rollout_state']}" + (f" ({row['note']})" if row["note"] else "")
        for row in action_rows
    ]
    return {
        "schema_version": EXPERIMENT_CONTROL_POLICY_SUMMARY_SCHEMA_VERSION,
        "project_id": _normalize_text(effective_policy.get("project_id")).lower(),
        "headline": headline,
        "available_actions": available_actions,
        "blocked_actions": blocked_actions,
        "unsupported_actions": unsupported_actions,
        "staged_actions": staged_actions,
        "hidden_actions": hidden_actions,
        "live_actions": live_actions,
        "action_rows": action_rows,
        "operator_lines": operator_lines,
    }


def _resolve_experiment_control_policy_uncached(
    source: Any = None,
    *,
    project_id: str = "",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    try:
        identity = _resolve_identity(source, project_id=project_id, repo_root=root)
    except Exception as exc:
        return {
            "schema_version": EXPERIMENT_CONTROL_POLICY_RESOLUTION_SCHEMA_VERSION,
            "ok": False,
            "failure_class": "experiment_control_policy_resolution_failed",
            "identity": {"project_id": "", "resolution_source": "run_record"},
            "errors": [
                _error(
                    code=ERROR_CODES["run_record_unreadable"],
                    field="run_record",
                    message=f"run record could not be read for experiment control policy resolution: {exc}",
                )
            ],
            "warnings": [],
        }

    resolved_project_id = _normalize_text(identity.get("project_id")).lower()
    if not resolved_project_id:
        return {
            "schema_version": EXPERIMENT_CONTROL_POLICY_RESOLUTION_SCHEMA_VERSION,
            "ok": False,
            "failure_class": "experiment_control_policy_resolution_failed",
            "identity": {"project_id": "", "resolution_source": ""},
            "errors": [
                _error(
                    code=ERROR_CODES["experiment_identity_unresolved"],
                    field="project_id",
                    message="experiment identity could not be resolved from durable run truth, project references, or scheduled job inputs",
                )
            ],
            "warnings": [],
        }

    contract_result = read_experiment_contract(resolved_project_id, repo_root=root)
    if not isinstance(contract_result.get("contract"), Mapping) or not bool(contract_result.get("ok")):
        return {
            "schema_version": EXPERIMENT_CONTROL_POLICY_RESOLUTION_SCHEMA_VERSION,
            "ok": False,
            "failure_class": "experiment_control_policy_read_failed",
            "identity": identity,
            "errors": [
                dict(row)
                for row in list(contract_result.get("errors") or [])
            ] or [
                _error(
                    code=ERROR_CODES["experiment_contract_unavailable"],
                    field="contract_path",
                    message=f"experiment contract unavailable for {resolved_project_id}",
                )
            ],
            "warnings": [dict(row) for row in list(contract_result.get("warnings") or [])],
        }

    policy_result = read_control_action_policy(resolved_project_id, repo_root=root)
    if not bool(policy_result.get("ok")) or not isinstance(policy_result.get("policy"), Mapping):
        errors = []
        validation = policy_result.get("validation") if isinstance(policy_result.get("validation"), Mapping) else {}
        if validation:
            errors.extend(dict(row) for row in list(validation.get("errors") or []))
        elif _normalize_text(policy_result.get("code")):
            errors.append(
                _error(
                    code=str(policy_result.get("code")),
                    field="control_action_policy",
                    message=str(policy_result.get("message") or "control action policy unavailable"),
                )
            )
        return {
            "schema_version": EXPERIMENT_CONTROL_POLICY_RESOLUTION_SCHEMA_VERSION,
            "ok": False,
            "failure_class": "experiment_control_policy_read_failed",
            "identity": identity,
            "errors": errors or [
                _error(
                    code=ERROR_CODES["control_action_policy_unavailable"],
                    field="control_action_policy",
                    message=f"control action policy unavailable for {resolved_project_id}",
                )
            ],
            "warnings": [],
        }

    effective_result = resolve_effective_control_action_policy(
        contract_result["contract"],
        policy_result["policy"],
    )
    if not bool(effective_result.get("ok")):
        return {
            "schema_version": EXPERIMENT_CONTROL_POLICY_RESOLUTION_SCHEMA_VERSION,
            "ok": False,
            "failure_class": "experiment_control_policy_conflict",
            "identity": identity,
            "errors": [dict(row) for row in list(effective_result.get("errors") or [])] or [
                _error(
                    code=ERROR_CODES["control_action_policy_conflict"],
                    field="control_action_policy",
                    message=f"effective control action policy resolution failed for {resolved_project_id}",
                )
            ],
            "warnings": [],
        }

    effective_policy = dict(effective_result.get("effective_policy") or {})
    control_policy_summary = summarize_effective_control_policy(effective_policy)
    contract_projection = dict(contract_result.get("summary") or {})
    contract_projection["effective_control_action_policy"] = effective_policy
    contract_projection["control_action_policy_summary"] = control_policy_summary
    contract_projection["control_action_policy_identity"] = {
        "project_id": resolved_project_id,
        "resolution_source": _normalize_text(identity.get("resolution_source")),
        "run_id": _normalize_text(identity.get("run_id")),
        "job_id": _normalize_text(identity.get("job_id")),
        "policy_path": _normalize_text(policy_result.get("path")),
    }

    return {
        "schema_version": EXPERIMENT_CONTROL_POLICY_RESOLUTION_SCHEMA_VERSION,
        "ok": True,
        "failure_class": "",
        "identity": identity,
        "contract": dict(contract_result.get("contract") or {}),
        "policy": dict(policy_result.get("policy") or {}),
        "effective_policy": effective_policy,
        "control_policy_summary": control_policy_summary,
        "contract_projection": contract_projection,
        "errors": [],
        "warnings": [dict(row) for row in list(contract_result.get("warnings") or [])],
    }


def resolve_experiment_control_policy(
    source: Any = None,
    *,
    project_id: str = "",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    snapshot_override = _snapshot_override_from_source(source)
    if snapshot_override is not None:
        override_project_id = _normalize_text(
            ((snapshot_override.get("identity") or {}) if isinstance(snapshot_override.get("identity"), Mapping) else {}).get("project_id")
            or ((snapshot_override.get("effective_policy") or {}) if isinstance(snapshot_override.get("effective_policy"), Mapping) else {}).get("project_id")
            or project_id
        ).lower()
        refresh_state = {
            "schema_version": EXPERIMENT_CONTROL_POLICY_REFRESH_SCHEMA_VERSION,
            "project_id": override_project_id,
            "status": "snapshot_override",
            "stale": False,
            "stale_reason": "",
            "last_refresh_attempt_utc": "",
            "last_refresh_success_utc": "",
            "last_refresh_failure_utc": "",
            "last_error": {},
            "snapshot_metadata": {},
            "effective_scope": "in_flight_work_keeps_prior_policy_snapshot",
        }
        return _apply_refresh_state(snapshot_override, refresh_state)

    try:
        identity = _resolve_identity(source, project_id=project_id, repo_root=root)
    except Exception:
        identity = {}
    resolved_project_id = _normalize_text(identity.get("project_id")).lower()
    if resolved_project_id:
        cache_key = _cache_key(project_id=resolved_project_id, repo_root=root)
        with _POLICY_CACHE_LOCK:
            entry = deepcopy(_POLICY_CACHE.get(cache_key) or {})
        if entry and isinstance(entry.get("result"), Mapping):
            cached_result = _apply_refresh_state(entry["result"], _refresh_state_from_entry(entry, project_id=resolved_project_id))
            if str(entry.get("status") or "").strip() == "reload_failed":
                cached_result["warnings"] = [dict(row) for row in list(cached_result.get("warnings") or [])]
                cached_result["warnings"].append(
                    {
                        "code": "control_action_policy_refresh_failed_last_known_good_retained",
                        "field": "control_action_policy",
                        "message": "latest control action policy reload failed; last known-good snapshot retained until a later refresh succeeds",
                    }
                )
            return cached_result

    result = _resolve_experiment_control_policy_uncached(source, project_id=project_id, repo_root=root)
    identity = result.get("identity") if isinstance(result.get("identity"), Mapping) else {}
    resolved_project_id = _normalize_text(identity.get("project_id")).lower()
    if not resolved_project_id:
        refresh_state = {
            "schema_version": EXPERIMENT_CONTROL_POLICY_REFRESH_SCHEMA_VERSION,
            "project_id": "",
            "status": "unresolved",
            "stale": False,
            "stale_reason": "",
            "last_refresh_attempt_utc": "",
            "last_refresh_success_utc": "",
            "last_refresh_failure_utc": "",
            "last_error": {},
            "snapshot_metadata": {},
            "effective_scope": "future_work_started_after_refresh",
        }
        return _apply_refresh_state(result, refresh_state)

    if bool(result.get("ok")):
        cache_key = _cache_key(project_id=resolved_project_id, repo_root=root)
        contract_path = experiment_contract_path(resolved_project_id, repo_root=root)
        policy_path = control_action_policy_path(resolved_project_id, repo_root=root)
        now_utc = _utc_now()
        entry = {
            "result": deepcopy(result),
            "status": "fresh",
            "snapshot_metadata": _build_snapshot_metadata(
                project_id=resolved_project_id,
                repo_root=root,
                contract_path=contract_path,
                policy_path=policy_path,
            ),
            "last_refresh_attempt_utc": now_utc,
            "last_refresh_success_utc": now_utc,
            "last_refresh_failure_utc": "",
            "last_error": {},
        }
        with _POLICY_CACHE_LOCK:
            _POLICY_CACHE[cache_key] = entry
        return _apply_refresh_state(result, _refresh_state_from_entry(entry, project_id=resolved_project_id))

    refresh_state = {
        "schema_version": EXPERIMENT_CONTROL_POLICY_REFRESH_SCHEMA_VERSION,
        "project_id": resolved_project_id,
        "status": "uncached_read_failed",
        "stale": False,
        "stale_reason": "",
        "last_refresh_attempt_utc": "",
        "last_refresh_success_utc": "",
        "last_refresh_failure_utc": "",
        "last_error": {
            "reason": "initial_read_failed",
            "failure_class": _normalize_text(result.get("failure_class")),
            "errors": [dict(row) for row in list(result.get("errors") or [])],
        },
        "snapshot_metadata": {},
        "effective_scope": "future_work_started_after_refresh",
    }
    return _apply_refresh_state(result, refresh_state)


def refresh_experiment_control_policy_cache(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    repo_key = str(root.resolve())
    now_utc = _utc_now()
    with _POLICY_CACHE_LOCK:
        cached_keys = [key for key in _POLICY_CACHE.keys() if key[0] == repo_key]

    refreshed_projects: List[Dict[str, Any]] = []
    ok = True
    for _, project_id in sorted(cached_keys):
        fresh_result = _resolve_experiment_control_policy_uncached(project_id=project_id, repo_root=root)
        cache_key = _cache_key(project_id=project_id, repo_root=root)
        with _POLICY_CACHE_LOCK:
            prior_entry = deepcopy(_POLICY_CACHE.get(cache_key) or {})
        if bool(fresh_result.get("ok")):
            contract_path = experiment_contract_path(project_id, repo_root=root)
            policy_path = control_action_policy_path(project_id, repo_root=root)
            entry = {
                "result": deepcopy(fresh_result),
                "status": "fresh",
                "snapshot_metadata": _build_snapshot_metadata(
                    project_id=project_id,
                    repo_root=root,
                    contract_path=contract_path,
                    policy_path=policy_path,
                ),
                "last_refresh_attempt_utc": now_utc,
                "last_refresh_success_utc": now_utc,
                "last_refresh_failure_utc": "",
                "last_error": {},
            }
            with _POLICY_CACHE_LOCK:
                _POLICY_CACHE[cache_key] = entry
            refreshed_projects.append(
                {
                    "project_id": project_id,
                    "ok": True,
                    "status": "fresh",
                    "policy_refresh_state": _refresh_state_from_entry(entry, project_id=project_id),
                }
            )
            continue

        ok = False
        entry = {
            **prior_entry,
            "status": "reload_failed",
            "last_refresh_attempt_utc": now_utc,
            "last_refresh_failure_utc": now_utc,
            "last_error": {
                "reason": "reload_failed_last_known_good_retained",
                "failure_class": _normalize_text(fresh_result.get("failure_class")),
                "errors": [dict(row) for row in list(fresh_result.get("errors") or [])],
            },
        }
        if prior_entry:
            with _POLICY_CACHE_LOCK:
                _POLICY_CACHE[cache_key] = entry
        refreshed_projects.append(
            {
                "project_id": project_id,
                "ok": False,
                "status": "reload_failed",
                "policy_refresh_state": _refresh_state_from_entry(entry, project_id=project_id),
                "errors": [dict(row) for row in list(fresh_result.get("errors") or [])],
            }
        )

    return {
        "schema_version": EXPERIMENT_CONTROL_POLICY_REFRESH_SCHEMA_VERSION,
        "utc": now_utc,
        "ok": ok,
        "effective_scope": "future_work_started_after_refresh",
        "cached_project_count": len(cached_keys),
        "refreshed_projects": refreshed_projects,
    }


__all__ = [
    "EXPERIMENT_CONTROL_POLICY_REFRESH_SCHEMA_VERSION",
    "EXPERIMENT_CONTROL_POLICY_RESOLUTION_SCHEMA_VERSION",
    "EXPERIMENT_CONTROL_POLICY_SUMMARY_SCHEMA_VERSION",
    "refresh_experiment_control_policy_cache",
    "resolve_experiment_control_policy",
    "summarize_effective_control_policy",
]
