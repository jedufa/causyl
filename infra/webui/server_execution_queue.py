"""infra.webui.server_execution_queue

Server-side sequential PIS execution queue built on top of ledger priorities
and the existing WF-01 orchestrator.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.controlplane import job_budget_cost_ledger_store as _job_budget_cost_ledger_store
from tools.git.support import commit_journal as _commit_journal
from infra.webui import server_agents_orchestrator as _orch
from infra.webui import server_lab_assistant_task_register as _task_register
from infra.webui.ledger.ledger_manager import ChangeLedger


SCHEMA_STATE = "mobile_execution_queue_state_v1"
SCHEMA_EVENT = "mobile_execution_queue_event_v1"
OPEN_STATUSES = {
    "draft",
    "ready_for_implement",
    "ready_to_implement",
    "ready",
}
DEFAULT_PHASE_LABEL = "WF-01"
DEFAULT_WORKFLOW_MAX_STEPS = 40
DEFAULT_COMMIT_TIMEOUT_S = 1800
JOB_LIFECYCLE_COST_UNIT = "lifecycle_events"
JOB_LIFECYCLE_COST_MINOR_UNITS = 1

_LOCK = threading.RLock()
_WORKER: Optional[threading.Thread] = None
_WORKER_STOP: Optional[threading.Event] = None
_STOP_AFTER_CURRENT = threading.Event()


class ExecutionQueueError(RuntimeError):
    """Raised for invalid or unsafe queue operations."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_root() -> Path:
    root = (_repo_root() / "governance" / "agents" / "runtime" / "execution_queue").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path() -> Path:
    return (_runtime_root() / "state.json").resolve()


def _events_path() -> Path:
    return (_runtime_root() / "events.jsonl").resolve()


def _artifacts_root() -> Path:
    root = (_runtime_root() / "artifacts").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.write("\n")
        f.flush()
        # State JSON is the authoritative recovery boundary for the queue.
        # Keep the audit log append-only, but avoid fsync-per-event so status
        # polling and queue progression are not serialized behind disk syncs.


def _task_row_for_ledger_uid(register: Dict[str, Any], *, ledger_uid: str) -> Optional[Dict[str, Any]]:
    target_uid = str(ledger_uid or "").strip()
    if not target_uid:
        return None
    for task in list(register.get("active_tasks") or []):
        if not isinstance(task, dict):
            continue
        if str(task.get("ledger_uid") or "").strip() == target_uid:
            return dict(task)
    return None


def _record_queue_execution_surface_binding(*, uid: str, workflow_id: str, queue_run_id: str) -> None:
    root = _repo_root()
    try:
        snapshot = _task_register.read_task_register_snapshot(root, initialize_if_missing=False)
    except Exception:
        return
    register = dict(snapshot.get("task_register") or {})
    if not register:
        return
    task = _task_row_for_ledger_uid(register, ledger_uid=uid)
    if not task:
        return
    try:
        _task_register.record_execution_surface_ownership(
            root,
            expected_generation=int(register.get("write_generation") or 0),
            writer="execution_queue.start",
            task_id=str(task.get("task_id") or "").strip(),
            selected_surface="pipeline",
            execution_id=workflow_id,
            execution_ref=f"workflow:{workflow_id}",
            binding_ref=f"execution_queue:{queue_run_id or uid}",
            job_uri="governance/agents/runtime/execution_queue/state.json",
            trace_id=queue_run_id or workflow_id,
            reference_status="execution_ref_active",
            reference_status_reason="execution_queue_started",
        )
    except Exception:
        return


def _record_queue_pause_intervention(*, state: Dict[str, Any]) -> None:
    uid = str(state.get("current_uid") or "").strip()
    workflow_id = str(state.get("current_workflow_id") or "").strip()
    queue_run_id = str(state.get("run_id") or "").strip()
    if not uid or not workflow_id:
        return
    _record_queue_execution_surface_binding(uid=uid, workflow_id=workflow_id, queue_run_id=queue_run_id)
    root = _repo_root()
    try:
        snapshot = _task_register.read_task_register_snapshot(root, initialize_if_missing=False)
    except Exception:
        return
    register = dict(snapshot.get("task_register") or {})
    if not register:
        return
    task = _task_row_for_ledger_uid(register, ledger_uid=uid)
    if not task:
        return
    current_run_id = str(task.get("current_run_id") or "").strip()
    current_run = next(
        (row for row in list(task.get("task_runs") or []) if isinstance(row, dict) and str(row.get("run_id") or "").strip() == current_run_id),
        None,
    )
    latest_intervention = (
        dict(list(current_run.get("interventions") or [])[-1])
        if isinstance(current_run, dict) and list(current_run.get("interventions") or [])
        else {}
    )
    if (
        str(latest_intervention.get("action") or "").strip() == "pause"
        and str(latest_intervention.get("outcome_state") or "").strip() in {"accepted", "completed"}
    ):
        return
    try:
        _task_register.record_task_run_intervention(
            root,
            expected_generation=int(register.get("write_generation") or 0),
            writer="execution_queue.stop",
            task_id=str(task.get("task_id") or "").strip(),
            run_id=workflow_id,
            action="pause",
            reason="Operator requested execution_queue stop-after-current.",
            requested_by="operator",
        )
    except Exception:
        return


