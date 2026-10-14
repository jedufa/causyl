"""Durable append-only storage and lookup helpers for continuation packets."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Mapping

from infra.controlplane import continuation_packet_contract as contract

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover - non-POSIX fallback.
    _fcntl = None  # type: ignore[assignment]


STORE_SCHEMA_VERSION = "continuation_packet_store_v1"
PACKET_RECORD_KIND = "packet_revision"
STATUS_RECORD_KIND = "packet_status"
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_INVALIDATED = "invalidated"
LOOKUP_KEY_NAMES: tuple[str, ...] = ("job_id", "run_id", "attempt_id", "job_family", "campaign_id")

_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


class ContinuationPacketStoreError(ValueError):
    """Raised when packet-store writes or reads fail closed."""


class AmbiguousLatestValidPacketError(ContinuationPacketStoreError):
    """Raised when latest-valid lookup cannot choose one durable packet head."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def store_root(*, repo_root: Path | None = None) -> Path:
    base = Path(repo_root) if repo_root is not None else _repo_root()
    return (base / "runtime" / "state" / "controlplane" / "continuation_packets").resolve()


def store_paths(*, repo_root: Path | None = None) -> Dict[str, Path]:
    root = store_root(repo_root=repo_root)
    return {
        "store_root": root,
        "records_log": (root / "records.jsonl").resolve(),
        "lock_file": (root / ".store.lock").resolve(),
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
def _store_lock(lock_file: Path, *, shared: bool) -> Iterator[None]:
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


def _append_jsonl_unlocked(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


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
            raise ContinuationPacketStoreError(f"malformed JSONL entry in {path} at line {idx}") from exc
        if not isinstance(obj, dict):
            raise ContinuationPacketStoreError(f"expected JSON object in {path} at line {idx}")
        rows.append(obj)
    return rows


def _validate_identifier(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ContinuationPacketStoreError(f"{field_name} must be a non-empty string")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if any(ch not in allowed for ch in text):
        raise ContinuationPacketStoreError(
            f"{field_name} must contain only letters, digits, '.', '_', ':', or '-'"
        )
    return text


def _normalize_lookup_keys(packet: Mapping[str, Any], lookup_keys: Mapping[str, Any] | None) -> Dict[str, str]:
    source_run = packet.get("source_run") if isinstance(packet.get("source_run"), dict) else {}
    raw = dict(lookup_keys or {})
    normalized: Dict[str, str] = {}
    for key_name in LOOKUP_KEY_NAMES:
        if key_name in {"job_id", "run_id", "attempt_id"} and key_name not in raw:
            raw[key_name] = source_run.get(key_name)
        value = raw.get(key_name)
        if value in (None, ""):
            continue
        normalized[key_name] = _validate_identifier(value, field_name=f"lookup_keys.{key_name}")
    for key_name in raw:
        if key_name not in LOOKUP_KEY_NAMES:
            raise ContinuationPacketStoreError(f"unsupported lookup key: {key_name}")
    for required in ("job_id", "run_id"):
        if required not in normalized:
            raise ContinuationPacketStoreError(f"lookup_keys.{required} is required")
    return normalized


def _normalize_status_reason(value: Any) -> str:
    return str(value or "").strip()


def _validate_packet_for_storage(raw_packet: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_packet or {})
    if "schema_version" not in raw:
        raise ContinuationPacketStoreError("packet write rejected: schema_version must be explicit")
    result = contract.validate_continuation_packet(raw)
    if not result["ok"]:
        details = ", ".join(f"{row['field']}:{row['code']}" for row in result["errors"])
        raise ContinuationPacketStoreError(f"packet write rejected: {details}")
    return dict(result["normalized_packet"])


def _packet_revision_uid(packet_id: str, packet_revision: int) -> str:
    return f"{packet_id}:r{packet_revision}"


def _status_record_uid(packet_id: str, packet_revision: int, status: str, target: str) -> str:
    return f"{packet_id}:r{packet_revision}:{status}:{target}"


def _packet_record(
    *,
    packet: Mapping[str, Any],
    lookup_keys: Mapping[str, str],
) -> Dict[str, Any]:
    packet_id = str(packet["packet_id"])
    packet_revision = int(packet["packet_revision"])
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "record_kind": PACKET_RECORD_KIND,
        "record_uid": _packet_revision_uid(packet_id, packet_revision),
        "packet_id": packet_id,
        "packet_revision": packet_revision,
        "created_utc": str(packet["created_utc"]),
        "packet_fingerprint": contract.continuation_packet_fingerprint(packet),
        "status": STATUS_ACTIVE,
        "supersedes_packet_id": str(packet.get("supersedes_packet_id") or ""),
        "source_run": dict(packet["source_run"]),
        "lookup_keys": dict(lookup_keys),
        "packet": dict(packet),
    }


def _packet_status_record(
    *,
    packet_id: str,
    packet_revision: int,
    status: Literal["superseded", "invalidated"],
    recorded_at_utc: str,
    reason: str,
    replacement_packet_id: str = "",
    replacement_packet_revision: int | None = None,
) -> Dict[str, Any]:
    target = replacement_packet_id or reason or status
    record: Dict[str, Any] = {
        "schema_version": STORE_SCHEMA_VERSION,
        "record_kind": STATUS_RECORD_KIND,
        "record_uid": _status_record_uid(packet_id, packet_revision, status, target),
        "packet_id": packet_id,
        "packet_revision": packet_revision,
        "status": status,
        "recorded_at_utc": recorded_at_utc,
        "reason": reason,
    }
    if replacement_packet_id:
        record["replacement_packet_id"] = replacement_packet_id
    if replacement_packet_revision is not None:
        record["replacement_packet_revision"] = replacement_packet_revision
    return record


def _normalize_store_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    record_kind = str(row.get("record_kind") or "").strip()
    schema_version = str(row.get("schema_version") or "").strip()
    if schema_version != STORE_SCHEMA_VERSION:
        raise ContinuationPacketStoreError(f"unexpected continuation packet store schema_version: {schema_version or '<missing>'}")
    if record_kind == PACKET_RECORD_KIND:
        packet = row.get("packet")
        if not isinstance(packet, dict):
            raise ContinuationPacketStoreError("packet revision record missing packet payload")
        normalized_packet = _validate_packet_for_storage(packet)
        lookup_keys = _normalize_lookup_keys(normalized_packet, row.get("lookup_keys") if isinstance(row.get("lookup_keys"), dict) else {})
        packet_record = _packet_record(packet=normalized_packet, lookup_keys=lookup_keys)
        if str(row.get("packet_fingerprint") or "") != packet_record["packet_fingerprint"]:
            raise ContinuationPacketStoreError(
                f"packet revision fingerprint drift detected for {packet_record['record_uid']}"
            )
        return packet_record
    if record_kind == STATUS_RECORD_KIND:
        packet_id = _validate_identifier(row.get("packet_id"), field_name="packet_id")
        packet_revision = row.get("packet_revision")
        if not isinstance(packet_revision, int) or packet_revision <= 0:
            raise ContinuationPacketStoreError("packet status record packet_revision must be a positive integer")
        status = str(row.get("status") or "").strip()
        if status not in {STATUS_SUPERSEDED, STATUS_INVALIDATED}:
            raise ContinuationPacketStoreError(f"unsupported packet status: {status or '<missing>'}")
        recorded_at_utc = str(row.get("recorded_at_utc") or "").strip()
        if not recorded_at_utc:
            raise ContinuationPacketStoreError("packet status record recorded_at_utc is required")
        reason = _normalize_status_reason(row.get("reason"))
        replacement_packet_id = str(row.get("replacement_packet_id") or "").strip()
        replacement_packet_revision = row.get("replacement_packet_revision")
        if replacement_packet_revision is not None and (
            not isinstance(replacement_packet_revision, int) or replacement_packet_revision <= 0
        ):
            raise ContinuationPacketStoreError("replacement_packet_revision must be a positive integer when provided")
        return _packet_status_record(
            packet_id=packet_id,
            packet_revision=packet_revision,
            status=status,  # type: ignore[arg-type]
            recorded_at_utc=recorded_at_utc,
            reason=reason,
            replacement_packet_id=replacement_packet_id,
            replacement_packet_revision=replacement_packet_revision,
        )
    raise ContinuationPacketStoreError(f"unsupported continuation packet store record_kind: {record_kind or '<missing>'}")


def read_store_records(*, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    paths = store_paths(repo_root=repo_root)
    with _store_lock(paths["lock_file"], shared=True):
        return [_normalize_store_row(row) for row in _read_jsonl_strict(paths["records_log"])]


def _replay_state(rows: List[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    revisions: Dict[str, Dict[str, Any]] = {}
    statuses: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        normalized = _normalize_store_row(row)
        key = _packet_revision_uid(normalized["packet_id"], normalized["packet_revision"])
        if normalized["record_kind"] == PACKET_RECORD_KIND:
            existing = revisions.get(key)
            if existing is not None and existing != normalized:
                raise ContinuationPacketStoreError(f"conflicting duplicate packet revision identity: {key}")
            revisions[key] = normalized
            statuses.setdefault(key, [])
            continue
        if key not in revisions:
            raise ContinuationPacketStoreError(f"packet status references unknown revision: {key}")
        bucket = statuses.setdefault(key, [])
        if normalized not in bucket:
            bucket.append(normalized)

    active_heads: Dict[str, Dict[str, Any]] = {}
    for key, revision_row in revisions.items():
        status_rows = statuses.get(key, [])
        terminal_status = revision_row["status"]
        for status_row in status_rows:
            terminal_status = status_row["status"]
        current = dict(revision_row)
        current["status"] = terminal_status
        current["status_history"] = list(status_rows)
        existing = active_heads.get(current["packet_id"])
        if existing is None or int(current["packet_revision"]) > int(existing["packet_revision"]):
            active_heads[current["packet_id"]] = current
    return active_heads


def _matching_active_heads(
    active_heads: Mapping[str, Mapping[str, Any]],
    *,
    lookup_key: str,
    lookup_value: str,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for row in active_heads.values():
        if row["status"] != STATUS_ACTIVE:
            continue
        if str((row.get("lookup_keys") or {}).get(lookup_key) or "") != lookup_value:
            continue
        matches.append(dict(row))
    matches.sort(key=lambda item: (str(item["created_utc"]), str(item["packet_id"]), int(item["packet_revision"])), reverse=True)
    return matches


def append_packet_revision_once(
    raw_packet: Mapping[str, Any],
    *,
    lookup_keys: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    normalized_packet = _validate_packet_for_storage(raw_packet)
    normalized_lookup_keys = _normalize_lookup_keys(normalized_packet, lookup_keys)
    packet_row = _packet_record(packet=normalized_packet, lookup_keys=normalized_lookup_keys)
    supersedes_packet_id = str(normalized_packet.get("supersedes_packet_id") or "")
    paths = store_paths(repo_root=repo_root)

    with _store_lock(paths["lock_file"], shared=False):
        rows = _read_jsonl_strict(paths["records_log"])
        active_heads = _replay_state(rows)
        revision_key = packet_row["record_uid"]
        normalized_rows = [_normalize_store_row(row) for row in rows]
        existing_revision = next(
            (
                row
                for row in normalized_rows
                if row["record_kind"] == PACKET_RECORD_KIND and row["record_uid"] == revision_key
            ),
            None,
        )
        if existing_revision is not None:
            if existing_revision["packet_fingerprint"] != packet_row["packet_fingerprint"]:
                raise ContinuationPacketStoreError(f"conflicting duplicate packet revision identity: {revision_key}")
            return {"record": dict(existing_revision), "deduped": True}

        superseded: Dict[str, Any] | None = None
        if supersedes_packet_id:
            superseded = active_heads.get(supersedes_packet_id)
            if superseded is None or superseded["status"] != STATUS_ACTIVE:
                raise ContinuationPacketStoreError(
                    f"supersedes_packet_id does not reference an active durable packet: {supersedes_packet_id}"
                )

        for lookup_key, lookup_value in normalized_lookup_keys.items():
            matches = _matching_active_heads(active_heads, lookup_key=lookup_key, lookup_value=lookup_value)
            if not matches:
                continue
            if len(matches) > 1:
                raise AmbiguousLatestValidPacketError(
                    f"ambiguous latest-valid lookup already exists for {lookup_key}={lookup_value}"
                )
            current = matches[0]
            same_packet = current["packet_id"] == packet_row["packet_id"]
            supersedes_current = supersedes_packet_id and supersedes_packet_id == current["packet_id"]
            if not same_packet and not supersedes_current:
                raise AmbiguousLatestValidPacketError(
                    f"new packet would create ambiguous latest-valid lookup for {lookup_key}={lookup_value}"
                )

        _append_jsonl_unlocked(paths["records_log"], packet_row)

        if supersedes_packet_id:
            assert superseded is not None  # Preflight above guarantees the durable target exists.
            status_row = _packet_status_record(
                packet_id=superseded["packet_id"],
                packet_revision=superseded["packet_revision"],
                status=STATUS_SUPERSEDED,
                recorded_at_utc=packet_row["created_utc"],
                reason="superseded_by_successor_packet",
                replacement_packet_id=packet_row["packet_id"],
                replacement_packet_revision=packet_row["packet_revision"],
            )
            _append_jsonl_unlocked(paths["records_log"], status_row)
        else:
            prior_same_packet = active_heads.get(packet_row["packet_id"])
            if prior_same_packet is not None and prior_same_packet["status"] == STATUS_ACTIVE:
                status_row = _packet_status_record(
                    packet_id=prior_same_packet["packet_id"],
                    packet_revision=prior_same_packet["packet_revision"],
                    status=STATUS_SUPERSEDED,
                    recorded_at_utc=packet_row["created_utc"],
                    reason="superseded_by_newer_revision",
                    replacement_packet_id=packet_row["packet_id"],
                    replacement_packet_revision=packet_row["packet_revision"],
                )
                _append_jsonl_unlocked(paths["records_log"], status_row)

    return {"record": dict(packet_row), "deduped": False}


def invalidate_packet(
    packet_id: str,
    *,
    packet_revision: int | None = None,
    reason: str,
    recorded_at_utc: str,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    canonical_packet_id = _validate_identifier(packet_id, field_name="packet_id")
    canonical_reason = _normalize_status_reason(reason)
    if not canonical_reason:
        raise ContinuationPacketStoreError("invalidation reason is required")
    if not str(recorded_at_utc or "").strip():
        raise ContinuationPacketStoreError("recorded_at_utc is required")
    paths = store_paths(repo_root=repo_root)
    with _store_lock(paths["lock_file"], shared=False):
        rows = _read_jsonl_strict(paths["records_log"])
        active_heads = _replay_state(rows)
        normalized_rows = [_normalize_store_row(entry) for entry in rows]
        if packet_revision is None:
            target = active_heads.get(canonical_packet_id)
            if target is None:
                raise ContinuationPacketStoreError(f"unknown packet_id: {canonical_packet_id}")
        else:
            revision_uid = _packet_revision_uid(canonical_packet_id, packet_revision)
            target = next(
                (
                    row
                    for row in active_heads.values()
                    if row["packet_id"] == canonical_packet_id and row["record_uid"] == revision_uid
                ),
                None,
            )
            if target is None:
                target = next(
                    (
                        row
                        for row in normalized_rows
                        if row["record_kind"] == PACKET_RECORD_KIND and row["record_uid"] == revision_uid
                    ),
                    None,
                )
            if target is None:
                raise ContinuationPacketStoreError(f"unknown packet revision: {revision_uid}")
        if target["status"] == STATUS_INVALIDATED:
            if "status_history" in target:
                return _packet_status_record(
                    packet_id=canonical_packet_id,
                    packet_revision=int(target["packet_revision"]),
                    status=STATUS_INVALIDATED,
                    recorded_at_utc=str(target["status_history"][-1]["recorded_at_utc"]),
                    reason=str(target["status_history"][-1]["reason"]),
                )
            return dict(target)
        status_row = _packet_status_record(
            packet_id=canonical_packet_id,
            packet_revision=int(target["packet_revision"]),
            status=STATUS_INVALIDATED,
            recorded_at_utc=str(recorded_at_utc).strip(),
            reason=canonical_reason,
        )
        _append_jsonl_unlocked(paths["records_log"], status_row)
        return dict(status_row)


def latest_valid_packet(
    *,
    lookup_key: str,
    lookup_value: str,
    repo_root: Path | None = None,
) -> Dict[str, Any] | None:
    canonical_lookup_key = str(lookup_key or "").strip()
    if canonical_lookup_key not in LOOKUP_KEY_NAMES:
        raise ContinuationPacketStoreError(f"unsupported lookup key: {canonical_lookup_key or '<missing>'}")
    canonical_lookup_value = _validate_identifier(lookup_value, field_name=f"lookup_value[{canonical_lookup_key}]")
    paths = store_paths(repo_root=repo_root)
    with _store_lock(paths["lock_file"], shared=True):
        active_heads = _replay_state(_read_jsonl_strict(paths["records_log"]))
    matches = _matching_active_heads(active_heads, lookup_key=canonical_lookup_key, lookup_value=canonical_lookup_value)
    if not matches:
        return None
    if len(matches) > 1:
        raise AmbiguousLatestValidPacketError(
            f"ambiguous latest-valid lookup for {canonical_lookup_key}={canonical_lookup_value}"
        )
    return matches[0]
