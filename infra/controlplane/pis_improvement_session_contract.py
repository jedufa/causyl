"""Canonical PIS improvement session contract helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


CONTRACT_SCHEMA_VERSION = "pis_improvement_session_contract_v1"
SESSION_SCHEMA_VERSION = "pis_improvement_session_v1"
REFERENCE_EXAMPLES_SCHEMA_VERSION = "pis_improvement_session_reference_examples_v1"
CONTRACT_ARTIFACT_REL = Path("governance/pis_improvement_session_contract_v1.json")
REFERENCE_EXAMPLES_REL = Path("governance/pis_improvement_session_reference_examples_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_pis_improvement_session_v1.md")
UID = "PIS-20260412-062100Z-A1C3"

TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "session_id",
    "session_version",
    "stable_identity",
    "metadata",
    "source_pis_ref",
    "model_profiles",
    "iteration_policy",
    "workflow_state",
    "candidate_selection",
    "artifact_pointers",
    "revision_ledger",
)
CRITIQUE_PACKET_REQUIRED_FIELDS: Tuple[str, ...] = (
    "packet_schema_version",
    "packet_id",
    "round_id",
    "candidate_ref",
    "reviewer_profile_id",
    "summary",
    "packet_provenance",
    "critique_items",
    "overall_verdict",
)
CRITIQUE_PACKET_PROVENANCE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "source_pis_hash",
    "cited_artifacts",
)
CRITIQUE_ITEM_REQUIRED_FIELDS: Tuple[str, ...] = (
    "critique_id",
    "category",
    "severity",
    "finding",
    "evidence",
    "proposed_resolution",
)
REVISER_PACKET_REQUIRED_FIELDS: Tuple[str, ...] = (
    "packet_schema_version",
    "packet_id",
    "round_id",
    "candidate_ref",
    "reviser_profile_id",
    "source_critique_packet_id",
    "summary",
    "resolution_items",
    "overall_status",
)
RESOLUTION_ITEM_REQUIRED_FIELDS: Tuple[str, ...] = (
    "critique_id",
    "disposition",
    "response",
    "change_summary",
)
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
SOURCE_PIS_REF_REQUIRED_FIELDS: Tuple[str, ...] = (
    "ledger_uid",
    "pis_path",
    "content_sha256",
)
MODEL_PROFILE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "profile_id",
    "role",
    "model_family",
    "selection_reason",
)
ITERATION_POLICY_REQUIRED_FIELDS: Tuple[str, ...] = (
    "default_iteration_cap",
    "stop_conditions",
    "fail_closed_on_invalid_revision_entry",
)
WORKFLOW_STATE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "status",
    "current_iteration",
    "last_transition_utc",
)
CANDIDATE_SELECTION_REQUIRED_FIELDS: Tuple[str, ...] = (
    "selection_basis",
    "candidate_pool_materialization_ref",
    "preserved_filters",
    "preserved_exclusions",
    "candidate_refs",
    "selected_candidate_refs",
    "deterministic_order",
)
FILTER_REQUIRED_FIELDS: Tuple[str, ...] = (
    "filter_id",
    "field",
    "operator",
    "value",
)
EXCLUSION_REQUIRED_FIELDS: Tuple[str, ...] = (
    "exclusion_id",
    "candidate_ref",
    "reason_code",
)
CANDIDATE_REF_REQUIRED_FIELDS: Tuple[str, ...] = (
    "candidate_ref",
    "source_kind",
    "locator",
    "recency_basis",
)
ARTIFACT_POINTER_REQUIRED_FIELDS: Tuple[str, ...] = (
    "session_manifest",
    "candidate_snapshot",
    "revision_ledger",
    "final_recommendation",
)
REVISION_LEDGER_REQUIRED_FIELDS: Tuple[str, ...] = (
    "ledger_schema_version",
    "critique_rounds",
    "revision_deltas",
    "recommendation_history",
)
CRITIQUE_ROUND_REQUIRED_FIELDS: Tuple[str, ...] = (
    "round_id",
    "iteration_index",
    "candidate_ref",
    "reviewer_profile_id",
    "critique_packet_ref",
    "critique_packet",
    "status",
)
REVISION_DELTA_REQUIRED_FIELDS: Tuple[str, ...] = (
    "delta_id",
    "round_id",
    "reviser_profile_id",
    "proposed_revision_ref",
    "reviser_packet",
    "change_summary",
)
RECOMMENDATION_REQUIRED_FIELDS: Tuple[str, ...] = (
    "recommendation_id",
    "decided_at_utc",
    "outcome",
    "winning_candidate_ref",
    "rationale_ref",
)
REFERENCE_EXAMPLE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "example_id",
    "example_class",
    "expected_validation_outcome",
    "session_record",
)
PACKET_REFERENCE_EXAMPLE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "example_id",
    "example_class",
    "expected_validation_outcome",
    "packet_kind",
    "packet_record",
)

WORKFLOW_STATUSES: Tuple[str, ...] = ("draft", "running", "completed", "failed")
MODEL_ROLES: Tuple[str, ...] = ("reviewer", "reviser", "arbiter")
SELECTION_BASIS_VALUES: Tuple[str, ...] = ("recent_ledger_items", "explicit_file_list")
CANDIDATE_SOURCE_KINDS: Tuple[str, ...] = ("ledger", "file")
ROUND_STATUSES: Tuple[str, ...] = ("accepted", "rejected", "superseded")
RECOMMENDATION_OUTCOMES: Tuple[str, ...] = ("adopt_revision", "keep_source", "needs_operator_review")
EXAMPLE_CLASSES: Tuple[str, ...] = (
    "valid_recent_pis_session",
    "invalid_hidden_context_selection",
    "invalid_revision_history_gap",
)
PACKET_EXAMPLE_CLASSES: Tuple[str, ...] = (
    "valid_actionable_critique_packet",
    "invalid_generic_praise_critique_packet",
    "invalid_opaque_reviser_packet",
)
EXPECTED_EXAMPLE_OUTCOMES: Tuple[str, ...] = ("accepted", "rejected")
PACKET_KINDS: Tuple[str, ...] = ("critique", "reviser")
CRITIQUE_PACKET_SCHEMA_VERSION = "pis_improvement_critique_packet_v1"
REVISER_PACKET_SCHEMA_VERSION = "pis_improvement_reviser_packet_v1"
CRITIQUE_CATEGORIES: Tuple[str, ...] = (
    "contract_gap",
    "evidence_gap",
    "provenance_gap",
    "resolution_gap",
    "scope_boundary",
    "test_gap",
)
CRITIQUE_SEVERITIES: Tuple[str, ...] = ("critical", "major", "minor")
CRITIQUE_VERDICTS: Tuple[str, ...] = ("revision_required", "candidate_acceptable", "candidate_rejected")
RESOLUTION_DISPOSITIONS: Tuple[str, ...] = ("addressed", "rejected", "deferred")
REVISER_STATUSES: Tuple[str, ...] = ("ready_for_review", "partial", "blocked")
FORBIDDEN_TOP_LEVEL_FIELDS: Tuple[str, ...] = (
    "chat_context_summary",
    "latest_rewrite_blob",
    "mutable_source_pis_body",
)
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "source_pis_reference_iteration_policy_and_artifact_pointers_are_mandatory",
    "candidate_selection_must_be_replayable_from_file_or_ledger_backed_inputs",
    "hidden_chat_context_candidate_selection_is_rejected",
    "selected_candidates_must_come_from_the_materialized_candidate_pool",
    "revision_history_requires_explicit_critique_and_delta_artifact_refs",
    "critique_packets_must_be_specific_evidence_bearing_and_non_flattering",
    "reviser_packets_must_map_every_resolution_back_to_a_concrete_critique_item",
    "recommendation_history_cannot_overwrite_prior_rounds",
    "mutable_source_pis_payloads_forbidden_inline",
)
GENERIC_PRAISE_PHRASES: Tuple[str, ...] = (
    "good job",
    "great job",
    "great work",
    "nice work",
    "well done",
    "looks good",
    "lgtm",
    "ship it",
    "sounds good",
    "solid work",
)


@dataclass(frozen=True)
class PISImprovementSessionContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    reference_examples_rel: Path
    canonical_doc_rel: Path
    top_level_required_fields: Tuple[str, ...]
    critique_packet_required_fields: Tuple[str, ...]
    reviser_packet_required_fields: Tuple[str, ...]
    candidate_selection_required_fields: Tuple[str, ...]
    revision_ledger_required_fields: Tuple[str, ...]
    fail_closed_rules: Tuple[str, ...]


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


def _normalize_safe_repo_rel_list(value: Any) -> List[str]:
    return [text for text in _normalize_text_list(value) if _is_safe_repo_rel(text)]


def _normalize_positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed >= 0 else 0


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


def _append_missing_errors(errors: List[Dict[str, str]], prefix: str, row: Mapping[str, Any], fields: Sequence[str]) -> None:
    for field in fields:
        value = row.get(field)
        if value in ("", None, [], {}):
            errors.append({"field": f"{prefix}.{field}", "code": "missing_field", "message": f"{prefix}.{field} is required"})


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_safe_repo_rel(path_text: str) -> bool:
    text = _normalize_text(path_text)
    if not text:
        return False
    path = Path(text)
    return not path.is_absolute() and ".." not in path.parts


def _normalize_stable_identity(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "canonicalization": _normalize_text(row.get("canonicalization")),
        "content_hash_algorithm": _normalize_text(row.get("content_hash_algorithm")).lower(),
        "schema_migration_policy": _normalize_text(row.get("schema_migration_policy")),
    }


def _normalize_metadata(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "title": _normalize_text(row.get("title")),
        "created_at_utc": _normalize_text(row.get("created_at_utc")),
        "source_intent_uid": _normalize_text(row.get("source_intent_uid")),
        "ledger_refs": _normalize_text_list(row.get("ledger_refs")),
    }


def _normalize_source_pis_ref(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "ledger_uid": _normalize_text(row.get("ledger_uid")),
        "pis_path": _normalize_text(row.get("pis_path")),
        "content_sha256": _normalize_text(row.get("content_sha256")).lower(),
    }


def _normalize_model_profile(value: Mapping[str, Any]) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "profile_id": _normalize_text(row.get("profile_id")),
        "role": _normalize_text(row.get("role")).lower(),
        "model_family": _normalize_text(row.get("model_family")),
        "selection_reason": _normalize_text(row.get("selection_reason")),
    }


def _normalize_iteration_policy(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "default_iteration_cap": _normalize_positive_int(row.get("default_iteration_cap")),
        "stop_conditions": _normalize_text_list(row.get("stop_conditions")),
        "fail_closed_on_invalid_revision_entry": _normalize_bool(row.get("fail_closed_on_invalid_revision_entry"), default=True),
    }


def _normalize_workflow_state(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "status": _normalize_text(row.get("status")).lower(),
        "current_iteration": _normalize_positive_int(row.get("current_iteration")),
        "last_transition_utc": _normalize_text(row.get("last_transition_utc")),
    }


def _normalize_filter(value: Mapping[str, Any]) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "filter_id": _normalize_text(row.get("filter_id")),
        "field": _normalize_text(row.get("field")),
        "operator": _normalize_text(row.get("operator")),
        "value": _normalize_text(row.get("value")),
    }


def _normalize_exclusion(value: Mapping[str, Any]) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "exclusion_id": _normalize_text(row.get("exclusion_id")),
        "candidate_ref": _normalize_text(row.get("candidate_ref")),
        "reason_code": _normalize_text(row.get("reason_code")),
    }


def _normalize_candidate_ref(value: Mapping[str, Any]) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "candidate_ref": _normalize_text(row.get("candidate_ref")),
        "source_kind": _normalize_text(row.get("source_kind")).lower(),
        "locator": _normalize_text(row.get("locator")),
        "recency_basis": _normalize_text(row.get("recency_basis")),
    }


def _normalize_candidate_selection(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "selection_basis": _normalize_text(row.get("selection_basis")).lower(),
        "candidate_pool_materialization_ref": _normalize_text(row.get("candidate_pool_materialization_ref")),
        "preserved_filters": [_normalize_filter(raw) for raw in list(row.get("preserved_filters") or []) if isinstance(raw, Mapping)],
        "preserved_exclusions": [_normalize_exclusion(raw) for raw in list(row.get("preserved_exclusions") or []) if isinstance(raw, Mapping)],
        "candidate_refs": [_normalize_candidate_ref(raw) for raw in list(row.get("candidate_refs") or []) if isinstance(raw, Mapping)],
        "selected_candidate_refs": _normalize_text_list(row.get("selected_candidate_refs")),
        "deterministic_order": _normalize_text_list(row.get("deterministic_order")),
    }


def _normalize_artifact_pointers(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {field: _normalize_text(row.get(field)) for field in ARTIFACT_POINTER_REQUIRED_FIELDS}


def _normalize_critique_item(value: Mapping[str, Any]) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "critique_id": _normalize_text(row.get("critique_id")),
        "category": _normalize_text(row.get("category")).lower(),
        "severity": _normalize_text(row.get("severity")).lower(),
        "finding": _normalize_text(row.get("finding")),
        "evidence": _normalize_text(row.get("evidence")),
        "proposed_resolution": _normalize_text(row.get("proposed_resolution")),
    }


def _normalize_critique_packet_provenance(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "source_pis_hash": _normalize_text(row.get("source_pis_hash")).lower(),
        "cited_artifacts": _normalize_safe_repo_rel_list(row.get("cited_artifacts")),
    }


def _normalize_critique_packet(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "packet_schema_version": _normalize_text(row.get("packet_schema_version") or CRITIQUE_PACKET_SCHEMA_VERSION),
        "packet_id": _normalize_text(row.get("packet_id")),
        "round_id": _normalize_text(row.get("round_id")),
        "candidate_ref": _normalize_text(row.get("candidate_ref")),
        "reviewer_profile_id": _normalize_text(row.get("reviewer_profile_id")),
        "summary": _normalize_text(row.get("summary")),
        "packet_provenance": _normalize_critique_packet_provenance(row.get("packet_provenance")),
        "critique_items": [_normalize_critique_item(raw) for raw in list(row.get("critique_items") or []) if isinstance(raw, Mapping)],
        "overall_verdict": _normalize_text(row.get("overall_verdict")).lower(),
    }


def _normalize_resolution_item(value: Mapping[str, Any]) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "critique_id": _normalize_text(row.get("critique_id")),
        "disposition": _normalize_text(row.get("disposition")).lower(),
        "response": _normalize_text(row.get("response")),
        "change_summary": _normalize_text(row.get("change_summary")),
    }


def _normalize_reviser_packet(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "packet_schema_version": _normalize_text(row.get("packet_schema_version") or REVISER_PACKET_SCHEMA_VERSION),
        "packet_id": _normalize_text(row.get("packet_id")),
        "round_id": _normalize_text(row.get("round_id")),
        "candidate_ref": _normalize_text(row.get("candidate_ref")),
        "reviser_profile_id": _normalize_text(row.get("reviser_profile_id")),
        "source_critique_packet_id": _normalize_text(row.get("source_critique_packet_id")),
        "summary": _normalize_text(row.get("summary")),
        "resolution_items": [_normalize_resolution_item(raw) for raw in list(row.get("resolution_items") or []) if isinstance(raw, Mapping)],
        "overall_status": _normalize_text(row.get("overall_status")).lower(),
    }


def _normalize_critique_round(value: Mapping[str, Any]) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "round_id": _normalize_text(row.get("round_id")),
        "iteration_index": _normalize_positive_int(row.get("iteration_index")),
        "candidate_ref": _normalize_text(row.get("candidate_ref")),
        "reviewer_profile_id": _normalize_text(row.get("reviewer_profile_id")),
        "critique_packet_ref": _normalize_text(row.get("critique_packet_ref")),
        "critique_packet": _normalize_critique_packet(row.get("critique_packet")),
        "status": _normalize_text(row.get("status")).lower(),
    }


def _normalize_revision_delta(value: Mapping[str, Any]) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "delta_id": _normalize_text(row.get("delta_id")),
        "round_id": _normalize_text(row.get("round_id")),
        "reviser_profile_id": _normalize_text(row.get("reviser_profile_id")),
        "proposed_revision_ref": _normalize_text(row.get("proposed_revision_ref")),
        "reviser_packet": _normalize_reviser_packet(row.get("reviser_packet")),
        "change_summary": _normalize_text(row.get("change_summary")),
    }


def _normalize_recommendation(value: Mapping[str, Any]) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "recommendation_id": _normalize_text(row.get("recommendation_id")),
        "decided_at_utc": _normalize_text(row.get("decided_at_utc")),
        "outcome": _normalize_text(row.get("outcome")).lower(),
        "winning_candidate_ref": _normalize_text(row.get("winning_candidate_ref")),
        "rationale_ref": _normalize_text(row.get("rationale_ref")),
    }


def _normalize_revision_ledger(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "ledger_schema_version": _normalize_text(row.get("ledger_schema_version") or SESSION_SCHEMA_VERSION),
        "critique_rounds": [_normalize_critique_round(raw) for raw in list(row.get("critique_rounds") or []) if isinstance(raw, Mapping)],
        "revision_deltas": [_normalize_revision_delta(raw) for raw in list(row.get("revision_deltas") or []) if isinstance(raw, Mapping)],
        "recommendation_history": [_normalize_recommendation(raw) for raw in list(row.get("recommendation_history") or []) if isinstance(raw, Mapping)],
    }


def normalize_pis_improvement_session(raw_session: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_session or {})
    normalized = {
        "schema_version": _normalize_text(raw.get("schema_version") or SESSION_SCHEMA_VERSION),
        "session_id": _normalize_text(raw.get("session_id")),
        "session_version": _normalize_text(raw.get("session_version")),
        "stable_identity": _normalize_stable_identity(raw.get("stable_identity")),
        "metadata": _normalize_metadata(raw.get("metadata")),
        "source_pis_ref": _normalize_source_pis_ref(raw.get("source_pis_ref")),
        "model_profiles": [_normalize_model_profile(raw_row) for raw_row in list(raw.get("model_profiles") or []) if isinstance(raw_row, Mapping)],
        "iteration_policy": _normalize_iteration_policy(raw.get("iteration_policy")),
        "workflow_state": _normalize_workflow_state(raw.get("workflow_state")),
        "candidate_selection": _normalize_candidate_selection(raw.get("candidate_selection")),
        "artifact_pointers": _normalize_artifact_pointers(raw.get("artifact_pointers")),
        "revision_ledger": _normalize_revision_ledger(raw.get("revision_ledger")),
    }
    normalized["computed_fingerprint"] = _fingerprint(
        {key: value for key, value in normalized.items() if key != "computed_fingerprint"}
    )
    return normalized


def pis_improvement_session_contract_v1() -> PISImprovementSessionContractV1:
    return PISImprovementSessionContractV1(
        version=CONTRACT_SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        reference_examples_rel=REFERENCE_EXAMPLES_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        top_level_required_fields=TOP_LEVEL_REQUIRED_FIELDS,
        critique_packet_required_fields=CRITIQUE_PACKET_REQUIRED_FIELDS,
        reviser_packet_required_fields=REVISER_PACKET_REQUIRED_FIELDS,
        candidate_selection_required_fields=CANDIDATE_SELECTION_REQUIRED_FIELDS,
        revision_ledger_required_fields=REVISION_LEDGER_REQUIRED_FIELDS,
        fail_closed_rules=FAIL_CLOSED_RULES,
    )


def load_pis_improvement_session_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_pis_improvement_session_reference_examples(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / REFERENCE_EXAMPLES_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _looks_generic_praise(text: str) -> bool:
    normalized = " ".join(_normalize_text(text).lower().split())
    if not normalized:
        return True
    if normalized in GENERIC_PRAISE_PHRASES:
        return True
    return any(phrase in normalized for phrase in GENERIC_PRAISE_PHRASES) and len(normalized.split()) <= 10


def validate_pis_improvement_critique_packet(
    raw_packet: Mapping[str, Any],
    *,
    expected_round_id: str | None = None,
    expected_candidate_ref: str | None = None,
    expected_reviewer_profile_id: str | None = None,
) -> Dict[str, Any]:
    normalized = _normalize_critique_packet(raw_packet)
    errors: List[Dict[str, str]] = []

    _append_missing_errors(errors, "critique_packet", normalized, CRITIQUE_PACKET_REQUIRED_FIELDS)
    if normalized["packet_schema_version"] != CRITIQUE_PACKET_SCHEMA_VERSION:
        errors.append({"field": "critique_packet.packet_schema_version", "code": "invalid_value", "message": "critique packet schema version is not canonical"})
    if expected_round_id and normalized["round_id"] != expected_round_id:
        errors.append({"field": "critique_packet.round_id", "code": "round_mismatch", "message": "critique packet round_id must match its parent critique round"})
    if expected_candidate_ref and normalized["candidate_ref"] != expected_candidate_ref:
        errors.append({"field": "critique_packet.candidate_ref", "code": "candidate_mismatch", "message": "critique packet candidate_ref must match its parent critique round"})
    if expected_reviewer_profile_id and normalized["reviewer_profile_id"] != expected_reviewer_profile_id:
        errors.append({"field": "critique_packet.reviewer_profile_id", "code": "profile_mismatch", "message": "critique packet reviewer_profile_id must match its parent critique round"})
    if _looks_generic_praise(normalized["summary"]):
        errors.append({"field": "critique_packet.summary", "code": "generic_praise", "message": "critique packet summary must name a concrete defect or risk"})

    provenance = normalized["packet_provenance"]
    _append_missing_errors(errors, "critique_packet.packet_provenance", provenance, CRITIQUE_PACKET_PROVENANCE_REQUIRED_FIELDS)
    if len(provenance["source_pis_hash"]) != 64:
        errors.append({"field": "critique_packet.packet_provenance.source_pis_hash", "code": "invalid_value", "message": "critique packet source_pis_hash must be a full sha256 hex digest"})
    if not provenance["cited_artifacts"]:
        errors.append({"field": "critique_packet.packet_provenance.cited_artifacts", "code": "missing_field", "message": "critique packet must cite at least one artifact"})

    item_ids: set[str] = set()
    if not normalized["critique_items"]:
        errors.append({"field": "critique_packet.critique_items", "code": "missing_field", "message": "critique packet must contain at least one actionable critique item"})
    for index, row in enumerate(normalized["critique_items"]):
        prefix = f"critique_packet.critique_items[{index}]"
        _append_missing_errors(errors, prefix, row, CRITIQUE_ITEM_REQUIRED_FIELDS)
        if row["critique_id"] in item_ids:
            errors.append({"field": f"{prefix}.critique_id", "code": "duplicate_id", "message": "critique_id must be unique within the packet"})
        if row["critique_id"]:
            item_ids.add(row["critique_id"])
        if row["category"] not in CRITIQUE_CATEGORIES:
            errors.append({"field": f"{prefix}.category", "code": "invalid_value", "message": "critique category is not canonical"})
        if row["severity"] not in CRITIQUE_SEVERITIES:
            errors.append({"field": f"{prefix}.severity", "code": "invalid_value", "message": "critique severity is not canonical"})
        for field_name in ("finding", "evidence", "proposed_resolution"):
            if _looks_generic_praise(row[field_name]):
                errors.append({"field": f"{prefix}.{field_name}", "code": "generic_praise", "message": f"{field_name} must be specific and non-flattering"})

    if normalized["overall_verdict"] not in CRITIQUE_VERDICTS:
        errors.append({"field": "critique_packet.overall_verdict", "code": "invalid_value", "message": "critique packet verdict is not canonical"})

    return {"ok": not errors, "errors": errors, "normalized_packet": normalized}


def validate_pis_improvement_reviser_packet(
    raw_packet: Mapping[str, Any],
    *,
    expected_round_id: str | None = None,
    expected_candidate_ref: str | None = None,
    expected_reviser_profile_id: str | None = None,
    expected_critique_packet: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized = _normalize_reviser_packet(raw_packet)
    errors: List[Dict[str, str]] = []

    _append_missing_errors(errors, "reviser_packet", normalized, REVISER_PACKET_REQUIRED_FIELDS)
    if normalized["packet_schema_version"] != REVISER_PACKET_SCHEMA_VERSION:
        errors.append({"field": "reviser_packet.packet_schema_version", "code": "invalid_value", "message": "reviser packet schema version is not canonical"})
    if expected_round_id and normalized["round_id"] != expected_round_id:
        errors.append({"field": "reviser_packet.round_id", "code": "round_mismatch", "message": "reviser packet round_id must match its parent revision delta"})
    if expected_candidate_ref and normalized["candidate_ref"] != expected_candidate_ref:
        errors.append({"field": "reviser_packet.candidate_ref", "code": "candidate_mismatch", "message": "reviser packet candidate_ref must match its parent revision delta"})
    if expected_reviser_profile_id and normalized["reviser_profile_id"] != expected_reviser_profile_id:
        errors.append({"field": "reviser_packet.reviser_profile_id", "code": "profile_mismatch", "message": "reviser packet reviser_profile_id must match its parent revision delta"})
    if _looks_generic_praise(normalized["summary"]):
        errors.append({"field": "reviser_packet.summary", "code": "opaque_summary", "message": "reviser packet summary must explain the concrete response to critique"})
    if normalized["overall_status"] not in REVISER_STATUSES:
        errors.append({"field": "reviser_packet.overall_status", "code": "invalid_value", "message": "reviser packet status is not canonical"})

    resolution_item_ids: set[str] = set()
    if not normalized["resolution_items"]:
        errors.append({"field": "reviser_packet.resolution_items", "code": "missing_field", "message": "reviser packet must resolve at least one critique item"})
    for index, row in enumerate(normalized["resolution_items"]):
        prefix = f"reviser_packet.resolution_items[{index}]"
        _append_missing_errors(errors, prefix, row, RESOLUTION_ITEM_REQUIRED_FIELDS)
        critique_id = row["critique_id"]
        if critique_id in resolution_item_ids:
            errors.append({"field": f"{prefix}.critique_id", "code": "duplicate_id", "message": "each critique_id may appear at most once in a reviser packet"})
        if critique_id:
            resolution_item_ids.add(critique_id)
        if row["disposition"] not in RESOLUTION_DISPOSITIONS:
            errors.append({"field": f"{prefix}.disposition", "code": "invalid_value", "message": "resolution disposition is not canonical"})
        if _looks_generic_praise(row["response"]):
            errors.append({"field": f"{prefix}.response", "code": "opaque_response", "message": "resolution response must explain the chosen action or refusal"})
        if row["disposition"] == "addressed" and _looks_generic_praise(row["change_summary"]):
            errors.append({"field": f"{prefix}.change_summary", "code": "opaque_rewrite", "message": "addressed resolutions must describe a concrete change, not a generic rewrite"})

    if expected_critique_packet is not None:
        critique_packet = _normalize_critique_packet(expected_critique_packet)
        if normalized["source_critique_packet_id"] != critique_packet["packet_id"]:
            errors.append({"field": "reviser_packet.source_critique_packet_id", "code": "unknown_ref", "message": "reviser packet must point to the critique packet it answers"})
        critique_ids = {row["critique_id"] for row in critique_packet["critique_items"] if row["critique_id"]}
        for critique_id in sorted(resolution_item_ids - critique_ids):
            errors.append({"field": "reviser_packet.resolution_items", "code": "unknown_ref", "message": f"resolution references unknown critique_id {critique_id}"})
        for critique_id in sorted(critique_ids - resolution_item_ids):
            errors.append({"field": "reviser_packet.resolution_items", "code": "missing_resolution", "message": f"reviser packet must resolve critique_id {critique_id}"})

    return {"ok": not errors, "errors": errors, "normalized_packet": normalized}


def validate_pis_improvement_session(raw_session: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_session or {})
    normalized = normalize_pis_improvement_session(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        if normalized.get(field) in ("", None, [], {}):
            errors.append({"field": field, "code": "missing_field", "message": f"session record requires {field}"})

    if normalized["schema_version"] != SESSION_SCHEMA_VERSION:
        errors.append({"field": "schema_version", "code": "invalid_value", "message": "session schema_version drifted"})
    if not normalized["session_version"]:
        errors.append({"field": "session_version", "code": "missing_field", "message": "session_version is required"})

    _append_missing_errors(errors, "stable_identity", normalized["stable_identity"], STABLE_IDENTITY_REQUIRED_FIELDS)
    if normalized["stable_identity"]["content_hash_algorithm"] != "sha256":
        errors.append({"field": "stable_identity.content_hash_algorithm", "code": "invalid_value", "message": "session content hashing must use sha256"})

    _append_missing_errors(errors, "metadata", normalized["metadata"], METADATA_REQUIRED_FIELDS)
    _append_missing_errors(errors, "source_pis_ref", normalized["source_pis_ref"], SOURCE_PIS_REF_REQUIRED_FIELDS)
    if not _is_safe_repo_rel(normalized["source_pis_ref"]["pis_path"]):
        errors.append({"field": "source_pis_ref.pis_path", "code": "invalid_ref", "message": "source PIS path must be a safe repo-relative path"})
    if len(normalized["source_pis_ref"]["content_sha256"]) != 64:
        errors.append({"field": "source_pis_ref.content_sha256", "code": "invalid_value", "message": "source PIS hash must be a full sha256 hex digest"})

    if not normalized["model_profiles"]:
        errors.append({"field": "model_profiles", "code": "missing_field", "message": "at least one model profile is required"})
    profile_ids: set[str] = set()
    for index, row in enumerate(normalized["model_profiles"]):
        prefix = f"model_profiles[{index}]"
        _append_missing_errors(errors, prefix, row, MODEL_PROFILE_REQUIRED_FIELDS)
        if row["profile_id"] in profile_ids:
            errors.append({"field": f"{prefix}.profile_id", "code": "duplicate_id", "message": "profile_id must be unique"})
        if row["profile_id"]:
            profile_ids.add(row["profile_id"])
        if row["role"] not in MODEL_ROLES:
            errors.append({"field": f"{prefix}.role", "code": "invalid_value", "message": "model profile role is not canonical"})

    _append_missing_errors(errors, "iteration_policy", normalized["iteration_policy"], ITERATION_POLICY_REQUIRED_FIELDS)
    if normalized["iteration_policy"]["default_iteration_cap"] <= 0:
        errors.append({"field": "iteration_policy.default_iteration_cap", "code": "invalid_value", "message": "default_iteration_cap must be a positive integer"})

    _append_missing_errors(errors, "workflow_state", normalized["workflow_state"], WORKFLOW_STATE_REQUIRED_FIELDS)
    if normalized["workflow_state"]["status"] not in WORKFLOW_STATUSES:
        errors.append({"field": "workflow_state.status", "code": "invalid_value", "message": "workflow status is not canonical"})
    if normalized["workflow_state"]["current_iteration"] > normalized["iteration_policy"]["default_iteration_cap"]:
        errors.append({"field": "workflow_state.current_iteration", "code": "invalid_value", "message": "current_iteration may not exceed default_iteration_cap"})

    candidate_selection = normalized["candidate_selection"]
    _append_missing_errors(errors, "candidate_selection", candidate_selection, CANDIDATE_SELECTION_REQUIRED_FIELDS)
    if candidate_selection["selection_basis"] not in SELECTION_BASIS_VALUES:
        errors.append({"field": "candidate_selection.selection_basis", "code": "invalid_selection_basis", "message": "candidate selection must be ledger or file backed"})
    if not _is_safe_repo_rel(candidate_selection["candidate_pool_materialization_ref"]):
        errors.append({"field": "candidate_selection.candidate_pool_materialization_ref", "code": "invalid_ref", "message": "candidate pool materialization ref must be repo-relative"})

    candidate_ids: set[str] = set()
    for index, row in enumerate(candidate_selection["candidate_refs"]):
        prefix = f"candidate_selection.candidate_refs[{index}]"
        _append_missing_errors(errors, prefix, row, CANDIDATE_REF_REQUIRED_FIELDS)
        candidate_ref = row["candidate_ref"]
        if candidate_ref in candidate_ids:
            errors.append({"field": f"{prefix}.candidate_ref", "code": "duplicate_id", "message": "candidate_ref must be unique"})
        if candidate_ref:
            candidate_ids.add(candidate_ref)
        if row["source_kind"] not in CANDIDATE_SOURCE_KINDS:
            errors.append({"field": f"{prefix}.source_kind", "code": "invalid_value", "message": "candidate source_kind is not canonical"})
        if row["source_kind"] == "file" and not _is_safe_repo_rel(row["locator"]):
            errors.append({"field": f"{prefix}.locator", "code": "invalid_ref", "message": "file candidates must use safe repo-relative locators"})

    for index, row in enumerate(candidate_selection["preserved_filters"]):
        _append_missing_errors(errors, f"candidate_selection.preserved_filters[{index}]", row, FILTER_REQUIRED_FIELDS)
    excluded_candidate_refs: set[str] = set()
    for index, row in enumerate(candidate_selection["preserved_exclusions"]):
        prefix = f"candidate_selection.preserved_exclusions[{index}]"
        _append_missing_errors(errors, prefix, row, EXCLUSION_REQUIRED_FIELDS)
        if row["candidate_ref"] and row["candidate_ref"] not in candidate_ids:
            errors.append({"field": f"{prefix}.candidate_ref", "code": "unknown_ref", "message": "exclusions must reference a materialized candidate"})
        if row["candidate_ref"]:
            excluded_candidate_refs.add(row["candidate_ref"])

    selected_ids = candidate_selection["selected_candidate_refs"]
    deterministic_order = candidate_selection["deterministic_order"]
    if not selected_ids:
        errors.append({"field": "candidate_selection.selected_candidate_refs", "code": "missing_field", "message": "at least one selected candidate is required"})
    if sorted(deterministic_order) != sorted(candidate_ids):
        errors.append({"field": "candidate_selection.deterministic_order", "code": "invalid_deterministic_order", "message": "deterministic_order must name every candidate exactly once"})
    for candidate_ref in selected_ids:
        if candidate_ref not in candidate_ids:
            errors.append({"field": "candidate_selection.selected_candidate_refs", "code": "unknown_ref", "message": "selected candidates must come from candidate_refs"})
        if candidate_ref in excluded_candidate_refs:
            errors.append({"field": "candidate_selection.selected_candidate_refs", "code": "selected_excluded_candidate", "message": "excluded candidates may not remain selected"})

    artifact_pointers = normalized["artifact_pointers"]
    _append_missing_errors(errors, "artifact_pointers", artifact_pointers, ARTIFACT_POINTER_REQUIRED_FIELDS)
    for field, value in artifact_pointers.items():
        if value and not _is_safe_repo_rel(value):
            errors.append({"field": f"artifact_pointers.{field}", "code": "invalid_ref", "message": "artifact pointers must be repo-relative"})

    revision_ledger = normalized["revision_ledger"]
    _append_missing_errors(errors, "revision_ledger", revision_ledger, REVISION_LEDGER_REQUIRED_FIELDS)
    if revision_ledger["ledger_schema_version"] != SESSION_SCHEMA_VERSION:
        errors.append({"field": "revision_ledger.ledger_schema_version", "code": "invalid_value", "message": "revision ledger schema_version must align with the session contract"})

    round_ids: set[str] = set()
    critique_rounds_by_id: Dict[str, Dict[str, Any]] = {}
    critique_packets_by_round_id: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(revision_ledger["critique_rounds"]):
        prefix = f"revision_ledger.critique_rounds[{index}]"
        _append_missing_errors(errors, prefix, row, CRITIQUE_ROUND_REQUIRED_FIELDS)
        if row["round_id"] in round_ids:
            errors.append({"field": f"{prefix}.round_id", "code": "duplicate_id", "message": "round_id must be unique"})
        if row["round_id"]:
            round_ids.add(row["round_id"])
            critique_rounds_by_id[row["round_id"]] = row
        if row["candidate_ref"] and row["candidate_ref"] not in candidate_ids:
            errors.append({"field": f"{prefix}.candidate_ref", "code": "unknown_ref", "message": "critique rounds must target a materialized candidate"})
        if row["reviewer_profile_id"] and row["reviewer_profile_id"] not in profile_ids:
            errors.append({"field": f"{prefix}.reviewer_profile_id", "code": "unknown_ref", "message": "critique rounds must reference a declared model profile"})
        if row["status"] not in ROUND_STATUSES:
            errors.append({"field": f"{prefix}.status", "code": "invalid_value", "message": "critique round status is not canonical"})
        if row["critique_packet_ref"] and not _is_safe_repo_rel(row["critique_packet_ref"]):
            errors.append({"field": f"{prefix}.critique_packet_ref", "code": "invalid_ref", "message": "critique packet refs must be repo-relative"})
        critique_packet_result = validate_pis_improvement_critique_packet(
            row["critique_packet"],
            expected_round_id=row["round_id"],
            expected_candidate_ref=row["candidate_ref"],
            expected_reviewer_profile_id=row["reviewer_profile_id"],
        )
        for issue in critique_packet_result["errors"]:
            errors.append(
                {
                    "field": issue["field"].replace("critique_packet", f"{prefix}.critique_packet", 1),
                    "code": issue["code"],
                    "message": issue["message"],
                }
            )
        if row["round_id"]:
            critique_packets_by_round_id[row["round_id"]] = critique_packet_result["normalized_packet"]

    if not revision_ledger["critique_rounds"]:
        errors.append({"field": "revision_ledger.critique_rounds", "code": "missing_field", "message": "at least one critique round is required"})

    delta_ids: set[str] = set()
    revision_delta_round_ids: set[str] = set()
    for index, row in enumerate(revision_ledger["revision_deltas"]):
        prefix = f"revision_ledger.revision_deltas[{index}]"
        _append_missing_errors(errors, prefix, row, REVISION_DELTA_REQUIRED_FIELDS)
        if row["delta_id"] in delta_ids:
            errors.append({"field": f"{prefix}.delta_id", "code": "duplicate_id", "message": "delta_id must be unique"})
        if row["delta_id"]:
            delta_ids.add(row["delta_id"])
        if row["round_id"] not in round_ids:
            errors.append({"field": f"{prefix}.round_id", "code": "unknown_ref", "message": "revision deltas must reference an existing critique round"})
        else:
            revision_delta_round_ids.add(row["round_id"])
        if row["reviser_profile_id"] and row["reviser_profile_id"] not in profile_ids:
            errors.append({"field": f"{prefix}.reviser_profile_id", "code": "unknown_ref", "message": "revision deltas must reference a declared model profile"})
        if row["proposed_revision_ref"] and not _is_safe_repo_rel(row["proposed_revision_ref"]):
            errors.append({"field": f"{prefix}.proposed_revision_ref", "code": "invalid_ref", "message": "revision refs must be repo-relative"})
        reviser_packet_result = validate_pis_improvement_reviser_packet(
            row["reviser_packet"],
            expected_round_id=row["round_id"],
            expected_candidate_ref=critique_rounds_by_id.get(row["round_id"], {}).get("candidate_ref"),
            expected_reviser_profile_id=row["reviser_profile_id"],
            expected_critique_packet=critique_packets_by_round_id.get(row["round_id"]),
        )
        for issue in reviser_packet_result["errors"]:
            errors.append(
                {
                    "field": issue["field"].replace("reviser_packet", f"{prefix}.reviser_packet", 1),
                    "code": issue["code"],
                    "message": issue["message"],
                }
            )

    for round_id in sorted(round_ids - revision_delta_round_ids):
        errors.append({"field": f"revision_ledger.round:{round_id}", "code": "missing_revision_delta", "message": "every critique round requires a linked revision delta"})

    recommendation_ids: set[str] = set()
    for index, row in enumerate(revision_ledger["recommendation_history"]):
        prefix = f"revision_ledger.recommendation_history[{index}]"
        _append_missing_errors(errors, prefix, row, RECOMMENDATION_REQUIRED_FIELDS)
        if row["recommendation_id"] in recommendation_ids:
            errors.append({"field": f"{prefix}.recommendation_id", "code": "duplicate_id", "message": "recommendation_id must be unique"})
        if row["recommendation_id"]:
            recommendation_ids.add(row["recommendation_id"])
        if row["outcome"] not in RECOMMENDATION_OUTCOMES:
            errors.append({"field": f"{prefix}.outcome", "code": "invalid_value", "message": "recommendation outcome is not canonical"})
        if row["winning_candidate_ref"] and row["winning_candidate_ref"] not in candidate_ids:
            errors.append({"field": f"{prefix}.winning_candidate_ref", "code": "unknown_ref", "message": "recommendation must point at a materialized candidate"})
        if row["rationale_ref"] and not _is_safe_repo_rel(row["rationale_ref"]):
            errors.append({"field": f"{prefix}.rationale_ref", "code": "invalid_ref", "message": "recommendation rationale refs must be repo-relative"})

    if not revision_ledger["recommendation_history"]:
        errors.append({"field": "revision_ledger.recommendation_history", "code": "missing_field", "message": "recommendation history is required"})

    for field in FORBIDDEN_TOP_LEVEL_FIELDS:
        if field in raw:
            errors.append({"field": field, "code": "forbidden_inline_ownership", "message": "mutable chat-derived or source-payload fields are forbidden inline"})

    for key in raw.keys():
        if key not in TOP_LEVEL_REQUIRED_FIELDS:
            warnings.append({"field": key, "code": "unknown_field", "message": "field is outside the v1 session contract"})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_session": normalized,
        "computed_fingerprint": _fingerprint(normalized),
    }


def validate_pis_improvement_session_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_pis_improvement_session_contract(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != CONTRACT_SCHEMA_VERSION:
        issues.append("schema_version must remain pis_improvement_session_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260412-062100Z-A1C3")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion improvement-session contract doc")
    if str(payload.get("reference_examples_rel") or "") != REFERENCE_EXAMPLES_REL.as_posix():
        issues.append("reference_examples_rel must point at the canonical reference examples")
    if tuple(payload.get("top_level_required_fields") or ()) != TOP_LEVEL_REQUIRED_FIELDS:
        issues.append("top_level_required_fields must match the runtime helper contract")
    if tuple(payload.get("critique_packet_required_fields") or ()) != CRITIQUE_PACKET_REQUIRED_FIELDS:
        issues.append("critique_packet_required_fields must match the runtime helper contract")
    if tuple(payload.get("reviser_packet_required_fields") or ()) != REVISER_PACKET_REQUIRED_FIELDS:
        issues.append("reviser_packet_required_fields must match the runtime helper contract")
    if tuple(payload.get("candidate_selection_required_fields") or ()) != CANDIDATE_SELECTION_REQUIRED_FIELDS:
        issues.append("candidate_selection_required_fields must match the runtime helper contract")
    if tuple(payload.get("revision_ledger_required_fields") or ()) != REVISION_LEDGER_REQUIRED_FIELDS:
        issues.append("revision_ledger_required_fields must match the runtime helper contract")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules must match the runtime helper contract")

    examples_issues = validate_pis_improvement_session_reference_examples_v1(repo_root=repo_root)
    if examples_issues:
        issues.append("reference examples must satisfy their declared accepted/rejected outcomes")

    return tuple(issues)


def validate_pis_improvement_session_reference_examples_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_pis_improvement_session_reference_examples(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != REFERENCE_EXAMPLES_SCHEMA_VERSION:
        issues.append("schema_version must remain pis_improvement_session_reference_examples_v1")

    examples = list(payload.get("examples") or [])
    if not examples:
        issues.append("reference examples must include at least one example")

    for index, row in enumerate(examples):
        prefix = f"examples[{index}]"
        if not isinstance(row, Mapping):
            issues.append(f"{prefix} must be a mapping")
            continue
        for field in REFERENCE_EXAMPLE_REQUIRED_FIELDS:
            value = row.get(field)
            if value in ("", None, [], {}):
                issues.append(f"{prefix}.{field} is required")
        example_class = _normalize_text(row.get("example_class"))
        expected_outcome = _normalize_text(row.get("expected_validation_outcome"))
        if example_class not in EXAMPLE_CLASSES:
            issues.append(f"{prefix}.example_class must be one of the canonical example classes")
        if expected_outcome not in EXPECTED_EXAMPLE_OUTCOMES:
            issues.append(f"{prefix}.expected_validation_outcome must be accepted or rejected")
        result = validate_pis_improvement_session(row.get("session_record") if isinstance(row.get("session_record"), Mapping) else {})
        if expected_outcome == "accepted" and not result["ok"]:
            issues.append(f"{prefix} should validate but does not")
        if expected_outcome == "rejected" and result["ok"]:
            issues.append(f"{prefix} should fail validation but passed")

    packet_examples = list(payload.get("packet_examples") or [])
    if not packet_examples:
        issues.append("reference examples must include packet_examples")
    for index, row in enumerate(packet_examples):
        prefix = f"packet_examples[{index}]"
        if not isinstance(row, Mapping):
            issues.append(f"{prefix} must be a mapping")
            continue
        for field in PACKET_REFERENCE_EXAMPLE_REQUIRED_FIELDS:
            value = row.get(field)
            if value in ("", None, [], {}):
                issues.append(f"{prefix}.{field} is required")
        example_class = _normalize_text(row.get("example_class"))
        packet_kind = _normalize_text(row.get("packet_kind")).lower()
        expected_outcome = _normalize_text(row.get("expected_validation_outcome"))
        if example_class not in PACKET_EXAMPLE_CLASSES:
            issues.append(f"{prefix}.example_class must be one of the canonical packet example classes")
        if packet_kind not in PACKET_KINDS:
            issues.append(f"{prefix}.packet_kind must be critique or reviser")
            continue
        if expected_outcome not in EXPECTED_EXAMPLE_OUTCOMES:
            issues.append(f"{prefix}.expected_validation_outcome must be accepted or rejected")
        if packet_kind == "critique":
            result = validate_pis_improvement_critique_packet(row.get("packet_record") if isinstance(row.get("packet_record"), Mapping) else {})
        else:
            result = validate_pis_improvement_reviser_packet(row.get("packet_record") if isinstance(row.get("packet_record"), Mapping) else {})
        if expected_outcome == "accepted" and not result["ok"]:
            issues.append(f"{prefix} should validate but does not")
        if expected_outcome == "rejected" and result["ok"]:
            issues.append(f"{prefix} should fail validation but passed")

    return tuple(issues)
