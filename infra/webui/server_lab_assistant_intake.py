"""Structured request intake and clarification persistence for Lab Assistant v1.

This module decides whether a raw operator utterance is active work, detects
missing information, and writes the resulting intake facts back into BD05
without creating a rival mutable store.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from infra.webui import server_lab_assistant_task_register as _register
from infra.webui import server_lab_assistant_status_taxonomy as _status_taxonomy


INTAKE_SCHEMA_VERSION = _register.TASK_INTAKE_SCHEMA_VERSION
INTAKE_REQUEST_FINGERPRINT_VERSION = "lab_assistant_intake_request_fingerprint_v1"
INTAKE_WORK_STATES = set(_register.TASK_INTAKE_WORK_STATES)
INTAKE_READINESS_STATES = set(_register.TASK_INTAKE_READINESS_STATES)
_ACTIVE_WORK_PATTERNS = (
    "implement",
    "fix",
    "update",
    "add",
    "review",
    "investigate",
    "debug",
    "run",
    "prepare",
    "schedule",
    "persist",
    "build",
    "write",
    "patch",
    "change",
)
_SOFTWARE_HINTS = (".py", ".md", ".ts", ".tsx", ".js", "api", "test", "route", "module", "server", "repo", "file")
_LAB_OPS_HINTS = ("scheduler", "workflow", "queue", "verifier", "operator", "deploy", "host", "bootstrap", "task register")
_EXPERIMENT_HINTS = ("assay", "experiment", "reagent", "sample", "protocol", "incubator")
_MATERIALS_HINTS = ("coupon", "laminate", "microstructure", "microscopy", "batch", "material")
_DEICTIC_TOKENS = {"this", "that", "it", "above", "below", "same", "again", "here"}
_SMALL_TALK_TOKENS = {
    "thanks",
    "thank you",
    "hello",
    "hi",
    "good morning",
    "good night",
    "how are you",
}


def _canonical_intake_request_payload(intake: Dict[str, Any], *, ledger_uid: str, force_active_work: bool) -> Dict[str, Any]:
    return {
        "raw_request_text": intake["raw_request_text"],
        "inferred_intent": intake["inferred_intent"],
        "summary": intake["summary"],
        "confidence": intake["confidence"],
        "work_class": intake["work_class"],
        "work_state": intake["work_state"],
        "readiness": intake["readiness"],
        "missing_information": sorted(list(intake["missing_information"]), key=lambda row: str(row.get("field") or "")),
        "ledger_uid": _clean_text(ledger_uid),
        "force_active_work": bool(force_active_work),
    }


def _intake_request_fingerprint(intake: Dict[str, Any], *, ledger_uid: str, force_active_work: bool) -> str:
    payload = _canonical_intake_request_payload(intake, ledger_uid=ledger_uid, force_active_work=force_active_work)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _intake_task_id(intake: Dict[str, Any], request_fingerprint: str) -> str:
    return f"task_intake_{_slug(intake['inferred_intent'])}_{request_fingerprint[:10]}"


def _find_task_by_fingerprint(active_tasks: List[Dict[str, Any]], request_fingerprint: str) -> Dict[str, Any] | None:
    if not request_fingerprint:
        return None
    normalized = _clean_text(request_fingerprint).lower()
    for row in active_tasks:
        existing = dict(row)
        existing_fingerprint = existing.get("request_fingerprint")
        if existing_fingerprint is None:
            existing_fingerprint = (existing.get("intake") or {}).get("request_fingerprint")
        existing_fingerprint = _clean_text(existing_fingerprint).lower()
        if existing_fingerprint and existing_fingerprint == normalized:
            return existing
    return None


class IntakeError(RuntimeError):
    """Raised when intake persistence cannot be completed honestly."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _slug(text: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", _clean_text(text).lower()).strip("_")
    return safe[:48] or "request"


def _first_sentence(text: str) -> str:
    cleaned = _clean_text(text)
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return _clean_text(parts[0]) if parts else cleaned


def _infer_work_class(text: str) -> str:
    lower = text.lower()
    if "task register" in lower or "fresh-turn bootstrap" in lower:
        return "lab_ops"
    if any(token in lower for token in _SOFTWARE_HINTS):
        return "software"
    if any(token in lower for token in _LAB_OPS_HINTS):
        return "lab_ops"
    if any(token in lower for token in _EXPERIMENT_HINTS):
        return "experiment"
    if any(token in lower for token in _MATERIALS_HINTS):
        return "materials"
    return "general"


