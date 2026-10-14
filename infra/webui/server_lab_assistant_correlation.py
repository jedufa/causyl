"""F4A9 durable turn/request/job/result correlation and reattach truth."""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.webui import server_lab_assistant_delegation_policy as _policy


CORRELATION_RECORD_SCHEMA_VERSION = "lab_assistant_correlation_record_v1"
CORRELATION_RESULT_SCHEMA_VERSION = "lab_assistant_correlation_result_v1"
CORRELATION_EVENT_SCHEMA_VERSION = "lab_assistant_correlation_event_v1"
CORRELATION_SNAPSHOT_SCHEMA_VERSION = "lab_assistant_correlation_snapshot_v1"
_LOCK = threading.RLock()


class CorrelationError(RuntimeError):
    """Raised when durable correlation truth cannot be updated safely."""


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[_dt.datetime] = None) -> str:
    when = dt or _utc_now()
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_name(value: Any) -> str:
    text = _clean_text(value)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip("-")
    return safe or "x"


def correlation_root(repo_root: Path) -> Path:
    path = (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "correlation").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def records_dir(repo_root: Path) -> Path:
    path = (correlation_root(repo_root) / "records").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def results_dir(repo_root: Path) -> Path:
    path = (correlation_root(repo_root) / "results").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_path(repo_root: Path, correlation_id: str) -> Path:
    return (records_dir(repo_root) / f"{_safe_name(correlation_id)}.json").resolve()


def result_path(repo_root: Path, result_id: str) -> Path:
    return (results_dir(repo_root) / f"{_safe_name(result_id)}.json").resolve()


def snapshot_path(repo_root: Path) -> Path:
    return (correlation_root(repo_root) / "correlation_snapshot.json").resolve()


def events_path(repo_root: Path) -> Path:
    return (correlation_root(repo_root) / "correlation_events.jsonl").resolve()


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


def _clean_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for raw in values:
        text = _clean_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _event(repo_root: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        events_path(repo_root),
        {
            "schema_version": CORRELATION_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": _clean_text(event_type),
            "utc": _utc_iso(),
            "payload": dict(payload or {}),
        },
    )


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {
        "schema_version": CORRELATION_RECORD_SCHEMA_VERSION,
        "correlation_id": _clean_text(record.get("correlation_id")),
        "launch_turn_id": _clean_text(record.get("launch_turn_id")),
        "request_id": _clean_text(record.get("request_id")),
        "surface": _clean_text(record.get("surface")).lower(),
        "summary": _clean_text(record.get("summary")),
        "requested_action": _clean_text(record.get("requested_action")),
        "owner_ref": dict(record.get("owner_ref") or {}),
        "bound_utc": _clean_text(record.get("bound_utc") or _utc_iso()),
        "updated_utc": _clean_text(record.get("updated_utc") or _utc_iso()),
        "detached_job_id": _clean_text(record.get("detached_job_id")),
        "job_surface": _clean_text(record.get("job_surface")).lower(),
        "result_correlation_basis": _clean_text(record.get("result_correlation_basis") or "request_id"),
        "launch_cause": _clean_text(record.get("launch_cause")),
        "parent_request_id": _clean_text(record.get("parent_request_id")),
        "prior_result_id": _clean_text(record.get("prior_result_id")),
        "result_ids": _clean_list(record.get("result_ids")),
        "pending_result_ids": _clean_list(record.get("pending_result_ids")),
        "delivered_result_ids": _clean_list(record.get("delivered_result_ids")),
        "ambiguous_result_ids": _clean_list(record.get("ambiguous_result_ids")),
        "unresolved_result_ids": _clean_list(record.get("unresolved_result_ids")),
        "latest_result_id": _clean_text(record.get("latest_result_id")),
        "latest_delivery_status": _clean_text(record.get("latest_delivery_status")),
    }
    if not normalized["correlation_id"]:
        raise CorrelationError("correlation_id is required")
    if not normalized["launch_turn_id"]:
        raise CorrelationError(f"correlation record {normalized['correlation_id']} requires launch_turn_id")
    if not normalized["request_id"]:
        raise CorrelationError(f"correlation record {normalized['correlation_id']} requires request_id")
    return normalized


