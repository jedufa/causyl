"""Compiled execution IR schema, normalization, and validation."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional

from infra.controlplane import context_bundles as _context_bundles


IR_VERSION = "compiled_execution_ir_v1"
MODE_SET = frozenset({"plan", "build", "verify", "repair"})
CLAIM_SCOPE_SET = frozenset(
    {
        "analysis_only",
        "bounded_artifact_only",
        "bounded_repo_change",
        "verification_only",
    }
)

REQUIRED_FIELDS = (
    "ir_version",
    "task_id",
    "title",
    "goal",
    "constraints",
    "required_context_bundle",
    "additional_context_refs",
    "forbidden_context_refs",
    "required_output_kind",
    "allowed_files",
    "validation_plan",
    "claim_scope",
    "mode",
    "execution_family",
    "metadata",
)

ERROR_CODES = MappingProxyType(
    {
        "missing_field": "missing_field",
        "unknown_ir_version": "unknown_ir_version",
        "invalid_mode": "invalid_mode",
        "invalid_claim_scope": "invalid_claim_scope",
        "invalid_context_bundle": "invalid_context_bundle",
        "contradictory_context_refs": "contradictory_context_refs",
        "missing_required_output_kind": "missing_required_output_kind",
        "missing_required_context_bundle": "missing_required_context_bundle",
        "missing_allowed_files": "missing_allowed_files",
        "missing_validation_plan": "missing_validation_plan",
        "invalid_metadata": "invalid_metadata",
        "unknown_field": "unknown_field",
        "strict_unknown_field": "strict_unknown_field",
    }
)

STRICT_EXECUTION_MODES = frozenset({"soft_gate", "hard_gate"})


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


def _normalize_metadata(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in sorted(value.keys()):
        k = _normalize_text(key)
        if not k:
            continue
        raw = value[key]
        if isinstance(raw, (str, int, float, bool)) or raw is None:
            out[k] = raw
        else:
            out[k] = _normalize_text(raw)
    return out


def _normalize_execution_family(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in (
        "contract_kind",
        "phase",
        "source_intent_uid",
        "parent_uid",
        "child_uid",
        "child_kind",
        "required_final_child_kind",
        "required_final_child_uid",
        "integration_audit_mode",
        "trust_hardening_mode",
        "status_promotion_policy",
        "receipt_selection_policy",
    ):
        text = _normalize_text(value.get(key))
        if text:
            out[key] = text
    for key in ("child_uids", "child_job_ids", "allowed_repo_paths", "allowed_ledger_paths", "closeout_rules"):
        rows = sorted(_normalize_text_list(value.get(key)))
        if rows:
            out[key] = rows
    return out


def normalize_execution_ir(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    return {
        "ir_version": _normalize_text(obj.get("ir_version") or IR_VERSION),
        "task_id": _normalize_text(obj.get("task_id")),
        "title": _normalize_text(obj.get("title")),
        "goal": _normalize_text(obj.get("goal")),
        "constraints": sorted(_normalize_text_list(obj.get("constraints"))),
        "required_context_bundle": _normalize_text(obj.get("required_context_bundle")),
        "additional_context_refs": sorted(_normalize_text_list(obj.get("additional_context_refs"))),
        "forbidden_context_refs": sorted(_normalize_text_list(obj.get("forbidden_context_refs"))),
        "required_output_kind": _normalize_text(obj.get("required_output_kind")),
        "allowed_files": sorted(_normalize_text_list(obj.get("allowed_files"))),
        "validation_plan": sorted(_normalize_text_list(obj.get("validation_plan"))),
        "claim_scope": _normalize_text(obj.get("claim_scope")),
        "mode": _normalize_text(obj.get("mode")).lower(),
        "execution_family": _normalize_execution_family(obj.get("execution_family")),
        "metadata": _normalize_metadata(obj.get("metadata")),
    }


def canonical_execution_ir_json(normalized_ir: Mapping[str, Any]) -> str:
    return json.dumps(dict(normalized_ir), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def execution_ir_fingerprint(normalized_ir: Mapping[str, Any]) -> str:
    raw = canonical_execution_ir_json(normalized_ir).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _strict_mode_from_raw(raw: Mapping[str, Any]) -> bool:
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    execution_mode = _normalize_text(metadata.get("compiled_execution_mode")).lower()
    return execution_mode in STRICT_EXECUTION_MODES


def validate_execution_ir(raw: Mapping[str, Any], *, strict: Optional[bool] = None) -> Dict[str, Any]:
    normalized = normalize_execution_ir(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    strict_mode = _strict_mode_from_raw(dict(raw or {})) if strict is None else bool(strict)

    raw_keys = set(dict(raw or {}).keys())
    unknown_keys = sorted(raw_keys - set(REQUIRED_FIELDS))
    for key in unknown_keys:
        row = {
            "code": ERROR_CODES["strict_unknown_field"] if strict_mode else ERROR_CODES["unknown_field"],
            "field": key,
            "message": f"unknown field ignored: {key}" if not strict_mode else f"unknown field is not allowed in strict mode: {key}",
        }
        if strict_mode:
            errors.append(row)
        else:
            warnings.append(row)

    for field in ("task_id", "title", "goal", "required_output_kind", "claim_scope", "mode"):
        if not _normalize_text(normalized.get(field)):
            code = ERROR_CODES["missing_required_output_kind"] if field == "required_output_kind" else ERROR_CODES["missing_field"]
            errors.append({"code": code, "field": field, "message": f"{field} is required"})

    if strict_mode and not _normalize_text(normalized.get("required_context_bundle")):
        errors.append(
            {
                "code": ERROR_CODES["missing_required_context_bundle"],
                "field": "required_context_bundle",
                "message": "required_context_bundle is required in strict compiled execution modes",
            }
        )

    if normalized["ir_version"] != IR_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["unknown_ir_version"],
                "field": "ir_version",
                "message": f"ir_version must be {IR_VERSION}",
            }
        )
    if normalized["mode"] and normalized["mode"] not in MODE_SET:
        errors.append({"code": ERROR_CODES["invalid_mode"], "field": "mode", "message": "mode must be one of plan, build, verify, repair"})
    if normalized["claim_scope"] and normalized["claim_scope"] not in CLAIM_SCOPE_SET:
        errors.append(
            {
                "code": ERROR_CODES["invalid_claim_scope"],
                "field": "claim_scope",
                "message": "claim_scope must be one of analysis_only, bounded_artifact_only, bounded_repo_change, verification_only",
            }
        )
    bundle_id = normalized["required_context_bundle"]
    if bundle_id and bundle_id not in _context_bundles.CONTEXT_BUNDLES:
        errors.append(
            {
                "code": ERROR_CODES["invalid_context_bundle"],
                "field": "required_context_bundle",
                "message": f"required_context_bundle must be one of: {', '.join(_context_bundles.available_bundle_ids())}",
            }
        )
    overlap = sorted(set(normalized["additional_context_refs"]) & set(normalized["forbidden_context_refs"]))
    if overlap:
        errors.append(
            {
                "code": ERROR_CODES["contradictory_context_refs"],
                "field": "additional_context_refs",
                "message": f"context refs cannot be both allowed and forbidden: {', '.join(overlap)}",
            }
        )
    if normalized["claim_scope"] == "bounded_repo_change" and not normalized["allowed_files"]:
        errors.append(
            {
                "code": ERROR_CODES["missing_allowed_files"],
                "field": "allowed_files",
                "message": "bounded_repo_change requires at least one allowed_files entry",
            }
        )
    if normalized["claim_scope"] == "bounded_repo_change" and not normalized["validation_plan"]:
        errors.append(
            {
                "code": ERROR_CODES["missing_validation_plan"],
                "field": "validation_plan",
                "message": "bounded_repo_change requires non-empty validation_plan",
            }
        )
    if normalized["mode"] in {"build", "repair"} and not normalized["validation_plan"]:
        errors.append(
            {
                "code": ERROR_CODES["missing_validation_plan"],
                "field": "validation_plan",
                "message": f"{normalized['mode']} mode requires non-empty validation_plan",
            }
        )
    if normalized["mode"] == "verify" and normalized["required_output_kind"] not in {"verdict_report", "report"}:
        errors.append(
            {
                "code": ERROR_CODES["missing_required_output_kind"],
                "field": "required_output_kind",
                "message": "verify mode requires required_output_kind of verdict_report or report",
            }
        )
    if not isinstance(normalized["metadata"], dict):
        errors.append(
            {
                "code": ERROR_CODES["invalid_metadata"],
                "field": "metadata",
                "message": "metadata must normalize to a flat object",
            }
        )
    execution_family = normalized["execution_family"] if isinstance(normalized.get("execution_family"), dict) else {}
    if execution_family:
        if not _normalize_text(execution_family.get("contract_kind")):
            errors.append(
                {
                    "code": ERROR_CODES["missing_field"],
                    "field": "execution_family.contract_kind",
                    "message": "execution_family.contract_kind is required when execution_family is present",
                }
            )
        if not _normalize_text(execution_family.get("parent_uid")):
            errors.append(
                {
                    "code": ERROR_CODES["missing_field"],
                    "field": "execution_family.parent_uid",
                    "message": "execution_family.parent_uid is required when execution_family is present",
                }
            )

    ok = not errors
    return {
        "ok": ok,
        "normalized_ir": normalized,
        "canonical_json": canonical_execution_ir_json(normalized),
        "fingerprint": execution_ir_fingerprint(normalized),
        "warnings": warnings,
        "errors": errors,
        "strict_mode": strict_mode,
        "failure_class": "" if ok else "ir_validation_failed",
    }