def _read_jsonl(path: Path, *, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    if len(lines) > limit:
        lines = lines[-limit:]
    out: List[Dict[str, Any]] = []
    for raw in lines:
        try:
            row = json.loads(raw)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _event(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_EVENT,
        "utc": _utc_now_iso(),
        "event": str(event_type or "").strip(),
        "payload": dict(payload or {}),
    }


def _post_job_lifecycle_cost_checkpoint(
    *,
    job_uid: str,
    workflow_id: str,
    checkpoint_id: str,
    recorded_at_utc: str,
    cost_reason_code: str,
    direction: str = "debit",
    amount_minor_units: int = JOB_LIFECYCLE_COST_MINOR_UNITS,
) -> Dict[str, Any]:
    safe_job_uid = str(job_uid or "").strip()
    safe_workflow_id = str(workflow_id or "").strip()
    safe_checkpoint_id = str(checkpoint_id or "").strip()
    safe_recorded_at_utc = str(recorded_at_utc or "").strip()
    if not safe_job_uid or not safe_workflow_id:
        raise ValueError("job lifecycle cost checkpoint requires stable job_uid and workflow_id")
    if not safe_checkpoint_id:
        raise ValueError("job lifecycle cost checkpoint requires a non-empty checkpoint_id")
    if not safe_recorded_at_utc:
        raise ValueError("job lifecycle cost checkpoint requires a durable recorded_at_utc timestamp")
    return _job_budget_cost_ledger_store.append_cost_event_record_once(
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "cost_event_uid": f"{safe_workflow_id}:{safe_checkpoint_id}",
            "job_uid": safe_job_uid,
            "recorded_at_utc": safe_recorded_at_utc,
            "amount_minor_units": int(amount_minor_units),
            "unit": JOB_LIFECYCLE_COST_UNIT,
            "direction": str(direction or "").strip() or "debit",
            "cost_reason_code": str(cost_reason_code or "").strip() or "usage",
        },
        repo_root=_repo_root(),
    )


def _safe_post_job_lifecycle_cost_checkpoint(
    *,
    state: Dict[str, Any],
    checkpoint_id: str,
    cost_reason_code: str,
    recorded_at_utc: str,
    direction: str = "debit",
) -> None:
    uid = str(state.get("current_uid") or "").strip()
    workflow_id = str(state.get("current_workflow_id") or "").strip()
    if not uid or not workflow_id:
        return
    try:
        _post_job_lifecycle_cost_checkpoint(
            job_uid=uid,
            workflow_id=workflow_id,
            checkpoint_id=checkpoint_id,
            recorded_at_utc=recorded_at_utc,
            cost_reason_code=cost_reason_code,
            direction=direction,
        )
    except Exception as exc:
        _append_jsonl(
            _events_path(),
            _event(
                "queue_cost_checkpoint_write_failed",
                {
                    "uid": uid,
                    "workflow_id": workflow_id,
                    "checkpoint_id": str(checkpoint_id or "").strip(),
                    "cost_reason_code": str(cost_reason_code or "").strip(),
                    "recorded_at_utc": str(recorded_at_utc or "").strip(),
                    "error": str(exc),
                },
            ),
        )


def _default_state() -> Dict[str, Any]:
    now = _utc_now_iso()
    return {
        "schema_version": SCHEMA_STATE,
        "active": False,
        "status": "idle",
        "status_detail": "idle",
        "run_id": "",
        "started_utc": "",
        "updated_utc": now,
        "last_tick_utc": "",
        "phase_label": DEFAULT_PHASE_LABEL,
        "workflow_options": {},
        "commit_each_success": True,
        "commit_boundary_mode": "between_items",
        "stop_requested": False,
        "stop_mode": "",
        "queued_items": [],
        "current_index": -1,
        "resume_count": 0,
        "last_resume_utc": "",
        "current_uid": "",
        "current_title": "",
        "current_workflow_id": "",
        "current_workflow_status": "",
        "current_phase": "",
        "current_started_utc": "",
        "current_running_started_utc": "",
        "current_commit_log": "",
        "current_error": "",
        "last_completed_uid": "",
        "last_completed_workflow_id": "",
        "last_completed_commit_sha": "",
        "last_completed_tag": "",
        "last_completed_utc": "",
        "last_failed_uid": "",
        "last_failed_workflow_id": "",
        "last_failed_stage": "",
        "last_error": "",
        "completed_uids": [],
        "failed_uids": [],
        "skipped_uids": [],
        "recent_results": [],
    }


