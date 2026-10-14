"""E2F8 delegated outcome writeback into canonical BD05 task truth."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from infra.webui import server_lab_assistant_execution_truth_ownership as _truth_ownership
from infra.webui import server_lab_assistant_task_register as _register


WRITEBACK_RECORD_SCHEMA_VERSION = "lab_assistant_task_writeback_record_v1"
WRITEBACK_EVENT_SCHEMA_VERSION = "lab_assistant_task_writeback_event_v1"
WRITEBACK_SNAPSHOT_SCHEMA_VERSION = "lab_assistant_task_writeback_snapshot_v1"
TASK_MARKER_SCHEMA_VERSION = "lab_assistant_task_writeback_marker_v1"

ASSIMILATION_STATES = {
    "pending_assimilation",
    "ambiguous_assimilation",
    "failed_assimilation",
    "successful_assimilation",
}
_PENDING_STATUSES = {"accepted", "running", "waiting_on_job", "waiting_on_verification"}
_AMBIGUOUS_STATUSES = {"interrupted", "timed_out", "ambiguous_artifact", "evidence_missing", "superseded"}
_TERMINAL_STATUSES = {"completed", "failed"}
_LOCK = threading.RLock()
class TaskWritebackError(RuntimeError):
    """Raised when delegated outcome writeback cannot be reconciled honestly."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _writeback_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "writeback").resolve()


def _records_dir(repo_root: Path) -> Path:
    path = (_writeback_root(repo_root) / "records").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _events_path(repo_root: Path) -> Path:
    return (_writeback_root(repo_root) / "writeback_events.jsonl").resolve()


def _snapshot_path(repo_root: Path) -> Path:
    return (_writeback_root(repo_root) / "writeback_snapshot.json").resolve()


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in _clean_text(value)).strip("-") or "x"


def _record_path(repo_root: Path, task_id: str) -> Path:
    return (_records_dir(repo_root) / f"{_safe_name(task_id)}.json").resolve()


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


