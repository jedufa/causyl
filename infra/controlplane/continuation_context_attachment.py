"""Bounded continuation context retrieval, freshness ranking, and attachment."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from infra.controlplane import continuation_long_horizon_memory as _memory
from infra.controlplane import continuation_packet_bootstrap as _packet_bootstrap
from infra.controlplane import continuation_packet_store as _packet_store
from infra.webui import server_lab_assistant_task_register as _task_register
from infra.webui.ledger import ledger_storage as _ledger_storage


SCHEMA_VERSION = "continuation_context_attachment_v1"
MAX_BOUNDED_ITEMS = 3
ATTACHMENT_ROOT_REL = Path("runtime/state/controlplane/continuation_context_attachments")
SURFACE_PROJECTION_SCHEMA_VERSION = "continuation_surface_projection_v1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_text_list(values: Any, *, limit: int = MAX_BOUNDED_ITEMS) -> List[str]:
    rows = values if isinstance(values, list) else []
    out: List[str] = []
    for item in rows:
        text = _clean_text(item)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _append_visible_caveat(visible_caveats: List[str], message: str) -> None:
    text = _clean_text(message)
    if text and text not in visible_caveats:
        visible_caveats.append(text)


def _append_visible_gap(target: Mapping[str, Any] | Dict[str, Any], message: str) -> None:
    if not isinstance(target, dict):
        return
    selection_metadata = target.get("selection_metadata")
    if not isinstance(selection_metadata, dict):
        return
    visible_gaps = selection_metadata.get("visible_gaps")
    if not isinstance(visible_gaps, list):
        return
    text = _clean_text(message)
    if text and text not in visible_gaps:
        visible_gaps.append(text)


def _parse_utc(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            parsed = datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _spec_as_of_utc(spec: Mapping[str, Any]) -> str:
    schedule = spec.get("schedule") if isinstance(spec.get("schedule"), Mapping) else {}
    for value in (
        schedule.get("run_at"),
        spec.get("updated_utc"),
        spec.get("created_utc"),
    ):
        text = _clean_text(value)
        if text:
            return text
    return _utc_now_iso()


def _age_seconds(*, created_utc: Any, as_of_utc: Any) -> int | None:
    created = _parse_utc(created_utc)
    as_of = _parse_utc(as_of_utc)
    if created is None or as_of is None:
        return None
    return max(0, int((as_of - created).total_seconds()))


def _packet_freshness_projection(*, created_utc: Any, as_of_utc: str) -> Dict[str, Any]:
    age_s = _age_seconds(created_utc=created_utc, as_of_utc=as_of_utc)
    if age_s is None:
        return {
            "status": "unknown",
            "reason": "packet_created_utc_missing_or_invalid",
            "age_s": None,
            "as_of_utc": _clean_text(as_of_utc),
        }
    if age_s >= 7 * 24 * 3600:
        status = "stale"
    elif age_s >= 24 * 3600:
        status = "aging"
    else:
        status = "fresh"
    return {
        "status": status,
        "reason": "",
        "age_s": age_s,
        "as_of_utc": _clean_text(as_of_utc),
    }


def _attachment_key(spec: Mapping[str, Any]) -> str:
    job_id = _clean_text(spec.get("job_id"))
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    material = json.dumps(
        {
            "job_id": job_id,
            "binding_type": _clean_text(binding.get("type")),
            "binding_id": _clean_text(binding.get("id")),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    stem = job_id or f"{_clean_text(binding.get('type'))}_{_clean_text(binding.get('id'))}" or "unbound"
    slug = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in stem)[:48].strip("._")
    return f"{slug or 'attachment'}__{digest}"


def attachment_root(*, repo_root: Path | None = None) -> Path:
    root = Path(repo_root or _repo_root()).resolve()
    return (root / ATTACHMENT_ROOT_REL).resolve()


def _attachment_relpath(path: Path, *, repo_root: Path) -> str:
    return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")


def _task_register_payload(repo_root: Path) -> Dict[str, Any] | None:
    try:
        snapshot = _task_register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    except Exception:
        return None
    return snapshot.get("task_register") if isinstance(snapshot, dict) else None


def _lookup_candidates(packet_snapshot: Mapping[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in list(packet_snapshot.get("lookup_candidates") or []):
        if not isinstance(row, dict):
            continue
        lookup_key = _clean_text(row.get("lookup_key"))
        lookup_value = _clean_text(row.get("lookup_value"))
        source = _clean_text(row.get("source"))
        if lookup_key and lookup_value:
            out.append({"lookup_key": lookup_key, "lookup_value": lookup_value, "source": source})
    return out


def _task_for_binding(
    binding: Mapping[str, Any] | None,
    *,
    task_register: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    register_obj = task_register if isinstance(task_register, dict) else {}
    tasks = [dict(row) for row in list(register_obj.get("active_tasks") or []) if isinstance(row, dict)]
    binding_obj = binding if isinstance(binding, dict) else {}
    binding_type = _clean_text(binding_obj.get("type"))
    binding_id = _clean_text(binding_obj.get("id"))
    if not binding_type or not binding_id:
        return {}
    if binding_type == "task_id":
        matches = [task for task in tasks if _clean_text(task.get("task_id")) == binding_id]
    elif binding_type == "ledger_uid":
        matches = [task for task in tasks if _clean_text(task.get("ledger_uid")) == binding_id]
    else:
        return {}
    if len(matches) != 1:
        return {}
    return dict(matches[0])


def _packet_scope_ok(packet: Mapping[str, Any], *, lookup_key: str) -> tuple[bool, str]:
    applicability = packet.get("applicability") if isinstance(packet.get("applicability"), dict) else {}
    scope = _clean_text(applicability.get("scope")).lower()
    if scope == "reference_only":
        return False, "packet_scope_reference_only"
    if scope == "operator_triage":
        return False, "packet_scope_operator_triage"
    if scope == "immediate_successor":
        if lookup_key in {"job_id", "run_id", "attempt_id"}:
            return True, ""
        return False, "packet_scope_requires_exact_successor_lookup"
    if scope == "same_family":
        if lookup_key in {"job_id", "run_id", "attempt_id", "job_family", "campaign_id"}:
            return True, ""
        return False, "packet_scope_requires_family_or_exact_lookup"
    return False, "packet_scope_not_supported"


def _direct_current_context_facts(spec: Mapping[str, Any]) -> List[Dict[str, str]]:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    facts = [
        {"fact_type": "binding", "summary": f"{_clean_text(binding.get('type'))}:{_clean_text(binding.get('id'))}".strip(":")},
        {"fact_type": "job_kind", "summary": _clean_text(spec.get("job_kind"))},
        {"fact_type": "required_context_bundle", "summary": _clean_text(spec.get("required_context_bundle"))},
        {"fact_type": "goal", "summary": _clean_text(spec.get("prompt_preview") or spec.get("display_title") or spec.get("prompt"))},
    ]
    return [row for row in facts if row["summary"]][:MAX_BOUNDED_ITEMS]


def _packet_revision_rows(*, repo_root: Path) -> List[Dict[str, Any]]:
    rows = _packet_store.read_store_records(repo_root=repo_root)
    packet_rows = [
        dict(row)
        for row in rows
        if str(row.get("record_kind") or "") == _packet_store.PACKET_RECORD_KIND
    ]
    statuses: Dict[tuple[str, int], List[Dict[str, Any]]] = {}
    for row in rows:
        if str(row.get("record_kind") or "") != _packet_store.STATUS_RECORD_KIND:
            continue
        key = (_clean_text(row.get("packet_id")), int(row.get("packet_revision") or 0))
        statuses.setdefault(key, []).append(dict(row))
    for row in packet_rows:
        key = (_clean_text(row.get("packet_id")), int(row.get("packet_revision") or 0))
        history = statuses.get(key, [])
        terminal = _packet_store.STATUS_ACTIVE
        for status_row in history:
            terminal = _clean_text(status_row.get("status")) or terminal
        row["terminal_status"] = terminal
        row["status_history"] = history
    packet_rows.sort(
        key=lambda row: (
            _clean_text(row.get("created_utc")),
            _clean_text(row.get("packet_id")),
            int(row.get("packet_revision") or 0),
        ),
        reverse=True,
    )
    return packet_rows


def _matched_packet_rows(
    packet_rows: Sequence[Mapping[str, Any]],
    *,
    lookup_candidates: Sequence[Mapping[str, str]],
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for candidate in lookup_candidates:
        lookup_key = _clean_text(candidate.get("lookup_key"))
        lookup_value = _clean_text(candidate.get("lookup_value"))
        if not lookup_key or not lookup_value:
            continue
        for row in packet_rows:
            lookup_keys = row.get("lookup_keys") if isinstance(row.get("lookup_keys"), dict) else {}
            if _clean_text(lookup_keys.get(lookup_key)) != lookup_value:
                continue
            packet = row.get("packet") if isinstance(row.get("packet"), dict) else {}
            scope_ok, scope_reason = _packet_scope_ok(packet, lookup_key=lookup_key)
            if not scope_ok:
                continue
            key = (_clean_text(row.get("packet_id")), int(row.get("packet_revision") or 0))
            if key in seen:
                continue
            seen.add(key)
            enriched = dict(row)
            enriched["matched_lookup"] = dict(candidate)
            enriched["scope_match_reason"] = scope_reason
            matches.append(enriched)
    matches.sort(
        key=lambda row: (
            _clean_text(row.get("created_utc")),
            _clean_text(row.get("packet_id")),
            int(row.get("packet_revision") or 0),
        ),
        reverse=True,
    )
    return matches


def _bounded_packet_delta(packet_record: Mapping[str, Any]) -> Dict[str, Any]:
    packet = packet_record.get("packet") if isinstance(packet_record.get("packet"), dict) else {}
    result_summary = packet.get("result_summary") if isinstance(packet.get("result_summary"), dict) else {}
    source_run = packet.get("source_run") if isinstance(packet.get("source_run"), dict) else {}
    return {
        "packet_id": _clean_text(packet_record.get("packet_id") or packet.get("packet_id")),
        "packet_revision": int(packet_record.get("packet_revision") or packet.get("packet_revision") or 0),
        "created_utc": _clean_text(packet.get("created_utc")),
        "scope": _clean_text(((packet.get("applicability") if isinstance(packet.get("applicability"), dict) else {}).get("scope"))).lower(),
        "handoff_brief": _clean_text(packet.get("handoff_brief")),
        "result_summary": {
            "outcome": _clean_text(result_summary.get("outcome")),
            "summary": _clean_text(result_summary.get("summary")),
        },
        "source_run": {
            "job_id": _clean_text(source_run.get("job_id")),
            "run_id": _clean_text(source_run.get("run_id")),
            "attempt_id": _clean_text(source_run.get("attempt_id")),
            "receipt_refs": _clean_text_list(source_run.get("receipt_refs")),
        },
        "failed_paths": [
            {
                "path_id": _clean_text((row if isinstance(row, dict) else {}).get("path_id")),
                "summary": _clean_text((row if isinstance(row, dict) else {}).get("summary")),
            }
            for row in list(packet.get("failed_paths") or [])[:MAX_BOUNDED_ITEMS]
            if _clean_text((row if isinstance(row, dict) else {}).get("summary"))
        ],
        "open_questions": [
            {
                "question_id": _clean_text((row if isinstance(row, dict) else {}).get("question_id")),
                "question": _clean_text((row if isinstance(row, dict) else {}).get("question")),
            }
            for row in list(packet.get("open_questions") or [])[:MAX_BOUNDED_ITEMS]
            if _clean_text((row if isinstance(row, dict) else {}).get("question"))
        ],
        "recommended_next_steps": [
            {
                "step_id": _clean_text((row if isinstance(row, dict) else {}).get("step_id")),
                "step": _clean_text((row if isinstance(row, dict) else {}).get("step")),
            }
            for row in list(packet.get("recommended_next_steps") or [])[:MAX_BOUNDED_ITEMS]
            if _clean_text((row if isinstance(row, dict) else {}).get("step"))
        ],
    }


def _bounded_memory_delta(memory_records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in memory_records[:MAX_BOUNDED_ITEMS]:
        source_packet = row.get("source_packet") if isinstance(row.get("source_packet"), dict) else {}
        retention = row.get("retention") if isinstance(row.get("retention"), dict) else {}
        out.append(
            {
                "memory_id": _clean_text(row.get("memory_id")),
                "memory_kind": _clean_text(row.get("memory_kind")),
                "summary": _clean_text(row.get("summary")),
                "source_packet_id": _clean_text(source_packet.get("packet_id")),
                "source_packet_revision": int(source_packet.get("packet_revision") or 0),
                "source_packet_created_utc": _clean_text(source_packet.get("created_utc")),
                "expires_at_utc": _clean_text(retention.get("expires_at_utc")),
                "provenance_refs": _clean_text_list(row.get("provenance_refs")),
            }
        )
    return out


def _memory_record_projection(record: Mapping[str, Any], *, as_of_utc: str) -> Dict[str, Any]:
    row = dict(record or {})
    activity = _memory.evaluate_memory_record_activity(row, as_of_utc=as_of_utc)
    retention = row.get("retention") if isinstance(row.get("retention"), dict) else {}
    return {
        "memory_id": _clean_text(row.get("memory_id")),
        "memory_kind": _clean_text(row.get("memory_kind")),
        "summary": _clean_text(row.get("summary")),
        "status": _clean_text(activity.get("status")) or "invalid",
        "reason": _clean_text(activity.get("reason")),
        "active": bool(activity.get("active")),
        "expires_at_utc": _clean_text(retention.get("expires_at_utc")),
        "provenance_refs": _clean_text_list(row.get("provenance_refs")),
    }


def _handoff_summary_text(
    *,
    packet_projection: Mapping[str, Any],
    memory_projection: Mapping[str, Any],
    authoritative_source: str,
    visible_caveats: Sequence[str],
) -> str:
    packet = packet_projection if isinstance(packet_projection, dict) else {}
    memory = memory_projection if isinstance(memory_projection, dict) else {}
    memory_records = [row for row in list(memory.get("records") or []) if isinstance(row, dict)]
    failed_paths = [
        _clean_text((row if isinstance(row, dict) else {}).get("summary"))
        for row in list(packet.get("failed_paths") or [])
        if _clean_text((row if isinstance(row, dict) else {}).get("summary"))
    ]
    open_questions = [
        _clean_text((row if isinstance(row, dict) else {}).get("question"))
        for row in list(packet.get("open_questions") or [])
        if _clean_text((row if isinstance(row, dict) else {}).get("question"))
    ]
    packet_freshness = packet.get("freshness") if isinstance(packet.get("freshness"), dict) else {}
    packet_freshness_status = _clean_text(packet_freshness.get("status"))
    packet_freshness_age_s = packet_freshness.get("age_s")
    next_steps = [
        _clean_text((row if isinstance(row, dict) else {}).get("step"))
        for row in list(packet.get("recommended_next_steps") or [])
        if _clean_text((row if isinstance(row, dict) else {}).get("step"))
    ]
    if not failed_paths:
        failed_paths = [
            _clean_text(row.get("summary"))
            for row in memory_records
            if _clean_text(row.get("memory_kind")) == "negative_finding" and _clean_text(row.get("summary"))
        ]
    if not open_questions:
        open_questions = [
            _clean_text(row.get("summary"))
            for row in memory_records
            if _clean_text(row.get("memory_kind")) == "open_question" and _clean_text(row.get("summary"))
        ]
    if not next_steps:
        next_steps = [
            _clean_text(row.get("summary"))
            for row in memory_records
            if _clean_text(row.get("memory_kind")) == "recommended_next_step" and _clean_text(row.get("summary"))
        ]
    if authoritative_source == "continuation_packet":
        parts = [
            _clean_text(packet.get("handoff_brief")) or _clean_text((packet.get("result_summary") or {}).get("summary")) or "durable continuation packet selected",
        ]
    elif authoritative_source == "memory_records":
        counts = memory.get("activity_counts") if isinstance(memory.get("activity_counts"), dict) else {}
        parts = [
            (
                "No valid continuation packet; using packet-derived memory "
                f"from {_clean_text(memory.get('source_packet_id')) or 'packet'}"
                f":r{int(memory.get('source_packet_revision') or 0)}."
            ).strip()
        ]
        if int(counts.get("active", 0)) <= 0:
            parts.append("Derived memory has no active guidance.")
    elif authoritative_source == "direct_current_context":
        parts = ["No durable continuation packet or usable derived memory is available."]
    else:
        parts = ["Continuation state is unavailable."]
    if packet_freshness_status in {"fresh", "aging", "stale"}:
        age_clause = ""
        if isinstance(packet_freshness_age_s, int):
            age_clause = f" age={packet_freshness_age_s}s"
        parts.append(f"Packet freshness: {packet_freshness_status}.{age_clause}".strip())
    memory_status = _clean_text(memory.get("status"))
    if memory_status not in {"", "none"}:
        counts = memory.get("activity_counts") if isinstance(memory.get("activity_counts"), dict) else {}
        parts.append(
            "Derived memory: "
            f"{memory_status} "
            f"(active={int(counts.get('active', 0))}, "
            f"expired={int(counts.get('expired', 0))}, "
            f"withdrawn={int(counts.get('withdrawn', 0))}, "
            f"contradicted={int(counts.get('contradicted', 0))})."
        )
    if failed_paths:
        parts.append(f"Failed path: {failed_paths[0]}")
    if open_questions:
        parts.append(f"Open question: {open_questions[0]}")
    if next_steps:
        parts.append(f"Next: {next_steps[0]}")
    if visible_caveats:
        parts.append(f"Caveat: {_clean_text(visible_caveats[0])}")
    return " ".join(part for part in parts if part).strip()


def inspect_continuation_state_for_binding(
    binding: Mapping[str, Any] | None,
    *,
    task_register: Mapping[str, Any] | None,
    repo_root: Path,
    as_of_utc: str | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    binding_obj = binding if isinstance(binding, dict) else {}
    now_utc = as_of_utc or _utc_now_iso()
    packet_snapshot = _packet_bootstrap.packet_snapshot_for_binding(binding_obj, task_register=task_register, repo_root=root)
    lookup_candidates = _lookup_candidates(packet_snapshot)
    task = _task_for_binding(binding_obj, task_register=task_register)
    packet_projection: Dict[str, Any] = {
        "status": _clean_text(packet_snapshot.get("mode")) or "none",
        "reason": _clean_text(packet_snapshot.get("reason")),
        "required": bool(packet_snapshot.get("required")),
        "lookup_candidates": lookup_candidates,
        "packet_id": "",
        "packet_revision": 0,
        "created_utc": "",
        "freshness": {
            "status": "unknown",
            "reason": "packet_not_selected",
            "age_s": None,
            "as_of_utc": _clean_text(now_utc),
        },
        "handoff_brief": "",
        "result_summary": {"outcome": "", "summary": ""},
        "failed_paths": [],
        "open_questions": [],
        "recommended_next_steps": [],
    }
    selected_packet = packet_snapshot.get("packet_record") if isinstance(packet_snapshot.get("packet_record"), dict) else None
    if selected_packet is not None:
        bounded_packet = _bounded_packet_delta(selected_packet)
        packet_projection.update(
            {
                "status": "ready",
                "packet_id": _clean_text(bounded_packet.get("packet_id")),
                "packet_revision": int(bounded_packet.get("packet_revision") or 0),
                "created_utc": _clean_text(bounded_packet.get("created_utc")),
                "freshness": _packet_freshness_projection(
                    created_utc=bounded_packet.get("created_utc"),
                    as_of_utc=now_utc,
                ),
                "handoff_brief": _clean_text(bounded_packet.get("handoff_brief")),
                "result_summary": dict(bounded_packet.get("result_summary") or {}),
                "failed_paths": list(bounded_packet.get("failed_paths") or []),
                "open_questions": list(bounded_packet.get("open_questions") or []),
                "recommended_next_steps": list(bounded_packet.get("recommended_next_steps") or []),
            }
        )

    memory_projection: Dict[str, Any] = {
        "status": "none",
        "reason": "",
        "source_packet_id": "",
        "source_packet_revision": 0,
        "source_packet_created_utc": "",
        "activity_counts": {"active": 0, "expired": 0, "withdrawn": 0, "contradicted": 0, "invalid": 0},
        "records": [],
    }
    memory_source_record = selected_packet
    packet_snapshot_mode = _clean_text(packet_snapshot.get("mode"))
    visible_caveats: List[str] = []
    if packet_snapshot_mode in {"degraded", "blocked"} and _clean_text(packet_snapshot.get("reason")):
        _append_visible_caveat(visible_caveats, _clean_text(packet_snapshot.get("reason")))

    if memory_source_record is None and packet_snapshot_mode != "blocked":
        try:
            packet_rows = _packet_revision_rows(repo_root=root)
        except Exception as exc:
            packet_rows = []
            memory_projection["status"] = "blocked"
            memory_projection["reason"] = f"memory_packet_scan_failed:{type(exc).__name__}"
            _append_visible_caveat(visible_caveats, memory_projection["reason"])
        if packet_rows:
            memory_packet_rows = [
                row
                for row in _matched_packet_rows(packet_rows, lookup_candidates=lookup_candidates)
                if _clean_text(row.get("terminal_status")) != _packet_store.STATUS_INVALIDATED
            ]
            if memory_packet_rows:
                top = memory_packet_rows[0]
                top_created = _clean_text(top.get("created_utc"))
                ambiguous = [
                    row
                    for row in memory_packet_rows[1:]
                    if _clean_text(row.get("created_utc")) == top_created
                    and (
                        _clean_text(row.get("packet_id")) != _clean_text(top.get("packet_id"))
                        or int(row.get("packet_revision") or 0) != int(top.get("packet_revision") or 0)
                    )
                ]
                if ambiguous:
                    memory_projection["status"] = "blocked"
                    memory_projection["reason"] = "ambiguous_memory_packet_freshness"
                    _append_visible_caveat(visible_caveats, "ambiguous_memory_packet_freshness")
                else:
                    memory_source_record = top

    if memory_source_record is not None and memory_projection["status"] != "blocked":
        promotion = _memory.promote_packet_to_memory_records(
            memory_source_record.get("packet") if isinstance(memory_source_record.get("packet"), dict) else {},
            promoted_at_utc=_clean_text(memory_source_record.get("created_utc")) or now_utc,
        )
        if not promotion.get("ok"):
            memory_projection["status"] = "blocked"
            memory_projection["reason"] = _clean_text(promotion.get("reason")) or "packet_memory_promotion_failed"
            _append_visible_caveat(visible_caveats, memory_projection["reason"])
        else:
            projected_records = [
                _memory_record_projection(record, as_of_utc=now_utc)
                for record in list(promotion.get("records") or [])
            ]
            counts = {"active": 0, "expired": 0, "withdrawn": 0, "contradicted": 0, "invalid": 0}
            for row in projected_records:
                status = _clean_text(row.get("status")) or "invalid"
                counts[status if status in counts else "invalid"] += 1
            memory_projection.update(
                {
                    "status": "ready" if counts["active"] > 0 else ("degraded" if projected_records else "none"),
                    "reason": "" if counts["active"] > 0 else (_clean_text(promotion.get("reason")) or "all_derived_memory_inactive"),
                    "source_packet_id": _clean_text(memory_source_record.get("packet_id")),
                    "source_packet_revision": int(memory_source_record.get("packet_revision") or 0),
                    "source_packet_created_utc": _clean_text(memory_source_record.get("created_utc")),
                    "activity_counts": counts,
                    "records": projected_records[:MAX_BOUNDED_ITEMS],
                }
            )
            if memory_projection["status"] != "ready" and memory_projection["reason"]:
                _append_visible_caveat(visible_caveats, memory_projection["reason"])
    elif memory_projection["status"] == "none":
        memory_projection["reason"] = "no_promotable_packet_backing_memory"

    packet_freshness = packet_projection.get("freshness") if isinstance(packet_projection.get("freshness"), dict) else {}
    packet_freshness_status = _clean_text(packet_freshness.get("status"))
    if packet_projection["status"] == "ready" and packet_freshness_status == "stale":
        _append_visible_caveat(
            visible_caveats,
            "selected continuation packet is stale; treat it as review-needed guidance, not live completion truth",
        )

    memory_counts = memory_projection.get("activity_counts") if isinstance(memory_projection.get("activity_counts"), dict) else {}
    inactive_memory_count = sum(
        int(memory_counts.get(key, 0))
        for key in ("expired", "withdrawn", "contradicted", "invalid")
    )
    if memory_projection["status"] == "ready" and inactive_memory_count > 0:
        _append_visible_caveat(
            visible_caveats,
            "derived memory includes inactive records; active guidance is only partial",
        )

    authoritative_source = "direct_current_context"
    handoff_status = "degraded"
    if packet_projection["status"] == "ready":
        authoritative_source = "continuation_packet"
        handoff_status = "degraded" if packet_freshness_status == "stale" else "ready"
    elif memory_projection["status"] == "ready":
        authoritative_source = "memory_records"
        handoff_status = "degraded" if inactive_memory_count > 0 else "ready"
    elif packet_snapshot_mode == "blocked" or memory_projection["status"] == "blocked":
        authoritative_source = "none"
        handoff_status = "blocked"

    handoff_projection = {
        "status": handoff_status,
        "authoritative_source": authoritative_source,
        "summary_text": _handoff_summary_text(
            packet_projection=packet_projection,
            memory_projection=memory_projection,
            authoritative_source=authoritative_source,
            visible_caveats=visible_caveats,
        ),
        "visible_caveats": visible_caveats[:MAX_BOUNDED_ITEMS],
    }
    return {
        "schema_version": SURFACE_PROJECTION_SCHEMA_VERSION,
        "binding": {"type": _clean_text(binding_obj.get("type")), "id": _clean_text(binding_obj.get("id"))},
        "task_ref": {
            "task_id": _clean_text(task.get("task_id")),
            "ledger_uid": _clean_text(task.get("ledger_uid")),
            "title": _clean_text(task.get("title")),
        },
        "packet": packet_projection,
        "memory": memory_projection,
        "handoff": handoff_projection,
    }


def render_surface_projection_lines(projection: Mapping[str, Any]) -> List[str]:
    row = projection if isinstance(projection, dict) else {}
    task_ref = row.get("task_ref") if isinstance(row.get("task_ref"), dict) else {}
    label = _clean_text(task_ref.get("task_id")) or _clean_text(task_ref.get("ledger_uid")) or _clean_text(
        ((row.get("binding") if isinstance(row.get("binding"), dict) else {}).get("id"))
    ) or "binding"
    packet = row.get("packet") if isinstance(row.get("packet"), dict) else {}
    memory = row.get("memory") if isinstance(row.get("memory"), dict) else {}
    handoff = row.get("handoff") if isinstance(row.get("handoff"), dict) else {}
    counts = memory.get("activity_counts") if isinstance(memory.get("activity_counts"), dict) else {}
    lines = [
        (
            f"- [{label}] handoff={_clean_text(handoff.get('status')) or 'none'}; "
            f"source={_clean_text(handoff.get('authoritative_source')) or 'none'}; "
            f"summary={_clean_text(handoff.get('summary_text')) or 'continuation state unavailable'}"
        )
    ]
    packet_status = _clean_text(packet.get("status")) or "none"
    if packet_status != "none":
        if packet_status == "ready":
            freshness = packet.get("freshness") if isinstance(packet.get("freshness"), dict) else {}
            lines.append(
                f"- [{label}] packet={_clean_text(packet.get('packet_id'))}:r{int(packet.get('packet_revision') or 0)}; "
                f"outcome={_clean_text((packet.get('result_summary') or {}).get('outcome')) or 'unknown'}; "
                f"freshness={_clean_text(freshness.get('status')) or 'unknown'}; "
                f"age_s={freshness.get('age_s') if freshness.get('age_s') is not None else 'unknown'}"
            )
        else:
            lines.append(f"- [{label}] packet={packet_status}; reason={_clean_text(packet.get('reason')) or 'unknown'}")
    memory_status = _clean_text(memory.get("status")) or "none"
    if memory_status != "none":
        lines.append(
            f"- [{label}] memory={memory_status}; active={int(counts.get('active', 0))}; "
            f"expired={int(counts.get('expired', 0))}; withdrawn={int(counts.get('withdrawn', 0))}; "
            f"contradicted={int(counts.get('contradicted', 0))}"
        )
    caveats = [_clean_text(item) for item in list(handoff.get("visible_caveats") or []) if _clean_text(item)]
    if caveats:
        lines.append(f"- [{label}] caveat={caveats[0]}")
    return lines


def materialize_attachment_for_job_spec(
    spec: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    as_of_utc: str | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    binding_type = _clean_text(binding.get("type"))
    binding_id = _clean_text(binding.get("id"))
    if not binding_type or not binding_id:
        return {"ok": True, "context_ref": "", "attachment": {}, "reason": "binding_not_task_addressable"}

    register = _task_register_payload(root)
    packet_snapshot = _packet_bootstrap.packet_snapshot_for_binding(binding, task_register=register, repo_root=root)
    if _clean_text(packet_snapshot.get("mode")) == "none":
        return {"ok": True, "context_ref": "", "attachment": {}, "reason": "no_continuation_context_binding_state"}
    lookup_candidates = _lookup_candidates(packet_snapshot)
    now_utc = as_of_utc or _spec_as_of_utc(spec)
    direct_facts = _direct_current_context_facts(spec)

    attachment: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "binding": {"type": binding_type, "id": binding_id},
        "job_id": _clean_text(spec.get("job_id")),
        "created_utc": now_utc,
        "status": "degraded",
        "selected_source": "direct_current_context",
        "reason": "no_durable_continuation_context_available",
        "freshness_basis": "current_context_only",
        "packet_snapshot": {
            "mode": _clean_text(packet_snapshot.get("mode")),
            "reason": _clean_text(packet_snapshot.get("reason")),
            "lookup_candidates": lookup_candidates,
        },
        "selection_metadata": {
            "precedence_rule": "latest_valid_packet_then_packet_derived_memory_then_direct_current_context",
            "transcript_fallback": "forbidden",
            "suppressed_sources": [],
            "visible_gaps": [],
            "selected_packet_freshness": {
                "status": "unknown",
                "reason": "packet_not_selected",
                "age_s": None,
                "as_of_utc": _clean_text(now_utc),
            },
            "memory_source_packet_freshness": {
                "status": "unknown",
                "reason": "memory_packet_not_selected",
                "age_s": None,
                "as_of_utc": _clean_text(now_utc),
            },
        },
        "bounded_context": {"direct_current_context_facts": direct_facts},
    }

    selected_packet_record = packet_snapshot.get("packet_record") if isinstance(packet_snapshot.get("packet_record"), dict) else None
    selected_lookup = packet_snapshot.get("selected_lookup") if isinstance(packet_snapshot.get("selected_lookup"), dict) else {}
    if selected_packet_record is not None:
        scope_ok, scope_reason = _packet_scope_ok(
            selected_packet_record.get("packet") if isinstance(selected_packet_record.get("packet"), dict) else {},
            lookup_key=_clean_text(selected_lookup.get("lookup_key")),
        )
        packet_freshness = _packet_freshness_projection(
            created_utc=selected_packet_record.get("created_utc"),
            as_of_utc=now_utc,
        )
        attachment["selection_metadata"]["selected_packet_freshness"] = packet_freshness
        if scope_ok:
            attachment["status"] = "degraded" if _clean_text(packet_freshness.get("status")) == "stale" else "ready"
            attachment["selected_source"] = "continuation_packet"
            attachment["reason"] = "latest_valid_scoped_packet_selected"
            attachment["freshness_basis"] = f"packet_created_utc:{_clean_text(selected_packet_record.get('created_utc'))}"
            attachment["selection_metadata"]["suppressed_sources"] = [
                "packet_derived_memory_suppressed_by_fresher_packet",
                "direct_current_context_suppressed_by_durable_packet",
            ]
            attachment["bounded_context"] = {"continuation_packet": _bounded_packet_delta(selected_packet_record)}
            if _clean_text(packet_freshness.get("status")) == "stale":
                _append_visible_gap(
                    attachment,
                    "selected continuation packet is stale; treat it as review-needed guidance, not live completion truth",
                )
        else:
            attachment["status"] = "degraded"
            attachment["selected_source"] = "direct_current_context"
            attachment["reason"] = scope_reason
            _append_visible_gap(attachment, scope_reason)

    packet_snapshot_mode = _clean_text(packet_snapshot.get("mode"))

    if attachment["selected_source"] != "continuation_packet" and packet_snapshot_mode != "blocked":
        memory_packet_rows: List[Dict[str, Any]] = []
        try:
            packet_rows = _packet_revision_rows(repo_root=root)
        except Exception as exc:
            _append_visible_gap(attachment, f"memory_packet_scan_failed:{type(exc).__name__}")
            packet_rows = []
        for row in _matched_packet_rows(packet_rows, lookup_candidates=lookup_candidates):
            if _clean_text(row.get("terminal_status")) == _packet_store.STATUS_INVALIDATED:
                continue
            if selected_packet_record is not None and (
                _clean_text(row.get("packet_id")) == _clean_text(selected_packet_record.get("packet_id"))
                and int(row.get("packet_revision") or 0) == int(selected_packet_record.get("packet_revision") or 0)
            ):
                continue
            memory_packet_rows.append(row)
        if memory_packet_rows:
            top = memory_packet_rows[0]
            top_created = _clean_text(top.get("created_utc"))
            ambiguous = [
                row
                for row in memory_packet_rows[1:]
                if _clean_text(row.get("created_utc")) == top_created
                and (
                    _clean_text(row.get("packet_id")) != _clean_text(top.get("packet_id"))
                    or int(row.get("packet_revision") or 0) != int(top.get("packet_revision") or 0)
                )
            ]
            if ambiguous:
                attachment["status"] = "blocked"
                attachment["selected_source"] = "none"
                attachment["reason"] = "ambiguous_memory_packet_freshness"
                attachment["freshness_basis"] = f"memory_packet_created_utc:{top_created}"
                attachment["selection_metadata"]["visible_gaps"].append("ambiguous_memory_packet_freshness")
                attachment["bounded_context"] = {"direct_current_context_facts": direct_facts}
            else:
                memory_packet_freshness = _packet_freshness_projection(created_utc=top.get("created_utc"), as_of_utc=now_utc)
                attachment["selection_metadata"]["memory_source_packet_freshness"] = memory_packet_freshness
                promotion = _memory.promote_packet_to_memory_records(
                    top.get("packet") if isinstance(top.get("packet"), dict) else {},
                    promoted_at_utc=_clean_text(top.get("created_utc")) or now_utc,
                )
                active_records = [
                    row
                    for row in list(promotion.get("records") or [])
                    if _memory.evaluate_memory_record_activity(row, as_of_utc=now_utc).get("active") is True
                ]
                if active_records:
                    attachment["status"] = "degraded" if _clean_text(memory_packet_freshness.get("status")) == "stale" else "ready"
                    attachment["selected_source"] = "memory_records"
                    attachment["reason"] = "packet_derived_memory_selected"
                    attachment["freshness_basis"] = f"memory_source_packet_created_utc:{top_created}"
                    attachment["selection_metadata"]["suppressed_sources"] = [
                        "direct_current_context_suppressed_by_packet_derived_memory"
                    ]
                    if packet_snapshot_mode in {"degraded", "blocked"}:
                        _append_visible_gap(
                            attachment,
                            _clean_text(packet_snapshot.get("reason")) or "missing_valid_continuation_packet",
                        )
                    if _clean_text(memory_packet_freshness.get("status")) == "stale":
                        _append_visible_gap(
                            attachment,
                            "selected packet-derived memory comes from a stale packet; verify before treating it as current guidance",
                        )
                    attachment["bounded_context"] = {"memory_records": _bounded_memory_delta(active_records)}
                elif promotion.get("reason"):
                    _append_visible_gap(attachment, _clean_text(promotion.get("reason")))

    if attachment["selected_source"] == "direct_current_context" and packet_snapshot_mode == "degraded":
        _append_visible_gap(
            attachment,
            _clean_text(packet_snapshot.get("reason")) or "missing_valid_continuation_packet",
        )

    if packet_snapshot_mode == "blocked":
        attachment["status"] = "blocked"
        attachment["selected_source"] = "none"
        attachment["reason"] = _clean_text(packet_snapshot.get("reason")) or "blocked_packet_snapshot"
        _append_visible_gap(attachment, attachment["reason"])
        attachment["bounded_context"] = {"direct_current_context_facts": direct_facts}

    out_path = attachment_root(repo_root=root) / f"{_attachment_key(spec)}.json"
    _ledger_storage.atomic_write_text(
        out_path,
        json.dumps(attachment, sort_keys=True, indent=2, ensure_ascii=False),
    )
    return {
        "ok": True,
        "context_ref": _attachment_relpath(out_path, repo_root=root),
        "attachment": attachment,
        "reason": _clean_text(attachment.get("reason")),
    }


def continuation_bootstrap_projection_for_job_spec(
    spec: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    as_of_utc: str | None = None,
) -> Dict[str, Any]:
    result = materialize_attachment_for_job_spec(spec, repo_root=repo_root, as_of_utc=as_of_utc)
    attachment = result.get("attachment") if isinstance(result.get("attachment"), dict) else {}
    selection_metadata = attachment.get("selection_metadata") if isinstance(attachment.get("selection_metadata"), dict) else {}
    projection = {
        "status": "none",
        "selected_source": "none",
        "reason": _clean_text(result.get("reason")),
        "context_ref": _clean_text(result.get("context_ref")),
        "visible_gaps": [],
    }
    if attachment:
        projection.update(
            {
                "status": _clean_text(attachment.get("status")) or "none",
                "selected_source": _clean_text(attachment.get("selected_source")) or "none",
                "reason": _clean_text(attachment.get("reason")),
                "visible_gaps": _clean_text_list(selection_metadata.get("visible_gaps")),
            }
        )
    return projection


def context_attachment_ref_for_job_spec(spec: Mapping[str, Any], *, repo_root: Path | None = None) -> str:
    result = materialize_attachment_for_job_spec(spec, repo_root=repo_root)
    return _clean_text(result.get("context_ref"))
