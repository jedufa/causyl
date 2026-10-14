"""Canonical research evidence pack v1 contract helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


SCHEMA_VERSION = "research_evidence_pack_contract_v1"
PACK_SCHEMA_VERSION = "research_evidence_pack_v1"
CONTRACT_ARTIFACT_REL = Path("governance/research_evidence_pack_contract_v1.json")
REFERENCE_PACK_REL = Path("governance/research_evidence_pack_reference_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_research_evidence_pack_v1.md")
UID = "PIS-20260410-102637Z-DCFE"

TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "pack_id",
    "pack_version",
    "stable_identity",
    "metadata",
    "pack_scope",
    "sources",
    "source_quality_notes",
    "claims",
    "claim_source_links",
    "contradictions",
    "unresolved_conflict_notes",
    "summary_layers",
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
PACK_SCOPE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "belongs_in_evidence_pack",
    "outside_evidence_pack",
)
SOURCE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "source_id",
    "source_type",
    "title",
    "locator",
    "captured_at_utc",
)
SOURCE_QUALITY_NOTE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "source_id",
    "quality_level",
    "note",
)
CLAIM_REQUIRED_FIELDS: Tuple[str, ...] = (
    "claim_id",
    "statement",
    "claim_type",
    "status",
)
CLAIM_LINK_REQUIRED_FIELDS: Tuple[str, ...] = (
    "link_id",
    "claim_id",
    "source_id",
    "support_type",
    "evidence_locator",
)
CONTRADICTION_REQUIRED_FIELDS: Tuple[str, ...] = (
    "contradiction_id",
    "source_claim_id",
    "target_claim_id",
    "conflict_type",
    "severity",
    "status",
    "evidence_link_ids",
)
UNRESOLVED_NOTE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "note_id",
    "contradiction_id",
    "note",
    "next_resolution_step",
)
SUMMARY_LAYER_REQUIRED_FIELDS: Tuple[str, ...] = (
    "layer_id",
    "summary_type",
    "summary_text",
    "claim_ids",
    "contradiction_ids",
)

SOURCE_TYPES = frozenset({"paper", "preprint", "dataset", "lab_note", "web", "book", "other"})
QUALITY_LEVELS = frozenset({"strong", "moderate", "weak", "unknown"})
CLAIM_TYPES = frozenset({"finding", "method", "assumption", "limitation", "interpretation", "note"})
CLAIM_STATUSES = frozenset({"clean", "weak_evidence", "contested", "superseded"})
SUPPORT_TYPES = frozenset({"supports", "weakly_supports", "contradicts", "context_only"})
CONFLICT_TYPES = frozenset({"direct_conflict", "scope_mismatch", "measurement_mismatch", "interpretation_conflict"})
CONTRADICTION_SEVERITIES = frozenset({"low", "medium", "high"})
CONTRADICTION_STATUSES = frozenset({"unresolved", "resolved", "deferred"})
SUMMARY_TYPES = frozenset({"operator_summary", "technical_summary", "open_questions"})

FORBIDDEN_PACK_FIELDS: Tuple[str, ...] = (
    "continuation_packet",
    "continuation_packet_body",
    "hypothesis_registry",
    "experiment_runtime_artifacts",
    "runtime_artifact_payloads",
    "chat_summary",
)
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "claims_without_explicit_source_links_rejected",
    "claim_links_without_source_locator_rejected",
    "contradictions_must_name_both_claims",
    "unresolved_contradictions_require_unresolved_notes",
    "summary_layers_cannot_replace_claim_source_links",
    "continuation_packet_or_runtime_artifact_payloads_forbidden_inline",
)


@dataclass(frozen=True)
class ResearchEvidencePackContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    reference_pack_rel: Path
    canonical_doc_rel: Path
    top_level_required_fields: Tuple[str, ...]
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


def _normalize_row(raw: Any, fields: Sequence[str]) -> Dict[str, Any]:
    row = raw if isinstance(raw, Mapping) else {}
    return {field: row.get(field) for field in fields}


def _normalize_rows(raw: Any, fields: Sequence[str]) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [_normalize_row(row, fields) for row in raw if isinstance(row, Mapping)]


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def research_evidence_pack_contract_v1() -> ResearchEvidencePackContractV1:
    return ResearchEvidencePackContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        reference_pack_rel=REFERENCE_PACK_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        top_level_required_fields=TOP_LEVEL_REQUIRED_FIELDS,
        fail_closed_rules=FAIL_CLOSED_RULES,
    )


def load_research_evidence_pack_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_reference_evidence_pack(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / REFERENCE_PACK_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_research_evidence_pack(raw_pack: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_pack or {})
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
    stable_identity = raw.get("stable_identity") if isinstance(raw.get("stable_identity"), Mapping) else {}
    pack_scope = raw.get("pack_scope") if isinstance(raw.get("pack_scope"), Mapping) else {}

    return {
        "schema_version": _normalize_text(raw.get("schema_version") or PACK_SCHEMA_VERSION),
        "pack_id": _normalize_text(raw.get("pack_id")),
        "pack_version": _normalize_text(raw.get("pack_version")),
        "stable_identity": {
            "canonicalization": _normalize_text(stable_identity.get("canonicalization")),
            "content_hash_algorithm": _normalize_text(stable_identity.get("content_hash_algorithm")),
            "schema_migration_policy": _normalize_text(stable_identity.get("schema_migration_policy")),
        },
        "metadata": {
            "title": _normalize_text(metadata.get("title")),
            "created_at_utc": _normalize_text(metadata.get("created_at_utc")),
            "source_intent_uid": _normalize_text(metadata.get("source_intent_uid")),
            "ledger_refs": _normalize_text_list(metadata.get("ledger_refs")),
        },
        "pack_scope": {
            "belongs_in_evidence_pack": _normalize_text_list(pack_scope.get("belongs_in_evidence_pack")),
            "outside_evidence_pack": _normalize_text_list(pack_scope.get("outside_evidence_pack")),
        },
        "sources": _normalize_rows(raw.get("sources"), SOURCE_REQUIRED_FIELDS),
        "source_quality_notes": _normalize_rows(raw.get("source_quality_notes"), SOURCE_QUALITY_NOTE_REQUIRED_FIELDS),
        "claims": _normalize_rows(raw.get("claims"), CLAIM_REQUIRED_FIELDS),
        "claim_source_links": _normalize_rows(raw.get("claim_source_links"), CLAIM_LINK_REQUIRED_FIELDS),
        "contradictions": _normalize_rows(raw.get("contradictions"), CONTRADICTION_REQUIRED_FIELDS),
        "unresolved_conflict_notes": _normalize_rows(raw.get("unresolved_conflict_notes"), UNRESOLVED_NOTE_REQUIRED_FIELDS),
        "summary_layers": _normalize_rows(raw.get("summary_layers"), SUMMARY_LAYER_REQUIRED_FIELDS),
    }


def _missing_required(row: Mapping[str, Any], required_fields: Sequence[str]) -> List[str]:
    missing: List[str] = []
    for field in required_fields:
        value = row.get(field)
        if value in ("", None, [], {}):
            missing.append(field)
    return missing


def _append_missing_errors(errors: List[Dict[str, str]], prefix: str, row: Mapping[str, Any], fields: Sequence[str]) -> None:
    for field in _missing_required(row, fields):
        errors.append({"field": f"{prefix}.{field}", "code": "missing_field", "message": f"{prefix}.{field} is required"})


def validate_research_evidence_pack(raw_pack: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_pack or {})
    normalized = normalize_research_evidence_pack(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", [], {}) or value is None:
            errors.append({"field": field, "code": "missing_field", "message": f"evidence pack requires {field}"})

    if normalized["schema_version"] != PACK_SCHEMA_VERSION:
        errors.append({"field": "schema_version", "code": "invalid_value", "message": f"schema_version must be {PACK_SCHEMA_VERSION}"})

    _append_missing_errors(errors, "stable_identity", normalized["stable_identity"], STABLE_IDENTITY_REQUIRED_FIELDS)
    _append_missing_errors(errors, "metadata", normalized["metadata"], METADATA_REQUIRED_FIELDS)
    _append_missing_errors(errors, "pack_scope", normalized["pack_scope"], PACK_SCOPE_REQUIRED_FIELDS)

    for forbidden in FORBIDDEN_PACK_FIELDS:
        if forbidden in raw and raw.get(forbidden) not in (None, "", [], {}, ()):
            errors.append(
                {
                    "field": forbidden,
                    "code": "forbidden_inline_ownership",
                    "message": "evidence packs link neighboring durable artifacts but do not embed their payloads",
                }
            )

    sources_by_id: Dict[str, Mapping[str, Any]] = {}
    for index, source in enumerate(normalized["sources"]):
        prefix = f"sources[{index}]"
        _append_missing_errors(errors, prefix, source, SOURCE_REQUIRED_FIELDS)
        source_id = _normalize_text(source.get("source_id"))
        if source_id in sources_by_id:
            errors.append({"field": f"{prefix}.source_id", "code": "duplicate_id", "message": "source_id must be unique"})
        if source_id:
            sources_by_id[source_id] = source
        if _normalize_text(source.get("source_type")) not in SOURCE_TYPES:
            errors.append({"field": f"{prefix}.source_type", "code": "invalid_value", "message": "source_type is not canonical"})

    claims_by_id: Dict[str, Mapping[str, Any]] = {}
    for index, claim in enumerate(normalized["claims"]):
        prefix = f"claims[{index}]"
        _append_missing_errors(errors, prefix, claim, CLAIM_REQUIRED_FIELDS)
        claim_id = _normalize_text(claim.get("claim_id"))
        if claim_id in claims_by_id:
            errors.append({"field": f"{prefix}.claim_id", "code": "duplicate_id", "message": "claim_id must be unique"})
        if claim_id:
            claims_by_id[claim_id] = claim
        if _normalize_text(claim.get("claim_type")) not in CLAIM_TYPES:
            errors.append({"field": f"{prefix}.claim_type", "code": "invalid_value", "message": "claim_type is not canonical"})
        if _normalize_text(claim.get("status")) not in CLAIM_STATUSES:
            errors.append({"field": f"{prefix}.status", "code": "invalid_value", "message": "claim status is not canonical"})

    links_by_id: Dict[str, Mapping[str, Any]] = {}
    linked_claim_ids: set[str] = set()
    for index, link in enumerate(normalized["claim_source_links"]):
        prefix = f"claim_source_links[{index}]"
        _append_missing_errors(errors, prefix, link, CLAIM_LINK_REQUIRED_FIELDS)
        link_id = _normalize_text(link.get("link_id"))
        claim_id = _normalize_text(link.get("claim_id"))
        source_id = _normalize_text(link.get("source_id"))
        if link_id in links_by_id:
            errors.append({"field": f"{prefix}.link_id", "code": "duplicate_id", "message": "link_id must be unique"})
        if link_id:
            links_by_id[link_id] = link
        if claim_id not in claims_by_id:
            errors.append({"field": f"{prefix}.claim_id", "code": "unknown_ref", "message": "claim link targets an unknown claim"})
        else:
            linked_claim_ids.add(claim_id)
        if source_id not in sources_by_id:
            errors.append({"field": f"{prefix}.source_id", "code": "unknown_ref", "message": "claim link targets an unknown source"})
        if _normalize_text(link.get("support_type")) not in SUPPORT_TYPES:
            errors.append({"field": f"{prefix}.support_type", "code": "invalid_value", "message": "support_type is not canonical"})

    for claim_id in sorted(set(claims_by_id) - linked_claim_ids):
        errors.append(
            {
                "field": f"claim:{claim_id}",
                "code": "unsupported_claim",
                "message": "every claim must have an explicit claim_source_links entry",
            }
        )

    contradiction_ids: set[str] = set()
    unresolved_contradiction_ids: set[str] = set()
    for index, contradiction in enumerate(normalized["contradictions"]):
        prefix = f"contradictions[{index}]"
        _append_missing_errors(errors, prefix, contradiction, CONTRADICTION_REQUIRED_FIELDS)
        contradiction_id = _normalize_text(contradiction.get("contradiction_id"))
        source_claim_id = _normalize_text(contradiction.get("source_claim_id"))
        target_claim_id = _normalize_text(contradiction.get("target_claim_id"))
        evidence_link_ids = _normalize_text_list(contradiction.get("evidence_link_ids"))
        if contradiction_id in contradiction_ids:
            errors.append({"field": f"{prefix}.contradiction_id", "code": "duplicate_id", "message": "contradiction_id must be unique"})
        if contradiction_id:
            contradiction_ids.add(contradiction_id)
        if source_claim_id == target_claim_id and source_claim_id:
            errors.append({"field": prefix, "code": "invalid_value", "message": "contradiction must compare two distinct claims"})
        for field, claim_id in (("source_claim_id", source_claim_id), ("target_claim_id", target_claim_id)):
            if claim_id not in claims_by_id:
                errors.append({"field": f"{prefix}.{field}", "code": "unknown_ref", "message": "contradiction targets an unknown claim"})
        for link_id in evidence_link_ids:
            if link_id not in links_by_id:
                errors.append({"field": f"{prefix}.evidence_link_ids", "code": "unknown_ref", "message": "contradiction targets an unknown claim-source link"})
        if _normalize_text(contradiction.get("conflict_type")) not in CONFLICT_TYPES:
            errors.append({"field": f"{prefix}.conflict_type", "code": "invalid_value", "message": "conflict_type is not canonical"})
        if _normalize_text(contradiction.get("severity")) not in CONTRADICTION_SEVERITIES:
            errors.append({"field": f"{prefix}.severity", "code": "invalid_value", "message": "severity is not canonical"})
        status = _normalize_text(contradiction.get("status"))
        if status not in CONTRADICTION_STATUSES:
            errors.append({"field": f"{prefix}.status", "code": "invalid_value", "message": "contradiction status is not canonical"})
        if status == "unresolved" and contradiction_id:
            unresolved_contradiction_ids.add(contradiction_id)

    noted_unresolved_ids: set[str] = set()
    for index, note in enumerate(normalized["unresolved_conflict_notes"]):
        prefix = f"unresolved_conflict_notes[{index}]"
        _append_missing_errors(errors, prefix, note, UNRESOLVED_NOTE_REQUIRED_FIELDS)
        contradiction_id = _normalize_text(note.get("contradiction_id"))
        if contradiction_id not in contradiction_ids:
            errors.append({"field": f"{prefix}.contradiction_id", "code": "unknown_ref", "message": "unresolved note targets an unknown contradiction"})
        if contradiction_id:
            noted_unresolved_ids.add(contradiction_id)

    for contradiction_id in sorted(unresolved_contradiction_ids - noted_unresolved_ids):
        errors.append(
            {
                "field": f"contradiction:{contradiction_id}",
                "code": "unresolved_contradiction_without_note",
                "message": "unresolved contradictions require an explicit unresolved_conflict_notes entry",
            }
        )

    for index, note in enumerate(normalized["source_quality_notes"]):
        prefix = f"source_quality_notes[{index}]"
        _append_missing_errors(errors, prefix, note, SOURCE_QUALITY_NOTE_REQUIRED_FIELDS)
        source_id = _normalize_text(note.get("source_id"))
        if source_id not in sources_by_id:
            errors.append({"field": f"{prefix}.source_id", "code": "unknown_ref", "message": "quality note targets an unknown source"})
        if _normalize_text(note.get("quality_level")) not in QUALITY_LEVELS:
            errors.append({"field": f"{prefix}.quality_level", "code": "invalid_value", "message": "quality_level is not canonical"})

    for index, summary in enumerate(normalized["summary_layers"]):
        prefix = f"summary_layers[{index}]"
        _append_missing_errors(errors, prefix, summary, SUMMARY_LAYER_REQUIRED_FIELDS)
        if _normalize_text(summary.get("summary_type")) not in SUMMARY_TYPES:
            errors.append({"field": f"{prefix}.summary_type", "code": "invalid_value", "message": "summary_type is not canonical"})
        for claim_id in _normalize_text_list(summary.get("claim_ids")):
            if claim_id not in claims_by_id:
                errors.append({"field": f"{prefix}.claim_ids", "code": "unknown_ref", "message": "summary targets an unknown claim"})
        for contradiction_id in _normalize_text_list(summary.get("contradiction_ids")):
            if contradiction_id not in contradiction_ids:
                errors.append({"field": f"{prefix}.contradiction_ids", "code": "unknown_ref", "message": "summary targets an unknown contradiction"})

    for key in raw.keys():
        if key not in TOP_LEVEL_REQUIRED_FIELDS:
            warnings.append({"field": key, "code": "unknown_field", "message": "field is outside the v1 evidence pack contract"})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_pack": normalized,
        "computed_fingerprint": _fingerprint(normalized),
    }


def validate_research_evidence_pack_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_research_evidence_pack_contract(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain research_evidence_pack_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260410-102637Z-DCFE")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion research evidence pack contract doc")
    if str(payload.get("reference_pack_rel") or "") != REFERENCE_PACK_REL.as_posix():
        issues.append("reference_pack_rel must point at the canonical reference pack")
    if tuple(payload.get("top_level_required_fields") or ()) != TOP_LEVEL_REQUIRED_FIELDS:
        issues.append("top_level_required_fields must list the canonical evidence pack fields in order")
    if tuple(payload.get("claim_required_fields") or ()) != CLAIM_REQUIRED_FIELDS:
        issues.append("claim_required_fields must match the runtime helper contract")
    if tuple(payload.get("claim_source_link_required_fields") or ()) != CLAIM_LINK_REQUIRED_FIELDS:
        issues.append("claim_source_link_required_fields must match the runtime helper contract")
    if tuple(payload.get("contradiction_required_fields") or ()) != CONTRADICTION_REQUIRED_FIELDS:
        issues.append("contradiction_required_fields must match the runtime helper contract")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules must match the runtime helper contract")

    reference = load_reference_evidence_pack(repo_root=repo_root)
    reference_result = validate_research_evidence_pack(reference)
    if not reference_result["ok"]:
        issues.append("reference evidence pack must validate under the runtime helper contract")

    return tuple(issues)
