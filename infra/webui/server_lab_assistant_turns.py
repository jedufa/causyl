"""T1R4 durable direct-turn identity and lifecycle contract."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]

from infra.webui import server_lab_assistant_correlation as _correlation
from infra.webui import server_lab_assistant_task_register as _register


DIRECT_TURN_SCHEMA_VERSION = "lab_assistant_direct_turn_v1"
DIRECT_TURN_EVENT_SCHEMA_VERSION = "lab_assistant_direct_turn_event_v1"
DIRECT_TURN_SNAPSHOT_SCHEMA_VERSION = "lab_assistant_direct_turn_snapshot_v1"
DIRECT_TURN_REPLAY_SCHEMA_VERSION = "lab_assistant_direct_turn_replay_v1"
DIRECT_TURN_REATTACH_SCHEMA_VERSION = "lab_assistant_direct_turn_reattach_v1"
DIRECT_TURN_REATTACH_EVENT_SCHEMA_VERSION = "lab_assistant_direct_turn_reattach_event_v1"
DIRECT_TURN_CLASSIFICATION_SCHEMA_VERSION = "lab_assistant_direct_turn_classification_v1"
DIRECT_TURN_BOOT_RECONCILIATION_SCHEMA_VERSION = "lab_assistant_direct_turn_boot_reconciliation_v1"
DIRECT_TURN_RECENT_PROJECTION_SCHEMA_VERSION = "lab_assistant_direct_turn_recent_projection_v1"
DIRECT_TURN_FINAL_COMMIT_STATES = ("not_started", "pending", "committed")
DIRECT_TURN_ALLOWED_STATES = (
    "submitted",
    "running",
    "awaiting_resume",
    "completed",
    "failed",
    "cancelled",
)
DIRECT_TURN_TERMINAL_STATES = {"completed", "failed", "cancelled"}
DIRECT_TURN_ACTIVE_STATES = {"submitted", "running", "awaiting_resume"}
DIRECT_TURN_OUTPUT_EVENT_TYPES = {"output_delta", "progress", "terminal"}
DIRECT_TURN_TRANSITIONS = {
    "submitted": {"running", "awaiting_resume", "completed", "failed", "cancelled"},
    "running": {"awaiting_resume", "completed", "failed", "cancelled"},
    # A resumed turn can already have terminal output committed before late
    # lifecycle bookkeeping flips the durable state back to completion.
    "awaiting_resume": {"running", "completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}
_LOCK = threading.RLock()


class DirectTurnError(RuntimeError):
    """Raised when direct-turn truth cannot be mutated honestly."""


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[_dt.datetime] = None) -> str:
    when = dt or _utc_now()
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_name(value: Any) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in _clean_text(value)).strip("-")
    return safe or "x"


def _normalized_message(message: Any) -> str:
    return _clean_text(message)


def _binding_obj(binding: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(binding, dict):
        raise DirectTurnError("binding must be an object")
    binding_type = _clean_text(binding.get("type")).lower()
    binding_id = _clean_text(binding.get("id"))
    if binding_type not in {"task_id", "ledger_uid"} or not binding_id:
        raise DirectTurnError("binding requires durable type/id")
    return {"type": binding_type, "id": binding_id}


def _task_for_binding(register: Dict[str, Any], binding: Dict[str, str]) -> Optional[Dict[str, Any]]:
    tasks = list(register.get("active_tasks") or [])
    if binding.get("type") == "task_id":
        task_id = _clean_text(binding.get("id"))
        for task in tasks:
            if _clean_text(task.get("task_id")) == task_id:
                return dict(task)
        return None
    if binding.get("type") == "ledger_uid":
        ledger_uid = _clean_text(binding.get("id"))
        matches = [dict(task) for task in tasks if _clean_text(task.get("ledger_uid")) == ledger_uid]
        if len(matches) == 1:
            return matches[0]
    return None


def _checkpoint_status_for_turn_state(state: str) -> str:
    normalized = _clean_text(state).lower()
    if normalized == "completed":
        return "finalized"
    if normalized in {"failed", "cancelled", "awaiting_resume"}:
        return "stale"
    return "recorded"


def _checkpoint_for_turn(repo_root: Path, *, turn: Dict[str, Any], writer: str) -> Dict[str, Any]:
    turn_id = _clean_text(turn.get("turn_id"))
    record_ref = record_path(repo_root, turn_id)
    events_ref = events_path(repo_root)
    try:
        record_rel = str(record_ref.relative_to(Path(repo_root).resolve()))
    except Exception:
        record_rel = str(record_ref)
    try:
        events_rel = str(events_ref.relative_to(Path(repo_root).resolve()))
    except Exception:
        events_rel = str(events_ref)
    return {
        "schema_version": _register.TASK_RUN_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": f"direct_turn:{turn_id}",
        "checkpoint_status": _checkpoint_status_for_turn_state(turn.get("state")),
        "checkpoint_utc": _clean_text(turn.get("updated_utc")) or _utc_iso(),
        "checkpoint_note": f"direct_turn_state:{_clean_text(turn.get('state')).lower()}",
        "checkpoint_source": _clean_text(writer),
        "checkpoint_revision": int(turn.get("generation") or 0),
        "progress_percent": None,
        "progress_summary": _clean_text(turn.get("state_reason")),
        "evidence_refs": [record_rel, events_rel],
    }


def _resume_decision_for_turn(
    *,
    turn: Dict[str, Any],
    checkpoint: Dict[str, Any],
    writer: str,
) -> Optional[Dict[str, Any]]:
    state = _clean_text(turn.get("state")).lower()
    if state == "awaiting_resume":
        decision = "blocked"
    elif state in {"failed", "cancelled"}:
        decision = "retry"
    else:
        return None
    return {
        "schema_version": _register.TASK_RUN_RESUME_DECISION_SCHEMA_VERSION,
        "decision": decision,
        "decision_reason": f"direct_turn_state:{state}",
        "decision_summary": _clean_text(turn.get("state_reason")),
        "decision_source": "direct_turn_lifecycle",
        "checkpoint_id": _clean_text(checkpoint.get("checkpoint_id")),
        "evidence_refs": list(checkpoint.get("evidence_refs") or []),
    }


def _record_direct_turn_binding(repo_root: Path, *, turn: Dict[str, Any], writer: str) -> Optional[Dict[str, Any]]:
    root = Path(repo_root).resolve()
    try:
        snapshot = _register.read_task_register_snapshot(root, initialize_if_missing=False)
    except Exception:
        return None
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    if not register:
        return None
    task = _task_for_binding(register, dict(turn.get("binding") or {}))
    if not task:
        return None
    turn_id = _clean_text(turn.get("turn_id"))
    if not turn_id:
        return None
    record_ref = record_path(root, turn_id)
    try:
        job_uri = str(record_ref.relative_to(root))
    except Exception:
        job_uri = str(record_ref)
    checkpoint = _checkpoint_for_turn(root, turn=turn, writer=writer)
    try:
        return _register.record_execution_surface_ownership(
            root,
            expected_generation=int(register.get("write_generation") or 0),
            writer=writer,
            task_id=_clean_text(task.get("task_id")),
            selected_surface="direct_turn_job",
            execution_id=turn_id,
            execution_ref=f"direct_turn:{turn_id}",
            binding_ref=f"direct_turn:{turn_id}",
            job_uri=job_uri,
            trace_id=turn_id,
            checkpoint=checkpoint,
            reference_status="execution_ref_unresolved",
            reference_status_reason="direct_turn_created",
        )
    except Exception:
        return None


def _record_direct_turn_progress(repo_root: Path, *, turn: Dict[str, Any], writer: str) -> None:
    root = Path(repo_root).resolve()
    try:
        snapshot = _register.read_task_register_snapshot(root, initialize_if_missing=False)
    except Exception:
        return
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    if not register:
        return
    task = _task_for_binding(register, dict(turn.get("binding") or {}))
    if not task:
        return
    turn_id = _clean_text(turn.get("turn_id"))
    if not turn_id:
        return
    checkpoint = _checkpoint_for_turn(root, turn=turn, writer=writer)
    expected_generation = int(register.get("write_generation") or 0)
    try:
        updated = _register.record_task_run_checkpoint(
            root,
            expected_generation=expected_generation,
            writer=writer,
            task_id=_clean_text(task.get("task_id")),
            run_id=turn_id,
            checkpoint=checkpoint,
            auto_decide=False,
        )
        expected_generation = int((updated.get("task_register") or {}).get("write_generation") or expected_generation)
    except Exception:
        return
    decision = _resume_decision_for_turn(turn=turn, checkpoint=checkpoint, writer=writer)
    if not decision:
        return
    try:
        _register.record_task_run_resume_decision(
            root,
            expected_generation=expected_generation,
            writer=writer,
            task_id=_clean_text(task.get("task_id")),
            run_id=turn_id,
            resume_decision=decision,
        )
    except Exception:
        return


def _request_fingerprint(*, binding: Dict[str, str], role: str, message: str) -> str:
    payload = json.dumps(
        {
            "binding": binding,
            "role": _clean_text(role).lower(),
            "message": _normalized_message(message),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _duplicate_window_s() -> int:
    raw = _clean_text(os.environ.get("EIEL_DIRECT_TURN_DUPLICATE_WINDOW_S") or "30")
    try:
        value = int(raw)
    except Exception:
        value = 30
    return max(0, min(value, 3600))


def turns_root(repo_root: Path) -> Path:
    path = (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "direct_turns").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def records_dir(repo_root: Path) -> Path:
    path = (turns_root(repo_root) / "records").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_path(repo_root: Path, turn_id: str) -> Path:
    return (records_dir(repo_root) / f"{_safe_name(turn_id)}.json").resolve()


def events_path(repo_root: Path) -> Path:
    return (turns_root(repo_root) / "turn_events.jsonl").resolve()


def snapshot_path(repo_root: Path) -> Path:
    return (turns_root(repo_root) / "turn_snapshot.json").resolve()


def reattach_events_path(repo_root: Path) -> Path:
    return (turns_root(repo_root) / "turn_reattach_events.jsonl").resolve()


def lock_path(repo_root: Path) -> Path:
    return (turns_root(repo_root) / ".turns.lock").resolve()


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


def _normalize_optional_nonnegative_int(value: Any, *, field: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and not _clean_text(value):
        return None
    try:
        parsed = int(value)
    except Exception:
        raise DirectTurnError(f"{field} must be an integer")
    if parsed < 0:
        raise DirectTurnError(f"{field} must be >= 0")
    return parsed


def _normalize_turn(turn: Dict[str, Any]) -> Dict[str, Any]:
    binding = _binding_obj(turn.get("binding") if isinstance(turn.get("binding"), dict) else {})
    state = _clean_text(turn.get("state")).lower()
    if state not in DIRECT_TURN_ALLOWED_STATES:
        raise DirectTurnError(f"invalid turn state: {state}")
    generation = int(turn.get("generation") or 0)
    created_utc = _clean_text(turn.get("created_utc")) or _utc_iso()
    updated_utc = _clean_text(turn.get("updated_utc")) or created_utc
    state_since_utc = _clean_text(turn.get("state_since_utc")) or updated_utc
    started_utc = _clean_text(turn.get("started_utc"))
    terminal_utc = _clean_text(turn.get("terminal_utc"))
    final_commit_state = _clean_text(turn.get("final_commit_state")).lower() or "not_started"
    if final_commit_state not in DIRECT_TURN_FINAL_COMMIT_STATES:
        final_commit_state = "not_started"
    return {
        "schema_version": DIRECT_TURN_SCHEMA_VERSION,
        "turn_id": _clean_text(turn.get("turn_id")),
        "binding": binding,
        "role": _clean_text(turn.get("role")).lower(),
        "state": state,
        "state_reason": _clean_text(turn.get("state_reason")),
        "request_fingerprint": _clean_text(turn.get("request_fingerprint")),
        "request_text": _clean_text(turn.get("request_text")),
        "resend_key": _clean_text(turn.get("resend_key")),
        "resume_count": int(turn.get("resume_count") or 0),
        "duplicate_hits": int(turn.get("duplicate_hits") or 0),
        "advisory_transport_ids": list(dict.fromkeys(_clean_text(v) for v in list(turn.get("advisory_transport_ids") or []) if _clean_text(v))),
        "created_utc": created_utc,
        "updated_utc": updated_utc,
        "state_since_utc": state_since_utc,
        "started_utc": started_utc,
        "terminal_utc": terminal_utc,
        "generation": generation,
        "last_writer": _clean_text(turn.get("last_writer")),
        "resume_unproven_reason": _clean_text(turn.get("resume_unproven_reason")),
        "ambiguity_reason": _clean_text(turn.get("ambiguity_reason")),
        "last_event_seq": int(turn.get("last_event_seq") or 0),
        "last_event_type": _clean_text(turn.get("last_event_type")),
        "last_event_utc": _clean_text(turn.get("last_event_utc")),
        "output_cursor_chars": max(0, int(turn.get("output_cursor_chars") or 0)),
        "terminal_event_seq": max(0, int(turn.get("terminal_event_seq") or 0)),
        "final_commit_state": final_commit_state,
        "final_commit_attempts": max(0, int(turn.get("final_commit_attempts") or 0)),
        "final_commit_turn_event_seq": max(0, int(turn.get("final_commit_turn_event_seq") or 0)),
        "final_commit_pending_since_utc": _clean_text(turn.get("final_commit_pending_since_utc")),
        "final_commit_committed_utc": _clean_text(turn.get("final_commit_committed_utc")),
        "final_commit_last_error": _clean_text(turn.get("final_commit_last_error")),
        "final_commit_session_row_sha256": _clean_text(turn.get("final_commit_session_row_sha256")),
    }


def _reason_has_token(reason: str, *, tokens: List[str]) -> bool:
    lowered = _clean_text(reason).lower()
    return any(token in lowered for token in tokens)


def _derive_turn_classification(
    turn: Dict[str, Any],
    *,
    continuity_proven: Optional[bool],
    terminal_proven: Optional[bool],
    interruption_reason: str,
    output_present: bool,
) -> Dict[str, Any]:
    state = _clean_text(turn.get("state")).lower()
    reason = _clean_text(turn.get("state_reason")).lower()
    ambiguity_reason = _clean_text(turn.get("ambiguity_reason"))
    terminal = state in DIRECT_TURN_TERMINAL_STATES
    interruption = _clean_text(interruption_reason)
    proof_known = continuity_proven is not None and terminal_proven is not None
    final_commit_state = _clean_text(turn.get("final_commit_state")).lower() or "not_started"
    terminal_ambiguous = (
        terminal
        and (
            not proof_known
            or not bool(continuity_proven)
            or not bool(terminal_proven)
        )
    )
    cancel_intent = _reason_has_token(
        reason,
        tokens=["cancel requested", "cancel_requested", "pending cancel", "cancellation requested"],
    )
    timeout_hint = _reason_has_token(reason, tokens=["timeout", "timed out", "deadline exceeded"])
    interrupt_hint = _reason_has_token(
        reason,
        tokens=["interrupt", "stopped", "transport dropped", "resume", "connection lost"],
    ) or bool(interruption)

    classification = "running"
    if final_commit_state == "pending" and output_present:
        classification = "pending_commit"
    elif terminal_ambiguous:
        classification = "ambiguous_terminal"
    elif state == "submitted":
        classification = "submitted"
    elif state == "running":
        classification = "cancel" if cancel_intent else "running"
    elif state == "awaiting_resume":
        if timeout_hint:
            classification = "timed_out"
        elif cancel_intent:
            classification = "cancel"
        else:
            classification = "interrupted"
    elif state == "completed":
        classification = "finished"
    elif state == "failed":
        if timeout_hint:
            classification = "timed_out"
        elif interrupt_hint:
            classification = "interrupted"
        else:
            classification = "failed"
    elif state == "cancelled":
        classification = "cancelled"

    return {
        "schema_version": DIRECT_TURN_CLASSIFICATION_SCHEMA_VERSION,
        "state": state,
        "status": classification,
        "terminal": bool(terminal and not terminal_ambiguous),
        "terminal_ambiguous": bool(terminal_ambiguous),
        "continuity_proven": continuity_proven,
        "terminal_proven": terminal_proven,
        "output_present": bool(output_present),
        "interruption_reason": interruption,
        "ambiguity_reason": ambiguity_reason,
        "final_commit_state": final_commit_state,
        "pending_commit": bool(final_commit_state == "pending"),
    }


def _mark_final_commit_state(
    repo_root: Path,
    *,
    turn_id: str,
    writer: str,
    new_state: str,
    turn_event_seq: Optional[int] = None,
    session_row_sha256: str = "",
    error: str = "",
) -> Dict[str, Any]:
    if new_state not in DIRECT_TURN_FINAL_COMMIT_STATES:
        raise DirectTurnError(f"invalid final commit state: {new_state}")
    with _LOCK:
        lock_path(repo_root).parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path(repo_root), "a+", encoding="utf-8") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                current = _load_turn(repo_root, turn_id)
                now = _utc_iso()
                current_state = _clean_text(current.get("final_commit_state")).lower() or "not_started"
                if current_state == "committed":
                    if new_state == "committed":
                        committed_sha = _clean_text(current.get("final_commit_session_row_sha256"))
                        wanted_sha = _clean_text(session_row_sha256)
                        if committed_sha and wanted_sha and committed_sha != wanted_sha:
                            raise DirectTurnError("final transcript commit already completed with different payload")
                        return {"ok": True, "changed": False, "turn": current}
                    return {"ok": True, "changed": False, "turn": current}
                if new_state == "not_started":
                    return {"ok": True, "changed": False, "turn": current}
                changed = False
                if current_state != new_state:
                    current["final_commit_state"] = new_state
                    changed = True
                if new_state == "pending":
                    if not _clean_text(current.get("final_commit_pending_since_utc")):
                        current["final_commit_pending_since_utc"] = now
                        changed = True
                    current["final_commit_attempts"] = int(current.get("final_commit_attempts") or 0) + 1
                    changed = True
                    current["final_commit_last_error"] = _clean_text(error)
                elif new_state == "committed":
                    current["final_commit_committed_utc"] = now
                    current["final_commit_last_error"] = ""
                    current["final_commit_session_row_sha256"] = _clean_text(session_row_sha256)
                    changed = True
                if turn_event_seq is not None:
                    parsed_seq = max(0, int(turn_event_seq))
                    if parsed_seq and parsed_seq != int(current.get("final_commit_turn_event_seq") or 0):
                        current["final_commit_turn_event_seq"] = parsed_seq
                        changed = True
                if not changed:
                    return {"ok": True, "changed": False, "turn": current}
                current["updated_utc"] = now
                current["generation"] = int(current.get("generation") or 0) + 1
                current["last_writer"] = _clean_text(writer)
                current["last_event_seq"] = int(current.get("last_event_seq") or 0) + 1
                event_type = "turn_final_commit_pending" if new_state == "pending" else "turn_final_commit_committed"
                current["last_event_type"] = event_type
                current["last_event_utc"] = now
                stored = _store_turn(repo_root, current)
                snapshot = _write_snapshot(repo_root, writer=writer)
                _record_event(
                    repo_root,
                    event_type=event_type,
                    payload={
                        "turn_id": stored["turn_id"],
                        "writer": _clean_text(writer),
                        "final_commit_state": new_state,
                        "turn_event_seq": int(stored.get("final_commit_turn_event_seq") or 0),
                        "session_row_sha256": _clean_text(stored.get("final_commit_session_row_sha256")),
                        "error": _clean_text(error),
                    },
                    turn=stored,
                )
                return {"ok": True, "changed": True, "turn": stored, "snapshot": snapshot}
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def mark_turn_final_commit_pending(
    repo_root: Path,
    *,
    turn_id: str,
    writer: str,
    turn_event_seq: Optional[int] = None,
    error: str = "",
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    return _mark_final_commit_state(
        root,
        turn_id=turn_id,
        writer=writer,
        new_state="pending",
        turn_event_seq=turn_event_seq,
        error=error,
    )


def mark_turn_final_commit_committed(
    repo_root: Path,
    *,
    turn_id: str,
    writer: str,
    session_row_sha256: str,
    turn_event_seq: Optional[int] = None,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    digest = _clean_text(session_row_sha256)
    if not digest:
        raise DirectTurnError("session_row_sha256 is required")
    return _mark_final_commit_state(
        root,
        turn_id=turn_id,
        writer=writer,
        new_state="committed",
        session_row_sha256=digest,
        turn_event_seq=turn_event_seq,
    )


def _record_event(repo_root: Path, *, event_type: str, payload: Dict[str, Any], turn: Optional[Dict[str, Any]] = None) -> None:
    seq = 0
    cursor_chars = 0
    turn_id = ""
    binding: Dict[str, str] = {}
    role = ""
    turn_generation = 0
    if turn:
        seq = max(0, int(turn.get("last_event_seq") or 0))
        cursor_chars = max(0, int(turn.get("output_cursor_chars") or 0))
        turn_id = _clean_text(turn.get("turn_id"))
        binding = dict(turn.get("binding") or {})
        role = _clean_text(turn.get("role")).lower()
        turn_generation = max(0, int(turn.get("generation") or 0))
    _append_jsonl(
        events_path(repo_root),
        {
            "schema_version": DIRECT_TURN_EVENT_SCHEMA_VERSION,
            "event_type": _clean_text(event_type),
            "utc": _utc_iso(),
            "turn_id": turn_id,
            "binding": binding,
            "role": role,
            "turn_generation": turn_generation,
            "event_seq": seq,
            "cursor": {
                "event_seq": seq,
                "content_chars": cursor_chars,
            },
            "payload": dict(payload or {}),
        },
    )


def _record_reattach_event(
    repo_root: Path,
    *,
    writer: str,
    turn: Optional[Dict[str, Any]],
    decision: Dict[str, Any],
) -> None:
    _append_jsonl(
        reattach_events_path(repo_root),
        {
            "schema_version": DIRECT_TURN_REATTACH_EVENT_SCHEMA_VERSION,
            "utc": _utc_iso(),
            "writer": _clean_text(writer),
            "turn_id": _clean_text((turn or {}).get("turn_id")),
            "binding": dict((turn or {}).get("binding") or {}),
            "role": _clean_text((turn or {}).get("role")).lower(),
            "decision": dict(decision or {}),
        },
    )


def _state_counts(repo_root: Path) -> Dict[str, int]:
    counts = {state: 0 for state in DIRECT_TURN_ALLOWED_STATES}
    for row in list_turns(repo_root):
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return counts


def _write_snapshot(repo_root: Path, *, writer: str) -> Dict[str, Any]:
    rows = list_turns(repo_root)
    payload = {
        "schema_version": DIRECT_TURN_SNAPSHOT_SCHEMA_VERSION,
        "writer": _clean_text(writer),
        "generated_utc": _utc_iso(),
        "turn_count": len(rows),
        "state_counts": _state_counts(repo_root),
        "turns": [
            {
                "turn_id": row["turn_id"],
                "binding": dict(row["binding"]),
                "role": row["role"],
                "state": row["state"],
                "generation": row["generation"],
                "request_fingerprint": row["request_fingerprint"],
                "resend_key": row["resend_key"],
                "updated_utc": row["updated_utc"],
                "path": str(record_path(repo_root, row["turn_id"])),
            }
            for row in rows
        ],
    }
    _write_json_atomic(snapshot_path(repo_root), payload)
    return payload


def _store_turn(repo_root: Path, turn: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_turn(turn)
    if not normalized["turn_id"]:
        raise DirectTurnError("turn_id is required")
    if not normalized["request_fingerprint"]:
        raise DirectTurnError("request_fingerprint is required")
    if not normalized["request_text"]:
        raise DirectTurnError("request_text is required")
    _write_json_atomic(record_path(repo_root, normalized["turn_id"]), normalized)
    return normalized


def _load_turn(repo_root: Path, turn_id: str) -> Dict[str, Any]:
    obj = _read_json(record_path(repo_root, turn_id))
    if not obj:
        raise DirectTurnError(f"turn not found: {turn_id}")
    return _normalize_turn(obj)


def list_turns(repo_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(records_dir(repo_root).glob("*.json"), key=lambda item: item.name):
        obj = _read_json(path)
        if not obj:
            continue
        try:
            rows.append(_normalize_turn(obj))
        except Exception:
            continue
    rows.sort(key=lambda row: (row["created_utc"], row["turn_id"]))
    return rows


def _transition_allowed(*, old_state: str, new_state: str) -> bool:
    if old_state == new_state:
        return True
    return new_state in DIRECT_TURN_TRANSITIONS.get(old_state, set())


def _matching_candidates(
    repo_root: Path,
    *,
    binding: Dict[str, str],
    role: str,
    request_fingerprint: str,
    resend_key: str,
) -> List[Dict[str, Any]]:
    now = _utc_now()
    out: List[Dict[str, Any]] = []
    for row in list_turns(repo_root):
        if row["binding"] != binding or row["role"] != role:
            continue
        if resend_key:
            if row["resend_key"] != resend_key:
                continue
            if row["request_fingerprint"] != request_fingerprint:
                continue
            if row["state"] not in DIRECT_TURN_ACTIVE_STATES.union(DIRECT_TURN_TERMINAL_STATES):
                continue
        elif row["request_fingerprint"] != request_fingerprint:
            continue
        elif row["state"] not in DIRECT_TURN_ACTIVE_STATES:
            continue
        updated = _dt.datetime.fromisoformat(row["updated_utc"].replace("Z", "+00:00"))
        if (now - updated).total_seconds() > float(_duplicate_window_s()):
            continue
        out.append(row)
    return out


def _evaluate_reattach(
    repo_root: Path,
    *,
    turn: Dict[str, Any],
    requested_after_event_seq: Optional[int],
    requested_content_chars: Optional[int],
) -> Dict[str, Any]:
    correlation_gate = _correlation_gate_for_turn(repo_root, turn_id=turn["turn_id"])
    replay = replay_turn_events(
        repo_root,
        turn_id=turn["turn_id"],
        after_event_seq=(requested_after_event_seq or 0),
    )
    reason = ""
    cursor_proven = True
    if requested_after_event_seq is None:
        cursor_proven = False
        reason = "resume_cursor_missing"
    elif requested_after_event_seq > int(replay["resume_cursor"]["after_event_seq"]):
        cursor_proven = False
        reason = "resume_cursor_ahead_of_durable"
    if cursor_proven and requested_content_chars is not None:
        if requested_content_chars > int(replay["resume_cursor"]["content_chars"]):
            cursor_proven = False
            reason = "resume_content_cursor_ahead_of_durable"
    continuity_proven = bool(replay.get("continuity_proven"))
    if not continuity_proven and not reason:
        interrupt_reason = _clean_text(replay.get("interruption_reason")) or "continuity_unproven"
        reason = f"resume_continuity_unproven:{interrupt_reason}"
    correlation_reattach = {
        "status": "clear",
        "exact_count": int(correlation_gate.get("exact_count", 0)),
        "ambiguous_count": int(correlation_gate.get("ambiguous_count", 0)),
        "unresolved_count": int(correlation_gate.get("unresolved_count", 0)),
        "result_ids": {
            "exact": list(correlation_gate.get("exact_result_ids") or []),
            "ambiguous": list(correlation_gate.get("ambiguous_result_ids") or []),
            "unresolved": list(correlation_gate.get("unresolved_result_ids") or []),
        },
    }
    if not correlation_gate["continuity_ok"]:
        continuity_proven = False
        reason = reason or correlation_gate["reason"]
        if correlation_gate["reason"].endswith("ambiguous"):
            correlation_reattach["status"] = "ambiguous"
        elif correlation_gate["reason"].endswith("unresolved"):
            correlation_reattach["status"] = "unresolved"
        elif correlation_gate["reason"].startswith("correlation_gate_unavailable"):
            correlation_reattach["status"] = "error"
    turn_state = _clean_text(turn.get("state")).lower()
    reattachable = continuity_proven and cursor_proven and turn_state in DIRECT_TURN_ACTIVE_STATES
    decision = "resume_existing_turn" if reattachable else "retry_new_turn"
    if turn_state in DIRECT_TURN_TERMINAL_STATES and continuity_proven and cursor_proven:
        reason = reason or "resume_turn_not_active"
    return {
        "schema_version": DIRECT_TURN_REATTACH_SCHEMA_VERSION,
        "turn": dict(turn),
        "requested_cursor": {
            "after_event_seq": requested_after_event_seq,
            "content_chars": requested_content_chars,
        },
        "resume_cursor": dict(replay["resume_cursor"]),
        "replay": replay,
        "continuity_proven": continuity_proven,
        "cursor_proven": cursor_proven,
        "reattachable": reattachable,
        "resume_decision": decision,
        "reason": reason,
        "correlation_reattach": correlation_reattach,
        "correlation_gate": correlation_gate,
    }


def _correlation_gate_for_turn(repo_root: Path, *, turn_id: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "continuity_ok": True,
        "turn_id": _clean_text(turn_id),
        "reason": "",
    }
    try:
        snapshot = _correlation.read_snapshot(repo_root, initialize_if_missing=False)
        results = list(snapshot.get("results") or [])
        exact = [row for row in results if _clean_text(row.get("launch_turn_id")) == turn_id and _clean_text(row.get("delivery_status")) in {"pending_reattach", "delivered"}]
        ambiguous = [row for row in results if _clean_text(row.get("launch_turn_id")) == turn_id and _clean_text(row.get("delivery_status")) == "ambiguous"]
        unresolved = [row for row in results if _clean_text(row.get("launch_turn_id")) == turn_id and _clean_text(row.get("delivery_status")) == "unresolved"]
        result.update(
            {
                "continuity_ok": not bool(ambiguous or unresolved),
                "exact_count": len(exact),
                "ambiguous_count": len(ambiguous),
                "unresolved_count": len(unresolved),
                "delivery_statuses": {
                    "pending_reattach": sum(1 for row in results if _clean_text(row.get("launch_turn_id")) == turn_id and _clean_text(row.get("delivery_status")) == "pending_reattach"),
                    "delivered": sum(1 for row in results if _clean_text(row.get("launch_turn_id")) == turn_id and _clean_text(row.get("delivery_status")) == "delivered"),
                    "ambiguous": len(ambiguous),
                    "unresolved": len(unresolved),
                },
                "exact_result_ids": [row.get("result_id") for row in exact],
                "ambiguous_result_ids": [row.get("result_id") for row in ambiguous],
                "unresolved_result_ids": [row.get("result_id") for row in unresolved],
            }
        )
        if ambiguous:
            result["reason"] = "resume_blocked_by_correlation_ambiguous"
        elif unresolved:
            result["reason"] = "resume_blocked_by_correlation_unresolved"
    except Exception as exc:  # pragma: no cover
        result.update(
            {
                "continuity_ok": False,
                "reason": f"correlation_gate_unavailable:{type(exc).__name__}",
                "exact_count": 0,
                "ambiguous_count": 0,
                "unresolved_count": 0,
                "delivery_statuses": {"pending_reattach": 0, "delivered": 0, "ambiguous": 0, "unresolved": 0},
                "exact_result_ids": [],
                "ambiguous_result_ids": [],
                "unresolved_result_ids": [],
            }
        )
    return result


def evaluate_reattach(
    repo_root: Path,
    *,
    binding: Dict[str, Any],
    role: str,
    turn_id: str,
    writer: str,
    after_event_seq: Optional[int] = None,
    content_chars: Optional[int] = None,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    normalized_binding = _binding_obj(binding)
    normalized_role = _clean_text(role).lower()
    if not normalized_role:
        raise DirectTurnError("role is required")
    with _LOCK:
        lock_path(root).parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path(root), "a+", encoding="utf-8") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                requested_after = _normalize_optional_nonnegative_int(after_event_seq, field="after_event_seq")
                requested_chars = _normalize_optional_nonnegative_int(content_chars, field="content_chars")
                turn = _load_turn(root, turn_id)
                if turn["binding"] != normalized_binding or turn["role"] != normalized_role:
                    decision = {
                        "schema_version": DIRECT_TURN_REATTACH_SCHEMA_VERSION,
                        "turn": dict(turn),
                        "requested_cursor": {
                            "after_event_seq": requested_after,
                            "content_chars": requested_chars,
                        },
                        "resume_cursor": {
                            "after_event_seq": int(turn.get("last_event_seq") or 0),
                            "content_chars": int(turn.get("output_cursor_chars") or 0),
                        },
                        "replay": {},
                        "continuity_proven": False,
                        "cursor_proven": False,
                        "reattachable": False,
                        "resume_decision": "retry_new_turn",
                        "reason": "resume_identity_unproven",
                    }
                    _record_reattach_event(root, writer=writer, turn=turn, decision=decision)
                    return decision
                decision = _evaluate_reattach(
                    root,
                    turn=turn,
                    requested_after_event_seq=requested_after,
                    requested_content_chars=requested_chars,
                )
                _record_reattach_event(root, writer=writer, turn=turn, decision=decision)
                return decision
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def begin_or_resume_turn(
    repo_root: Path,
    *,
    binding: Dict[str, Any],
    role: str,
    message: str,
    writer: str,
    resend_key: str = "",
    resume_turn_id: str = "",
    advisory_transport_id: str = "",
    resume_after_event_seq: Optional[int] = None,
    resume_content_chars: Optional[int] = None,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    normalized_binding = _binding_obj(binding)
    normalized_role = _clean_text(role).lower()
    normalized_message = _normalized_message(message)
    if not normalized_role:
        raise DirectTurnError("role is required")
    if not normalized_message:
        raise DirectTurnError("message is required")
    fingerprint = _request_fingerprint(binding=normalized_binding, role=normalized_role, message=normalized_message)
    with _LOCK:
        lock_path(root).parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path(root), "a+", encoding="utf-8") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                wanted_resume_id = _clean_text(resume_turn_id)
                requested_resume_after = _normalize_optional_nonnegative_int(
                    resume_after_event_seq,
                    field="resume_after_event_seq",
                )
                requested_resume_chars = _normalize_optional_nonnegative_int(
                    resume_content_chars,
                    field="resume_content_chars",
                )
                resume_unproven_reason = ""
                if wanted_resume_id:
                    try:
                        existing = _load_turn(root, wanted_resume_id)
                    except DirectTurnError:
                        existing = {}
                    if existing:
                        if (
                            existing["binding"] == normalized_binding
                            and existing["role"] == normalized_role
                            and existing["request_fingerprint"] == fingerprint
                            and existing["state"] in DIRECT_TURN_ACTIVE_STATES
                        ):
                            reattach = _evaluate_reattach(
                                root,
                                turn=existing,
                                requested_after_event_seq=requested_resume_after,
                                requested_content_chars=requested_resume_chars,
                            )
                            _record_reattach_event(root, writer=writer, turn=existing, decision=reattach)
                            if bool(reattach.get("reattachable")):
                                if advisory_transport_id and advisory_transport_id not in existing["advisory_transport_ids"]:
                                    existing["advisory_transport_ids"].append(advisory_transport_id)
                                existing["resume_count"] = int(existing.get("resume_count") or 0) + 1
                                existing["updated_utc"] = _utc_iso()
                                existing["last_writer"] = _clean_text(writer)
                                existing["generation"] = int(existing.get("generation") or 0) + 1
                                existing["last_event_seq"] = int(existing.get("last_event_seq") or 0) + 1
                                existing["last_event_type"] = "turn_resume_matched"
                                existing["last_event_utc"] = existing["updated_utc"]
                                stored = _store_turn(root, existing)
                                snapshot = _write_snapshot(root, writer=writer)
                                _record_event(
                                    root,
                                    event_type="turn_resume_matched",
                                    payload={"turn_id": stored["turn_id"], "writer": _clean_text(writer)},
                                    turn=stored,
                                )
                                return {
                                    "turn": stored,
                                    "created": False,
                                    "duplicate_suppressed": False,
                                    "resume_detected": True,
                                    "ambiguity_preserved": False,
                                    "snapshot": snapshot,
                                }
                            resume_unproven_reason = _clean_text(reattach.get("reason")) or "resume_unproven"
                        elif (
                            existing["binding"] == normalized_binding
                            and existing["role"] == normalized_role
                            and existing["request_fingerprint"] == fingerprint
                            and existing["state"] in DIRECT_TURN_TERMINAL_STATES
                        ):
                            resume_unproven_reason = "resume_turn_not_active"
                        else:
                            resume_unproven_reason = "resume_identity_unproven"
                    else:
                        resume_unproven_reason = "resume_identity_unproven"

                candidates = _matching_candidates(
                    root,
                    binding=normalized_binding,
                    role=normalized_role,
                    request_fingerprint=fingerprint,
                    resend_key=_clean_text(resend_key),
                )
                if len(candidates) == 1:
                    existing = dict(candidates[0])
                    if advisory_transport_id and advisory_transport_id not in existing["advisory_transport_ids"]:
                        existing["advisory_transport_ids"].append(advisory_transport_id)
                    existing["duplicate_hits"] = int(existing.get("duplicate_hits") or 0) + 1
                    existing["updated_utc"] = _utc_iso()
                    existing["last_writer"] = _clean_text(writer)
                    existing["generation"] = int(existing.get("generation") or 0) + 1
                    existing["last_event_seq"] = int(existing.get("last_event_seq") or 0) + 1
                    existing["last_event_type"] = "turn_duplicate_suppressed"
                    existing["last_event_utc"] = existing["updated_utc"]
                    stored = _store_turn(root, existing)
                    snapshot = _write_snapshot(root, writer=writer)
                    _record_event(
                        root,
                        event_type="turn_duplicate_suppressed",
                        payload={"turn_id": stored["turn_id"], "writer": _clean_text(writer)},
                        turn=stored,
                    )
                    return {
                        "turn": stored,
                        "created": False,
                        "duplicate_suppressed": True,
                        "resume_detected": False,
                        "ambiguity_preserved": False,
                        "snapshot": snapshot,
                    }

                now = _utc_iso()
                ambiguity_reason = "multiple_active_matches" if len(candidates) > 1 else ""
                if wanted_resume_id:
                    resume_unproven_reason = resume_unproven_reason or "resume_identity_unproven"
                created = {
                    "schema_version": DIRECT_TURN_SCHEMA_VERSION,
                    "turn_id": f"turn_{uuid.uuid4().hex}",
                    "binding": normalized_binding,
                    "role": normalized_role,
                    "state": "submitted",
                    "state_reason": "direct turn accepted",
                    "request_fingerprint": fingerprint,
                    "request_text": normalized_message,
                    "resend_key": _clean_text(resend_key),
                    "resume_count": 0,
                    "duplicate_hits": 0,
                    "advisory_transport_ids": ([advisory_transport_id] if _clean_text(advisory_transport_id) else []),
                    "created_utc": now,
                    "updated_utc": now,
                    "state_since_utc": now,
                    "started_utc": "",
                    "terminal_utc": "",
                    "generation": 0,
                    "last_writer": _clean_text(writer),
                    "resume_unproven_reason": resume_unproven_reason,
                    "ambiguity_reason": ambiguity_reason,
                    "last_event_seq": 1,
                    "last_event_type": "turn_created",
                    "last_event_utc": now,
                    "output_cursor_chars": 0,
                    "terminal_event_seq": 0,
                }
                stored = _store_turn(root, created)
                snapshot = _write_snapshot(root, writer=writer)
                _record_event(
                    root,
                    event_type="turn_created",
                    payload={
                        "turn_id": stored["turn_id"],
                        "writer": _clean_text(writer),
                        "ambiguity_reason": ambiguity_reason,
                        "resume_unproven_reason": resume_unproven_reason,
                    },
                    turn=stored,
                )
                _record_direct_turn_binding(root, turn=stored, writer=writer)
                return {
                    "turn": stored,
                    "created": True,
                    "duplicate_suppressed": False,
                    "resume_detected": False,
                    "ambiguity_preserved": bool(ambiguity_reason),
                    "snapshot": snapshot,
                }
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def transition_turn(
    repo_root: Path,
    *,
    turn_id: str,
    writer: str,
    new_state: str,
    state_reason: str = "",
    advisory_transport_id: str = "",
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    wanted_state = _clean_text(new_state).lower()
    if wanted_state not in DIRECT_TURN_ALLOWED_STATES:
        raise DirectTurnError(f"invalid turn state: {wanted_state}")
    with _LOCK:
        lock_path(root).parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path(root), "a+", encoding="utf-8") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                current = _load_turn(root, turn_id)
                if not _transition_allowed(old_state=current["state"], new_state=wanted_state):
                    # Late post-turn bookkeeping must not downgrade a terminal successful turn.
                    if current["state"] == "completed" and wanted_state == "failed":
                        return {"ok": True, "turn": dict(current), "changed": False}
                    raise DirectTurnError(f"illegal turn transition: {current['state']} -> {wanted_state}")
                changed = False
                now = _utc_iso()
                if current["state"] != wanted_state:
                    current["state"] = wanted_state
                    current["state_since_utc"] = now
                    changed = True
                if state_reason and state_reason != current["state_reason"]:
                    current["state_reason"] = state_reason
                    changed = True
                if advisory_transport_id and advisory_transport_id not in current["advisory_transport_ids"]:
                    current["advisory_transport_ids"].append(advisory_transport_id)
                    changed = True
                if wanted_state == "running" and not current["started_utc"]:
                    current["started_utc"] = now
                    changed = True
                if wanted_state in DIRECT_TURN_TERMINAL_STATES and not current["terminal_utc"]:
                    current["terminal_utc"] = now
                    changed = True
                if not changed:
                    return {"ok": True, "changed": False, "turn": current}
                current["updated_utc"] = now
                current["generation"] = int(current.get("generation") or 0) + 1
                current["last_writer"] = _clean_text(writer)
                current["last_event_seq"] = int(current.get("last_event_seq") or 0) + 1
                current["last_event_type"] = "turn_state_changed"
                current["last_event_utc"] = now
                stored = _store_turn(root, current)
                snapshot = _write_snapshot(root, writer=writer)
                _record_event(
                    root,
                    event_type="turn_state_changed",
                    payload={
                        "turn_id": stored["turn_id"],
                        "writer": _clean_text(writer),
                        "new_state": wanted_state,
                        "state_reason": _clean_text(state_reason),
                    },
                    turn=stored,
                )
                _record_direct_turn_progress(root, turn=stored, writer=writer)
                return {"ok": True, "changed": True, "turn": stored, "snapshot": snapshot}
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def read_turn(repo_root: Path, *, turn_id: str) -> Dict[str, Any]:
    return _load_turn(Path(repo_root).resolve(), turn_id)


def append_turn_output_event(
    repo_root: Path,
    *,
    turn_id: str,
    writer: str,
    event_type: str,
    text: str = "",
    status_message: str = "",
    terminal_state: str = "",
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    normalized_type = _clean_text(event_type).lower()
    if normalized_type not in DIRECT_TURN_OUTPUT_EVENT_TYPES:
        raise DirectTurnError(f"invalid output event type: {normalized_type}")
    normalized_text = str(text or "")
    normalized_terminal_state = _clean_text(terminal_state).lower()
    if normalized_type == "output_delta" and not normalized_text:
        raise DirectTurnError("output_delta requires non-empty text")
    if normalized_type == "terminal" and normalized_terminal_state not in DIRECT_TURN_TERMINAL_STATES.union({"awaiting_resume"}):
        raise DirectTurnError("terminal event requires terminal_state")
    with _LOCK:
        lock_path(root).parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path(root), "a+", encoding="utf-8") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                current = _load_turn(root, turn_id)
                next_seq = int(current.get("last_event_seq") or 0) + 1
                cursor_chars = int(current.get("output_cursor_chars") or 0)
                payload: Dict[str, Any] = {"writer": _clean_text(writer)}
                if normalized_type == "output_delta":
                    cursor_chars += len(normalized_text)
                    payload["text"] = normalized_text
                elif normalized_type == "progress":
                    payload["status_message"] = _clean_text(status_message)
                else:
                    payload["terminal_state"] = normalized_terminal_state
                    payload["text"] = normalized_text
                    cursor_chars = len(normalized_text)
                current["last_event_seq"] = next_seq
                current["last_event_type"] = normalized_type
                current["last_event_utc"] = _utc_iso()
                current["output_cursor_chars"] = cursor_chars
                if normalized_type == "terminal":
                    current["terminal_event_seq"] = next_seq
                current["updated_utc"] = current["last_event_utc"]
                current["generation"] = int(current.get("generation") or 0) + 1
                current["last_writer"] = _clean_text(writer)
                stored = _store_turn(root, current)
                _record_event(root, event_type=normalized_type, payload=payload, turn=stored)
                snapshot = _write_snapshot(root, writer=writer)
                return {"ok": True, "turn": stored, "event_seq": next_seq, "snapshot": snapshot}
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def _load_replay_rows_by_turn_id(
    repo_root: Path,
    *,
    wanted_turn_ids: Optional[List[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    root = Path(repo_root).resolve()
    wanted = {_clean_text(item) for item in list(wanted_turn_ids or []) if _clean_text(item)}
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        raw_lines = events_path(root).read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return out
    for raw in raw_lines:
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue
        if not isinstance(row, dict) or str(row.get("schema_version") or "") != DIRECT_TURN_EVENT_SCHEMA_VERSION:
            continue
        turn_id = _clean_text(row.get("turn_id"))
        if not turn_id:
            continue
        if wanted and turn_id not in wanted:
            continue
        out.setdefault(turn_id, []).append(row)
    return out


def replay_turn_events(
    repo_root: Path,
    *,
    turn_id: str,
    after_event_seq: int = 0,
    _preloaded_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    turn = _load_turn(root, turn_id)
    wanted_after = max(0, int(after_event_seq))
    continuity_ok = True
    interruption_reason = ""
    rows: List[Dict[str, Any]] = []
    if _preloaded_rows is not None:
        for row in list(_preloaded_rows):
            if not isinstance(row, dict):
                continue
            if _clean_text(row.get("turn_id")) != turn_id:
                continue
            rows.append(dict(row))
    else:
        try:
            raw_lines = events_path(root).read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            raw_lines = []
        for raw in raw_lines:
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continuity_ok = False
                interruption_reason = "event_log_corrupt"
                break
            if not isinstance(row, dict) or str(row.get("schema_version") or "") != DIRECT_TURN_EVENT_SCHEMA_VERSION:
                continue
            if _clean_text(row.get("turn_id")) != turn_id:
                continue
            rows.append(row)
    rows.sort(key=lambda row: int(row.get("event_seq") or 0))
    expected_seq = 1
    for row in rows:
        seq = int(row.get("event_seq") or 0)
        if seq != expected_seq:
            continuity_ok = False
            interruption_reason = "event_sequence_gap"
            break
        expected_seq += 1
    terminal_event_seq = max(0, int(turn.get("terminal_event_seq") or 0))
    terminal_proven = any(
        str(row.get("event_type") or "") == "terminal"
        and int(row.get("event_seq") or 0) == terminal_event_seq
        for row in rows
    )
    if continuity_ok and turn["state"] in DIRECT_TURN_TERMINAL_STATES and not terminal_proven:
        continuity_ok = False
        interruption_reason = "terminal_boundary_missing"
    replay_status = str(turn.get("state") or "")
    if not continuity_ok:
        replay_status = "interrupted"
    content_parts: List[str] = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        event_type = str(row.get("event_type") or "")
        if event_type == "output_delta":
            content_parts.append(str(payload.get("text") or ""))
        elif event_type == "terminal":
            content_parts = [str(payload.get("text") or "")]
    replay_rows = [row for row in rows if int(row.get("event_seq") or 0) > wanted_after]
    classification = _derive_turn_classification(
        turn,
        continuity_proven=continuity_ok,
        terminal_proven=(terminal_proven and continuity_ok),
        interruption_reason=interruption_reason,
        output_present=bool(content_parts),
    )
    return {
        "schema_version": DIRECT_TURN_REPLAY_SCHEMA_VERSION,
        "turn": turn,
        "events": replay_rows,
        "replay_status": replay_status,
        "continuity_proven": continuity_ok,
        "interruption_reason": interruption_reason,
        "terminal_proven": terminal_proven and continuity_ok,
        "content": "".join(content_parts),
        "classification": classification,
        "final_commit": {
            "state": _clean_text(turn.get("final_commit_state")).lower() or "not_started",
            "attempts": int(turn.get("final_commit_attempts") or 0),
            "pending_since_utc": _clean_text(turn.get("final_commit_pending_since_utc")),
            "committed_utc": _clean_text(turn.get("final_commit_committed_utc")),
            "session_row_sha256": _clean_text(turn.get("final_commit_session_row_sha256")),
            "last_error": _clean_text(turn.get("final_commit_last_error")),
            "turn_event_seq": int(turn.get("final_commit_turn_event_seq") or 0),
        },
        "resume_cursor": {
            "after_event_seq": int(turn.get("last_event_seq") or 0),
            "content_chars": int(turn.get("output_cursor_chars") or 0),
        },
    }


def classify_turn(repo_root: Path, *, turn_id: str) -> Dict[str, Any]:
    replay = replay_turn_events(repo_root, turn_id=turn_id, after_event_seq=0)
    return {
        "schema_version": DIRECT_TURN_CLASSIFICATION_SCHEMA_VERSION,
        "turn": dict(replay.get("turn") or {}),
        "classification": dict(replay.get("classification") or {}),
        "replay_status": _clean_text(replay.get("replay_status")),
        "continuity_proven": bool(replay.get("continuity_proven")),
        "terminal_proven": bool(replay.get("terminal_proven")),
        "interruption_reason": _clean_text(replay.get("interruption_reason")),
    }


def wait_for_turn_terminal(repo_root: Path, *, turn_id: str, timeout_s: int, poll_interval_s: float = 0.05) -> Dict[str, Any]:
    deadline = _dt.datetime.now(_dt.timezone.utc).timestamp() + max(0, int(timeout_s))
    while True:
        current = read_turn(repo_root, turn_id=turn_id)
        if current["state"] in DIRECT_TURN_TERMINAL_STATES:
            return current
        if _dt.datetime.now(_dt.timezone.utc).timestamp() >= deadline:
            return current
        time.sleep(poll_interval_s)


def reconcile_turns_on_boot(
    repo_root: Path,
    *,
    writer: str = "assistant_recovery.startup.turns",
) -> Dict[str, Any]:
    """Reconcile startup-time nonterminal turn truth without auto-replaying work."""
    root = Path(repo_root).resolve()
    changed_turn_ids: List[str] = []
    with _LOCK:
        lock_path(root).parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path(root), "a+", encoding="utf-8") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                for row in list_turns(root):
                    state = _clean_text(row.get("state")).lower()
                    if state not in {"submitted", "running"}:
                        continue
                    now = _utc_iso()
                    reason = (
                        "startup reconciliation marked nonterminal turn as awaiting_resume; "
                        "restart continuity for in-flight execution is unproven"
                    )
                    row["state"] = "awaiting_resume"
                    row["state_reason"] = reason
                    row["state_since_utc"] = now
                    row["updated_utc"] = now
                    row["generation"] = int(row.get("generation") or 0) + 1
                    row["last_writer"] = _clean_text(writer)
                    row["last_event_seq"] = int(row.get("last_event_seq") or 0) + 1
                    row["last_event_type"] = "turn_boot_reconciled"
                    row["last_event_utc"] = now
                    stored = _store_turn(root, row)
                    changed_turn_ids.append(stored["turn_id"])
                    _record_event(
                        root,
                        event_type="turn_boot_reconciled",
                        payload={
                            "turn_id": stored["turn_id"],
                            "writer": _clean_text(writer),
                            "new_state": "awaiting_resume",
                            "state_reason": reason,
                        },
                        turn=stored,
                    )
                snapshot = _write_snapshot(root, writer=writer)
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
    projections = recent_turn_projections(root, limit=200)
    counts = {
        "running": 0,
        "submitted": 0,
        "interrupted": 0,
        "pending_commit": 0,
        "ambiguous_terminal": 0,
        "finished": 0,
        "failed": 0,
        "cancelled": 0,
        "timed_out": 0,
        "cancel": 0,
    }
    for row in projections["turns"]:
        status = _clean_text((row.get("classification") or {}).get("status")).lower()
        if status in counts:
            counts[status] += 1
    return {
        "schema_version": DIRECT_TURN_BOOT_RECONCILIATION_SCHEMA_VERSION,
        "writer": _clean_text(writer),
        "reconciled_utc": _utc_iso(),
        "changed_turn_ids": changed_turn_ids,
        "changed_count": len(changed_turn_ids),
        "turn_count": int(projections.get("turn_count") or 0),
        "status_counts": counts,
        "pending_commit_turn_ids": [
            str(item.get("turn_id"))
            for item in projections["turns"]
            if bool(((item.get("classification") or {}) if isinstance(item.get("classification"), dict) else {}).get("pending_commit"))
        ],
        "ambiguous_turn_ids": [
            str(item.get("turn_id"))
            for item in projections["turns"]
            if bool(((item.get("classification") or {}) if isinstance(item.get("classification"), dict) else {}).get("terminal_ambiguous"))
        ],
        "snapshot_turn_count": int(snapshot.get("turn_count") or 0),
    }


def recent_turn_projections(
    repo_root: Path,
    *,
    limit: int = 20,
    binding: Optional[Dict[str, Any]] = None,
    role: str = "",
) -> Dict[str, Any]:
    """Build a read-only recent-turn projection from durable turn/event truth."""
    root = Path(repo_root).resolve()
    rows = list_turns(root)
    wanted_binding: Optional[Dict[str, str]] = None
    if isinstance(binding, dict) and binding:
        wanted_binding = _binding_obj(binding)
    wanted_role = _clean_text(role).lower()
    filtered: List[Dict[str, Any]] = []
    for row in rows:
        if wanted_binding is not None and dict(row.get("binding") or {}) != wanted_binding:
            continue
        if wanted_role and _clean_text(row.get("role")).lower() != wanted_role:
            continue
        filtered.append(row)
    filtered.sort(key=lambda item: (_clean_text(item.get("updated_utc")), _clean_text(item.get("turn_id"))), reverse=True)
    cap = max(1, min(int(limit or 20), 200))
    selected = filtered[:cap]
    preloaded_rows = _load_replay_rows_by_turn_id(
        root,
        wanted_turn_ids=[_clean_text(row.get("turn_id")) for row in selected],
    )
    projected: List[Dict[str, Any]] = []
    for row in selected:
        turn_id = _clean_text(row.get("turn_id"))
        replay = replay_turn_events(
            root,
            turn_id=turn_id,
            after_event_seq=0,
            _preloaded_rows=list(preloaded_rows.get(turn_id) or []),
        )
        classification = dict(replay.get("classification") or {})
        projected.append(
            {
                "schema_version": DIRECT_TURN_RECENT_PROJECTION_SCHEMA_VERSION,
                "turn_id": turn_id,
                "binding": dict(row.get("binding") or {}),
                "role": _clean_text(row.get("role")).lower(),
                "state": _clean_text(row.get("state")).lower(),
                "state_reason": _clean_text(row.get("state_reason")),
                "created_utc": _clean_text(row.get("created_utc")),
                "updated_utc": _clean_text(row.get("updated_utc")),
                "terminal_utc": _clean_text(row.get("terminal_utc")),
                "resume_unproven_reason": _clean_text(row.get("resume_unproven_reason")),
                "ambiguity_reason": _clean_text(row.get("ambiguity_reason")),
                "replay_status": _clean_text(replay.get("replay_status")),
                "continuity_proven": bool(replay.get("continuity_proven")),
                "terminal_proven": bool(replay.get("terminal_proven")),
                "interruption_reason": _clean_text(replay.get("interruption_reason")),
                "classification": classification,
                "final_commit": dict(replay.get("final_commit") or {}),
                "resume_cursor": dict(replay.get("resume_cursor") or {}),
                "record_path": str(record_path(root, turn_id)),
                "events_path": str(events_path(root)),
            }
        )
    return {
        "schema_version": DIRECT_TURN_RECENT_PROJECTION_SCHEMA_VERSION,
        "generated_utc": _utc_iso(),
        "turn_count": len(projected),
        "total_matching_turns": len(filtered),
        "limit": cap,
        "turns": projected,
    }
