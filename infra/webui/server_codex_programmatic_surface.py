"""Codex-facing durable task/run surface and migration guards.

This module is the narrow contract for Codex-managed durable work:
- register Codex work in the canonical task register
- launch Codex runs against canonical task/run truth
- write Codex checkpoint evidence back into canonical truth
- fail closed on direct managed-state mutation unless an explicit migration
  exception is allowlisted
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.webui import server_lab_assistant_execution_truth_ownership as _truth_ownership
from infra.webui import server_lab_assistant_task_register as _register


CODEX_SURFACE_EVENT_SCHEMA_VERSION = "lab_assistant_codex_surface_event_v1"
CODEX_SURFACE_PAYLOAD_SCHEMA_VERSION = "lab_assistant_codex_surface_payload_v1"
CODEX_MIGRATION_EXCEPTION_SCHEMA_VERSION = "codex_migration_exceptions_v1"
CODEX_MIGRATION_GUARD_EVENT_SCHEMA_VERSION = "lab_assistant_codex_migration_guard_event_v1"
CODEX_SURFACE_UID = "PIS-20260327-132825Z-0EEC"
_LOCK = threading.RLock()
_MANAGED_STATE_PREFIXES = (
    "runtime/state/lab_assistant/task_register/",
    "runtime/state/lab_assistant/writeback/",
    "runtime/state/lab_assistant/detached_jobs/",
    "governance/agents/runtime/scheduled_jobs/",
)


class CodexProgrammaticSurfaceError(RuntimeError):
    """Raised when the Codex surface cannot update canonical platform truth."""


class CodexMigrationGuardError(CodexProgrammaticSurfaceError):
    """Raised when a direct state mutation bypasses the Codex surface contract."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _repo_root(repo_root: Path) -> Path:
    return Path(repo_root).resolve()


def _surface_root(repo_root: Path) -> Path:
    path = (_repo_root(repo_root) / "runtime" / "state" / "lab_assistant" / "codex_surface").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _events_path(repo_root: Path) -> Path:
    return (_surface_root(repo_root) / "events.jsonl").resolve()


def _migration_events_path(repo_root: Path) -> Path:
    return (_surface_root(repo_root) / "migration_guard_events.jsonl").resolve()


def _payload_dir(repo_root: Path) -> Path:
    path = (_surface_root(repo_root) / "payloads").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _migration_exceptions_path(repo_root: Path) -> Path:
    return (_repo_root(repo_root) / "governance" / "codex_migration_exceptions.json").resolve()


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


def _to_repo_rel(repo_root: Path, path_value: Any) -> str:
    text = _clean_text(path_value)
    if not text:
        return ""
    root = _repo_root(repo_root)
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except Exception:
            return candidate.resolve().as_posix()
    return candidate.as_posix().lstrip("./")


def _is_managed_state_target(repo_root: Path, target_path: Any) -> bool:
    rel = _to_repo_rel(repo_root, target_path)
    return any(rel.startswith(prefix) for prefix in _MANAGED_STATE_PREFIXES)


def _load_migration_exceptions(repo_root: Path) -> Dict[str, Any]:
    path = _migration_exceptions_path(repo_root)
    if not path.exists():
        return {"schema_version": CODEX_MIGRATION_EXCEPTION_SCHEMA_VERSION, "entries": []}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CodexProgrammaticSurfaceError(f"migration exceptions file is malformed: {path}") from exc
    if not isinstance(obj, dict):
        raise CodexProgrammaticSurfaceError("migration exceptions file must be a JSON object")
    entries = obj.get("entries")
    if not isinstance(entries, list):
        raise CodexProgrammaticSurfaceError("migration exceptions file must contain an entries list")
    return {
        "schema_version": _clean_text(obj.get("schema_version")) or CODEX_MIGRATION_EXCEPTION_SCHEMA_VERSION,
        "entries": [dict(row) for row in entries if isinstance(row, dict)],
    }


def _match_migration_exception(repo_root: Path, caller_path: Any) -> Dict[str, Any]:
    rel = _to_repo_rel(repo_root, caller_path)
    if not rel:
        return {}
    for row in list(_load_migration_exceptions(repo_root).get("entries") or []):
        if _to_repo_rel(repo_root, row.get("script_path")) == rel:
            return dict(row)
    return {}


