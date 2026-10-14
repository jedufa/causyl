"""5437 restart recovery, durable idempotency, and duplicate suppression."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.controlplane import scheduled_jobs as _scheduled_jobs
from infra.webui import server_lab_assistant_delegation_policy as _policy
from infra.webui import server_lab_assistant_host as _host
from infra.webui import server_lab_assistant_job_lifecycle as _jobs
from infra.webui import server_lab_assistant_state as _state
from infra.webui import server_lab_assistant_task_register as _register
from infra.webui import server_lab_assistant_turns as _turns
from infra.webui import server_lab_assistant_writeback as _writeback


RECOVERY_SNAPSHOT_SCHEMA_VERSION = "lab_assistant_recovery_snapshot_v1"
RECOVERY_EVENT_SCHEMA_VERSION = "lab_assistant_recovery_event_v1"
IDEMPOTENCY_EVENT_SCHEMA_VERSION = "lab_assistant_idempotency_event_v1"


class AssistantRecoveryError(RuntimeError):
    """Raised when 5437 recovery cannot truthfully reconcile state."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_text_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for raw in values:
        text = _clean_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _live_scheduler_job_ids(repo_root: Path) -> set[str]:
    live: set[str] = set()
    for row in _scheduled_jobs.list_jobs(repo_root=repo_root, now_utc=_utc_now_iso()):
        if not isinstance(row, dict):
            continue
        job_id = _clean_text(row.get("job_id"))
        if not job_id:
            continue
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        if bool(row.get("enabled")) or _clean_text(state.get("active_run_id")) or _clean_text(state.get("last_status")).lower() in {
            "running",
            "queued",
            "starting",
            "resuming",
            "workflow_running",
        }:
            live.add(job_id)
    return live


def _mark_task_scheduler_binding_stale(task: Dict[str, Any], *, writer: str, live_job_ids: set[str]) -> bool:
    summary = _register.scheduler_binding_summary(task)
    if not bool(summary.get("has_scheduler_binding")):
        return False
    has_only_scheduler_surfaces = bool(summary.get("has_only_scheduler_surfaces"))
    job_ids = [_clean_text(row) for row in list(summary.get("job_ids") or []) if _clean_text(row)]
    if not job_ids or any(job_id in live_job_ids for job_id in job_ids):
        return False

    changed = False
    stripped_refs: List[Dict[str, Any]] = []
    if list(task.get("delegation_refs") or []):
        kept_refs: List[Dict[str, Any]] = []
        for ref in list(task.get("delegation_refs") or []):
            if not isinstance(ref, dict):
                continue
            current_ref = dict(ref)
            if _clean_text(current_ref.get("surface")).lower() in _register._SCHEDULER_LIKE_SURFACES:
                stripped_refs.append(current_ref)
                continue
            kept_refs.append(current_ref)
        if kept_refs != list(task.get("delegation_refs") or []):
            task["delegation_refs"] = kept_refs
            changed = True

    owner = dict(task.get("execution_surface_ownership") or {})
    if (
        _clean_text(owner.get("selected_surface")).lower() in _register._SCHEDULER_LIKE_SURFACES
        and _clean_text(owner.get("reference_status")).lower() in {"execution_ref_active", "execution_ref_unresolved"}
    ):
        owner["reference_status"] = "execution_ref_stale"
        reason = _clean_text(owner.get("reference_status_reason"))
        stale_reason = "restart recovery found no live scheduler binding for the recorded task ownership"
        owner["reference_status_reason"] = f"{reason} | {stale_reason}".strip(" |")
        owner["updated_utc"] = _utc_now_iso()
        owner["last_writer"] = writer
        task["execution_surface_ownership"] = owner
        changed = True
    elif (
        stripped_refs
        and has_only_scheduler_surfaces
        and _clean_text(owner.get("selected_surface")).lower() not in _register._SCHEDULER_LIKE_SURFACES
    ):
        seed_ref = stripped_refs[0]
        task["execution_surface_ownership"] = {
            "selected_surface": _clean_text(seed_ref.get("surface")).lower() or "scheduler",
            "execution_id": _clean_text(seed_ref.get("subsystem_id")) or _clean_text(seed_ref.get("delegation_request_id")),
            "reference_status": "execution_ref_stale",
            "reference_status_reason": "restart recovery found no live scheduler binding for the recorded task ownership",
            "updated_utc": _utc_now_iso(),
            "last_writer": writer,
        }
        changed = True

    task_runs = [dict(row) for row in list(task.get("task_runs") or []) if isinstance(row, dict)]
    current_run_id = _clean_text(task.get("current_run_id"))
    next_runs: List[Dict[str, Any]] = []
    stale_scheduler_run_present = False
    for run in task_runs:
        if (
            _clean_text(run.get("surface")).lower() in _register._SCHEDULER_LIKE_SURFACES
            and _clean_text(run.get("run_state")).lower() in {"requested", "active", "running", "waiting"}
        ):
            run["run_state"] = "stale"
            run["attention_state"] = _register._run_state_to_attention_state("stale")
            run["updated_utc"] = _utc_now_iso()
            run["writer"] = writer
            run["run_status_source"] = "recovery_missing_scheduler_binding"
            changed = True
        if _clean_text(run.get("surface")).lower() in _register._SCHEDULER_LIKE_SURFACES:
            stale_scheduler_run_present = True
        next_runs.append(run)
    if stripped_refs and has_only_scheduler_surfaces and not stale_scheduler_run_present:
        for index, ref in enumerate(stripped_refs, start=1):
            execution_id = _clean_text(ref.get("subsystem_id")) or _clean_text(ref.get("delegation_request_id"))
            if not execution_id:
                continue
            synthetic_run_id = f"stale_scheduler_binding::{task.get('task_id') or 'task'}::{index}"
            next_runs.append(
                {
                    "run_id": synthetic_run_id,
                    "task_id": _clean_text(task.get("task_id")),
                    "surface": _clean_text(ref.get("surface")).lower() or "scheduler",
                    "run_state": "stale",
                    "attention_state": _register._run_state_to_attention_state("stale"),
                    "execution_id": execution_id,
                    "updated_utc": _utc_now_iso(),
                    "writer": writer,
                    "run_status_source": "recovery_missing_scheduler_binding",
                }
            )
            if not current_run_id:
                task["current_run_id"] = synthetic_run_id
                current_run_id = synthetic_run_id
            changed = True
    if changed and task_runs:
        task["task_runs"] = next_runs
    elif changed and next_runs:
        task["task_runs"] = next_runs
    if changed:
        task["updated_utc"] = _utc_now_iso()
    return changed