def _normalize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    envelope_input = dict(result or {})
    envelope_input["schema_version"] = _policy.DELEGATION_RESULT_SCHEMA_VERSION
    envelope = _policy.validate_typed_delegation_result(envelope_input)
    normalized = {
        **dict(envelope),
        "schema_version": CORRELATION_RESULT_SCHEMA_VERSION,
        "delivery_status": _clean_text(result.get("delivery_status") or "pending_reattach"),
        "delivery_utc": _clean_text(result.get("delivery_utc")),
        "delivery_turn_id": _clean_text(result.get("delivery_turn_id")),
        "delivery_writer": _clean_text(result.get("delivery_writer")),
        "candidate_correlation_ids": _clean_list(result.get("candidate_correlation_ids")),
        "attached_correlation_id": _clean_text(result.get("attached_correlation_id")),
        "unresolved_reason": _clean_text(result.get("unresolved_reason")),
    }
    if normalized["delivery_status"] not in {"pending_reattach", "delivered", "ambiguous", "unresolved"}:
        raise CorrelationError(f"invalid delivery_status: {normalized['delivery_status'] or '<missing>'}")
    return normalized


def _store_record(repo_root: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_record(record)
    _write_json_atomic(record_path(repo_root, normalized["correlation_id"]), normalized)
    return normalized


def _store_result(repo_root: Path, result: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_result(result)
    _write_json_atomic(result_path(repo_root, normalized["result_id"]), normalized)
    return normalized


def _records(repo_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(records_dir(repo_root).glob("*.json"), key=lambda row: row.name):
        record = _read_json(path)
        if not record:
            continue
        try:
            rows.append(_normalize_record(record))
        except Exception:
            continue
    rows.sort(key=lambda row: (_clean_text(row.get("bound_utc")), row["correlation_id"]))
    return rows


def _results(repo_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in sorted(results_dir(repo_root).glob("*.json"), key=lambda row: row.name):
        result = _read_json(path)
        if not result:
            continue
        try:
            rows.append(_normalize_result(result))
        except Exception:
            continue
    rows.sort(key=lambda row: (_clean_text(row.get("created_utc")), row["result_id"]))
    return rows


def _write_snapshot(repo_root: Path, *, writer: str) -> Dict[str, Any]:
    records = _records(repo_root)
    results = _results(repo_root)
    delivery_counts: Dict[str, int] = {"pending_reattach": 0, "delivered": 0, "ambiguous": 0, "unresolved": 0}
    for row in results:
        state = _clean_text(row.get("delivery_status"))
        delivery_counts[state] = delivery_counts.get(state, 0) + 1
    payload = {
        "schema_version": CORRELATION_SNAPSHOT_SCHEMA_VERSION,
        "writer": _clean_text(writer),
        "generated_utc": _utc_iso(),
        "record_count": len(records),
        "result_count": len(results),
        "delivery_counts": delivery_counts,
        "records": [
            {
                "correlation_id": row["correlation_id"],
                "launch_turn_id": row["launch_turn_id"],
                "request_id": row["request_id"],
                "detached_job_id": row["detached_job_id"],
                "latest_result_id": row["latest_result_id"],
                "latest_delivery_status": row["latest_delivery_status"],
                "pending_result_ids": list(row["pending_result_ids"]),
                "ambiguous_result_ids": list(row["ambiguous_result_ids"]),
                "unresolved_result_ids": list(row["unresolved_result_ids"]),
                "path": str(record_path(repo_root, row["correlation_id"])),
            }
            for row in records
        ],
        "results": [
            {
                "result_id": row["result_id"],
                "request_id": row["request_id"],
                "launch_turn_id": row["launch_turn_id"],
                "detached_job_id": row["detached_job_id"],
                "delivery_status": row["delivery_status"],
                "attached_correlation_id": row["attached_correlation_id"],
                "candidate_correlation_ids": list(row["candidate_correlation_ids"]),
                "path": str(result_path(repo_root, row["result_id"])),
            }
            for row in results
        ],
        "events_path": str(events_path(repo_root)),
    }
    _write_json_atomic(snapshot_path(repo_root), payload)
    return payload


def _matching_records(repo_root: Path, result: Dict[str, Any]) -> List[Dict[str, Any]]:
    basis = _clean_text(result.get("result_correlation_basis")).lower()
    wanted = ""
    if basis == "request_id":
        wanted = _clean_text(result.get("request_id"))
        return [row for row in _records(repo_root) if row["request_id"] == wanted]
    if basis == "correlation_id":
        wanted = _clean_text(result.get("correlation_id"))
        return [row for row in _records(repo_root) if row["correlation_id"] == wanted]
    if basis == "detached_job_id":
        wanted = _clean_text(result.get("detached_job_id"))
        return [row for row in _records(repo_root) if row["detached_job_id"] == wanted]
    if basis == "launch_turn_id":
        wanted = _clean_text(result.get("launch_turn_id"))
        return [row for row in _records(repo_root) if row["launch_turn_id"] == wanted]
    return []


def record_delegation_binding(repo_root: Path, *, delegation_ref: Dict[str, Any], writer: str) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    ref = dict(delegation_ref or {})
    with _LOCK:
        existing = _read_json(record_path(repo_root, _clean_text(ref.get("correlation_id"))))
        updated = {
            **dict(existing or {}),
            "schema_version": CORRELATION_RECORD_SCHEMA_VERSION,
            "correlation_id": _clean_text(ref.get("correlation_id")),
            "launch_turn_id": _clean_text(ref.get("launch_turn_id")),
            "request_id": _clean_text(ref.get("delegation_request_id")),
            "surface": _clean_text(ref.get("surface")),
            "summary": _clean_text(ref.get("summary")),
            "requested_action": _clean_text(ref.get("requested_action")),
            "owner_ref": dict(ref.get("owner_ref") or {}),
            "bound_utc": _clean_text(ref.get("bound_utc") or (existing or {}).get("bound_utc") or _utc_iso()),
            "updated_utc": _utc_iso(),
            "detached_job_id": _clean_text(ref.get("detached_job_id") or (existing or {}).get("detached_job_id")),
            "job_surface": _clean_text(ref.get("subsystem") if _clean_text(ref.get("surface")) == "mailbox" else ""),
            "result_correlation_basis": _clean_text(ref.get("result_correlation_basis") or "request_id"),
            "launch_cause": _clean_text(ref.get("launch_cause")),
            "parent_request_id": _clean_text(ref.get("parent_request_id")),
            "prior_result_id": _clean_text(ref.get("prior_result_id")),
            "result_ids": list((existing or {}).get("result_ids") or []),
            "pending_result_ids": list((existing or {}).get("pending_result_ids") or []),
            "delivered_result_ids": list((existing or {}).get("delivered_result_ids") or []),
            "ambiguous_result_ids": list((existing or {}).get("ambiguous_result_ids") or []),
            "unresolved_result_ids": list((existing or {}).get("unresolved_result_ids") or []),
            "latest_result_id": _clean_text((existing or {}).get("latest_result_id")),
            "latest_delivery_status": _clean_text((existing or {}).get("latest_delivery_status")),
        }
        stored = _store_record(repo_root, updated)
        snapshot = _write_snapshot(repo_root, writer=writer)
        _event(
            repo_root,
            event_type="binding_recorded",
            payload={"writer": _clean_text(writer), "correlation_id": stored["correlation_id"], "request_id": stored["request_id"]},
        )
        return {"ok": True, "correlation_record": stored, "correlation_snapshot": snapshot}


def record_job_handoff(repo_root: Path, *, request_record: Dict[str, Any], writer: str) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    mailbox_record = dict(request_record or {})
    request = dict(mailbox_record.get("request") or {})
    handoff = dict(mailbox_record.get("handoff") or {})
    correlation_id = _clean_text(request.get("correlation_id"))
    with _LOCK:
        existing = _read_json(record_path(repo_root, correlation_id))
        if not existing:
            existing = {
                "correlation_id": correlation_id,
                "launch_turn_id": _clean_text(request.get("launch_turn_id")),
                "request_id": _clean_text(mailbox_record.get("request_id") or request.get("request_id")),
                "surface": _clean_text(request.get("surface") or "mailbox"),
                "summary": _clean_text(request.get("summary")),
                "requested_action": _clean_text(request.get("requested_action")),
                "owner_ref": dict(request.get("owner_ref") or {}),
                "bound_utc": _clean_text(mailbox_record.get("created_utc") or request.get("created_utc") or _utc_iso()),
                "result_correlation_basis": _clean_text(request.get("result_correlation_basis") or "request_id"),
            }
        existing["updated_utc"] = _utc_iso()
        existing["detached_job_id"] = _clean_text(handoff.get("detached_job_id"))
        existing["job_surface"] = _clean_text(handoff.get("job_surface"))
        stored = _store_record(repo_root, existing)
        snapshot = _write_snapshot(repo_root, writer=writer)
        _event(
            repo_root,
            event_type="handoff_recorded",
            payload={
                "writer": _clean_text(writer),
                "correlation_id": stored["correlation_id"],
                "request_id": stored["request_id"],
                "detached_job_id": stored["detached_job_id"],
            },
        )
        return {"ok": True, "correlation_record": stored, "correlation_snapshot": snapshot}


def record_detached_result(repo_root: Path, *, result: Dict[str, Any], writer: str) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    normalized_result = _policy.validate_typed_delegation_result(result)
    with _LOCK:
        candidates = _matching_records(repo_root, normalized_result)
        stored_result = dict(normalized_result)
        stored_result["schema_version"] = CORRELATION_RESULT_SCHEMA_VERSION
        stored_result["delivery_writer"] = _clean_text(writer)
        if len(candidates) == 1:
            record = dict(candidates[0])
            stored_result["delivery_status"] = "pending_reattach"
            stored_result["attached_correlation_id"] = record["correlation_id"]
            stored_result["candidate_correlation_ids"] = [record["correlation_id"]]
            stored_result["unresolved_reason"] = ""
            result_id = _clean_text(stored_result.get("result_id"))
            result_ids = _clean_list(record.get("result_ids"))
            pending = _clean_list(record.get("pending_result_ids"))
            if result_id not in result_ids:
                result_ids.append(result_id)
            if result_id not in pending:
                pending.append(result_id)
            record["result_ids"] = result_ids
            record["pending_result_ids"] = pending
            record["latest_result_id"] = result_id
            record["latest_delivery_status"] = "pending_reattach"
            record["updated_utc"] = _utc_iso()
            _store_record(repo_root, record)
        elif len(candidates) > 1:
            stored_result["delivery_status"] = "ambiguous"
            stored_result["attached_correlation_id"] = ""
            stored_result["candidate_correlation_ids"] = [row["correlation_id"] for row in candidates]
            stored_result["unresolved_reason"] = "multiple_correlation_records_matched"
            for candidate in candidates:
                record = dict(candidate)
                ambiguous = _clean_list(record.get("ambiguous_result_ids"))
                if stored_result["result_id"] not in ambiguous:
                    ambiguous.append(stored_result["result_id"])
                record["ambiguous_result_ids"] = ambiguous
                record["latest_result_id"] = stored_result["result_id"]
                record["latest_delivery_status"] = "ambiguous"
                record["updated_utc"] = _utc_iso()
                _store_record(repo_root, record)
        else:
            stored_result["delivery_status"] = "unresolved"
            stored_result["attached_correlation_id"] = ""
            stored_result["candidate_correlation_ids"] = []
            stored_result["unresolved_reason"] = f"no_correlation_record_for_{_clean_text(normalized_result.get('result_correlation_basis'))}"
        persisted = _store_result(repo_root, stored_result)
        snapshot = _write_snapshot(repo_root, writer=writer)
        _event(
            repo_root,
            event_type="result_recorded",
            payload={
                "writer": _clean_text(writer),
                "result_id": persisted["result_id"],
                "delivery_status": persisted["delivery_status"],
                "candidate_correlation_ids": list(persisted["candidate_correlation_ids"]),
            },
        )
        return {
            "ok": True,
            "result_record": persisted,
            "matched_correlation_ids": list(persisted["candidate_correlation_ids"]),
            "correlation_snapshot": snapshot,
        }


def reattach_results_for_turn(
    repo_root: Path,
    *,
    launch_turn_id: str,
    current_turn_id: str = "",
    writer: str = "",
    mark_delivered: bool = False,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target_turn_id = _clean_text(launch_turn_id)
    now = _utc_iso()
    delivered_ids: List[str] = []
    with _LOCK:
        exact: List[Dict[str, Any]] = []
        ambiguous: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []
        for row in _results(repo_root):
            if _clean_text(row.get("launch_turn_id")) != target_turn_id:
                continue
            status = _clean_text(row.get("delivery_status"))
            if status in {"pending_reattach", "delivered"}:
                if mark_delivered and status == "pending_reattach":
                    row["delivery_status"] = "delivered"
                    row["delivery_utc"] = now
                    row["delivery_turn_id"] = _clean_text(current_turn_id)
                    row["delivery_writer"] = _clean_text(writer)
                    _store_result(repo_root, row)
                    delivered_ids.append(row["result_id"])
                    attached_id = _clean_text(row.get("attached_correlation_id"))
                    if attached_id:
                        record = _normalize_record(_read_json(record_path(repo_root, attached_id)))
                        pending = [item for item in _clean_list(record.get("pending_result_ids")) if item != row["result_id"]]
                        delivered = _clean_list(record.get("delivered_result_ids"))
                        if row["result_id"] not in delivered:
                            delivered.append(row["result_id"])
                        record["pending_result_ids"] = pending
                        record["delivered_result_ids"] = delivered
                        record["latest_result_id"] = row["result_id"]
                        record["latest_delivery_status"] = "delivered"
                        record["updated_utc"] = now
                        _store_record(repo_root, record)
                exact.append(_normalize_result(_read_json(result_path(repo_root, row["result_id"]))))
            elif status == "ambiguous":
                ambiguous.append(row)
            elif status == "unresolved":
                unresolved.append(row)
        snapshot = _write_snapshot(repo_root, writer=writer or "correlation.reattach")
        _event(
            repo_root,
            event_type="turn_reattached",
            payload={
                "writer": _clean_text(writer),
                "launch_turn_id": target_turn_id,
                "current_turn_id": _clean_text(current_turn_id),
                "mark_delivered": bool(mark_delivered),
                "delivered_result_ids": delivered_ids,
                "exact_count": len(exact),
                "ambiguous_count": len(ambiguous),
                "unresolved_count": len(unresolved),
            },
        )
        return {
            "ok": True,
            "launch_turn_id": target_turn_id,
            "current_turn_id": _clean_text(current_turn_id),
            "exact_results": exact,
            "ambiguous_results": ambiguous,
            "unresolved_results": unresolved,
            "delivered_result_ids": delivered_ids,
            "correlation_snapshot": snapshot,
        }


def read_snapshot(repo_root: Path, *, initialize_if_missing: bool = True) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    snapshot = _read_json(snapshot_path(repo_root))
    if snapshot:
        return snapshot
    if not initialize_if_missing:
        return {
            "schema_version": CORRELATION_SNAPSHOT_SCHEMA_VERSION,
            "writer": "",
            "generated_utc": "",
            "record_count": 0,
            "result_count": 0,
            "delivery_counts": {},
            "records": [],
            "results": [],
            "events_path": str(events_path(repo_root)),
        }
    with _LOCK:
        return _write_snapshot(repo_root, writer="correlation.read")
