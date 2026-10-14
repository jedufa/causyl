"""Durable task register and open-loop memory contract (BD05).

This module owns:
- canonical durable task/open-loop register storage
- append-only task-register mutation log
- durable constraints and recent execution references
- derived rolling summary that can be rebuilt from register facts

It does not own fresh-turn bootstrap, delegation policy, or restart
reconciliation.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import os
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from infra.root_tools import load_tool_module as _load_root_tool_module

_fsio = _load_root_tool_module("shared_fsio", "tools/shared/fsio.py")
from infra.webui import server_lab_assistant_execution_truth_ownership as _truth_ownership
from infra.webui import server_lab_assistant_host as _lab_host
from infra.webui import server_lab_assistant_state as _lab_state
from infra.webui.server_lab_assistant_task_register_types import TaskRegisterSnapshot
from infra.webui.ledger import ledger_storage as _ledger_storage

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]


TASK_REGISTER_SCHEMA_VERSION = "lab_assistant_task_register_v1"
TASK_REGISTER_EVENT_SCHEMA_VERSION = "lab_assistant_task_register_event_v1"
TASK_REGISTER_SUMMARY_SCHEMA_VERSION = "lab_assistant_task_register_summary_v1"
TASK_REGISTER_SNAPSHOT_REFRESH_SCHEMA_VERSION = "lab_assistant_task_register_snapshot_refresh_v1"
_INTERNAL_HELPER_TITLE_PREFIXES = (
    "[Codex Scheduled Controller Contract]",
)
OPEN_LOOP_ALLOWED_KINDS = {
    "waiting_on_operator",
    "waiting_on_verification",
    "blocked_question",
    "deferred_idea",
}
TASK_INTAKE_SCHEMA_VERSION = "lab_assistant_request_intake_v1"
TASK_INTAKE_WORK_STATES = {
    "casual_chat",
    "active_work",
}
TASK_INTAKE_PATH_CLASSIFICATIONS = {"canonical", "compatibility_shim", "forbidden"}
TASK_INTAKE_READINESS_STATES = {
    "clarification_needed",
    "proceed_with_assumptions",
    "good_enough_to_plan",
    "good_enough_to_execute_directly",
}
TASK_SCOPE_CONTRACT_SCHEMA_VERSION = "lab_assistant_task_scope_contract_v1"
TASK_SCOPE_ASSUMPTION_KINDS = {"explicit", "inferred"}
TASK_SCOPE_ASSUMPTION_APPROVAL_STATES = {
    "provisional",
    "needs_operator_confirmation",
    "operator_confirmed",
    "operator_rejected",
}
TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION = "lab_assistant_task_acceptance_contract_v1"
TASK_PROOF_TARGET_CLASSES = {"test", "artifact", "review", "host_verification", "inspection", "other"}
TASK_PROOF_TARGET_STATES = {"pending", "satisfied", "waived"}
TASK_REVIEW_GATE_STATES = {"pending", "approved", "rejected", "waived"}
TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION = "lab_assistant_task_decomposition_contract_v1"
TASK_DECOMPOSITION_ARTIFACT_SCHEMA_VERSION = "lab_assistant_task_decomposition_artifact_v1"
TASK_PLANNING_HANDOFF_SCHEMA_VERSION = "lab_assistant_planning_handoff_v1"
TASK_DECOMPOSITION_MODES = {"direct", "milestone_plan"}
TASK_ROUTING_DECISION_SCHEMA_VERSION = "lab_assistant_routing_decision_v1"
TASK_ROUTING_DECISION_OUTCOMES = {"direct", "delegate", "blocked", "deferred"}
TASK_DEPENDENCY_FANOUT_POLICIES = {"serial_only", "explicit_safe_fanout"}
TASK_DEPENDENCY_EXECUTION_MODES = {"serial_only", "fanout_safe"}
TASK_REPLANNING_STATES = {"settled", "needs_replan", "blocked", "degraded"}
TASK_PLAN_REVISION_STATES = {"current", "superseded", "invalidated"}
TASK_REPLAN_TRIGGER_KINDS = {
    "initial_plan",
    "changed_evidence",
    "failed_assumption",
    "changed_constraint",
    "proof_target_changed",
    "operator_redirect",
    "restart_recovery",
}
TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION = "lab_assistant_task_execution_surface_ownership_v1"
TASK_RUN_RECORD_SCHEMA_VERSION = "lab_assistant_task_run_record_v1"
TASK_RUN_CHECKPOINT_SCHEMA_VERSION = "lab_assistant_task_run_checkpoint_v1"
TASK_RUN_RESUME_DECISION_SCHEMA_VERSION = "lab_assistant_task_run_resume_decision_v1"
TASK_RUN_INTERVENTION_SCHEMA_VERSION = "lab_assistant_task_run_intervention_v1"
TASK_EXECUTION_SURFACES = {"codex_worker", "pipeline", "scheduler", "verifier", "watchdog", "healer", "other"}
TASK_EXECUTION_REFERENCE_STATUSES = {
    "execution_ref_active",
    "execution_ref_unresolved",
    "execution_ref_stale",
    "execution_ref_conflict",
}
TASK_RUN_STATES = {"requested", "active", "running", "waiting", "completed", "failed", "stale", "conflict", "blocked"}
TASK_ATTENTION_STATES = {"needs_attention", "monitoring", "blocked", "attention_complete", "degraded"}
TASK_RUN_CHECKPOINT_STATUSES = {"recorded", "finalized", "stale", "ambiguous", "failed"}
TASK_RUN_RESUME_DECISION_OUTCOMES = {"resume", "retry", "rebind", "blocked", "unknown"}
TASK_RUN_INTERVENTION_ACTIONS = {"pause", "resume", "cancel", "retry", "rebind", "repair"}
TASK_RUN_INTERVENTION_OUTCOMES = {"accepted", "blocked", "conflict", "completed"}
_RUN_STATE_TO_REFERENCE_STATUS = {
    "requested": "execution_ref_unresolved",
    "active": "execution_ref_active",
    "running": "execution_ref_active",
    "waiting": "execution_ref_unresolved",
    "completed": "execution_ref_active",
    "failed": "execution_ref_stale",
    "stale": "execution_ref_stale",
    "conflict": "execution_ref_conflict",
    "blocked": "execution_ref_conflict",
}
DELEGATION_REF_SCHEMA_VERSION = "lab_assistant_delegation_ref_v1"
_LEDGER_UID_RE = re.compile(r"^PIS-[A-Za-z0-9._:-]+$")
_TASK_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_LOCK = threading.RLock()
_ROUTING_OWNER_UID = "PIS-20260312-224201Z-D1E7"
_EXECUTION_OWNER_UID = "PIS-20260312-223543Z-E4F1"


class TaskRegisterError(RuntimeError):
    """Raised when the BD05 task-register contract cannot be satisfied."""


def _utc_now_iso() -> str:
    return _fsio.utc_now_iso()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def is_internal_helper_task(task: Dict[str, Any]) -> bool:
    title = _clean_text(dict(task or {}).get("title"))
    return any(title.startswith(prefix) for prefix in _INTERNAL_HELPER_TITLE_PREFIXES)


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_task_intake_path(value: Any, *, source: str) -> str:
    normalized = _clean_text(value)
    if not normalized:
        normalized = "canonical" if _clean_text(source) == "operator_intake" else "compatibility_shim"
    if normalized not in TASK_INTAKE_PATH_CLASSIFICATIONS:
        raise TaskRegisterError(f"task intake_path is invalid: {normalized}")
    return normalized


def _normalize_task_for_legacy_load(task: Any) -> Any:
    if not isinstance(task, dict):
        return task
    restored = dict(task)
    restored["intake_path"] = _normalize_task_intake_path(
        restored.get("intake_path"), source=str(restored.get("source") or "")
    )
    return restored


def _normalize_register_for_legacy_load(register: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(register, dict):
        raise TaskRegisterError("task register payload must be an object")
    restored = dict(register)
    tasks = list(restored.get("active_tasks") or [])
    restored["active_tasks"] = [_normalize_task_for_legacy_load(task) for task in tasks]
    completed_tasks = list(restored.get("completed_tasks") or [])
    residue_tasks = list(restored.get("residue_tasks") or [])
    restored["completed_tasks"] = [_normalize_task_for_legacy_load(task) for task in completed_tasks]
    restored["residue_tasks"] = [_normalize_task_for_legacy_load(task) for task in residue_tasks]
    return restored


def _task_register_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "task_register").resolve()


def _register_path(repo_root: Path) -> Path:
    return (_task_register_root(repo_root) / "task_register.json").resolve()


def _events_path(repo_root: Path) -> Path:
    return (_task_register_root(repo_root) / "task_register_events.jsonl").resolve()


def _summary_cache_path(repo_root: Path) -> Path:
    return (_task_register_root(repo_root) / "summary_cache.json").resolve()


def _looks_like_partial_json_write(text: str, exc: Exception) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return True
    if stripped in {"{", "[", '"'}:
        return True
    message = str(exc or "").lower()
    if not stripped.endswith("}") and not stripped.endswith("]"):
        return True
    if "unterminated" in message or "expecting value" in message:
        return True
    if stripped.count("{") != stripped.count("}"):
        return True
    if stripped.count("[") != stripped.count("]"):
        return True
    return False


def _task_register_event_log_validation_max_bytes() -> int:
    raw = str(os.environ.get("EIEL_TASK_REGISTER_EVENT_LOG_VALIDATION_MAX_BYTES", str(32 * 1024 * 1024)) or "").strip()
    try:
        limit = int(raw)
    except Exception:
        limit = 32 * 1024 * 1024
    return max(0, limit)


def _task_register_event_log_compact_max_bytes() -> int:
    raw = str(os.environ.get("EIEL_TASK_REGISTER_EVENT_LOG_COMPACT_MAX_BYTES", str(64 * 1024 * 1024)) or "").strip()
    try:
        limit = int(raw)
    except Exception:
        limit = 64 * 1024 * 1024
    return max(0, limit)


def _task_register_event_log_archive_retention_days() -> int:
    raw = str(os.environ.get("EIEL_TASK_REGISTER_EVENT_LOG_ARCHIVE_RETENTION_DAYS", "7") or "").strip()
    try:
        days = int(raw)
    except Exception:
        days = 7
    return max(0, days)


def _task_register_event_log_archive_dir(repo_root: Path) -> Path:
    return (_task_register_root(repo_root) / "archive").resolve()


def _iter_jsonl_dicts_strict(path: Path) -> Iterator[Dict[str, Any]]:
    try:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for idx, raw in enumerate(fh, start=1):
                text = raw.strip()
                if not text:
                    continue
                obj = json.loads(text)
                if not isinstance(obj, dict):
                    raise ValueError(f"expected JSON object on line {idx} in {path}")
                yield obj
    except Exception as exc:
        raise TaskRegisterError(f"{path.name} is malformed") from exc


def _read_last_nonempty_line(path: Path, *, max_bytes: int = 65536) -> str:
    if max_bytes <= 0:
        max_bytes = 65536
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if size <= 0:
            return ""
        start = max(0, size - max_bytes)
        fh.seek(start)
        chunk = fh.read(size - start)
    text = chunk.decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _write_jsonl_atomic(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def _prune_task_register_event_log_archives(repo_root: Path) -> None:
    retention_days = _task_register_event_log_archive_retention_days()
    if retention_days <= 0:
        return
    archive_dir = _task_register_event_log_archive_dir(repo_root)
    if not archive_dir.exists():
        return
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=retention_days)
    for candidate in archive_dir.glob("task_register_events__*.jsonl.gz"):
        try:
            modified = _dt.datetime.fromtimestamp(candidate.stat().st_mtime, tz=_dt.timezone.utc)
        except Exception:
            continue
        if modified < cutoff:
            try:
                candidate.unlink()
            except Exception:
                pass


def _maybe_compact_task_register_event_log(
    repo_root: Path,
    *,
    register: Dict[str, Any],
    summary: Dict[str, Any],
    writer: str,
) -> None:
    limit = _task_register_event_log_compact_max_bytes()
    if limit <= 0:
        return
    events_path = _events_path(repo_root)
    if not events_path.exists() or not events_path.is_file():
        return
    try:
        current_size = events_path.stat().st_size
    except Exception:
        return
    if current_size <= limit:
        _prune_task_register_event_log_archives(repo_root)
        return

    archive_dir = _task_register_event_log_archive_dir(repo_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    compacted_utc = _utc_now_iso()
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_raw = archive_dir / f"task_register_events__{stamp}.jsonl"
    archive_gz = archive_dir / f"task_register_events__{stamp}.jsonl.gz"
    counter = 1
    while archive_raw.exists() or archive_gz.exists():
        archive_raw = archive_dir / f"task_register_events__{stamp}_{counter}.jsonl"
        archive_gz = archive_dir / f"task_register_events__{stamp}_{counter}.jsonl.gz"
        counter += 1

    refresh_state = dict(register.get("snapshot_projection") or {})
    compacted_rows = [
        {
            "schema_version": TASK_REGISTER_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": "task_register_initialized",
            "utc": compacted_utc,
            "payload": {"register": dict(register)},
        },
        {
            "schema_version": TASK_REGISTER_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": "task_register_committed",
            "utc": compacted_utc,
            "payload": {
                "writer": str(writer or ""),
                "register": dict(register),
                "summary_cache": dict(summary),
            },
        },
        {
            "schema_version": TASK_REGISTER_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": "task_register_projection_refreshed",
            "utc": compacted_utc,
            "payload": {
                "schema_version": TASK_REGISTER_SNAPSHOT_REFRESH_SCHEMA_VERSION,
                "writer": str(writer or ""),
                "write_generation": int(register.get("write_generation") or 0),
                "refresh_mode": str(refresh_state.get("refresh_mode") or "eager"),
                "summary_cache_present": bool(summary),
                "snapshot_refresh": refresh_state,
            },
        },
        {
            "schema_version": TASK_REGISTER_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": "task_register_event_log_compacted",
            "utc": compacted_utc,
            "payload": {
                "writer": str(writer or ""),
                "previous_size_bytes": int(current_size),
                "compaction_threshold_bytes": int(limit),
                "archive_path": str(archive_gz),
                "write_generation": int(register.get("write_generation") or 0),
            },
        },
    ]

    try:
        events_path.replace(archive_raw)
        _write_jsonl_atomic(events_path, compacted_rows)
        with archive_raw.open("rb") as src, gzip.open(archive_gz, "wb", compresslevel=1) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        archive_raw.unlink(missing_ok=True)
    except Exception:
        if archive_raw.exists() and not events_path.exists():
            try:
                archive_raw.replace(events_path)
            except Exception:
                pass
        return

    _prune_task_register_event_log_archives(repo_root)


def _json_file_integrity(path: Path) -> Dict[str, Any]:
    resolved = Path(path).resolve()
    payload = {
        "path": str(resolved),
        "status": "ok",
        "detail": "",
        "exists": False,
        "size_bytes": 0,
        "partial_write_suspected": False,
    }
    if not resolved.exists():
        payload["status"] = "missing"
        return payload
    if not resolved.is_file() or resolved.is_symlink():
        payload["status"] = "invalid_path"
        payload["exists"] = True
        return payload
    payload["exists"] = True
    try:
        raw = resolved.read_text(encoding="utf-8")
    except Exception as exc:
        payload["status"] = "unreadable"
        payload["detail"] = str(exc)
        return payload
    payload["size_bytes"] = len(raw.encode("utf-8"))
    stripped = raw.strip()
    if not stripped:
        payload["status"] = "empty"
        payload["partial_write_suspected"] = True
        return payload
    try:
        data = json.loads(raw)
    except Exception as exc:
        payload["status"] = "malformed"
        payload["detail"] = str(exc)
        payload["partial_write_suspected"] = _looks_like_partial_json_write(stripped, exc)
        return payload
    if not isinstance(data, dict):
        payload["status"] = "wrong_type"
        payload["detail"] = f"expected JSON object, got {type(data).__name__}"
    return payload


def task_register_integrity_snapshot(repo_root: Path) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    canonical = _json_file_integrity(_register_path(repo_root))
    summary = _json_file_integrity(_summary_cache_path(repo_root))
    events_path = _events_path(repo_root)
    event_log = {
        "path": str(events_path),
        "status": "ok",
        "exists": bool(events_path.exists()),
        "detail": "",
        "size_bytes": events_path.stat().st_size if events_path.exists() and events_path.is_file() else 0,
        "partial_write_suspected": False,
        "validation_mode": "full",
        "validation_skipped": False,
    }
    if events_path.exists() and events_path.is_file() and not events_path.is_symlink():
        size_bytes = int(event_log.get("size_bytes") or 0)
        validation_limit = _task_register_event_log_validation_max_bytes()
        if validation_limit > 0 and size_bytes > validation_limit:
            event_log["validation_mode"] = "size_capped"
            event_log["validation_skipped"] = True
            event_log["detail"] = (
                f"strict validation deferred for oversized event log ({size_bytes} bytes > {validation_limit} byte limit)"
            )
            try:
                tail_line = _read_last_nonempty_line(events_path)
            except Exception:
                tail_line = ""
            if tail_line:
                try:
                    tail_obj = json.loads(tail_line)
                    if not isinstance(tail_obj, dict):
                        event_log["status"] = "wrong_type"
                        event_log["detail"] = "last non-empty event-log line is not a JSON object"
                except Exception as exc:
                    event_log["status"] = "malformed"
                    event_log["detail"] = str(exc)
                    event_log["partial_write_suspected"] = _looks_like_partial_json_write(tail_line, exc)
        else:
            try:
                for _ in _iter_jsonl_dicts_strict(events_path):
                    pass
            except Exception as exc:
                event_log["status"] = "malformed"
                event_log["detail"] = str(exc)
                try:
                    tail_line = _read_last_nonempty_line(events_path)
                except Exception:
                    tail_line = ""
                if tail_line:
                    event_log["partial_write_suspected"] = _looks_like_partial_json_write(tail_line, exc)
    elif events_path.exists():
        event_log["status"] = "invalid_path"

    degraded_reasons: List[str] = []
    if str(canonical.get("status") or "") not in {"ok", "missing"}:
        degraded_reasons.append(f"canonical_register_{canonical.get('status')}")
    if str(summary.get("status") or "") not in {"ok", "missing"}:
        degraded_reasons.append(f"summary_cache_{summary.get('status')}")
    if str(event_log.get("status") or "") not in {"ok", "missing"}:
        degraded_reasons.append(f"event_log_{event_log.get('status')}")

    recovery_guidance: List[str] = []
    if str(canonical.get("status") or "") in {"malformed", "empty", "wrong_type", "unreadable"}:
        recovery_guidance.append("rebuild canonical register from event log or restore from backup before trusting completion state")
    if str(summary.get("status") or "") in {"malformed", "empty", "wrong_type", "unreadable"}:
        recovery_guidance.append("discard summary cache and rebuild from canonical register; treat projection as degraded until rewrite completes")
    if str(event_log.get("status") or "") in {"malformed", "empty", "wrong_type", "unreadable"}:
        recovery_guidance.append("repair or restore the task_register_events.jsonl append-only log before replay-based recovery")

    return {
        "schema_version": "lab_assistant_task_register_integrity_v1",
        "canonical_register": canonical,
        "summary_cache": summary,
        "event_log": event_log,
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "recovery_guidance": recovery_guidance,
    }


def _lock_path(repo_root: Path) -> Path:
    return (_task_register_root(repo_root) / ".task_register.lock").resolve()

def _read_json(path: Path) -> Dict[str, Any]:
    return _fsio.read_json_dict(path)


def _read_json_strict(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise TaskRegisterError(f"{path.name} is missing")
    try:
        obj = _fsio.read_json_dict_strict(path)
    except Exception as exc:
        raise TaskRegisterError(f"{path.name} is malformed") from exc
    return obj


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    _fsio.write_json_atomic(path, obj)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    _fsio.append_jsonl(path, obj)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return _fsio.read_jsonl(path)


def _read_jsonl_strict(path: Path) -> List[Dict[str, Any]]:
    try:
        return _fsio.read_jsonl_strict(path)
    except Exception as exc:
        raise TaskRegisterError(f"{path.name} is malformed") from exc


@contextmanager
def _task_register_lock(repo_root: Path):
    lock_path = _lock_path(repo_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as handle:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def _summary_cache_ref(repo_root: Path) -> Dict[str, Any]:
    return {
        "path": str(_summary_cache_path(repo_root)),
        "derived_only": True,
        "schema_version": TASK_REGISTER_SUMMARY_SCHEMA_VERSION,
    }


def _normalize_datetime_value(value: Any) -> str:
    return str(value or "").strip() or _utc_now_iso()


def _normalize_execution_surface(raw_surface: Any) -> str:
    surface = str(raw_surface or "").strip().lower()
    if surface in {"execution_queue", "workflow"}:
        return "pipeline"
    if surface in {"direct_turn_job", "codex", "codex_worker"}:
        return "codex_worker"
    if surface == "scheduler":
        return "scheduler"
    if surface in TASK_EXECUTION_SURFACES:
        return surface
    return "other"


def _normalize_execution_reference_status(value: Any, *, has_all_required: bool) -> str:
    normalized = str(value or "").strip()
    if normalized not in TASK_EXECUTION_REFERENCE_STATUSES:
        normalized = "execution_ref_active" if has_all_required else "execution_ref_unresolved"
    elif normalized == "execution_ref_active" and not has_all_required:
        normalized = "execution_ref_unresolved"
    return normalized


def _execution_refs_complete(row: Dict[str, Any]) -> bool:
    return all(
        [
            bool(str(row.get("execution_id") or "").strip()),
            bool(str(row.get("execution_ref") or "").strip()),
            bool(str(row.get("binding_ref") or "").strip()),
            bool(str(row.get("job_uri") or "").strip()),
            bool(str(row.get("trace_id") or "").strip()),
        ]
    )


def _normalize_execution_surface_ownership(row: Dict[str, Any]) -> Dict[str, Any]:
    owner_uid = str(row.get("owner_uid") or "").strip() or _EXECUTION_OWNER_UID
    selected_surface = _normalize_execution_surface(row.get("selected_surface"))
    trace_id = str(row.get("trace_id") or "").strip()
    execution_id = str(row.get("execution_id") or "").strip()
    execution_ref = str(row.get("execution_ref") or "").strip()
    binding_ref = str(row.get("binding_ref") or "").strip()
    job_uri = str(row.get("job_uri") or "").strip()
    has_all_required = all([execution_id, execution_ref, binding_ref, job_uri, trace_id])
    status = _normalize_execution_reference_status(row.get("reference_status"), has_all_required=has_all_required)
    return {
        "schema_version": str(row.get("schema_version") or TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION).strip()
        or TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
        "owner_uid": owner_uid,
        "selected_surface": selected_surface,
        "execution_id": execution_id,
        "execution_ref": execution_ref,
        "binding_ref": binding_ref,
        "job_uri": job_uri,
        "trace_id": trace_id,
        "reference_status": status,
        "reference_status_reason": str(row.get("reference_status_reason") or "").strip(),
        "reference_revision": int(_int_or_default(row.get("reference_revision"), default=0)),
        "owner_surface": str(row.get("owner_surface") or selected_surface).strip() or selected_surface,
        "updated_utc": _normalize_datetime_value(row.get("updated_utc")),
        "last_writer": str(row.get("last_writer") or "").strip(),
    }


def _run_state_to_attention_state(run_state: str) -> str:
    normalized = str(run_state or "").strip().lower()
    if normalized in {"requested", "waiting"}:
        return "needs_attention"
    if normalized in {"active", "running"}:
        return "monitoring"
    if normalized in {"conflict", "blocked"}:
        return "blocked"
    if normalized in {"failed", "stale"}:
        return "degraded"
    if normalized in {"completed"}:
        return "attention_complete"
    return "needs_attention"


def _run_reference_status(run_state: str) -> str:
    normalized = str(run_state or "").strip().lower()
    return _RUN_STATE_TO_REFERENCE_STATUS.get(normalized, "execution_ref_unresolved")


def _normalize_task_run_evidence_refs(values: Any) -> List[str]:
    out: List[str] = []
    for raw in list(values or []):
        text = _clean_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _normalize_task_run_checkpoint(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if not any(str(v).strip() for v in value.values() if v is not None):
        return {}
    checkpoint_id = _clean_text(value.get("checkpoint_id"))
    if not checkpoint_id:
        raise TaskRegisterError("task run checkpoint requires checkpoint_id")
    status = _clean_text(value.get("checkpoint_status")).lower() or "recorded"
    if status not in TASK_RUN_CHECKPOINT_STATUSES:
        raise TaskRegisterError(f"task run checkpoint status is invalid: {status}")
    progress_percent = value.get("progress_percent")
    if progress_percent is None or progress_percent == "":
        progress_percent_value: Optional[float] = None
    else:
        try:
            progress_percent_value = float(progress_percent)
        except Exception as exc:
            raise TaskRegisterError("task run checkpoint progress_percent must be a number") from exc
        if progress_percent_value < 0.0 or progress_percent_value > 100.0:
            raise TaskRegisterError("task run checkpoint progress_percent must be within 0-100")
    evidence_refs = _truth_ownership.filter_durable_evidence_refs(list(value.get("evidence_refs") or []))
    return {
        "schema_version": _clean_text(value.get("schema_version")) or TASK_RUN_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id,
        "checkpoint_status": status,
        "checkpoint_utc": _clean_text(value.get("checkpoint_utc")) or _utc_now_iso(),
        "checkpoint_note": _clean_text(value.get("checkpoint_note")),
        "checkpoint_source": _clean_text(value.get("checkpoint_source")),
        "checkpoint_revision": int(_int_or_default(value.get("checkpoint_revision"), default=0)),
        "progress_percent": progress_percent_value,
        "progress_summary": _clean_text(value.get("progress_summary")),
        "evidence_refs": evidence_refs,
    }


def _normalize_task_run_resume_decision(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if not any(str(v).strip() for v in value.values() if v is not None):
        return {}
    decision = _clean_text(value.get("decision")).lower() or "unknown"
    if decision not in TASK_RUN_RESUME_DECISION_OUTCOMES:
        raise TaskRegisterError(f"task run resume decision is invalid: {decision}")
    evidence_refs = _truth_ownership.filter_durable_evidence_refs(list(value.get("evidence_refs") or []))
    return {
        "schema_version": _clean_text(value.get("schema_version")) or TASK_RUN_RESUME_DECISION_SCHEMA_VERSION,
        "decision": decision,
        "decision_reason": _clean_text(value.get("decision_reason")),
        "decision_summary": _clean_text(value.get("decision_summary")),
        "decision_utc": _clean_text(value.get("decision_utc")) or _utc_now_iso(),
        "decision_source": _clean_text(value.get("decision_source")),
        "checkpoint_id": _clean_text(value.get("checkpoint_id")),
        "evidence_refs": evidence_refs,
    }


def _normalize_task_run_intervention(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if not any(str(v).strip() for v in value.values() if v is not None):
        return {}
    intervention_id = _clean_text(value.get("intervention_id"))
    if not intervention_id:
        raise TaskRegisterError("task run intervention requires intervention_id")
    action = _clean_text(value.get("action")).lower()
    if action not in TASK_RUN_INTERVENTION_ACTIONS:
        raise TaskRegisterError(f"task run intervention action is invalid: {action}")
    outcome_state = _clean_text(value.get("outcome_state")).lower() or "accepted"
    if outcome_state not in TASK_RUN_INTERVENTION_OUTCOMES:
        raise TaskRegisterError(f"task run intervention outcome_state is invalid: {outcome_state}")
    evidence_refs = _truth_ownership.filter_durable_evidence_refs(list(value.get("evidence_refs") or []))
    return {
        "schema_version": _clean_text(value.get("schema_version")) or TASK_RUN_INTERVENTION_SCHEMA_VERSION,
        "intervention_id": intervention_id,
        "action": action,
        "requested_utc": _clean_text(value.get("requested_utc")) or _utc_now_iso(),
        "requested_by": _clean_text(value.get("requested_by")),
        "reason": _clean_text(value.get("reason")),
        "outcome_state": outcome_state,
        "outcome_reason": _clean_text(value.get("outcome_reason")),
        "resolved_utc": _clean_text(value.get("resolved_utc")),
        "supersedes_intervention_id": _clean_text(value.get("supersedes_intervention_id")),
        "conflict_with_intervention_id": _clean_text(value.get("conflict_with_intervention_id")),
        "evidence_refs": evidence_refs,
    }


def _normalize_task_run_interventions(values: Any) -> List[Dict[str, Any]]:
    interventions = [_normalize_task_run_intervention(dict(row)) for row in list(values or []) if isinstance(row, dict)]
    ids = [str(row.get("intervention_id") or "") for row in interventions]
    duplicates = {row_id for row_id in ids if row_id and ids.count(row_id) > 1}
    if duplicates:
        dupes = ", ".join(sorted(duplicates))
        raise TaskRegisterError(f"task run interventions have duplicate intervention IDs: {dupes}")
    return interventions


def _latest_task_run_intervention(run_row: Dict[str, Any]) -> Dict[str, Any]:
    interventions = [dict(row) for row in list(run_row.get("interventions") or []) if isinstance(row, dict)]
    if not interventions:
        return {}
    return max(
        interventions,
        key=lambda row: (
            str(row.get("requested_utc") or ""),
            str(row.get("resolved_utc") or ""),
            str(row.get("intervention_id") or ""),
        ),
    )


def _classify_run_resume_decision(
    *,
    run_row: Dict[str, Any],
    checkpoint: Optional[Dict[str, Any]] = None,
    decision_source: str = "",
) -> Dict[str, Any]:
    """Classify resume vs retry vs rebind based on canonical run truth."""
    checkpoint_row = checkpoint if isinstance(checkpoint, dict) else dict(run_row.get("checkpoint") or {})
    checkpoint_row = _normalize_task_run_checkpoint(checkpoint_row) if checkpoint_row else {}
    run_state = _clean_text(run_row.get("run_state")).lower()
    if not checkpoint_row:
        decision = "retry"
        reason = "no_checkpoint_recorded"
    else:
        status = _clean_text(checkpoint_row.get("checkpoint_status")).lower()
        evidence_refs = list(checkpoint_row.get("evidence_refs") or [])
        if status in {"ambiguous"} or not evidence_refs:
            decision = "blocked"
            reason = "ambiguous_or_unverified_checkpoint"
        elif status == "stale":
            decision = "rebind"
            reason = "checkpoint_marked_stale"
        else:
            if run_state in {"stale", "conflict", "blocked"}:
                decision = "rebind"
                reason = "run_state_conflict_or_stale"
            elif run_state in {"failed"}:
                decision = "retry"
                reason = "run_failed_after_checkpoint"
            else:
                decision = "resume"
                reason = "checkpoint_verified_resume_allowed"
    return {
        "schema_version": TASK_RUN_RESUME_DECISION_SCHEMA_VERSION,
        "decision": decision,
        "decision_reason": reason,
        "decision_summary": "",
        "decision_utc": _utc_now_iso(),
        "decision_source": _clean_text(decision_source),
        "checkpoint_id": _clean_text(checkpoint_row.get("checkpoint_id")),
        "evidence_refs": list(checkpoint_row.get("evidence_refs") or []),
    }


def _normalize_task_run(row: Dict[str, Any], *, task_id: str, now: Optional[str] = None) -> Dict[str, Any]:
    created = str(row.get("created_utc") or now or _utc_now_iso())
    run_state = str(row.get("run_state") or "requested").strip().lower()
    if run_state not in TASK_RUN_STATES:
        raise TaskRegisterError(f"task {task_id} run_state is invalid: {run_state or '<missing>'}")
    attention_state = str(row.get("attention_state") or "").strip().lower()
    if not attention_state:
        attention_state = _run_state_to_attention_state(run_state)
    if attention_state not in TASK_ATTENTION_STATES:
        raise TaskRegisterError(f"task {task_id} run attention_state is invalid: {attention_state}")
    run_id = _clean_text(row.get("run_id"))
    if not run_id:
        raise TaskRegisterError(f"task {task_id} task_run requires run_id")
    if not _TASK_RUN_ID_RE.match(run_id):
        raise TaskRegisterError(f"task {task_id} task_run run_id is invalid")
    normalized_task_id = _clean_text(row.get("task_id") or task_id)
    if normalized_task_id and normalized_task_id != _clean_text(task_id):
        raise TaskRegisterError(f"task {task_id} task_run task_id mismatch: {normalized_task_id}")
    evidence_refs = _normalize_task_run_evidence_refs(row.get("evidence_refs"))
    checkpoint = _normalize_task_run_checkpoint(row.get("checkpoint"))
    resume_decision = _normalize_task_run_resume_decision(row.get("resume_decision"))
    interventions = _normalize_task_run_interventions(row.get("interventions"))
    out = {
        "schema_version": str(row.get("schema_version") or TASK_RUN_RECORD_SCHEMA_VERSION).strip() or TASK_RUN_RECORD_SCHEMA_VERSION,
        "run_id": run_id,
        "task_id": normalized_task_id,
        "run_state": run_state,
        "attention_state": attention_state,
        "surface": _normalize_execution_surface(row.get("surface")),
        "execution_id": str(row.get("execution_id") or "").strip(),
        "execution_ref": str(row.get("execution_ref") or "").strip(),
        "binding_ref": str(row.get("binding_ref") or "").strip(),
        "job_uri": str(row.get("job_uri") or "").strip(),
        "trace_id": str(row.get("trace_id") or "").strip(),
        "evidence_refs": evidence_refs,
        "checkpoint": checkpoint,
        "resume_decision": resume_decision,
        "interventions": interventions,
        "created_utc": str(row.get("created_utc") or created),
        "updated_utc": str(row.get("updated_utc") or _utc_now_iso()),
        "writer": str(row.get("writer") or "").strip(),
        "revision": int(_int_or_default(row.get("revision"), default=0)),
        "run_status_source": _clean_text(row.get("run_status_source") or ""),
    }
    return out


def _validate_task_run_record(task_id: str, row: Dict[str, Any]) -> None:
    row_task_id = _clean_text(row.get("task_id") or "")
    run_id = _clean_text(row.get("run_id") or "")
    if row_task_id != _clean_text(task_id):
        raise TaskRegisterError(f"task {task_id} task_run has task_id mismatch: {row_task_id}")
    if not run_id:
        raise TaskRegisterError(f"task {task_id} task_run requires run_id")
    schema_version = str(row.get("schema_version") or "").strip()
    if schema_version and schema_version != TASK_RUN_RECORD_SCHEMA_VERSION:
        raise TaskRegisterError(f"task {task_id} task_run has incompatible schema_version: {schema_version}")
    run_state = str(row.get("run_state") or "").strip().lower()
    if run_state not in TASK_RUN_STATES:
        raise TaskRegisterError(f"task {task_id} run {run_id} run_state is invalid")
    attention_state = str(row.get("attention_state") or "").strip().lower()
    if attention_state not in TASK_ATTENTION_STATES:
        raise TaskRegisterError(f"task {task_id} run {run_id} attention_state is invalid")
    if row.get("revision") is None:
        raise TaskRegisterError(f"task {task_id} run {run_id} revision is required")
    if not isinstance(_int_or_default(row.get("revision"), default=0), int):
        raise TaskRegisterError(f"task {task_id} run {run_id} revision must be an integer")
    if row.get("checkpoint"):
        _normalize_task_run_checkpoint(row.get("checkpoint"))
    if row.get("resume_decision"):
        _normalize_task_run_resume_decision(row.get("resume_decision"))
    if row.get("interventions") is not None:
        _normalize_task_run_interventions(row.get("interventions"))


def _pick_latest_task_run(task_runs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not task_runs:
        return None
    return max(
        list(task_runs),
        key=lambda row: (
            int(_int_or_default(row.get("revision"), default=-1)),
            str(row.get("updated_utc") or row.get("created_utc") or ""),
            str(row.get("run_id") or ""),
        ),
    )


def _task_runs_index(task_runs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("run_id") or "").strip(): dict(row) for row in list(task_runs) if str(row.get("run_id") or "").strip()}


def _normalize_task_runs(task_id: str, task_runs: List[Dict[str, Any]], *, now: Optional[str] = None) -> List[Dict[str, Any]]:
    normalized_runs = [_normalize_task_run(dict(row), task_id=task_id, now=now) for row in task_runs]
    run_ids = [str(row.get("run_id") or "") for row in normalized_runs]
    duplicates = {run_id for run_id in run_ids if run_ids.count(run_id) > 1}
    if duplicates:
        dupes = ", ".join(sorted(duplicates))
        raise TaskRegisterError(f"task {task_id} has duplicate task run IDs: {dupes}")
    return normalized_runs


def _build_task_run_record_payload(
    *,
    task_id: str,
    run_row: Dict[str, Any],
    write_generation: int,
    write_utc: str,
) -> Dict[str, Any]:
    run_row = _normalize_task_run(dict(run_row), task_id=task_id, now=write_utc)
    _validate_task_run_record(task_id=task_id, row=run_row)
    return {
        "task_id": task_id,
        "run_id": str(run_row.get("run_id") or "").strip(),
        "current_run_id": str(run_row.get("run_id") or "").strip(),
        "run_revision": int(run_row.get("revision") or 0),
        "run_row": dict(run_row),
        "task_snapshot": {
            "task_id": task_id,
            "kind": str(run_row.get("task_id") or "software").strip() if False else "software",
            "title": f"task:{task_id}",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "reconcile run history",
            "delegation_refs": [],
        },
        "write_generation": int(write_generation),
        "write_utc": str(write_utc or _utc_now_iso()),
    }


def _normalize_recovered_task_from_run_snapshot(
    task_id: str,
    *,
    task_snapshot: Dict[str, Any],
    run_row: Dict[str, Any],
) -> Dict[str, Any]:
    snapshot = dict(task_snapshot or {})
    if str(snapshot.get("task_id") or "").strip() and str(snapshot.get("task_id") or "").strip() != task_id:
        raise TaskRegisterError(f"task_run_recorded task_snapshot task_id mismatch for {task_id}")
    return _normalize_task(
        {
            "task_id": task_id,
            "kind": str(snapshot.get("kind") or "software").strip(),
            "title": str(snapshot.get("title") or f"task:{task_id}").strip(),
            "status": str(snapshot.get("status") or "active").strip(),
            "owner": str(snapshot.get("owner") or "assistant").strip(),
            "source": str(snapshot.get("source") or "operator").strip(),
            "next_expected_action": str(snapshot.get("next_expected_action") or "reconcile run history").strip(),
            "delegation_refs": list(snapshot.get("delegation_refs") or []),
            "task_runs": [dict(run_row)],
            "current_run_id": str(run_row.get("run_id") or ""),
        }
    )