def _clear_task_scheduler_binding_residue(task: Dict[str, Any], *, writer: str, live_job_ids: set[str]) -> bool:
    summary = _register.scheduler_binding_summary(task)
    if not bool(summary.get("has_scheduler_binding")):
        return False
    job_ids = [_clean_text(row) for row in list(summary.get("job_ids") or []) if _clean_text(row)]
    if any(job_id in live_job_ids for job_id in job_ids):
        return False

    changed = False
    scheduler_like_surfaces = _register._SCHEDULER_LIKE_SURFACES

    refs = [dict(row) for row in list(task.get("delegation_refs") or []) if isinstance(row, dict)]
    next_refs: List[Dict[str, Any]] = []
    for ref in refs:
        surface = _clean_text(ref.get("surface")).lower()
        if surface not in scheduler_like_surfaces:
            next_refs.append(ref)
            continue
        next_ref = dict(ref)
        next_ref["surface"] = f"{surface}_history"
        next_ref["reference_status"] = "execution_ref_stale"
        next_ref["updated_utc"] = _utc_now_iso()
        next_refs.append(next_ref)
    if next_refs != refs:
        task["delegation_refs"] = next_refs
        changed = True

    owner = dict(task.get("execution_surface_ownership") or {})
    if _clean_text(owner.get("selected_surface")).lower() in scheduler_like_surfaces:
        task.pop("execution_surface_ownership", None)
        changed = True

    current_run_id = _clean_text(task.get("current_run_id"))
    task_runs = [dict(row) for row in list(task.get("task_runs") or []) if isinstance(row, dict)]
    non_scheduler_runs = [
        dict(row)
        for row in task_runs
        if _clean_text(row.get("surface")).lower() not in scheduler_like_surfaces
    ]
    if non_scheduler_runs and len(non_scheduler_runs) != len(task_runs):
        task_runs = non_scheduler_runs
        task["task_runs"] = task_runs
        changed = True
    remaining_run_ids = {_clean_text(row.get("run_id")) for row in task_runs if _clean_text(row.get("run_id"))}
    if current_run_id and current_run_id not in remaining_run_ids:
        next_current_run_id = _clean_text(task_runs[0].get("run_id")) if task_runs else ""
        if next_current_run_id:
            task["current_run_id"] = next_current_run_id
        else:
            task.pop("current_run_id", None)
        changed = True
    elif current_run_id:
        current_run = next(
            (row for row in task_runs if _clean_text(row.get("run_id")) == current_run_id),
            {},
        )
        if _clean_text(dict(current_run).get("surface")).lower() in scheduler_like_surfaces:
            task.pop("current_run_id", None)
            changed = True

    if changed:
        task["updated_utc"] = _utc_now_iso()
        task["last_writer"] = writer
    return changed


def _mark_bucket_scheduler_bindings_stale(
    tasks: List[Dict[str, Any]],
    *,
    writer: str,
    live_job_ids: set[str],
) -> bool:
    changed = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if _mark_task_scheduler_binding_stale(task, writer=writer, live_job_ids=live_job_ids):
            changed = True
    return changed


def _recovery_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "recovery").resolve()


def _snapshot_path(repo_root: Path) -> Path:
    return (_recovery_root(repo_root) / "recovery_snapshot.json").resolve()


def _events_path(repo_root: Path) -> Path:
    return (_recovery_root(repo_root) / "recovery_events.jsonl").resolve()


def _idempotency_journal_path(repo_root: Path) -> Path:
    return (_recovery_root(repo_root) / "idempotency_journal.jsonl").resolve()


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