def _normalize_state(raw: Dict[str, Any]) -> Dict[str, Any]:
    state = _default_state()
    if isinstance(raw, dict):
        state.update(raw)
    state["schema_version"] = SCHEMA_STATE
    state["phase_label"] = str(state.get("phase_label") or DEFAULT_PHASE_LABEL).strip() or DEFAULT_PHASE_LABEL
    state["workflow_options"] = dict(state.get("workflow_options") or {})
    state["commit_each_success"] = bool(state.get("commit_each_success", True))
    state["commit_boundary_mode"] = "between_items" if bool(state.get("commit_each_success", True)) else "none"
    state["current_index"] = int(state.get("current_index", -1) or -1)
    state["resume_count"] = max(0, int(state.get("resume_count", 0) or 0))
    state["current_started_utc"] = str(state.get("current_started_utc") or "").strip()
    state["current_running_started_utc"] = str(state.get("current_running_started_utc") or "").strip()
    for key in ("completed_uids", "failed_uids", "skipped_uids", "recent_results", "queued_items"):
        value = state.get(key)
        state[key] = list(value) if isinstance(value, list) else []
    normalized_queue: List[Dict[str, Any]] = []
    for idx, item in enumerate(list(state.get("queued_items") or [])):
        if not isinstance(item, dict):
            continue
        uid = str(item.get("uid") or "").strip()
        if not uid:
            continue
        normalized_queue.append(
            {
                "uid": uid,
                "title": str(item.get("title") or "").strip(),
                "status": str(item.get("status") or "").strip(),
                "execution_priority": item.get("execution_priority"),
                "queue_index": int(item.get("queue_index", idx) or idx),
            }
        )
    state["queued_items"] = normalized_queue
    return state


def _load_state() -> Dict[str, Any]:
    return _normalize_state(_read_json(_state_path()))


def _save_state(state: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_state(state)
    normalized["updated_utc"] = _utc_now_iso()
    _write_json_atomic(_state_path(), normalized)
    return normalized


def _save_state_if_changed(original: Dict[str, Any], updated: Dict[str, Any]) -> Dict[str, Any]:
    """Persist queue state only when reconciliation actually changed it."""

    before = _normalize_state(original)
    after = _normalize_state(updated)
    if before == after:
        return after
    return _save_state(after)


def _git_status_porcelain() -> str:
    cp = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if cp.returncode != 0:
        raise ExecutionQueueError(str(cp.stderr or cp.stdout or "git status failed").strip())
    return str(cp.stdout or "").strip()


def _run_git(args: List[str], *, timeout_s: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_s)),
        check=False,
    )


def _preflight_start() -> None:
    dirty = _git_status_porcelain()
    if dirty:
        raise ExecutionQueueError("execution queue requires a clean git worktree before start")
    journal = _commit_journal.scan_pending_entries(repo=_repo_root())
    if int(journal.get("invalid_count") or 0) > 0:
        raise ExecutionQueueError("execution queue refuses to start with invalid pending commit journal fragments")
    if int(journal.get("pending_count") or 0) > 0:
        raise ExecutionQueueError("execution queue requires an empty pending commit journal before start")


def _open_prioritized_items() -> List[Dict[str, Any]]:
    ledger = ChangeLedger(repo_root=_repo_root())
    items = ledger.list_items(limit=2000)
    out: List[Dict[str, Any]] = []
    for item in items:
        status = str(item.get("status") or "").strip().lower()
        priority = item.get("execution_priority")
        if priority is None:
            continue
        if status not in OPEN_STATUSES:
            continue
        out.append(dict(item))
    return out


def _selected_items(selected_uids: List[str], *, selection_mode: str = "selected_order") -> List[Dict[str, Any]]:
    ledger = ChangeLedger(repo_root=_repo_root())
    normalized: List[str] = []
    for value in list(selected_uids or []):
        uid = str(value or "").strip()
        if not uid or uid in normalized:
            continue
        normalized.append(uid)
    if not normalized:
        return []
    rows: List[Dict[str, Any]] = []
    for uid in normalized:
        payload = dict(ledger.read_item(uid) or {})
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        item = {**meta, "uid": str(payload.get("uid") or meta.get("uid") or uid).strip()}
        status = str(item.get("status") or "").strip().lower()
        if status not in OPEN_STATUSES:
            raise ExecutionQueueError(f"selected item is not open for queue launch: {uid}")
        rows.append(item)
    safe_mode = str(selection_mode or "selected_order").strip().lower() or "selected_order"
    if safe_mode == "priority_order":
        order = {uid: idx for idx, uid in enumerate(normalized)}
        rows.sort(
            key=lambda item: (
                item.get("execution_priority") is None,
                int(item.get("execution_priority") or 0),
                order.get(str(item.get("uid") or "").strip(), 10**9),
            )
        )
    return rows