def _read_records(repo_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(_records_dir(repo_root).glob("*.json"), key=lambda row: row.name):
        row = _read_json(path)
        if row:
            rows.append(row)
    rows.sort(key=lambda row: (_clean_text(row.get("last_attempt_utc")), _clean_text(row.get("task_id"))))
    return rows


def _result_fingerprint(latest: Dict[str, Any]) -> str:
    payload = {
        "status": _clean_text(latest.get("status")).lower(),
        "native_status": _clean_text(latest.get("native_status")).lower(),
        "summary": _clean_text(latest.get("summary")),
        "request_id": _clean_text(latest.get("delegation_request_id") or latest.get("request_id")),
        "surface": _clean_text(latest.get("surface")).lower(),
        "subsystem_id": _clean_text(latest.get("subsystem_id")),
        "detached_job_id": _clean_text(latest.get("detached_job_id")),
        "evidence_status": _clean_text(latest.get("evidence_status")).lower(),
        "evidence_refs": sorted(_clean_text(row) for row in list(latest.get("evidence_refs") or []) if _clean_text(row)),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _marker_core(marker: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in dict(marker or {}).items() if key != "updated_utc"}


def _should_retire_task_after_writeback(*, task: Dict[str, Any], decision: Dict[str, str]) -> bool:
    return bool(
        _register.is_internal_helper_task(task)
        and _clean_text(decision.get("assimilation_status")) == "successful_assimilation"
        and _clean_text(decision.get("task_status")).lower() in _TERMINAL_STATUSES
    )


def _build_marker(
    *,
    task_status: str,
    latest: Dict[str, Any],
    assimilation_status: str,
    decision_reason: str,
    now_utc: str,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = {
        "schema_version": TASK_MARKER_SCHEMA_VERSION,
        "assimilation_status": assimilation_status,
        "canonical_task_status": _clean_text(task_status),
        "latest_delegation_status": _clean_text(latest.get("status")).lower(),
        "decision_reason": _clean_text(decision_reason),
        "decision_summary": _clean_text(latest.get("summary")),
        "delegation_request_id": _clean_text(latest.get("delegation_request_id") or latest.get("request_id")),
        "correlation_id": _clean_text(latest.get("correlation_id")),
        "surface": _clean_text(latest.get("surface")).lower(),
        "subsystem_id": _clean_text(latest.get("subsystem_id")),
        "detached_job_id": _clean_text(latest.get("detached_job_id")),
        "evidence_status": _clean_text(latest.get("evidence_status")).lower(),
        "evidence_refs": [_clean_text(row) for row in list(latest.get("evidence_refs") or []) if _clean_text(row)],
        "result_fingerprint": _result_fingerprint(latest),
    }
    previous = _marker_core(existing or {})
    out = dict(base)
    out["updated_utc"] = _clean_text((existing or {}).get("updated_utc")) if previous == base else now_utc
    return out


def _ensure_loop(
    loops: List[Dict[str, Any]],
    *,
    kind: str,
    task_id: str,
    ledger_uid: str,
    note: str,
    active: bool,
    now_utc: str,
) -> Tuple[List[Dict[str, Any]], bool]:
    loop_id = f"writeback::{task_id or ledger_uid}::{kind}"
    out: List[Dict[str, Any]] = []
    changed = False
    found = False
    for row in loops:
        current = dict(row)
        if _clean_text(current.get("loop_id")) != loop_id:
            out.append(current)
            continue
        found = True
        desired_status = "open" if active else "resolved"
        desired_resolved = "" if active else now_utc
        if (
            _clean_text(current.get("status")) != desired_status
            or _clean_text(current.get("note")) != _clean_text(note)
            or _clean_text(current.get("resolved_utc")) != desired_resolved
        ):
            changed = True
        current["status"] = desired_status
        current["note"] = _clean_text(note)
        current["updated_utc"] = now_utc
        current["resolved_utc"] = desired_resolved
        out.append(current)
    if active and not found:
        changed = True
        out.append(
            {
                "loop_id": loop_id,
                "kind": kind,
                "status": "open",
                "linked_task_id": task_id,
                "linked_ledger_uid": ledger_uid,
                "note": _clean_text(note),
                "next_review_utc": "",
                "created_utc": now_utc,
                "updated_utc": now_utc,
                "resolved_utc": "",
            }
        )
    return out, changed


def _apply_loop_policy(
    loops: List[Dict[str, Any]],
    *,
    task: Dict[str, Any],
    desired_status: str,
    summary: str,
    now_utc: str,
) -> Tuple[List[Dict[str, Any]], bool]:
    updated = list(loops)
    changed = False
    task_id = _clean_text(task.get("task_id"))
    ledger_uid = _clean_text(task.get("ledger_uid"))
    if desired_status == "waiting_on_verification":
        updated, delta = _ensure_loop(
            updated,
            kind="waiting_on_verification",
            task_id=task_id,
            ledger_uid=ledger_uid,
            note=summary or "Verification remains in progress.",
            active=True,
            now_utc=now_utc,
        )
        changed = changed or delta
        updated, delta = _ensure_loop(
            updated,
            kind="blocked_question",
            task_id=task_id,
            ledger_uid=ledger_uid,
            note=summary or "Blocked recovery loop resolved by delegated verification progress.",
            active=False,
            now_utc=now_utc,
        )
        changed = changed or delta
        return updated, changed
    if desired_status == "completed":
        for kind in ("waiting_on_verification", "blocked_question"):
            updated, delta = _ensure_loop(
                updated,
                kind=kind,
                task_id=task_id,
                ledger_uid=ledger_uid,
                note=summary or "Delegated outcome closed this loop.",
                active=False,
                now_utc=now_utc,
            )
            changed = changed or delta
        return updated, changed
    if desired_status in {"failed", "interrupted_recovering"}:
        updated, delta = _ensure_loop(
            updated,
            kind="blocked_question",
            task_id=task_id,
            ledger_uid=ledger_uid,
            note=summary or "Delegated outcome needs assistant review.",
            active=True,
            now_utc=now_utc,
        )
        changed = changed or delta
        updated, delta = _ensure_loop(
            updated,
            kind="waiting_on_verification",
            task_id=task_id,
            ledger_uid=ledger_uid,
            note=summary or "Verification loop closed during delegated outcome reconciliation.",
            active=False,
            now_utc=now_utc,
        )
        changed = changed or delta
        return updated, changed
    for kind in ("waiting_on_verification", "blocked_question"):
        updated, delta = _ensure_loop(
            updated,
            kind=kind,
            task_id=task_id,
            ledger_uid=ledger_uid,
            note=summary or "Delegated outcome remains pending elsewhere.",
            active=False,
            now_utc=now_utc,
        )
        changed = changed or delta
    return updated, changed


def _strong_terminal(latest: Dict[str, Any]) -> bool:
    return _clean_text(latest.get("status")).lower() in _TERMINAL_STATUSES and _clean_text(latest.get("evidence_status")).lower() == "present"


def _decision_for_task(task: Dict[str, Any], latest: Dict[str, Any]) -> Dict[str, str]:
    status = _clean_text(latest.get("status")).lower()
    summary = _clean_text(latest.get("summary"))
    next_expected = _clean_text(((latest.get("next_decision") or {}) if isinstance(latest.get("next_decision"), dict) else {}).get("summary"))
    current_marker = dict(task.get("delegated_outcome_writeback") or {})
    current_status = _clean_text(task.get("status"))
    current_blocked = _clean_text(task.get("blocked_reason"))
    if _strong_terminal(latest):
        desired_status = status
        return {
            "assimilation_status": "successful_assimilation",
            "decision_reason": "terminal_worker_evidence",
            "task_status": desired_status,
            "blocked_reason": summary if desired_status == "failed" else "",
            "next_expected_action": next_expected or _clean_text(task.get("next_expected_action")),
        }
    if (
        _clean_text(current_marker.get("assimilation_status")) == "successful_assimilation"
        and current_status in _TERMINAL_STATUSES
        and status in (_PENDING_STATUSES | _AMBIGUOUS_STATUSES)
    ):
        return {
            "assimilation_status": "successful_assimilation",
            "decision_reason": "retained_stronger_canonical_terminal_truth",
            "task_status": current_status,
            "blocked_reason": current_blocked,
            "next_expected_action": _clean_text(task.get("next_expected_action")),
        }
    if status == "waiting_on_verification":
        return {
            "assimilation_status": "pending_assimilation",
            "decision_reason": "verification_in_progress",
            "task_status": "waiting_on_verification",
            "blocked_reason": "",
            "next_expected_action": next_expected or _clean_text(task.get("next_expected_action")),
        }
    if status in _PENDING_STATUSES:
        return {
            "assimilation_status": "pending_assimilation",
            "decision_reason": "delegated_work_in_progress",
            "task_status": "waiting_on_job",
            "blocked_reason": "",
            "next_expected_action": next_expected or _clean_text(task.get("next_expected_action")),
        }
    if status in _AMBIGUOUS_STATUSES or _clean_text(latest.get("evidence_status")).lower() in {"ambiguous", "missing_result", "missing_native_record"}:
        return {
            "assimilation_status": "ambiguous_assimilation",
            "decision_reason": "delegated_truth_ambiguous",
            "task_status": "interrupted_recovering",
            "blocked_reason": summary,
            "next_expected_action": next_expected or _clean_text(task.get("next_expected_action")),
        }
    return {
        "assimilation_status": "failed_assimilation",
        "decision_reason": "unsupported_delegation_status",
        "task_status": current_status or "active",
        "blocked_reason": current_blocked,
        "next_expected_action": _clean_text(task.get("next_expected_action")),
    }


def _store_record(repo_root: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    _write_json_atomic(_record_path(repo_root, _clean_text(record.get("task_id"))), record)
    return record


def _write_snapshot(repo_root: Path, *, writer: str) -> Dict[str, Any]:
    rows = _read_records(repo_root)
    counts: Dict[str, int] = {key: 0 for key in sorted(ASSIMILATION_STATES)}
    task_counts: Dict[str, int] = {}
    for row in rows:
        state = _clean_text(row.get("assimilation_status"))
        if state in counts:
            counts[state] += 1
        task_status = _clean_text(row.get("task_status_after"))
        if task_status:
            task_counts[task_status] = task_counts.get(task_status, 0) + 1
    payload = {
        "schema_version": WRITEBACK_SNAPSHOT_SCHEMA_VERSION,
        "writer": _clean_text(writer),
        "generated_utc": _utc_now_iso(),
        "record_count": len(rows),
        "assimilation_counts": counts,
        "task_status_counts": task_counts,
        "records": rows,
        "events_path": str(_events_path(repo_root)),
    }
    _write_json_atomic(_snapshot_path(repo_root), payload)
    return payload


def _append_event(repo_root: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        _events_path(repo_root),
        {
            "schema_version": WRITEBACK_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": _clean_text(event_type),
            "utc": _utc_now_iso(),
            "payload": dict(payload or {}),
        },
    )


def read_task_record(repo_root: Path, *, task_id: str) -> Dict[str, Any]:
    return _read_json(_record_path(Path(repo_root).resolve(), task_id))


def read_snapshot(repo_root: Path, *, initialize_if_missing: bool = True) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    snapshot = _read_json(_snapshot_path(repo_root))
    if snapshot:
        return snapshot
    if not initialize_if_missing:
        return {
            "schema_version": WRITEBACK_SNAPSHOT_SCHEMA_VERSION,
            "writer": "",
            "generated_utc": "",
            "record_count": 0,
            "assimilation_counts": {},
            "task_status_counts": {},
            "records": [],
            "events_path": str(_events_path(repo_root)),
        }
    with _LOCK:
        return _write_snapshot(repo_root, writer="task_writeback.read")


def reconcile_task(
    repo_root: Path,
    *,
    task_id: str,
    writer: str,
    bindings_module: Any = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_TASK_WRITEBACK,
        writer=writer,
    )
    target_task_id = _clean_text(task_id)
    if not target_task_id:
        raise TaskWritebackError("task_id is required")
    bindings = bindings_module
    if bindings is None:
        from infra.webui import server_lab_assistant_delegation_bindings as _bindings

        bindings = _bindings
    now_utc = _utc_now_iso()
    proof_report: Dict[str, Any] = {}
    proof_report_error = ""
    with _LOCK:
        existing_record = read_task_record(repo_root, task_id=target_task_id)
        attempt_count = int(existing_record.get("attempt_count") or 0) + 1
        try:
            synthesis = bindings.synthesize_task_status(repo_root, task_id=target_task_id)
            latest = dict(synthesis.get("latest") or {})
        except Exception as exc:
            latest = {
                "status": "interrupted",
                "summary": f"Writeback could not synthesize delegated status: {exc}",
                "next_decision": {"summary": "Inspect native delegation evidence before retrying writeback."},
                "evidence_status": "ambiguous",
                "evidence_refs": [],
                "surface": "",
                "subsystem_id": "",
                "delegation_request_id": "",
            }
            synthesis = {"delegation_refs": []}

        try:
            with _register._LOCK:
                with _register._task_register_lock(repo_root):
                    register = _register._load_or_init_task_register_locked(repo_root)
                    task_bucket = None
                    task = None
                    for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks"):
                        task = next(
                            (dict(row) for row in list(register.get(bucket_name) or []) if _clean_text(row.get("task_id")) == target_task_id),
                            None,
                        )
                        if task is not None:
                            task_bucket = bucket_name
                            break
                    if task is None:
                        raise TaskWritebackError(f"task not found: {target_task_id}")
                    refs = list(task.get("delegation_refs") or [])
                    if not refs:
                        raise TaskWritebackError(f"task {target_task_id} has no delegation refs to assimilate")
                    decision = _decision_for_task(task, latest)
                    previous_status = _clean_text(task.get("status"))
                    next_task = dict(task)
                    next_task["status"] = decision["task_status"]
                    next_task["next_expected_action"] = decision["next_expected_action"]
                    next_task["blocked_reason"] = decision["blocked_reason"]
                    existing_marker = dict(task.get("delegated_outcome_writeback") or {})
                    if (
                        decision["decision_reason"] == "retained_stronger_canonical_terminal_truth"
                        and existing_marker
                    ):
                        marker = existing_marker
                    else:
                        marker = _build_marker(
                            task_status=decision["task_status"],
                            latest=latest,
                            assimilation_status=decision["assimilation_status"],
                            decision_reason=decision["decision_reason"],
                            now_utc=now_utc,
                            existing=existing_marker,
                        )
                    next_task["delegated_outcome_writeback"] = marker
                    task_changed = any(
                        (
                            _clean_text(task.get("status")) != _clean_text(next_task.get("status")),
                            _clean_text(task.get("next_expected_action")) != _clean_text(next_task.get("next_expected_action")),
                            _clean_text(task.get("blocked_reason")) != _clean_text(next_task.get("blocked_reason")),
                            _marker_core(dict(task.get("delegated_outcome_writeback") or {})) != _marker_core(marker),
                        )
                    )
                    if task_changed:
                        next_task["updated_utc"] = now_utc
                    updated_loops, loop_changed = _apply_loop_policy(
                        list(register.get("open_loops") or []),
                        task=next_task,
                        desired_status=decision["task_status"],
                        summary=_clean_text(latest.get("summary")),
                        now_utc=now_utc,
                    )
                    retire_from_active_tasks = _should_retire_task_after_writeback(task=next_task, decision=decision)
                    changed = task_changed or loop_changed or retire_from_active_tasks
                    if changed:
                        if retire_from_active_tasks and task_bucket == "active_tasks":
                            register["active_tasks"] = [
                                row
                                for row in list(register.get("active_tasks") or [])
                                if _clean_text(row.get("task_id")) != target_task_id
                            ]
                        else:
                            register[str(task_bucket)] = [
                                next_task if _clean_text(row.get("task_id")) == target_task_id else row
                                for row in list(register.get(str(task_bucket)) or [])
                            ]
                        register["open_loops"] = updated_loops
                        _register._set_generation(register, writer=writer)
                        _register._append_event(
                            repo_root,
                            event_type="delegated_outcome_writeback_applied",
                            payload={
                                "writer": _clean_text(writer),
                                "task_id": target_task_id,
                                "assimilation_status": decision["assimilation_status"],
                                "latest_delegation_status": _clean_text(latest.get("status")),
                                "decision_reason": decision["decision_reason"],
                                "retired_from_active_tasks": bool(retire_from_active_tasks),
                            },
                        )
                        _register._write_generation_event(repo_root, register=register, writer=writer)
                        summary = _register._write_summary_cache(repo_root, register)
                        _register._append_commit_event(repo_root, register=register, summary=summary, writer=writer)
                        _register._write_json_atomic(_register._register_path(repo_root), register)
                        _register._write_json_atomic(_register._summary_cache_path(repo_root), summary)
                    elif not retire_from_active_tasks:
                        next_task = next(
                            dict(row)
                            for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks")
                            for row in list(register.get(bucket_name) or [])
                            if _clean_text(row.get("task_id")) == target_task_id
                        )
                    record = {
                        "schema_version": WRITEBACK_RECORD_SCHEMA_VERSION,
                        "task_id": target_task_id,
                        "last_writer": _clean_text(writer),
                        "last_attempt_utc": now_utc,
                        "attempt_count": attempt_count,
                        "task_present": True,
                        "delegated": True,
                        "delegation_count": len(refs),
                        "assimilation_status": decision["assimilation_status"],
                        "decision_reason": decision["decision_reason"],
                        "decision_summary": _clean_text(latest.get("summary")),
                        "latest_delegation_status": _clean_text(latest.get("status")),
                        "task_status_before": previous_status,
                        "task_status_after": _clean_text(next_task.get("status")),
                        "blocked_reason_after": _clean_text(next_task.get("blocked_reason")),
                        "delegation_request_id": _clean_text(latest.get("delegation_request_id") or latest.get("request_id")),
                        "correlation_id": _clean_text(latest.get("correlation_id")),
                        "surface": _clean_text(latest.get("surface")),
                        "subsystem_id": _clean_text(latest.get("subsystem_id")),
                        "detached_job_id": _clean_text(latest.get("detached_job_id")),
                        "evidence_status": _clean_text(latest.get("evidence_status")),
                        "evidence_refs": [_clean_text(row) for row in list(latest.get("evidence_refs") or []) if _clean_text(row)],
                        "result_fingerprint": _result_fingerprint(latest),
                        "applied": bool(changed),
                        "retired_from_active_tasks": bool(retire_from_active_tasks),
                        "task_generation": int(register.get("write_generation") or 0),
                    }
                    stored = _store_record(repo_root, record)
        except Exception as exc:
            stored = _store_record(
                repo_root,
                {
                    "schema_version": WRITEBACK_RECORD_SCHEMA_VERSION,
                    "task_id": target_task_id,
                    "last_writer": _clean_text(writer),
                    "last_attempt_utc": now_utc,
                    "attempt_count": attempt_count,
                    "task_present": False,
                    "delegated": bool(list(synthesis.get("delegation_refs") or [])),
                    "delegation_count": len(list(synthesis.get("delegation_refs") or [])),
                    "assimilation_status": "failed_assimilation",
                    "decision_reason": type(exc).__name__,
                    "decision_summary": str(exc),
                    "latest_delegation_status": _clean_text(latest.get("status")),
                    "task_status_before": "",
                    "task_status_after": "",
                    "blocked_reason_after": "",
                    "delegation_request_id": _clean_text(latest.get("delegation_request_id") or latest.get("request_id")),
                    "correlation_id": _clean_text(latest.get("correlation_id")),
                    "surface": _clean_text(latest.get("surface")),
                    "subsystem_id": _clean_text(latest.get("subsystem_id")),
                    "detached_job_id": _clean_text(latest.get("detached_job_id")),
                    "evidence_status": _clean_text(latest.get("evidence_status")),
                    "evidence_refs": [_clean_text(row) for row in list(latest.get("evidence_refs") or []) if _clean_text(row)],
                    "result_fingerprint": _result_fingerprint(latest),
                    "applied": False,
                    "task_generation": -1,
                },
            )
        _append_event(
            repo_root,
            event_type="task_reconciled",
            payload={
                "writer": _clean_text(writer),
                "task_id": target_task_id,
                "assimilation_status": _clean_text(stored.get("assimilation_status")),
                "latest_delegation_status": _clean_text(stored.get("latest_delegation_status")),
                "applied": bool(stored.get("applied")),
                "decision_reason": _clean_text(stored.get("decision_reason")),
            },
        )
        snapshot = _write_snapshot(repo_root, writer=writer)
        try:
            from infra.webui import server_lab_assistant_execution_proof as _proof

            proof_report = _proof.record_execution_proof_report(repo_root, writer=f"{writer}.proof", now_utc=now_utc)
        except Exception as exc:  # Derived proof should not block canonical writeback truth.
            proof_report_error = str(exc)
        return {
            "ok": True,
            "writeback_record": stored,
            "writeback_snapshot": snapshot,
            "proof_report": proof_report,
            "proof_report_error": proof_report_error,
            "task_outcome": {
                "task_id": target_task_id,
                "previous_status": _clean_text(stored.get("task_status_before")),
                "reconciled_status": _clean_text(stored.get("task_status_after")),
                "latest_delegation_status": _clean_text(stored.get("latest_delegation_status")),
                "summary": _clean_text(stored.get("decision_summary")),
                "delegation_request_id": _clean_text(stored.get("delegation_request_id")),
                "surface": _clean_text(stored.get("surface")),
                "subsystem_id": _clean_text(stored.get("subsystem_id")),
                "assimilation_status": _clean_text(stored.get("assimilation_status")),
                "decision_reason": _clean_text(stored.get("decision_reason")),
            },
        }


def reconcile_all(
    repo_root: Path,
    *,
    writer: str,
    bindings_module: Any = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    task_ids = [
        _clean_text(task.get("task_id"))
        for task in list(register.get("active_tasks") or [])
        if _clean_text(task.get("task_id")) and list(task.get("delegation_refs") or [])
    ]
    outcomes = [reconcile_task(repo_root, task_id=task_id, writer=writer, bindings_module=bindings_module)["task_outcome"] for task_id in task_ids]
    return {
        "ok": True,
        "task_outcomes": outcomes,
        "writeback_snapshot": read_snapshot(repo_root, initialize_if_missing=True),
    }
