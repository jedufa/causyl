"""L8Q1 backend API boundary and command-surface contract.

Last modified: 2026-03-31 Europe/Vienna
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]


BACKEND_API_BOUNDARY_SCHEMA_VERSION = "lab_assistant_backend_api_boundary_v1"
L8Q1_UID = "PIS-20260313-100600Z-L8Q1"
P2T4_UID = "PIS-20260313-100603Z-P2T4"
DEPENDENCY_UIDS = ["PIS-20260313-100601Z-M9R2"]

COMMAND_STATUS_ACCEPTED = "accepted"
COMMAND_STATUS_DUPLICATE_SUPPRESSED = "duplicate_suppressed"
COMMAND_STATUS_AMBIGUOUS_TIMEOUT = "ambiguous_timeout"
COMMAND_STATUS_REJECTED = "rejected"
COMMAND_STATUS_BACKEND_UNAVAILABLE = "backend_unavailable"

REASON_UNSUPPORTED_COMMAND = "unsupported_command"
REASON_INVALID_REQUEST = "invalid_request"
REASON_BACKEND_UNREACHABLE = "backend_unreachable"
REASON_DUPLICATE_SUPPRESSED = "duplicate_suppressed"
REASON_IDEMPOTENCY_CONFLICT = "idempotency_conflict"
REASON_AMBIGUOUS_TIMEOUT = "ambiguous_timeout"
REASON_FORBIDDEN_SCOPE = "forbidden_scope"
REASON_PROHIBITED_INTENT = "prohibited_intent"
REASON_MISSING_IDEMPOTENCY_KEY = "missing_idempotency_key"

IDEMPOTENCY_STATE_SCHEMA_VERSION = "backend_command_idempotency_state_v1"
COMMAND_EVENT_SCHEMA_VERSION = "backend_command_event_v1"
_LOCK = threading.RLock()

READ_SURFACES = (
    {
        "surface_id": "health",
        "mutating": False,
        "request_shape": {"selector": "health"},
        "response_schema": "backend_health_v1",
        "truth_note": "Reachability and backend identity only; no mutation side effects.",
    },
    {
        "surface_id": "status",
        "mutating": False,
        "request_shape": {"selector": "status"},
        "response_schema": "backend_status_v1",
        "truth_note": "Read-only projection of scheduler/runtime durable truth.",
    },
    {
        "surface_id": "receipts",
        "mutating": False,
        "request_shape": {"selector": "receipts", "job_id": "required", "limit": "optional"},
        "response_schema": "backend_receipts_v1",
        "truth_note": "Durable execution receipts only; no process-local completion inference.",
    },
)

WRITE_COMMAND_SPECS = {
    "send_turn": {
        "command_id": "send_turn",
        "mutating": True,
        "payload_required_keys": ["binding", "role", "message"],
        "response_schema": "backend_command_result_v1",
        "notes": "Submits one runtime turn request; acceptance does not imply completion.",
        "retry_owner": "backend_boundary_with_idempotency_key",
    },
    "job.trigger": {
        "command_id": "job.trigger",
        "mutating": True,
        "payload_required_keys": ["job_id"],
        "response_schema": "backend_command_result_v1",
        "notes": "Requests immediate execution for an allowed scheduled job.",
        "retry_owner": "backend_boundary_with_idempotency_key",
    },
    "job.suspend": {
        "command_id": "job.suspend",
        "mutating": True,
        "payload_required_keys": ["job_id"],
        "response_schema": "backend_command_result_v1",
        "notes": "Pauses an allowed job controller path.",
        "retry_owner": "backend_boundary_with_idempotency_key",
    },
    "job.resume": {
        "command_id": "job.resume",
        "mutating": True,
        "payload_required_keys": ["job_id"],
        "response_schema": "backend_command_result_v1",
        "notes": "Resumes a previously suspended job controller path.",
        "retry_owner": "backend_boundary_with_idempotency_key",
    },
    "job.cancel": {
        "command_id": "job.cancel",
        "mutating": True,
        "payload_required_keys": ["job_id"],
        "response_schema": "backend_command_result_v1",
        "notes": "Requests cancellation through the scheduler/runtime owner path.",
        "retry_owner": "backend_boundary_with_idempotency_key",
    },
}


ASSISTANT_COMMAND_SPECS = {
    "assistant.repo_search": {
        "command_id": "assistant.repo_search",
        "mutating": False,
        "payload_required_keys": ["query"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Performs allowlisted repo search for authenticated assistant callers.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["repo.read"],
        "allowed_intent_classes": ["read_only"],
    },
    "assistant.ledger_list_active": {
        "command_id": "assistant.ledger_list_active",
        "mutating": False,
        "payload_required_keys": [],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Lists active ledger items for authenticated assistant callers.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["ledger.read"],
        "allowed_intent_classes": ["read_only"],
    },
    "assistant.ledger_read_item": {
        "command_id": "assistant.ledger_read_item",
        "mutating": False,
        "payload_required_keys": ["uid"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Reads one active ledger item for authenticated assistant callers.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["ledger.read"],
        "allowed_intent_classes": ["read_only"],
    },
    "assistant.file_read": {
        "command_id": "assistant.file_read",
        "mutating": False,
        "payload_required_keys": ["path"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Reads one bounded repo file for authenticated assistant callers.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["repo.read"],
        "allowed_intent_classes": ["read_only"],
    },
    "assistant.pis_improvement_trigger": {
        "command_id": "assistant.pis_improvement_trigger",
        "mutating": True,
        "payload_required_keys": ["request"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Launches or resumes the shared PIS-improvement entrypoint contract for authenticated assistant callers.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["ledger.read", "session.open"],
        "allowed_intent_classes": ["isolated_trial"],
    },
    "assistant.execution_session_open": {
        "command_id": "assistant.execution_session_open",
        "mutating": True,
        "payload_required_keys": ["base_commit_sha"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Opens one bounded assistant execution session.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["session.open"],
        "conditional_scopes": {"live": ["repo.write.live"]},
        "allowed_intent_classes": ["isolated_trial"],
    },
    "assistant.execution_session_apply_diff": {
        "command_id": "assistant.execution_session_apply_diff",
        "mutating": True,
        "payload_required_keys": ["session_id", "base_commit_sha", "diff_text", "diff_hash", "expected_changed_files"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Applies one bounded trial diff inside an owned assistant session.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["diff.apply"],
        "conditional_scopes": {"live": ["repo.write.live"]},
        "allowed_intent_classes": ["isolated_trial"],
    },
    "assistant.execution_session_validate": {
        "command_id": "assistant.execution_session_validate",
        "mutating": True,
        "payload_required_keys": ["session_id", "validation"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Runs one allowlisted validation command inside an owned assistant session.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["validation.run"],
        "allowed_intent_classes": ["validation_only"],
    },
    "assistant.execution_session_list_artifacts": {
        "command_id": "assistant.execution_session_list_artifacts",
        "mutating": False,
        "payload_required_keys": ["session_id"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Lists artifacts for an owned assistant session or run.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["artifacts.read"],
        "allowed_intent_classes": ["read_only"],
    },
    "assistant.execution_session_read_artifact": {
        "command_id": "assistant.execution_session_read_artifact",
        "mutating": False,
        "payload_required_keys": ["session_id", "artifact_ref"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Reads one bounded artifact for an owned assistant session.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["artifacts.read"],
        "allowed_intent_classes": ["read_only"],
    },
    "assistant.execution_session_close": {
        "command_id": "assistant.execution_session_close",
        "mutating": True,
        "payload_required_keys": ["session_id"],
        "response_schema": "assistant_bridge_command_result_v1",
        "notes": "Closes one assistant execution session and preserves audit evidence.",
        "retry_owner": "backend_boundary_with_idempotency_key",
        "require_explicit_idempotency_key": True,
        "required_scopes": ["session.open"],
        "allowed_intent_classes": ["isolated_trial"],
    },
}


COMMAND_SPECS = {**WRITE_COMMAND_SPECS, **ASSISTANT_COMMAND_SPECS}


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_payload(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text_list(values: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(values, (list, tuple, set)):
        return out
    for raw in list(values):
        text = _clean_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _state_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "controlplane" / "backend_api_boundary").resolve()


def _idempotency_state_path(repo_root: Path) -> Path:
    return (_state_root(repo_root) / "command_idempotency_state.json").resolve()


def _command_events_path(repo_root: Path) -> Path:
    return (_state_root(repo_root) / "command_events.jsonl").resolve()


def _lock_path(repo_root: Path) -> Path:
    return (_state_root(repo_root) / ".command_boundary.lock").resolve()


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = path.parent / f".{path.name}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _empty_idempotency_state() -> Dict[str, Any]:
    return {
        "schema_version": IDEMPOTENCY_STATE_SCHEMA_VERSION,
        "updated_utc": "",
        "commands_by_key": {},
        "explicit_idempotency_fingerprints": {},
    }


def _read_idempotency_state(repo_root: Path) -> Dict[str, Any]:
    obj = _read_json(_idempotency_state_path(repo_root))
    if not obj:
        return _empty_idempotency_state()
    out = _empty_idempotency_state()
    out["updated_utc"] = _clean_text(obj.get("updated_utc"))
    by_key = obj.get("commands_by_key")
    if isinstance(by_key, dict):
        out["commands_by_key"] = {str(k): dict(v) for k, v in by_key.items() if isinstance(v, dict)}
    explicit = obj.get("explicit_idempotency_fingerprints")
    if isinstance(explicit, dict):
        out["explicit_idempotency_fingerprints"] = {str(k): _clean_text(v) for k, v in explicit.items()}
    return out


def _append_command_event(repo_root: Path, *, event: str, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        _command_events_path(repo_root),
        {
            "schema_version": COMMAND_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": _clean_text(event),
            "utc": _utc_now_iso(),
            "payload": dict(payload),
        },
    )


def _missing_required_payload_keys(payload: Dict[str, Any], required: List[str]) -> List[str]:
    missing: List[str] = []
    for key in list(required or []):
        if not _clean_text(payload.get(key)):
            missing.append(str(key))
    return missing


def command_specs() -> List[Dict[str, Any]]:
    return [dict(WRITE_COMMAND_SPECS[key]) for key in sorted(WRITE_COMMAND_SPECS)]


def assistant_bridge_command_specs() -> List[Dict[str, Any]]:
    return [dict(ASSISTANT_COMMAND_SPECS[key]) for key in sorted(ASSISTANT_COMMAND_SPECS)]


def command_result(
    *,
    request_id: Any,
    command_id: Any,
    status: str,
    reason_code: str = "",
    summary: str = "",
    missing_fields: List[str] | None = None,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema_version": "backend_command_result_v1",
        "utc": _utc_now_iso(),
        "request_id": _clean_text(request_id),
        "command_id": _clean_text(command_id),
        "status": _clean_text(status),
        "ok": status == COMMAND_STATUS_ACCEPTED,
        "reason_code": _clean_text(reason_code),
        "summary": _clean_text(summary),
    }
    if missing_fields:
        payload["missing_fields"] = sorted({_clean_text(item) for item in missing_fields if _clean_text(item)})
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key not in payload:
                payload[key] = value
    return payload


def http_status_for_result(result: Dict[str, Any]) -> int:
    status = _clean_text((result or {}).get("status")).lower()
    reason = _clean_text((result or {}).get("reason_code")).lower()
    if status in {COMMAND_STATUS_ACCEPTED, COMMAND_STATUS_DUPLICATE_SUPPRESSED}:
        return 200
    if status == COMMAND_STATUS_AMBIGUOUS_TIMEOUT:
        return 504
    if status == COMMAND_STATUS_BACKEND_UNAVAILABLE:
        return 503
    if status == COMMAND_STATUS_REJECTED:
        if reason == REASON_IDEMPOTENCY_CONFLICT:
            return 409
        if reason in {REASON_FORBIDDEN_SCOPE, REASON_PROHIBITED_INTENT}:
            return 403
        if reason == REASON_MISSING_IDEMPOTENCY_KEY:
            return 400
        return 400
    return 400


def evaluate_command_request(raw: Dict[str, Any], *, backend_reachable: bool = True) -> Dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    request_id = _clean_text(obj.get("request_id"))
    command_id = _clean_text(obj.get("command_id"))
    payload = _clean_payload(obj.get("payload"))
    explicit_idempotency_key = _clean_text(obj.get("idempotency_key")) or _clean_text(payload.get("idempotency_key"))
    token_scopes = _clean_text_list(obj.get("token_scopes")) or _clean_text_list(payload.get("token_scopes"))
    intent_classification = _clean_text(obj.get("intent_classification")) or _clean_text(payload.get("intent_classification"))

    if not backend_reachable:
        return command_result(
            request_id=request_id,
            command_id=command_id,
            status=COMMAND_STATUS_BACKEND_UNAVAILABLE,
            reason_code=REASON_BACKEND_UNREACHABLE,
            summary="Backend unavailable; command not accepted.",
        )

    spec = COMMAND_SPECS.get(command_id)
    if spec is None:
        return command_result(
            request_id=request_id,
            command_id=command_id,
            status=COMMAND_STATUS_REJECTED,
            reason_code=REASON_UNSUPPORTED_COMMAND,
            summary="Command rejected because the command_id is not in the allowlist.",
        )

    if bool(spec.get("require_explicit_idempotency_key")) and not explicit_idempotency_key:
        return command_result(
            request_id=request_id,
            command_id=command_id,
            status=COMMAND_STATUS_REJECTED,
            reason_code=REASON_MISSING_IDEMPOTENCY_KEY,
            summary="Command rejected because an explicit idempotency_key is required.",
            missing_fields=["idempotency_key"],
        )

    missing = _missing_required_payload_keys(payload, list(spec.get("payload_required_keys") or []))
    if missing:
        return command_result(
            request_id=request_id,
            command_id=command_id,
            status=COMMAND_STATUS_REJECTED,
            reason_code=REASON_INVALID_REQUEST,
            summary="Command rejected because required payload fields are missing.",
            missing_fields=missing,
        )

    required_scopes = _clean_text_list(spec.get("required_scopes"))
    missing_scopes = [scope for scope in required_scopes if scope not in token_scopes]
    conditional_scopes = spec.get("conditional_scopes") if isinstance(spec.get("conditional_scopes"), dict) else {}
    execution_mode = _clean_text(payload.get("execution_mode")).lower()
    if execution_mode and execution_mode in conditional_scopes:
        for scope in _clean_text_list(conditional_scopes.get(execution_mode)):
            if scope not in token_scopes and scope not in missing_scopes:
                missing_scopes.append(scope)
    if missing_scopes:
        return command_result(
            request_id=request_id,
            command_id=command_id,
            status=COMMAND_STATUS_REJECTED,
            reason_code=REASON_FORBIDDEN_SCOPE,
            summary="Command rejected because token capabilities do not satisfy the command scope requirements.",
            extra={"missing_scopes": missing_scopes},
        )

    allowed_intent_classes = _clean_text_list(spec.get("allowed_intent_classes"))
    if allowed_intent_classes and intent_classification not in allowed_intent_classes:
        return command_result(
            request_id=request_id,
            command_id=command_id,
            status=COMMAND_STATUS_REJECTED,
            reason_code=REASON_PROHIBITED_INTENT,
            summary="Command rejected because the request intent classification is not allowed for this command.",
            extra={"intent_classification": intent_classification, "allowed_intent_classes": allowed_intent_classes},
        )

    return command_result(
        request_id=request_id,
        command_id=command_id,
        status=COMMAND_STATUS_ACCEPTED,
        summary="Command accepted by contract validation.",
    )


def _request_identity_hash(*, command_id: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps(
        {"command_id": _clean_text(command_id).lower(), "payload": dict(payload)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotency_identity(raw: Dict[str, Any]) -> Dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    payload = _clean_payload(obj.get("payload"))
    command_id = _clean_text(obj.get("command_id"))
    request_id = _clean_text(obj.get("request_id"))
    explicit = _clean_text(obj.get("idempotency_key")) or _clean_text(payload.get("idempotency_key"))
    identity_hash = _request_identity_hash(command_id=command_id, payload=payload)
    keys: List[str] = [f"request_identity:{identity_hash}"]
    if request_id:
        keys.insert(0, f"request_id:{request_id}")
    if explicit:
        keys.insert(0, f"idempotency_key:{explicit}")
    return {
        "request_id": request_id,
        "command_id": command_id,
        "payload": payload,
        "explicit_idempotency_key": explicit,
        "request_identity_hash": identity_hash,
        "identity_keys": keys,
    }


def process_command_request(
    raw: Dict[str, Any],
    *,
    repo_root: Path,
    backend_reachable: bool = True,
    transport_timed_out: bool = False,
    writer: str = "backend_api_boundary",
) -> Dict[str, Any]:
    """
    Apply P2T4 boundary semantics for mutating commands with durable idempotency.

    This function does not execute command side effects; it classifies acceptance
    and replay safety at the boundary contract.
    """
    validation = evaluate_command_request(raw, backend_reachable=backend_reachable)
    identity = _idempotency_identity(raw)
    now_utc = _utc_now_iso()
    root = Path(repo_root).resolve()
    writer_text = _clean_text(writer)

    if validation["status"] in {COMMAND_STATUS_REJECTED, COMMAND_STATUS_BACKEND_UNAVAILABLE}:
        _append_command_event(
            root,
            event="command_validation_failed",
            payload={
                "writer": writer_text,
                "request_id": _clean_text(validation.get("request_id")),
                "command_id": _clean_text(validation.get("command_id")),
                "status": _clean_text(validation.get("status")),
                "reason_code": _clean_text(validation.get("reason_code")),
            },
        )
        return validation

    with _LOCK:
        _lock_path(root).parent.mkdir(parents=True, exist_ok=True)
        with open(_lock_path(root), "a+", encoding="utf-8") as lock_handle:
            if _fcntl is not None:
                _fcntl.flock(lock_handle.fileno(), _fcntl.LOCK_EX)
            state = _read_idempotency_state(root)
            by_key = state.get("commands_by_key")
            by_key = by_key if isinstance(by_key, dict) else {}
            explicit_by_key = state.get("explicit_idempotency_fingerprints")
            explicit_by_key = explicit_by_key if isinstance(explicit_by_key, dict) else {}

            explicit_key = _clean_text(identity.get("explicit_idempotency_key"))
            identity_hash = _clean_text(identity.get("request_identity_hash"))
            if explicit_key:
                prior_hash = _clean_text(explicit_by_key.get(explicit_key))
                if prior_hash and prior_hash != identity_hash:
                    out = command_result(
                        request_id=identity.get("request_id"),
                        command_id=identity.get("command_id"),
                        status=COMMAND_STATUS_REJECTED,
                        reason_code=REASON_IDEMPOTENCY_CONFLICT,
                        summary="Command rejected: idempotency_key already bound to a different command payload.",
                        extra={
                            "retry_owner": "caller_fix_request",
                            "idempotency_key": explicit_key,
                            "request_identity_hash": identity_hash,
                        },
                    )
                    _append_command_event(
                        root,
                        event="idempotency_conflict",
                        payload={
                            "writer": writer_text,
                            "request_id": _clean_text(identity.get("request_id")),
                            "command_id": _clean_text(identity.get("command_id")),
                            "idempotency_key": explicit_key,
                            "prior_request_identity_hash": prior_hash,
                            "request_identity_hash": identity_hash,
                        },
                    )
                    return out

            existing: Dict[str, Any] | None = None
            for key in list(identity.get("identity_keys") or []):
                row = by_key.get(str(key))
                if isinstance(row, dict):
                    existing = dict(row)
                    break

            if existing is not None:
                existing["duplicate_hits"] = int(existing.get("duplicate_hits") or 0) + 1
                existing["last_seen_utc"] = now_utc
                for key in list(identity.get("identity_keys") or []):
                    by_key[str(key)] = dict(existing)
                state["commands_by_key"] = by_key
                state["updated_utc"] = now_utc
                _write_json_atomic(_idempotency_state_path(root), state)
                _append_command_event(
                    root,
                    event="duplicate_suppressed",
                    payload={
                        "writer": writer_text,
                        "request_id": _clean_text(identity.get("request_id")),
                        "command_id": _clean_text(identity.get("command_id")),
                        "identity_keys": list(identity.get("identity_keys") or []),
                        "duplicate_of_request_id": _clean_text(existing.get("request_id")),
                        "original_status": _clean_text(existing.get("status")),
                    },
                )
                return command_result(
                    request_id=identity.get("request_id"),
                    command_id=identity.get("command_id"),
                    status=COMMAND_STATUS_DUPLICATE_SUPPRESSED,
                    reason_code=REASON_DUPLICATE_SUPPRESSED,
                    summary="Duplicate suppressed by durable idempotency identity.",
                    extra={
                        "retry_owner": "backend_boundary",
                        "duplicate_of_request_id": _clean_text(existing.get("request_id")),
                        "original_status": _clean_text(existing.get("status")),
                        "request_identity_hash": identity_hash,
                        "idempotency_keys": list(identity.get("identity_keys") or []),
                    },
                )

            status = COMMAND_STATUS_AMBIGUOUS_TIMEOUT if transport_timed_out else COMMAND_STATUS_ACCEPTED
            reason_code = REASON_AMBIGUOUS_TIMEOUT if transport_timed_out else ""
            summary = (
                "Transport timeout: backend cannot honestly classify command as accepted or rejected yet."
                if transport_timed_out
                else "Command accepted by contract validation."
            )
            retry_owner = "backend_boundary" if not transport_timed_out else "caller_retry_same_idempotency_key"
            record = {
                "request_id": _clean_text(identity.get("request_id")),
                "command_id": _clean_text(identity.get("command_id")),
                "status": status,
                "reason_code": reason_code,
                "request_identity_hash": identity_hash,
                "idempotency_keys": list(identity.get("identity_keys") or []),
                "first_seen_utc": now_utc,
                "last_seen_utc": now_utc,
                "duplicate_hits": 0,
                "writer": writer_text,
            }
            for key in list(identity.get("identity_keys") or []):
                by_key[str(key)] = dict(record)
            if explicit_key:
                explicit_by_key[explicit_key] = identity_hash
            state["commands_by_key"] = by_key
            state["explicit_idempotency_fingerprints"] = explicit_by_key
            state["updated_utc"] = now_utc
            _write_json_atomic(_idempotency_state_path(root), state)
            _append_command_event(
                root,
                event="command_recorded",
                payload={
                    "writer": writer_text,
                    "request_id": _clean_text(identity.get("request_id")),
                    "command_id": _clean_text(identity.get("command_id")),
                    "status": status,
                    "reason_code": reason_code,
                    "idempotency_keys": list(identity.get("identity_keys") or []),
                },
            )
            return command_result(
                request_id=identity.get("request_id"),
                command_id=identity.get("command_id"),
                status=status,
                reason_code=reason_code,
                summary=summary,
                extra={
                    "retry_owner": retry_owner,
                    "request_identity_hash": identity_hash,
                    "idempotency_keys": list(identity.get("identity_keys") or []),
                    "durable_receipt_path": str(_idempotency_state_path(root)),
                },
            )


def boundary_snapshot() -> Dict[str, Any]:
    return {
        "schema_version": BACKEND_API_BOUNDARY_SCHEMA_VERSION,
        "generated_utc": _utc_now_iso(),
        "uid": L8Q1_UID,
        "depends_on": list(DEPENDENCY_UIDS) + [P2T4_UID],
        "read_surfaces": [dict(row) for row in READ_SURFACES],
        "write_surfaces": command_specs(),
        "assistant_bridge_commands": assistant_bridge_command_specs(),
        "command_response_semantics": {
            COMMAND_STATUS_ACCEPTED: "Command accepted by backend contract checks; execution may still fail later.",
            COMMAND_STATUS_DUPLICATE_SUPPRESSED: "Duplicate retry was suppressed using durable identity keys.",
            COMMAND_STATUS_AMBIGUOUS_TIMEOUT: "Timeout between caller and backend; outcome remains honestly ambiguous.",
            COMMAND_STATUS_REJECTED: "Command rejected with machine-readable reason_code and no mutation.",
            COMMAND_STATUS_BACKEND_UNAVAILABLE: "Backend unreachable; caller must treat command as not accepted.",
        },
        "assistant_bridge_reason_codes": {
            REASON_FORBIDDEN_SCOPE: "Token capabilities are insufficient for the requested assistant bridge command.",
            REASON_PROHIBITED_INTENT: "The request intent classification is not allowed for the requested assistant bridge command.",
            REASON_MISSING_IDEMPOTENCY_KEY: "Assistant bridge commands require an explicit idempotency_key.",
        },
        "idempotency_contract": {
            "scope_uid": P2T4_UID,
            "identity_keys_in_order": ["idempotency_key", "request_id", "request_identity_hash"],
            "durable_state_path": "runtime/state/controlplane/backend_api_boundary/command_idempotency_state.json",
            "events_path": "runtime/state/controlplane/backend_api_boundary/command_events.jsonl",
            "retry_rules": [
                "Retries for mutating commands must reuse the same idempotency_key to remain replay-safe.",
                "Reusing an idempotency_key with different payload is rejected as idempotency_conflict.",
                "When timeout is ambiguous, the boundary preserves uncertainty and never claims success.",
            ],
        },
        "truth_guardrails": [
            "Read surfaces are read-only and must not mutate scheduler/runtime truth.",
            "Mutating commands are explicit and allowlisted by command_id.",
            "The boundary projects backend-owned durable truth and does not create a second canonical store.",
        ],
        "seam_contract": {
            "public_contract_module": "infra.controlplane.backend_api_boundary",
            "web_console_adapter_module": "infra.webui.server_routes_and_main",
            "migration_direction": [
                {
                    "module": "infra.webui.server_agents",
                    "target_root": "tools/backend/runtime/",
                    "owner": "execution_backend",
                },
                {
                    "module": "infra.webui.server_codex_runtime",
                    "target_root": "tools/backend/runtime/",
                    "owner": "execution_backend",
                },
                {
                    "module": "infra.webui.server_agents_orchestrator",
                    "target_root": "tools/backend/runtime/",
                    "owner": "execution_backend",
                },
            ],
        },
    }