def _snapshot_queue_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    snapshot: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        snapshot.append(
            {
                "uid": str(item.get("uid") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "status": str(item.get("status") or "").strip(),
                "execution_priority": item.get("execution_priority"),
                "queue_index": idx,
            }
        )
    return snapshot


def _queue_items(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    queued = list(state.get("queued_items") or [])
    if queued:
        return queued
    return _snapshot_queue_items(_open_prioritized_items())


def _queue_item_index(state: Dict[str, Any], uid: str) -> int:
    safe_uid = str(uid or "").strip()
    if not safe_uid:
        return -1
    for idx, item in enumerate(_queue_items(state)):
        if str(item.get("uid") or "").strip() == safe_uid:
            return idx
    return -1


def _has_resumeable_work(state: Dict[str, Any]) -> bool:
    if bool(state.get("active")):
        return False
    if str(state.get("status") or "").strip().lower() != "paused":
        return False
    pending = _next_candidate(state)
    return bool(pending)


def _next_candidate(state: Dict[str, Any]) -> Dict[str, Any]:
    seen = {
        str(uid).strip()
        for uid in list(state.get("completed_uids") or []) + list(state.get("failed_uids") or []) + list(state.get("skipped_uids") or [])
        if str(uid).strip()
    }
    current_uid = str(state.get("current_uid") or "").strip()
    if current_uid:
        seen.discard(current_uid)
    for item in _queue_items(state):
        uid = str(item.get("uid") or "").strip()
        if not uid or uid in seen:
            continue
        return item
    return {}


def _queue_preview(state: Dict[str, Any], *, limit: int = 8) -> List[Dict[str, Any]]:
    preview: List[Dict[str, Any]] = []
    seen = {
        str(uid).strip()
        for uid in list(state.get("completed_uids") or []) + list(state.get("failed_uids") or []) + list(state.get("skipped_uids") or [])
        if str(uid).strip()
    }
    current_uid = str(state.get("current_uid") or "").strip()
    for item in _queue_items(state):
        uid = str(item.get("uid") or "").strip()
        if not uid or uid in seen:
            continue
        preview.append(
            {
                "uid": uid,
                "title": str(item.get("title") or "").strip(),
                "status": str(item.get("status") or "").strip(),
                "execution_priority": item.get("execution_priority"),
                "is_current": uid == current_uid,
                "queue_index": int(item.get("queue_index") or len(preview)),
            }
        )
        if len(preview) >= limit:
            break
    return preview


def _batch_report(state: Dict[str, Any]) -> Dict[str, Any]:
    queue_items = _queue_items(state)
    queue_uids = {str(item.get("uid") or "").strip() for item in queue_items if str(item.get("uid") or "").strip()}
    completed = [uid for uid in list(state.get("completed_uids") or []) if uid in queue_uids]
    failed = [uid for uid in list(state.get("failed_uids") or []) if uid in queue_uids]
    skipped = [uid for uid in list(state.get("skipped_uids") or []) if uid in queue_uids]
    current_uid = str(state.get("current_uid") or "").strip()
    current_in_queue = current_uid in queue_uids
    settled = set(completed) | set(failed) | set(skipped)
    remaining = [item for item in queue_items if str(item.get("uid") or "").strip() not in settled and str(item.get("uid") or "").strip() != current_uid]
    status = str(state.get("status") or "").strip().lower()
    outcome = status or "idle"
    if status == "paused" and (completed or remaining):
        outcome = "paused_partial"
    elif status == "failed" and (completed or remaining or current_in_queue):
        outcome = "failed_partial"
    elif status == "idle" and queue_items and len(completed) == len(queue_items):
        outcome = "completed"
    elif status == "running":
        outcome = "running"
    return {
        "run_id": str(state.get("run_id") or ""),
        "total_count": len(queue_items),
        "completed_count": len(completed),
        "failed_count": len(failed),
        "skipped_count": len(skipped),
        "remaining_count": len(remaining) + (1 if current_in_queue else 0),
        "current_index": int(state.get("current_index", -1) or -1),
        "current_uid": current_uid,
        "resume_count": int(state.get("resume_count", 0) or 0),
        "outcome": outcome,
        "resumable": _has_resumeable_work(state),
        "commit_boundary_mode": str(state.get("commit_boundary_mode") or "between_items"),
    }


def _record_recent_result(state: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    results = list(state.get("recent_results") or [])
    results.append(dict(row))
    if len(results) > 20:
        results = results[-20:]
    state["recent_results"] = results
    return state


def _set_failure(
    state: Dict[str, Any],
    *,
    uid: str = "",
    workflow_id: str = "",
    stage: str,
    error: str,
) -> Dict[str, Any]:
    _STOP_AFTER_CURRENT.clear()
    safe_uid = str(uid or state.get("current_uid") or "").strip()
    safe_wf = str(workflow_id or state.get("current_workflow_id") or "").strip()
    recorded_at_utc = (
        str(state.get("last_tick_utc") or "").strip()
        or str(state.get("current_running_started_utc") or "").strip()
        or str(state.get("current_started_utc") or "").strip()
        or _utc_now_iso()
    )
    if safe_uid and safe_wf:
        _safe_post_job_lifecycle_cost_checkpoint(
            state={
                **state,
                "current_uid": safe_uid,
                "current_workflow_id": safe_wf,
            },
            checkpoint_id="terminal:failed",
            cost_reason_code="usage",
            recorded_at_utc=recorded_at_utc,
        )
    state["active"] = False
    state["status"] = "failed"
    state["status_detail"] = "failed"
    state["stop_requested"] = False
    state["stop_mode"] = ""
    state["last_failed_uid"] = safe_uid
    state["last_failed_workflow_id"] = safe_wf
    state["last_failed_stage"] = str(stage or "").strip()
    state["last_error"] = str(error or "execution queue failed").strip()
    state["current_error"] = state["last_error"]
    if safe_uid:
        failed = list(state.get("failed_uids") or [])
        if safe_uid not in failed:
            failed.append(safe_uid)
        state["failed_uids"] = failed
    _record_recent_result(
        state,
        {
            "uid": safe_uid,
            "workflow_id": safe_wf,
            "outcome": "failed",
            "stage": str(stage or "").strip(),
            "error": state["last_error"],
            "utc": _utc_now_iso(),
        },
    )
    state = _save_state(state)
    _append_jsonl(
        _events_path(),
        _event(
            "queue_failed",
            {
                "uid": safe_uid,
                "workflow_id": safe_wf,
                "stage": str(stage or "").strip(),
                "error": state["last_error"],
            },
        ),
    )
    if safe_uid:
        try:
            ChangeLedger(repo_root=_repo_root()).append_event(
                safe_uid,
                event_type="execution_queue_failed",
                payload={
                    "uid": safe_uid,
                    "workflow_id": safe_wf,
                    "stage": str(stage or "").strip(),
                    "error": state["last_error"],
                    "utc": _utc_now_iso(),
                },
            )
        except Exception:
            pass
    return state


def _resume_reconcile(state: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(state.get("active")):
        return state
    phase = str(state.get("current_phase") or "").strip().lower()
    current_uid = str(state.get("current_uid") or "").strip()
    workflow_id = str(state.get("current_workflow_id") or "").strip()
    if phase == "committing":
        return _set_failure(
            state,
            uid=current_uid,
            workflow_id=workflow_id,
            stage="reconcile",
            error="execution queue was interrupted during commit; manual review required before resume",
        )
    if current_uid and not workflow_id and phase:
        return _set_failure(
            state,
            uid=current_uid,
            stage="reconcile",
            error="execution queue lost the active workflow id; manual review required before resume",
        )
    return state


def _status_payload_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a status payload from the current persisted queue state."""

    rendered = dict(state)
    if bool(rendered.get("active")) and _STOP_AFTER_CURRENT.is_set():
        rendered["stop_requested"] = True
        rendered["stop_mode"] = "after_current"
        if not str(rendered.get("status_detail") or "").strip() or str(rendered.get("status_detail") or "").strip() == "workflow_running":
            rendered["status_detail"] = "stop_requested"
    return {
        "ok": True,
        "schema_version": SCHEMA_STATE,
        "state": rendered,
        "events": _read_jsonl(_events_path(), limit=120),
        "queue_preview": _queue_preview(rendered),
        "batch_report": _batch_report(rendered),
        "worker_alive": bool(_WORKER is not None and _WORKER.is_alive()),
    }


def _resolve_commit_tag() -> str:
    cp = _run_git(["tag", "--points-at", "HEAD"], timeout_s=15)
    if cp.returncode != 0:
        return ""
    tags = [line.strip() for line in str(cp.stdout or "").splitlines() if line.strip()]
    baseline_tags = [tag for tag in tags if tag.startswith("baseline-")]
    if baseline_tags:
        return sorted(baseline_tags)[-1]
    return tags[-1] if tags else ""


def _commit_worktree(*, uid: str, workflow_id: str, timeout_s: int) -> Dict[str, Any]:
    journal = _commit_journal.scan_pending_entries(repo=_repo_root())
    if int(journal.get("invalid_count") or 0) > 0:
        raise ExecutionQueueError("pending commit journal contains invalid fragments")
    if int(journal.get("pending_count") or 0) <= 0:
        raise ExecutionQueueError("workflow completed without pending commit journal fragments")
    audit = _commit_journal.audit_worktree_coverage(repo=_repo_root())
    if not bool(audit.get("ok")):
        uncovered = ", ".join(list(audit.get("uncovered_paths") or [])[:8])
        raise ExecutionQueueError(f"commit journal does not fully cover current worktree changes: {uncovered}")
    log_path = (_artifacts_root() / f"{_utc_compact()}__{uid}__commit.log").resolve()
    proc = subprocess.run(
        ["bash", str((_repo_root() / "tools" / "git" / "git-commit-tag.sh").resolve())],
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        timeout=max(60, int(timeout_s)),
        check=False,
    )
    output = str(proc.stdout or "")
    if proc.stderr:
        if output and not output.endswith("\n"):
            output += "\n"
        output += str(proc.stderr or "")
    log_path.write_text(output, encoding="utf-8")
    if proc.returncode != 0:
        raise ExecutionQueueError(f"git commit/tag failed (rc={int(proc.returncode)}); see {log_path}")
    head = _run_git(["rev-parse", "--verify", "HEAD"], timeout_s=15)
    commit_sha = str(head.stdout or "").strip() if head.returncode == 0 else ""
    return {
        "log_path": str(log_path),
        "commit_sha": commit_sha,
        "tag": _resolve_commit_tag(),
    }


def _record_success(
    state: Dict[str, Any],
    *,
    uid: str,
    title: str,
    workflow_id: str,
    commit_result: Dict[str, Any],
) -> Dict[str, Any]:
    terminal_utc = _utc_now_iso()
    _safe_post_job_lifecycle_cost_checkpoint(
        state=state,
        checkpoint_id="terminal:succeeded",
        cost_reason_code="usage",
        recorded_at_utc=terminal_utc,
    )
    ledger = ChangeLedger(repo_root=_repo_root())
    updated_utc = ""
    try:
        ledger.update_meta(uid, {"execution_priority": None})
        updated = ledger.update_status(uid, "implemented", note="execution_queue_committed")
        updated_utc = str(updated.get("updated_utc") or "").strip()
        ledger.append_event(
            uid,
            event_type="execution_queue_committed",
            payload={
                "uid": uid,
                "workflow_id": workflow_id,
                "commit_sha": str(commit_result.get("commit_sha") or ""),
                "tag": str(commit_result.get("tag") or ""),
                "utc": _utc_now_iso(),
            },
        )
    except Exception:
        updated_utc = _utc_now_iso()
    completed = list(state.get("completed_uids") or [])
    if uid not in completed:
        completed.append(uid)
    state["completed_uids"] = completed
    state["current_index"] = _queue_item_index(state, uid)
    state["current_uid"] = ""
    state["current_title"] = ""
    state["current_workflow_id"] = ""
    state["current_workflow_status"] = "completed"
    state["current_phase"] = ""
    state["current_started_utc"] = ""
    state["current_running_started_utc"] = ""
    state["current_commit_log"] = ""
    state["current_error"] = ""
    state["last_completed_uid"] = uid
    state["last_completed_workflow_id"] = workflow_id
    state["last_completed_commit_sha"] = str(commit_result.get("commit_sha") or "")
    state["last_completed_tag"] = str(commit_result.get("tag") or "")
    state["last_completed_utc"] = updated_utc or terminal_utc
    _record_recent_result(
        state,
        {
            "uid": uid,
            "title": title,
            "workflow_id": workflow_id,
            "outcome": "committed",
            "commit_sha": str(commit_result.get("commit_sha") or ""),
            "tag": str(commit_result.get("tag") or ""),
            "utc": state["last_completed_utc"],
        },
    )
    state = _save_state(state)
    _append_jsonl(
        _events_path(),
        _event(
            "queue_item_committed",
            {
                "uid": uid,
                "title": title,
                "workflow_id": workflow_id,
                "commit_sha": str(commit_result.get("commit_sha") or ""),
                "tag": str(commit_result.get("tag") or ""),
            },
        ),
    )
    return state


def _drain_completed_workflow(state: Dict[str, Any]) -> Dict[str, Any]:
    uid = str(state.get("current_uid") or "").strip()
    title = str(state.get("current_title") or "").strip()
    workflow_id = str(state.get("current_workflow_id") or "").strip()
    if not uid or not workflow_id:
        return _set_failure(state, stage="commit", error="completed workflow is missing uid or workflow_id")
    if not bool(state.get("commit_each_success", True)):
        return _record_success(state, uid=uid, title=title, workflow_id=workflow_id, commit_result={})
    state["current_phase"] = "committing"
    state = _save_state(state)
    _append_jsonl(_events_path(), _event("queue_item_committing", {"uid": uid, "workflow_id": workflow_id}))
    try:
        commit_result = _commit_worktree(
            uid=uid,
            workflow_id=workflow_id,
            timeout_s=int(state.get("workflow_options", {}).get("commit_timeout_s") or DEFAULT_COMMIT_TIMEOUT_S),
        )
    except Exception as exc:
        return _set_failure(state, uid=uid, workflow_id=workflow_id, stage="commit", error=str(exc))
    state["current_commit_log"] = str(commit_result.get("log_path") or "")
    should_pause_after_current = (
        (bool(state.get("stop_requested")) and str(state.get("stop_mode") or "").strip() == "after_current")
        or _STOP_AFTER_CURRENT.is_set()
    )
    if should_pause_after_current:
        _record_queue_pause_intervention(state=state)
    state = _record_success(state, uid=uid, title=title, workflow_id=workflow_id, commit_result=commit_result)
    if should_pause_after_current:
        state = _pause_after_current(state)
    return state


def _pause_after_current(state: Dict[str, Any]) -> Dict[str, Any]:
    _STOP_AFTER_CURRENT.clear()
    state["active"] = False
    state["status"] = "paused"
    state["status_detail"] = "stopped_after_current"
    state["stop_requested"] = False
    state["stop_mode"] = ""
    state = _save_state(state)
    _append_jsonl(_events_path(), _event("queue_paused", {"reason": "after_current"}))
    return state


def _drive_current_workflow(state: Dict[str, Any]) -> Dict[str, Any]:
    workflow_id = str(state.get("current_workflow_id") or "").strip()
    uid = str(state.get("current_uid") or "").strip()
    if not workflow_id or not uid:
        return _set_failure(state, uid=uid, workflow_id=workflow_id, stage="workflow", error="active queue item missing workflow binding")
    needs_running_checkpoint = not str(state.get("current_running_started_utc") or "").strip()
    try:
        result = _orch.run_workflow(
            workflow_id=workflow_id,
            max_steps=int(state.get("workflow_options", {}).get("workflow_max_steps") or DEFAULT_WORKFLOW_MAX_STEPS),
        )
    except Exception as exc:
        return _set_failure(state, uid=uid, workflow_id=workflow_id, stage="workflow", error=str(exc))
    wf_state = result.get("state") if isinstance(result.get("state"), dict) else {}
    state["current_workflow_status"] = str(wf_state.get("status") or result.get("error") or "").strip()
    state["last_tick_utc"] = _utc_now_iso()
    if needs_running_checkpoint and not bool(result.get("done")):
        state["current_running_started_utc"] = state["last_tick_utc"]
    state = _save_state(state)
    if needs_running_checkpoint and not bool(result.get("done")):
        _safe_post_job_lifecycle_cost_checkpoint(
            state=state,
            checkpoint_id="running_started",
            cost_reason_code="usage",
            recorded_at_utc=str(state.get("current_running_started_utc") or ""),
        )
    if not bool(result.get("done")):
        return state
    status = str(wf_state.get("status") or "").strip().lower()
    if status == "completed":
        return _drain_completed_workflow(state)
    error = str(result.get("error") or wf_state.get("last_error") or f"workflow {status or 'failed'}").strip()
    return _set_failure(state, uid=uid, workflow_id=workflow_id, stage="workflow", error=error)


def _start_next_item(state: Dict[str, Any]) -> Dict[str, Any]:
    candidate = _next_candidate(state)
    if not candidate:
        state["active"] = False
        state["status"] = "idle"
        state["status_detail"] = "queue_empty"
        state["stop_requested"] = False
        state["stop_mode"] = ""
        state = _save_state(state)
        _append_jsonl(_events_path(), _event("queue_drained", {"reason": "no_prioritized_items"}))
        return state
    uid = str(candidate.get("uid") or "").strip()
    title = str(candidate.get("title") or "").strip()
    try:
        started = _orch.start_workflow(
            binding={"type": "ledger_uid", "id": uid},
            phase_label=str(state.get("phase_label") or DEFAULT_PHASE_LABEL),
            objective="",
            options=dict(state.get("workflow_options") or {}),
        )
    except Exception as exc:
        return _set_failure(state, uid=uid, stage="start", error=str(exc))
    wf = str(started.get("workflow_id") or "").strip()
    state["current_uid"] = uid
    state["current_title"] = title
    state["current_index"] = _queue_item_index(state, uid)
    state["current_workflow_id"] = wf
    state["current_workflow_status"] = "started"
    state["current_phase"] = "workflow_running"
    state["current_started_utc"] = _utc_now_iso()
    state["current_running_started_utc"] = ""
    state["current_error"] = ""
    state["status"] = "running"
    state["status_detail"] = "workflow_running"
    state["last_tick_utc"] = state["current_started_utc"]
    state = _save_state(state)
    _append_jsonl(
        _events_path(),
        _event(
            "queue_item_started",
            {
                "uid": uid,
                "title": title,
                "workflow_id": wf,
                "phase_label": str(state.get("phase_label") or DEFAULT_PHASE_LABEL),
            },
        ),
    )
    _record_queue_execution_surface_binding(uid=uid, workflow_id=wf, queue_run_id=str(state.get("run_id") or ""))
    _safe_post_job_lifecycle_cost_checkpoint(
        state=state,
        checkpoint_id="launch",
        cost_reason_code="reservation",
        recorded_at_utc=str(state.get("current_started_utc") or ""),
    )
    try:
        ChangeLedger(repo_root=_repo_root()).append_event(
            uid,
            event_type="execution_queue_started",
            payload={
                "uid": uid,
                "workflow_id": wf,
                "phase_label": str(state.get("phase_label") or DEFAULT_PHASE_LABEL),
                "utc": _utc_now_iso(),
            },
        )
    except Exception:
        pass
    return state


def _worker_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            with _LOCK:
                state = _resume_reconcile(_load_state())
                if not bool(state.get("active")):
                    _STOP_AFTER_CURRENT.clear()
                    return
                if _STOP_AFTER_CURRENT.is_set():
                    if not bool(state.get("stop_requested")) or str(state.get("stop_mode") or "").strip() != "after_current":
                        state["stop_requested"] = True
                        state["stop_mode"] = "after_current"
                        if str(state.get("status") or "").strip() == "running":
                            state["status_detail"] = "stop_requested"
                        state = _save_state(state)
                        _record_queue_pause_intervention(state=state)
                if (
                    (bool(state.get("stop_requested")) or _STOP_AFTER_CURRENT.is_set())
                    and (str(state.get("stop_mode") or "").strip() == "after_current" or _STOP_AFTER_CURRENT.is_set())
                    and not str(state.get("current_workflow_id") or "").strip()
                ):
                    _pause_after_current(state)
                    return
                if str(state.get("current_workflow_id") or "").strip():
                    state = _drive_current_workflow(state)
                else:
                    state = _start_next_item(state)
                if not bool(state.get("active")):
                    return
        except Exception as exc:
            with _LOCK:
                state = _load_state()
                if bool(state.get("active")):
                    _set_failure(state, stage="worker", error=f"execution queue worker crashed: {exc}")
            return


def _start_worker_locked() -> None:
    global _WORKER, _WORKER_STOP
    if _WORKER is not None and _WORKER.is_alive():
        return
    stop_event = threading.Event()
    worker = threading.Thread(target=_worker_loop, args=(stop_event,), name="webui_execution_queue", daemon=True)
    _WORKER_STOP = stop_event
    _WORKER = worker
    worker.start()


def ensure_worker_running() -> Dict[str, Any]:
    with _LOCK:
        original = _load_state()
        state = _save_state_if_changed(original, _resume_reconcile(original))
        if bool(state.get("active")):
            _start_worker_locked()
        return _status_payload_from_state(state)


def start_queue(*, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    opts = dict(options or {})
    with _LOCK:
        _STOP_AFTER_CURRENT.clear()
        state = _resume_reconcile(_load_state())
        if bool(state.get("active")):
            _start_worker_locked()
            payload = _status_payload_from_state(state)
            payload["noop_reason"] = "already_running"
            return payload
        _preflight_start()
        if _has_resumeable_work(state):
            state["active"] = True
            state["status"] = "running"
            state["status_detail"] = "resuming"
            state["stop_requested"] = False
            state["stop_mode"] = ""
            state["last_tick_utc"] = _utc_now_iso()
            state["last_resume_utc"] = state["last_tick_utc"]
            state["resume_count"] = int(state.get("resume_count", 0) or 0) + 1
            state = _save_state(state)
            _append_jsonl(
                _events_path(),
                _event(
                    "queue_resumed",
                    {
                        "run_id": str(state.get("run_id") or ""),
                        "resume_count": int(state.get("resume_count", 0) or 0),
                        "current_uid": str(state.get("current_uid") or ""),
                    },
                ),
            )
            _start_worker_locked()
            payload = _status_payload_from_state(state)
            payload["resumed"] = True
            return payload
        selected_uids = [str(uid).strip() for uid in list(opts.get("selected_uids") or []) if str(uid).strip()]
        if selected_uids:
            items = _selected_items(selected_uids, selection_mode=str(opts.get("selection_mode") or "selected_order"))
        else:
            items = _open_prioritized_items()
        if not items:
            state["active"] = False
            state["status"] = "idle"
            state["status_detail"] = "queue_empty"
            state["phase_label"] = str(opts.get("phase_label") or state.get("phase_label") or DEFAULT_PHASE_LABEL).strip() or DEFAULT_PHASE_LABEL
            state["workflow_options"] = dict(opts.get("workflow_options") or {})
            state["commit_each_success"] = bool(opts.get("commit_each_success", True))
            state["commit_boundary_mode"] = "between_items" if bool(state.get("commit_each_success")) else "none"
            state["run_id"] = ""
            state = _save_state(state)
            _append_jsonl(_events_path(), _event("queue_start_noop", {"reason": "no_prioritized_items"}))
            return _status_payload_from_state(state)
        state = _default_state()
        state["active"] = True
        state["status"] = "running"
        state["status_detail"] = "starting"
        state["run_id"] = f"eq-{_utc_compact()}"
        state["started_utc"] = _utc_now_iso()
        state["last_tick_utc"] = state["started_utc"]
        state["phase_label"] = str(opts.get("phase_label") or DEFAULT_PHASE_LABEL).strip() or DEFAULT_PHASE_LABEL
        state["workflow_options"] = dict(opts.get("workflow_options") or {})
        state["commit_each_success"] = bool(opts.get("commit_each_success", True))
        state["commit_boundary_mode"] = "between_items" if bool(state.get("commit_each_success")) else "none"
        state["queued_items"] = _snapshot_queue_items(items)
        state = _save_state(state)
        _append_jsonl(
            _events_path(),
            _event(
                "queue_started",
                {
                    "run_id": str(state.get("run_id") or ""),
                    "phase_label": str(state.get("phase_label") or DEFAULT_PHASE_LABEL),
                    "queued_count": len(items),
                    "selection_mode": str(opts.get("selection_mode") or ("selected_order" if selected_uids else "priority_order")),
                    "commit_each_success": bool(state.get("commit_each_success")),
                },
            ),
        )
        _start_worker_locked()
        return _status_payload_from_state(state)


def stop_queue(*, mode: str = "after_current") -> Dict[str, Any]:
    safe_mode = str(mode or "after_current").strip().lower() or "after_current"
    if safe_mode not in {"after_current"}:
        raise ExecutionQueueError("unsupported stop mode")
    _STOP_AFTER_CURRENT.set()
    if not _LOCK.acquire(timeout=0.05):
        state = _load_state()
        if not bool(state.get("active")):
            _STOP_AFTER_CURRENT.clear()
        return _status_payload_from_state(state)
    try:
        state = _resume_reconcile(_load_state())
        if not bool(state.get("active")):
            _STOP_AFTER_CURRENT.clear()
            return _status_payload_from_state(state)
        state["stop_requested"] = True
        state["stop_mode"] = safe_mode
        state["status_detail"] = "stop_requested"
        state = _save_state(state)
        _append_jsonl(_events_path(), _event("queue_stop_requested", {"mode": safe_mode}))
        _record_queue_pause_intervention(state=state)
        return _status_payload_from_state(state)
    finally:
        _LOCK.release()


def status() -> Dict[str, Any]:
    # Keep status lock-free and read-only so UI polling never blocks behind a
    # long workflow step or commit operation running in the worker thread.
    return _status_payload_from_state(_load_state())
