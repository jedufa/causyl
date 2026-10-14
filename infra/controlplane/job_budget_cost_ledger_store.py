"""Durable append/replay helpers for the job budget and cost ledger."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Mapping

from infra.controlplane import job_budget_cost_ledger_contract as contract

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover - non-POSIX fallback.
    _fcntl = None  # type: ignore[assignment]


RecordKind = Literal["allocation", "cost_event", "recovery_outcome"]
RecoveryPolicy = Literal[
    "allow_future_runs",
    "warn_future_runs",
    "require_manual_review",
    "block_future_runs",
]
RecoveryReasonCode = Literal[
    "overspend_detected",
    "ledger_inconsistency_detected",
    "manual_reconciliation",
    "manual_override",
]

RECOVERY_OUTCOME_LOG_FILENAME = "recovery_outcomes.jsonl"
RECOVERY_OUTCOME_SCHEMA_VERSION = "job_budget_recovery_outcome_v1"
RECOVERY_REASON_CODES: tuple[str, ...] = (
    "overspend_detected",
    "ledger_inconsistency_detected",
    "manual_reconciliation",
    "manual_override",
)
RECOVERY_FOLLOW_UP_POLICIES: tuple[str, ...] = (
    "allow_future_runs",
    "warn_future_runs",
    "require_manual_review",
    "block_future_runs",
)

_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ledger_root(*, repo_root: Path | None = None) -> Path:
    base = Path(repo_root) if repo_root is not None else _repo_root()
    return (base / contract.STORAGE_ROOT_REL).resolve()


def ledger_paths_for_job(job_uid: str, *, repo_root: Path | None = None) -> Dict[str, Path]:
    rel_paths = contract.ledger_paths_for_job(job_uid)
    root = ledger_root(repo_root=repo_root)
    job_dir = (root / "jobs" / rel_paths["job_dir"].name).resolve()
    return {
        "ledger_root": root,
        "job_dir": job_dir,
        "allocation_log": (job_dir / contract.ALLOCATION_LOG_FILENAME).resolve(),
        "cost_event_log": (job_dir / contract.COST_EVENT_LOG_FILENAME).resolve(),
        "recovery_outcome_log": (job_dir / RECOVERY_OUTCOME_LOG_FILENAME).resolve(),
        "lock_file": (job_dir / ".ledger.lock").resolve(),
    }


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def _job_lock(lock_file: Path, *, shared: bool) -> Iterator[None]:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    thread_lock = _thread_lock(lock_file)
    with thread_lock:
        with open(lock_file, "a+", encoding="utf-8") as handle:
            if _fcntl is not None:
                mode = _fcntl.LOCK_SH if shared else _fcntl.LOCK_EX
                _fcntl.flock(handle.fileno(), mode)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def _append_jsonl(path: Path, record: Mapping[str, Any], *, lock_file: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with _job_lock(lock_file, shared=False):
        _append_jsonl_unlocked(path, line)


def _append_jsonl_unlocked(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def append_allocation_record(record: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = contract.validate_allocation_record(record)
    paths = ledger_paths_for_job(normalized["job_uid"], repo_root=repo_root)
    _append_jsonl(paths["allocation_log"], normalized, lock_file=paths["lock_file"])
    return dict(normalized)


def append_allocation_record_once(
    record: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    """Append one allocation if its stable UID has not already been recorded."""
    normalized = contract.validate_allocation_record(record)
    paths = ledger_paths_for_job(normalized["job_uid"], repo_root=repo_root)
    line = json.dumps(dict(normalized), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with _job_lock(paths["lock_file"], shared=False):
        rows = _read_jsonl_strict(paths["allocation_log"])
        for row in rows:
            existing = contract.validate_allocation_record(row)
            if existing["allocation_uid"] != normalized["allocation_uid"]:
                continue
            if existing != normalized:
                raise ValueError(
                    "allocation UID already exists with different content: "
                    f"{normalized['allocation_uid']}"
                )
            return {"record": dict(existing), "deduped": True}
        paths["allocation_log"].parent.mkdir(parents=True, exist_ok=True)
        _append_jsonl_unlocked(paths["allocation_log"], line)
    return {"record": dict(normalized), "deduped": False}


def append_cost_event_record(record: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = contract.validate_cost_event_record(record)
    paths = ledger_paths_for_job(normalized["job_uid"], repo_root=repo_root)
    _append_jsonl(paths["cost_event_log"], normalized, lock_file=paths["lock_file"])
    return dict(normalized)


def _validate_nonempty_text(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def validate_recovery_outcome_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    required = (
        "schema_version",
        "recovery_outcome_uid",
        "job_uid",
        "recorded_at_utc",
        "actor",
        "reason_code",
        "follow_up_policy",
    )
    missing = [field_name for field_name in required if field_name not in record]
    if missing:
        raise ValueError(f"recovery outcome record missing required fields: {', '.join(missing)}")
    if str(record.get("schema_version") or "") != RECOVERY_OUTCOME_SCHEMA_VERSION:
        raise ValueError(f"recovery outcome record schema_version must remain {RECOVERY_OUTCOME_SCHEMA_VERSION}")
    reason_code = str(record.get("reason_code") or "").strip()
    if reason_code not in RECOVERY_REASON_CODES:
        raise ValueError("reason_code must be one of: " + ", ".join(RECOVERY_REASON_CODES))
    follow_up_policy = str(record.get("follow_up_policy") or "").strip()
    if follow_up_policy not in RECOVERY_FOLLOW_UP_POLICIES:
        raise ValueError("follow_up_policy must be one of: " + ", ".join(RECOVERY_FOLLOW_UP_POLICIES))

    return {
        "schema_version": RECOVERY_OUTCOME_SCHEMA_VERSION,
        "recovery_outcome_uid": contract._validate_path_safe_identifier(
            record.get("recovery_outcome_uid"),
            field_name="recovery_outcome_uid",
        ),
        "job_uid": contract._validate_path_safe_identifier(record.get("job_uid"), field_name="job_uid"),
        "recorded_at_utc": contract._validate_recorded_at_utc(record.get("recorded_at_utc")),
        "actor": _validate_nonempty_text(record.get("actor"), field_name="actor"),
        "reason_code": reason_code,
        "follow_up_policy": follow_up_policy,
        "message": str(record.get("message") or "").strip(),
        "evidence": dict(record.get("evidence") or {}),
    }


def append_recovery_outcome_record(record: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = validate_recovery_outcome_record(record)
    paths = ledger_paths_for_job(normalized["job_uid"], repo_root=repo_root)
    _append_jsonl(paths["recovery_outcome_log"], normalized, lock_file=paths["lock_file"])
    return dict(normalized)


def append_cost_event_record_once(
    record: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    """Append one cost event if its stable UID has not already been recorded."""
    normalized = contract.validate_cost_event_record(record)
    paths = ledger_paths_for_job(normalized["job_uid"], repo_root=repo_root)
    line = json.dumps(dict(normalized), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with _job_lock(paths["lock_file"], shared=False):
        rows = _read_jsonl_strict(paths["cost_event_log"])
        for row in rows:
            existing = contract.validate_cost_event_record(row)
            if existing["cost_event_uid"] != normalized["cost_event_uid"]:
                continue
            if existing != normalized:
                raise ValueError(
                    "cost event UID already exists with different content: "
                    f"{normalized['cost_event_uid']}"
                )
            return {"record": dict(existing), "deduped": True}
        paths["cost_event_log"].parent.mkdir(parents=True, exist_ok=True)
        _append_jsonl_unlocked(paths["cost_event_log"], line)
    return {"record": dict(normalized), "deduped": False}


def append_recovery_outcome_record_once(
    record: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    normalized = validate_recovery_outcome_record(record)
    paths = ledger_paths_for_job(normalized["job_uid"], repo_root=repo_root)
    line = json.dumps(dict(normalized), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with _job_lock(paths["lock_file"], shared=False):
        rows = _read_jsonl_strict(paths["recovery_outcome_log"])
        for row in rows:
            existing = validate_recovery_outcome_record(row)
            if existing["recovery_outcome_uid"] != normalized["recovery_outcome_uid"]:
                continue
            if existing != normalized:
                raise ValueError(
                    "recovery outcome UID already exists with different content: "
                    f"{normalized['recovery_outcome_uid']}"
                )
            return {"record": dict(existing), "deduped": True}
        paths["recovery_outcome_log"].parent.mkdir(parents=True, exist_ok=True)
        _append_jsonl_unlocked(paths["recovery_outcome_log"], line)
    return {"record": dict(normalized), "deduped": False}


def _read_jsonl_strict(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed JSONL entry in {path} at line {idx}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"expected JSON object in {path} at line {idx}")
        rows.append(obj)
    return rows


def read_allocation_records(job_uid: str, *, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    canonical_job_uid = contract._validate_path_safe_identifier(job_uid, field_name="job_uid")
    paths = ledger_paths_for_job(canonical_job_uid, repo_root=repo_root)
    with _job_lock(paths["lock_file"], shared=True):
        rows = _read_jsonl_strict(paths["allocation_log"])
    out: List[Dict[str, Any]] = []
    for row in rows:
        record = contract.validate_allocation_record(row)
        if record["job_uid"] != canonical_job_uid:
            raise ValueError("allocation record job_uid does not match its partition")
        out.append(record)
    return out


def read_cost_event_records(job_uid: str, *, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    canonical_job_uid = contract._validate_path_safe_identifier(job_uid, field_name="job_uid")
    paths = ledger_paths_for_job(canonical_job_uid, repo_root=repo_root)
    with _job_lock(paths["lock_file"], shared=True):
        rows = _read_jsonl_strict(paths["cost_event_log"])
    out: List[Dict[str, Any]] = []
    for row in rows:
        record = contract.validate_cost_event_record(row)
        if record["job_uid"] != canonical_job_uid:
            raise ValueError("cost event record job_uid does not match its partition")
        out.append(record)
    return out


def read_recovery_outcome_records(job_uid: str, *, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    canonical_job_uid = contract._validate_path_safe_identifier(job_uid, field_name="job_uid")
    paths = ledger_paths_for_job(canonical_job_uid, repo_root=repo_root)
    with _job_lock(paths["lock_file"], shared=True):
        rows = _read_jsonl_strict(paths["recovery_outcome_log"])
    out: List[Dict[str, Any]] = []
    for row in rows:
        record = validate_recovery_outcome_record(row)
        if record["job_uid"] != canonical_job_uid:
            raise ValueError("recovery outcome record job_uid does not match its partition")
        out.append(record)
    return out


def replay_job_budget_state(job_uid: str, *, repo_root: Path | None = None) -> Dict[str, Any]:
    allocations = read_allocation_records(job_uid, repo_root=repo_root)
    cost_events = read_cost_event_records(job_uid, repo_root=repo_root)
    return contract.replay_job_budget_balance(allocations, cost_events, job_uid=job_uid)


def total_allocated_minor_units(job_uid: str, *, repo_root: Path | None = None) -> int:
    return int(replay_job_budget_state(job_uid, repo_root=repo_root)["allocated_minor_units"])


def total_spent_minor_units(job_uid: str, *, repo_root: Path | None = None) -> int:
    state = replay_job_budget_state(job_uid, repo_root=repo_root)
    return int(state["cost_debit_minor_units"]) - int(state["cost_credit_minor_units"])


def remaining_budget_minor_units(job_uid: str, *, repo_root: Path | None = None) -> int:
    return int(replay_job_budget_state(job_uid, repo_root=repo_root)["remaining_minor_units"])


def _durable_events_from_records(
    allocations: List[Dict[str, Any]],
    cost_events: List[Dict[str, Any]],
    recovery_outcomes: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for record in allocations:
        events.append(
            {
                "record_kind": "allocation",
                "entry_uid": record["allocation_uid"],
                **record,
            }
        )
    for record in cost_events:
        events.append(
            {
                "record_kind": "cost_event",
                "entry_uid": record["cost_event_uid"],
                **record,
            }
        )
    for record in recovery_outcomes or []:
        events.append(
            {
                "record_kind": "recovery_outcome",
                "entry_uid": record["recovery_outcome_uid"],
                **record,
            }
        )
    return events


def last_durable_event(job_uid: str, *, repo_root: Path | None = None) -> Dict[str, Any] | None:
    allocations = read_allocation_records(job_uid, repo_root=repo_root)
    cost_events = read_cost_event_records(job_uid, repo_root=repo_root)
    recovery_outcomes = read_recovery_outcome_records(job_uid, repo_root=repo_root)
    events = _durable_events_from_records(allocations, cost_events, recovery_outcomes)
    if not events:
        return None
    return max(events, key=lambda item: (item["recorded_at_utc"], item["entry_uid"], item["record_kind"]))


def latest_recovery_outcome(job_uid: str, *, repo_root: Path | None = None) -> Dict[str, Any] | None:
    rows = read_recovery_outcome_records(job_uid, repo_root=repo_root)
    if not rows:
        return None
    return max(rows, key=lambda item: (item["recorded_at_utc"], item["recovery_outcome_uid"]))


def recovery_follow_up_state(job_uid: str, *, repo_root: Path | None = None) -> Dict[str, Any]:
    record = latest_recovery_outcome(job_uid, repo_root=repo_root)
    if record is None:
        return {
            "job_uid": contract._validate_path_safe_identifier(job_uid, field_name="job_uid"),
            "present": False,
            "status": "clear",
            "allowed": True,
            "warning": False,
            "reason": "",
            "message": "",
            "record": None,
        }
    policy = str(record["follow_up_policy"])
    status = {
        "allow_future_runs": "clear",
        "warn_future_runs": "warning",
        "require_manual_review": "review_required",
        "block_future_runs": "blocked",
    }[policy]
    return {
        "job_uid": record["job_uid"],
        "present": True,
        "status": status,
        "allowed": policy in {"allow_future_runs", "warn_future_runs"},
        "warning": policy == "warn_future_runs",
        "reason": str(record["reason_code"]),
        "message": str(record.get("message") or ""),
        "record": dict(record),
    }


def ensure_recovery_outcome_recorded(
    job_uid: str,
    *,
    recorded_at_utc: str,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    canonical_job_uid = contract._validate_path_safe_identifier(job_uid, field_name="job_uid")
    normalized_recorded_at = contract._validate_recorded_at_utc(recorded_at_utc)
    persisted: Dict[str, Any] | None = None
    detected_state = "clear"
    snapshot: Dict[str, Any] | None = None
    error = ""

    try:
        allocations = read_allocation_records(canonical_job_uid, repo_root=repo_root)
        cost_events = read_cost_event_records(canonical_job_uid, repo_root=repo_root)
        snapshot = replay_job_budget_state(canonical_job_uid, repo_root=repo_root)
        remaining_minor_units = int(snapshot.get("remaining_minor_units") or 0)
        if remaining_minor_units < 0:
            events = _durable_events_from_records(allocations, cost_events)
            last_event = (
                max(events, key=lambda item: (item["recorded_at_utc"], item["entry_uid"], item["record_kind"]))
                if events
                else None
            )
            entry_uid = str((last_event or {}).get("entry_uid") or "replay")
            amount = abs(remaining_minor_units)
            result = append_recovery_outcome_record_once(
                {
                    "schema_version": RECOVERY_OUTCOME_SCHEMA_VERSION,
                    "recovery_outcome_uid": f"{canonical_job_uid}:overspend:{entry_uid}:{amount}",
                    "job_uid": canonical_job_uid,
                    "recorded_at_utc": normalized_recorded_at,
                    "actor": "system",
                    "reason_code": "overspend_detected",
                    "follow_up_policy": "block_future_runs",
                    "message": f"Replay-derived remaining budget dropped below zero by {amount} {snapshot.get('unit') or 'minor_units'}.",
                    "evidence": {
                        "remaining_minor_units": remaining_minor_units,
                        "unit": snapshot.get("unit") or "",
                        "trigger_entry_uid": entry_uid,
                    },
                },
                repo_root=repo_root,
            )
            persisted = dict(result["record"])
            detected_state = "blocked"
    except Exception as exc:
        fingerprint = sha256(f"{exc.__class__.__name__}:{exc}".encode("utf-8")).hexdigest()[:12]
        error = str(exc)
        result = append_recovery_outcome_record_once(
            {
                "schema_version": RECOVERY_OUTCOME_SCHEMA_VERSION,
                "recovery_outcome_uid": f"{canonical_job_uid}:inconsistency:{fingerprint}",
                "job_uid": canonical_job_uid,
                "recorded_at_utc": normalized_recorded_at,
                "actor": "system",
                "reason_code": "ledger_inconsistency_detected",
                "follow_up_policy": "require_manual_review",
                "message": f"Durable ledger replay failed and requires manual review: {exc}",
                "evidence": {
                    "error_class": exc.__class__.__name__,
                    "error": str(exc),
                },
            },
            repo_root=repo_root,
        )
        persisted = dict(result["record"])
        detected_state = "review_required"

    return {
        **recovery_follow_up_state(canonical_job_uid, repo_root=repo_root),
        "detected_state": detected_state,
        "persisted_record": persisted,
        "snapshot": snapshot,
        "error": error,
    }
