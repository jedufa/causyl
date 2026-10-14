"""Admin read/write surface for experiment control action policy in the plane."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from experiments_shared import canonicalize_control_action_policy
from experiments_shared import control_action_policy_path
from experiments_shared import read_control_action_policy
from experiments_shared import read_experiment_contract
from experiments_shared import resolve_effective_control_action_policy
from experiments_shared import resolve_experiment_control_policy
from experiments_shared import summarize_effective_control_policy
from experiments_shared import validate_control_action_policy
from infra.webui import server_assistant_bridge as _assistant_bridge
from infra.webui.server_http_core import _admin_extract_token

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]


_WRITE_LOCKS_GUARD = threading.Lock()
_WRITE_LOCKS: Dict[str, threading.Lock] = {}


class ExperimentControlPolicyAdminError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code or "experiment_control_policy_admin_error")
        self.details = dict(details or {})


@dataclass(frozen=True)
class ActorContext:
    actor_kind: str
    token_id: str
    issuer: str
    owner_match: bool
    session_id: str


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _status_payload(exc: ExperimentControlPolicyAdminError) -> Dict[str, Any]:
    return {
        "ok": False,
        "error_code": exc.code,
        "error": str(exc),
        "details": dict(exc.details),
    }


def _token_identity(session_record: Mapping[str, Any], *, repo_root: Path) -> Dict[str, str]:
    token_id = _clean_text(session_record.get("token_id"))
    if not token_id:
        return {"token_id": "", "issuer": ""}
    try:
        config = _assistant_bridge._load_config(repo_root)  # type: ignore[attr-defined]
        token_row = _assistant_bridge._token_row_by_id(  # type: ignore[attr-defined]
            _assistant_bridge._load_token_rows(repo_root, config),  # type: ignore[attr-defined]
            token_id,
        )
    except Exception:
        token_row = {}
    return {
        "token_id": token_id,
        "issuer": _clean_text(token_row.get("issuer")),
    }


def resolve_actor_context(
    handler: Any,
    cfg: Any,
    qs: Mapping[str, List[str]],
    *,
    repo_root: Path,
    project_owner: str,
) -> ActorContext:
    provided = _clean_text(_admin_extract_token(handler, dict(qs)))
    required_admin = _clean_text(getattr(cfg, "admin_token", ""))
    if required_admin and provided == required_admin:
        return ActorContext(
            actor_kind="admin",
            token_id="",
            issuer="",
            owner_match=True,
            session_id="",
        )

    session_record = _assistant_bridge.validate_control_pane_access_token(repo_root, provided)
    if not session_record:
        raise ExperimentControlPolicyAdminError(
            HTTPStatus.UNAUTHORIZED,
            "auth_required",
            "experiment control policy routes require an admin token or a valid control-pane session token",
        )
    identity = _token_identity(session_record, repo_root=repo_root)
    owner_key = _clean_text(project_owner)
    owner_match = owner_key in {identity["token_id"], identity["issuer"]}
    if not owner_match:
        raise ExperimentControlPolicyAdminError(
            HTTPStatus.FORBIDDEN,
            "experiment_owner_required",
            f"project owner mismatch for {owner_key or 'unknown owner'}",
            details={
                "project_owner": owner_key,
                "token_id": identity["token_id"],
                "issuer": identity["issuer"],
                "session_id": _clean_text(session_record.get("session_id")),
            },
        )
    return ActorContext(
        actor_kind="owner_session",
        token_id=identity["token_id"],
        issuer=identity["issuer"],
        owner_match=True,
        session_id=_clean_text(session_record.get("session_id")),
    )


def read_surface(
    handler: Any,
    cfg: Any,
    qs: Mapping[str, List[str]],
    *,
    repo_root: Path,
    project_id: str,
) -> Dict[str, Any]:
    project_key = _clean_text(project_id).lower()
    if not project_key:
        raise ExperimentControlPolicyAdminError(HTTPStatus.BAD_REQUEST, "project_id_required", "project_id is required")
    contract_result = read_experiment_contract(project_key, repo_root=repo_root)
    if not bool(contract_result.get("ok")) or not isinstance(contract_result.get("contract"), Mapping):
        raise ExperimentControlPolicyAdminError(
            HTTPStatus.NOT_FOUND,
            "experiment_contract_unavailable",
            f"experiment contract unavailable for {project_key}",
            details={"errors": [dict(row) for row in list(contract_result.get("errors") or [])]},
        )
    owner = _clean_text(dict(contract_result["contract"].get("project") or {}).get("owner"))
    actor = resolve_actor_context(handler, cfg, qs, repo_root=repo_root, project_owner=owner)
    resolved = resolve_experiment_control_policy({"project_id": project_key}, repo_root=repo_root)
    if not bool(resolved.get("ok")):
        raise ExperimentControlPolicyAdminError(
            HTTPStatus.CONFLICT,
            _clean_text(resolved.get("failure_class")) or "experiment_control_policy_read_failed",
            f"experiment control policy could not be resolved for {project_key}",
            details={"errors": [dict(row) for row in list(resolved.get("errors") or [])]},
        )
    policy = dict(resolved.get("policy") or {})
    return {
        "schema_version": "admin_experiment_control_policy_read_v1",
        "ok": True,
        "project_id": project_key,
        "authorization": {
            "actor_kind": actor.actor_kind,
            "project_owner": owner,
            "token_id": actor.token_id,
            "issuer": actor.issuer,
            "session_id": actor.session_id,
        },
        "policy_path": _clean_text(((resolved.get("contract_projection") or {}) if isinstance(resolved.get("contract_projection"), Mapping) else {}).get("control_action_policy_identity", {}).get("policy_path")),
        "policy_hash": _clean_text(policy.get("policy_hash")),
        "policy": policy,
        "effective_policy": dict(resolved.get("effective_policy") or {}),
        "control_policy_summary": dict(resolved.get("control_policy_summary") or {}),
        "policy_refresh_state": dict(resolved.get("policy_refresh_state") or {}),
        "mutation_contract": {
            "method": "POST",
            "api_path": "/api/admin/experiment_control_policy",
            "required_fields": ["project_id", "action_id", "desired_policy_state", "expected_policy_hash", "confirmation"],
            "optional_fields": ["rollout_state", "note"],
        },
    }


def _lock_for_path(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _WRITE_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _policy_write_lock(policy_path: Path):
    thread_lock = _lock_for_path(policy_path)
    lock_path = policy_path.with_name(policy_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with thread_lock:
        with open(lock_path, "a+", encoding="utf-8") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def _policy_json_text(policy: Mapping[str, Any]) -> str:
    return json.dumps(policy, indent=2, sort_keys=True) + "\n"


def _atomic_write_policy(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _policy_json_text(payload)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
        tmp_path = Path(handle.name)
        handle.write(text)
    os.replace(tmp_path, path)


def _build_mutated_policy(
    *,
    current_policy: Mapping[str, Any],
    project_id: str,
    action_id: str,
    desired_policy_state: str,
    rollout_state: str,
    note: str,
) -> Dict[str, Any]:
    next_policy = {
        "schema_version": _clean_text(current_policy.get("schema_version")) or "control_action_policy_v1",
        "project_id": project_id,
        "actions": dict(current_policy.get("actions") or {}),
    }
    actions = dict(next_policy["actions"])
    current_entry = dict(actions.get(action_id) or {})
    actions[action_id] = {
        "policy_state": desired_policy_state,
        "rollout_state": rollout_state or _clean_text(current_entry.get("rollout_state")) or "live",
        "note": note if note or "note" in current_entry else _clean_text(current_entry.get("note")),
    }
    next_policy["actions"] = actions
    return canonicalize_control_action_policy(next_policy)


def mutate_surface(
    handler: Any,
    cfg: Any,
    qs: Mapping[str, List[str]],
    payload: Mapping[str, Any],
    *,
    repo_root: Path,
    refresh_runtime,
) -> Dict[str, Any]:
    project_id = _clean_text(payload.get("project_id")).lower()
    action_id = _clean_text(payload.get("action_id")).lower()
    desired_policy_state = _clean_text(payload.get("desired_policy_state")).lower()
    expected_policy_hash = _clean_text(payload.get("expected_policy_hash"))
    confirmation = _clean_text(payload.get("confirmation"))
    rollout_state = _clean_text(payload.get("rollout_state")).lower()
    note = _clean_text(payload.get("note"))
    if not project_id:
        raise ExperimentControlPolicyAdminError(HTTPStatus.BAD_REQUEST, "project_id_required", "project_id is required")
    if not action_id:
        raise ExperimentControlPolicyAdminError(HTTPStatus.BAD_REQUEST, "action_id_required", "action_id is required")
    if not desired_policy_state:
        raise ExperimentControlPolicyAdminError(
            HTTPStatus.BAD_REQUEST,
            "desired_policy_state_required",
            "desired_policy_state is required",
        )
    if not expected_policy_hash:
        raise ExperimentControlPolicyAdminError(
            HTTPStatus.BAD_REQUEST,
            "expected_policy_hash_required",
            "expected_policy_hash is required for compare-and-swap mutation",
        )
    if not confirmation:
        raise ExperimentControlPolicyAdminError(
            HTTPStatus.BAD_REQUEST,
            "confirmation_required",
            "confirmation is required",
        )

    contract_result = read_experiment_contract(project_id, repo_root=repo_root)
    if not bool(contract_result.get("ok")) or not isinstance(contract_result.get("contract"), Mapping):
        raise ExperimentControlPolicyAdminError(
            HTTPStatus.NOT_FOUND,
            "experiment_contract_unavailable",
            f"experiment contract unavailable for {project_id}",
            details={"errors": [dict(row) for row in list(contract_result.get("errors") or [])]},
        )
    owner = _clean_text(dict(contract_result["contract"].get("project") or {}).get("owner"))
    actor = resolve_actor_context(handler, cfg, qs, repo_root=repo_root, project_owner=owner)
    policy_path = control_action_policy_path(project_id, repo_root=repo_root)

    with _policy_write_lock(policy_path):
        current = read_control_action_policy(project_id, repo_root=repo_root)
        if not bool(current.get("ok")) or not isinstance(current.get("policy"), Mapping):
            raise ExperimentControlPolicyAdminError(
                HTTPStatus.CONFLICT,
                "control_action_policy_unavailable",
                f"control action policy unavailable for {project_id}",
                details={
                    "code": _clean_text(current.get("code")),
                    "validation": dict(current.get("validation") or {}),
                },
            )
        current_policy = dict(current.get("policy") or {})
        current_hash = _clean_text(current_policy.get("policy_hash"))
        if current_hash != expected_policy_hash:
            raise ExperimentControlPolicyAdminError(
                HTTPStatus.CONFLICT,
                "expected_policy_hash_mismatch",
                "control action policy changed since the operator last read it",
                details={"current_policy_hash": current_hash, "expected_policy_hash": expected_policy_hash},
            )
        mutated_policy = _build_mutated_policy(
            current_policy=current_policy,
            project_id=project_id,
            action_id=action_id,
            desired_policy_state=desired_policy_state,
            rollout_state=rollout_state,
            note=note,
        )
        validation = validate_control_action_policy(mutated_policy, expected_project_id=project_id)
        if not bool(validation.get("ok")):
            raise ExperimentControlPolicyAdminError(
                HTTPStatus.BAD_REQUEST,
                "control_action_policy_validation_failed",
                f"mutated control action policy is malformed for {project_id}",
                details={"errors": [dict(row) for row in list(validation.get("errors") or [])]},
            )
        effective = resolve_effective_control_action_policy(contract_result["contract"], mutated_policy)
        if not bool(effective.get("ok")):
            raise ExperimentControlPolicyAdminError(
                HTTPStatus.BAD_REQUEST,
                "control_action_policy_conflict",
                f"mutated control action policy is invalid for {project_id}",
                details={"errors": [dict(row) for row in list(effective.get("errors") or [])]},
            )
        _atomic_write_policy(policy_path, mutated_policy)

    summary = summarize_effective_control_policy(dict(effective.get("effective_policy") or {}))
    refresh_payload = dict(refresh_runtime() or {})
    refresh_ok = bool(refresh_payload.get("ok"))
    confirmation_summary = (
        f"Policy for {project_id}:{action_id} was written and refreshed. Future work sees the new policy; in-flight turns/jobs are unchanged."
        if refresh_ok
        else f"Policy for {project_id}:{action_id} was written, but runtime refresh did not fully apply. Future work may keep using the last known-good cached policy until a later refresh succeeds."
    )
    return {
        "schema_version": "admin_experiment_control_policy_mutation_v1",
        "ok": refresh_ok,
        "project_id": project_id,
        "action_id": action_id,
        "authorization": {
            "actor_kind": actor.actor_kind,
            "project_owner": owner,
            "token_id": actor.token_id,
            "issuer": actor.issuer,
            "session_id": actor.session_id,
        },
        "mutation_applied": True,
        "policy_path": str(policy_path),
        "previous_policy_hash": expected_policy_hash,
        "written_policy_hash": _clean_text(mutated_policy.get("policy_hash")),
        "requested_confirmation": confirmation,
        "confirmation": {
            "state": "applied_and_refreshed" if refresh_ok else "written_refresh_degraded",
            "summary": confirmation_summary,
            "effective_scope": _clean_text(dict(refresh_payload.get("scope") or {}).get("applies_to")) or "future_work_started_after_refresh",
        },
        "written_policy": mutated_policy,
        "written_effective_policy": dict(effective.get("effective_policy") or {}),
        "written_control_policy_summary": summary,
        "runtime_refresh": refresh_payload,
    }
