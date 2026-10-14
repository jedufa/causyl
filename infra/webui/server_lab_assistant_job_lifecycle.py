"""Detached delegated job lifecycle truth after mailbox handoff."""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.controlplane import continuation_packet_store as _continuation_packet_store
from infra.webui import server_lab_assistant_correlation as _correlation
from infra.webui import server_lab_assistant_delegation_policy as _policy
from infra.webui import server_lab_assistant_execution_truth_ownership as _truth_ownership
from infra.webui import server_lab_assistant_task_register as _register


DETACHED_JOB_SCHEMA_VERSION = "lab_assistant_detached_job_v1"
DETACHED_JOB_EVENT_SCHEMA_VERSION = "lab_assistant_detached_job_event_v1"
DETACHED_JOB_SNAPSHOT_SCHEMA_VERSION = "lab_assistant_detached_job_snapshot_v1"
DETACHED_JOB_ALLOWED_STATES = {
    "queued",
    "running",
    "heartbeat_stale",
    "timed_out",
    "cancelled",
    "interrupted",
    "ambiguous",
    "completed",
    "failed",
}
_TERMINAL_STATES = {"timed_out", "cancelled", "ambiguous", "completed", "failed"}
_LOCK = threading.RLock()


class DetachedJobLifecycleError(RuntimeError):
    """Raised when detached job lifecycle truth cannot be updated safely."""


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[_dt.datetime] = None) -> str:
    when = dt or _utc_now()
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(raw: Any) -> _dt.datetime:
    text = _clean_text(raw)
    if not text:
        raise ValueError("missing UTC timestamp")
    return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _owner_ref_identity(raw: Any) -> Dict[str, str]:
    owner_ref = raw if isinstance(raw, dict) else {}
    return {
        "task_id": _clean_text(owner_ref.get("task_id")),
        "ledger_uid": _clean_text(owner_ref.get("ledger_uid")),
        "open_loop_id": _clean_text(owner_ref.get("open_loop_id")),
    }


