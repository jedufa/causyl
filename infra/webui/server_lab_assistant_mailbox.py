"""Durable inter-agent request mailbox transport and claim semantics."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from infra.webui import server_lab_assistant_correlation as _correlation
from infra.webui import server_lab_assistant_delegation_policy as _policy
from infra.webui import server_lab_assistant_job_lifecycle as _job_lifecycle
from infra.webui import server_lab_assistant_recovery as _recovery

try:
    import fcntl as _fcntl
except Exception:  # pragma: no cover
    _fcntl = None  # type: ignore[assignment]


MAILBOX_REQUEST_SCHEMA_VERSION = "lab_assistant_mailbox_request_v1"
MAILBOX_EVENT_SCHEMA_VERSION = "lab_assistant_mailbox_event_v1"
MAILBOX_SNAPSHOT_SCHEMA_VERSION = "lab_assistant_mailbox_snapshot_v1"
MAILBOX_ALLOWED_STATES = {
    "queued",
    "claimed",
    "lease_expired",
    "replayed",
    "dead_lettered",
    "handed_off",
    "superseded",
}
_LOCK = threading.RLock()


class MailboxError(RuntimeError):
    """Raised when mailbox transport truth cannot be updated safely."""


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(dt: Optional[_dt.datetime] = None) -> str:
    when = dt or _utc_now()
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(raw: Any) -> _dt.datetime:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("missing UTC timestamp")
    return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-") or "x"


def _handoff_identity(record: Dict[str, Any]) -> Dict[str, str]:
    handoff = dict(record.get("handoff") or {})
    return {
        "worker_id": _clean_text(handoff.get("worker_id")),
        "claim_id": _clean_text(handoff.get("claim_id")),
        "detached_job_id": _clean_text(handoff.get("detached_job_id")),
        "job_surface": _clean_text(handoff.get("job_surface")).lower(),
    }


def _handoff_mismatch_fields(
    record: Dict[str, Any],
    *,
    worker_id: str,
    claim_id: str,
    detached_job_id: str,
    job_surface: str,
) -> List[str]:
    actual = _handoff_identity(record)
    expected = {
        "worker_id": _clean_text(worker_id),
        "claim_id": _clean_text(claim_id),
        "detached_job_id": _clean_text(detached_job_id),
        "job_surface": _clean_text(job_surface).lower(),
    }
    return [key for key, value in expected.items() if _clean_text(actual.get(key)) != _clean_text(value)]


def mailbox_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "mailbox").resolve()


def requests_dir(repo_root: Path) -> Path:
    path = (mailbox_root(repo_root) / "requests").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_path(repo_root: Path) -> Path:
    return (mailbox_root(repo_root) / "mailbox_snapshot.json").resolve()


def events_path(repo_root: Path) -> Path:
    return (mailbox_root(repo_root) / "mailbox_events.jsonl").resolve()


def lock_path(repo_root: Path) -> Path:
    return (mailbox_root(repo_root) / ".mailbox.lock").resolve()


def request_path(repo_root: Path, request_id: str) -> Path:
    return (requests_dir(repo_root) / f"{_safe_name(request_id)}.json").resolve()


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


def _request_files(repo_root: Path) -> Iterable[Path]:
    root = requests_dir(repo_root)
    return sorted((path for path in root.glob("*.json") if path.is_file() and not path.is_symlink()), key=lambda p: p.name)


@contextmanager
def _mailbox_lock(repo_root: Path):
    path = lock_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        if _fcntl is not None:
            _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
        try:
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def _event(repo_root: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        events_path(repo_root),
        {
            "schema_version": MAILBOX_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": str(event_type or "").strip(),
            "utc": _utc_iso(),
            "payload": dict(payload),
        },
    )


def _active_claim(raw: Any) -> Dict[str, Any]:
    claim = raw if isinstance(raw, dict) else {}
    return {
        "claim_id": _clean_text(claim.get("claim_id")),
        "worker_id": _clean_text(claim.get("worker_id")),
        "claimed_utc": _clean_text(claim.get("claimed_utc")),
        "heartbeat_utc": _clean_text(claim.get("heartbeat_utc")),
        "lease_s": int(claim.get("lease_s") or 0),
        "lease_expires_utc": _clean_text(claim.get("lease_expires_utc")),
    }


def _handoff(raw: Any) -> Dict[str, Any]:
    handoff = raw if isinstance(raw, dict) else {}
    return {
        "detached_job_id": _clean_text(handoff.get("detached_job_id")),
        "job_surface": _clean_text(handoff.get("job_surface")),
        "accepted_utc": _clean_text(handoff.get("accepted_utc")),
        "worker_id": _clean_text(handoff.get("worker_id")),
        "claim_id": _clean_text(handoff.get("claim_id")),
    }


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    request = _policy.validate_typed_delegation_request(dict(record.get("request") or {}))
    state = _clean_text(record.get("state")).lower() or "queued"
    if state not in MAILBOX_ALLOWED_STATES:
        raise MailboxError(f"invalid mailbox state: {state or '<missing>'}")
    out = {
        "schema_version": MAILBOX_REQUEST_SCHEMA_VERSION,
        "request_id": _clean_text(record.get("request_id") or request.get("request_id")),
        "state": state,
        "created_utc": _clean_text(record.get("created_utc") or request.get("created_utc") or _utc_iso()),
        "updated_utc": _clean_text(record.get("updated_utc") or _utc_iso()),
        "writer": _clean_text(record.get("writer")),
        "request": request,
        "idempotency_keys": _recovery.delegation_idempotency_keys(request),
        "delivery_attempt_count": max(0, int(record.get("delivery_attempt_count") or 0)),
        "claim_count": max(0, int(record.get("claim_count") or 0)),
        "replay_count": max(0, int(record.get("replay_count") or 0)),
        "max_delivery_attempts": max(1, int(record.get("max_delivery_attempts") or 3)),
        "last_error": _clean_text(record.get("last_error")),
        "dead_letter_reason": _clean_text(record.get("dead_letter_reason")),
        "replay_reason": _clean_text(record.get("replay_reason")),
        "duplicate_of_request_id": _clean_text(record.get("duplicate_of_request_id")),
        "duplicate_suppressed": bool(record.get("duplicate_suppressed")),
        "ambiguous_delivery": bool(record.get("ambiguous_delivery")),
        "stale_claim_risk": bool(record.get("stale_claim_risk")),
        "active_claim": _active_claim(record.get("active_claim")),
        "handoff": _handoff(record.get("handoff")),
    }
    if not out["request_id"]:
        raise MailboxError("request_id is required")
    if out["state"] == "claimed":
        if not out["active_claim"]["claim_id"] or not out["active_claim"]["worker_id"]:
            raise MailboxError(f"claimed mailbox request {out['request_id']} requires active_claim")
    if out["state"] == "handed_off" and not out["handoff"]["detached_job_id"]:
        raise MailboxError(f"handed_off mailbox request {out['request_id']} requires detached_job_id")
    return out


def _record_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    claim = dict(record.get("active_claim") or {})
    handoff = dict(record.get("handoff") or {})
    request = dict(record.get("request") or {})
    input_payload = request.get("input_payload") if isinstance(request.get("input_payload"), dict) else {}
    routing_decision = input_payload.get("routing_decision") if isinstance(input_payload.get("routing_decision"), dict) else {}
    active_claim = dict(claim)
    if not _clean_text(active_claim.get("claim_id")) and _clean_text(handoff.get("claim_id")):
        active_claim = {
            "claim_id": _clean_text(handoff.get("claim_id")),
            "worker_id": _clean_text(handoff.get("worker_id")),
            "lease_expires_utc": _clean_text(claim.get("lease_expires_utc")),
        }
    return {
        "request_id": _clean_text(record.get("request_id")),
        "state": _clean_text(record.get("state")).lower(),
        "surface": _clean_text(request.get("surface")).lower(),
        "summary": _clean_text(request.get("summary")),
        "owner_ref": dict(request.get("owner_ref") or {}),
        "created_utc": _clean_text(record.get("created_utc")),
        "updated_utc": _clean_text(record.get("updated_utc")),
        "delivery_attempt_count": int(record.get("delivery_attempt_count") or 0),
        "claim_count": int(record.get("claim_count") or 0),
        "replay_count": int(record.get("replay_count") or 0),
        "ambiguous_delivery": bool(record.get("ambiguous_delivery")),
        "stale_claim_risk": bool(record.get("stale_claim_risk")),
        "worker_id": _clean_text(active_claim.get("worker_id")),
        "claim_id": _clean_text(active_claim.get("claim_id")),
        "lease_expires_utc": _clean_text(active_claim.get("lease_expires_utc")),
        "active_claim": active_claim,
        "detached_job_id": _clean_text(handoff.get("detached_job_id")),
        "job_surface": _clean_text(handoff.get("job_surface")),
        "idempotency_keys": list(record.get("idempotency_keys") or []),
        "routing_decision": dict(routing_decision),
    }


def _load_record(repo_root: Path, request_id: str) -> Dict[str, Any]:
    record = _read_json(request_path(repo_root, request_id))
    if not record:
        raise MailboxError(f"mailbox request not found: {_clean_text(request_id) or '<missing>'}")
    return _normalize_record(record)


def _store_record(repo_root: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_record(record)
    _write_json_atomic(request_path(repo_root, normalized["request_id"]), normalized)
    return normalized


def _records(repo_root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in _request_files(repo_root):
        record = _read_json(path)
        if not record:
            continue
        try:
            normalized = _normalize_record(record)
        except Exception:
            continue
        rows.append(normalized)
    rows.sort(key=lambda row: (_clean_text(row.get("created_utc")), _clean_text(row.get("request_id"))))
    return rows


def _write_snapshot(repo_root: Path, *, writer: str) -> Dict[str, Any]:
    rows = _records(repo_root)
    counts: Dict[str, int] = {state: 0 for state in sorted(MAILBOX_ALLOWED_STATES)}
    request_summaries: List[Dict[str, Any]] = []
    for row in rows:
        state = _clean_text(row.get("state")).lower()
        counts[state] = counts.get(state, 0) + 1
        summary = _record_summary(row)
        summary["path"] = str(request_path(repo_root, _clean_text(row.get("request_id"))))
        request_summaries.append(summary)
    payload = {
        "schema_version": MAILBOX_SNAPSHOT_SCHEMA_VERSION,
        "writer": _clean_text(writer),
        "generated_utc": _utc_iso(),
        "request_count": len(rows),
        "counts": counts,
        "requests": request_summaries,
        "events_path": str(events_path(repo_root)),
    }
    _write_json_atomic(snapshot_path(repo_root), payload)
    return payload


def _recover_expired_claims_locked(repo_root: Path, *, writer: str, now: _dt.datetime) -> List[str]:
    expired: List[str] = []
    for record in _records(repo_root):
        if _clean_text(record.get("state")).lower() != "claimed":
            continue
        claim = dict(record.get("active_claim") or {})
        lease_expires_utc = _clean_text(claim.get("lease_expires_utc"))
        if not lease_expires_utc:
            continue
        if _parse_utc(lease_expires_utc) > now:
            continue
        record["state"] = "lease_expired"
        record["updated_utc"] = _utc_iso(now)
        record["ambiguous_delivery"] = True
        record["stale_claim_risk"] = True
        record["last_error"] = "claim lease expired before job handoff was durably accepted"
        record["active_claim"] = {}
        stored = _store_record(repo_root, record)
        expired.append(stored["request_id"])
        _event(
            repo_root,
            event_type="lease_expired",
            payload={
                "writer": _clean_text(writer),
                "request_id": stored["request_id"],
                "worker_id": _clean_text(claim.get("worker_id")),
                "claim_id": _clean_text(claim.get("claim_id")),
                "lease_expires_utc": lease_expires_utc,
            },
        )
    return expired


def _mark_dead_letter_locked(repo_root: Path, *, request_id: str, writer: str, reason: str) -> Dict[str, Any]:
    why = _clean_text(reason)
    if not why:
        raise MailboxError("dead-letter reason is required")
    record = _load_record(repo_root, request_id)
    record["state"] = "dead_lettered"
    record["updated_utc"] = _utc_iso()
    record["dead_letter_reason"] = why
    record["active_claim"] = {}
    stored = _store_record(repo_root, record)
    snapshot = _write_snapshot(repo_root, writer=writer)
    _event(
        repo_root,
        event_type="dead_lettered",
        payload={
            "writer": _clean_text(writer),
            "request_id": stored["request_id"],
            "reason": why,
        },
    )
    return {"ok": True, "request_record": stored, "mailbox_snapshot": snapshot}


def read_mailbox_snapshot(repo_root: Path, *, initialize_if_missing: bool = True) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    snapshot = _read_json(snapshot_path(repo_root))
    if snapshot:
        return snapshot
    if not initialize_if_missing:
        return {
            "schema_version": MAILBOX_SNAPSHOT_SCHEMA_VERSION,
            "writer": "",
            "generated_utc": "",
            "request_count": 0,
            "counts": {},
            "requests": [],
            "events_path": str(events_path(repo_root)),
        }
    with _LOCK, _mailbox_lock(repo_root):
        return _write_snapshot(repo_root, writer="mailbox.read")


def enqueue_request(
    repo_root: Path,
    *,
    request: Dict[str, Any],
    writer: str,
    max_delivery_attempts: int = 3,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    normalized_request = _policy.validate_typed_delegation_request(dict(request))
    wanted = set(_recovery.delegation_idempotency_keys(normalized_request))
    with _LOCK, _mailbox_lock(repo_root):
        for existing in _records(repo_root):
            matched = sorted(wanted.intersection(set(existing.get("idempotency_keys") or [])))
            if matched:
                snapshot = _write_snapshot(repo_root, writer=writer)
                _event(
                    repo_root,
                    event_type="duplicate_suppressed",
                    payload={
                        "writer": _clean_text(writer),
                        "request_id": _clean_text(normalized_request.get("request_id")),
                        "existing_request_id": _clean_text(existing.get("request_id")),
                        "matched_keys": matched,
                    },
                )
                return {
                    "ok": True,
                    "duplicate_suppressed": True,
                    "matched_keys": matched,
                    "request_record": existing,
                    "mailbox_snapshot": snapshot,
                }
        now = _utc_iso()
        record = {
            "schema_version": MAILBOX_REQUEST_SCHEMA_VERSION,
            "request_id": _clean_text(normalized_request.get("request_id")),
            "state": "queued",
            "created_utc": now,
            "updated_utc": now,
            "writer": _clean_text(writer),
            "request": normalized_request,
            "delivery_attempt_count": 0,
            "claim_count": 0,
            "replay_count": 0,
            "max_delivery_attempts": max(1, int(max_delivery_attempts)),
            "last_error": "",
            "dead_letter_reason": "",
            "replay_reason": "",
            "duplicate_of_request_id": "",
            "duplicate_suppressed": False,
            "ambiguous_delivery": False,
            "stale_claim_risk": False,
            "active_claim": {},
            "handoff": {},
        }
        stored = _store_record(repo_root, record)
        snapshot = _write_snapshot(repo_root, writer=writer)
        _event(
            repo_root,
            event_type="enqueued",
            payload={
                "writer": _clean_text(writer),
                "request_id": stored["request_id"],
                "surface": _clean_text(normalized_request.get("surface")).lower(),
            },
        )
        return {
            "ok": True,
            "duplicate_suppressed": False,
            "matched_keys": [],
            "request_record": stored,
            "mailbox_snapshot": snapshot,
        }


def claim_next_request(
    repo_root: Path,
    *,
    worker_id: str,
    writer: str,
    lease_s: int,
    surfaces: Optional[List[str]] = None,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    worker = _clean_text(worker_id)
    if not worker:
        raise MailboxError("worker_id is required")
    lease_seconds = max(1, int(lease_s))
    allowed_surfaces = {_clean_text(item).lower() for item in list(surfaces or []) if _clean_text(item)}
    claim_now = _parse_utc(now_utc) if _clean_text(now_utc) else _utc_now()
    with _LOCK, _mailbox_lock(repo_root):
        _recover_expired_claims_locked(repo_root, writer=f"{writer}.recover_before_claim", now=claim_now)
        candidates = []
        for row in _records(repo_root):
            if _clean_text(row.get("state")).lower() not in {"queued", "replayed"}:
                continue
            surface = _clean_text(((row.get("request") or {}) if isinstance(row.get("request"), dict) else {}).get("surface")).lower()
            if allowed_surfaces and surface not in allowed_surfaces:
                continue
            candidates.append(row)
        if not candidates:
            snapshot = _write_snapshot(repo_root, writer=writer)
            return {"ok": True, "claimed": False, "request_record": {}, "mailbox_snapshot": snapshot}
        target = candidates[0]
        claim_id = f"mailbox_claim_{uuid.uuid4().hex}"
        lease_expires = claim_now + _dt.timedelta(seconds=lease_seconds)
        target["state"] = "claimed"
        target["updated_utc"] = _utc_iso(claim_now)
        target["claim_count"] = int(target.get("claim_count") or 0) + 1
        target["delivery_attempt_count"] = int(target.get("delivery_attempt_count") or 0) + 1
        target["active_claim"] = {
            "claim_id": claim_id,
            "worker_id": worker,
            "claimed_utc": _utc_iso(claim_now),
            "heartbeat_utc": _utc_iso(claim_now),
            "lease_s": lease_seconds,
            "lease_expires_utc": _utc_iso(lease_expires),
        }
        stored = _store_record(repo_root, target)
        snapshot = _write_snapshot(repo_root, writer=writer)
        _event(
            repo_root,
            event_type="claimed",
            payload={
                "writer": _clean_text(writer),
                "request_id": stored["request_id"],
                "claim_id": claim_id,
                "worker_id": worker,
                "lease_expires_utc": _utc_iso(lease_expires),
            },
        )
        return {"ok": True, "claimed": True, "request_record": stored, "mailbox_snapshot": snapshot}


def heartbeat_claim(
    repo_root: Path,
    *,
    request_id: str,
    worker_id: str,
    claim_id: str,
    writer: str,
    lease_s: int,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    beat_now = _parse_utc(now_utc) if _clean_text(now_utc) else _utc_now()
    with _LOCK, _mailbox_lock(repo_root):
        record = _load_record(repo_root, request_id)
        claim = dict(record.get("active_claim") or {})
        if _clean_text(record.get("state")).lower() != "claimed":
            raise MailboxError(f"mailbox request {request_id} is not currently claimed")
        if _clean_text(claim.get("worker_id")) != _clean_text(worker_id) or _clean_text(claim.get("claim_id")) != _clean_text(claim_id):
            raise MailboxError(f"mailbox claim mismatch for request {request_id}")
        lease_seconds = max(1, int(lease_s))
        claim["heartbeat_utc"] = _utc_iso(beat_now)
        claim["lease_s"] = lease_seconds
        claim["lease_expires_utc"] = _utc_iso(beat_now + _dt.timedelta(seconds=lease_seconds))
        record["active_claim"] = claim
        record["updated_utc"] = _utc_iso(beat_now)
        stored = _store_record(repo_root, record)
        snapshot = _write_snapshot(repo_root, writer=writer)
        _event(
            repo_root,
            event_type="heartbeat",
            payload={
                "writer": _clean_text(writer),
                "request_id": stored["request_id"],
                "claim_id": _clean_text(claim.get("claim_id")),
                "worker_id": _clean_text(worker_id),
                "lease_expires_utc": _clean_text(claim.get("lease_expires_utc")),
            },
        )
        return {"ok": True, "request_record": stored, "mailbox_snapshot": snapshot}


def recover_expired_claims(repo_root: Path, *, writer: str, now_utc: str = "") -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    recover_now = _parse_utc(now_utc) if _clean_text(now_utc) else _utc_now()
    with _LOCK, _mailbox_lock(repo_root):
        expired = _recover_expired_claims_locked(repo_root, writer=writer, now=recover_now)
        snapshot = _write_snapshot(repo_root, writer=writer)
    return {"ok": True, "expired_request_ids": expired, "mailbox_snapshot": snapshot}


def replay_request(repo_root: Path, *, request_id: str, writer: str, reason: str = "manual_replay") -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    with _LOCK, _mailbox_lock(repo_root):
        record = _load_record(repo_root, request_id)
        state = _clean_text(record.get("state")).lower()
        if state not in {"lease_expired", "dead_lettered"}:
            raise MailboxError(f"mailbox request {request_id} is not replayable from state {state or '<missing>'}")
        if state == "dead_lettered":
            raise MailboxError(f"mailbox request {request_id} is dead-lettered and must not be replayed")
        if int(record.get("delivery_attempt_count") or 0) >= int(record.get("max_delivery_attempts") or 0):
            return _mark_dead_letter_locked(
                repo_root,
                request_id=request_id,
                writer=writer,
                reason=f"delivery_attempts_exhausted:{_clean_text(reason) or 'replay'}",
            )
        record["state"] = "replayed"
        record["updated_utc"] = _utc_iso()
        record["replay_count"] = int(record.get("replay_count") or 0) + 1
        record["replay_reason"] = _clean_text(reason)
        record["ambiguous_delivery"] = True
        stored = _store_record(repo_root, record)
        snapshot = _write_snapshot(repo_root, writer=writer)
        _event(
            repo_root,
            event_type="replayed",
            payload={
                "writer": _clean_text(writer),
                "request_id": stored["request_id"],
                "reason": _clean_text(reason),
            },
        )
        return {"ok": True, "request_record": stored, "mailbox_snapshot": snapshot}


def mark_dead_letter(repo_root: Path, *, request_id: str, writer: str, reason: str) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    with _LOCK, _mailbox_lock(repo_root):
        return _mark_dead_letter_locked(repo_root, request_id=request_id, writer=writer, reason=reason)


def accept_claim_handoff(
    repo_root: Path,
    *,
    request_id: str,
    worker_id: str,
    claim_id: str,
    detached_job_id: str,
    job_surface: str,
    writer: str,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    detached = _clean_text(detached_job_id)
    if not detached:
        raise MailboxError("detached_job_id is required")
    surface = _clean_text(job_surface).lower()
    if not surface:
        raise MailboxError("job_surface is required")
    with _LOCK, _mailbox_lock(repo_root):
        record = _load_record(repo_root, request_id)
        state = _clean_text(record.get("state")).lower()
        claim = dict(record.get("active_claim") or {})
        if state == "handed_off":
            mismatches = _handoff_mismatch_fields(
                record,
                worker_id=worker_id,
                claim_id=claim_id,
                detached_job_id=detached,
                job_surface=surface,
            )
            if mismatches:
                raise MailboxError(
                    f"mailbox request {request_id} was already handed off with different {', '.join(mismatches)}"
                )
            stored = record
            accepted_utc = _clean_text((stored.get("handoff") or {}).get("accepted_utc") or stored.get("updated_utc") or _utc_iso())
            try:
                lifecycle = _job_lifecycle.accept_mailbox_handoff(
                    repo_root,
                    request_record=stored,
                    writer=f"{writer}.detached_job",
                    now_utc=accepted_utc,
                )
                _correlation.record_job_handoff(repo_root, request_record=stored, writer=f"{writer}.correlation")
            except (_job_lifecycle.DetachedJobLifecycleError, _correlation.CorrelationError) as exc:
                raise MailboxError(str(exc)) from exc
            snapshot = _write_snapshot(repo_root, writer=writer)
            return {"ok": True, "request_record": stored, "mailbox_snapshot": snapshot, "job_lifecycle": lifecycle}
        if state != "claimed":
            raise MailboxError(f"mailbox request {request_id} is not claimable for handoff from state {state or '<missing>'}")
        if _clean_text(claim.get("worker_id")) != _clean_text(worker_id) or _clean_text(claim.get("claim_id")) != _clean_text(claim_id):
            raise MailboxError(f"mailbox claim mismatch for handoff on request {request_id}")
        accepted_utc = _clean_text(claim.get("heartbeat_utc") or claim.get("claimed_utc") or _utc_iso())
        updated = dict(record)
        updated["state"] = "handed_off"
        updated["updated_utc"] = accepted_utc
        updated["handoff"] = {
            "detached_job_id": detached,
            "job_surface": surface,
            "accepted_utc": accepted_utc,
            "worker_id": _clean_text(worker_id),
            "claim_id": _clean_text(claim_id),
        }
        updated["request"] = dict(updated.get("request") or {})
        updated["request"]["detached_job_id"] = detached
        updated["active_claim"] = {}
        try:
            _job_lifecycle.validate_mailbox_handoff_target(repo_root, request_record=updated)
        except _job_lifecycle.DetachedJobLifecycleError as exc:
            raise MailboxError(str(exc)) from exc
        stored = _store_record(repo_root, updated)
        try:
            lifecycle = _job_lifecycle.accept_mailbox_handoff(
                repo_root,
                request_record=stored,
                writer=f"{writer}.detached_job",
                now_utc=accepted_utc,
            )
            _correlation.record_job_handoff(repo_root, request_record=stored, writer=f"{writer}.correlation")
        except (_job_lifecycle.DetachedJobLifecycleError, _correlation.CorrelationError) as exc:
            raise MailboxError(str(exc)) from exc
        snapshot = _write_snapshot(repo_root, writer=writer)
        _event(
            repo_root,
            event_type="handed_off",
            payload={
                "writer": _clean_text(writer),
                "request_id": stored["request_id"],
                "worker_id": _clean_text(worker_id),
                "claim_id": _clean_text(claim_id),
                "detached_job_id": detached,
                "job_surface": surface,
            },
        )
        return {"ok": True, "request_record": stored, "mailbox_snapshot": snapshot, "job_lifecycle": lifecycle}
