"""Canonical campaign-plan contract helpers for the intent-to-campaign compiler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import budget_adaptive_research_policy
from infra.controlplane import scientific_campaign_method_template


CONTRACT_SCHEMA_VERSION = "campaign_plan_contract_v1"
PLAN_SCHEMA_VERSION = "campaign_plan_v1"
REFERENCE_EXAMPLES_SCHEMA_VERSION = "campaign_plan_reference_examples_v1"
CONTRACT_ARTIFACT_REL = Path("governance/campaign_plan_contract_v1.json")
REFERENCE_EXAMPLES_REL = Path("governance/campaign_plan_reference_examples_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_campaign_plan_v1.md")
UID = "PIS-20260411-113558Z-0B60"

TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "plan_id",
    "plan_version",
    "stable_identity",
    "metadata",
    "vocabulary_boundary",
    "ambiguity_policy",
    "provenance",
    "phases",
)
OPTIONAL_TOP_LEVEL_FIELDS: Tuple[str, ...] = ("policy_enrichment",)
STABLE_IDENTITY_REQUIRED_FIELDS: Tuple[str, ...] = (
    "canonicalization",
    "content_hash_algorithm",
    "schema_migration_policy",
)
METADATA_REQUIRED_FIELDS: Tuple[str, ...] = (
    "title",
    "created_at_utc",
    "source_intent_uid",
    "ledger_refs",
)
VOCABULARY_BOUNDARY_KEYS: Tuple[str, ...] = (
    "intent_sheet",
    "campaign_plan",
    "pis",
    "job",
)
AMBIGUITY_POLICY_REQUIRED_FIELDS: Tuple[str, ...] = (
    "allowed_in_campaign_plan",
    "must_resolve_before_pis",
    "named_contract_violation",
)
PROVENANCE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "source_intent_uid",
    "upstream_contract_refs",
    "supporting_artifact_refs",
    "lineage_hash",
)
PHASE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "phase_id",
    "label",
    "goal",
    "deliverables",
    "method_template_phase_ids",
    "budget_mode",
    "allowed_ambiguities",
    "must_resolve_before_pis",
    "pis_readiness",
)
PHASE_ALLOW_EMPTY_FIELDS = frozenset({"allowed_ambiguities", "must_resolve_before_pis"})
REFERENCE_EXAMPLE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "example_id",
    "example_class",
    "expected_validation_outcome",
    "campaign_plan",
)
POLICY_ENRICHMENT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "method_template_ref",
    "budget_policy_ref",
    "selected_budget_mode",
    "declared_constraints",
    "enrichment_receipts",
)
DECLARED_CONSTRAINT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "constraint_id",
    "constraint_kind",
    "phase_id",
    "method_phase_id",
    "advisory_only",
    "rationale",
)
ENRICHMENT_RECEIPT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "receipt_id",
    "policy_source_kind",
    "policy_ref",
    "target_phase_ids",
    "changed_fields",
    "change_summary",
    "reason_codes",
    "receipt_state",
)
ENRICHMENT_RECEIPT_ALLOW_EMPTY_FIELDS = frozenset({"changed_fields"})
PIS_READINESS_STATES: Tuple[str, ...] = ("advisory_only", "ready_for_pis_generation", "blocked")
EXAMPLE_CLASSES: Tuple[str, ...] = (
    "valid_broad_intent_plan",
    "invalid_pseudo_pis_plan",
    "invalid_under_specified_plan",
)
EXPECTED_EXAMPLE_OUTCOMES: Tuple[str, ...] = ("accepted", "rejected")
CONSTRAINT_KINDS: Tuple[str, ...] = ("require_method_phase", "forbid_method_phase")
POLICY_SOURCE_KINDS: Tuple[str, ...] = ("method_template", "budget_policy", "declared_constraint")
ENRICHMENT_RECEIPT_STATES: Tuple[str, ...] = ("applied", "validated_no_change", "blocked")
FORBIDDEN_TOP_LEVEL_FIELDS: Tuple[str, ...] = (
    "file_edit_plan",
    "patch_plan",
    "validation_commands",
    "shell_commands",
    "endpoint_diffs",
    "commit_message",
    "job_payloads",
)
FORBIDDEN_PHASE_FIELDS: Tuple[str, ...] = (
    "file_paths",
    "code_changes",
    "shell_commands",
    "validation_commands",
    "endpoint_patch",
    "commit_message",
    "job_payload",
)
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "intent_sheet_campaign_plan_pis_and_job_vocabularies_must_stay_distinct",
    "campaign_plan_requires_phases_constraints_and_named_ambiguity_policy",
    "phase_budget_modes_and_method_template_phase_refs_must_use_upstream_vocabularies",
    "pseudo_pis_file_edit_and_validation_detail_forbidden_inline",
    "under_specified_non_plans_rejected",
)


@dataclass(frozen=True)
class CampaignPlanContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    reference_examples_rel: Path
    canonical_doc_rel: Path
    top_level_required_fields: Tuple[str, ...]
    phase_required_fields: Tuple[str, ...]
    pis_readiness_states: Tuple[str, ...]
    example_classes: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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


def _append_missing_errors(errors: List[Dict[str, str]], prefix: str, row: Mapping[str, Any], fields: Sequence[str]) -> None:
    for field in fields:
        value = row.get(field)
        if value in ("", None, [], {}):
            errors.append({"field": f"{prefix}.{field}", "code": "missing_field", "message": f"{prefix}.{field} is required"})


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_stable_identity(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "canonicalization": _normalize_text(row.get("canonicalization")),
        "content_hash_algorithm": _normalize_text(row.get("content_hash_algorithm")).lower(),
        "schema_migration_policy": _normalize_text(row.get("schema_migration_policy")),
    }


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = _normalize_text(value).lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_metadata(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "title": _normalize_text(row.get("title")),
        "created_at_utc": _normalize_text(row.get("created_at_utc")),
        "source_intent_uid": _normalize_text(row.get("source_intent_uid")),
        "ledger_refs": _normalize_text_list(row.get("ledger_refs")),
    }


def _normalize_vocabulary_boundary(value: Any) -> Dict[str, List[str]]:
    row = value if isinstance(value, Mapping) else {}
    return {key: _normalize_text_list(row.get(key)) for key in VOCABULARY_BOUNDARY_KEYS}


def _normalize_ambiguity_policy(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "allowed_in_campaign_plan": _normalize_text_list(row.get("allowed_in_campaign_plan")),
        "must_resolve_before_pis": _normalize_text_list(row.get("must_resolve_before_pis")),
        "named_contract_violation": _normalize_text(row.get("named_contract_violation")),
    }


def _normalize_provenance(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "source_intent_uid": _normalize_text(row.get("source_intent_uid")),
        "upstream_contract_refs": _normalize_text_list(row.get("upstream_contract_refs")),
        "supporting_artifact_refs": _normalize_text_list(row.get("supporting_artifact_refs")),
        "lineage_hash": _normalize_text(row.get("lineage_hash")),
    }


def _normalize_phase(value: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(value or {})
    return {
        "phase_id": _normalize_text(row.get("phase_id")),
        "label": _normalize_text(row.get("label")),
        "goal": _normalize_text(row.get("goal")),
        "deliverables": _normalize_text_list(row.get("deliverables")),
        "method_template_phase_ids": _normalize_text_list(row.get("method_template_phase_ids")),
        "budget_mode": _normalize_text(row.get("budget_mode")),
        "allowed_ambiguities": _normalize_text_list(row.get("allowed_ambiguities")),
        "must_resolve_before_pis": _normalize_text_list(row.get("must_resolve_before_pis")),
        "pis_readiness": _normalize_text(row.get("pis_readiness")).lower(),
    }


def _normalize_declared_constraint(value: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(value or {})
    return {
        "constraint_id": _normalize_text(row.get("constraint_id")),
        "constraint_kind": _normalize_text(row.get("constraint_kind")).lower(),
        "phase_id": _normalize_text(row.get("phase_id")),
        "method_phase_id": _normalize_text(row.get("method_phase_id")),
        "advisory_only": _normalize_bool(row.get("advisory_only"), default=False),
        "rationale": _normalize_text(row.get("rationale")),
    }


def _normalize_enrichment_receipt(value: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(value or {})
    return {
        "receipt_id": _normalize_text(row.get("receipt_id")),
        "policy_source_kind": _normalize_text(row.get("policy_source_kind")).lower(),
        "policy_ref": _normalize_text(row.get("policy_ref")),
        "target_phase_ids": _normalize_text_list(row.get("target_phase_ids")),
        "changed_fields": _normalize_text_list(row.get("changed_fields")),
        "change_summary": _normalize_text(row.get("change_summary")),
        "reason_codes": _normalize_text_list(row.get("reason_codes")),
        "receipt_state": _normalize_text(row.get("receipt_state")).lower(),
    }


def _normalize_policy_enrichment(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "method_template_ref": _normalize_text(row.get("method_template_ref")),
        "budget_policy_ref": _normalize_text(row.get("budget_policy_ref")),
        "selected_budget_mode": _normalize_text(row.get("selected_budget_mode")).lower(),
        "declared_constraints": [
            _normalize_declared_constraint(raw)
            for raw in list(row.get("declared_constraints") or [])
            if isinstance(raw, Mapping)
        ],
        "enrichment_receipts": [
            _normalize_enrichment_receipt(raw)
            for raw in list(row.get("enrichment_receipts") or [])
            if isinstance(raw, Mapping)
        ],
    }


def normalize_campaign_plan(raw_plan: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_plan or {})
    phases = [_normalize_phase(row) for row in list(raw.get("phases") or []) if isinstance(row, Mapping)]
    phases.sort(key=lambda row: row["phase_id"])
    normalized = {
        "schema_version": _normalize_text(raw.get("schema_version") or PLAN_SCHEMA_VERSION),
        "plan_id": _normalize_text(raw.get("plan_id")),
        "plan_version": _normalize_text(raw.get("plan_version")),
        "stable_identity": _normalize_stable_identity(raw.get("stable_identity")),
        "metadata": _normalize_metadata(raw.get("metadata")),
        "vocabulary_boundary": _normalize_vocabulary_boundary(raw.get("vocabulary_boundary")),
        "ambiguity_policy": _normalize_ambiguity_policy(raw.get("ambiguity_policy")),
        "provenance": _normalize_provenance(raw.get("provenance")),
        "phases": phases,
        "policy_enrichment": _normalize_policy_enrichment(raw.get("policy_enrichment")),
    }
    normalized["computed_fingerprint"] = _fingerprint(
        {key: value for key, value in normalized.items() if key != "computed_fingerprint"}
    )
    return normalized


def campaign_plan_contract_v1() -> CampaignPlanContractV1:
    return CampaignPlanContractV1(
        version=CONTRACT_SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        reference_examples_rel=REFERENCE_EXAMPLES_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        top_level_required_fields=TOP_LEVEL_REQUIRED_FIELDS,
        phase_required_fields=PHASE_REQUIRED_FIELDS,
        pis_readiness_states=PIS_READINESS_STATES,
        example_classes=EXAMPLE_CLASSES,
    )


def load_campaign_plan_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_campaign_plan_reference_examples(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / REFERENCE_EXAMPLES_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _is_safe_repo_rel(path_text: str) -> bool:
    text = _normalize_text(path_text)
    if not text:
        return False
    path = Path(text)
    return not path.is_absolute() and ".." not in path.parts


def validate_campaign_plan(raw_plan: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    raw = dict(raw_plan or {})
    normalized = normalize_campaign_plan(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", None, [], {}):
            errors.append({"field": field, "code": "missing_field", "message": f"campaign plan requires {field}"})

    if normalized["schema_version"] != PLAN_SCHEMA_VERSION:
        errors.append({"field": "schema_version", "code": "invalid_value", "message": "campaign plan schema_version drifted"})
    if not normalized["plan_version"]:
        errors.append({"field": "plan_version", "code": "missing_field", "message": "campaign plan requires a plan_version"})

    _append_missing_errors(errors, "stable_identity", normalized["stable_identity"], STABLE_IDENTITY_REQUIRED_FIELDS)
    if normalized["stable_identity"]["content_hash_algorithm"] != "sha256":
        errors.append({"field": "stable_identity.content_hash_algorithm", "code": "invalid_value", "message": "campaign plan content hashing must use sha256 in v1"})

    _append_missing_errors(errors, "metadata", normalized["metadata"], METADATA_REQUIRED_FIELDS)
    _append_missing_errors(errors, "ambiguity_policy", normalized["ambiguity_policy"], AMBIGUITY_POLICY_REQUIRED_FIELDS)
    _append_missing_errors(errors, "provenance", normalized["provenance"], PROVENANCE_REQUIRED_FIELDS)

    if normalized["metadata"]["source_intent_uid"] != normalized["provenance"]["source_intent_uid"]:
        errors.append({"field": "provenance.source_intent_uid", "code": "invalid_value", "message": "campaign plan provenance must point at the same source intent as metadata"})

    for key in FORBIDDEN_TOP_LEVEL_FIELDS:
        if key in raw:
            errors.append({"field": key, "code": "forbidden_pseudo_pis_detail", "message": "campaign plans may not inline pseudo-PIS execution detail"})

    boundary = normalized["vocabulary_boundary"]
    seen_terms: Dict[str, str] = {}
    for key in VOCABULARY_BOUNDARY_KEYS:
        values = boundary.get(key, [])
        if not values:
            errors.append({"field": f"vocabulary_boundary.{key}", "code": "missing_field", "message": f"campaign plan vocabulary boundary requires {key} entries"})
            continue
        for term in values:
            canonical = term.casefold()
            prior = seen_terms.get(canonical)
            if prior and prior != key:
                errors.append({"field": f"vocabulary_boundary.{key}", "code": "overlapping_vocabulary", "message": f"term {term!r} appears in both {prior} and {key}"})
            else:
                seen_terms[canonical] = key

    if not normalized["ambiguity_policy"]["allowed_in_campaign_plan"]:
        errors.append({"field": "ambiguity_policy.allowed_in_campaign_plan", "code": "under_specified_plan", "message": "campaign plans must name which ambiguities may remain open"})
    if not normalized["ambiguity_policy"]["must_resolve_before_pis"]:
        errors.append({"field": "ambiguity_policy.must_resolve_before_pis", "code": "under_specified_plan", "message": "campaign plans must name which fields become mandatory before PIS generation"})

    upstream_method_phase_ids = set(scientific_campaign_method_template.PHASE_IDS)
    upstream_budget_modes = set(budget_adaptive_research_policy.MODE_IDS)

    if not normalized["phases"]:
        errors.append({"field": "phases", "code": "under_specified_plan", "message": "campaign plans must declare at least one phase"})

    phase_ids: set[str] = set()
    for index, phase in enumerate(normalized["phases"]):
        prefix = f"phases[{index}]"
        for field in PHASE_REQUIRED_FIELDS:
            value = phase.get(field)
            if value in ("", None, [], {}) and field not in PHASE_ALLOW_EMPTY_FIELDS:
                errors.append({"field": f"{prefix}.{field}", "code": "missing_field", "message": f"{prefix}.{field} is required"})
        for key in FORBIDDEN_PHASE_FIELDS:
            if key in dict(raw.get("phases", [])[index]) if isinstance(raw.get("phases"), list) and index < len(raw.get("phases", [])) and isinstance(raw.get("phases", [])[index], Mapping) else False:
                errors.append({"field": f"{prefix}.{key}", "code": "forbidden_pseudo_pis_detail", "message": "campaign-plan phases may not embed executable PIS detail"})
        phase_id = phase["phase_id"]
        if phase_id in phase_ids:
            errors.append({"field": f"{prefix}.phase_id", "code": "duplicate_phase_id", "message": "phase_id values must be unique"})
        phase_ids.add(phase_id)
        if phase["budget_mode"] not in upstream_budget_modes:
            errors.append({"field": f"{prefix}.budget_mode", "code": "invalid_value", "message": "phase budget_mode must use the upstream budget-adaptive mode vocabulary"})
        if not phase["method_template_phase_ids"]:
            errors.append({"field": f"{prefix}.method_template_phase_ids", "code": "missing_field", "message": "campaign-plan phases must link to method-template phase ids"})
        elif any(value not in upstream_method_phase_ids for value in phase["method_template_phase_ids"]):
            errors.append({"field": f"{prefix}.method_template_phase_ids", "code": "invalid_value", "message": "campaign-plan phases must use the upstream method-template phase vocabulary"})
        if phase["pis_readiness"] not in PIS_READINESS_STATES:
            errors.append({"field": f"{prefix}.pis_readiness", "code": "invalid_value", "message": "illegal phase PIS-readiness state"})
        if phase["pis_readiness"] == "ready_for_pis_generation" and phase["must_resolve_before_pis"]:
            errors.append({"field": f"{prefix}.must_resolve_before_pis", "code": "invalid_value", "message": "ready phases may not keep unresolved pre-PIS requirements"})
        if phase["pis_readiness"] != "ready_for_pis_generation" and not phase["allowed_ambiguities"] and not phase["must_resolve_before_pis"]:
            errors.append({"field": prefix, "code": "under_specified_plan", "message": "non-ready phases must explain what remains ambiguous or unresolved"})

    root = Path(repo_root) if repo_root is not None else _repo_root()
    for field_name in ("upstream_contract_refs", "supporting_artifact_refs"):
        for ref in normalized["provenance"][field_name]:
            if not _is_safe_repo_rel(ref):
                errors.append({"field": f"provenance.{field_name}", "code": "invalid_ref", "message": "campaign-plan provenance refs must stay repo-relative and traversal-safe"})
                continue
            if not (root / ref).exists():
                errors.append({"field": f"provenance.{field_name}", "code": "missing_ref", "message": f"referenced artifact does not exist: {ref}"})

    policy_enrichment = normalized["policy_enrichment"]
    if any(policy_enrichment.values()):
        _append_missing_errors(errors, "policy_enrichment", policy_enrichment, POLICY_ENRICHMENT_REQUIRED_FIELDS)
        if policy_enrichment["selected_budget_mode"] not in upstream_budget_modes:
            errors.append(
                {
                    "field": "policy_enrichment.selected_budget_mode",
                    "code": "invalid_value",
                    "message": "policy enrichment must use the canonical upstream budget mode vocabulary",
                }
            )
        for field_name in ("method_template_ref", "budget_policy_ref"):
            ref = policy_enrichment[field_name]
            if not _is_safe_repo_rel(ref):
                errors.append(
                    {
                        "field": f"policy_enrichment.{field_name}",
                        "code": "invalid_ref",
                        "message": "policy enrichment refs must stay repo-relative and traversal-safe",
                    }
                )
            elif not (root / ref).exists():
                errors.append(
                    {
                        "field": f"policy_enrichment.{field_name}",
                        "code": "missing_ref",
                        "message": f"referenced enrichment artifact does not exist: {ref}",
                    }
                )

        seen_constraint_ids: set[str] = set()
        seen_requirements: set[tuple[str, str]] = set()
        seen_forbidden: set[tuple[str, str]] = set()
        known_phase_ids = {row["phase_id"] for row in normalized["phases"]}
        for index, row in enumerate(policy_enrichment["declared_constraints"]):
            prefix = f"policy_enrichment.declared_constraints[{index}]"
            _append_missing_errors(errors, prefix, row, DECLARED_CONSTRAINT_REQUIRED_FIELDS)
            if row["constraint_id"] in seen_constraint_ids:
                errors.append({"field": f"{prefix}.constraint_id", "code": "duplicate_constraint_id", "message": "declared constraint ids must be unique"})
            seen_constraint_ids.add(row["constraint_id"])
            if row["constraint_kind"] not in CONSTRAINT_KINDS:
                errors.append({"field": f"{prefix}.constraint_kind", "code": "invalid_value", "message": "declared constraint kind is unknown"})
            if row["phase_id"] not in known_phase_ids:
                errors.append({"field": f"{prefix}.phase_id", "code": "invalid_value", "message": "declared constraint must point at an existing campaign-plan phase"})
            if row["method_phase_id"] not in upstream_method_phase_ids:
                errors.append({"field": f"{prefix}.method_phase_id", "code": "invalid_value", "message": "declared constraint must use the upstream method-template phase vocabulary"})
            key = (row["phase_id"], row["method_phase_id"])
            if row["constraint_kind"] == "require_method_phase":
                seen_requirements.add(key)
            if row["constraint_kind"] == "forbid_method_phase":
                seen_forbidden.add(key)
        for phase_id, method_phase_id in sorted(seen_requirements & seen_forbidden):
            errors.append(
                {
                    "field": "policy_enrichment.declared_constraints",
                    "code": "contradictory_constraint",
                    "message": f"phase {phase_id} cannot both require and forbid method phase {method_phase_id}",
                }
            )

        for index, row in enumerate(policy_enrichment["enrichment_receipts"]):
            prefix = f"policy_enrichment.enrichment_receipts[{index}]"
            for field in ENRICHMENT_RECEIPT_REQUIRED_FIELDS:
                value = row.get(field)
                if value in ("", None, [], {}) and field not in ENRICHMENT_RECEIPT_ALLOW_EMPTY_FIELDS:
                    errors.append({"field": f"{prefix}.{field}", "code": "missing_field", "message": f"{prefix}.{field} is required"})
            if row["policy_source_kind"] not in POLICY_SOURCE_KINDS:
                errors.append({"field": f"{prefix}.policy_source_kind", "code": "invalid_value", "message": "unknown policy_source_kind"})
            if row["receipt_state"] not in ENRICHMENT_RECEIPT_STATES:
                errors.append({"field": f"{prefix}.receipt_state", "code": "invalid_value", "message": "unknown enrichment receipt_state"})
            if not _is_safe_repo_rel(row["policy_ref"]):
                errors.append({"field": f"{prefix}.policy_ref", "code": "invalid_ref", "message": "enrichment receipt policy refs must stay repo-relative and traversal-safe"})
            elif not (root / row["policy_ref"]).exists():
                errors.append({"field": f"{prefix}.policy_ref", "code": "missing_ref", "message": f"referenced enrichment policy does not exist: {row['policy_ref']}"})
            if not row["target_phase_ids"]:
                errors.append({"field": f"{prefix}.target_phase_ids", "code": "missing_field", "message": "enrichment receipts must name at least one target phase"})
            elif any(phase_id not in known_phase_ids for phase_id in row["target_phase_ids"]):
                errors.append({"field": f"{prefix}.target_phase_ids", "code": "invalid_value", "message": "enrichment receipts must target existing campaign-plan phases"})
            if row["receipt_state"] == "applied" and not row["changed_fields"]:
                errors.append({"field": f"{prefix}.changed_fields", "code": "missing_field", "message": "applied enrichment receipts must name at least one changed field"})
            if not row["reason_codes"]:
                errors.append({"field": f"{prefix}.reason_codes", "code": "missing_field", "message": "enrichment receipts must carry explicit reason codes"})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_plan": normalized,
        "computed_fingerprint": normalized["computed_fingerprint"],
    }


def validate_campaign_plan_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_campaign_plan_contract(repo_root=repo_root)
    issues: List[str] = []
    if payload.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        issues.append("schema_version must equal campaign_plan_contract_v1")
    if payload.get("uid") != UID:
        issues.append("uid must match the bound PIS")
    if payload.get("canonical_doc_rel") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the campaign-plan contract doc")
    if payload.get("reference_examples_rel") != REFERENCE_EXAMPLES_REL.as_posix():
        issues.append("reference_examples_rel must point at the campaign-plan example fixtures")
    if tuple(payload.get("top_level_required_fields") or ()) != TOP_LEVEL_REQUIRED_FIELDS:
        issues.append("top_level_required_fields drifted")
    if tuple(payload.get("phase_required_fields") or ()) != PHASE_REQUIRED_FIELDS:
        issues.append("phase_required_fields drifted")
    if tuple(payload.get("optional_top_level_fields") or ()) != OPTIONAL_TOP_LEVEL_FIELDS:
        issues.append("optional_top_level_fields drifted")
    if tuple(payload.get("pis_readiness_states") or ()) != PIS_READINESS_STATES:
        issues.append("pis_readiness_states drifted")
    return tuple(issues)


def validate_campaign_plan_reference_examples_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_campaign_plan_reference_examples(repo_root=repo_root)
    issues: List[str] = []
    if payload.get("schema_version") != REFERENCE_EXAMPLES_SCHEMA_VERSION:
        issues.append("reference examples schema_version drifted")
    examples = payload.get("examples")
    if not isinstance(examples, list) or not examples:
        return ("reference examples must define a non-empty examples list",)

    seen_classes: List[str] = []
    for index, row in enumerate(examples):
        prefix = f"examples[{index}]"
        if not isinstance(row, Mapping):
            issues.append(f"{prefix} must be an object")
            continue
        for field in REFERENCE_EXAMPLE_REQUIRED_FIELDS:
            if row.get(field) in ("", None, [], {}):
                issues.append(f"{prefix}.{field} is required")
        example_class = _normalize_text(row.get("example_class"))
        if example_class not in EXAMPLE_CLASSES:
            issues.append(f"{prefix}.example_class must use the closed example vocabulary")
            continue
        seen_classes.append(example_class)
        expected = _normalize_text(row.get("expected_validation_outcome"))
        if expected not in EXPECTED_EXAMPLE_OUTCOMES:
            issues.append(f"{prefix}.expected_validation_outcome must be accepted or rejected")
            continue
        result = validate_campaign_plan(row.get("campaign_plan") if isinstance(row.get("campaign_plan"), Mapping) else {}, repo_root=repo_root)
        if expected == "accepted" and not result["ok"]:
            issues.append(f"{prefix} expected accepted but validator rejected it")
        if expected == "rejected" and result["ok"]:
            issues.append(f"{prefix} expected rejected but validator accepted it")
    if tuple(sorted(seen_classes)) != tuple(sorted(EXAMPLE_CLASSES)):
        issues.append("reference examples must include exactly one valid broad-intent, one pseudo-PIS invalid, and one under-specified invalid example")
    return tuple(issues)
