"""Canonical compiled work-order envelope and validation helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Tuple

from infra.controlplane import context_bundles as _context_bundles


SCHEMA_VERSION = "compiled_work_order_envelope_v1"
CONTRACT_ARTIFACT_REL = Path("governance/compiled_work_order_envelope_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_compiled_work_order_envelope_v1.md")
UID = "PIS-20260411-144241Z-154B"

REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "work_order_id",
    "source_binding",
    "handoff_kind",
    "scope_fence",
    "required_context_bundle",
    "additional_context_refs",
    "forbidden_context_refs",
    "expected_outputs",
    "validation_expectations",
)
OPTIONAL_FIELDS: Tuple[str, ...] = ("execution_family", "continuation_packet_ref")
KNOWN_FIELDS = frozenset(REQUIRED_FIELDS + OPTIONAL_FIELDS)

ALLOWED_EMPTY_LIST_FIELDS = frozenset({"additional_context_refs", "forbidden_context_refs"})
ALLOWED_SCOPE_EMPTY_LIST_FIELDS = frozenset({"scope_fence.out_of_scope"})

FAILURE_CLASSES = MappingProxyType(
    {
        "validation": "compiled_work_order_validation_failed",
        "context_resolution": "compiled_work_order_context_resolution_blocked",
        "coexistence": "compiled_work_order_coexistence_blocked",
    }
)

ERROR_CODES = MappingProxyType(
    {
        "missing_field": "missing_field",
        "invalid_value": "invalid_value",
        "unknown_field": "unknown_field",
        "contradictory_context_refs": "contradictory_context_refs",
        "forbidden_bundle_ref": "forbidden_bundle_ref",
        "missing_input": "missing_input",
        "coexistence_conflict": "coexistence_conflict",
    }
)

PROSE_COEXISTENCE_COMPILED_ONLY = "compiled_only"
PROSE_COEXISTENCE_FALLBACK_ALLOWED = "fallback_prose_allowed"
PROSE_COEXISTENCE_MODES = (
    PROSE_COEXISTENCE_COMPILED_ONLY,
    PROSE_COEXISTENCE_FALLBACK_ALLOWED,
)

FAILURE_MODE_FAIL_CLOSED = "fail_closed"
FAILURE_MODES = (FAILURE_MODE_FAIL_CLOSED,)


@dataclass(frozen=True)
class CompiledWorkOrderEnvelopeContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    required_fields: Tuple[str, ...]
    optional_fields: Tuple[str, ...]
    required_context_bundle_rule: str
    compiled_precedence_rule: str
    prose_coexistence_modes: Tuple[str, ...]
    failure_classes: Mapping[str, str]


def compiled_work_order_envelope_contract_v1() -> CompiledWorkOrderEnvelopeContractV1:
    return CompiledWorkOrderEnvelopeContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        required_fields=REQUIRED_FIELDS,
        optional_fields=OPTIONAL_FIELDS,
        required_context_bundle_rule="bundle_plus_delta_minus_forbidden",
        compiled_precedence_rule="compiled_envelope_authoritative_when_present",
        prose_coexistence_modes=PROSE_COEXISTENCE_MODES,
        failure_classes=FAILURE_CLASSES,
    )


def load_compiled_work_order_envelope_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    return json.loads((root / CONTRACT_ARTIFACT_REL).read_text(encoding="utf-8"))


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _normalize_source_binding(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "binding_type": _normalize_text(row.get("binding_type")),
        "binding_id": _normalize_text(row.get("binding_id")),
    }


def _normalize_scope_fence(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "objective": _normalize_text(row.get("objective")),
        "in_scope": _normalize_text_list(row.get("in_scope")),
        "out_of_scope": _normalize_text_list(row.get("out_of_scope")),
    }


def _normalize_expected_outputs(value: Any) -> List[Dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: List[Dict[str, Any]] = []
    for raw in rows:
        row = raw if isinstance(raw, Mapping) else {}
        normalized = {
            "output_id": _normalize_text(row.get("output_id")),
            "kind": _normalize_text(row.get("kind")).lower(),
            "summary": _normalize_text(row.get("summary")),
            "required": bool(row.get("required", True)),
        }
        if any((normalized["output_id"], normalized["kind"], normalized["summary"], normalized["required"])):
            out.append(normalized)
    out.sort(key=lambda row: (row["output_id"], row["kind"], row["summary"]))
    return out


def _normalize_validation_expectations(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "required_checks": _normalize_text_list(row.get("required_checks")),
        "failure_mode": _normalize_text(row.get("failure_mode")).lower(),
        "prose_coexistence": _normalize_text(row.get("prose_coexistence")).lower(),
    }


def normalize_compiled_work_order_envelope(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    execution_family = obj.get("execution_family") if isinstance(obj.get("execution_family"), Mapping) else {}
    return {
        "schema_version": _normalize_text(obj.get("schema_version") or SCHEMA_VERSION),
        "work_order_id": _normalize_text(obj.get("work_order_id")),
        "source_binding": _normalize_source_binding(obj.get("source_binding")),
        "handoff_kind": _normalize_text(obj.get("handoff_kind")),
        "scope_fence": _normalize_scope_fence(obj.get("scope_fence")),
        "required_context_bundle": _normalize_text(obj.get("required_context_bundle")),
        "additional_context_refs": _normalize_text_list(obj.get("additional_context_refs")),
        "forbidden_context_refs": _normalize_text_list(obj.get("forbidden_context_refs")),
        "expected_outputs": _normalize_expected_outputs(obj.get("expected_outputs")),
        "validation_expectations": _normalize_validation_expectations(obj.get("validation_expectations")),
        "execution_family": {k: _normalize_text(v) for k, v in sorted(execution_family.items()) if _normalize_text(k)},
        "continuation_packet_ref": _normalize_text(obj.get("continuation_packet_ref")),
    }


def canonical_compiled_work_order_envelope_json(normalized_envelope: Mapping[str, Any]) -> str:
    return json.dumps(dict(normalized_envelope), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compiled_work_order_envelope_fingerprint(normalized_envelope: Mapping[str, Any]) -> str:
    raw = canonical_compiled_work_order_envelope_json(normalized_envelope).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_compiled_work_order_envelope(raw_envelope: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_envelope or {})
    normalized = normalize_compiled_work_order_envelope(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    unknown_fields = sorted(set(raw.keys()) - KNOWN_FIELDS)
    for key in unknown_fields:
        warnings.append(
            {
                "code": ERROR_CODES["unknown_field"],
                "field": key,
                "message": f"unknown top-level field ignored for compatibility: {key}",
            }
        )

    for field in REQUIRED_FIELDS:
        if field not in raw:
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})
            continue
        value = normalized.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value):
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})
        elif isinstance(value, list) and not value and field not in ALLOWED_EMPTY_LIST_FIELDS:
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "schema_version",
                "message": f"schema_version must be {SCHEMA_VERSION}",
            }
        )

    for field in ("binding_type", "binding_id"):
        if not normalized["source_binding"].get(field):
            errors.append(
                {
                    "code": ERROR_CODES["missing_field"],
                    "field": f"source_binding.{field}",
                    "message": f"source_binding.{field} is required",
                }
            )

    for field in ("objective", "in_scope", "out_of_scope"):
        value = normalized["scope_fence"].get(field)
        dotted = f"scope_fence.{field}"
        if value in ("", None):
            errors.append({"code": ERROR_CODES["missing_field"], "field": dotted, "message": f"{dotted} is required"})
        elif isinstance(value, list) and not value and dotted not in ALLOWED_SCOPE_EMPTY_LIST_FIELDS:
            errors.append({"code": ERROR_CODES["missing_field"], "field": dotted, "message": f"{dotted} is required"})

    if normalized["required_context_bundle"] not in _context_bundles.available_bundle_ids():
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "required_context_bundle",
                "message": "required_context_bundle must name a registered context bundle",
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
    bundle_refs = set(_context_bundles.bundle_refs(normalized["required_context_bundle"])) if normalized["required_context_bundle"] else set()
    if sorted(bundle_refs & forbidden_refs):
        errors.append(
            {
                "code": ERROR_CODES["forbidden_bundle_ref"],
                "field": "forbidden_context_refs",
                "message": "forbidden_context_refs may not remove required bundle refs",
            }
        )

    if not normalized["expected_outputs"]:
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "expected_outputs",
                "message": "expected_outputs must contain at least one declared output",
            }
        )
    for idx, row in enumerate(normalized["expected_outputs"]):
        for field in ("output_id", "kind", "summary"):
            if not row.get(field):
                errors.append(
                    {
                        "code": ERROR_CODES["missing_field"],
                        "field": f"expected_outputs[{idx}].{field}",
                        "message": f"expected_outputs[{idx}].{field} is required",
                    }
                )

    validation = normalized["validation_expectations"]
    if not validation.get("required_checks"):
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "validation_expectations.required_checks",
                "message": "validation_expectations.required_checks is required",
            }
        )
    if validation.get("failure_mode") not in FAILURE_MODES:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "validation_expectations.failure_mode",
                "message": f"validation_expectations.failure_mode must be one of: {', '.join(FAILURE_MODES)}",
            }
        )
    if validation.get("prose_coexistence") not in PROSE_COEXISTENCE_MODES:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "validation_expectations.prose_coexistence",
                "message": (
                    "validation_expectations.prose_coexistence must be one of: "
                    + ", ".join(PROSE_COEXISTENCE_MODES)
                ),
            }
        )

    return {
        "ok": not errors,
        "failure_class": "" if not errors else FAILURE_CLASSES["validation"],
        "errors": errors,
        "warnings": warnings,
        "normalized_envelope": normalized,
        "computed_fingerprint": compiled_work_order_envelope_fingerprint(normalized),
    }


def resolve_compiled_work_order_context_refs(raw_envelope: Mapping[str, Any]) -> Dict[str, Any]:
    validated = validate_compiled_work_order_envelope(raw_envelope)
    if not validated["ok"]:
        return {
            "ok": False,
            "failure_class": FAILURE_CLASSES["context_resolution"],
            "errors": list(validated["errors"]),
            "warnings": list(validated["warnings"]),
            "effective_refs": [],
            "omitted_refs": [],
        }
    normalized = dict(validated["normalized_envelope"])
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
        "normalized_envelope": normalized,
    }


def resolve_compiled_work_order_coexistence(
    *,
    compiled_envelope: Mapping[str, Any] | None,
    prose_brief_present: bool,
) -> Dict[str, Any]:
    if compiled_envelope:
        validated = validate_compiled_work_order_envelope(compiled_envelope)
        if not validated["ok"]:
            return {
                "ok": False,
                "failure_class": FAILURE_CLASSES["coexistence"],
                "errors": list(validated["errors"]),
                "warnings": list(validated["warnings"]),
                "effective_path": "",
                "precedence": "",
            }
        normalized = dict(validated["normalized_envelope"])
        coexistence_mode = normalized["validation_expectations"]["prose_coexistence"]
        if not prose_brief_present:
            return {
                "ok": True,
                "failure_class": "",
                "errors": [],
                "warnings": list(validated["warnings"]),
                "effective_path": "compiled_only",
                "precedence": "compiled_envelope",
                "normalized_envelope": normalized,
            }
        if coexistence_mode == PROSE_COEXISTENCE_FALLBACK_ALLOWED:
            warnings = list(validated["warnings"]) + [
                {
                    "code": "prose_fallback_present",
                    "field": "validation_expectations.prose_coexistence",
                    "message": "prose brief may coexist only as an explicit fallback; compiled envelope remains authoritative",
                }
            ]
            return {
                "ok": True,
                "failure_class": "",
                "errors": [],
                "warnings": warnings,
                "effective_path": "dual_path_shadow",
                "precedence": "compiled_envelope",
                "normalized_envelope": normalized,
            }
        return {
            "ok": False,
            "failure_class": FAILURE_CLASSES["coexistence"],
            "errors": [
                {
                    "code": ERROR_CODES["coexistence_conflict"],
                    "field": "validation_expectations.prose_coexistence",
                    "message": "compiled_only envelopes may not be paired with a prose brief",
                }
            ],
            "warnings": list(validated["warnings"]),
            "effective_path": "",
            "precedence": "",
            "normalized_envelope": normalized,
        }

    if prose_brief_present:
        return {
            "ok": True,
            "failure_class": "",
            "errors": [],
            "warnings": [
                {
                    "code": "legacy_prose_path",
                    "field": "compiled_envelope",
                    "message": "prose-only fallback remains allowed during rollout, but it carries no compiled authority",
                }
            ],
            "effective_path": "prose_only",
            "precedence": "prose_brief",
        }
    return {
        "ok": False,
        "failure_class": FAILURE_CLASSES["coexistence"],
        "errors": [
            {
                "code": ERROR_CODES["missing_input"],
                "field": "compiled_envelope",
                "message": "compiled envelope or prose brief is required",
            }
        ],
        "warnings": [],
        "effective_path": "",
        "precedence": "",
    }


def validate_compiled_work_order_envelope_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    issues: List[str] = []
    contract = compiled_work_order_envelope_contract_v1()
    payload = load_compiled_work_order_envelope_contract(repo_root=repo_root)

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append(f"schema_version must remain {SCHEMA_VERSION}")
    if str(payload.get("uid") or "") != UID:
        issues.append(f"uid must remain bound to {UID}")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the compiled work-order envelope contract doc")
    if str(payload.get("contract_family") or "") != "compiled_work_order_envelope":
        issues.append("contract_family must remain compiled_work_order_envelope")
    if tuple(payload.get("required_fields") or ()) != REQUIRED_FIELDS:
        issues.append("required_fields must remain stable and ordered")
    if tuple(payload.get("optional_fields") or ()) != OPTIONAL_FIELDS:
        issues.append("optional_fields must remain stable and ordered")
    if str(payload.get("context_resolution_rule") or "") != "bundle_plus_delta_minus_forbidden":
        issues.append("context_resolution_rule must remain bundle_plus_delta_minus_forbidden")
    if str(payload.get("compiled_precedence_rule") or "") != "compiled_envelope_authoritative_when_present":
        issues.append("compiled_precedence_rule must remain compiled_envelope_authoritative_when_present")
    if tuple(payload.get("prose_coexistence_modes") or ()) != PROSE_COEXISTENCE_MODES:
        issues.append("prose_coexistence_modes must remain stable and ordered")
    if tuple(payload.get("failure_classes") or ()) != tuple(FAILURE_CLASSES.values()):
        issues.append("failure_classes must remain stable and ordered")

    if contract.required_context_bundle_rule != "bundle_plus_delta_minus_forbidden":
        issues.append("required_context_bundle_rule drifted from the lab bundle contract")
    if contract.compiled_precedence_rule != "compiled_envelope_authoritative_when_present":
        issues.append("compiled precedence rule drifted from the rollout contract")
    if contract.prose_coexistence_modes != PROSE_COEXISTENCE_MODES:
        issues.append("prose coexistence modes drifted from the canonical envelope contract")
    if tuple(contract.required_fields) != REQUIRED_FIELDS:
        issues.append("required fields drifted from the canonical envelope contract")
    return tuple(issues)