def _append_recovery_event(repo_root: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        _events_path(repo_root),
        {
            "schema_version": RECOVERY_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": str(event_type),
            "utc": _utc_now_iso(),
            "payload": dict(payload),
        },
    )


def _append_idempotency_event(repo_root: Path, *, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        _idempotency_journal_path(repo_root),
        {
            "schema_version": IDEMPOTENCY_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "utc": _utc_now_iso(),
            **dict(payload),
        },
    )


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _request_identity_payload(request: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _policy.validate_typed_delegation_request(request)
    return {
        "surface": _clean_text(normalized.get("surface")).lower(),
        "owner_ref": dict(normalized.get("owner_ref") or {}),
        "summary": _clean_text(normalized.get("summary")),
        "requested_action": _clean_text(normalized.get("requested_action")),
        "input_payload": dict(normalized.get("input_payload") or {}),
        "expected_evidence": list(normalized.get("expected_evidence") or []),
        "next_decision": dict(normalized.get("next_decision") or {}),
    }


def delegation_idempotency_keys(request: Dict[str, Any]) -> List[str]:
    normalized = _policy.validate_typed_delegation_request(request)
    payload = _request_identity_payload(normalized)
    durable_key = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    keys = [
        f"request_id:{_clean_text(normalized.get('request_id'))}",
        f"idempotency_key:{_clean_text(normalized.get('idempotency_key'))}",
        f"request_identity:{durable_key}",
    ]
    return [key for key in keys if key and not key.endswith(":")]


def attach_idempotency_keys(binding_ref: Dict[str, Any], *, request: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(binding_ref)
    keys = _clean_text_list(list(out.get("idempotency_keys") or []))
    for key in delegation_idempotency_keys(request):
        if key not in keys:
            keys.append(key)
    out["idempotency_keys"] = keys
    return out


def _delegation_ref_keys(ref: Dict[str, Any]) -> List[str]:
    keys = _clean_text_list(list(ref.get("idempotency_keys") or []))
    request_id = _clean_text(ref.get("delegation_request_id"))
    if request_id:
        prefixed = f"request_id:{request_id}"
        if prefixed not in keys:
            keys.append(prefixed)
    return keys


def find_duplicate_delegation(repo_root: Path, *, request: Dict[str, Any], ignore_request_id: str = "") -> Dict[str, Any]:
    normalized = _policy.validate_typed_delegation_request(request)
    wanted = set(delegation_idempotency_keys(normalized))
    snapshot = _register.read_task_register_snapshot(Path(repo_root).resolve(), initialize_if_missing=False)
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    for task in list(register.get("active_tasks") or []):
        for ref in list(task.get("delegation_refs") or []):
            matched = sorted(wanted.intersection(_delegation_ref_keys(ref)))
            if matched:
                return {
                    "duplicate": True,
                    "task": dict(task),
                    "delegation_ref": dict(ref),
                    "matched_keys": matched,
                    "source": "task_register",
                }
    journal_path = _idempotency_journal_path(Path(repo_root).resolve())
    for row in reversed(_read_jsonl(journal_path)):
        matched = sorted(wanted.intersection(_clean_text_list(list(row.get("idempotency_keys") or []))))
        if not matched:
            continue
        if _clean_text(row.get("request_id")) == _clean_text(ignore_request_id):
            continue
        status = _clean_text(row.get("status")).lower()
        if status not in {"launch_observed", "bound", "duplicate_reattached"}:
            continue
        delegation_ref = row.get("delegation_ref") if isinstance(row.get("delegation_ref"), dict) else {}
        if delegation_ref:
            return {
                "duplicate": True,
                "task": {},
                "delegation_ref": dict(delegation_ref),
                "matched_keys": matched,
                "source": "idempotency_journal",
            }
    return {"duplicate": False, "matched_keys": []}


def record_delegation_idempotency(
    repo_root: Path,
    *,
    request: Dict[str, Any],
    status: str,
    writer: str,
    delegation_ref: Optional[Dict[str, Any]] = None,
    matched_keys: Optional[List[str]] = None,
) -> None:
    normalized = _policy.validate_typed_delegation_request(request)
    _append_idempotency_event(
        Path(repo_root).resolve(),
        payload={
            "status": _clean_text(status),
            "writer": _clean_text(writer),
            "request_id": _clean_text(normalized.get("request_id")),
            "surface": _clean_text(normalized.get("surface")).lower(),
            "owner_ref": dict(normalized.get("owner_ref") or {}),
            "matched_keys": _clean_text_list(list(matched_keys or [])),
            "idempotency_keys": delegation_idempotency_keys(normalized),
            "delegation_request_id": _clean_text((delegation_ref or {}).get("delegation_request_id")),
            "subsystem": _clean_text((delegation_ref or {}).get("subsystem")),
            "subsystem_id": _clean_text((delegation_ref or {}).get("subsystem_id")),
            "delegation_ref": dict(delegation_ref or {}),
        },
    )


def read_recovery_snapshot(repo_root: Path) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    snapshot = _read_json(_snapshot_path(root))
    if snapshot:
        return snapshot
    return {
        "schema_version": RECOVERY_SNAPSHOT_SCHEMA_VERSION,
        "recovery_id": "",
        "reconciled_utc": "",
        "summary": "",
        "status": "unknown",
        "counts": {},
        "task_outcomes": [],
        "detached_job_reconciliation": {"ran": False, "changed_job_ids": [], "job_counts": {}, "error": ""},
        "direct_turn_reconciliation": {"ran": False, "changed_turn_ids": [], "status_counts": {}, "error": ""},
        "recovery_note_path": str(_snapshot_path(root)),
        "idempotency_journal_path": str(_idempotency_journal_path(root)),
    }


def record_recovery_failure(
    repo_root: Path,
    *,
    writer: str,
    error: Exception,
    phase: str = "startup_reconcile",
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    try:
        host_health = _host.host_health_snapshot(root, initialize_if_missing=False)
    except Exception as exc:
        host_health = {
            "status": "blocked",
            "mode": "bootstrap_read_failed",
            "summary": str(exc),
            "reasons": [f"host_health_unavailable:{type(exc).__name__}"],
        }
    recovery_id = f"recovery_{uuid.uuid4().hex}"
    error_type = type(error).__name__
    summary = f"Startup recovery failed during {phase}: {error}"
    snapshot = {
        "schema_version": RECOVERY_SNAPSHOT_SCHEMA_VERSION,
        "recovery_id": recovery_id,
        "reconciled_utc": _utc_now_iso(),
        "writer": _clean_text(writer),
        "status": "blocked",
        "summary": summary,
        "continuity_proven": False,
        "host_health": host_health,
        "assistant_id": "",
        "assistant_state_present": False,
        "task_register_present": False,
        "counts": {
            "waiting_on_job": 0,
            "waiting_on_verification": 0,
            "completed": 0,
            "failed": 0,
            "interrupted_recovering": 0,
            "blocked": 1,
        },
        "task_outcomes": [],
        "degraded_reasons": [f"recovery_failure:{phase}:{error_type}"],
        "recovery_note_path": str(_snapshot_path(root)),
        "idempotency_journal_path": str(_idempotency_journal_path(root)),
        "detached_job_reconciliation": {"ran": False, "changed_job_ids": [], "job_counts": {}, "error": ""},
        "direct_turn_reconciliation": {"ran": False, "changed_turn_ids": [], "status_counts": {}, "error": ""},
    }
    _write_json_atomic(_snapshot_path(root), snapshot)
    _append_recovery_event(
        root,
        event_type="startup_reconcile_failed",
        payload={
            "recovery_id": recovery_id,
            "writer": _clean_text(writer),
            "phase": _clean_text(phase),
            "error_type": error_type,
            "error": str(error),
        },
    )
    return snapshot


def _desired_task_status(latest_status: str) -> str:
    value = _clean_text(latest_status).lower()
    if value in {"waiting_on_job", "waiting_on_verification", "completed", "failed"}:
        return value
    if value in {"interrupted", "ambiguous_artifact", "evidence_missing"}:
        return "interrupted_recovering"
    return value or "active"


def _blocked_reason_for_status(latest: Dict[str, Any]) -> str:
    status = _clean_text(latest.get("status")).lower()
    if status in {"failed", "interrupted", "ambiguous_artifact", "evidence_missing"}:
        return _clean_text(latest.get("summary"))
    return ""


def _task_needs_change(task: Dict[str, Any], *, desired_status: str, desired_next: str, desired_blocked_reason: str) -> bool:
    return any(
        (
            _clean_text(task.get("status")) != desired_status,
            _clean_text(task.get("next_expected_action")) != desired_next,
            _clean_text(task.get("blocked_reason")) != desired_blocked_reason,
        )
    )


def _ensure_recovery_loop(
    loops: List[Dict[str, Any]],
    *,
    kind: str,
    task_id: str,
    ledger_uid: str,
    note: str,
    active: bool,
) -> List[Dict[str, Any]]:
    loop_id = f"recovery::{task_id or ledger_uid}::{kind}"
    out: List[Dict[str, Any]] = []
    found = False
    now = _utc_now_iso()
    for row in loops:
        current = dict(row)
        if _clean_text(current.get("loop_id")) != loop_id:
            out.append(current)
            continue
        found = True
        current["status"] = "open" if active else "resolved"
        current["note"] = note
        current["updated_utc"] = now
        current["resolved_utc"] = "" if active else now
        out.append(current)
    if active and not found:
        out.append(
            {
                "loop_id": loop_id,
                "kind": kind,
                "status": "open",
                "linked_task_id": task_id,
                "linked_ledger_uid": ledger_uid,
                "note": note,
                "next_review_utc": "",
                "created_utc": now,
                "updated_utc": now,
                "resolved_utc": "",
            }
        )
    return out


def _apply_register_reconciliation(
    repo_root: Path,
    *,
    register: Dict[str, Any],
    reconciled_tasks: List[Dict[str, Any]],
    reconciled_completed_tasks: List[Dict[str, Any]],
    reconciled_residue_tasks: List[Dict[str, Any]],
    reconciled_loops: List[Dict[str, Any]],
    writer: str,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    with _register._LOCK:
        with _register._task_register_lock(repo_root):
            live = _register._load_or_init_task_register_locked(repo_root)
            if int(live.get("write_generation") or 0) != int(register.get("write_generation") or 0):
                raise AssistantRecoveryError("task register changed during recovery reconciliation")
            next_register = dict(live)
            next_register["active_tasks"] = reconciled_tasks
            next_register["completed_tasks"] = reconciled_completed_tasks
            next_register["residue_tasks"] = reconciled_residue_tasks
            next_register["open_loops"] = reconciled_loops
            _register._set_generation(next_register, writer=writer)
            _register._append_event(
                repo_root,
                event_type="recovery_reconciled",
                payload={
                    "writer": writer,
                    "active_task_count": len(reconciled_tasks),
                    "completed_task_count": len(reconciled_completed_tasks),
                    "residue_task_count": len(reconciled_residue_tasks),
                    "open_loop_count": len(reconciled_loops),
                },
            )
            _register._write_generation_event(repo_root, register=next_register, writer=writer)
            summary = _register._write_summary_cache(repo_root, next_register)
            _register._append_commit_event(repo_root, register=next_register, summary=summary, writer=writer)
            _register._write_json_atomic(_register._register_path(repo_root), next_register)
            _register._write_json_atomic(_register._summary_cache_path(repo_root), summary)
            return next_register


def reconcile_startup(
    repo_root: Path,
    *,
    writer: str = "assistant_recovery.startup",
    bindings_module: Any = None,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    bindings = bindings_module
    if bindings is None:
        from infra.webui import server_lab_assistant_delegation_bindings as _bindings  # local import avoids cycle

        bindings = _bindings

    host_health = _host.host_health_snapshot(root, initialize_if_missing=False)
    assistant_state_error = ""
    register_error = ""
    try:
        assistant_snapshot = _state.read_assistant_state_snapshot(root, initialize_if_missing=False)
        assistant_state = assistant_snapshot.get("assistant_state") if isinstance(assistant_snapshot.get("assistant_state"), dict) else None
    except Exception as exc:
        assistant_state = None
        assistant_state_error = type(exc).__name__
    try:
        register_snapshot = _register.read_task_register_snapshot(root, initialize_if_missing=False)
        register = register_snapshot.get("task_register") if isinstance(register_snapshot.get("task_register"), dict) else None
    except Exception as exc:
        register = None
        register_error = type(exc).__name__

    host_status = _clean_text(host_health.get("status")).lower()
    register_events_exist = _register._events_path(root).exists()
    if (
        assistant_state is not None
        and register is None
        and not register_error
        and register_events_exist
        and host_status not in {"blocked", "degraded"}
    ):
        try:
            repaired_snapshot = _register.read_task_register_snapshot(root, initialize_if_missing=True)
            repaired_register = (
                repaired_snapshot.get("task_register") if isinstance(repaired_snapshot.get("task_register"), dict) else None
            )
            if repaired_register is not None:
                register = repaired_register
        except Exception as exc:
            register_error = type(exc).__name__

    recovery_id = f"recovery_{uuid.uuid4().hex}"
    degraded_reasons = _clean_text_list(list(host_health.get("reasons") or []))
    surface_gaps: List[str] = []
    task_outcomes: List[Dict[str, Any]] = []
    waiting_on_job: List[str] = []
    waiting_on_verification: List[str] = []
    interrupted: List[str] = []
    blocked: List[str] = []
    failed: List[str] = []
    completed: List[str] = []
    assistant_state_present = bool(assistant_state)
    task_register_present = bool(register)
    continuity_proven = bool(host_health.get("status") == "ok")
    detached_job_reconciliation = {
        "ran": False,
        "changed_job_ids": [],
        "job_counts": {},
        "error": "",
    }
    direct_turn_reconciliation = {
        "ran": False,
        "changed_turn_ids": [],
        "status_counts": {},
        "error": "",
    }
    if assistant_state_error:
        degraded_reasons.append(f"assistant_state_unavailable:{assistant_state_error}")
        surface_gaps.append("assistant runtime state unreadable after restart")
    elif not assistant_state_present:
        degraded_reasons.append("assistant_state_missing")
        surface_gaps.append("assistant runtime state missing after restart")
    if register_error:
        degraded_reasons.append(f"task_register_unavailable:{register_error}")
        surface_gaps.append("task register unreadable after restart")
    elif not task_register_present:
        degraded_reasons.append("task_register_missing")
        surface_gaps.append("task register missing after restart")
        if assistant_state is not None and not register_events_exist and host_status not in {"blocked", "degraded"}:
            degraded_reasons.append("canonical_task_register_missing_no_evidence_backed_reconstruction")
            surface_gaps.append("canonical task register missing; recovery refused to synthesize a blank register")
    if surface_gaps:
        continuity_proven = False
    try:
        detached = _jobs.reconcile_jobs(root, writer=f"{writer}.detached_jobs", mode="startup")
        detached_job_reconciliation = {
            "ran": True,
            "changed_job_ids": list(detached.get("changed_job_ids") or []),
            "job_counts": dict((detached.get("job_snapshot") or {}).get("counts") or {}),
            "error": "",
        }
    except Exception as exc:
        detached_job_reconciliation = {
            "ran": False,
            "changed_job_ids": [],
            "job_counts": {},
            "error": str(exc),
        }
        degraded_reasons.append(f"detached_job_reconcile_failed:{type(exc).__name__}")
        surface_gaps.append("detached job reconciliation failed after restart")
        continuity_proven = False
    try:
        turn_reconcile = _turns.reconcile_turns_on_boot(root, writer=f"{writer}.direct_turns")
        direct_turn_reconciliation = {
            "ran": True,
            "changed_turn_ids": list(turn_reconcile.get("changed_turn_ids") or []),
            "status_counts": dict(turn_reconcile.get("status_counts") or {}),
            "error": "",
        }
    except Exception as exc:
        direct_turn_reconciliation = {
            "ran": False,
            "changed_turn_ids": [],
            "status_counts": {},
            "error": str(exc),
        }
        degraded_reasons.append(f"direct_turn_reconcile_failed:{type(exc).__name__}")
        surface_gaps.append("direct turn reconciliation failed after restart")
        continuity_proven = False

    if register:
        reconciled_tasks: List[Dict[str, Any]] = []
        reconciled_completed_tasks: List[Dict[str, Any]] = [dict(task) for task in list(register.get("completed_tasks") or [])]
        reconciled_residue_tasks: List[Dict[str, Any]] = [dict(task) for task in list(register.get("residue_tasks") or [])]
        reconciled_loops: List[Dict[str, Any]] = list(register.get("open_loops") or [])
        register_changed = False
        live_scheduler_job_ids = _live_scheduler_job_ids(root)
        for task in list(register.get("active_tasks") or []):
            current_task = dict(task)
            task_id = _clean_text(current_task.get("task_id"))
            refs = list(current_task.get("delegation_refs") or [])
            if refs:
                writeback_outcome = _writeback.reconcile_task(
                    root,
                    task_id=task_id,
                    writer=f"{writer}.writeback",
                    bindings_module=bindings,
                )["task_outcome"]
                summary = _clean_text(writeback_outcome.get("summary"))
                desired_status = _clean_text(writeback_outcome.get("reconciled_status"))
                task_outcomes.append(
                    {
                        "task_id": task_id,
                        "title": _clean_text(current_task.get("title")),
                        "previous_status": _clean_text(writeback_outcome.get("previous_status")),
                        "reconciled_status": desired_status,
                        "latest_delegation_status": _clean_text(writeback_outcome.get("latest_delegation_status")),
                        "summary": summary,
                        "delegation_request_id": _clean_text(writeback_outcome.get("delegation_request_id")),
                        "surface": _clean_text(writeback_outcome.get("surface")),
                        "subsystem_id": _clean_text(writeback_outcome.get("subsystem_id")),
                        "assimilation_status": _clean_text(writeback_outcome.get("assimilation_status")),
                    }
                )
                if desired_status == "waiting_on_job":
                    waiting_on_job.append(task_id)
                elif desired_status == "waiting_on_verification":
                    waiting_on_verification.append(task_id)
                elif desired_status == "completed":
                    completed.append(task_id)
                elif desired_status == "failed":
                    failed.append(task_id)
                    blocked.append(task_id)
                    continuity_proven = False
                elif desired_status == "interrupted_recovering":
                    interrupted.append(task_id)
                    blocked.append(task_id)
                    continuity_proven = False
                elif _clean_text(writeback_outcome.get("assimilation_status")) == "failed_assimilation":
                    blocked.append(task_id)
                    continuity_proven = False
                register_snapshot = _register.read_task_register_snapshot(root, initialize_if_missing=False)
                live_task = None
                live_register = register_snapshot.get("task_register") if isinstance(register_snapshot.get("task_register"), dict) else None
                if live_register:
                    register = live_register
                    reconciled_loops = list(live_register.get("open_loops") or [])
                    live_task = next(
                        (
                            row
                            for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks")
                            for row in list(live_register.get(bucket_name) or [])
                            if _clean_text(row.get("task_id")) == task_id
                        ),
                        None,
                    )
                if isinstance(live_task, dict):
                    if _mark_task_scheduler_binding_stale(
                        live_task,
                        writer=f"{writer}.scheduler_binding_cleanup",
                        live_job_ids=live_scheduler_job_ids,
                    ):
                        register_changed = True
                    live_task_row = dict(live_task)
                    bucket_name = _register._task_bucket_name(live_task_row)
                    if bucket_name == "completed_tasks":
                        reconciled_completed_tasks.append(live_task_row)
                    elif bucket_name == "residue_tasks":
                        reconciled_residue_tasks.append(live_task_row)
                    else:
                        reconciled_tasks.append(live_task_row)
                continue
            if _clean_text(current_task.get("status")).lower() not in {"completed", "failed"}:
                latest = {
                    "status": "interrupted",
                    "summary": "Active responsibility has no durable delegation bindings after restart; continuity cannot be proven.",
                    "next_decision": {"summary": "Inspect task state and decide whether to resume, rebind, or close the responsibility."},
                }
            else:
                try:
                    latest = bindings.synthesize_task_status(root, task_id=task_id).get("latest") or {}
                except Exception as exc:
                    latest = {
                        "status": "interrupted",
                        "summary": f"Recovery could not synthesize delegated status: {exc}",
                        "next_decision": {"summary": "Inspect native subsystem state before continuing."},
                    }
            desired_status = _desired_task_status(_clean_text(latest.get("status")))
            desired_next = _clean_text((latest.get("next_decision") or {}).get("summary")) or _clean_text(
                current_task.get("next_expected_action")
            )
            desired_blocked_reason = _blocked_reason_for_status(latest)
            if _task_needs_change(
                current_task,
                desired_status=desired_status,
                desired_next=desired_next,
                desired_blocked_reason=desired_blocked_reason,
            ):
                current_task["status"] = desired_status
                current_task["next_expected_action"] = desired_next
                current_task["blocked_reason"] = desired_blocked_reason
                current_task["updated_utc"] = _utc_now_iso()
                register_changed = True
            if _mark_task_scheduler_binding_stale(
                current_task,
                writer=f"{writer}.scheduler_binding_cleanup",
                live_job_ids=live_scheduler_job_ids,
            ):
                register_changed = True
            bucket_name = _register._task_bucket_name(current_task)
            if bucket_name == "completed_tasks":
                reconciled_completed_tasks.append(current_task)
            elif bucket_name == "residue_tasks":
                reconciled_residue_tasks.append(current_task)
            else:
                reconciled_tasks.append(current_task)

            summary = _clean_text(latest.get("summary"))
            task_outcomes.append(
                {
                    "task_id": task_id,
                    "title": _clean_text(current_task.get("title")),
                    "previous_status": _clean_text(task.get("status")),
                    "reconciled_status": desired_status,
                    "latest_delegation_status": _clean_text(latest.get("status")),
                    "summary": summary,
                    "delegation_request_id": _clean_text(latest.get("delegation_request_id")),
                    "surface": _clean_text(latest.get("surface")),
                    "subsystem_id": _clean_text(latest.get("subsystem_id")),
                }
            )
            ledger_uid = _clean_text(current_task.get("ledger_uid"))
            if desired_status == "waiting_on_job":
                waiting_on_job.append(task_id)
            elif desired_status == "waiting_on_verification":
                waiting_on_verification.append(task_id)
                reconciled_loops = _ensure_recovery_loop(
                    reconciled_loops,
                    kind="waiting_on_verification",
                    task_id=task_id,
                    ledger_uid=ledger_uid,
                    note=summary or "Verification remains in progress after restart.",
                    active=True,
                )
                register_changed = True
            elif desired_status == "completed":
                completed.append(task_id)
                reconciled_loops = _ensure_recovery_loop(
                    reconciled_loops,
                    kind="waiting_on_verification",
                    task_id=task_id,
                    ledger_uid=ledger_uid,
                    note=summary or "Recovery closed the verification loop.",
                    active=False,
                )
                reconciled_loops = _ensure_recovery_loop(
                    reconciled_loops,
                    kind="blocked_question",
                    task_id=task_id,
                    ledger_uid=ledger_uid,
                    note=summary or "Recovery closed the blocked recovery loop.",
                    active=False,
                )
                register_changed = True
            elif desired_status == "failed":
                failed.append(task_id)
                blocked.append(task_id)
                continuity_proven = False
                reconciled_loops = _ensure_recovery_loop(
                    reconciled_loops,
                    kind="blocked_question",
                    task_id=task_id,
                    ledger_uid=ledger_uid,
                    note=summary or "Delegated work failed and needs attention.",
                    active=True,
                )
                register_changed = True
            elif desired_status == "interrupted_recovering":
                interrupted.append(task_id)
                continuity_proven = False
                reconciled_loops = _ensure_recovery_loop(
                    reconciled_loops,
                    kind="blocked_question",
                    task_id=task_id,
                    ledger_uid=ledger_uid,
                    note=summary or "Recovery could not prove continuity after restart.",
                    active=True,
                )
                register_changed = True
        if _mark_bucket_scheduler_bindings_stale(
            reconciled_completed_tasks,
            writer=f"{writer}.scheduler_binding_cleanup",
            live_job_ids=live_scheduler_job_ids,
        ):
            register_changed = True
        if _mark_bucket_scheduler_bindings_stale(
            reconciled_residue_tasks,
            writer=f"{writer}.scheduler_binding_cleanup",
            live_job_ids=live_scheduler_job_ids,
        ):
            register_changed = True
        if register_changed:
                register = _apply_register_reconciliation(
                    root,
                    register=register,
                    reconciled_tasks=reconciled_tasks,
                    reconciled_completed_tasks=reconciled_completed_tasks,
                    reconciled_residue_tasks=reconciled_residue_tasks,
                    reconciled_loops=reconciled_loops,
                    writer=writer,
                )
        register_snapshot = _register.read_task_register_snapshot(root, initialize_if_missing=False)
        register = register_snapshot.get("task_register") if isinstance(register_snapshot.get("task_register"), dict) else register

    if host_health.get("status") == "blocked":
        recovery_status = "blocked"
        state_value = "blocked"
        continuity_proven = False
    elif host_health.get("status") == "degraded":
        recovery_status = "partial_restore"
        state_value = "blocked"
        continuity_proven = False
    elif surface_gaps:
        recovery_status = "interrupted_recovering"
        state_value = "interrupted_recovering"
    elif interrupted:
        recovery_status = "interrupted_recovering"
        state_value = "interrupted_recovering"
    elif waiting_on_verification:
        recovery_status = "waiting_on_verification"
        state_value = "waiting_on_verification"
    elif waiting_on_job:
        recovery_status = "waiting_on_job"
        state_value = "waiting_on_job"
    elif failed:
        recovery_status = "blocked"
        state_value = "blocked"
    else:
        recovery_status = "ready"
        state_value = "idle"

    summary_bits: List[str] = []
    if host_health.get("status") == "degraded":
        summary_bits.append("partial restore detected")
    elif host_health.get("status") == "blocked":
        summary_bits.append(_clean_text(host_health.get("summary")) or "host baseline blocked")
    summary_bits.extend(surface_gaps)
    if waiting_on_job:
        summary_bits.append(f"{len(waiting_on_job)} delegated job(s) still running")
    if detached_job_reconciliation["changed_job_ids"]:
        summary_bits.append(f"{len(detached_job_reconciliation['changed_job_ids'])} detached job(s) reconciled after restart")
    if direct_turn_reconciliation["changed_turn_ids"]:
        summary_bits.append(f"{len(direct_turn_reconciliation['changed_turn_ids'])} direct turn(s) marked awaiting_resume after restart")
    if waiting_on_verification:
        summary_bits.append(f"{len(waiting_on_verification)} verification task(s) still running")
    if completed:
        summary_bits.append(f"{len(completed)} task(s) finished while away")
    if failed:
        summary_bits.append(f"{len(failed)} task(s) failed while away")
    if interrupted:
        summary_bits.append(f"{len(interrupted)} task(s) need interrupted recovery")
        interrupted_summaries = [
            _clean_text(row.get("summary"))
            for row in task_outcomes
            if _clean_text(row.get("reconciled_status")) == "interrupted_recovering" and _clean_text(row.get("summary"))
        ]
        if interrupted_summaries:
            summary_bits.append(interrupted_summaries[0])
    if not summary_bits:
        summary_bits.append("Recovery found no unresolved delegated work.")
    summary = "; ".join(summary_bits)

    snapshot = {
        "schema_version": RECOVERY_SNAPSHOT_SCHEMA_VERSION,
        "recovery_id": recovery_id,
        "reconciled_utc": _utc_now_iso(),
        "writer": writer,
        "status": recovery_status,
        "summary": summary,
        "continuity_proven": continuity_proven,
        "host_health": host_health,
        "assistant_id": _clean_text((assistant_state or {}).get("assistant_id")),
        "assistant_state_present": assistant_state_present,
        "task_register_present": task_register_present,
        "counts": {
            "waiting_on_job": len(waiting_on_job),
            "waiting_on_verification": len(waiting_on_verification),
            "completed": len(completed),
            "failed": len(failed),
            "interrupted_recovering": len(interrupted),
            "blocked": len(blocked),
        },
        "task_outcomes": task_outcomes,
        "degraded_reasons": degraded_reasons,
        "surface_gaps": surface_gaps,
        "detached_job_reconciliation": detached_job_reconciliation,
        "direct_turn_reconciliation": direct_turn_reconciliation,
        "recovery_note_path": str(_snapshot_path(root)),
        "idempotency_journal_path": str(_idempotency_journal_path(root)),
    }
    _write_json_atomic(_snapshot_path(root), snapshot)
    _append_recovery_event(
        root,
        event_type="startup_reconciled",
        payload={
            "recovery_id": recovery_id,
            "status": recovery_status,
            "summary": summary,
            "continuity_proven": continuity_proven,
        },
    )

    if assistant_state:
        marker = {
            "kind": "startup_reconciliation",
            "recovery_id": recovery_id,
            "recovery_status": recovery_status,
            "host_mode": _clean_text(host_health.get("mode")),
            "host_status": _clean_text(host_health.get("status")),
            "continuity_proven": continuity_proven,
            "waiting_on_job_task_ids": waiting_on_job,
            "waiting_on_verification_task_ids": waiting_on_verification,
            "interrupted_task_ids": interrupted,
            "blocked_task_ids": blocked,
            "summary": summary,
            "recovery_note_path": str(_snapshot_path(root)),
            "idempotency_journal_path": str(_idempotency_journal_path(root)),
            "set_utc": snapshot["reconciled_utc"],
        }
        try:
            _state.update_assistant_state(
                root,
                expected_generation=int(assistant_state.get("write_generation") or 0),
                writer=writer,
                current_state=state_value,
                state_reason=summary,
                recovery_marker=marker,
            )
        except Exception as exc:
            _append_recovery_event(
                root,
                event_type="assistant_state_recovery_write_failed",
                payload={"recovery_id": recovery_id, "error": str(exc)},
            )
    return snapshot