def _mailbox_handoff_identity(request_record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(request_record or {})
    request = dict(record.get("request") or {})
    handoff = dict(record.get("handoff") or {})
    return {
        "detached_job_id": _clean_text(handoff.get("detached_job_id") or request.get("detached_job_id")),
        "request_id": _clean_text(record.get("request_id") or request.get("request_id")),
        "correlation_id": _clean_text(request.get("correlation_id")),
        "launch_turn_id": _clean_text(request.get("launch_turn_id")),
        "owner_ref": _owner_ref_identity(request.get("owner_ref")),
        "job_surface": _clean_text(handoff.get("job_surface")).lower(),
        "mailbox_request_id": _clean_text(record.get("request_id")),
        "mailbox_claim_id": _clean_text(handoff.get("claim_id")),
        "accepted_utc": _clean_text(handoff.get("accepted_utc")),
    }


def _stable_job_identity(job_record: Dict[str, Any]) -> Dict[str, Any]:
    record = _normalize_record(dict(job_record or {}))
    return {
        "detached_job_id": _clean_text(record.get("detached_job_id")),
        "request_id": _clean_text(record.get("request_id")),
        "correlation_id": _clean_text(record.get("correlation_id")),
        "launch_turn_id": _clean_text(record.get("launch_turn_id")),
        "owner_ref": _owner_ref_identity(record.get("owner_ref")),
        "job_surface": _clean_text(record.get("job_surface")).lower(),
        "mailbox_request_id": _clean_text(record.get("mailbox_request_id")),
        "mailbox_claim_id": _clean_text(record.get("mailbox_claim_id")),
        "accepted_utc": _clean_text(record.get("accepted_utc")),
    }


def _mailbox_handoff_mismatch_fields(expected: Dict[str, Any], actual: Dict[str, Any]) -> List[str]:
    mismatches: List[str] = []
    for key in (
        "detached_job_id",
        "request_id",
        "correlation_id",
        "launch_turn_id",
        "job_surface",
        "mailbox_request_id",
        "mailbox_claim_id",
        "accepted_utc",
    ):
        if _clean_text(expected.get(key)) != _clean_text(actual.get(key)):
            mismatches.append(key)
    expected_owner = _owner_ref_identity(expected.get("owner_ref"))
    actual_owner = _owner_ref_identity(actual.get("owner_ref"))
    for key in ("task_id", "ledger_uid", "open_loop_id"):
        if _clean_text(expected_owner.get(key)) != _clean_text(actual_owner.get(key)):
            mismatches.append(f"owner_ref.{key}")
    return mismatches


def validate_mailbox_handoff_target(repo_root: Path, *, request_record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(request_record or {})
    expected = _mailbox_handoff_identity(record)
    detached_job_id = _clean_text(expected.get("detached_job_id"))
    if not detached_job_id:
        raise DetachedJobLifecycleError("detached_job_id is required")
    existing = _read_json(job_path(repo_root, detached_job_id))
    if not existing:
        return {}
    stored = _normalize_record(existing)
    mismatches = _mailbox_handoff_mismatch_fields(expected, _stable_job_identity(stored))
    if mismatches:
        joined = ", ".join(mismatches)
        raise DetachedJobLifecycleError(
            f"detached job id collision for {detached_job_id}: existing lifecycle truth belongs to a different mailbox handoff ({joined})"
        )
    return stored


def _task_for_owner_ref(register: Dict[str, Any], owner_ref: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _clean_text(owner_ref.get("task_id"))
    ledger_uid = _clean_text(owner_ref.get("ledger_uid"))
    open_loop_id = _clean_text(owner_ref.get("open_loop_id"))
    tasks = list(register.get("active_tasks") or [])
    if task_id:
        for task in tasks:
            if _clean_text(task.get("task_id")) == task_id:
                return dict(task)
        raise DetachedJobLifecycleError(f"task register missing task_id {task_id}")
    if ledger_uid:
        for task in tasks:
            if _clean_text(task.get("ledger_uid")) == ledger_uid:
                return dict(task)
        raise DetachedJobLifecycleError(f"task register missing ledger_uid {ledger_uid}")
    if open_loop_id:
        for loop in list(register.get("open_loops") or []):
            if _clean_text(loop.get("loop_id")) == open_loop_id:
                linked_task_id = _clean_text(loop.get("linked_task_id"))
                if linked_task_id:
                    for task in tasks:
                        if _clean_text(task.get("task_id")) == linked_task_id:
                            return dict(task)
        raise DetachedJobLifecycleError(f"task register missing open_loop_id {open_loop_id}")
    raise DetachedJobLifecycleError("owner_ref missing task_id/ledger_uid/open_loop_id")


def _task_run_for_job(task: Dict[str, Any], detached_job_id: str) -> Optional[Dict[str, Any]]:
    run_id = _clean_text(task.get("current_run_id"))
    runs = [dict(row) for row in list(task.get("task_runs") or []) if isinstance(row, dict)]
    if run_id:
        for row in runs:
            if _clean_text(row.get("run_id")) == run_id:
                return dict(row)
    if detached_job_id:
        for row in runs:
            if _clean_text(row.get("run_id")) == detached_job_id or _clean_text(row.get("execution_id")) == detached_job_id:
                return dict(row)
    return None


def _checkpoint_status_for_job_state(state: str) -> str:
    normalized = _clean_text(state).lower()
    if normalized == "completed":
        return "finalized"
    if normalized == "ambiguous":
        return "ambiguous"
    if normalized in {"failed", "cancelled", "timed_out", "interrupted", "heartbeat_stale"}:
        return "stale"
    return "recorded"


def _checkpoint_payload_for_job(
    *,
    repo_root: Path,
    job_record: Dict[str, Any],
    run_row: Optional[Dict[str, Any]],
    writer: str,
    now_utc: str,
) -> Optional[Dict[str, Any]]:
    progress_message = _clean_text(job_record.get("last_progress_message"))
    progress_count = int(job_record.get("progress_count") or 0)
    existing_checkpoint = dict(run_row.get("checkpoint") or {}) if isinstance(run_row, dict) else {}
    has_existing = bool(existing_checkpoint)
    if not has_existing and not progress_message and progress_count <= 0:
        return None
    detached_job_id = _clean_text(job_record.get("detached_job_id"))
    checkpoint_id = _clean_text(existing_checkpoint.get("checkpoint_id")) or f"job:{detached_job_id}"
    checkpoint_revision = max(int(existing_checkpoint.get("checkpoint_revision") or 0), progress_count)
    status = _checkpoint_status_for_job_state(job_record.get("state"))
    job_record_path = job_path(repo_root, detached_job_id)
    try:
        job_record_ref = str(job_record_path.relative_to(Path(repo_root).resolve()))
    except Exception:
        job_record_ref = str(job_record_path)
    return {
        "schema_version": _register.TASK_RUN_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "checkpoint_status": status,
        "checkpoint_utc": _clean_text(job_record.get("updated_utc")) or _clean_text(now_utc),
        "checkpoint_note": _clean_text(job_record.get("state_reason")) or "detached_job_progress",
        "checkpoint_source": _clean_text(writer),
        "checkpoint_revision": checkpoint_revision,
        "progress_percent": None,
        "progress_summary": progress_message or _clean_text(existing_checkpoint.get("progress_summary")),
        "evidence_refs": [job_record_ref],
    }


def _resume_decision_for_job_state(
    *,
    job_state: str,
    checkpoint: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    state = _clean_text(job_state).lower()
    if state in {"failed", "cancelled"}:
        decision = "retry"
    elif state in {"ambiguous"}:
        decision = "blocked"
    elif state in {"timed_out", "interrupted", "heartbeat_stale"}:
        has_checkpoint = bool(checkpoint) and bool(list(dict(checkpoint or {}).get("evidence_refs") or []))
        decision = "resume" if has_checkpoint else "retry"
    else:
        return None
    return {
        "schema_version": _register.TASK_RUN_RESUME_DECISION_SCHEMA_VERSION,
        "decision": decision,
        "decision_reason": f"job_state:{state}",
        "decision_summary": f"detached job state is {state}",
        "decision_source": "detached_job_lifecycle",
        "checkpoint_id": _clean_text(dict(checkpoint or {}).get("checkpoint_id")),
        "evidence_refs": list(dict(checkpoint or {}).get("evidence_refs") or []),
    }


def _record_task_checkpoint_and_decision(
    repo_root: Path,
    *,
    job_record: Dict[str, Any],
    writer: str,
    now_utc: str,
    record_decision: bool,
) -> None:
    owner_ref = dict(job_record.get("owner_ref") or {})
    if not owner_ref:
        return
    snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    if not register:
        return
    try:
        task = _task_for_owner_ref(register, owner_ref)
    except DetachedJobLifecycleError:
        return
    task_id = _clean_text(task.get("task_id"))
    if not task_id:
        return
    run_row = _task_run_for_job(task, _clean_text(job_record.get("detached_job_id")))
    run_id = _clean_text((run_row or {}).get("run_id"))
    if not run_id:
        return
    checkpoint = _checkpoint_payload_for_job(
        repo_root=repo_root,
        job_record=job_record,
        run_row=run_row,
        writer=writer,
        now_utc=now_utc,
    )
    expected_generation = int(register.get("write_generation") or 0)
    if checkpoint:
        try:
            updated = _register.record_task_run_checkpoint(
                repo_root,
                expected_generation=expected_generation,
                writer=writer,
                task_id=task_id,
                run_id=run_id,
                checkpoint=checkpoint,
                auto_decide=False,
            )
            expected_generation = int((updated.get("task_register") or {}).get("write_generation") or expected_generation)
        except Exception:
            return
    if not record_decision:
        return
    decision = _resume_decision_for_job_state(job_state=job_record.get("state"), checkpoint=checkpoint)
    if not decision:
        return
    try:
        _register.record_task_run_resume_decision(
            repo_root,
            expected_generation=expected_generation,
            writer=writer,
            task_id=task_id,
            run_id=run_id,
            resume_decision=decision,
        )
    except Exception:
        return


def lifecycle_root(repo_root: Path) -> Path:
    root = (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "detached_jobs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def jobs_dir(repo_root: Path) -> Path:
    path = (lifecycle_root(repo_root) / "jobs").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_path(repo_root: Path, detached_job_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in _clean_text(detached_job_id)).strip("-") or "x"
    return (jobs_dir(repo_root) / f"{safe}.json").resolve()


def snapshot_path(repo_root: Path) -> Path:
    return (lifecycle_root(repo_root) / "job_snapshot.json").resolve()


def events_path(repo_root: Path) -> Path:
    return (lifecycle_root(repo_root) / "job_events.jsonl").resolve()


def lock_path(repo_root: Path) -> Path:
    return (lifecycle_root(repo_root) / ".detached_jobs.lock").resolve()


try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]


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


def _result_summary(raw: Any) -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    return {
        "result_id": _clean_text(row.get("result_id")),
        "status": _clean_text(row.get("status")).lower(),
        "summary": _clean_text(row.get("summary")),
        "recorded_utc": _clean_text(row.get("recorded_utc")),
        "producer": _clean_text(row.get("producer")),
        "evidence_refs": [str(item).strip() for item in list(row.get("evidence_refs") or []) if str(item).strip()],
        "superseded": bool(row.get("superseded")),
        "late_result": bool(row.get("late_result")),
    }


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    state = _clean_text(record.get("state")).lower() or "queued"
    if state not in DETACHED_JOB_ALLOWED_STATES:
        raise DetachedJobLifecycleError(f"invalid detached job state: {state or '<missing>'}")
    current = {
        "schema_version": DETACHED_JOB_SCHEMA_VERSION,
        "detached_job_id": _clean_text(record.get("detached_job_id")),
        "request_id": _clean_text(record.get("request_id")),
        "correlation_id": _clean_text(record.get("correlation_id")),
        "launch_turn_id": _clean_text(record.get("launch_turn_id")),
        "owner_ref": dict(record.get("owner_ref") or {}),
        "job_surface": _clean_text(record.get("job_surface")).lower(),
        "mailbox_request_id": _clean_text(record.get("mailbox_request_id")),
        "mailbox_claim_id": _clean_text(record.get("mailbox_claim_id")),
        "accepted_utc": _clean_text(record.get("accepted_utc")),
        "created_utc": _clean_text(record.get("created_utc")),
        "updated_utc": _clean_text(record.get("updated_utc") or _utc_iso()),
        "state": state,
        "state_reason": _clean_text(record.get("state_reason")),
        "worker_id": _clean_text(record.get("worker_id")),
        "heartbeat_interval_s": max(1, int(record.get("heartbeat_interval_s") or 60)),
        "heartbeat_timeout_s": max(1, int(record.get("heartbeat_timeout_s") or 180)),
        "timeout_s": max(0, int(record.get("timeout_s") or 0)),
        "deadline_utc": _clean_text(record.get("deadline_utc")),
        "last_heartbeat_utc": _clean_text(record.get("last_heartbeat_utc")),
        "last_progress_utc": _clean_text(record.get("last_progress_utc")),
        "last_progress_message": _clean_text(record.get("last_progress_message")),
        "progress_count": max(0, int(record.get("progress_count") or 0)),
        "cancellation_requested": bool(record.get("cancellation_requested")),
        "cancellation_requested_utc": _clean_text(record.get("cancellation_requested_utc")),
        "cancellation_reason": _clean_text(record.get("cancellation_reason")),
        "late_result": bool(record.get("late_result")),
        "late_result_pending_reconciliation": bool(record.get("late_result_pending_reconciliation")),
        "late_result_basis_state": _clean_text(record.get("late_result_basis_state")).lower(),
        "superseded_result_count": max(0, int(record.get("superseded_result_count") or 0)),
        "latest_result": _result_summary(record.get("latest_result")),
        "terminal_result": _result_summary(record.get("terminal_result")),
        "superseded_results": [_result_summary(item) for item in list(record.get("superseded_results") or []) if isinstance(item, dict)],
        "reconciliation": {
            "last_reconciled_utc": _clean_text(((record.get("reconciliation") or {}) if isinstance(record.get("reconciliation"), dict) else {}).get("last_reconciled_utc")),
            "last_reconcile_mode": _clean_text(((record.get("reconciliation") or {}) if isinstance(record.get("reconciliation"), dict) else {}).get("last_reconcile_mode")),
            "late_result_reconciled_utc": _clean_text(((record.get("reconciliation") or {}) if isinstance(record.get("reconciliation"), dict) else {}).get("late_result_reconciled_utc")),
            "late_result_reconciled_by": _clean_text(((record.get("reconciliation") or {}) if isinstance(record.get("reconciliation"), dict) else {}).get("late_result_reconciled_by")),
            "late_result_reconciliation_reason": _clean_text(((record.get("reconciliation") or {}) if isinstance(record.get("reconciliation"), dict) else {}).get("late_result_reconciliation_reason")),
        },
    }
    if not current["detached_job_id"]:
        raise DetachedJobLifecycleError("detached_job_id is required")
    if not current["request_id"]:
        raise DetachedJobLifecycleError(f"detached job {current['detached_job_id']} requires request_id")
    if not current["accepted_utc"]:
        raise DetachedJobLifecycleError(f"detached job {current['detached_job_id']} requires accepted_utc")
    return current


def _records(repo_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(jobs_dir(repo_root).glob("*.json"), key=lambda row: row.name):
        record = _read_json(path)
        if not record:
            continue
        try:
            rows.append(_normalize_record(record))
        except Exception:
            continue
    rows.sort(key=lambda row: (_clean_text(row.get("accepted_utc")), _clean_text(row.get("detached_job_id"))))
    return rows


def _store_record(repo_root: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_record(record)
    _write_json_atomic(job_path(repo_root, normalized["detached_job_id"]), normalized)
    return normalized


def _load_record(repo_root: Path, detached_job_id: str) -> Dict[str, Any]:
    record = _read_json(job_path(repo_root, detached_job_id))
    if not record:
        raise DetachedJobLifecycleError(f"detached job not found: {_clean_text(detached_job_id) or '<missing>'}")
    return _normalize_record(record)


def _event(repo_root: Path, *, event_type: str, payload: Dict[str, Any], now: Optional[_dt.datetime] = None) -> None:
    _append_jsonl(
        events_path(repo_root),
        {
            "schema_version": DETACHED_JOB_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": _clean_text(event_type),
            "utc": _utc_iso(now),
            "payload": dict(payload or {}),
        },
    )


def _write_snapshot(repo_root: Path, *, writer: str, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    rows = _records(repo_root)
    counts: Dict[str, int] = {state: 0 for state in sorted(DETACHED_JOB_ALLOWED_STATES)}
    summaries: List[Dict[str, Any]] = []
    for row in rows:
        state = _clean_text(row.get("state")).lower()
        counts[state] = counts.get(state, 0) + 1
        summaries.append(
            {
                "detached_job_id": row["detached_job_id"],
                "request_id": row["request_id"],
                "job_surface": row["job_surface"],
                "state": row["state"],
                "state_reason": row["state_reason"],
                "worker_id": row["worker_id"],
                "accepted_utc": row["accepted_utc"],
                "updated_utc": row["updated_utc"],
                "last_heartbeat_utc": row["last_heartbeat_utc"],
                "deadline_utc": row["deadline_utc"],
                "cancellation_requested": bool(row.get("cancellation_requested")),
                "late_result": bool(row.get("late_result")),
                "late_result_pending_reconciliation": bool(row.get("late_result_pending_reconciliation")),
                "late_result_basis_state": _clean_text(row.get("late_result_basis_state")),
                "path": str(job_path(repo_root, row["detached_job_id"])),
            }
        )
    payload = {
        "schema_version": DETACHED_JOB_SNAPSHOT_SCHEMA_VERSION,
        "writer": _clean_text(writer),
        "generated_utc": _utc_iso(now),
        "job_count": len(rows),
        "counts": counts,
        "jobs": summaries,
        "events_path": str(events_path(repo_root)),
    }
    _write_json_atomic(snapshot_path(repo_root), payload)
    return payload


def _stale_deadline(record: Dict[str, Any], base_utc: str) -> _dt.datetime:
    return _parse_utc(base_utc) + _dt.timedelta(seconds=max(1, int(record.get("heartbeat_timeout_s") or 180)))


def _deadline_utc(now: _dt.datetime, timeout_s: int) -> str:
    return _utc_iso(now + _dt.timedelta(seconds=max(1, int(timeout_s))))


def _is_terminal_state(state: str) -> bool:
    return state in _TERMINAL_STATES


def _job_record_ref(repo_root: Path, detached_job_id: str) -> str:
    path = job_path(repo_root, detached_job_id)
    try:
        return str(path.relative_to(Path(repo_root).resolve()))
    except Exception:
        return str(path)


def _result_evidence_refs(record: Dict[str, Any]) -> List[str]:
    latest = record.get("latest_result") if isinstance(record.get("latest_result"), dict) else {}
    refs = [str(item).strip() for item in list(latest.get("evidence_refs") or []) if str(item).strip()]
    deduped: List[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return deduped


def _packet_summary_and_outcome(record: Dict[str, Any]) -> tuple[str | None, str, str]:
    state = _clean_text(record.get("state")).lower()
    latest = record.get("latest_result") if isinstance(record.get("latest_result"), dict) else {}
    latest_summary = _clean_text(latest.get("summary"))
    progress_message = _clean_text(record.get("last_progress_message"))
    state_reason = _clean_text(record.get("state_reason"))

    if state == "queued":
        return None, "", "insufficient_lifecycle_truth"
    if state == "running":
        if not progress_message:
            return None, "", "ambiguous_checkpoint_state"
        return "partial", progress_message, ""
    if state == "completed":
        if bool(record.get("late_result_pending_reconciliation")):
            return "blocked", latest_summary or "late completion requires reconciliation before success is trustworthy", ""
        return "succeeded", latest_summary or "detached job completed", ""
    if state == "failed":
        return "failed", latest_summary or "detached job failed", ""
    if state == "ambiguous":
        return "blocked", latest_summary or state_reason or "detached job result is ambiguous", ""
    if state in {"cancelled", "interrupted", "heartbeat_stale", "timed_out"}:
        return "interrupted", latest_summary or state_reason or f"detached job ended as {state}", ""
    return None, "", f"unsupported_emission_state:{state or 'missing'}"


def _continuation_packet_payload(*, repo_root: Path, record: Dict[str, Any], now_utc: str) -> Dict[str, Any] | None:
    outcome, summary, blocked_reason = _packet_summary_and_outcome(record)
    if not outcome:
        return None

    detached_job_id = _clean_text(record.get("detached_job_id"))
    request_id = _clean_text(record.get("request_id"))
    if not detached_job_id or not request_id:
        return None
    job_record_ref = _job_record_ref(repo_root, detached_job_id)
    state = _clean_text(record.get("state")).lower()
    progress_message = _clean_text(record.get("last_progress_message"))
    latest = record.get("latest_result") if isinstance(record.get("latest_result"), dict) else {}
    latest_summary = _clean_text(latest.get("summary"))
    result_evidence_refs = _result_evidence_refs(record)
    receipt_refs = [job_record_ref, *result_evidence_refs]

    evidence_created = [
        {
            "evidence_id": f"job_record:{detached_job_id}",
            "kind": "lifecycle_record",
            "summary": f"Detached job lifecycle state is {state}.",
            "refs": [job_record_ref],
        }
    ]
    for idx, ref in enumerate(result_evidence_refs, start=1):
        evidence_created.append(
            {
                "evidence_id": f"result_evidence:{idx}",
                "kind": "result_evidence",
                "summary": f"Linked detached-job evidence {idx}.",
                "refs": [ref],
            }
        )

    artifact_index = [
        {
            "artifact_id": f"job_record:{detached_job_id}",
            "kind": "json",
            "uri": job_record_ref,
            "summary": "Detached job lifecycle truth record.",
        }
    ]
    for idx, ref in enumerate(result_evidence_refs, start=1):
        artifact_index.append(
            {
                "artifact_id": f"result_ref:{idx}",
                "kind": "evidence_ref",
                "uri": ref,
                "summary": "Detached job result evidence reference.",
            }
        )

    if outcome == "succeeded":
        open_questions = [
            {
                "question_id": "q_successor_review",
                "question": "Which immediate successor should consume the completed handoff first?",
            }
        ]
        failed_paths = [
            {
                "path_id": "fp_clean_finish_required",
                "summary": "Any restart or retry path should defer to the linked lifecycle truth if it contradicts this packet.",
            }
        ]
        next_steps = [
            {
                "step_id": "step_review_success",
                "priority": "now",
                "step": "Review the linked evidence and continue from the finished result.",
            }
        ]
        confidence = {
            "level": "high" if result_evidence_refs else "medium",
            "rationale": "Terminal lifecycle truth is durable; confidence increases when explicit evidence refs are present.",
        }
    elif outcome == "partial":
        open_questions = [
            {
                "question_id": "q_remaining_work",
                "question": "What remaining work should the successor resume from this checkpoint?",
            }
        ]
        failed_paths = [
            {
                "path_id": "fp_terminal_not_proven",
                "summary": "A clean terminal outcome has not been proven yet; downstream work must not treat this checkpoint as finished.",
            }
        ]
        next_steps = [
            {
                "step_id": "step_resume_checkpoint",
                "priority": "now",
                "step": "Resume from the latest durable checkpoint and verify terminal completion separately.",
            }
        ]
        confidence = {
            "level": "medium",
            "rationale": "Progress is durably recorded, but terminal outcome truth is still pending.",
        }
    elif outcome == "failed":
        open_questions = [
            {
                "question_id": "q_failure_recovery",
                "question": "Is the correct recovery path a retry, repair, or operator review?",
            }
        ]
        failed_paths = [
            {
                "path_id": "fp_terminal_failure",
                "summary": latest_summary or "Detached job ended in failure.",
            }
        ]
        next_steps = [
            {
                "step_id": "step_investigate_failure",
                "priority": "now",
                "step": "Inspect the linked lifecycle truth and failure evidence before retrying.",
            }
        ]
        confidence = {
            "level": "high" if result_evidence_refs else "medium",
            "rationale": "Failure state is explicit in durable lifecycle truth.",
        }
    elif outcome == "blocked":
        open_questions = [
            {
                "question_id": "q_reconcile_conflict",
                "question": "Which terminal state should remain authoritative after reviewing the conflicting lifecycle truth?",
            }
        ]
        failed_paths = [
            {
                "path_id": "fp_conflicting_terminal_truth",
                "summary": latest_summary or "Detached job has conflicting or unreconciled terminal truth.",
            }
        ]
        next_steps = [
            {
                "step_id": "step_review_conflict",
                "priority": "now",
                "step": "Resolve the conflict before allowing any successor to treat this job as successful.",
            }
        ]
        confidence = {
            "level": "low",
            "rationale": "Conflicting terminal truth blocks a clean continuation claim.",
        }
    else:
        open_questions = [
            {
                "question_id": "q_resume_or_retry",
                "question": "Should the next run resume from the last checkpoint or start a fresh retry?",
            }
        ]
        failed_paths = [
            {
                "path_id": "fp_interrupted_progress",
                "summary": progress_message or summary or "Detached job stopped without a clean terminal finish.",
            }
        ]
        next_steps = [
            {
                "step_id": "step_resume_or_retry",
                "priority": "now",
                "step": "Use the linked lifecycle truth to choose resume versus retry.",
            }
        ]
        confidence = {
            "level": "medium",
            "rationale": "Interruption truth is durable, but follow-up still depends on the last checkpoint and missing work.",
        }

    return {
        "schema_version": "continuation_packet_v1",
        "packet_id": f"detached_job:{detached_job_id}",
        "packet_revision": 1,
        "created_utc": now_utc,
        "source_run": {
            "job_id": detached_job_id,
            "run_id": request_id,
            "attempt_id": _clean_text(record.get("launch_turn_id")),
            "receipt_refs": receipt_refs,
        },
        "handoff_brief": summary,
        "result_summary": {
            "outcome": outcome,
            "summary": summary,
        },
        "evidence_created": evidence_created,
        "evidence_pack_refs": [],
        "artifact_index": artifact_index,
        "open_questions": open_questions,
        "failed_paths": failed_paths,
        "recommended_next_steps": next_steps,
        "confidence": confidence,
        "applicability": {
            "scope": "immediate_successor",
            "limitations": [
                "Linked detached-job lifecycle truth remains authoritative for raw status and terminal-state identity.",
            ],
        },
    }


def _packet_compare_basis(packet: Dict[str, Any]) -> Dict[str, Any]:
    basis = deepcopy(dict(packet))
    basis.pop("packet_revision", None)
    basis.pop("created_utc", None)
    basis["supersedes_packet_id"] = _clean_text(basis.get("supersedes_packet_id"))
    return basis


def emit_continuation_packet(
    repo_root: Path,
    *,
    detached_job_id: str,
    writer: str,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_RUNTIME_TRUTH,
        writer=writer,
    )
    timestamp = _clean_text(now_utc) or _utc_iso()
    record = _load_record(repo_root, detached_job_id)
    payload = _continuation_packet_payload(repo_root=repo_root, record=record, now_utc=timestamp)
    if payload is None:
        reason = _packet_summary_and_outcome(record)[2] or "continuation_packet_emission_blocked"
        _event(
            repo_root,
            event_type="continuation_packet_blocked",
            now=_parse_utc(timestamp),
            payload={
                "writer": _clean_text(writer),
                "detached_job_id": detached_job_id,
                "reason": reason,
            },
        )
        return {"ok": True, "emitted": False, "reason": reason}

    latest = _continuation_packet_store.latest_valid_packet(
        lookup_key="job_id",
        lookup_value=payload["source_run"]["job_id"],
        repo_root=repo_root,
    )
    if latest is not None and str(latest.get("packet_id") or "") == payload["packet_id"]:
        current_basis = _packet_compare_basis(dict(latest.get("packet") or latest))
        candidate_basis = _packet_compare_basis(payload)
        if current_basis == candidate_basis:
            return {"ok": True, "emitted": False, "reason": "idempotent_existing_packet", "packet_record": dict(latest)}
        payload["packet_revision"] = int(latest.get("packet_revision") or 0) + 1

    result = _continuation_packet_store.append_packet_revision_once(
        payload,
        lookup_keys={
            "job_id": payload["source_run"]["job_id"],
            "run_id": payload["source_run"]["run_id"],
            "attempt_id": payload["source_run"].get("attempt_id") or "",
            "job_family": f"detached_job:{_clean_text(record.get('job_surface')) or 'unknown'}",
        },
        repo_root=repo_root,
    )
    _event(
        repo_root,
        event_type="continuation_packet_emitted",
        now=_parse_utc(timestamp),
        payload={
            "writer": _clean_text(writer),
            "detached_job_id": detached_job_id,
            "packet_id": payload["packet_id"],
            "packet_revision": int(payload["packet_revision"]),
            "outcome": str(((payload.get("result_summary") or {}) if isinstance(payload.get("result_summary"), dict) else {}).get("outcome") or ""),
        },
    )
    return {"ok": True, "emitted": True, "packet_record": dict(result.get("record") or {})}


def read_job(repo_root: Path, *, detached_job_id: str) -> Dict[str, Any]:
    return _load_record(Path(repo_root).resolve(), detached_job_id)


def read_snapshot(repo_root: Path, *, initialize_if_missing: bool = True) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    snapshot = _read_json(snapshot_path(repo_root))
    if snapshot:
        return snapshot
    if not initialize_if_missing:
        return {
            "schema_version": DETACHED_JOB_SNAPSHOT_SCHEMA_VERSION,
            "writer": "",
            "generated_utc": "",
            "job_count": 0,
            "counts": {},
            "jobs": [],
            "events_path": str(events_path(repo_root)),
        }
    with _LOCK:
        return _write_snapshot(repo_root, writer="detached_job.read")


def accept_mailbox_handoff(
    repo_root: Path,
    *,
    request_record: Dict[str, Any],
    writer: str,
    heartbeat_interval_s: int = 60,
    heartbeat_timeout_s: Optional[int] = None,
    timeout_s: int = 3600,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_RUNTIME_TRUTH,
        writer=writer,
    )
    record = dict(request_record or {})
    request = dict(record.get("request") or {})
    handoff = dict(record.get("handoff") or {})
    detached_job_id = _clean_text(handoff.get("detached_job_id"))
    accepted_utc = _clean_text(handoff.get("accepted_utc") or now_utc) or _utc_iso()
    if _clean_text(record.get("state")).lower() != "handed_off":
        raise DetachedJobLifecycleError("mailbox handoff must be durably accepted before creating detached job lifecycle")
    now = _parse_utc(now_utc) if _clean_text(now_utc) else _parse_utc(accepted_utc)
    heartbeat_every = max(1, int(heartbeat_interval_s or 60))
    heartbeat_limit = max(heartbeat_every + 1, int(heartbeat_timeout_s or max(heartbeat_every * 3, 180)))
    with _LOCK:
        existing = validate_mailbox_handoff_target(repo_root, request_record=record)
        if existing:
            snapshot = _write_snapshot(repo_root, writer=writer, now=now)
            return {"ok": True, "created": False, "job_record": existing, "job_snapshot": snapshot}
        row = {
            "schema_version": DETACHED_JOB_SCHEMA_VERSION,
            "detached_job_id": detached_job_id,
            "request_id": _clean_text(record.get("request_id") or request.get("request_id")),
            "correlation_id": _clean_text(request.get("correlation_id")),
            "launch_turn_id": _clean_text(request.get("launch_turn_id")),
            "owner_ref": dict(request.get("owner_ref") or {}),
            "job_surface": _clean_text(handoff.get("job_surface")),
            "mailbox_request_id": _clean_text(record.get("request_id")),
            "mailbox_claim_id": _clean_text(handoff.get("claim_id")),
            "accepted_utc": accepted_utc,
            "created_utc": accepted_utc,
            "updated_utc": accepted_utc,
            "state": "queued",
            "state_reason": "mailbox_handoff_accepted",
            "worker_id": _clean_text(handoff.get("worker_id")),
            "heartbeat_interval_s": heartbeat_every,
            "heartbeat_timeout_s": heartbeat_limit,
            "timeout_s": max(0, int(timeout_s or 0)),
            "deadline_utc": _deadline_utc(now, int(timeout_s)) if int(timeout_s or 0) > 0 else "",
            "last_heartbeat_utc": "",
            "last_progress_utc": "",
            "last_progress_message": "",
            "progress_count": 0,
            "cancellation_requested": False,
            "cancellation_requested_utc": "",
            "cancellation_reason": "",
            "late_result": False,
            "late_result_pending_reconciliation": False,
            "late_result_basis_state": "",
            "superseded_result_count": 0,
            "latest_result": {},
            "terminal_result": {},
            "superseded_results": [],
            "reconciliation": {
                "last_reconciled_utc": "",
                "last_reconcile_mode": "",
                "late_result_reconciled_utc": "",
                "late_result_reconciled_by": "",
                "late_result_reconciliation_reason": "",
            },
        }
        stored = _store_record(repo_root, row)
        snapshot = _write_snapshot(repo_root, writer=writer, now=now)
        _event(
            repo_root,
            event_type="accepted",
            now=now,
            payload={
                "writer": _clean_text(writer),
                "detached_job_id": detached_job_id,
                "request_id": stored["request_id"],
                "job_surface": stored["job_surface"],
            },
        )
        return {"ok": True, "created": True, "job_record": stored, "job_snapshot": snapshot}


def heartbeat_job(
    repo_root: Path,
    *,
    detached_job_id: str,
    writer: str,
    worker_id: str = "",
    progress_message: str = "",
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_RUNTIME_TRUTH,
        writer=writer,
    )
    now = _parse_utc(now_utc) if _clean_text(now_utc) else _utc_now()
    with _LOCK:
        record = _load_record(repo_root, detached_job_id)
        if record["state"] in _TERMINAL_STATES:
            raise DetachedJobLifecycleError(f"detached job {detached_job_id} is terminal and cannot heartbeat")
        record["state"] = "running"
        record["state_reason"] = "worker_heartbeat"
        record["updated_utc"] = _utc_iso(now)
        record["last_heartbeat_utc"] = _utc_iso(now)
        record["worker_id"] = _clean_text(worker_id) or record["worker_id"]
        message = _clean_text(progress_message)
        if message:
            record["last_progress_message"] = message
            record["last_progress_utc"] = _utc_iso(now)
        record["progress_count"] = int(record.get("progress_count") or 0) + 1
        stored = _store_record(repo_root, record)
        snapshot = _write_snapshot(repo_root, writer=writer, now=now)
        _event(
            repo_root,
            event_type="heartbeat",
            now=now,
            payload={
                "writer": _clean_text(writer),
                "detached_job_id": stored["detached_job_id"],
                "worker_id": stored["worker_id"],
                "progress_message": message,
            },
        )
        _record_task_checkpoint_and_decision(
            repo_root,
            job_record=stored,
            writer=writer,
            now_utc=_utc_iso(now),
            record_decision=False,
        )
        emit_continuation_packet(
            repo_root,
            detached_job_id=stored["detached_job_id"],
            writer=f"{writer}.continuation_packet",
            now_utc=_utc_iso(now),
        )
        return {"ok": True, "job_record": stored, "job_snapshot": snapshot}


def cancel_job(
    repo_root: Path,
    *,
    detached_job_id: str,
    writer: str,
    reason: str,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_RUNTIME_TRUTH,
        writer=writer,
    )
    why = _clean_text(reason)
    if not why:
        raise DetachedJobLifecycleError("cancellation reason is required")
    now = _parse_utc(now_utc) if _clean_text(now_utc) else _utc_now()
    with _LOCK:
        record = _load_record(repo_root, detached_job_id)
        if record["state"] not in _TERMINAL_STATES:
            record["state"] = "cancelled"
            record["state_reason"] = f"cancelled:{why}"
            record["updated_utc"] = _utc_iso(now)
        record["cancellation_requested"] = True
        record["cancellation_requested_utc"] = _utc_iso(now)
        record["cancellation_reason"] = why
        stored = _store_record(repo_root, record)
        snapshot = _write_snapshot(repo_root, writer=writer, now=now)
        _event(
            repo_root,
            event_type="cancelled",
            now=now,
            payload={"writer": _clean_text(writer), "detached_job_id": stored["detached_job_id"], "reason": why},
        )
        emit_continuation_packet(
            repo_root,
            detached_job_id=stored["detached_job_id"],
            writer=f"{writer}.continuation_packet",
            now_utc=_utc_iso(now),
        )
        return {"ok": True, "job_record": stored, "job_snapshot": snapshot}


def record_job_result(
    repo_root: Path,
    *,
    detached_job_id: str,
    status: str,
    summary: str,
    writer: str,
    producer: str = "",
    result_id: str = "",
    evidence_refs: Optional[List[str]] = None,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_RUNTIME_TRUTH,
        writer=writer,
    )
    normalized_status = _clean_text(status).lower()
    if normalized_status not in {"completed", "failed", "cancelled", "ambiguous"}:
        raise DetachedJobLifecycleError(f"invalid detached job terminal status: {normalized_status or '<missing>'}")
    now = _parse_utc(now_utc) if _clean_text(now_utc) else _utc_now()
    with _LOCK:
        record = _load_record(repo_root, detached_job_id)
        current_state = record["state"]
        prior_terminal_result_id = _clean_text(
            ((record.get("terminal_result") or {}) if isinstance(record.get("terminal_result"), dict) else {}).get("result_id")
        )
        result = {
            "result_id": _clean_text(result_id) or f"job_result_{uuid.uuid4().hex}",
            "status": normalized_status,
            "summary": _clean_text(summary),
            "recorded_utc": _utc_iso(now),
            "producer": _clean_text(producer),
            "evidence_refs": [str(item).strip() for item in list(evidence_refs or []) if str(item).strip()],
            "superseded": False,
            "late_result": current_state in {"timed_out", "cancelled"},
        }
        if current_state in {"completed", "failed"}:
            result["superseded"] = True
            superseded = list(record.get("superseded_results") or [])
            superseded.append(result)
            record["superseded_results"] = superseded[-20:]
            record["superseded_result_count"] = int(record.get("superseded_result_count") or 0) + 1
        elif current_state == "cancelled":
            record["state"] = "ambiguous"
            record["state_reason"] = "conflicting_terminal_after_cancelled"
            record["late_result"] = True
            record["late_result_pending_reconciliation"] = True
            record["late_result_basis_state"] = current_state
            record["latest_result"] = result
            record["terminal_result"] = result
        elif current_state == "timed_out":
            record["state"] = normalized_status
            record["state_reason"] = "late_terminal_after_timeout_pending_reconciliation"
            record["late_result"] = True
            record["late_result_pending_reconciliation"] = True
            record["late_result_basis_state"] = current_state
            record["latest_result"] = result
            record["terminal_result"] = result
        else:
            record["state"] = normalized_status
            record["state_reason"] = "terminal_result_recorded"
            record["late_result"] = bool(result.get("late_result"))
            record["late_result_pending_reconciliation"] = False
            record["late_result_basis_state"] = ""
            record["latest_result"] = result
            record["terminal_result"] = result
        record["updated_utc"] = _utc_iso(now)
        stored = _store_record(repo_root, record)
        snapshot = _write_snapshot(repo_root, writer=writer, now=now)
        _event(
            repo_root,
            event_type="terminal_result",
            now=now,
            payload={
                "writer": _clean_text(writer),
                "detached_job_id": stored["detached_job_id"],
                "status": normalized_status,
                "superseded": bool(result.get("superseded")),
                "late_result": bool(result.get("late_result")),
            },
        )
        suppress_duplicate_correlation = False
        if bool(result.get("superseded")) and prior_terminal_result_id and stored["correlation_id"]:
            prior_result_record = _read_json(_correlation.result_path(repo_root, prior_terminal_result_id))
            prior_delivery_status = _clean_text(prior_result_record.get("delivery_status")).lower()
            prior_attached_correlation_id = _clean_text(prior_result_record.get("attached_correlation_id"))
            suppress_duplicate_correlation = (
                prior_attached_correlation_id == stored["correlation_id"]
                and prior_delivery_status in {"pending_reattach", "delivered"}
            )
        if suppress_duplicate_correlation:
            _event(
                repo_root,
                event_type="duplicate_terminal_result_correlation_suppressed",
                now=now,
                payload={
                    "writer": _clean_text(writer),
                    "detached_job_id": stored["detached_job_id"],
                    "result_id": result["result_id"],
                    "prior_result_id": prior_terminal_result_id,
                },
            )
        else:
            _correlation.record_detached_result(
                repo_root,
                result=_policy.validate_typed_delegation_result(
                    {
                        "schema_version": _policy.DELEGATION_RESULT_SCHEMA_VERSION,
                        "result_id": result["result_id"],
                        "request_id": stored["request_id"],
                        "surface": stored["job_surface"] or "mailbox",
                        "status": "ambiguous_artifact" if stored["state"] == "ambiguous" else normalized_status,
                        "summary": _clean_text(summary),
                        "native_status": normalized_status,
                        "evidence_status": "present" if list(result.get("evidence_refs") or []) else "missing",
                        "evidence_refs": list(result.get("evidence_refs") or []),
                        "artifact_refs": [],
                        "next_decision": {
                            "kind": "review_evidence" if normalized_status == "completed" else "assistant_continue",
                            "summary": (
                                f"Review detached result for {stored['detached_job_id']}."
                                if normalized_status == "completed"
                                else f"Inspect detached result for {stored['detached_job_id']}."
                            ),
                            "owner": "assistant",
                        },
                        "created_utc": _clean_text(result.get("recorded_utc") or _utc_iso(now)),
                        "updated_utc": _clean_text(result.get("recorded_utc") or _utc_iso(now)),
                        "correlation_id": stored["correlation_id"],
                        "launch_turn_id": stored["launch_turn_id"],
                        "detached_job_id": stored["detached_job_id"],
                        "result_correlation_basis": "detached_job_id" if stored["detached_job_id"] else "request_id",
                        "worker_ref": stored["worker_id"],
                        "producer": _clean_text(producer) or "detached_job_lifecycle",
                        "launch_cause": "",
                        "parent_request_id": "",
                        "prior_result_id": prior_terminal_result_id,
                        "owner_ref": dict(stored.get("owner_ref") or {}),
                    }
                ),
                writer=f"{writer}.correlation",
            )
        emit_continuation_packet(
            repo_root,
            detached_job_id=stored["detached_job_id"],
            writer=f"{writer}.continuation_packet",
            now_utc=_utc_iso(now),
        )
        return {"ok": True, "job_record": stored, "job_snapshot": snapshot}


def reconcile_late_result(
    repo_root: Path,
    *,
    detached_job_id: str,
    writer: str,
    final_state: str,
    reason: str,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_RUNTIME_TRUTH,
        writer=writer,
    )
    desired_state = _clean_text(final_state).lower()
    why = _clean_text(reason)
    if desired_state not in {"completed", "failed", "cancelled", "ambiguous"}:
        raise DetachedJobLifecycleError(f"invalid late result reconciliation state: {desired_state or '<missing>'}")
    if not why:
        raise DetachedJobLifecycleError("late result reconciliation reason is required")
    now = _parse_utc(now_utc) if _clean_text(now_utc) else _utc_now()
    with _LOCK:
        record = _load_record(repo_root, detached_job_id)
        if not bool(record.get("late_result_pending_reconciliation")):
            raise DetachedJobLifecycleError(f"detached job {detached_job_id} has no pending late result reconciliation")
        terminal_result = dict(record.get("terminal_result") or {})
        terminal_status = _clean_text(terminal_result.get("status")).lower()
        if desired_state in {"completed", "failed"} and terminal_status and terminal_status != desired_state:
            raise DetachedJobLifecycleError(
                f"late result reconciliation state {desired_state} conflicts with terminal result status {terminal_status}"
            )
        record["state"] = desired_state
        record["state_reason"] = f"late_result_reconciled:{why}"
        record["updated_utc"] = _utc_iso(now)
        record["late_result_pending_reconciliation"] = False
        if desired_state not in {"completed", "failed"}:
            record["latest_result"] = terminal_result if terminal_result else dict(record.get("latest_result") or {})
        record["reconciliation"] = {
            **dict(record.get("reconciliation") or {}),
            "late_result_reconciled_utc": _utc_iso(now),
            "late_result_reconciled_by": _clean_text(writer),
            "late_result_reconciliation_reason": why,
        }
        stored = _store_record(repo_root, record)
        snapshot = _write_snapshot(repo_root, writer=writer, now=now)
        _event(
            repo_root,
            event_type="late_result_reconciled",
            now=now,
            payload={
                "writer": _clean_text(writer),
                "detached_job_id": stored["detached_job_id"],
                "state": desired_state,
                "reason": why,
            },
        )
        emit_continuation_packet(
            repo_root,
            detached_job_id=stored["detached_job_id"],
            writer=f"{writer}.continuation_packet",
            now_utc=_utc_iso(now),
        )
        return {"ok": True, "job_record": stored, "job_snapshot": snapshot}


def reconcile_jobs(
    repo_root: Path,
    *,
    writer: str,
    mode: str = "steady_state",
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_RUNTIME_TRUTH,
        writer=writer,
    )
    reconcile_mode = _clean_text(mode).lower() or "steady_state"
    if reconcile_mode not in {"steady_state", "startup"}:
        raise DetachedJobLifecycleError(f"invalid reconcile mode: {reconcile_mode}")
    now = _parse_utc(now_utc) if _clean_text(now_utc) else _utc_now()
    changed: List[str] = []
    with _LOCK:
        for record in _records(repo_root):
            if record["state"] in _TERMINAL_STATES:
                continue
            updated = dict(record)
            if int(updated.get("timeout_s") or 0) > 0 and _clean_text(updated.get("deadline_utc")):
                if _parse_utc(updated["deadline_utc"]) <= now:
                    updated["state"] = "timed_out"
                    updated["state_reason"] = "timeout_deadline_elapsed"
            if updated["state"] not in _TERMINAL_STATES:
                base_utc = _clean_text(updated.get("last_heartbeat_utc") or updated.get("accepted_utc"))
                if base_utc and _stale_deadline(updated, base_utc) <= now:
                    updated["state"] = "interrupted" if reconcile_mode == "startup" else "heartbeat_stale"
                    updated["state_reason"] = "startup_reconcile_missing_heartbeat" if reconcile_mode == "startup" else "heartbeat_missing"
            updated["updated_utc"] = _utc_iso(now)
            updated["reconciliation"] = {
                "last_reconciled_utc": _utc_iso(now),
                "last_reconcile_mode": reconcile_mode,
                "late_result_reconciled_utc": _clean_text(((updated.get("reconciliation") or {}) if isinstance(updated.get("reconciliation"), dict) else {}).get("late_result_reconciled_utc")),
                "late_result_reconciled_by": _clean_text(((updated.get("reconciliation") or {}) if isinstance(updated.get("reconciliation"), dict) else {}).get("late_result_reconciled_by")),
                "late_result_reconciliation_reason": _clean_text(((updated.get("reconciliation") or {}) if isinstance(updated.get("reconciliation"), dict) else {}).get("late_result_reconciliation_reason")),
            }
            if _normalize_record(updated) != record:
                _store_record(repo_root, updated)
                changed.append(updated["detached_job_id"])
                _event(
                    repo_root,
                    event_type="reconciled",
                    now=now,
                    payload={
                        "writer": _clean_text(writer),
                        "detached_job_id": updated["detached_job_id"],
                        "state": updated["state"],
                        "mode": reconcile_mode,
                    },
                )
                _record_task_checkpoint_and_decision(
                    repo_root,
                    job_record=updated,
                    writer=writer,
                    now_utc=_utc_iso(now),
                    record_decision=_clean_text(updated.get("state")).lower()
                    in {"interrupted", "heartbeat_stale", "timed_out"},
                )
                emit_continuation_packet(
                    repo_root,
                    detached_job_id=updated["detached_job_id"],
                    writer=f"{writer}.continuation_packet",
                    now_utc=_utc_iso(now),
                )
        snapshot = _write_snapshot(repo_root, writer=writer, now=now)
    return {"ok": True, "changed_job_ids": changed, "job_snapshot": snapshot}
