"""Canonical continuation-packet v1 schema helpers.

This module defines the bounded handoff contract for continuation packets.
Packets summarize durable handoff truth for successor work, but they do not
replace substrate-native lifecycle receipts or heavy artifact storage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Tuple

from infra.controlplane import evidence_pack_handoff_refs


SCHEMA_VERSION = "continuation_packet_v1"
CONTRACT_DOC_REL = Path("docs/contracts/continuation_packet_v1.md")
UID = "PIS-20260407-063101Z-A1C1"

PACKET_IDENTITY_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "packet_id",
    "packet_revision",
)

TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "packet_id",
    "packet_revision",
    "created_utc",
    "source_run",
    "handoff_brief",
    "result_summary",
    "evidence_created",
    "artifact_index",
    "open_questions",
    "failed_paths",
    "recommended_next_steps",
    "confidence",
    "applicability",
)

TOP_LEVEL_OPTIONAL_FIELDS: Tuple[str, ...] = ("supersedes_packet_id", "evidence_pack_refs")

TOP_LEVEL_KNOWN_FIELDS = frozenset(TOP_LEVEL_REQUIRED_FIELDS + TOP_LEVEL_OPTIONAL_FIELDS)

NESTED_REQUIRED_FIELDS: Tuple[str, ...] = (
    "source_run.job_id",
    "source_run.run_id",
    "source_run.receipt_refs",
    "result_summary.outcome",
    "result_summary.summary",
    "confidence.level",
    "confidence.rationale",
    "applicability.scope",
    "applicability.limitations",
)

OUTCOME_SET = frozenset({"succeeded", "partial", "failed", "interrupted", "blocked"})
CONFIDENCE_LEVEL_SET = frozenset({"low", "medium", "high"})
APPLICABILITY_SCOPE_SET = frozenset({"immediate_successor", "same_family", "operator_triage", "reference_only"})
NEXT_STEP_PRIORITY_SET = frozenset({"now", "soon", "later"})

FIELD_REQUIREMENT_REQUIRED = "required"
FIELD_REQUIREMENT_OPTIONAL = "optional"

FIELD_REQUIREMENTS: Mapping[str, str] = MappingProxyType(
    {
        "schema_version": FIELD_REQUIREMENT_REQUIRED,
        "packet_id": FIELD_REQUIREMENT_REQUIRED,
        "packet_revision": FIELD_REQUIREMENT_REQUIRED,
        "created_utc": FIELD_REQUIREMENT_REQUIRED,
        "source_run": FIELD_REQUIREMENT_REQUIRED,
        "source_run.job_id": FIELD_REQUIREMENT_REQUIRED,
        "source_run.run_id": FIELD_REQUIREMENT_REQUIRED,
        "source_run.attempt_id": FIELD_REQUIREMENT_OPTIONAL,
        "source_run.receipt_refs": FIELD_REQUIREMENT_REQUIRED,
        "handoff_brief": FIELD_REQUIREMENT_REQUIRED,
        "result_summary": FIELD_REQUIREMENT_REQUIRED,
        "result_summary.outcome": FIELD_REQUIREMENT_REQUIRED,
        "result_summary.summary": FIELD_REQUIREMENT_REQUIRED,
        "evidence_created": FIELD_REQUIREMENT_REQUIRED,
        "artifact_index": FIELD_REQUIREMENT_REQUIRED,
        "open_questions": FIELD_REQUIREMENT_REQUIRED,
        "failed_paths": FIELD_REQUIREMENT_REQUIRED,
        "recommended_next_steps": FIELD_REQUIREMENT_REQUIRED,
        "confidence": FIELD_REQUIREMENT_REQUIRED,
        "confidence.level": FIELD_REQUIREMENT_REQUIRED,
        "confidence.rationale": FIELD_REQUIREMENT_REQUIRED,
        "applicability": FIELD_REQUIREMENT_REQUIRED,
        "applicability.scope": FIELD_REQUIREMENT_REQUIRED,
        "applicability.limitations": FIELD_REQUIREMENT_REQUIRED,
        "supersedes_packet_id": FIELD_REQUIREMENT_OPTIONAL,
        "evidence_pack_refs": FIELD_REQUIREMENT_OPTIONAL,
    }
)

FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "missing_required_top_level_section",
    "missing_source_run_provenance",
    "negative_outcomes_require_failed_paths",
    "truth_authority_override_rejected",
    "embedded_artifact_payload_rejected",
    "missing_or_stale_evidence_pack_ref_rejected",
)

TRUTH_BOUNDARY_AUTHORITIES: Mapping[str, str] = MappingProxyType(
    {
        "packet_summary": "continuation_packet_record",
        "source_run_identity": "substrate_native_receipt_or_result",
        "source_run_status": "substrate_native_receipt_or_result",
        "artifact_payloads": "linked_artifact_storage",
        "operator_or_transcript_prose": "non_authoritative_summary_surface",
        "evidence_pack_payloads": "linked_evidence_pack_storage",
    }
)

TRUTH_BOUNDARY_NOT_AUTHORITATIVE: Mapping[str, str] = MappingProxyType(
    {
        "packet_summary": "raw_lifecycle_status_and_terminal_outcome",
        "source_run_identity": "packet_defined_identity_rewrite",
        "source_run_status": "packet_defined_status_override",
        "artifact_payloads": "embedded_heavy_payload_bytes",
        "operator_or_transcript_prose": "durable_truth_override",
        "evidence_pack_payloads": "embedded_evidence_pack_body_or_pack_schema_copy",
    }
)

MALFORMED_PACKET_REJECTIONS: Tuple[str, ...] = (
    "blank_packet_identity_rejected",
    "non_positive_packet_revision_rejected",
    "missing_result_summary_rejected",
    "missing_confidence_rationale_rejected",
    "missing_applicability_limitations_rejected",
    "negative_outcome_without_failed_paths_rejected",
    "truth_authority_override_rejected",
    "embedded_artifact_payload_rejected",
    "stale_or_missing_evidence_pack_ref_rejected",
)

ERROR_CODES = MappingProxyType(
    {
        "missing_field": "missing_field",
        "invalid_schema_version": "invalid_schema_version",
        "invalid_type": "invalid_type",
        "invalid_value": "invalid_value",
        "unknown_field": "unknown_field",
        "negative_path_required": "negative_path_required",
        "ambiguous_truth_boundary": "ambiguous_truth_boundary",
        "artifact_payload_not_allowed": "artifact_payload_not_allowed",
        "missing_evidence_pack": "missing_evidence_pack",
        "invalid_evidence_pack": "invalid_evidence_pack",
        "fingerprint_mismatch": "fingerprint_mismatch",
    }
)


@dataclass(frozen=True)
class ContinuationPacketContractV1:
    version: str
    uid: str
    canonical_doc_rel: Path
    packet_identity_fields: Tuple[str, ...]
    required_fields: Tuple[str, ...]
    optional_fields: Tuple[str, ...]
    nested_required_fields: Tuple[str, ...]
    field_requirements: Mapping[str, str]
    truth_boundary_authorities: Mapping[str, str]
    truth_boundary_not_authoritative: Mapping[str, str]
    fail_closed_rules: Tuple[str, ...]
    malformed_packet_rejections: Tuple[str, ...]
    outcome_set: frozenset[str]
    confidence_level_set: frozenset[str]
    applicability_scope_set: frozenset[str]


def continuation_packet_contract_v1() -> ContinuationPacketContractV1:
    return ContinuationPacketContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_doc_rel=CONTRACT_DOC_REL,
        packet_identity_fields=PACKET_IDENTITY_FIELDS,
        required_fields=TOP_LEVEL_REQUIRED_FIELDS,
        optional_fields=TOP_LEVEL_OPTIONAL_FIELDS,
        nested_required_fields=NESTED_REQUIRED_FIELDS,
        field_requirements=FIELD_REQUIREMENTS,
        truth_boundary_authorities=TRUTH_BOUNDARY_AUTHORITIES,
        truth_boundary_not_authoritative=TRUTH_BOUNDARY_NOT_AUTHORITATIVE,
        fail_closed_rules=FAIL_CLOSED_RULES,
        malformed_packet_rejections=MALFORMED_PACKET_REJECTIONS,
        outcome_set=OUTCOME_SET,
        confidence_level_set=CONFIDENCE_LEVEL_SET,
        applicability_scope_set=APPLICABILITY_SCOPE_SET,
    )


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = _normalize_text(value)
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items = {_normalize_text(item) for item in value}
    return sorted(item for item in items if item)


def _normalize_source_run(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "job_id": _normalize_text(row.get("job_id")),
        "run_id": _normalize_text(row.get("run_id")),
        "attempt_id": _normalize_text(row.get("attempt_id")),
        "receipt_refs": _normalize_text_list(row.get("receipt_refs")),
    }


def _normalize_result_summary(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "outcome": _normalize_text(row.get("outcome")).lower(),
        "summary": _normalize_text(row.get("summary")),
    }


def _normalize_confidence(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "level": _normalize_text(row.get("level")).lower(),
        "rationale": _normalize_text(row.get("rationale")),
    }


def _normalize_applicability(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "scope": _normalize_text(row.get("scope")).lower(),
        "limitations": _normalize_text_list(row.get("limitations")),
    }


def _normalize_evidence_created(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = raw if isinstance(raw, dict) else {}
        evidence = {
            "evidence_id": _normalize_text(row.get("evidence_id")),
            "kind": _normalize_text(row.get("kind")).lower(),
            "summary": _normalize_text(row.get("summary")),
            "refs": _normalize_text_list(row.get("refs")),
        }
        if any(evidence.values()):
            out.append(evidence)
    out.sort(key=lambda row: (row["evidence_id"], row["kind"], row["summary"]))
    return out


def _normalize_artifact_index(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = raw if isinstance(raw, dict) else {}
        artifact = {
            "artifact_id": _normalize_text(row.get("artifact_id")),
            "kind": _normalize_text(row.get("kind")).lower(),
            "uri": _normalize_text(row.get("uri")),
            "summary": _normalize_text(row.get("summary")),
        }
        if any(artifact.values()):
            out.append(artifact)
    out.sort(key=lambda row: (row["artifact_id"], row["kind"], row["uri"]))
    return out


def _normalize_open_questions(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = raw if isinstance(raw, dict) else {}
        question = {
            "question_id": _normalize_text(row.get("question_id")),
            "question": _normalize_text(row.get("question")),
        }
        if any(question.values()):
            out.append(question)
    out.sort(key=lambda row: (row["question_id"], row["question"]))
    return out


def _normalize_failed_paths(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = raw if isinstance(raw, dict) else {}
        failed_path = {
            "path_id": _normalize_text(row.get("path_id")),
            "summary": _normalize_text(row.get("summary")),
        }
        if any(failed_path.values()):
            out.append(failed_path)
    out.sort(key=lambda row: (row["path_id"], row["summary"]))
    return out


def _normalize_recommended_next_steps(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = raw if isinstance(raw, dict) else {}
        step = {
            "step_id": _normalize_text(row.get("step_id")),
            "priority": _normalize_text(row.get("priority")).lower(),
            "step": _normalize_text(row.get("step")),
        }
        if any(step.values()):
            out.append(step)
    out.sort(key=lambda row: (row["step_id"], row["priority"], row["step"]))
    return out


def normalize_continuation_packet(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    return {
        "schema_version": _normalize_text(obj.get("schema_version") or SCHEMA_VERSION),
        "packet_id": _normalize_text(obj.get("packet_id")),
        "packet_revision": _normalize_positive_int(obj.get("packet_revision")),
        "created_utc": _normalize_text(obj.get("created_utc")),
        "source_run": _normalize_source_run(obj.get("source_run")),
        "handoff_brief": _normalize_text(obj.get("handoff_brief")),
        "result_summary": _normalize_result_summary(obj.get("result_summary")),
        "evidence_created": _normalize_evidence_created(obj.get("evidence_created")),
        "artifact_index": _normalize_artifact_index(obj.get("artifact_index")),
        "open_questions": _normalize_open_questions(obj.get("open_questions")),
        "failed_paths": _normalize_failed_paths(obj.get("failed_paths")),
        "recommended_next_steps": _normalize_recommended_next_steps(obj.get("recommended_next_steps")),
        "confidence": _normalize_confidence(obj.get("confidence")),
        "applicability": _normalize_applicability(obj.get("applicability")),
        "supersedes_packet_id": _normalize_text(obj.get("supersedes_packet_id")),
        "evidence_pack_refs": evidence_pack_handoff_refs.normalize_evidence_pack_refs(obj.get("evidence_pack_refs")),
    }


def canonical_continuation_packet_json(normalized_packet: Mapping[str, Any]) -> str:
    return json.dumps(dict(normalized_packet), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def continuation_packet_fingerprint(normalized_packet: Mapping[str, Any]) -> str:
    raw = canonical_continuation_packet_json(normalized_packet).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def truth_boundary_authority(boundary: str) -> str:
    key = _normalize_text(boundary)
    authority = TRUTH_BOUNDARY_AUTHORITIES.get(key)
    if authority is None:
        raise KeyError(f"unknown truth boundary: {key}")
    return authority


def packet_field_requirement(field_name: str) -> str:
    key = _normalize_text(field_name)
    requirement = FIELD_REQUIREMENTS.get(key)
    if requirement is None:
        raise KeyError(f"unknown continuation packet field: {key}")
    return requirement


def truth_boundary_non_authority(boundary: str) -> str:
    key = _normalize_text(boundary)
    restriction = TRUTH_BOUNDARY_NOT_AUTHORITATIVE.get(key)
    if restriction is None:
        raise KeyError(f"unknown truth boundary: {key}")
    return restriction


def validate_continuation_packet(raw_packet: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    raw = dict(raw_packet or {})
    normalized = normalize_continuation_packet(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    unknown_top_level = sorted(set(raw.keys()) - TOP_LEVEL_KNOWN_FIELDS)
    for key in unknown_top_level:
        warnings.append(
            {
                "code": ERROR_CODES["unknown_field"],
                "field": str(key),
                "message": f"unknown top-level field ignored for compatibility: {key}",
            }
        )

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value) or (isinstance(value, list) and not value):
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["invalid_schema_version"],
                "field": "schema_version",
                "message": f"schema_version must be {SCHEMA_VERSION}",
            }
        )
    if normalized["packet_revision"] is None:
        errors.append(
            {
                "code": ERROR_CODES["invalid_type"],
                "field": "packet_revision",
                "message": "packet_revision must be a positive integer",
            }
        )
    if not normalized["source_run"].get("job_id"):
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "source_run.job_id",
                "message": "source_run.job_id is required",
            }
        )
    if not normalized["source_run"].get("run_id"):
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "source_run.run_id",
                "message": "source_run.run_id is required",
            }
        )
    if not normalized["source_run"].get("receipt_refs"):
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "source_run.receipt_refs",
                "message": "source_run.receipt_refs must include at least one linked lifecycle receipt or result ref",
            }
        )

    outcome = normalized["result_summary"].get("outcome", "")
    if outcome not in OUTCOME_SET:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "result_summary.outcome",
                "message": f"result_summary.outcome must be one of: {', '.join(sorted(OUTCOME_SET))}",
            }
        )
    if not normalized["result_summary"].get("summary"):
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "result_summary.summary",
                "message": "result_summary.summary is required",
            }
        )

    if outcome in {"failed", "interrupted", "blocked"} and not normalized["failed_paths"]:
        errors.append(
            {
                "code": ERROR_CODES["negative_path_required"],
                "field": "failed_paths",
                "message": "negative outcomes require at least one failed_paths entry",
            }
        )

    for idx, row in enumerate(normalized["evidence_created"]):
        for field in ("evidence_id", "kind", "summary"):
            if not row.get(field):
                errors.append(
                    {
                        "code": ERROR_CODES["missing_field"],
                        "field": f"evidence_created[{idx}].{field}",
                        "message": f"evidence_created[{idx}].{field} is required",
                    }
                )

    for idx, row in enumerate(normalized["artifact_index"]):
        for field in ("artifact_id", "kind", "uri", "summary"):
            if not row.get(field):
                errors.append(
                    {
                        "code": ERROR_CODES["missing_field"],
                        "field": f"artifact_index[{idx}].{field}",
                        "message": f"artifact_index[{idx}].{field} is required",
                    }
                )
        raw_row = raw.get("artifact_index")[idx] if isinstance(raw.get("artifact_index"), list) and idx < len(raw.get("artifact_index")) else {}
        if isinstance(raw_row, dict) and _normalize_text(raw_row.get("content")):
            errors.append(
                {
                    "code": ERROR_CODES["artifact_payload_not_allowed"],
                    "field": f"artifact_index[{idx}].content",
                    "message": "artifact_index rows must link to artifacts; embedded payload content is not allowed",
                }
            )

    for idx, row in enumerate(normalized["open_questions"]):
        for field in ("question_id", "question"):
            if not row.get(field):
                errors.append(
                    {
                        "code": ERROR_CODES["missing_field"],
                        "field": f"open_questions[{idx}].{field}",
                        "message": f"open_questions[{idx}].{field} is required",
                    }
                )

    for idx, row in enumerate(normalized["failed_paths"]):
        for field in ("path_id", "summary"):
            if not row.get(field):
                errors.append(
                    {
                        "code": ERROR_CODES["missing_field"],
                        "field": f"failed_paths[{idx}].{field}",
                        "message": f"failed_paths[{idx}].{field} is required",
                    }
                )

    for idx, row in enumerate(normalized["recommended_next_steps"]):
        for field in ("step_id", "priority", "step"):
            if not row.get(field):
                errors.append(
                    {
                        "code": ERROR_CODES["missing_field"],
                        "field": f"recommended_next_steps[{idx}].{field}",
                        "message": f"recommended_next_steps[{idx}].{field} is required",
                    }
                )
        if row.get("priority") and row["priority"] not in NEXT_STEP_PRIORITY_SET:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_value"],
                    "field": f"recommended_next_steps[{idx}].priority",
                    "message": f"priority must be one of: {', '.join(sorted(NEXT_STEP_PRIORITY_SET))}",
                }
            )

    confidence = normalized["confidence"]
    if confidence.get("level") not in CONFIDENCE_LEVEL_SET:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "confidence.level",
                "message": f"confidence.level must be one of: {', '.join(sorted(CONFIDENCE_LEVEL_SET))}",
            }
        )
    if not confidence.get("rationale"):
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "confidence.rationale",
                "message": "confidence.rationale is required",
            }
        )

    applicability = normalized["applicability"]
    if applicability.get("scope") not in APPLICABILITY_SCOPE_SET:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "applicability.scope",
                "message": f"applicability.scope must be one of: {', '.join(sorted(APPLICABILITY_SCOPE_SET))}",
            }
        )
    if not applicability.get("limitations"):
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "applicability.limitations",
                "message": "applicability.limitations must declare at least one limitation or boundary",
            }
        )

    if _normalize_text(raw.get("truth_authority_override")):
        errors.append(
            {
                "code": ERROR_CODES["ambiguous_truth_boundary"],
                "field": "truth_authority_override",
                "message": "continuation packets cannot override lifecycle or artifact truth authority",
            }
        )

    errors.extend(
        evidence_pack_handoff_refs.validate_evidence_pack_refs(
            normalized["evidence_pack_refs"],
            repo_root=repo_root,
            field_prefix="evidence_pack_refs",
        )
    )

    return {
        "ok": not errors,
        "failure_class": "" if not errors else "continuation_packet_validation_failed",
        "errors": errors,
        "warnings": warnings,
        "normalized_packet": normalized,
        "computed_fingerprint": continuation_packet_fingerprint(normalized),
    }


def validate_continuation_packet_contract_v1() -> Tuple[str, ...]:
    issues = []
    contract = continuation_packet_contract_v1()
    if contract.uid != UID:
        issues.append("uid must remain bound to PIS-20260407-063101Z-A1C1")
    if contract.packet_identity_fields != PACKET_IDENTITY_FIELDS:
        issues.append("packet identity fields must match continuation_packet_v1 exactly")
    if contract.required_fields != TOP_LEVEL_REQUIRED_FIELDS:
        issues.append("required fields must match continuation_packet_v1 exactly")
    if contract.optional_fields != TOP_LEVEL_OPTIONAL_FIELDS:
        issues.append("optional fields must match continuation_packet_v1 exactly")
    if contract.nested_required_fields != NESTED_REQUIRED_FIELDS:
        issues.append("nested required fields must match continuation_packet_v1 exactly")
    if dict(contract.field_requirements) != dict(FIELD_REQUIREMENTS):
        issues.append("field requirements must match continuation_packet_v1 exactly")
    if set(contract.truth_boundary_authorities) != set(TRUTH_BOUNDARY_AUTHORITIES):
        issues.append("truth boundaries must match the v1 continuation-packet contract exactly")
    if set(contract.truth_boundary_not_authoritative) != set(TRUTH_BOUNDARY_NOT_AUTHORITATIVE):
        issues.append("truth-boundary non-authoritative scopes must match the v1 continuation-packet contract exactly")
    if contract.fail_closed_rules != FAIL_CLOSED_RULES:
        issues.append("fail-closed rules must match continuation_packet_v1 exactly")
    if contract.malformed_packet_rejections != MALFORMED_PACKET_REJECTIONS:
        issues.append("malformed packet rejections must match continuation_packet_v1 exactly")
    return tuple(issues)