def _append_surface_event(
    repo_root: Path,
    *,
    event: str,
    writer: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    row = {
        "schema_version": CODEX_SURFACE_EVENT_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "event": _clean_text(event),
        "utc": _utc_now_iso(),
        "uid": CODEX_SURFACE_UID,
        "writer": _clean_text(writer),
        "payload": dict(payload or {}),
    }
    _append_jsonl(_events_path(repo_root), row)
    return row


def _append_migration_guard_event(
    repo_root: Path,
    *,
    writer: str,
    operation: str,
    target_path: str,
    caller_path: str,
    task_id: str,
    status: str,
    reason: str,
    exception_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row = {
        "schema_version": CODEX_MIGRATION_GUARD_EVENT_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "utc": _utc_now_iso(),
        "uid": CODEX_SURFACE_UID,
        "writer": _clean_text(writer),
        "operation": _clean_text(operation),
        "target_path": _clean_text(target_path),
        "caller_path": _clean_text(caller_path),
        "task_id": _clean_text(task_id),
        "status": _clean_text(status),
        "reason": _clean_text(reason),
        "migration_exception": dict(exception_row or {}),
    }
    _append_jsonl(_migration_events_path(repo_root), row)
    return row


def _lookup_task_run(repo_root: Path, *, task_id: str, run_id: str) -> Dict[str, Any]:
    snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    task = next((dict(row) for row in list(register.get("active_tasks") or []) if _clean_text(row.get("task_id")) == _clean_text(task_id)), None)
    if not task:
        raise CodexProgrammaticSurfaceError(f"task not found for codex evidence writeback: {task_id}")
    run = next((dict(row) for row in list(task.get("task_runs") or []) if _clean_text(row.get("run_id")) == _clean_text(run_id)), None)
    if not run:
        raise CodexProgrammaticSurfaceError(f"run not found for codex evidence writeback: {run_id}")
    return run


def _store_payload_record(
    repo_root: Path,
    *,
    task_id: str,
    run_id: str,
    writer: str,
    payload: Dict[str, Any],
) -> str:
    event_id = str(uuid.uuid4())
    file_name = f"{_clean_text(task_id) or 'task'}__{_clean_text(run_id) or 'run'}__{event_id}.json"
    path = (_payload_dir(repo_root) / file_name).resolve()
    record = {
        "schema_version": CODEX_SURFACE_PAYLOAD_SCHEMA_VERSION,
        "payload_id": event_id,
        "uid": CODEX_SURFACE_UID,
        "task_id": _clean_text(task_id),
        "run_id": _clean_text(run_id),
        "writer": _clean_text(writer),
        "created_utc": _utc_now_iso(),
        "payload": dict(payload or {}),
    }
    _write_json_atomic(path, record)
    return _to_repo_rel(repo_root, path)


def register_codex_task(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    """Register Codex-managed work in canonical task truth.

    The caller supplies the next expected task-register generation and a task
    payload compatible with the canonical task register. The surface adds the
    Codex source marker and records a durable surface event so the contract is
    visible in append-only truth.
    """
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_TASK_WRITEBACK,
        writer=writer,
    )
    payload = dict(task or {})
    payload.setdefault("source", "codex_surface")
    payload.setdefault("owner", "assistant")
    result = _register.upsert_task(
        repo_root,
        expected_generation=int(expected_generation),
        writer=writer,
        task=payload,
    )
    task_register = dict(result.get("task_register") or {})
    task_id = _clean_text(payload.get("task_id"))
    _append_surface_event(
        repo_root,
        event="task_registered",
        writer=writer,
        payload={
            "task_id": task_id,
            "ledger_uid": _clean_text(payload.get("ledger_uid")),
            "write_generation": int(task_register.get("write_generation") or 0),
        },
    )
    return {"ok": True, "task_id": task_id, "task_register": task_register}


def launch_codex_run(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    run_id: str,
    execution_ref: str,
    binding_ref: str,
    job_uri: str,
    trace_id: str,
    reference_status: str = "execution_ref_active",
    reference_status_reason: str = "codex_surface_launch",
) -> Dict[str, Any]:
    """Attach a Codex run to canonical execution-surface ownership.

    This is the supported launch path for durable Codex work. It records the
    selected execution surface, worker reference, and binding reference in the
    same task/run truth used by operator-managed work.
    """
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_TASK_WRITEBACK,
        writer=writer,
    )
    result = _register.record_execution_surface_ownership(
        repo_root,
        expected_generation=int(expected_generation),
        writer=writer,
        task_id=_clean_text(task_id),
        selected_surface="codex_worker",
        execution_id=_clean_text(run_id),
        execution_ref=_clean_text(execution_ref),
        binding_ref=_clean_text(binding_ref),
        job_uri=_clean_text(job_uri),
        trace_id=_clean_text(trace_id),
        reference_status=_clean_text(reference_status),
        reference_status_reason=_clean_text(reference_status_reason),
    )
    task_register = dict(result.get("task_register") or {})
    _append_surface_event(
        repo_root,
        event="run_launched",
        writer=writer,
        payload={
            "task_id": _clean_text(task_id),
            "run_id": _clean_text(run_id),
            "selected_surface": "codex_worker",
            "execution_ref": _clean_text(execution_ref),
            "binding_ref": _clean_text(binding_ref),
            "trace_id": _clean_text(trace_id),
            "write_generation": int(task_register.get("write_generation") or 0),
        },
    )
    return {"ok": True, "run_id": _clean_text(run_id), "task_register": task_register}


def write_codex_run_evidence(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    run_id: str,
    summary: str,
    evidence_refs: Optional[List[Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
    checkpoint_status: str = "recorded",
    progress_percent: Optional[float] = None,
) -> Dict[str, Any]:
    """Write Codex checkpoint evidence into canonical run truth.

    Evidence refs are filtered through the existing durable-truth ownership
    policy. Structured payloads are written as durable payload records and then
    linked back through the canonical checkpoint row.
    """
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_TASK_WRITEBACK,
        writer=writer,
    )
    current_run = _lookup_task_run(repo_root, task_id=_clean_text(task_id), run_id=_clean_text(run_id))
    checkpoint = dict(current_run.get("checkpoint") or {})
    next_revision = int(checkpoint.get("checkpoint_revision") or 0) + 1
    durable_refs = _truth_ownership.filter_durable_evidence_refs(list(evidence_refs or []))
    payload_ref = ""
    if isinstance(payload, dict) and payload:
        payload_ref = _store_payload_record(
            repo_root,
            task_id=_clean_text(task_id),
            run_id=_clean_text(run_id),
            writer=writer,
            payload=payload,
        )
        durable_refs = _truth_ownership.filter_durable_evidence_refs(durable_refs + [payload_ref])
    normalized_status = _clean_text(checkpoint_status).lower() or "recorded"
    checkpoint_row = {
        "schema_version": _register.TASK_RUN_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": _clean_text(checkpoint.get("checkpoint_id")) or f"codex:{_clean_text(run_id)}",
        "checkpoint_status": normalized_status,
        "checkpoint_utc": _utc_now_iso(),
        "checkpoint_note": _clean_text(summary),
        "checkpoint_source": _clean_text(writer),
        "checkpoint_revision": next_revision,
        "progress_percent": progress_percent,
        "progress_summary": _clean_text(summary),
        "evidence_refs": durable_refs,
    }
    result = _register.record_task_run_checkpoint(
        repo_root,
        expected_generation=int(expected_generation),
        writer=writer,
        task_id=_clean_text(task_id),
        run_id=_clean_text(run_id),
        checkpoint=checkpoint_row,
        auto_decide=True,
    )
    task_register = dict(result.get("task_register") or {})
    _append_surface_event(
        repo_root,
        event="run_evidence_written",
        writer=writer,
        payload={
            "task_id": _clean_text(task_id),
            "run_id": _clean_text(run_id),
            "checkpoint_revision": next_revision,
            "checkpoint_status": normalized_status,
            "evidence_refs": durable_refs,
            "payload_ref": payload_ref,
            "write_generation": int(task_register.get("write_generation") or 0),
        },
    )
    return {
        "ok": True,
        "run_id": _clean_text(run_id),
        "payload_ref": payload_ref,
        "evidence_refs": durable_refs,
        "task_register": task_register,
    }


def enforce_codex_migration_guard(
    repo_root: Path,
    *,
    writer: str,
    operation: str,
    target_path: str,
    caller_path: str = "",
    task_id: str = "",
) -> Dict[str, Any]:
    """Reject silent bypass of managed truth, or record an explicit exception.

    Direct mutation of managed state paths is blocked unless the caller is
    allowlisted in the migration exceptions file. Both allowed exceptions and
    rejected bypasses are written to durable migration-guard event truth.
    """
    repo_root = _repo_root(repo_root)
    target_rel = _to_repo_rel(repo_root, target_path)
    caller_rel = _to_repo_rel(repo_root, caller_path)
    if not _is_managed_state_target(repo_root, target_rel):
        return {
            "ok": True,
            "guarded": False,
            "target_path": target_rel,
            "caller_path": caller_rel,
            "migration_exception": {},
        }
    exception_row = _match_migration_exception(repo_root, caller_rel)
    if exception_row:
        event = _append_migration_guard_event(
            repo_root,
            writer=writer,
            operation=operation,
            target_path=target_rel,
            caller_path=caller_rel,
            task_id=task_id,
            status="migration_exception",
            reason=_clean_text(exception_row.get("reason")) or "allowlisted legacy migration exception",
            exception_row=exception_row,
        )
        return {
            "ok": True,
            "guarded": True,
            "target_path": target_rel,
            "caller_path": caller_rel,
            "migration_exception": dict(exception_row),
            "event": event,
        }
    reason = "direct state mutation of managed platform truth must use the Codex surface API"
    _append_migration_guard_event(
        repo_root,
        writer=writer,
        operation=operation,
        target_path=target_rel,
        caller_path=caller_rel,
        task_id=task_id,
        status="rejected",
        reason=reason,
        exception_row={},
    )
    raise CodexMigrationGuardError(
        f"codex migration guard rejected {operation or 'mutation'} of {target_rel or '<missing>'} "
        f"from {caller_rel or '<missing>'}: {reason}"
    )


def read_codex_surface_events(repo_root: Path) -> List[Dict[str, Any]]:
    path = _events_path(repo_root)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = _clean_text(line)
        if not text:
            continue
        try:
            row = json.loads(text)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_codex_migration_events(repo_root: Path) -> List[Dict[str, Any]]:
    path = _migration_events_path(repo_root)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = _clean_text(line)
        if not text:
            continue
        try:
            row = json.loads(text)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows
