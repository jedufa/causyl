"""Durable append-only audit trail for plane control actions."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from experiments_shared.experiment_records import read_run_record as _read_run_record
from experiments_shared.experiment_records import summarize_run_record as _summarize_run_record
from tools.shared import fsio as _fsio


CONTROL_ACTION_AUDIT_SCHEMA_VERSION = "plane_control_action_audit_event_v1"
_LOCK_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "control_audit").resolve()


def events_path(repo_root: Path) -> Path:
    return (_root(repo_root) / "control_actions.jsonl").resolve()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCK_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _append(path: Path, row: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = dict(row)
    normalized["schema_version"] = CONTROL_ACTION_AUDIT_SCHEMA_VERSION
    normalized["recorded_at_utc"] = _clean_text(normalized.get("recorded_at_utc")) or _fsio.utc_now_iso()
    lock = _lock_for(path)
    with lock:
        _fsio.append_jsonl(path, normalized)
    return normalized


def _clean_text_list(values: Any) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in list(values or []):
        item = _clean_text(raw)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _coerce_mapping(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _build_correlation(
    *,
    correlation: Optional[Mapping[str, Any]],
    actor: Optional[Mapping[str, Any]],
    target: Optional[Mapping[str, Any]],
    policy_snapshot: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    src = _coerce_mapping(correlation)
    actor_map = _coerce_mapping(actor)
    target_map = _coerce_mapping(target)
    policy_map = _coerce_mapping(policy_snapshot)
    experiment_id = _clean_text(
        src.get("experiment_id")
        or src.get("project_id")
        or target_map.get("experiment_id")
        or target_map.get("project_id")
    ).lower()
    run_id = _clean_text(src.get("run_id") or target_map.get("run_id"))
    job_id = _clean_text(src.get("job_id") or target_map.get("job_id"))
    return {
        **src,
        "experiment_id": experiment_id,
        "run_id": run_id,
        "job_id": job_id,
        "run_record_path": _clean_text(src.get("run_record_path") or target_map.get("run_record_path")),
        "run_dir": _clean_text(src.get("run_dir") or target_map.get("run_dir")),
        "policy_hash": _clean_text(src.get("policy_hash") or policy_map.get("policy_hash")),
        "policy_path": _clean_text(src.get("policy_path") or policy_map.get("policy_path")),
        "operator_identity": {
            "kind": _clean_text(src.get("operator_kind") or actor_map.get("kind")),
            "token_id": _clean_text(src.get("token_id") or actor_map.get("token_id")),
            "issuer": _clean_text(src.get("issuer") or actor_map.get("issuer")),
            "session_id": _clean_text(src.get("session_id") or actor_map.get("session_id")),
        },
    }


def _run_record_target(repo_root: Path, row: Mapping[str, Any]) -> Optional[Path]:
    correlation = _coerce_mapping(row.get("correlation"))
    target = _coerce_mapping(row.get("target"))
    params = _coerce_mapping(row.get("params"))
    candidates = [
        correlation.get("run_record_path"),
        target.get("run_record_path"),
        params.get("run_record_path"),
        correlation.get("run_dir"),
        target.get("run_dir"),
        params.get("run_dir"),
    ]
    for raw in candidates:
        text = _clean_text(raw)
        if not text:
            continue
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = (Path(repo_root).resolve() / path).resolve()
        return path
    return None


def _run_truth_summary(repo_root: Path, row: Mapping[str, Any]) -> Dict[str, Any]:
    target = _run_record_target(repo_root, row)
    if target is None:
        return {}
    try:
        return dict(_summarize_run_record(_read_run_record(target)))
    except Exception:
        return {}


def _is_realized_by_run_truth(action_id: str, run_summary: Mapping[str, Any]) -> bool:
    state = _clean_text(run_summary.get("state")).lower()
    terminal = bool(run_summary.get("terminal"))
    if action_id in {"restart", "resume"}:
        return state == "running"
    if action_id in {"graceful_stop", "force_stop"}:
        return terminal
    return False


def _reconciliation_outcome(row: Mapping[str, Any], *, run_summary: Mapping[str, Any], writer: str, now_utc: str) -> Dict[str, Any]:
    decision = _coerce_mapping(row.get("decision"))
    verification = _coerce_mapping(row.get("verification"))
    result = _coerce_mapping(row.get("result"))
    action_id = _clean_text(row.get("action_id")).lower()
    run_state = _clean_text(run_summary.get("state")).lower()
    run_confidence = _clean_text(run_summary.get("confidence")).lower()
    expected_states = _clean_text_list(
        verification.get("expected_lifecycle_states")
        or verification.get("expected_run_states")
        or result.get("expected_lifecycle_states")
        or _coerce_mapping(row.get("correlation")).get("expected_lifecycle_states")
    )

    verification_state = "interrupted_after_restart"
    verification_summary = "Startup reconciliation found a non-final control action and marked it interrupted by restart."
    outcome = "interrupted"
    reason_code = "startup_restart_reconciliation"

    if _clean_text(decision.get("state")).lower() == "denied":
        verification_state = "denied"
        verification_summary = "Startup reconciliation preserved a denied control action from durable audit truth."
        outcome = "denied"
        reason_code = _clean_text(result.get("reason_code")) or "durable_denial_preserved"
    elif run_summary:
        if expected_states and run_state in expected_states:
            verification_state = "realized_state_confirmed"
            verification_summary = f"Startup reconciliation confirmed realized action state from durable run truth: lifecycle.state={run_state}."
            outcome = "realized"
            reason_code = "durable_run_truth_confirmed"
        elif _is_realized_by_run_truth(action_id, run_summary):
            verification_state = "realized_state_confirmed"
            verification_summary = f"Startup reconciliation confirmed {action_id} from durable run truth: lifecycle.state={run_state}."
            outcome = "realized"
            reason_code = "durable_run_truth_confirmed"
        elif expected_states:
            if run_confidence == "uncertain" or run_state == "uncertain_interrupted":
                verification_state = "realization_uncertain_after_restart"
                verification_summary = (
                    "Startup reconciliation found durable run truth, but it remains uncertain and does not prove the requested action realized."
                )
                outcome = "uncertain"
                reason_code = "restart_reconciliation_uncertain"
            else:
                verification_state = "failed"
                verification_summary = (
                    f"Startup reconciliation found durable run truth, but lifecycle.state={run_state or 'unknown'} did not match the expected realized states."
                )
                outcome = "failed"
                reason_code = "realized_state_not_confirmed"

    return {
        "verification": {
            **verification,
            "state": verification_state,
            "summary": verification_summary,
            "verified_at_utc": now_utc,
            "restart_reconciliation_writer": _clean_text(writer) or "server_startup.control_audit",
            "run_truth": dict(run_summary or {}),
        },
        "result": {
            **result,
            "outcome": outcome,
            "reason_code": reason_code,
        },
    }


def _read_rows_strict(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except Exception as exc:  # pragma: no cover - exercised through callers
            raise ValueError(f"malformed JSONL entry in {path} at line {idx}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"expected JSON object in {path} at line {idx}")
        rows.append(obj)
    return rows


def begin_control_action(
    repo_root: Path,
    *,
    action_id: str,
    actor: Optional[Mapping[str, Any]] = None,
    params: Optional[Mapping[str, Any]] = None,
    writer: str = "",
    request_path: str = "",
    target: Optional[Mapping[str, Any]] = None,
    correlation: Optional[Mapping[str, Any]] = None,
    policy_snapshot: Optional[Mapping[str, Any]] = None,
    authorization: Optional[Mapping[str, Any]] = None,
    decision: Optional[Mapping[str, Any]] = None,
    executor: Optional[Mapping[str, Any]] = None,
    verification: Optional[Mapping[str, Any]] = None,
    result: Optional[Mapping[str, Any]] = None,
    requested_at_utc: str = "",
    audit_id: str = "",
) -> Dict[str, Any]:
    request_utc = _clean_text(requested_at_utc) or _fsio.utc_now_iso()
    safe_action = _clean_text(action_id).lower()
    safe_audit_id = _clean_text(audit_id) or f"ctrl_{uuid.uuid4().hex[:16]}"
    row = {
        "audit_id": safe_audit_id,
        "event_type": "control_action_requested",
        "requested_at_utc": request_utc,
        "recorded_at_utc": request_utc,
        "finalized": False,
        "action_id": safe_action,
        "writer": _clean_text(writer) or "server_control_action_audit",
        "request_path": _clean_text(request_path),
        "actor": dict(actor or {}),
        "params": dict(params or {}),
        "target": dict(target or {}),
        "correlation": _build_correlation(
            correlation=correlation,
            actor=actor,
            target=target,
            policy_snapshot=policy_snapshot,
        ),
        "authorization": dict(authorization or {}),
        "decision": dict(decision or {}),
        "executor": dict(executor or {}),
        "verification": dict(verification or {}),
        "result": dict(result or {}),
        "policy_snapshot": dict(policy_snapshot or {}),
    }
    return _append(events_path(repo_root), row)


def finalize_control_action(
    repo_root: Path,
    *,
    audit_id: str,
    action_id: str,
    actor: Optional[Mapping[str, Any]] = None,
    params: Optional[Mapping[str, Any]] = None,
    writer: str = "",
    request_path: str = "",
    target: Optional[Mapping[str, Any]] = None,
    correlation: Optional[Mapping[str, Any]] = None,
    policy_snapshot: Optional[Mapping[str, Any]] = None,
    authorization: Optional[Mapping[str, Any]] = None,
    decision: Optional[Mapping[str, Any]] = None,
    executor: Optional[Mapping[str, Any]] = None,
    verification: Optional[Mapping[str, Any]] = None,
    result: Optional[Mapping[str, Any]] = None,
    requested_at_utc: str = "",
    finalized: bool = True,
) -> Dict[str, Any]:
    row = {
        "audit_id": _clean_text(audit_id),
        "event_type": "control_action_finalized" if finalized else "control_action_progressed",
        "requested_at_utc": _clean_text(requested_at_utc) or _fsio.utc_now_iso(),
        "recorded_at_utc": _fsio.utc_now_iso(),
        "finalized": bool(finalized),
        "action_id": _clean_text(action_id).lower(),
        "writer": _clean_text(writer) or "server_control_action_audit",
        "request_path": _clean_text(request_path),
        "actor": dict(actor or {}),
        "params": dict(params or {}),
        "target": dict(target or {}),
        "correlation": _build_correlation(
            correlation=correlation,
            actor=actor,
            target=target,
            policy_snapshot=policy_snapshot,
        ),
        "authorization": dict(authorization or {}),
        "decision": dict(decision or {}),
        "executor": dict(executor or {}),
        "verification": dict(verification or {}),
        "result": dict(result or {}),
        "policy_snapshot": dict(policy_snapshot or {}),
    }
    return _append(events_path(repo_root), row)


def reconcile_after_restart(repo_root: Path, *, writer: str = "server_startup.control_audit") -> Dict[str, Any]:
    path = events_path(repo_root)
    rows = _read_rows_strict(path)
    latest_by_audit_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        audit_id = _clean_text(row.get("audit_id"))
        if audit_id:
            latest_by_audit_id[audit_id] = row

    reconciled_ids: List[str] = []
    now_utc = _fsio.utc_now_iso()
    for audit_id, last_row in latest_by_audit_id.items():
        if bool(last_row.get("finalized")):
            continue
        run_summary = _run_truth_summary(repo_root, last_row)
        reconciliation = _reconciliation_outcome(last_row, run_summary=run_summary, writer=writer, now_utc=now_utc)
        finalize_control_action(
            repo_root,
            audit_id=audit_id,
            action_id=_clean_text(last_row.get("action_id")),
            actor=dict(last_row.get("actor") or {}),
            params=dict(last_row.get("params") or {}),
            writer=writer,
            request_path=_clean_text(last_row.get("request_path")),
            target=dict(last_row.get("target") or {}),
            correlation=dict(last_row.get("correlation") or {}),
            policy_snapshot={
                **dict(last_row.get("policy_snapshot") or {}),
                "restart_reconciliation": {
                    "reconciled_at_utc": now_utc,
                    "reconciler": _clean_text(writer) or "server_startup.control_audit",
                    "previous_event_type": _clean_text(last_row.get("event_type")),
                    "run_truth": dict(run_summary or {}),
                },
            },
            authorization=dict(last_row.get("authorization") or {}),
            decision=dict(last_row.get("decision") or {}),
            executor={
                **dict(last_row.get("executor") or {}),
                "state": _clean_text(dict(last_row.get("executor") or {}).get("state")) or "interrupted_after_restart",
            },
            verification=reconciliation["verification"],
            result=reconciliation["result"],
            requested_at_utc=_clean_text(last_row.get("requested_at_utc")),
            finalized=True,
        )
        reconciled_ids.append(audit_id)

    return {
        "schema_version": "plane_control_action_audit_reconcile_v1",
        "ok": True,
        "utc": now_utc,
        "reconciled_audit_ids": reconciled_ids,
        "reconciled_count": len(reconciled_ids),
        "events_path": str(path),
    }


def recent_control_action_history(repo_root: Path, *, limit: int = 12) -> List[Dict[str, Any]]:
    rows = _read_rows_strict(events_path(repo_root))
    latest_by_audit_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows:
        audit_id = _clean_text(row.get("audit_id"))
        if not audit_id:
            continue
        if audit_id not in latest_by_audit_id:
            order.append(audit_id)
        latest_by_audit_id[audit_id] = row

    out: List[Dict[str, Any]] = []
    for audit_id in reversed(order[-max(1, int(limit)) :]):
        row = latest_by_audit_id[audit_id]
        actor = dict(row.get("actor") or {})
        out.append(
            {
                "audit_id": audit_id,
                "action_id": _clean_text(row.get("action_id")),
                "requested_at_utc": _clean_text(row.get("requested_at_utc")),
                "recorded_at_utc": _clean_text(row.get("recorded_at_utc")),
                "finalized": bool(row.get("finalized")),
                "request_path": _clean_text(row.get("request_path")),
                "actor": actor,
                "actor_label": _clean_text(actor.get("token_id") or actor.get("issuer") or actor.get("kind")),
                "target": dict(row.get("target") or {}),
                "correlation": dict(row.get("correlation") or {}),
                "authorization": dict(row.get("authorization") or {}),
                "decision": dict(row.get("decision") or {}),
                "executor": dict(row.get("executor") or {}),
                "verification": dict(row.get("verification") or {}),
                "result": dict(row.get("result") or {}),
                "params": dict(row.get("params") or {}),
                "policy_snapshot": dict(row.get("policy_snapshot") or {}),
            }
        )
    return out
