"""Packet-backed long-horizon memory promotion and correction policy."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from infra.controlplane import continuation_packet_contract as packet_contract


SCHEMA_VERSION = "continuation_long_horizon_memory_v1"
CONTRACT_DOC_REL = Path("docs/contracts/continuation_long_horizon_memory_v1.md")

PROMOTABLE_APPLICABILITY_SCOPES = frozenset({"immediate_successor", "same_family"})
MEMORY_KIND_SET = frozenset({"negative_finding", "open_question", "recommended_next_step"})
CORRECTION_STATE_SET = frozenset({"active", "contradicted", "withdrawn"})

RETENTION_POLICY_BY_KIND: Mapping[str, Dict[str, Any]] = {
    "negative_finding": {"policy_name": "packet_negative_finding_v1", "retention_days": 30},
    "open_question": {"policy_name": "packet_open_question_v1", "retention_days": 14},
    "recommended_next_step": {"policy_name": "packet_recommended_next_step_v1", "retention_days": 7},
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_text_list(value: Any) -> List[str]:
    rows = value if isinstance(value, list) else []
    deduped = {_clean_text(item) for item in rows}
    return sorted(item for item in deduped if item)


def _parse_utc(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1] + "+00:00").astimezone(timezone.utc)
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _add_days(value: str, *, days: int) -> str:
    parsed = _parse_utc(value)
    if parsed is None:
        raise ValueError(f"invalid UTC timestamp: {value!r}")
    return _utc_iso(parsed + timedelta(days=days))


def _stable_memory_id(*, packet_id: str, packet_revision: int, memory_kind: str, item_id: str) -> str:
    return f"{packet_id}:r{packet_revision}:{memory_kind}:{item_id}"


def _provenance_refs(packet: Mapping[str, Any]) -> List[str]:
    refs: List[str] = []
    source_run = packet.get("source_run") if isinstance(packet.get("source_run"), dict) else {}
    refs.extend(_clean_text_list(source_run.get("receipt_refs")))
    for evidence in list(packet.get("evidence_created") or []):
        if isinstance(evidence, dict):
            refs.extend(_clean_text_list(evidence.get("refs")))
    for artifact in list(packet.get("artifact_index") or []):
        if isinstance(artifact, dict):
            uri = _clean_text(artifact.get("uri"))
            if uri:
                refs.append(uri)
    return sorted({ref for ref in refs if ref})


def _base_memory_record(
    *,
    packet: Mapping[str, Any],
    memory_kind: str,
    item_id: str,
    summary: str,
    promoted_at_utc: str,
    policy_name: str,
    retention_days: int,
    provenance_refs: List[str],
) -> Dict[str, Any]:
    applicability = packet.get("applicability") if isinstance(packet.get("applicability"), dict) else {}
    source_run = packet.get("source_run") if isinstance(packet.get("source_run"), dict) else {}
    packet_id = _clean_text(packet.get("packet_id"))
    packet_revision = int(packet.get("packet_revision") or 0)
    return {
        "schema_version": SCHEMA_VERSION,
        "memory_id": _stable_memory_id(
            packet_id=packet_id,
            packet_revision=packet_revision,
            memory_kind=memory_kind,
            item_id=item_id,
        ),
        "memory_kind": memory_kind,
        "summary": _clean_text(summary),
        "source_packet": {
            "packet_id": packet_id,
            "packet_revision": packet_revision,
            "created_utc": _clean_text(packet.get("created_utc")),
            "source_run": {
                "job_id": _clean_text(source_run.get("job_id")),
                "run_id": _clean_text(source_run.get("run_id")),
                "attempt_id": _clean_text(source_run.get("attempt_id")),
                "receipt_refs": _clean_text_list(source_run.get("receipt_refs")),
            },
        },
        "provenance_refs": list(provenance_refs),
        "applicability": {
            "scope": _clean_text(applicability.get("scope")).lower(),
            "limitations": _clean_text_list(applicability.get("limitations")),
        },
        "correction": {
            "state": "active",
            "reason": "",
            "recorded_at_utc": "",
            "corrected_by_packet_id": "",
        },
        "retention": {
            "policy_name": policy_name,
            "retention_days": retention_days,
            "promoted_at_utc": promoted_at_utc,
            "expires_at_utc": _add_days(promoted_at_utc, days=retention_days),
        },
    }


def normalize_memory_record(raw_record: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_record or {})
    source_packet = raw.get("source_packet") if isinstance(raw.get("source_packet"), dict) else {}
    source_run = source_packet.get("source_run") if isinstance(source_packet.get("source_run"), dict) else {}
    applicability = raw.get("applicability") if isinstance(raw.get("applicability"), dict) else {}
    correction = raw.get("correction") if isinstance(raw.get("correction"), dict) else {}
    retention = raw.get("retention") if isinstance(raw.get("retention"), dict) else {}
    retention_days = retention.get("retention_days")
    if isinstance(retention_days, bool):
        retention_days = None
    elif isinstance(retention_days, str) and retention_days.strip().isdigit():
        retention_days = int(retention_days.strip())
    elif not isinstance(retention_days, int):
        retention_days = None

    return {
        "schema_version": _clean_text(raw.get("schema_version")),
        "memory_id": _clean_text(raw.get("memory_id")),
        "memory_kind": _clean_text(raw.get("memory_kind")).lower(),
        "summary": _clean_text(raw.get("summary")),
        "source_packet": {
            "packet_id": _clean_text(source_packet.get("packet_id")),
            "packet_revision": int(source_packet["packet_revision"]) if isinstance(source_packet.get("packet_revision"), int) else None,
            "created_utc": _clean_text(source_packet.get("created_utc")),
            "source_run": {
                "job_id": _clean_text(source_run.get("job_id")),
                "run_id": _clean_text(source_run.get("run_id")),
                "attempt_id": _clean_text(source_run.get("attempt_id")),
                "receipt_refs": _clean_text_list(source_run.get("receipt_refs")),
            },
        },
        "provenance_refs": _clean_text_list(raw.get("provenance_refs")),
        "applicability": {
            "scope": _clean_text(applicability.get("scope")).lower(),
            "limitations": _clean_text_list(applicability.get("limitations")),
        },
        "correction": {
            "state": _clean_text(correction.get("state")).lower(),
            "reason": _clean_text(correction.get("reason")),
            "recorded_at_utc": _clean_text(correction.get("recorded_at_utc")),
            "corrected_by_packet_id": _clean_text(correction.get("corrected_by_packet_id")),
        },
        "retention": {
            "policy_name": _clean_text(retention.get("policy_name")),
            "retention_days": retention_days,
            "promoted_at_utc": _clean_text(retention.get("promoted_at_utc")),
            "expires_at_utc": _clean_text(retention.get("expires_at_utc")),
        },
    }


def validate_memory_record(raw_record: Mapping[str, Any]) -> Dict[str, Any]:
    record = normalize_memory_record(raw_record)
    errors: List[Dict[str, str]] = []

    for field in ("schema_version", "memory_id", "memory_kind", "summary", "provenance_refs"):
        value = record.get(field)
        if value in ("", None) or (isinstance(value, list) and not value):
            errors.append({"field": field, "code": "missing_field", "message": f"{field} is required"})

    if record["schema_version"] != SCHEMA_VERSION:
        errors.append({"field": "schema_version", "code": "invalid_value", "message": f"schema_version must be {SCHEMA_VERSION}"})
    if record["memory_kind"] not in MEMORY_KIND_SET:
        errors.append({"field": "memory_kind", "code": "invalid_value", "message": "memory_kind is not supported"})

    source_packet = record["source_packet"]
    if not source_packet["packet_id"]:
        errors.append({"field": "source_packet.packet_id", "code": "missing_field", "message": "source_packet.packet_id is required"})
    if source_packet["packet_revision"] is None:
        errors.append({"field": "source_packet.packet_revision", "code": "invalid_value", "message": "source_packet.packet_revision must be a positive integer"})
    if _parse_utc(source_packet["created_utc"]) is None:
        errors.append({"field": "source_packet.created_utc", "code": "invalid_timestamp", "message": "source_packet.created_utc must be a valid UTC timestamp"})
    source_run = source_packet["source_run"]
    if not source_run["job_id"]:
        errors.append({"field": "source_packet.source_run.job_id", "code": "missing_field", "message": "source_packet.source_run.job_id is required"})
    if not source_run["run_id"]:
        errors.append({"field": "source_packet.source_run.run_id", "code": "missing_field", "message": "source_packet.source_run.run_id is required"})
    if not source_run["receipt_refs"]:
        errors.append({"field": "source_packet.source_run.receipt_refs", "code": "missing_field", "message": "source packet receipt provenance is required"})

    correction = record["correction"]
    if correction["state"] not in CORRECTION_STATE_SET:
        errors.append({"field": "correction.state", "code": "invalid_value", "message": "correction.state is not supported"})
    if correction["state"] != "active":
        if not correction["reason"]:
            errors.append({"field": "correction.reason", "code": "missing_field", "message": "inactive memory requires an explicit correction or withdrawal reason"})
        if _parse_utc(correction["recorded_at_utc"]) is None:
            errors.append({"field": "correction.recorded_at_utc", "code": "invalid_timestamp", "message": "inactive memory requires a valid recorded_at_utc"})

    retention = record["retention"]
    if not retention["policy_name"]:
        errors.append({"field": "retention.policy_name", "code": "missing_field", "message": "retention.policy_name is required"})
    if not isinstance(retention["retention_days"], int) or retention["retention_days"] <= 0:
        errors.append({"field": "retention.retention_days", "code": "invalid_value", "message": "retention.retention_days must be a positive integer"})
    if _parse_utc(retention["promoted_at_utc"]) is None:
        errors.append({"field": "retention.promoted_at_utc", "code": "invalid_timestamp", "message": "retention.promoted_at_utc must be a valid UTC timestamp"})
    if _parse_utc(retention["expires_at_utc"]) is None:
        errors.append({"field": "retention.expires_at_utc", "code": "invalid_timestamp", "message": "retention.expires_at_utc must be a valid UTC timestamp"})

    return {"ok": not errors, "errors": errors, "normalized_record": record}


def promote_packet_to_memory_records(
    raw_packet: Mapping[str, Any],
    *,
    promoted_at_utc: str,
) -> Dict[str, Any]:
    packet_validation = packet_contract.validate_continuation_packet(raw_packet)
    if not packet_validation["ok"]:
        return {
            "ok": False,
            "errors": [{"field": row["field"], "code": row["code"], "message": row["message"]} for row in packet_validation["errors"]],
            "warnings": [],
            "records": [],
            "reason": "packet_validation_failed",
        }
    packet = dict(packet_validation["normalized_packet"])
    applicability = dict(packet.get("applicability") or {})
    scope = _clean_text(applicability.get("scope")).lower()
    if scope not in PROMOTABLE_APPLICABILITY_SCOPES:
        return {
            "ok": True,
            "errors": [],
            "warnings": [{"field": "applicability.scope", "code": "not_promotable", "message": f"packet scope {scope or '<missing>'} is packet-local only"}],
            "records": [],
            "reason": "packet_scope_not_promotable",
        }

    provenance_refs = _provenance_refs(packet)
    if not provenance_refs:
        return {
            "ok": False,
            "errors": [{"field": "provenance_refs", "code": "missing_field", "message": "promoted memory requires durable packet provenance"}],
            "warnings": [],
            "records": [],
            "reason": "missing_memory_provenance",
        }

    if _parse_utc(promoted_at_utc) is None:
        return {
            "ok": False,
            "errors": [{"field": "promoted_at_utc", "code": "invalid_timestamp", "message": "promoted_at_utc must be a valid UTC timestamp"}],
            "warnings": [],
            "records": [],
            "reason": "invalid_promotion_timestamp",
        }

    records: List[Dict[str, Any]] = []
    for row in list(packet.get("failed_paths") or []):
        item = row if isinstance(row, dict) else {}
        item_id = _clean_text(item.get("path_id"))
        summary = _clean_text(item.get("summary"))
        if not item_id or not summary:
            continue
        policy = RETENTION_POLICY_BY_KIND["negative_finding"]
        records.append(
            _base_memory_record(
                packet=packet,
                memory_kind="negative_finding",
                item_id=item_id,
                summary=summary,
                promoted_at_utc=promoted_at_utc,
                policy_name=str(policy["policy_name"]),
                retention_days=int(policy["retention_days"]),
                provenance_refs=provenance_refs,
            )
        )
    for row in list(packet.get("open_questions") or []):
        item = row if isinstance(row, dict) else {}
        item_id = _clean_text(item.get("question_id"))
        summary = _clean_text(item.get("question"))
        if not item_id or not summary:
            continue
        policy = RETENTION_POLICY_BY_KIND["open_question"]
        records.append(
            _base_memory_record(
                packet=packet,
                memory_kind="open_question",
                item_id=item_id,
                summary=summary,
                promoted_at_utc=promoted_at_utc,
                policy_name=str(policy["policy_name"]),
                retention_days=int(policy["retention_days"]),
                provenance_refs=provenance_refs,
            )
        )
    for row in list(packet.get("recommended_next_steps") or []):
        item = row if isinstance(row, dict) else {}
        item_id = _clean_text(item.get("step_id"))
        summary = _clean_text(item.get("step"))
        if not item_id or not summary:
            continue
        policy = RETENTION_POLICY_BY_KIND["recommended_next_step"]
        records.append(
            _base_memory_record(
                packet=packet,
                memory_kind="recommended_next_step",
                item_id=item_id,
                summary=summary,
                promoted_at_utc=promoted_at_utc,
                policy_name=str(policy["policy_name"]),
                retention_days=int(policy["retention_days"]),
                provenance_refs=provenance_refs,
            )
        )

    errors: List[Dict[str, str]] = []
    for record in records:
        validation = validate_memory_record(record)
        if not validation["ok"]:
            errors.extend(validation["errors"])

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": [],
        "records": records,
        "reason": "" if records else "no_promotable_packet_facts",
    }


def evaluate_memory_record_activity(raw_record: Mapping[str, Any], *, as_of_utc: str) -> Dict[str, Any]:
    validation = validate_memory_record(raw_record)
    if not validation["ok"]:
        return {"active": False, "status": "invalid", "reason": "memory_record_validation_failed", "errors": validation["errors"]}
    record = validation["normalized_record"]
    correction = record["correction"]
    if correction["state"] == "contradicted":
        return {"active": False, "status": "contradicted", "reason": correction["reason"] or "memory_contradicted", "errors": []}
    if correction["state"] == "withdrawn":
        return {"active": False, "status": "withdrawn", "reason": correction["reason"] or "memory_withdrawn", "errors": []}
    as_of = _parse_utc(as_of_utc)
    expires_at = _parse_utc(record["retention"]["expires_at_utc"])
    if as_of is None or expires_at is None:
        return {"active": False, "status": "invalid", "reason": "memory_record_validation_failed", "errors": validation["errors"]}
    if as_of >= expires_at:
        return {"active": False, "status": "expired", "reason": "retention_elapsed", "errors": []}
    return {"active": True, "status": "active", "reason": "eligible_guidance", "errors": []}


def apply_memory_correction(
    raw_record: Mapping[str, Any],
    *,
    new_state: str,
    reason: str,
    recorded_at_utc: str,
    corrected_by_packet_id: str = "",
) -> Dict[str, Any]:
    validation = validate_memory_record(raw_record)
    if not validation["ok"]:
        return {"ok": False, "errors": validation["errors"], "record": None}
    canonical_state = _clean_text(new_state).lower()
    if canonical_state not in {"contradicted", "withdrawn"}:
        return {"ok": False, "errors": [{"field": "new_state", "code": "invalid_value", "message": "new_state must be contradicted or withdrawn"}], "record": None}
    if _parse_utc(recorded_at_utc) is None:
        return {"ok": False, "errors": [{"field": "recorded_at_utc", "code": "invalid_timestamp", "message": "recorded_at_utc must be a valid UTC timestamp"}], "record": None}
    if not _clean_text(reason):
        return {"ok": False, "errors": [{"field": "reason", "code": "missing_field", "message": "reason is required"}], "record": None}
    record = deepcopy(validation["normalized_record"])
    record["correction"]["state"] = canonical_state
    record["correction"]["reason"] = _clean_text(reason)
    record["correction"]["recorded_at_utc"] = _clean_text(recorded_at_utc)
    record["correction"]["corrected_by_packet_id"] = _clean_text(corrected_by_packet_id)
    post = validate_memory_record(record)
    return {"ok": post["ok"], "errors": post["errors"], "record": record if post["ok"] else None}