def _looks_like_small_talk(text: str) -> bool:
    lower = text.lower()
    return any(lower == token or lower.startswith(token + " ") for token in _SMALL_TALK_TOKENS)


def _looks_like_active_work(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in _ACTIVE_WORK_PATTERNS) or "can you" in lower or "please" in lower


def _missing_information(text: str, *, work_class: str) -> List[Dict[str, Any]]:
    lower = text.lower()
    missing: List[Dict[str, Any]] = []
    words = [token for token in re.split(r"[^a-z0-9_.:/-]+", lower) if token]

    if len(words) < 4 or lower in {"help", "fix", "implement", "review"}:
        missing.append(
            {
                "field": "desired_outcome",
                "question": "What outcome should be achieved?",
                "reason": "The request does not yet say what done should look like.",
                "required": True,
            }
        )

    has_scope_anchor = (
        any(token in lower for token in _SOFTWARE_HINTS + _LAB_OPS_HINTS + _EXPERIMENT_HINTS + _MATERIALS_HINTS)
        or "/" in text
        or bool(re.search(r"\b[a-z0-9_]+\.[a-z]{1,5}\b", lower))
        or " in " in lower
        or " on " in lower
        or " for " in lower
    )
    if not has_scope_anchor:
        missing.append(
            {
                "field": "scope_anchor",
                "question": "Which system, artifact, or work item does this apply to?",
                "reason": "The request names work but not the thing being changed.",
                "required": True,
            }
        )

    if any(token in _DEICTIC_TOKENS for token in words) and len(words) <= 8:
        missing.append(
            {
                "field": "referenced_context",
                "question": "What prior context or artifact does this refer to?",
                "reason": "The request points at prior context instead of naming it directly.",
                "required": True,
            }
        )

    if work_class == "general" and len(words) < 8:
        missing.append(
            {
                "field": "execution_shape",
                "question": "Should this be answered directly, planned, or tracked as active work?",
                "reason": "The request is work-like but still underspecified about its intended shape.",
                "required": False,
            }
        )

    deduped: List[Dict[str, Any]] = []
    seen = set()
    for row in missing:
        key = (row["field"], row["question"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def analyze_request_intake(raw_request_text: str, *, force_active_work: bool = False) -> Dict[str, Any]:
    text = _clean_text(raw_request_text)
    if not text:
        raise IntakeError("raw_request_text is required")

    active_work = force_active_work or (_looks_like_active_work(text) and not _looks_like_small_talk(text))
    work_state = "active_work" if active_work else "casual_chat"
    work_class = _infer_work_class(text)
    inferred_intent = _first_sentence(text) or text
    missing_information = _missing_information(text, work_class=work_class) if active_work else []
    required_missing = [row for row in missing_information if bool(row.get("required"))]

    if not active_work:
        readiness = "good_enough_to_execute_directly"
        summary = "Utterance stays as casual chat and does not create durable active work."
        confidence = 0.9
    elif required_missing:
        readiness = _status_taxonomy.INTAKE_READINESS_CLARIFICATION_NEEDED
        summary = f"Clarification required before bounded work can proceed; missing {len(required_missing)} required intake field(s)."
        confidence = 0.35
    else:
        narrow_scope = bool(re.search(r"\b(update|edit|patch|add|remove|rename)\b", text.lower())) and (
            any(token in text.lower() for token in _SOFTWARE_HINTS) or "/" in text or bool(re.search(r"\b[a-z0-9_]+\.[a-z]{1,5}\b", text.lower()))
        )
        if narrow_scope:
            readiness = "good_enough_to_execute_directly"
            summary = "Intake is specific enough to act directly without a clarification round."
            confidence = 0.85
        elif work_class in {"software", "lab_ops"}:
            readiness = "good_enough_to_plan"
            summary = "Intake is clear enough to plan, but still broad enough that bounded planning should come first."
            confidence = 0.72
        else:
            readiness = "proceed_with_assumptions"
            summary = "Intake is usable, but some non-blocking details may still need explicit assumptions later."
            confidence = 0.6

    return {
        "schema_version": INTAKE_SCHEMA_VERSION,
        "raw_request_text": text,
        "inferred_intent": inferred_intent,
        "work_class": work_class,
        "work_state": work_state,
        "readiness": readiness,
        "confidence": confidence,
        "summary": summary,
        "missing_information": missing_information,
        "captured_utc": _utc_now_iso(),
        "updated_utc": _utc_now_iso(),
    }


def _ensure_intake_loop(
    loops: List[Dict[str, Any]],
    *,
    task_id: str,
    ledger_uid: str,
    active: bool,
    note: str,
    now_utc: str,
) -> Tuple[List[Dict[str, Any]], bool]:
    loop_id = f"intake::{task_id}::clarification"
    changed = False
    found = False
    updated: List[Dict[str, Any]] = []
    for row in loops:
        current = dict(row)
        if _clean_text(current.get("loop_id")) != loop_id:
            updated.append(current)
            continue
        found = True
        desired_status = "open" if active else "resolved"
        desired_resolved = "" if active else now_utc
        if (
            _clean_text(current.get("status")) != desired_status
            or _clean_text(current.get("note")) != note
            or _clean_text(current.get("resolved_utc")) != desired_resolved
        ):
            changed = True
        current["kind"] = _status_taxonomy.OPEN_LOOP_WAITING_ON_OPERATOR
        current["status"] = desired_status
        current["linked_task_id"] = task_id
        current["linked_ledger_uid"] = ledger_uid
        current["note"] = note
        current["updated_utc"] = now_utc
        current["resolved_utc"] = desired_resolved
        updated.append(current)
    if active and not found:
        changed = True
        updated.append(
            {
                "loop_id": loop_id,
                "kind": _status_taxonomy.OPEN_LOOP_WAITING_ON_OPERATOR,
                "status": "open",
                "linked_task_id": task_id,
                "linked_ledger_uid": ledger_uid,
                "note": note,
                "next_review_utc": "",
                "created_utc": now_utc,
                "updated_utc": now_utc,
                "resolved_utc": "",
            }
        )
    return updated, changed


def persist_structured_request_intake(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    raw_request_text: str,
    task_id: str = "",
    ledger_uid: str = "",
    force_active_work: bool = False,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    task_register_snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=True)
    intake: Dict[str, Any]
    try:
        intake = analyze_request_intake(raw_request_text, force_active_work=force_active_work)
    except Exception as exc:
        return {
            "ok": False,
            "persisted": False,
            "reason": "malformed_input",
            "error": str(exc),
            "intake": {"raw_request_text": _clean_text(raw_request_text)},
            "task_register": task_register_snapshot["task_register"],
        }

    trusted_request_fingerprint = _intake_request_fingerprint(intake, ledger_uid=ledger_uid, force_active_work=force_active_work)
    trusted_intake = _canonical_intake_request_payload(intake, ledger_uid=ledger_uid, force_active_work=force_active_work)
    trusted_intake["request_fingerprint"] = trusted_request_fingerprint
    trusted_intake["request_fingerprint_version"] = INTAKE_REQUEST_FINGERPRINT_VERSION
    if trusted_intake.get("request_fingerprint_version") != INTAKE_REQUEST_FINGERPRINT_VERSION:
        return {
            "ok": False,
            "persisted": False,
            "reason": "malformed_input",
            "error": "request_fingerprint_version mismatch",
            "intake": dict(trusted_intake),
            "task_register": task_register_snapshot["task_register"],
        }
    if intake["work_state"] != "active_work":
        return {
            "ok": True,
            "persisted": False,
            "reason": "casual_chat",
            "intake": dict(intake),
            "task_register": task_register_snapshot["task_register"],
        }

    now_utc = _utc_now_iso()
    target_task_id = _intake_task_id(trusted_intake, trusted_request_fingerprint)
    title = intake["inferred_intent"][:120]
    next_action = (
        "Ask the operator for the missing intake details."
        if intake["readiness"] == _status_taxonomy.INTAKE_READINESS_CLARIFICATION_NEEDED
        else "Use the captured intake to choose the next bounded planning or execution step."
    )
    blocked_reason = intake["summary"] if intake["readiness"] == _status_taxonomy.INTAKE_READINESS_CLARIFICATION_NEEDED else ""
    task_status = _status_taxonomy.TASK_STATUS_BLOCKED if intake["readiness"] == _status_taxonomy.INTAKE_READINESS_CLARIFICATION_NEEDED else "active"

    try:
        with _register._LOCK:
            with _register._task_register_lock(repo_root):
                register = _register._load_or_init_task_register_locked(repo_root)
                current_generation = int(register.get("write_generation") or 0)
                if int(expected_generation) != current_generation:
                    _register._reject_stale_write(
                        repo_root,
                        writer=writer,
                        expected_generation=int(expected_generation),
                        current_generation=current_generation,
                    )
                    raise IntakeError(
                        f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                    )
                existing = _find_task_by_fingerprint(list(register.get("active_tasks") or []), trusted_request_fingerprint)
                if existing is None and _clean_text(task_id):
                    existing = next(
                        (
                            dict(row)
                            for row in list(register.get("active_tasks") or [])
                            if _clean_text(row.get("task_id")) == _clean_text(task_id)
                        ),
                        None,
                    )
                target_task_id = existing["task_id"] if existing else target_task_id
                next_task = {
                    "task_id": target_task_id,
                    "kind": existing.get("kind") if existing and _clean_text(existing.get("kind")) else intake["work_class"],
                    "request_fingerprint": trusted_request_fingerprint,
                    "request_fingerprint_version": INTAKE_REQUEST_FINGERPRINT_VERSION,
                    "intake_path": "canonical",
                    "title": title,
                    "status": task_status,
                    "owner": "assistant",
                    "source": "operator_intake",
                    "ledger_uid": _clean_text(ledger_uid) or _clean_text((existing or {}).get("ledger_uid")),
                    "delegation_refs": list((existing or {}).get("delegation_refs") or []),
                    "next_expected_action": next_action,
                    "blocked_reason": blocked_reason,
                    "created_utc": _clean_text((existing or {}).get("created_utc")) or now_utc,
                    "updated_utc": now_utc,
                    "intake": trusted_intake,
                }
                normalized_task = _register._normalize_task(next_task)
                loop_note = intake["summary"]
                updated_loops, loop_changed = _ensure_intake_loop(
                    list(register.get("open_loops") or []),
                    task_id=target_task_id,
                    ledger_uid=normalized_task["ledger_uid"],
                    active=intake["readiness"] == _status_taxonomy.INTAKE_READINESS_CLARIFICATION_NEEDED,
                    note=loop_note,
                    now_utc=now_utc,
                )
                previous = next(
                    (
                        dict(row)
                        for row in list(register.get("active_tasks") or [])
                        if _clean_text(row.get("task_id")) == target_task_id
                    ),
                    None,
                )
                task_changed = previous != normalized_task
                if task_changed or loop_changed:
                    register["active_tasks"] = [
                        normalized_task if _clean_text(row.get("task_id")) == target_task_id else row
                        for row in list(register.get("active_tasks") or [])
                    ]
                    if previous is None:
                        register["active_tasks"].append(normalized_task)
                    register["open_loops"] = updated_loops
                    _register._set_generation(register, writer=writer)
                    summary = _register._write_summary_cache(repo_root, register)
                    _register._write_json_atomic(_register._register_path(repo_root), register)
                    _register._write_json_atomic(_register._summary_cache_path(repo_root), summary)
                    _register._append_event(
                        repo_root,
                        event_type="task_intake_captured",
                        payload={
                            "writer": _clean_text(writer),
                            "task_id": target_task_id,
                            "readiness": intake["readiness"],
                            "work_class": intake["work_class"],
                            "missing_information": list(intake["missing_information"]),
                        },
                    )
                    _register._write_generation_event(repo_root, register=register, writer=writer)
                    _register._append_commit_event(repo_root, register=register, summary=summary, writer=writer)
                return {
                    "ok": True,
                    "persisted": True,
                    "task_id": target_task_id,
                    "intake": intake,
                    "task_register": dict(register),
                }
    except Exception as exc:
        if isinstance(exc, IntakeError) and "stale write rejected" in str(exc).lower():
            raise
        return {
            "ok": False,
            "persisted": False,
            "reason": "intake_capture_failed",
            "error": str(exc),
            "intake": intake,
            "task_register": task_register_snapshot["task_register"],
        }
