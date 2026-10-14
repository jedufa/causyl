"""Canonical control action policy overlay schema, validation, and merge rules."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .experiment_contract import HASH_PREFIX
from .experiment_contract import _normalize_json_like
from .experiment_contract import _normalize_text
from .experiment_contract import _path_display
from .experiment_contract import _repo_root


CONTROL_ACTION_POLICY_SCHEMA_VERSION = "control_action_policy_v1"
EFFECTIVE_CONTROL_ACTION_POLICY_SCHEMA_VERSION = "effective_control_action_policy_v1"
POLICY_STATE_SET = frozenset({"disabled", "enabled", "unsupported"})
ROLLOUT_STATE_SET = frozenset({"hidden", "live", "staged"})
TOP_LEVEL_FIELDS = frozenset({"schema_version", "policy_hash", "project_id", "actions"})
ACTION_ENTRY_FIELDS = frozenset({"policy_state", "rollout_state", "note"})

ERROR_CODES = {
    "invalid_policy_schema": "invalid_policy_schema",
    "invalid_policy_hash": "invalid_policy_hash",
    "invalid_policy_state": "invalid_policy_state",
    "invalid_rollout_state": "invalid_rollout_state",
    "unknown_control_action": "unknown_control_action",
    "contract_policy_conflict": "contract_policy_conflict",
}


def control_action_policy_path(project_id: str, *, repo_root: Optional[Path] = None) -> Path:
    project = str(project_id or "").strip().lower()
    if not project:
        raise ValueError("project_id is required")
    root = _repo_root(repo_root)
    return (root / "experiments" / "projects" / project / "_runner" / "control_action_policy.json").resolve()


def _normalize_action_policy_entry(raw: Any) -> Dict[str, Any]:
    obj = raw if isinstance(raw, Mapping) else {}
    note = obj.get("note")
    rollout_state = _normalize_text(obj.get("rollout_state")).lower() or "live"
    return {
        "policy_state": _normalize_text(obj.get("policy_state")).lower(),
        "rollout_state": rollout_state,
        "note": _normalize_text(note) if note is None or isinstance(note, str) else note,
    }


def normalize_control_action_policy(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    actions_raw = obj.get("actions") if isinstance(obj.get("actions"), Mapping) else {}
    actions: Dict[str, Dict[str, Any]] = {}
    for raw_action in sorted(actions_raw.keys(), key=lambda value: _normalize_text(value)):
        action = _normalize_text(raw_action)
        if not action:
            continue
        actions[action] = _normalize_action_policy_entry(actions_raw.get(raw_action))
    normalized = {
        "schema_version": _normalize_text(obj.get("schema_version") or CONTROL_ACTION_POLICY_SCHEMA_VERSION),
        "policy_hash": _normalize_text(obj.get("policy_hash")),
        "project_id": _normalize_text(obj.get("project_id")).lower(),
        "actions": actions,
    }
    return normalized


def canonical_control_action_policy_json(raw: Mapping[str, Any]) -> str:
    normalized = normalize_control_action_policy(raw)
    payload = dict(normalized)
    payload["policy_hash"] = ""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def compute_control_action_policy_hash(raw: Mapping[str, Any]) -> str:
    canonical_json = canonical_control_action_policy_json(raw)
    return HASH_PREFIX + hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def canonicalize_control_action_policy(raw: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = normalize_control_action_policy(raw)
    normalized["policy_hash"] = compute_control_action_policy_hash(normalized)
    return normalized


def validate_control_action_policy(
    raw: Mapping[str, Any],
    *,
    expected_project_id: str = "",
) -> Dict[str, Any]:
    raw_obj = raw if isinstance(raw, Mapping) else {}
    actions_raw = raw_obj.get("actions")
    normalized = normalize_control_action_policy(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    for field_name in sorted(raw_obj.keys()):
        if field_name in TOP_LEVEL_FIELDS:
            continue
        errors.append(
            {
                "code": ERROR_CODES["invalid_policy_schema"],
                "field": str(field_name),
                "message": f"unknown top-level control action policy field: {field_name}",
            }
        )

    if normalized["schema_version"] != CONTROL_ACTION_POLICY_SCHEMA_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["invalid_policy_schema"],
                "field": "schema_version",
                "message": f"schema_version must be {CONTROL_ACTION_POLICY_SCHEMA_VERSION}",
            }
        )
    if not normalized["project_id"]:
        errors.append(
            {
                "code": ERROR_CODES["invalid_policy_schema"],
                "field": "project_id",
                "message": "project_id is required",
            }
        )
    expected = _normalize_text(expected_project_id).lower()
    if expected and normalized["project_id"] and normalized["project_id"] != expected:
        errors.append(
            {
                "code": ERROR_CODES["invalid_policy_schema"],
                "field": "project_id",
                "message": f"project_id mismatch: expected {expected}",
            }
        )
    if not isinstance(actions_raw, Mapping):
        errors.append(
            {
                "code": ERROR_CODES["invalid_policy_schema"],
                "field": "actions",
                "message": "actions must be an object keyed by control action id",
            }
        )
        actions_raw = {}

    for raw_action, raw_entry in sorted(actions_raw.items(), key=lambda item: _normalize_text(item[0])):
        action = _normalize_text(raw_action)
        if not action:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_policy_schema"],
                    "field": "actions",
                    "message": "actions keys must be non-empty control action ids",
                }
            )
            continue
        if not isinstance(raw_entry, Mapping):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_policy_schema"],
                    "field": f"actions.{action}",
                    "message": f"actions.{action} must be an object",
                }
            )
            continue
        for field_name in sorted(raw_entry.keys()):
            if field_name in ACTION_ENTRY_FIELDS:
                continue
            errors.append(
                {
                    "code": ERROR_CODES["invalid_policy_schema"],
                    "field": f"actions.{action}.{field_name}",
                    "message": f"actions.{action} contains unsupported field: {field_name}",
                }
            )

    for action, entry in (normalized.get("actions") or {}).items():
        state = _normalize_text(entry.get("policy_state")).lower()
        rollout_state = _normalize_text(entry.get("rollout_state")).lower() or "live"
        note = entry.get("note")
        if state not in POLICY_STATE_SET:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_policy_state"],
                    "field": f"actions.{action}.policy_state",
                    "message": f"actions.{action}.policy_state must be one of: disabled, enabled, unsupported",
                }
            )
        if rollout_state not in ROLLOUT_STATE_SET:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_rollout_state"],
                    "field": f"actions.{action}.rollout_state",
                    "message": f"actions.{action}.rollout_state must be one of: hidden, live, staged",
                }
            )
        if not isinstance(note, str):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_policy_schema"],
                    "field": f"actions.{action}.note",
                    "message": f"actions.{action}.note must be a string",
                }
            )
        else:
            entry["rollout_state"] = rollout_state
            entry["note"] = note.strip()

    computed_hash = compute_control_action_policy_hash(normalized)
    stored_hash = _normalize_text(normalized.get("policy_hash"))
    if not stored_hash.startswith(HASH_PREFIX):
        errors.append(
            {
                "code": ERROR_CODES["invalid_policy_hash"],
                "field": "policy_hash",
                "message": "policy_hash must use sha256:<hex> format",
            }
        )
    elif stored_hash != computed_hash:
        errors.append(
            {
                "code": ERROR_CODES["invalid_policy_hash"],
                "field": "policy_hash",
                "message": "policy_hash does not match canonical control action policy payload",
            }
        )

    normalized.update(
        {
            "actions": _normalize_json_like(normalized.get("actions") if isinstance(normalized.get("actions"), dict) else {}),
        }
    )
    ok = not errors
    return {
        "ok": ok,
        "failure_class": "" if ok else "control_action_policy_validation_failed",
        "errors": errors,
        "warnings": warnings,
        "normalized_policy": normalized,
        "canonical_json": json.dumps({**normalized, "policy_hash": ""}, indent=2, sort_keys=True) + "\n",
        "computed_policy_hash": computed_hash,
    }


def read_control_action_policy(project_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    path = control_action_policy_path(project_id, repo_root=repo_root)
    if not path.exists():
        return {
            "ok": False,
            "code": "control_action_policy_not_found",
            "message": f"control action policy file not found: {_path_display(path, repo_root=_repo_root(repo_root))}",
            "path": _path_display(path, repo_root=_repo_root(repo_root)),
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    validation = validate_control_action_policy(payload, expected_project_id=project_id)
    return {
        "ok": validation["ok"],
        "path": _path_display(path, repo_root=_repo_root(repo_root)),
        "validation": validation,
        "policy": validation["normalized_policy"],
    }


def _contract_action_sets(normalized_contract: Mapping[str, Any]) -> Dict[str, set[str]]:
    project = normalized_contract.get("project") if isinstance(normalized_contract.get("project"), Mapping) else {}
    control = normalized_contract.get("control") if isinstance(normalized_contract.get("control"), Mapping) else {}
    supported = {
        _normalize_text(action)
        for action in list(control.get("intervention_actions") or [])
        if _normalize_text(action)
    }
    unsupported = {
        _normalize_text(action)
        for action in list(control.get("unsupported_actions") or [])
        if _normalize_text(action)
    }
    return {
        "project_id": {_normalize_text(project.get("project_id")).lower()},
        "supported": supported,
        "unsupported": unsupported,
        "declared": supported | unsupported,
    }


def resolve_effective_control_action_policy(
    normalized_contract: Mapping[str, Any],
    raw_policy: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    contract_actions = _contract_action_sets(normalized_contract)
    project_id = next(iter(contract_actions["project_id"]), "")
    validation = validate_control_action_policy(raw_policy or {}, expected_project_id=project_id) if raw_policy else None

    errors: List[Dict[str, str]] = []
    policy = None
    if validation is not None:
        errors.extend(validation["errors"])
        policy = validation["normalized_policy"]
        for action, entry in (policy.get("actions") or {}).items():
            if action not in contract_actions["declared"]:
                errors.append(
                    {
                        "code": ERROR_CODES["unknown_control_action"],
                        "field": f"actions.{action}",
                        "message": f"control action is not declared by experiment_contract.json: {action}",
                    }
                )
                continue
            if action in contract_actions["unsupported"] and entry.get("policy_state") != "unsupported":
                errors.append(
                    {
                        "code": ERROR_CODES["contract_policy_conflict"],
                        "field": f"actions.{action}.policy_state",
                        "message": f"overlay cannot enable or stage contract-unsupported action: {action}",
                    }
                )

    effective_actions: Dict[str, Dict[str, Any]] = {}
    for action in sorted(contract_actions["declared"]):
        if action in contract_actions["supported"]:
            effective_actions[action] = {
                "contract_state": "supported",
                "policy_state": "enabled",
                "effective_state": "enabled",
                "rollout_state": "live",
                "note": "",
                "policy_source": "contract_default",
            }
        else:
            effective_actions[action] = {
                "contract_state": "unsupported",
                "policy_state": "unsupported",
                "effective_state": "unsupported",
                "rollout_state": "hidden",
                "note": "",
                "policy_source": "contract_ceiling",
            }

    if policy is not None:
        for action, entry in sorted((policy.get("actions") or {}).items()):
            if action not in effective_actions:
                continue
            contract_state = effective_actions[action]["contract_state"]
            policy_state = _normalize_text(entry.get("policy_state")).lower()
            rollout_state = _normalize_text(entry.get("rollout_state")).lower() or "live"
            note = _normalize_text(entry.get("note"))
            if contract_state == "unsupported":
                policy_state = "unsupported"
                rollout_state = "hidden"
            effective_actions[action] = {
                "contract_state": contract_state,
                "policy_state": policy_state,
                "effective_state": policy_state if contract_state == "supported" else "unsupported",
                "rollout_state": rollout_state,
                "note": note,
                "policy_source": "overlay",
            }

    supported_actions = [action for action, row in effective_actions.items() if row["effective_state"] == "enabled"]
    blocked_actions = [action for action, row in effective_actions.items() if row["effective_state"] != "enabled"]
    effective_policy = {
        "schema_version": EFFECTIVE_CONTROL_ACTION_POLICY_SCHEMA_VERSION,
        "project_id": project_id,
        "contract_hash": _normalize_text(normalized_contract.get("contract_hash")),
        "policy_hash": "" if policy is None else _normalize_text(policy.get("policy_hash")),
        "actions": effective_actions,
        "supported_actions": supported_actions,
        "blocked_actions": blocked_actions,
    }
    return {
        "ok": not errors,
        "errors": errors,
        "effective_policy": effective_policy,
        "policy_validation": validation,
    }
