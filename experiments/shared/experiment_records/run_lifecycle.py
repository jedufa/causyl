"""Canonical experiment run lifecycle truth helpers.

This module owns the durable per-run lifecycle truth contract for one
experiment run wrapper directory. The primary record is ``run_record.json`` and
optional sidecars capture append-only interventions plus transient
partial-write markers used during crash reconciliation.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .canonical_json import dumps_canonical
from .hashing import sha256_bytes

RUN_RECORD_SCHEMA_VERSION = "experiment_run_record_v1"
RUN_INTERVENTION_SCHEMA_VERSION = "experiment_run_intervention_event_v1"
RUN_PARTIAL_WRITE_SCHEMA_VERSION = "experiment_run_record_partial_write_v1"
RUN_RECORD_FILENAME = "run_record.json"
RUN_INTERVENTION_LOG_FILENAME = "run_record.interventions.jsonl"
RUN_PARTIAL_WRITE_MARKER_FILENAME = "run_record.partial.json"

STATE_LAUNCHING = "launching"
STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"
STATE_CANCELLED = "cancelled"
STATE_UNCERTAIN_INTERRUPTED = "uncertain_interrupted"

TERMINAL_STATES = frozenset({STATE_SUCCEEDED, STATE_FAILED, STATE_SKIPPED, STATE_CANCELLED})
NONTERMINAL_STATES = frozenset({STATE_LAUNCHING, STATE_RUNNING, STATE_UNCERTAIN_INTERRUPTED})
ALL_STATES = TERMINAL_STATES | NONTERMINAL_STATES
TRUTH_CONFIDENCE_VALUES = frozenset({"durable", "reconciled", "uncertain"})
RECONCILIATION_SOURCE_PRIORITY = [
    "run_record",
    "checkpoint_evidence",
    "run_meta",
    "scheduler_state",
    "operator_projection",
]
STATE_TRANSITIONS = {
    STATE_LAUNCHING: frozenset(ALL_STATES),
    STATE_RUNNING: frozenset({STATE_RUNNING, STATE_SUCCEEDED, STATE_FAILED, STATE_SKIPPED, STATE_CANCELLED, STATE_UNCERTAIN_INTERRUPTED}),
    STATE_UNCERTAIN_INTERRUPTED: frozenset({STATE_UNCERTAIN_INTERRUPTED, STATE_RUNNING, STATE_SUCCEEDED, STATE_FAILED, STATE_SKIPPED, STATE_CANCELLED}),
    STATE_SUCCEEDED: frozenset({STATE_SUCCEEDED}),
    STATE_FAILED: frozenset({STATE_FAILED}),
    STATE_SKIPPED: frozenset({STATE_SKIPPED}),
    STATE_CANCELLED: frozenset({STATE_CANCELLED}),
}

_CHECKPOINT_BUNDLE_RE = re.compile(r"bundle_gen(?P<gen>\d{6})$")
_LOCK_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


class RunRecordError(ValueError):
    """Raised when a run record is missing, malformed, or violates lifecycle law."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_bool(value: Any) -> bool:
    return bool(value)


def _normalize_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        try:
            return int(float(str(value).strip()))
        except Exception:
            return None


def _normalize_utc(value: Any, *, fallback: str = "") -> str:
    text = _normalize_text(value)
    if not text:
        return str(fallback or "")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return str(fallback or "")


