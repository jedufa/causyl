"""Durable append/replay helpers for shared-resource claim state."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping

from experiments_shared import SharedResourceClass
from experiments_shared import SharedResourceContractError
from experiments_shared import device_resource_key
from experiments_shared import host_resource_key
from experiments_shared import job_family_resource_key
from experiments_shared import model_provider_resource_key
from experiments_shared import project_resource_key
from experiments_shared import workspace_resource_key

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover - non-POSIX fallback.
    _fcntl = None  # type: ignore[assignment]


STORAGE_ROOT_REL = Path("runtime/state/shared_resource_claim_ledger")
RECORD_SCHEMA_VERSION = "shared_resource_claim_record_v1"
RESOURCE_META_SCHEMA_VERSION = "shared_resource_partition_v1"
OCCUPANCY_SCHEMA_VERSION = "shared_resource_occupancy_snapshot_v1"
CLAIM_STATE_SCHEMA_VERSION = "shared_resource_claim_state_v1"
WAIT_REASON_SCHEMA_VERSION = "shared_resource_wait_reason_snapshot_v1"
RECOVERY_RECORD_SCHEMA_VERSION = "shared_resource_claim_recovery_record_v1"
RESOURCE_CONCURRENCY_SNAPSHOT_SCHEMA_VERSION = "shared_resource_concurrency_snapshot_v1"
CLAIMS_LOG_FILENAME = "claims.jsonl"
RECOVERY_LOG_FILENAME = "recovery.jsonl"
RESOURCE_META_FILENAME = "resource.json"
CLAIM_STATUSES: tuple[str, ...] = ("waiting", "acquired", "released", "cancelled", "rejected")
TERMINAL_STATUSES: frozenset[str] = frozenset({"released", "cancelled", "rejected"})
WAIT_REASON_REQUIRED_STATUSES: frozenset[str] = frozenset({"waiting", "rejected"})
RECOVERY_ACTIONS: tuple[str, ...] = ("released", "quarantined", "unresolved")

_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ledger_root(*, repo_root: Path | None = None) -> Path:
    base = Path(repo_root) if repo_root is not None else _repo_root()
    return (base / STORAGE_ROOT_REL).resolve()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def _root_lock(root: Path, *, shared: bool) -> Iterator[None]:
    lock_file = (root / ".claims-root.lock").resolve()
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


def _validate_path_safe_identifier(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty path-safe identifier")
    for char in normalized:
        if char.isalnum() or char in "._:-":
            continue
        raise ValueError(f"{field_name} must be a non-empty path-safe identifier")
    return normalized


def _validate_recorded_at_utc(value: Any, *, field_name: str = "recorded_at_utc") -> str:
    normalized = str(value or "").strip()
    if not normalized.endswith("Z"):
        raise ValueError(f"{field_name} must be an RFC3339 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an RFC3339 UTC timestamp ending in Z") from exc
    return normalized


def _validate_wait_reason_code(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        raise ValueError("wait_reason_code is required for waiting or rejected claims")
    for char in normalized:
        if char.isalnum() or char in "._-":
            continue
        raise ValueError("wait_reason_code must use lowercase letters, digits, dot, underscore, or hyphen")
    return normalized


def _normalize_detail(value: Any) -> str:
    return str(value or "").strip()


def _normalize_resource(resource: Mapping[str, Any]) -> Dict[str, str]:
    if not isinstance(resource, Mapping):
        raise ValueError("resource must be a mapping")
    resource_class = str(resource.get("resource_class") or "").strip()
    scope = str(resource.get("scope") or "").strip()
    locator = str(resource.get("locator") or "").strip()
    canonical_key = str(resource.get("canonical_key") or "").strip()
    if resource_class not in {item.value for item in SharedResourceClass}:
        raise ValueError("resource_class must use the canonical shared-resource vocabulary")
    try:
        if resource_class == SharedResourceClass.HOST.value:
            expected = host_resource_key(locator).as_dict()
        elif resource_class == SharedResourceClass.WORKSPACE.value:
            expected = workspace_resource_key(locator).as_dict()
        elif resource_class == SharedResourceClass.MODEL_PROVIDER.value:
            expected = model_provider_resource_key(locator).as_dict()
        elif resource_class == SharedResourceClass.DEVICE.value:
            expected = device_resource_key(scope, locator).as_dict()
        elif resource_class == SharedResourceClass.JOB_FAMILY.value:
            expected = job_family_resource_key(locator).as_dict()
        elif resource_class == SharedResourceClass.PROJECT.value:
            expected = project_resource_key(f"{scope}/{locator}").as_dict()
        else:  # pragma: no cover - protected by enum check above.
            raise ValueError("resource_class must use the canonical shared-resource vocabulary")
    except SharedResourceContractError as exc:
        raise ValueError(f"resource is invalid: {exc}") from exc
    if canonical_key != expected["canonical_key"]:
        raise ValueError("resource canonical_key does not match its canonical shared-resource key")
    if scope != expected["scope"] or locator != expected["locator"]:
        raise ValueError("resource fields must match canonical shared-resource normalization")
    return expected


def validate_claim_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("claim record must be a mapping")
    required = ("schema_version", "entry_uid", "claim_uid", "claimant_uid", "resource", "recorded_at_utc", "status")
    missing = [field_name for field_name in required if field_name not in record]
    if missing:
        raise ValueError(f"claim record missing required fields: {', '.join(missing)}")
    if str(record.get("schema_version") or "") != RECORD_SCHEMA_VERSION:
        raise ValueError(f"claim record schema_version must remain {RECORD_SCHEMA_VERSION}")
    status = str(record.get("status") or "").strip()
    if status not in CLAIM_STATUSES:
        raise ValueError("status must be one of: " + ", ".join(CLAIM_STATUSES))
    wait_reason_code = str(record.get("wait_reason_code") or "").strip()
    if status in WAIT_REASON_REQUIRED_STATUSES:
        wait_reason_code = _validate_wait_reason_code(wait_reason_code)
    elif wait_reason_code:
        wait_reason_code = _validate_wait_reason_code(wait_reason_code)
    else:
        wait_reason_code = ""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "entry_uid": _validate_path_safe_identifier(record.get("entry_uid"), field_name="entry_uid"),
        "claim_uid": _validate_path_safe_identifier(record.get("claim_uid"), field_name="claim_uid"),
        "claimant_uid": _validate_path_safe_identifier(record.get("claimant_uid"), field_name="claimant_uid"),
        "resource": _normalize_resource(record.get("resource") or {}),
        "recorded_at_utc": _validate_recorded_at_utc(record.get("recorded_at_utc")),
        "status": status,
        "wait_reason_code": wait_reason_code,
        "detail": _normalize_detail(record.get("detail")),
        "source": _normalize_detail(record.get("source")),
    }


def _resource_partition_name(resource: Mapping[str, Any]) -> str:
    canonical_key = str(resource["canonical_key"])
    digest = sha256(canonical_key.encode("utf-8")).hexdigest()[:16]
    return f"{resource['resource_class']}__{digest}"


def ledger_paths_for_resource(resource: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Path]:
    normalized_resource = _normalize_resource(resource)
    root = ledger_root(repo_root=repo_root)
    partition_dir = (root / "resources" / _resource_partition_name(normalized_resource)).resolve()
    return {
        "ledger_root": root,
        "partition_dir": partition_dir,
        "claims_log": (partition_dir / CLAIMS_LOG_FILENAME).resolve(),
        "recovery_log": (partition_dir / RECOVERY_LOG_FILENAME).resolve(),
        "resource_meta": (partition_dir / RESOURCE_META_FILENAME).resolve(),
    }


def _write_json_atomic(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(obj), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl_unlocked(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            raise ValueError(f"malformed JSONL entry in {path} at line {idx}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"expected JSON object in {path} at line {idx}")
        rows.append(obj)
    return rows


def _iter_claim_logs(root: Path) -> List[Path]:
    resources_root = (root / "resources").resolve()
    if not resources_root.exists():
        return []
    return sorted(path.resolve() for path in resources_root.glob(f"*/{CLAIMS_LOG_FILENAME}") if path.is_file())


def _read_all_claim_records_unlocked(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for claims_log in _iter_claim_logs(root):
        for raw_row in _read_jsonl_strict(claims_log):
            row = validate_claim_record(raw_row)
            expected_log = (root / "resources" / _resource_partition_name(row["resource"]) / CLAIMS_LOG_FILENAME).resolve()
            if claims_log != expected_log:
                raise ValueError("claim record resource partition does not match its canonical resource key")
            rows.append(row)
    return rows


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(record["recorded_at_utc"]), str(record["entry_uid"]), str(record["status"]))


def _recovery_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    return (str(record["recorded_at_utc"]), str(record["recovery_uid"]))


def validate_recovery_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("recovery record must be a mapping")
    required = (
        "schema_version",
        "recovery_uid",
        "claim_uid",
        "claimant_uid",
        "resource",
        "recorded_at_utc",
        "action",
        "reason_code",
    )
    missing = [field_name for field_name in required if field_name not in record]
    if missing:
        raise ValueError(f"recovery record missing required fields: {', '.join(missing)}")
    if str(record.get("schema_version") or "") != RECOVERY_RECORD_SCHEMA_VERSION:
        raise ValueError(
            f"recovery record schema_version must remain {RECOVERY_RECORD_SCHEMA_VERSION}"
        )
    action = str(record.get("action") or "").strip()
    if action not in RECOVERY_ACTIONS:
        raise ValueError("action must be one of: " + ", ".join(RECOVERY_ACTIONS))
    return {
        "schema_version": RECOVERY_RECORD_SCHEMA_VERSION,
        "recovery_uid": _validate_path_safe_identifier(record.get("recovery_uid"), field_name="recovery_uid"),
        "claim_uid": _validate_path_safe_identifier(record.get("claim_uid"), field_name="claim_uid"),
        "claimant_uid": _validate_path_safe_identifier(record.get("claimant_uid"), field_name="claimant_uid"),
        "resource": _normalize_resource(record.get("resource") or {}),
        "recorded_at_utc": _validate_recorded_at_utc(record.get("recorded_at_utc")),
        "action": action,
        "reason_code": _validate_wait_reason_code(record.get("reason_code")),
        "detail": _normalize_detail(record.get("detail")),
        "source": _normalize_detail(record.get("source")),
        "evidence": dict(record.get("evidence") or {}),
    }


def _project_claim_state(rows: List[Dict[str, Any]], *, claim_uid: str) -> Dict[str, Any]:
    if not rows:
        raise ValueError(f"claim_uid not found: {claim_uid}")
    rows = sorted(rows, key=_record_sort_key)
    seen_entry_uids: set[str] = set()
    resource_key = ""
    claimant_uid = ""
    latest: Dict[str, Any] | None = None
    for row in rows:
        entry_uid = str(row["entry_uid"])
        if entry_uid in seen_entry_uids:
            raise ValueError(f"duplicate entry_uid encountered during replay: {entry_uid}")
        seen_entry_uids.add(entry_uid)
        if resource_key and row["resource"]["canonical_key"] != resource_key:
            raise ValueError(f"claim_uid spans multiple resources: {claim_uid}")
        if claimant_uid and row["claimant_uid"] != claimant_uid:
            raise ValueError(f"claim_uid changed claimant_uid during replay: {claim_uid}")
        resource_key = str(row["resource"]["canonical_key"])
        claimant_uid = str(row["claimant_uid"])
        status = str(row["status"])
        if latest is None:
            if status == "released":
                raise ValueError(f"claim released before acquisition: {claim_uid}")
            latest = dict(row)
            continue
        previous_status = str(latest["status"])
        if previous_status in TERMINAL_STATUSES:
            raise ValueError(f"claim recorded additional events after terminal state: {claim_uid}")
        if status == "waiting":
            if previous_status == "acquired":
                raise ValueError(f"claim cannot transition from acquired back to waiting: {claim_uid}")
        elif status == "acquired":
            if previous_status == "acquired":
                raise ValueError(f"claim acquired more than once without release: {claim_uid}")
        elif status == "released":
            if previous_status != "acquired":
                raise ValueError(f"claim release requires prior acquired state: {claim_uid}")
        elif status == "rejected":
            if previous_status not in {"waiting"}:
                raise ValueError(f"claim rejection requires waiting state: {claim_uid}")
        elif status == "cancelled":
            if previous_status in TERMINAL_STATUSES:
                raise ValueError(f"claim cancelled after terminal state: {claim_uid}")
        latest = dict(row)
    assert latest is not None
    return {
        "schema_version": CLAIM_STATE_SCHEMA_VERSION,
        "claim_uid": claim_uid,
        "claimant_uid": claimant_uid,
        "resource": dict(latest["resource"]),
        "status": str(latest["status"]),
        "active": str(latest["status"]) == "acquired",
        "wait_reason_code": str(latest.get("wait_reason_code") or ""),
        "detail": str(latest.get("detail") or ""),
        "latest_source": str(latest.get("source") or ""),
        "latest_entry_uid": str(latest["entry_uid"]),
        "latest_recorded_at_utc": str(latest["recorded_at_utc"]),
        "lineage_length": len(rows),
    }


def append_claim_record(record: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = validate_claim_record(record)
    root = ledger_root(repo_root=repo_root)
    paths = ledger_paths_for_resource(normalized["resource"], repo_root=repo_root)
    line = json.dumps(dict(normalized), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with _root_lock(root, shared=False):
        existing_rows = _read_all_claim_records_unlocked(root)
        for row in existing_rows:
            if row["claim_uid"] == normalized["claim_uid"] and row["resource"]["canonical_key"] != normalized["resource"]["canonical_key"]:
                raise ValueError(f"claim_uid already exists for different resource: {normalized['claim_uid']}")
            if row["entry_uid"] == normalized["entry_uid"]:
                raise ValueError(f"entry_uid already exists: {normalized['entry_uid']}")
        if paths["resource_meta"].exists():
            payload = json.loads(paths["resource_meta"].read_text(encoding="utf-8"))
            if dict(payload.get("resource") or {}) != normalized["resource"]:
                raise ValueError("resource partition metadata does not match canonical resource")
        else:
            _write_json_atomic(
                paths["resource_meta"],
                {
                    "schema_version": RESOURCE_META_SCHEMA_VERSION,
                    "resource": dict(normalized["resource"]),
                },
            )
        _append_jsonl_unlocked(paths["claims_log"], line)
    return dict(normalized)


def append_claim_record_once(record: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = validate_claim_record(record)
    root = ledger_root(repo_root=repo_root)
    paths = ledger_paths_for_resource(normalized["resource"], repo_root=repo_root)
    line = json.dumps(dict(normalized), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with _root_lock(root, shared=False):
        existing_rows = _read_all_claim_records_unlocked(root)
        for row in existing_rows:
            if row["claim_uid"] == normalized["claim_uid"] and row["resource"]["canonical_key"] != normalized["resource"]["canonical_key"]:
                raise ValueError(f"claim_uid already exists for different resource: {normalized['claim_uid']}")
            if row["entry_uid"] != normalized["entry_uid"]:
                continue
            if row != normalized:
                raise ValueError(f"entry_uid already exists with different content: {normalized['entry_uid']}")
            return {"record": dict(row), "deduped": True}
        if paths["resource_meta"].exists():
            payload = json.loads(paths["resource_meta"].read_text(encoding="utf-8"))
            if dict(payload.get("resource") or {}) != normalized["resource"]:
                raise ValueError("resource partition metadata does not match canonical resource")
        else:
            _write_json_atomic(
                paths["resource_meta"],
                {
                    "schema_version": RESOURCE_META_SCHEMA_VERSION,
                    "resource": dict(normalized["resource"]),
                },
            )
        _append_jsonl_unlocked(paths["claims_log"], line)
    return {"record": dict(normalized), "deduped": False}


def append_recovery_record_once(record: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = validate_recovery_record(record)
    root = ledger_root(repo_root=repo_root)
    paths = ledger_paths_for_resource(normalized["resource"], repo_root=repo_root)
    line = json.dumps(dict(normalized), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with _root_lock(root, shared=False):
        rows = _read_jsonl_strict(paths["recovery_log"])
        for row in rows:
            existing = validate_recovery_record(row)
            if existing["recovery_uid"] != normalized["recovery_uid"]:
                continue
            if existing != normalized:
                raise ValueError(
                    f"recovery_uid already exists with different content: {normalized['recovery_uid']}"
                )
            return {"record": dict(existing), "deduped": True}
        _append_jsonl_unlocked(paths["recovery_log"], line)
    return {"record": dict(normalized), "deduped": False}


def read_claim_records_for_resource(resource: Mapping[str, Any], *, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    normalized_resource = _normalize_resource(resource)
    root = ledger_root(repo_root=repo_root)
    paths = ledger_paths_for_resource(normalized_resource, repo_root=repo_root)
    with _root_lock(root, shared=True):
        rows = _read_jsonl_strict(paths["claims_log"])
    out: List[Dict[str, Any]] = []
    for row in rows:
        record = validate_claim_record(row)
        if record["resource"]["canonical_key"] != normalized_resource["canonical_key"]:
            raise ValueError("claim record resource does not match its partition")
        out.append(record)
    return sorted(out, key=_record_sort_key)


def read_claim_lineage(claim_uid: str, *, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    canonical_claim_uid = _validate_path_safe_identifier(claim_uid, field_name="claim_uid")
    root = ledger_root(repo_root=repo_root)
    with _root_lock(root, shared=True):
        rows = [row for row in _read_all_claim_records_unlocked(root) if row["claim_uid"] == canonical_claim_uid]
    if not rows:
        return []
    _project_claim_state(rows, claim_uid=canonical_claim_uid)
    return sorted(rows, key=_record_sort_key)


def current_claim_state(claim_uid: str, *, repo_root: Path | None = None) -> Dict[str, Any] | None:
    rows = read_claim_lineage(claim_uid, repo_root=repo_root)
    if not rows:
        return None
    return _project_claim_state(rows, claim_uid=_validate_path_safe_identifier(claim_uid, field_name="claim_uid"))


def list_claim_states_for_resource(resource: Mapping[str, Any], *, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    normalized_resource = _normalize_resource(resource)
    rows = read_claim_records_for_resource(normalized_resource, repo_root=repo_root)
    claims: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        claims.setdefault(str(row["claim_uid"]), []).append(row)
    return [
        _project_claim_state(items, claim_uid=claim_uid)
        for claim_uid, items in sorted(claims.items())
    ]


def list_claim_states_for_resource_lenient(
    resource: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    normalized_resource = _normalize_resource(resource)
    rows = read_claim_records_for_resource(normalized_resource, repo_root=repo_root)
    claims: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        claims.setdefault(str(row["claim_uid"]), []).append(row)
    states: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for claim_uid, items in sorted(claims.items()):
        try:
            states.append(_project_claim_state(items, claim_uid=claim_uid))
        except Exception as exc:
            errors.append(
                {
                    "claim_uid": str(claim_uid),
                    "error": str(exc),
                    "lineage_length": len(items),
                }
            )
    return {
        "schema_version": CLAIM_STATE_SCHEMA_VERSION,
        "resource": normalized_resource,
        "states": states,
        "errors": errors,
    }


def replay_resource_occupancy(
    resource: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    skip_malformed: bool = False,
) -> Dict[str, Any]:
    normalized_resource = _normalize_resource(resource)
    rows = read_claim_records_for_resource(normalized_resource, repo_root=repo_root)
    claims: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        claims.setdefault(str(row["claim_uid"]), []).append(row)
    states: List[Dict[str, Any]] = []
    malformed_claims: List[Dict[str, Any]] = []
    for claim_uid, items in sorted(claims.items()):
        try:
            states.append(_project_claim_state(items, claim_uid=claim_uid))
        except Exception:
            if not skip_malformed:
                raise
            malformed_claims.append(
                {
                    "claim_uid": str(claim_uid),
                    "lineage_length": len(items),
                }
            )
    active_claims = [
        {
            "claim_uid": state["claim_uid"],
            "claimant_uid": state["claimant_uid"],
            "latest_entry_uid": state["latest_entry_uid"],
            "latest_recorded_at_utc": state["latest_recorded_at_utc"],
        }
        for state in states
        if state["active"]
    ]
    waiting_claims = [
        {
            "claim_uid": state["claim_uid"],
            "claimant_uid": state["claimant_uid"],
            "wait_reason_code": state["wait_reason_code"],
            "detail": state["detail"],
            "latest_entry_uid": state["latest_entry_uid"],
            "latest_recorded_at_utc": state["latest_recorded_at_utc"],
        }
        for state in states
        if state["status"] == "waiting"
    ]
    return {
        "schema_version": OCCUPANCY_SCHEMA_VERSION,
        "resource": normalized_resource,
        "active_claim_count": len(active_claims),
        "active_claims": active_claims,
        "waiting_claim_count": len(waiting_claims),
        "waiting_claims": waiting_claims,
        "malformed_claim_count": len(malformed_claims),
        "malformed_claims": malformed_claims,
    }


def wait_reasons_for_claimant(claimant_uid: str, *, repo_root: Path | None = None) -> Dict[str, Any]:
    canonical_claimant_uid = _validate_path_safe_identifier(claimant_uid, field_name="claimant_uid")
    root = ledger_root(repo_root=repo_root)
    with _root_lock(root, shared=True):
        all_rows = _read_all_claim_records_unlocked(root)
    claims: Dict[str, List[Dict[str, Any]]] = {}
    for row in all_rows:
        if row["claimant_uid"] != canonical_claimant_uid:
            continue
        claims.setdefault(str(row["claim_uid"]), []).append(row)
    wait_reasons: List[Dict[str, Any]] = []
    for claim_uid, rows in sorted(claims.items()):
        state = _project_claim_state(rows, claim_uid=claim_uid)
        if state["status"] != "waiting":
            continue
        wait_reasons.append(
            {
                "claim_uid": state["claim_uid"],
                "resource": dict(state["resource"]),
                "wait_reason_code": state["wait_reason_code"],
                "detail": state["detail"],
                "latest_entry_uid": state["latest_entry_uid"],
                "latest_recorded_at_utc": state["latest_recorded_at_utc"],
            }
        )
    return {
        "schema_version": WAIT_REASON_SCHEMA_VERSION,
        "claimant_uid": canonical_claimant_uid,
        "wait_reasons": wait_reasons,
    }


def read_recovery_records_for_claim(claim_uid: str, *, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    canonical_claim_uid = _validate_path_safe_identifier(claim_uid, field_name="claim_uid")
    state = current_claim_state(canonical_claim_uid, repo_root=repo_root)
    if state is None:
        return []
    root = ledger_root(repo_root=repo_root)
    paths = ledger_paths_for_resource(state["resource"], repo_root=repo_root)
    with _root_lock(root, shared=True):
        rows = _read_jsonl_strict(paths["recovery_log"])
    out: List[Dict[str, Any]] = []
    for row in rows:
        record = validate_recovery_record(row)
        if record["claim_uid"] == canonical_claim_uid:
            out.append(record)
    return sorted(out, key=_recovery_sort_key)


def _limit_projection_for_resource(resource: Mapping[str, Any], runtime_policy: Mapping[str, Any]) -> Dict[str, Any]:
    resource_class = str(resource.get("resource_class") or "").strip().lower()
    scope = str(resource.get("scope") or "").strip().lower()
    locator = str(resource.get("locator") or "").strip()
    resource_caps = runtime_policy.get("resource_class_caps") if isinstance(runtime_policy.get("resource_class_caps"), Mapping) else {}
    if resource_class == "job_family" and scope == "scheduler":
        raw = resource_caps.get(locator)
        if isinstance(raw, int) and raw > 0:
            return {
                "known": True,
                "value": int(raw),
                "source": "runtime_policy",
                "source_detail": "scheduler.resource_class_caps",
            }
    return {
        "known": False,
        "value": None,
        "source": "unknown",
        "source_detail": "",
    }


def _limit_source_label(limit: Mapping[str, Any]) -> str:
    source = str(limit.get("source") or "").strip().lower()
    if source == "runtime_policy":
        return "runtime policy"
    if source == "experiment_contract":
        return "experiment contract"
    if source == "recovery":
        return "recovery handling"
    return "unknown source"


def _resource_operator_summary(
    *,
    resource: Mapping[str, Any],
    active_claim_count: int,
    waiting_claim_count: int,
    waiting_claims: List[Dict[str, Any]],
    limit: Mapping[str, Any],
    saturation_state: str,
    recovery_state: str,
    recovery: Mapping[str, Any],
) -> Dict[str, Any]:
    canonical_key = str(resource.get("canonical_key") or "").strip()
    wait_reason_counts: Dict[str, int] = {}
    for row in waiting_claims:
        code = str(row.get("wait_reason_code") or "").strip() or "unspecified"
        wait_reason_counts[code] = int(wait_reason_counts.get(code) or 0) + 1
    dominant_wait_reason_code = ""
    dominant_wait_reason_count = 0
    if wait_reason_counts:
        dominant_wait_reason_code, dominant_wait_reason_count = min(
            wait_reason_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    wait_reason_bits: List[str] = []
    if dominant_wait_reason_code:
        wait_reason_bits.append(f"{dominant_wait_reason_count} waiting for {dominant_wait_reason_code}")
    extra_wait_reason_count = max(0, len(wait_reason_counts) - (1 if dominant_wait_reason_code else 0))
    if extra_wait_reason_count > 0:
        wait_reason_bits.append(f"{extra_wait_reason_count} additional wait reason(s)")
    if bool(limit.get("known")):
        limit_text = f"limit {int(limit.get('value') or 0)} from {_limit_source_label(limit)}"
    else:
        limit_text = "limit unknown"
    recovery_counts = dict(recovery.get("counts_by_action") or {})
    latest_recovery_utc = str(recovery.get("latest_recorded_at_utc") or "").strip()
    recovery_text = ""
    if recovery_state == "degraded":
        recovery_text = "recovery still unresolved"
    elif recovery_state == "recovered":
        action_bits = []
        for action in RECOVERY_ACTIONS:
            count = int(recovery_counts.get(action) or 0)
            if count > 0:
                action_bits.append(f"{count} {action}")
        action_summary = ", ".join(action_bits) if action_bits else "recent intervention"
        if latest_recovery_utc:
            recovery_text = f"recovery recorded {action_summary} at {latest_recovery_utc}"
        else:
            recovery_text = f"recovery recorded {action_summary}"
    if saturation_state == "saturated" or waiting_claim_count > 0:
        state = "contended"
        headline = f"{canonical_key} is contended"
    elif saturation_state == "unknown":
        state = "unknown"
        headline = f"{canonical_key} limit is unknown"
    else:
        state = "available"
        headline = f"{canonical_key} has free capacity"
    message_bits = [f"{active_claim_count} active", f"{waiting_claim_count} waiting"]
    if wait_reason_bits:
        message_bits.append("; ".join(wait_reason_bits))
    message_bits.append(limit_text)
    if recovery_text:
        message_bits.append(recovery_text)
    return {
        "canonical_key": canonical_key,
        "state": state,
        "headline": headline,
        "message": ". ".join(bit for bit in message_bits if bit).strip(),
        "wait_reason_counts": wait_reason_counts,
        "dominant_wait_reason_code": dominant_wait_reason_code,
        "recovery_state": recovery_state,
    }


def _resource_concurrency_rows(
    *,
    root: Path,
    runtime_policy: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    resources: List[Dict[str, Any]] = []
    for claims_log in _iter_claim_logs(root):
        raw_claim_rows = _read_jsonl_strict(claims_log)
        if not raw_claim_rows:
            continue
        claim_rows = [validate_claim_record(row) for row in raw_claim_rows]
        claims: Dict[str, List[Dict[str, Any]]] = {}
        for row in claim_rows:
            claims.setdefault(str(row["claim_uid"]), []).append(row)
        states = [_project_claim_state(items, claim_uid=claim_uid) for claim_uid, items in sorted(claims.items())]
        resource = dict(claim_rows[0]["resource"])
        limit = _limit_projection_for_resource(resource, runtime_policy)
        active_claims = [
            {
                "claim_uid": state["claim_uid"],
                "claimant_uid": state["claimant_uid"],
                "latest_entry_uid": state["latest_entry_uid"],
                "latest_recorded_at_utc": state["latest_recorded_at_utc"],
            }
            for state in states
            if state["active"]
        ]
        waiting_claims = [
            {
                "claim_uid": state["claim_uid"],
                "claimant_uid": state["claimant_uid"],
                "wait_reason_code": state["wait_reason_code"],
                "detail": state["detail"],
                "latest_entry_uid": state["latest_entry_uid"],
                "latest_recorded_at_utc": state["latest_recorded_at_utc"],
            }
            for state in states
            if state["status"] == "waiting"
        ]
        recovery_log = claims_log.with_name(RECOVERY_LOG_FILENAME)
        recovery_records = [
            validate_recovery_record(row)
            for row in _read_jsonl_strict(recovery_log)
        ] if recovery_log.exists() else []
        recovery_records.sort(key=_recovery_sort_key)
        recent_recovery = [
            {
                "recovery_uid": row["recovery_uid"],
                "claim_uid": row["claim_uid"],
                "claimant_uid": row["claimant_uid"],
                "action": row["action"],
                "detail": row["detail"],
                "recorded_at_utc": row["recorded_at_utc"],
                "source": row["source"],
            }
            for row in recovery_records[-5:]
        ]
        recovery_counts = {
            action: sum(1 for row in recovery_records if row["action"] == action)
            for action in RECOVERY_ACTIONS
        }
        if recovery_counts.get("unresolved"):
            recovery_state = "degraded"
        elif recovery_records:
            recovery_state = "recovered"
        else:
            recovery_state = "none"
        active_claim_count = len(active_claims)
        waiting_claim_count = len(waiting_claims)
        if bool(limit["known"]):
            saturation_state = "saturated" if active_claim_count >= int(limit["value"]) else "available"
        else:
            saturation_state = "unknown"
        latest_claim_utc = max((str(state["latest_recorded_at_utc"]) for state in states), default="")
        latest_recovery_utc = str(recent_recovery[-1]["recorded_at_utc"]) if recent_recovery else ""
        resources.append(
            {
                "resource": resource,
                "active_claim_count": active_claim_count,
                "active_claims": active_claims,
                "waiting_claim_count": waiting_claim_count,
                "waiting_claims": waiting_claims,
                "latest_claim_utc": latest_claim_utc,
                "limit": limit,
                "saturation_state": saturation_state,
                "recovery_state": recovery_state,
                "recovery": {
                    "record_count": len(recovery_records),
                    "counts_by_action": recovery_counts,
                    "latest_recorded_at_utc": latest_recovery_utc,
                    "recent_records": recent_recovery,
                },
                "operator_summary": _resource_operator_summary(
                    resource=resource,
                    active_claim_count=active_claim_count,
                    waiting_claim_count=waiting_claim_count,
                    waiting_claims=waiting_claims,
                    limit=limit,
                    saturation_state=saturation_state,
                    recovery_state=recovery_state,
                    recovery={
                        "counts_by_action": recovery_counts,
                        "latest_recorded_at_utc": latest_recovery_utc,
                    },
                ),
            }
        )
    resources.sort(key=lambda row: str(((row.get("resource") or {}).get("canonical_key") or "")))
    return resources


def resource_concurrency_snapshot(
    *,
    repo_root: Path | None = None,
    runtime_policy: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    root = ledger_root(repo_root=repo_root)
    policy = dict(runtime_policy or {})
    try:
        with _root_lock(root, shared=True):
            resources = _resource_concurrency_rows(root=root, runtime_policy=policy)
    except Exception as exc:
        return {
            "schema_version": RESOURCE_CONCURRENCY_SNAPSHOT_SCHEMA_VERSION,
            "status": "degraded",
            "summary": "Durable shared-resource concurrency truth could not be projected.",
            "resource_count": 0,
            "active_claim_count": 0,
            "waiting_claim_count": 0,
            "saturated_resource_count": 0,
            "recovered_resource_count": 0,
            "degraded_reasons": [str(exc)],
            "runtime_policy": {
                "policy_valid": bool(policy.get("policy_valid", True)),
                "policy_source": str(policy.get("policy_source") or ""),
            },
            "highlights": [],
            "resources": [],
        }

    active_claim_count = sum(int(row.get("active_claim_count") or 0) for row in resources)
    waiting_claim_count = sum(int(row.get("waiting_claim_count") or 0) for row in resources)
    saturated_resource_count = sum(1 for row in resources if row.get("saturation_state") == "saturated")
    recovered_resource_count = sum(1 for row in resources if row.get("recovery_state") == "recovered")
    highlights = [
        dict(row.get("operator_summary") or {})
        for row in resources
        if str((row.get("operator_summary") or {}).get("state") or "") in {"contended", "unknown"}
    ][:5]
    if resources:
        status = "ok"
        summary = (
            f"{len(resources)} shared resources tracked; "
            f"{active_claim_count} active claims, {waiting_claim_count} waiting claims, "
            f"{saturated_resource_count} saturated resources."
        )
    else:
        status = "unknown"
        summary = "No durable shared-resource claim records are available yet."
    return {
        "schema_version": RESOURCE_CONCURRENCY_SNAPSHOT_SCHEMA_VERSION,
        "status": status,
        "summary": summary,
        "resource_count": len(resources),
        "active_claim_count": active_claim_count,
        "waiting_claim_count": waiting_claim_count,
        "saturated_resource_count": saturated_resource_count,
        "recovered_resource_count": recovered_resource_count,
        "degraded_reasons": [],
        "runtime_policy": {
            "policy_valid": bool(policy.get("policy_valid", True)),
            "policy_source": str(policy.get("policy_source") or ""),
        },
        "highlights": highlights,
        "resources": resources,
    }
