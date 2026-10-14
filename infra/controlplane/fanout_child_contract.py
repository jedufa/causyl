"""Fanout child work-order and return-packet contract helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Tuple

from infra.controlplane import context_bundles as _context_bundles
from infra.controlplane import fanout_master_contract as _master_contract


SCHEMA_VERSION = "fanout_child_contract_v1"
CONTRACT_ARTIFACT_REL = Path("governance/fanout_child_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_fanout_child_execution_v1.md")
UID = "PIS-20260409-082101Z-B2G2"

WORK_ORDER_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "work_order_id",
    "parent_uid",
    "child_uid",
    "child_role",
    "scope_fence",
    "ownership_fence",
    "required_context_bundle",
    "additional_context_refs",
    "forbidden_context_refs",
    "expected_evidence",
    "next_decision",
)
WORK_ORDER_OPTIONAL_FIELDS: Tuple[str, ...] = ("continuation_packet_ref",)
WORK_ORDER_KNOWN_FIELDS = frozenset(WORK_ORDER_REQUIRED_FIELDS + WORK_ORDER_OPTIONAL_FIELDS)

RETURN_PACKET_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "packet_id",
    "work_order_id",
    "parent_uid",
    "child_uid",
    "status",
    "summary",
    "scope_fence",
    "ownership_fence",
    "evidence_summary",
    "changed_files",
    "caveats",
    "unresolved_questions",
    "next_decision",
)
RETURN_PACKET_OPTIONAL_FIELDS: Tuple[str, ...] = ("error_summary",)
RETURN_PACKET_KNOWN_FIELDS = frozenset(RETURN_PACKET_REQUIRED_FIELDS + RETURN_PACKET_OPTIONAL_FIELDS)

WORK_ORDER_STATUSLESS_REQUIRED_NESTED_FIELDS: Tuple[str, ...] = (
    "scope_fence.objective",
    "scope_fence.in_scope",
    "scope_fence.out_of_scope",
    "ownership_fence.owned_paths",
    "ownership_fence.forbidden_parent_claims",
    "next_decision.decision_owner",
    "next_decision.question",
)
RETURN_PACKET_REQUIRED_NESTED_FIELDS: Tuple[str, ...] = (
    "scope_fence.objective",
    "scope_fence.in_scope",
    "scope_fence.out_of_scope",
    "ownership_fence.owned_paths",
    "ownership_fence.forbidden_parent_claims",
    "next_decision.decision_owner",
    "next_decision.question",
    "next_decision.recommended_action",
)

RETURN_STATUS_SET = frozenset({"succeeded", "partial", "blocked", "failed"})
EVIDENCE_STATUS_SET = frozenset({"provided", "missing", "not_applicable"})
WORK_ORDER_ALLOW_EMPTY_LIST_FIELDS = frozenset({"additional_context_refs", "forbidden_context_refs"})
WORK_ORDER_ALLOW_EMPTY_NESTED_LIST_FIELDS = frozenset({"ownership_fence.owned_paths"})
RETURN_PACKET_ALLOW_EMPTY_LIST_FIELDS = frozenset({"changed_files", "caveats", "unresolved_questions"})
RETURN_PACKET_ALLOW_EMPTY_NESTED_LIST_FIELDS = frozenset({"ownership_fence.owned_paths"})
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "missing_scope_fence_rejected",
    "missing_evidence_summary_rejected",
    "contradictory_child_status_rejected",
    "undeclared_file_ownership_rejected",
    "forbidden_parent_claim_rejected",
    "forbidden_bundle_ref_rejected",
)

ERROR_CODES = MappingProxyType(
    {
        "missing_field": "missing_field",
        "invalid_type": "invalid_type",
        "invalid_value": "invalid_value",
        "unknown_field": "unknown_field",
        "contradictory_context_refs": "contradictory_context_refs",
        "forbidden_bundle_ref": "forbidden_bundle_ref",
        "ownership_violation": "ownership_violation",
        "missing_evidence_summary": "missing_evidence_summary",
        "contradictory_status": "contradictory_status",
        "forbidden_parent_claim": "forbidden_parent_claim",
        "scope_fence_mismatch": "scope_fence_mismatch",
        "ownership_fence_mismatch": "ownership_fence_mismatch",
    }
)


@dataclass(frozen=True)
class FanoutChildContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    work_order_required_fields: Tuple[str, ...]
    work_order_optional_fields: Tuple[str, ...]
    return_packet_required_fields: Tuple[str, ...]
    return_packet_optional_fields: Tuple[str, ...]
    required_context_bundle_rule: str
    fail_closed_rules: Tuple[str, ...]
    forbidden_parent_claims: Tuple[str, ...]
    return_statuses: frozenset[str]
    evidence_statuses: frozenset[str]


def fanout_child_contract_v1() -> FanoutChildContractV1:
    return FanoutChildContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        work_order_required_fields=WORK_ORDER_REQUIRED_FIELDS,
        work_order_optional_fields=WORK_ORDER_OPTIONAL_FIELDS,
        return_packet_required_fields=RETURN_PACKET_REQUIRED_FIELDS,
        return_packet_optional_fields=RETURN_PACKET_OPTIONAL_FIELDS,
        required_context_bundle_rule="bundle_plus_delta_minus_forbidden",
        fail_closed_rules=FAIL_CLOSED_RULES,
        forbidden_parent_claims=_master_contract.FORBIDDEN_CHILD_CLAIMS,
        return_statuses=RETURN_STATUS_SET,
        evidence_statuses=EVIDENCE_STATUS_SET,
    )


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _normalize_text(value).lower()
    return text in {"1", "true", "yes"}


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _normalize_scope_fence(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "objective": _normalize_text(row.get("objective")),
        "in_scope": _normalize_text_list(row.get("in_scope")),
        "out_of_scope": _normalize_text_list(row.get("out_of_scope")),
    }


def _normalize_ownership_fence(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    forbidden_claims = _normalize_text_list(row.get("forbidden_parent_claims"))
    if not forbidden_claims:
        forbidden_claims = list(_master_contract.FORBIDDEN_CHILD_CLAIMS)
    return {
        "owned_paths": _normalize_text_list(row.get("owned_paths")),
        "forbidden_parent_claims": forbidden_claims,
    }


def _normalize_expected_evidence(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = raw if isinstance(raw, Mapping) else {}
        normalized = {
            "evidence_id": _normalize_text(row.get("evidence_id")),
            "kind": _normalize_text(row.get("kind")).lower(),
            "summary": _normalize_text(row.get("summary")),
            "required": _normalize_bool(row.get("required", True)),
        }
        if any(
            (
                normalized["evidence_id"],
                normalized["kind"],
                normalized["summary"],
                normalized["required"],
            )
        ):
            out.append(normalized)
    out.sort(key=lambda row: (row["evidence_id"], row["kind"], row["summary"]))
    return out


def _normalize_next_decision(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "decision_owner": _normalize_text(row.get("decision_owner")),
        "question": _normalize_text(row.get("question")),
        "recommended_action": _normalize_text(row.get("recommended_action")),
    }


def _normalize_evidence_summary(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = raw if isinstance(raw, Mapping) else {}
        normalized = {
            "evidence_id": _normalize_text(row.get("evidence_id")),
            "status": _normalize_text(row.get("status")).lower(),
            "summary": _normalize_text(row.get("summary")),
            "refs": _normalize_text_list(row.get("refs")),
        }
        if any(
            (
                normalized["evidence_id"],
                normalized["status"],
                normalized["summary"],
                normalized["refs"],
            )
        ):
            out.append(normalized)
    out.sort(key=lambda row: (row["evidence_id"], row["status"], row["summary"]))
    return out


def normalize_child_work_order(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    return {
        "schema_version": _normalize_text(obj.get("schema_version") or SCHEMA_VERSION),
        "work_order_id": _normalize_text(obj.get("work_order_id")),
        "parent_uid": _normalize_text(obj.get("parent_uid")),
        "child_uid": _normalize_text(obj.get("child_uid")),
        "child_role": _normalize_text(obj.get("child_role")),
        "scope_fence": _normalize_scope_fence(obj.get("scope_fence")),
        "ownership_fence": _normalize_ownership_fence(obj.get("ownership_fence")),
        "required_context_bundle": _normalize_text(obj.get("required_context_bundle")),
        "additional_context_refs": _normalize_text_list(obj.get("additional_context_refs")),
        "forbidden_context_refs": _normalize_text_list(obj.get("forbidden_context_refs")),
        "expected_evidence": _normalize_expected_evidence(obj.get("expected_evidence")),
        "next_decision": _normalize_next_decision(obj.get("next_decision")),
        "continuation_packet_ref": _normalize_text(obj.get("continuation_packet_ref")),
    }


def normalize_child_return_packet(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    return {
        "schema_version": _normalize_text(obj.get("schema_version") or SCHEMA_VERSION),
        "packet_id": _normalize_text(obj.get("packet_id")),
        "work_order_id": _normalize_text(obj.get("work_order_id")),
        "parent_uid": _normalize_text(obj.get("parent_uid")),
        "child_uid": _normalize_text(obj.get("child_uid")),
        "status": _normalize_text(obj.get("status")).lower(),
        "summary": _normalize_text(obj.get("summary")),
        "scope_fence": _normalize_scope_fence(obj.get("scope_fence")),
        "ownership_fence": _normalize_ownership_fence(obj.get("ownership_fence")),
        "evidence_summary": _normalize_evidence_summary(obj.get("evidence_summary")),
        "changed_files": _normalize_text_list(obj.get("changed_files")),
        "caveats": _normalize_text_list(obj.get("caveats")),
        "unresolved_questions": _normalize_text_list(obj.get("unresolved_questions")),
        "next_decision": _normalize_next_decision(obj.get("next_decision")),
        "error_summary": _normalize_text(obj.get("error_summary")),
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _path_is_owned(path: str, owned_paths: List[str]) -> bool:
    normalized_path = _normalize_text(path).lstrip("./")
    if not normalized_path:
        return False
    candidate = PurePosixPath(normalized_path)
    for allowed in owned_paths:
        allowed_text = _normalize_text(allowed).lstrip("./").rstrip("/")
        if not allowed_text:
            continue
        allowed_path = PurePosixPath(allowed_text)
        if candidate == allowed_path:
            return True
        try:
            candidate.relative_to(allowed_path)
            return True
        except ValueError:
            continue
    return False


def validate_child_work_order(raw_work_order: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_work_order or {})
    normalized = normalize_child_work_order(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    unknown_fields = sorted(set(raw.keys()) - WORK_ORDER_KNOWN_FIELDS)
    for key in unknown_fields:
        warnings.append(
            {
                "code": ERROR_CODES["unknown_field"],
                "field": key,
                "message": f"unknown top-level field ignored for compatibility: {key}",
            }
        )

    for field in WORK_ORDER_REQUIRED_FIELDS:
        if field not in raw:
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})
            continue
        value = normalized.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value):
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})
        elif isinstance(value, list) and not value and field not in WORK_ORDER_ALLOW_EMPTY_LIST_FIELDS:
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "schema_version",
                "message": f"schema_version must be {SCHEMA_VERSION}",
            }
        )

    if normalized["required_context_bundle"] not in _context_bundles.available_bundle_ids():
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "required_context_bundle",
                "message": "required_context_bundle must name a registered context bundle",
            }
        )

    scope_fence = normalized["scope_fence"]
    for field in WORK_ORDER_STATUSLESS_REQUIRED_NESTED_FIELDS:
        top, nested = field.split(".", 1)
        value = scope_fence.get(nested) if top == "scope_fence" else None
        if top == "ownership_fence":
            value = normalized["ownership_fence"].get(nested)
        elif top == "next_decision":
            value = normalized["next_decision"].get(nested)
        if value in ("", None):
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})
        elif isinstance(value, list) and not value and field not in WORK_ORDER_ALLOW_EMPTY_NESTED_LIST_FIELDS:
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})

    missing_forbidden_claims = sorted(
        set(_master_contract.FORBIDDEN_CHILD_CLAIMS) - set(normalized["ownership_fence"]["forbidden_parent_claims"])
    )
    if missing_forbidden_claims:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "ownership_fence.forbidden_parent_claims",
                "message": "ownership_fence.forbidden_parent_claims must include all master-owned forbidden claims",
            }
        )

    if not any(row.get("required") for row in normalized["expected_evidence"]):
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "expected_evidence",
                "message": "expected_evidence must contain at least one required evidence obligation",
            }
        )
    for idx, row in enumerate(normalized["expected_evidence"]):
        for field in ("evidence_id", "kind", "summary"):
            if not row.get(field):
                errors.append(
                    {
                        "code": ERROR_CODES["missing_field"],
                        "field": f"expected_evidence[{idx}].{field}",
                        "message": f"expected_evidence[{idx}].{field} is required",
                    }
                )

    additional_refs = set(normalized["additional_context_refs"])
    forbidden_refs = set(normalized["forbidden_context_refs"])
    overlap = sorted(additional_refs & forbidden_refs)
    if overlap:
        errors.append(
            {
                "code": ERROR_CODES["contradictory_context_refs"],
                "field": "forbidden_context_refs",
                "message": "additional_context_refs and forbidden_context_refs may not overlap",
            }
        )
    bundle_refs = set(_context_bundles.bundle_refs(normalized["required_context_bundle"]))
    blocked_bundle_refs = sorted(bundle_refs & forbidden_refs)
    if blocked_bundle_refs:
        errors.append(
            {
                "code": ERROR_CODES["forbidden_bundle_ref"],
                "field": "forbidden_context_refs",
                "message": "forbidden_context_refs may not remove required bundle refs",
            }
        )

    return {
        "ok": not errors,
        "failure_class": "" if not errors else "child_work_order_validation_failed",
        "errors": errors,
        "warnings": warnings,
        "normalized_work_order": normalized,
        "computed_fingerprint": _fingerprint(normalized),
    }


def resolve_child_context_refs(raw_work_order: Mapping[str, Any]) -> Dict[str, Any]:
    validated = validate_child_work_order(raw_work_order)
    if not validated["ok"]:
        return {
            "ok": False,
            "failure_class": "child_context_resolution_blocked",
            "errors": list(validated["errors"]),
            "warnings": list(validated["warnings"]),
            "effective_refs": [],
        }
    normalized = dict(validated["normalized_work_order"])
    bundle_refs = list(_context_bundles.bundle_refs(normalized["required_context_bundle"]))
    additional_refs = list(normalized["additional_context_refs"])
    forbidden = set(normalized["forbidden_context_refs"])
    effective_refs: List[str] = []
    omitted_refs: List[Dict[str, str]] = []
    for ref in bundle_refs + additional_refs:
        if ref in forbidden:
            omitted_refs.append({"path": ref, "reason": "forbidden"})
            continue
        if ref not in effective_refs:
            effective_refs.append(ref)
    return {
        "ok": True,
        "failure_class": "",
        "errors": [],
        "warnings": list(validated["warnings"]),
        "effective_refs": effective_refs,
        "omitted_refs": omitted_refs,
        "normalized_work_order": normalized,
    }


def validate_child_return_packet(
    raw_packet: Mapping[str, Any],
    *,
    work_order: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    raw = dict(raw_packet or {})
    normalized = normalize_child_return_packet(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    unknown_fields = sorted(set(raw.keys()) - RETURN_PACKET_KNOWN_FIELDS)
    for key in unknown_fields:
        warnings.append(
            {
                "code": ERROR_CODES["unknown_field"],
                "field": key,
                "message": f"unknown top-level field ignored for compatibility: {key}",
            }
        )

    for field in RETURN_PACKET_REQUIRED_FIELDS:
        if field not in raw:
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})
            continue
        value = normalized.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value):
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})
        elif isinstance(value, list) and not value and field not in RETURN_PACKET_ALLOW_EMPTY_LIST_FIELDS:
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "schema_version",
                "message": f"schema_version must be {SCHEMA_VERSION}",
            }
        )

    if normalized["status"] not in RETURN_STATUS_SET:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "status",
                "message": f"status must be one of: {', '.join(sorted(RETURN_STATUS_SET))}",
            }
        )

    for field in RETURN_PACKET_REQUIRED_NESTED_FIELDS:
        top, nested = field.split(".", 1)
        if top == "scope_fence":
            value = normalized["scope_fence"].get(nested)
        elif top == "ownership_fence":
            value = normalized["ownership_fence"].get(nested)
        else:
            value = normalized["next_decision"].get(nested)
        if value in ("", None):
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})
        elif isinstance(value, list) and not value and field not in RETURN_PACKET_ALLOW_EMPTY_NESTED_LIST_FIELDS:
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})

    for idx, row in enumerate(normalized["evidence_summary"]):
        for field in ("evidence_id", "status", "summary"):
            if not row.get(field):
                errors.append(
                    {
                        "code": ERROR_CODES["missing_field"],
                        "field": f"evidence_summary[{idx}].{field}",
                        "message": f"evidence_summary[{idx}].{field} is required",
                    }
                )
        if row.get("status") and row["status"] not in EVIDENCE_STATUS_SET:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_value"],
                    "field": f"evidence_summary[{idx}].status",
                    "message": f"evidence_summary[{idx}].status must be one of: {', '.join(sorted(EVIDENCE_STATUS_SET))}",
                }
            )

    for claim in _master_contract.FORBIDDEN_CHILD_CLAIMS:
        value = raw.get(claim)
        if value not in (None, "", [], {}, ()):
            errors.append(
                {
                    "code": ERROR_CODES["forbidden_parent_claim"],
                    "field": claim,
                    "message": f"child packets may not claim parent-owned field {claim}",
                }
            )

    ownership_fence = normalized["ownership_fence"]
    for idx, path in enumerate(normalized["changed_files"]):
        if not _path_is_owned(path, ownership_fence["owned_paths"]):
            errors.append(
                {
                    "code": ERROR_CODES["ownership_violation"],
                    "field": f"changed_files[{idx}]",
                    "message": "changed file falls outside declared ownership_fence.owned_paths",
                }
            )

    validated_work_order: Dict[str, Any] | None = None
    if work_order is None:
        return {
            "ok": False,
            "failure_class": "child_return_packet_validation_blocked",
            "errors": [
                {
                    "code": ERROR_CODES["missing_field"],
                    "field": "work_order",
                    "message": "a bound work_order is required to validate child return packets",
                }
            ]
            + errors,
            "warnings": warnings,
            "normalized_packet": normalized,
            "normalized_work_order": None,
            "computed_fingerprint": _fingerprint(normalized),
        }

    work_order_result = validate_child_work_order(work_order)
    if not work_order_result["ok"]:
        return {
            "ok": False,
            "failure_class": "child_return_packet_validation_blocked",
            "errors": list(work_order_result["errors"]),
            "warnings": list(work_order_result["warnings"]) + warnings,
            "normalized_packet": normalized,
            "computed_fingerprint": _fingerprint(normalized),
        }
    validated_work_order = dict(work_order_result["normalized_work_order"])
    for field in ("work_order_id", "parent_uid", "child_uid"):
        if normalized[field] != validated_work_order[field]:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_value"],
                    "field": field,
                    "message": f"{field} must match the bound work order",
                }
            )
    if normalized["scope_fence"] != validated_work_order["scope_fence"]:
        errors.append(
            {
                "code": ERROR_CODES["scope_fence_mismatch"],
                "field": "scope_fence",
                "message": "scope_fence must match the bound work order exactly",
            }
        )
    if normalized["ownership_fence"] != validated_work_order["ownership_fence"]:
        errors.append(
            {
                "code": ERROR_CODES["ownership_fence_mismatch"],
                "field": "ownership_fence",
                "message": "ownership_fence must match the bound work order exactly",
            }
        )
    expected_ids = {
        row["evidence_id"]
        for row in validated_work_order["expected_evidence"]
        if row.get("required") and row.get("evidence_id")
    }
    summary_by_id = {row["evidence_id"]: row for row in normalized["evidence_summary"] if row.get("evidence_id")}
    missing_ids = sorted(expected_ids - set(summary_by_id))
    if missing_ids:
        errors.append(
            {
                "code": ERROR_CODES["missing_evidence_summary"],
                "field": "evidence_summary",
                "message": "required expected_evidence rows must appear in evidence_summary",
            }
        )
    if normalized["status"] == "succeeded":
        missing_or_bad = [
            evidence_id
            for evidence_id in expected_ids
            if summary_by_id.get(evidence_id, {}).get("status") != "provided"
        ]
        if missing_or_bad:
            errors.append(
                {
                    "code": ERROR_CODES["contradictory_status"],
                    "field": "status",
                    "message": "status=succeeded requires all required evidence obligations to be provided",
                }
            )

    return {
        "ok": not errors,
        "failure_class": "" if not errors else "child_return_packet_validation_failed",
        "errors": errors,
        "warnings": warnings,
        "normalized_packet": normalized,
        "normalized_work_order": validated_work_order,
        "computed_fingerprint": _fingerprint(normalized),
    }


def validate_fanout_child_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    payload = json.loads((root / CONTRACT_ARTIFACT_REL).read_text(encoding="utf-8"))
    issues = []
    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append(f"schema_version must remain {SCHEMA_VERSION}")
    if str(payload.get("uid") or "") != UID:
        issues.append(f"uid must remain bound to {UID}")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the child fanout execution contract doc")
    if tuple(payload.get("work_order_required_fields") or ()) != WORK_ORDER_REQUIRED_FIELDS:
        issues.append("work_order_required_fields must remain stable and ordered")
    if tuple(payload.get("return_packet_required_fields") or ()) != RETURN_PACKET_REQUIRED_FIELDS:
        issues.append("return_packet_required_fields must remain stable and ordered")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules must remain stable and ordered")
    if tuple(payload.get("forbidden_parent_claims") or ()) != _master_contract.FORBIDDEN_CHILD_CLAIMS:
        issues.append("forbidden_parent_claims must match the master-owned forbidden child claims")
    return tuple(issues)