def _parse_utc(value: Any) -> Optional[datetime]:
    text = _normalize_utc(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _normalize_string_list(values: Iterable[Any]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in list(values or []):
        item = _normalize_text(raw)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _stable_json_line(obj: Mapping[str, Any]) -> str:
    return dumps_canonical(dict(obj)) + "\n"


def _read_json_object(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RunRecordError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise RunRecordError(f"expected JSON object in {path}")
    return obj


def _read_json_object_best_effort(path: Path) -> Dict[str, Any]:
    try:
        return _read_json_object(path)
    except Exception:
        return {}


def _read_jsonl_objects(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCK_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _run_dir_for(target: str | os.PathLike[str]) -> Path:
    p = Path(target).resolve()
    if p.is_dir():
        return p
    if p.name == RUN_RECORD_FILENAME:
        return p.parent.resolve()
    raise RunRecordError(f"expected run directory or {RUN_RECORD_FILENAME} path: {target}")


def run_record_path(target: str | os.PathLike[str]) -> Path:
    run_dir = _run_dir_for(target)
    return run_dir / RUN_RECORD_FILENAME


def interventions_log_path(target: str | os.PathLike[str]) -> Path:
    run_dir = _run_dir_for(target)
    return run_dir / RUN_INTERVENTION_LOG_FILENAME


def partial_write_marker_path(target: str | os.PathLike[str]) -> Path:
    run_dir = _run_dir_for(target)
    return run_dir / RUN_PARTIAL_WRITE_MARKER_FILENAME


def _project_id_from_entry_module(entry_module: str) -> str:
    text = _normalize_text(entry_module)
    parts = text.split(".")
    if len(parts) >= 4 and parts[0] == "experiments" and parts[1] == "projects":
        return _normalize_text(parts[2]).lower()
    return ""


def _rel_if_under(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.name


def build_initial_run_record(
    *,
    run_id: str,
    job_id: str = "",
    job_spec_hash: str = "",
    project_id: str = "",
    created_at_utc: str = "",
    launch: Optional[Mapping[str, Any]] = None,
    paths: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the canonical initial ``run_record.json`` payload."""
    created = _normalize_utc(created_at_utc, fallback=_utc_now_iso())
    launch_obj = dict(launch or {})
    host_obj = launch_obj.get("host") if isinstance(launch_obj.get("host"), dict) else {}
    resources_obj = launch_obj.get("resources") if isinstance(launch_obj.get("resources"), dict) else {}
    merged_paths = {
        "job_spec": "job_spec.json",
        "run_meta": "run_meta.json",
        "artifact_manifest": "artifact_manifest.json",
        "stdout_stderr_log": "stdout_stderr.log",
        "payload_dir": "payload",
        "branch_meta": "branch_meta.json",
        "resume_from": "resume_from.json",
        "interventions": RUN_INTERVENTION_LOG_FILENAME,
    }
    merged_paths.update({k: v for k, v in dict(paths or {}).items() if _normalize_text(k)})

    record: Dict[str, Any] = {
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "run_id": _normalize_text(run_id),
        "project_id": _normalize_text(project_id) or _project_id_from_entry_module(_normalize_text(launch_obj.get("entry_module"))),
        "job_id": _normalize_text(job_id),
        "job_spec_hash": _normalize_text(job_spec_hash),
        "record_hash": "",
        "created_at_utc": created,
        "updated_at_utc": created,
        "launch": {
            "git_ref": _normalize_text(launch_obj.get("git_ref")),
            "resolved_commit": _normalize_text(launch_obj.get("resolved_commit")),
            "describe": _normalize_text(launch_obj.get("describe")),
            "dirty": _normalize_bool(launch_obj.get("dirty")),
            "entry_module": _normalize_text(launch_obj.get("entry_module")),
            "cli_args": [str(x) for x in list(launch_obj.get("cli_args") or [])],
            "seed": _normalize_int(launch_obj.get("seed")),
            "contract_hash": _normalize_text(launch_obj.get("contract_hash")),
            "wrapper_root": _normalize_text(launch_obj.get("wrapper_root")),
            "run_dir_rel": _normalize_text(launch_obj.get("run_dir_rel")),
            "payload_dir_rel": _normalize_text(launch_obj.get("payload_dir_rel")) or "payload",
            "host": {
                "hostname": _normalize_text(host_obj.get("hostname")),
                "os": _normalize_text(host_obj.get("os")),
                "python": _normalize_text(host_obj.get("python")),
            },
            "resources": {
                "device": _normalize_text(resources_obj.get("device")),
            },
        },
        "lifecycle": {
            "state": STATE_LAUNCHING,
            "confidence": "durable",
            "transition_reason": "wrapper_launch_record_created",
            "terminal": False,
            "launched_at_utc": created,
            "last_transition_utc": created,
            "last_heartbeat_utc": "",
            "heartbeat_count": 0,
            "ended_at_utc": "",
            "exit_code": None,
            "failure_code": "",
            "failure_detail": "",
        },
        "checkpoint": {
            "evidence_root_rel": "",
            "branch_id": "",
            "parent_branch_id": "",
            "root_branch_id": "",
            "parent_checkpoint_id": "",
            "root_checkpoint_id": "",
            "resume_from_checkpoint_id": "",
            "resume_from_generation": None,
            "resume_from_relpath": "",
            "resume_status": "fresh",
            "latest_checkpoint_id": "",
            "latest_checkpoint_generation": None,
            "latest_checkpoint_relpath": "",
            "checkpoint_count": 0,
            "warnings": [],
        },
        "interventions": {
            "count": 0,
            "repair_count": 0,
            "last_action": "",
            "last_action_at_utc": "",
            "last_actor": "",
            "actions_path": RUN_INTERVENTION_LOG_FILENAME,
        },
        "reconciliation": {
            "status": "not_run",
            "last_reconciled_at_utc": "",
            "source_priority": list(RECONCILIATION_SOURCE_PRIORITY),
            "repair_count": 0,
            "warnings": [],
        },
        "observations": {
            "run_meta_status": "",
            "run_meta_end_time_utc": "",
            "run_meta_exit_code": None,
            "scheduler_status": "",
            "partial_write_marker_present": False,
        },
        "paths": {k: _normalize_text(v) for k, v in merged_paths.items()},
    }
    return canonicalize_run_record(record)


def canonicalize_run_record(record: Mapping[str, Any], *, run_id_fallback: str = "") -> Dict[str, Any]:
    """Return a normalized run-record object with schema defaults applied."""
    src = dict(record or {})
    launch = src.get("launch") if isinstance(src.get("launch"), dict) else {}
    lifecycle = src.get("lifecycle") if isinstance(src.get("lifecycle"), dict) else {}
    checkpoint = src.get("checkpoint") if isinstance(src.get("checkpoint"), dict) else {}
    interventions = src.get("interventions") if isinstance(src.get("interventions"), dict) else {}
    reconciliation = src.get("reconciliation") if isinstance(src.get("reconciliation"), dict) else {}
    observations = src.get("observations") if isinstance(src.get("observations"), dict) else {}
    paths = src.get("paths") if isinstance(src.get("paths"), dict) else {}

    run_id = _normalize_text(src.get("run_id")) or _normalize_text(run_id_fallback)
    project_id = _normalize_text(src.get("project_id")) or _project_id_from_entry_module(_normalize_text(launch.get("entry_module")))
    created = _normalize_utc(src.get("created_at_utc"), fallback=_utc_now_iso())
    updated = _normalize_utc(src.get("updated_at_utc"), fallback=created)
    state = _normalize_text(lifecycle.get("state")) or STATE_LAUNCHING
    if state not in ALL_STATES:
        state = STATE_LAUNCHING
    confidence = _normalize_text(lifecycle.get("confidence")) or "durable"
    if confidence not in TRUTH_CONFIDENCE_VALUES:
        confidence = "durable"

    host = launch.get("host") if isinstance(launch.get("host"), dict) else {}
    resources = launch.get("resources") if isinstance(launch.get("resources"), dict) else {}

    canonical: Dict[str, Any] = {
        "schema_version": RUN_RECORD_SCHEMA_VERSION,
        "run_id": run_id,
        "project_id": project_id,
        "job_id": _normalize_text(src.get("job_id")),
        "job_spec_hash": _normalize_text(src.get("job_spec_hash")),
        "record_hash": _normalize_text(src.get("record_hash")),
        "created_at_utc": created,
        "updated_at_utc": updated,
        "launch": {
            "git_ref": _normalize_text(launch.get("git_ref")),
            "resolved_commit": _normalize_text(launch.get("resolved_commit")),
            "describe": _normalize_text(launch.get("describe")),
            "dirty": _normalize_bool(launch.get("dirty")),
            "entry_module": _normalize_text(launch.get("entry_module")),
            "cli_args": [str(x) for x in list(launch.get("cli_args") or [])],
            "seed": _normalize_int(launch.get("seed")),
            "contract_hash": _normalize_text(launch.get("contract_hash")),
            "wrapper_root": _normalize_text(launch.get("wrapper_root")),
            "run_dir_rel": _normalize_text(launch.get("run_dir_rel")),
            "payload_dir_rel": _normalize_text(launch.get("payload_dir_rel")) or "payload",
            "host": {
                "hostname": _normalize_text(host.get("hostname")),
                "os": _normalize_text(host.get("os")),
                "python": _normalize_text(host.get("python")),
            },
            "resources": {
                "device": _normalize_text(resources.get("device")),
            },
        },
        "lifecycle": {
            "state": state,
            "confidence": confidence,
            "transition_reason": _normalize_text(lifecycle.get("transition_reason")),
            "terminal": bool(state in TERMINAL_STATES),
            "launched_at_utc": _normalize_utc(lifecycle.get("launched_at_utc"), fallback=created),
            "last_transition_utc": _normalize_utc(lifecycle.get("last_transition_utc"), fallback=created),
            "last_heartbeat_utc": _normalize_utc(lifecycle.get("last_heartbeat_utc")),
            "heartbeat_count": int(_normalize_int(lifecycle.get("heartbeat_count")) or 0),
            "ended_at_utc": _normalize_utc(lifecycle.get("ended_at_utc")),
            "exit_code": _normalize_int(lifecycle.get("exit_code")),
            "failure_code": _normalize_text(lifecycle.get("failure_code")),
            "failure_detail": _normalize_text(lifecycle.get("failure_detail")),
        },
        "checkpoint": {
            "evidence_root_rel": _normalize_text(checkpoint.get("evidence_root_rel")),
            "branch_id": _normalize_text(checkpoint.get("branch_id")),
            "parent_branch_id": _normalize_text(checkpoint.get("parent_branch_id")),
            "root_branch_id": _normalize_text(checkpoint.get("root_branch_id")),
            "parent_checkpoint_id": _normalize_text(checkpoint.get("parent_checkpoint_id")),
            "root_checkpoint_id": _normalize_text(checkpoint.get("root_checkpoint_id")),
            "resume_from_checkpoint_id": _normalize_text(checkpoint.get("resume_from_checkpoint_id")),
            "resume_from_generation": _normalize_int(checkpoint.get("resume_from_generation")),
            "resume_from_relpath": _normalize_text(checkpoint.get("resume_from_relpath")),
            "resume_status": _normalize_text(checkpoint.get("resume_status")) or "fresh",
            "latest_checkpoint_id": _normalize_text(checkpoint.get("latest_checkpoint_id")),
            "latest_checkpoint_generation": _normalize_int(checkpoint.get("latest_checkpoint_generation")),
            "latest_checkpoint_relpath": _normalize_text(checkpoint.get("latest_checkpoint_relpath")),
            "checkpoint_count": int(_normalize_int(checkpoint.get("checkpoint_count")) or 0),
            "warnings": _normalize_string_list(checkpoint.get("warnings") or []),
        },
        "interventions": {
            "count": int(_normalize_int(interventions.get("count")) or 0),
            "repair_count": int(_normalize_int(interventions.get("repair_count")) or 0),
            "last_action": _normalize_text(interventions.get("last_action")),
            "last_action_at_utc": _normalize_utc(interventions.get("last_action_at_utc")),
            "last_actor": _normalize_text(interventions.get("last_actor")),
            "actions_path": _normalize_text(interventions.get("actions_path")) or RUN_INTERVENTION_LOG_FILENAME,
        },
        "reconciliation": {
            "status": _normalize_text(reconciliation.get("status")) or "not_run",
            "last_reconciled_at_utc": _normalize_utc(reconciliation.get("last_reconciled_at_utc")),
            "source_priority": _normalize_string_list(reconciliation.get("source_priority") or RECONCILIATION_SOURCE_PRIORITY),
            "repair_count": int(_normalize_int(reconciliation.get("repair_count")) or 0),
            "warnings": _normalize_string_list(reconciliation.get("warnings") or []),
        },
        "observations": {
            "run_meta_status": _normalize_text(observations.get("run_meta_status")),
            "run_meta_end_time_utc": _normalize_utc(observations.get("run_meta_end_time_utc")),
            "run_meta_exit_code": _normalize_int(observations.get("run_meta_exit_code")),
            "scheduler_status": _normalize_text(observations.get("scheduler_status")),
            "partial_write_marker_present": bool(observations.get("partial_write_marker_present", False)),
        },
        "paths": {
            "job_spec": _normalize_text(paths.get("job_spec")) or "job_spec.json",
            "run_meta": _normalize_text(paths.get("run_meta")) or "run_meta.json",
            "artifact_manifest": _normalize_text(paths.get("artifact_manifest")) or "artifact_manifest.json",
            "stdout_stderr_log": _normalize_text(paths.get("stdout_stderr_log")) or "stdout_stderr.log",
            "payload_dir": _normalize_text(paths.get("payload_dir")) or "payload",
            "branch_meta": _normalize_text(paths.get("branch_meta")) or "branch_meta.json",
            "resume_from": _normalize_text(paths.get("resume_from")) or "resume_from.json",
            "interventions": _normalize_text(paths.get("interventions")) or RUN_INTERVENTION_LOG_FILENAME,
        },
    }
    return canonical


def compute_run_record_hash(record: Mapping[str, Any]) -> str:
    """Compute the canonical run-record hash with ``record_hash`` blanked."""
    canonical = canonicalize_run_record(record)
    material = dict(canonical)
    material["record_hash"] = ""
    return "sha256:" + sha256_bytes(dumps_canonical(material).encode("utf-8"))


def _validate_transition(previous_state: Optional[str], new_state: str, *, allow_repair: bool) -> None:
    if not previous_state:
        return
    if previous_state == new_state:
        return
    if allow_repair:
        return
    allowed = STATE_TRANSITIONS.get(previous_state, frozenset())
    if new_state not in allowed:
        raise RunRecordError(f"illegal run-record state transition: {previous_state} -> {new_state}")


def validate_run_record(
    record: Mapping[str, Any],
    *,
    previous: Optional[Mapping[str, Any]] = None,
    allow_repair: bool = False,
    run_id_fallback: str = "",
) -> Dict[str, Any]:
    """Validate and normalize one run record against schema and state law."""
    canonical = canonicalize_run_record(record, run_id_fallback=run_id_fallback)
    if canonical["schema_version"] != RUN_RECORD_SCHEMA_VERSION:
        raise RunRecordError(f"unexpected schema_version: {canonical['schema_version']}")
    if not canonical["run_id"]:
        raise RunRecordError("run_record.run_id is required")
    if canonical["lifecycle"]["state"] not in ALL_STATES:
        raise RunRecordError(f"invalid lifecycle state: {canonical['lifecycle']['state']}")
    if canonical["lifecycle"]["confidence"] not in TRUTH_CONFIDENCE_VALUES:
        raise RunRecordError(f"invalid truth confidence: {canonical['lifecycle']['confidence']}")
    terminal = bool(canonical["lifecycle"]["state"] in TERMINAL_STATES)
    if bool(canonical["lifecycle"]["terminal"]) != terminal:
        raise RunRecordError("lifecycle.terminal does not match lifecycle.state")
    if terminal and not canonical["lifecycle"]["ended_at_utc"] and not allow_repair:
        raise RunRecordError("terminal run records must include lifecycle.ended_at_utc")
    if previous is not None:
        prev = validate_run_record(previous, allow_repair=True, run_id_fallback=run_id_fallback)
        if prev["run_id"] != canonical["run_id"]:
            raise RunRecordError("run_record.run_id may not change across updates")
        _validate_transition(prev["lifecycle"]["state"], canonical["lifecycle"]["state"], allow_repair=allow_repair)
    return canonical


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_run_record(
    target: str | os.PathLike[str],
    record: Mapping[str, Any],
    *,
    previous: Optional[Mapping[str, Any]] = None,
    allow_repair: bool = False,
) -> Dict[str, Any]:
    """Atomically write ``run_record.json`` with a partial-write marker."""
    record_path = run_record_path(target)
    run_dir = record_path.parent
    marker_path = partial_write_marker_path(run_dir)
    lock = _lock_for(record_path)
    with lock:
        if previous is None and record_path.exists():
            if allow_repair:
                try:
                    previous = read_run_record(record_path)
                except Exception:
                    previous = None
            else:
                previous = read_run_record(record_path)
        validated = validate_run_record(record, previous=previous, allow_repair=allow_repair, run_id_fallback=run_dir.name)
        validated["updated_at_utc"] = _normalize_utc(validated.get("updated_at_utc"), fallback=_utc_now_iso())
        validated["record_hash"] = ""
        validated["record_hash"] = compute_run_record_hash(validated)
        marker = {
            "schema_version": RUN_PARTIAL_WRITE_SCHEMA_VERSION,
            "target_file": RUN_RECORD_FILENAME,
            "run_id": str(validated.get("run_id") or run_dir.name),
            "started_at_utc": _utc_now_iso(),
        }
        _write_text_atomic(marker_path, _stable_json_line(marker))
        try:
            _write_text_atomic(record_path, _stable_json_line(validated))
        except Exception:
            raise
        else:
            try:
                marker_path.unlink(missing_ok=True)
            except Exception:
                pass
        return validated


def read_run_record(target: str | os.PathLike[str]) -> Dict[str, Any]:
    """Read and validate a canonical run record."""
    record_path = run_record_path(target)
    obj = _read_json_object(record_path)
    validated = validate_run_record(obj, run_id_fallback=record_path.parent.name, allow_repair=False)
    expected_hash = compute_run_record_hash(validated)
    actual_hash = _normalize_text(validated.get("record_hash"))
    if actual_hash and actual_hash != expected_hash:
        raise RunRecordError(f"run_record hash mismatch: expected {expected_hash}, got {actual_hash}")
    validated["record_hash"] = expected_hash
    return validated


def try_read_run_record(target: str | os.PathLike[str]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Best-effort read that returns warnings instead of raising."""
    record_path = run_record_path(target)
    if not record_path.exists():
        return None, ["run_record_missing"]
    try:
        return read_run_record(record_path), []
    except Exception as exc:
        return None, [f"run_record_invalid:{exc}"]


def update_run_record(
    target: str | os.PathLike[str],
    updater: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    allow_repair: bool = False,
    create_if_missing: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Load, mutate, and atomically rewrite one run record."""
    record_path = run_record_path(target)
    lock = _lock_for(record_path)
    with lock:
        if record_path.exists():
            current = read_run_record(record_path)
            previous: Optional[Dict[str, Any]] = current
        else:
            if create_if_missing is None:
                raise RunRecordError(f"run_record missing: {record_path}")
            current = validate_run_record(create_if_missing(), run_id_fallback=record_path.parent.name, allow_repair=True)
            previous = None
        proposed = updater(dict(current))
        return write_run_record(record_path, proposed, previous=previous, allow_repair=allow_repair)


def _refresh_intervention_summary(target: str | os.PathLike[str]) -> Dict[str, Any]:
    run_dir = _run_dir_for(target)
    rows = _read_jsonl_objects(interventions_log_path(run_dir))
    count = len(rows)
    repair_count = sum(1 for row in rows if _normalize_bool(row.get("repair")))
    last = rows[-1] if rows else {}

    def _updater(current: Dict[str, Any]) -> Dict[str, Any]:
        current.setdefault("interventions", {})
        current["interventions"] = {
            **current.get("interventions", {}),
            "count": int(count),
            "repair_count": int(repair_count),
            "last_action": _normalize_text(last.get("action")),
            "last_action_at_utc": _normalize_utc(last.get("recorded_at_utc")),
            "last_actor": _normalize_text(last.get("actor")),
            "actions_path": RUN_INTERVENTION_LOG_FILENAME,
        }
        current["updated_at_utc"] = _utc_now_iso()
        return current

    return update_run_record(
        run_dir,
        _updater,
        allow_repair=True,
        create_if_missing=lambda: build_initial_run_record(run_id=run_dir.name),
    )


def record_intervention(
    target: str | os.PathLike[str],
    *,
    action: str,
    actor: str = "operator",
    reason: str = "",
    details: Optional[Mapping[str, Any]] = None,
    repair: bool = False,
    recorded_at_utc: str = "",
) -> Dict[str, Any]:
    """Append one operator or repair action and refresh summary counts."""
    run_dir = _run_dir_for(target)
    event = {
        "schema_version": RUN_INTERVENTION_SCHEMA_VERSION,
        "recorded_at_utc": _normalize_utc(recorded_at_utc, fallback=_utc_now_iso()),
        "action": _normalize_text(action),
        "actor": _normalize_text(actor) or "operator",
        "reason": _normalize_text(reason),
        "repair": bool(repair),
        "event_type": "repair_action" if repair else "operator_action",
        "details": dict(details or {}),
    }
    if not event["action"]:
        raise RunRecordError("intervention action is required")
    log_path = interventions_log_path(run_dir)
    lock = _lock_for(log_path)
    with lock:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(_stable_json_line(event))
    _refresh_intervention_summary(run_dir)
    return event


def record_launch(
    target: str | os.PathLike[str],
    *,
    job_id: str,
    job_spec_hash: str,
    launch: Mapping[str, Any],
    created_at_utc: str = "",
) -> Dict[str, Any]:
    """Create the initial durable canonical run record for one wrapper run."""
    run_dir = _run_dir_for(target)
    initial = build_initial_run_record(
        run_id=run_dir.name,
        job_id=job_id,
        job_spec_hash=job_spec_hash,
        project_id=_normalize_text(launch.get("project_id")),
        created_at_utc=created_at_utc,
        launch=launch,
    )
    return write_run_record(run_dir, initial, previous=None, allow_repair=True)


def record_heartbeat(
    target: str | os.PathLike[str],
    *,
    heartbeat_at_utc: str = "",
    note: str = "",
) -> Dict[str, Any]:
    """Persist a running heartbeat update against the canonical run record."""
    run_dir = _run_dir_for(target)
    heartbeat = _normalize_utc(heartbeat_at_utc, fallback=_utc_now_iso())

    def _updater(current: Dict[str, Any]) -> Dict[str, Any]:
        lifecycle = dict(current.get("lifecycle") or {})
        state = _normalize_text(lifecycle.get("state")) or STATE_LAUNCHING
        if state in TERMINAL_STATES:
            raise RunRecordError(f"cannot record heartbeat for terminal state: {state}")
        next_state = STATE_RUNNING if state != STATE_RUNNING else STATE_RUNNING
        lifecycle["state"] = next_state
        lifecycle["confidence"] = "durable"
        if state != next_state:
            lifecycle["last_transition_utc"] = heartbeat
        lifecycle["transition_reason"] = _normalize_text(note) or ("wrapper_heartbeat" if state == STATE_RUNNING else "wrapper_started_running")
        lifecycle["last_heartbeat_utc"] = heartbeat
        lifecycle["heartbeat_count"] = int(_normalize_int(lifecycle.get("heartbeat_count")) or 0) + 1
        lifecycle["terminal"] = False
        current["lifecycle"] = lifecycle
        current["updated_at_utc"] = heartbeat
        return current

    return update_run_record(
        run_dir,
        _updater,
        create_if_missing=lambda: build_initial_run_record(run_id=run_dir.name, created_at_utc=heartbeat),
    )


def _status_to_state(status: str) -> str:
    raw = _normalize_text(status).lower()
    if raw in {"complete", "completed", "success", "succeeded", "ok"}:
        return STATE_SUCCEEDED
    if raw in {"failed", "error"}:
        return STATE_FAILED
    if raw in {"skipped", "skip"}:
        return STATE_SKIPPED
    if raw in {"cancelled", "canceled", "stopped"}:
        return STATE_CANCELLED
    if raw in {STATE_LAUNCHING, STATE_RUNNING, STATE_UNCERTAIN_INTERRUPTED}:
        return raw
    return STATE_FAILED


def record_terminal(
    target: str | os.PathLike[str],
    *,
    status: str,
    exit_code: Optional[int],
    failure_code: str = "",
    failure_detail: str = "",
    ended_at_utc: str = "",
    transition_reason: str = "",
) -> Dict[str, Any]:
    """Persist one terminal lifecycle update."""
    run_dir = _run_dir_for(target)
    ended = _normalize_utc(ended_at_utc, fallback=_utc_now_iso())
    final_state = _status_to_state(status)

    def _updater(current: Dict[str, Any]) -> Dict[str, Any]:
        lifecycle = dict(current.get("lifecycle") or {})
        lifecycle["state"] = final_state
        lifecycle["confidence"] = "durable"
        lifecycle["transition_reason"] = _normalize_text(transition_reason) or f"wrapper_terminal:{_normalize_text(status).lower() or final_state}"
        lifecycle["last_transition_utc"] = ended
        lifecycle["ended_at_utc"] = ended
        lifecycle["terminal"] = True
        lifecycle["exit_code"] = _normalize_int(exit_code)
        lifecycle["failure_code"] = _normalize_text(failure_code)
        lifecycle["failure_detail"] = _normalize_text(failure_detail)
        current["lifecycle"] = lifecycle
        current["updated_at_utc"] = ended
        return current

    return update_run_record(
        run_dir,
        _updater,
        create_if_missing=lambda: build_initial_run_record(run_id=run_dir.name, created_at_utc=ended),
    )


def _checkpoint_rows_from_index(root: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    index_path = root / "viz" / "checkpoints_index.json"
    if not index_path.exists():
        return [], []
    try:
        obj = _read_json_object(index_path)
    except Exception:
        return [], [f"invalid_checkpoints_index:{_rel_if_under(index_path, root)}"]
    rows: List[Dict[str, Any]] = []
    for row in list(obj.get("checkpoints") or []):
        if not isinstance(row, dict):
            continue
        gen = _normalize_int(row.get("generation"))
        rel = _normalize_text(row.get("relative_path"))
        ckpt_id = _normalize_text(row.get("checkpoint_id")) or (Path(rel).name if rel else "")
        if gen is None:
            continue
        rows.append(
            {
                "generation": int(gen),
                "checkpoint_id": ckpt_id,
                "relative_path": rel,
            }
        )
    rows.sort(key=lambda item: int(item.get("generation", -1)))
    return rows, []


def _checkpoint_rows_from_bundles(root: Path) -> List[Dict[str, Any]]:
    bundle_root = root / "checkpoint_bundles_v2"
    if not bundle_root.exists() or not bundle_root.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for child in sorted(bundle_root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        match = _CHECKPOINT_BUNDLE_RE.match(child.name)
        if not match:
            continue
        gen = int(match.group("gen"))
        meta = _read_json_object_best_effort(child / "metadata.json")
        gen = int(_normalize_int(meta.get("generation_index")) or gen)
        ckpt_id = _normalize_text(meta.get("checkpoint_id")) or child.name
        rows.append(
            {
                "generation": int(gen),
                "checkpoint_id": ckpt_id,
                "relative_path": _rel_if_under(child, root),
            }
        )
    rows.sort(key=lambda item: int(item.get("generation", -1)))
    return rows


def _discover_checkpoint_evidence_roots(run_dir: Path, *, max_depth: int = 6) -> List[Path]:
    markers = {"branch_meta.json", "resume_from.json"}
    out: List[Path] = []
    for root, dirs, files in os.walk(run_dir):
        path = Path(root)
        try:
            depth = len(path.relative_to(run_dir).parts)
        except Exception:
            depth = max_depth + 1
        if depth > max_depth:
            dirs[:] = []
            continue
        file_set = set(files)
        has_marker = bool(file_set & markers)
        has_checkpoint_dir = (path / "checkpoint_bundles_v2").is_dir()
        has_index = (path / "viz" / "checkpoints_index.json").is_file()
        if has_marker or has_checkpoint_dir or has_index:
            out.append(path.resolve())
    out.sort(key=lambda p: (len(p.relative_to(run_dir).parts), p.as_posix()))
    dedup: List[Path] = []
    seen: set[str] = set()
    for path in out:
        key = str(path)
        if key not in seen:
            seen.add(key)
            dedup.append(path)
    return dedup


def collect_checkpoint_truth(target: str | os.PathLike[str]) -> Dict[str, Any]:
    """Derive checkpoint, resume, and branch lineage truth from durable evidence."""
    run_dir = _run_dir_for(target)
    roots = _discover_checkpoint_evidence_roots(run_dir)
    warnings: List[str] = []
    evidence_root = roots[0] if roots else run_dir
    if len(roots) > 1:
        warnings.append("ambiguous_checkpoint_evidence_roots")

    branch_meta = _read_json_object_best_effort(evidence_root / "branch_meta.json")
    resume_from = _read_json_object_best_effort(evidence_root / "resume_from.json")
    index_rows, index_warnings = _checkpoint_rows_from_index(evidence_root)
    bundle_rows = _checkpoint_rows_from_bundles(evidence_root)
    rows = index_rows or bundle_rows
    warnings.extend(index_warnings)

    latest = rows[-1] if rows else {}
    resume_generation = _normalize_int(resume_from.get("checkpoint_generation"))
    resume_relpath = _normalize_text(resume_from.get("checkpoint_dir")) or _normalize_text(resume_from.get("relative_checkpoint_path"))
    resume_checkpoint_id = _normalize_text(resume_from.get("checkpoint_id"))
    if not resume_checkpoint_id and resume_relpath:
        for row in rows:
            if _normalize_text(row.get("relative_path")) == resume_relpath:
                resume_checkpoint_id = _normalize_text(row.get("checkpoint_id"))
                if resume_generation is None:
                    resume_generation = _normalize_int(row.get("generation"))
                break
    if resume_generation is None and resume_relpath:
        match = _CHECKPOINT_BUNDLE_RE.search(Path(resume_relpath).name)
        if match:
            resume_generation = int(match.group("gen"))

    parent_branch_id = _normalize_text(branch_meta.get("parent_branch_id"))
    parent_checkpoint_id = _normalize_text(branch_meta.get("parent_checkpoint_id"))
    if resume_checkpoint_id and parent_branch_id:
        resume_status = "branched_resume"
    elif resume_checkpoint_id:
        resume_status = "resumed"
    elif parent_branch_id or parent_checkpoint_id:
        resume_status = "branch_child"
    elif latest:
        resume_status = "checkpointed"
    else:
        resume_status = "fresh"

    return {
        "evidence_root_rel": _rel_if_under(evidence_root, run_dir) if evidence_root != run_dir else "",
        "branch_id": _normalize_text(branch_meta.get("branch_id")),
        "parent_branch_id": parent_branch_id,
        "root_branch_id": _normalize_text(branch_meta.get("root_branch_id")),
        "parent_checkpoint_id": parent_checkpoint_id,
        "root_checkpoint_id": _normalize_text(branch_meta.get("root_checkpoint_id")),
        "resume_from_checkpoint_id": resume_checkpoint_id,
        "resume_from_generation": resume_generation,
        "resume_from_relpath": resume_relpath,
        "resume_status": resume_status,
        "latest_checkpoint_id": _normalize_text(latest.get("checkpoint_id")),
        "latest_checkpoint_generation": _normalize_int(latest.get("generation")),
        "latest_checkpoint_relpath": _normalize_text(latest.get("relative_path")),
        "checkpoint_count": int(len(rows)),
        "warnings": _normalize_string_list(warnings),
    }


def _merge_checkpoint_truth(current: Mapping[str, Any], observed: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(canonicalize_run_record({"checkpoint": current}).get("checkpoint") or {})
    for key, value in dict(observed or {}).items():
        if key == "warnings":
            merged["warnings"] = _normalize_string_list(list(merged.get("warnings") or []) + list(value or []))
            continue
        if value not in (None, "", []):
            merged[key] = value
    return merged


def _map_run_meta_state(run_meta: Mapping[str, Any]) -> Optional[str]:
    raw = _normalize_text(run_meta.get("status")).lower()
    if raw in {"complete", "completed", "success", "succeeded"}:
        return STATE_SUCCEEDED
    if raw == "skipped":
        return STATE_SKIPPED
    if raw in {"failed", "error"}:
        return STATE_FAILED
    if raw in {"started", "running"}:
        return STATE_RUNNING
    return None


def _heartbeat_is_stale(record: Mapping[str, Any], *, observed_at_utc: str, stale_after_sec: int) -> bool:
    lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
    heartbeat_ts = _parse_utc(lifecycle.get("last_heartbeat_utc"))
    if heartbeat_ts is None:
        heartbeat_ts = _parse_utc(lifecycle.get("launched_at_utc"))
    observed_ts = _parse_utc(observed_at_utc)
    if heartbeat_ts is None or observed_ts is None:
        return False
    age = (observed_ts - heartbeat_ts).total_seconds()
    return age >= float(max(stale_after_sec, 1))


def reconcile_run_record(
    target: str | os.PathLike[str],
    *,
    run_meta: Optional[Mapping[str, Any]] = None,
    scheduler_state: Optional[Mapping[str, Any]] = None,
    observed_at_utc: str = "",
    stale_heartbeat_after_sec: int = 120,
    record_repair_event: bool = True,
) -> Dict[str, Any]:
    """Reconcile conflicting evidence into one canonical durable run answer."""
    run_dir = _run_dir_for(target)
    observed = _normalize_utc(observed_at_utc, fallback=_utc_now_iso())
    existing, read_warnings = try_read_run_record(run_dir)
    repairs: List[str] = []
    if existing is None:
        repairs.extend(read_warnings)
        current = build_initial_run_record(run_id=run_dir.name, created_at_utc=observed)
    else:
        current = dict(existing)
    partial_present = partial_write_marker_path(run_dir).exists()
    if partial_present:
        repairs.append("partial_write_marker_present")

    run_meta_obj = dict(run_meta or _read_json_object_best_effort(run_dir / "run_meta.json"))
    scheduler_obj = dict(scheduler_state or {})
    checkpoint_truth = collect_checkpoint_truth(run_dir)
    checkpoint_warnings = list(checkpoint_truth.get("warnings") or [])
    if checkpoint_warnings:
        repairs.extend(checkpoint_warnings)

    current["checkpoint"] = _merge_checkpoint_truth(current.get("checkpoint") or {}, checkpoint_truth)
    observations = dict(current.get("observations") or {})
    observations["run_meta_status"] = _normalize_text(run_meta_obj.get("status"))
    observations["run_meta_end_time_utc"] = _normalize_utc(run_meta_obj.get("end_time_utc"))
    observations["run_meta_exit_code"] = _normalize_int(run_meta_obj.get("exit_code"))
    observations["scheduler_status"] = _normalize_text(scheduler_obj.get("status"))
    observations["partial_write_marker_present"] = bool(partial_present)
    current["observations"] = observations

    lifecycle = dict(current.get("lifecycle") or {})
    current_state = _normalize_text(lifecycle.get("state")) or STATE_LAUNCHING
    desired_state = current_state
    desired_confidence = _normalize_text(lifecycle.get("confidence")) or "durable"
    transition_reason = _normalize_text(lifecycle.get("transition_reason")) or "reconciled_no_change"

    meta_state = _map_run_meta_state(run_meta_obj)
    if current_state in TERMINAL_STATES and meta_state and meta_state != current_state:
        repairs.append("terminal_conflict_preserved_canonical")
    elif meta_state:
        desired_state = meta_state
        desired_confidence = "durable" if meta_state in TERMINAL_STATES else "reconciled"
        transition_reason = f"reconciled_from_run_meta:{_normalize_text(run_meta_obj.get('status')).lower() or meta_state}"

    scheduler_status = _normalize_text(scheduler_obj.get("status")).lower()
    if desired_state not in TERMINAL_STATES and scheduler_status in {"running", "active"}:
        desired_state = STATE_RUNNING
        desired_confidence = "reconciled"
        transition_reason = f"reconciled_from_scheduler:{scheduler_status}"

    if desired_state not in TERMINAL_STATES and (
        partial_present or _heartbeat_is_stale(current, observed_at_utc=observed, stale_after_sec=stale_heartbeat_after_sec)
    ):
        desired_state = STATE_UNCERTAIN_INTERRUPTED
        desired_confidence = "uncertain"
        transition_reason = "reconciled_uncertain_interrupted"
        repairs.append("uncertain_interrupted_projected")

    if desired_state != current_state:
        lifecycle["last_transition_utc"] = observed
    lifecycle["state"] = desired_state
    lifecycle["confidence"] = desired_confidence
    lifecycle["transition_reason"] = transition_reason
    lifecycle["terminal"] = bool(desired_state in TERMINAL_STATES)
    if desired_state in TERMINAL_STATES:
        lifecycle["ended_at_utc"] = _normalize_utc(run_meta_obj.get("end_time_utc"), fallback=observed)
        lifecycle["exit_code"] = _normalize_int(run_meta_obj.get("exit_code"))
        lifecycle["failure_code"] = _normalize_text(run_meta_obj.get("failure_code"))
        lifecycle["failure_detail"] = _normalize_text(run_meta_obj.get("failure_detail"))
    current["lifecycle"] = lifecycle

    reconciliation = dict(current.get("reconciliation") or {})
    reconciliation["last_reconciled_at_utc"] = observed
    reconciliation["source_priority"] = list(RECONCILIATION_SOURCE_PRIORITY)
    reconciliation["warnings"] = _normalize_string_list(list(reconciliation.get("warnings") or []) + repairs)
    reconciliation["repair_count"] = int(_normalize_int(reconciliation.get("repair_count")) or 0) + (1 if repairs else 0)
    if desired_state == STATE_UNCERTAIN_INTERRUPTED:
        reconciliation["status"] = "uncertain"
    elif repairs:
        reconciliation["status"] = "repaired"
    else:
        reconciliation["status"] = "clean"
    current["reconciliation"] = reconciliation
    current["updated_at_utc"] = observed

    written = write_run_record(run_dir, current, previous=existing, allow_repair=True)
    if repairs and record_repair_event:
        record_intervention(
            run_dir,
            action="reconcile_run_truth",
            actor="system",
            reason=", ".join(repairs),
            details={
                "state": desired_state,
                "reconciliation_status": reconciliation["status"],
            },
            repair=True,
            recorded_at_utc=observed,
        )
        return read_run_record(run_dir)
    return written


def summarize_run_record(record_or_target: Mapping[str, Any] | str | os.PathLike[str]) -> Dict[str, Any]:
    """Return a compact operator-facing summary for one canonical run record."""
    record = read_run_record(record_or_target) if not isinstance(record_or_target, Mapping) else validate_run_record(record_or_target, allow_repair=True)
    checkpoint = record.get("checkpoint") if isinstance(record.get("checkpoint"), dict) else {}
    interventions = record.get("interventions") if isinstance(record.get("interventions"), dict) else {}
    reconciliation = record.get("reconciliation") if isinstance(record.get("reconciliation"), dict) else {}
    lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
    warnings = _normalize_string_list(list(checkpoint.get("warnings") or []) + list(reconciliation.get("warnings") or []))
    return {
        "schema_version": "experiment_run_record_summary_v1",
        "run_id": record.get("run_id"),
        "project_id": record.get("project_id"),
        "job_id": record.get("job_id"),
        "job_spec_hash": record.get("job_spec_hash"),
        "state": lifecycle.get("state"),
        "confidence": lifecycle.get("confidence"),
        "terminal": bool(lifecycle.get("terminal")),
        "transition_reason": lifecycle.get("transition_reason"),
        "last_heartbeat_utc": lifecycle.get("last_heartbeat_utc"),
        "ended_at_utc": lifecycle.get("ended_at_utc"),
        "exit_code": lifecycle.get("exit_code"),
        "failure_code": lifecycle.get("failure_code"),
        "latest_checkpoint_generation": checkpoint.get("latest_checkpoint_generation"),
        "latest_checkpoint_id": checkpoint.get("latest_checkpoint_id"),
        "resume_status": checkpoint.get("resume_status"),
        "checkpoint_count": checkpoint.get("checkpoint_count"),
        "intervention_count": interventions.get("count"),
        "repair_count": interventions.get("repair_count"),
        "reconciliation_status": reconciliation.get("status"),
        "warnings": warnings,
        "updated_at_utc": record.get("updated_at_utc"),
    }


__all__ = [
    "ALL_STATES",
    "NONTERMINAL_STATES",
    "RUN_INTERVENTION_LOG_FILENAME",
    "RUN_RECORD_FILENAME",
    "RUN_RECORD_SCHEMA_VERSION",
    "RUN_PARTIAL_WRITE_MARKER_FILENAME",
    "RunRecordError",
    "TERMINAL_STATES",
    "TRUTH_CONFIDENCE_VALUES",
    "build_initial_run_record",
    "canonicalize_run_record",
    "collect_checkpoint_truth",
    "compute_run_record_hash",
    "interventions_log_path",
    "partial_write_marker_path",
    "read_run_record",
    "record_heartbeat",
    "record_intervention",
    "record_launch",
    "record_terminal",
    "reconcile_run_record",
    "run_record_path",
    "summarize_run_record",
    "try_read_run_record",
    "update_run_record",
    "validate_run_record",
    "write_run_record",
]
