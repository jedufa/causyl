"""Canonical literature compendium v1 contract helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import research_evidence_pack_contract as evidence_pack


SCHEMA_VERSION = "literature_compendium_contract_v1"
COMPENDIUM_SCHEMA_VERSION = "literature_compendium_v1"
CONTRACT_ARTIFACT_REL = Path("governance/literature_compendium_contract_v1.json")
REFERENCE_COMPENDIUM_REL = Path("governance/literature_compendium_reference_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_literature_compendium_v1.md")
UID = "PIS-20260410-102638Z-23DF"

TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "compendium_id",
    "compendium_version",
    "metadata",
    "layout",
    "evidence_pack_refs",
    "entries",
    "unresolved_conflicts",
    "operator_commentary",
    "raw_note_refs",
)
METADATA_REQUIRED_FIELDS: Tuple[str, ...] = (
    "title",
    "created_at_utc",
    "source_intent_uid",
    "ledger_refs",
)
LAYOUT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "compendium_type",
    "summary_layers",
    "sections",
)
PACK_REF_REQUIRED_FIELDS: Tuple[str, ...] = (
    "pack_id",
    "pack_schema_version",
    "pack_artifact_ref",
    "pack_fingerprint",
)
ENTRY_REQUIRED_FIELDS: Tuple[str, ...] = (
    "entry_id",
    "entry_type",
    "summary_layer",
    "summary_text",
    "evidence_refs",
)
EVIDENCE_REF_REQUIRED_FIELDS: Tuple[str, ...] = (
    "pack_id",
    "claim_ids",
    "source_ids",
    "contradiction_ids",
)
CONFLICT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "conflict_id",
    "summary_text",
    "state",
    "evidence_refs",
    "next_resolution_step",
)
COMMENTARY_REQUIRED_FIELDS: Tuple[str, ...] = (
    "commentary_id",
    "commentary_type",
    "commentary_text",
    "related_entry_ids",
)
RAW_NOTE_REF_REQUIRED_FIELDS: Tuple[str, ...] = (
    "raw_note_ref_id",
    "locator",
    "boundary_note",
    "mapped_pack_ids",
)

COMPENDIUM_TYPES = frozenset({"topic", "merged"})
SUMMARY_LAYERS = frozenset({"operator_brief", "sourced_synthesis", "technical_detail", "open_conflicts"})
SECTIONS = frozenset({"topic_compendium", "merged_compendium", "unresolved_conflicts", "operator_commentary", "raw_note_boundary"})
ENTRY_TYPES = frozenset({"topic_summary", "merged_summary", "reusable_compendium_entry"})
CONFLICT_STATES = frozenset({"unresolved", "resolved", "deferred"})
COMMENTARY_TYPES = frozenset({"operator_note", "review_note", "decision_note"})

FORBIDDEN_COMPENDIUM_FIELDS: Tuple[str, ...] = (
    "raw_notes",
    "raw_note_body",
    "raw_note_bodies",
    "source_material_body",
    "source_materials",
    "operator_commentary_text",
    "mixed_commentary_synthesis",
)
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "compendium_entries_without_pack_claim_source_refs_rejected",
    "operator_commentary_must_stay_in_operator_commentary_section",
    "raw_notes_may_be_linked_but_not_embedded",
    "unresolved_conflicts_must_stay_visible",
    "pack_refs_must_validate_against_referenced_pack_claim_and_source_ids",
    "pack_refs_must_validate_against_referenced_pack_fingerprints",
)


@dataclass(frozen=True)
class LiteratureCompendiumContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    reference_compendium_rel: Path
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


def literature_compendium_contract_v1() -> LiteratureCompendiumContractV1:
    return LiteratureCompendiumContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        reference_compendium_rel=REFERENCE_COMPENDIUM_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        top_level_required_fields=TOP_LEVEL_REQUIRED_FIELDS,
        fail_closed_rules=FAIL_CLOSED_RULES,
    )


def load_literature_compendium_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_reference_literature_compendium(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / REFERENCE_COMPENDIUM_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_literature_compendium(raw_compendium: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_compendium or {})
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
    layout = raw.get("layout") if isinstance(raw.get("layout"), Mapping) else {}

    return {
        "schema_version": _normalize_text(raw.get("schema_version") or COMPENDIUM_SCHEMA_VERSION),
        "compendium_id": _normalize_text(raw.get("compendium_id")),
        "compendium_version": _normalize_text(raw.get("compendium_version")),
        "metadata": {
            "title": _normalize_text(metadata.get("title")),
            "created_at_utc": _normalize_text(metadata.get("created_at_utc")),
            "source_intent_uid": _normalize_text(metadata.get("source_intent_uid")),
            "ledger_refs": _normalize_text_list(metadata.get("ledger_refs")),
        },
        "layout": {
            "compendium_type": _normalize_text(layout.get("compendium_type")),
            "summary_layers": _normalize_text_list(layout.get("summary_layers")),
            "sections": _normalize_text_list(layout.get("sections")),
        },
        "evidence_pack_refs": _normalize_rows(raw.get("evidence_pack_refs"), PACK_REF_REQUIRED_FIELDS),
        "entries": _normalize_rows(raw.get("entries"), ENTRY_REQUIRED_FIELDS),
        "unresolved_conflicts": _normalize_rows(raw.get("unresolved_conflicts"), CONFLICT_REQUIRED_FIELDS),
        "operator_commentary": _normalize_rows(raw.get("operator_commentary"), COMMENTARY_REQUIRED_FIELDS),
        "raw_note_refs": _normalize_rows(raw.get("raw_note_refs"), RAW_NOTE_REF_REQUIRED_FIELDS),
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


def _evidence_refs(raw_refs: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_refs, list):
        return []
    return [_normalize_row(row, EVIDENCE_REF_REQUIRED_FIELDS) for row in raw_refs if isinstance(row, Mapping)]


def validate_literature_compendium(
    raw_compendium: Mapping[str, Any],
    *,
    evidence_packs: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    raw = dict(raw_compendium or {})
    normalized = normalize_literature_compendium(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", [], {}) or value is None:
            errors.append({"field": field, "code": "missing_field", "message": f"literature compendium requires {field}"})

    if normalized["schema_version"] != COMPENDIUM_SCHEMA_VERSION:
        errors.append({"field": "schema_version", "code": "invalid_value", "message": f"schema_version must be {COMPENDIUM_SCHEMA_VERSION}"})

    _append_missing_errors(errors, "metadata", normalized["metadata"], METADATA_REQUIRED_FIELDS)
    _append_missing_errors(errors, "layout", normalized["layout"], LAYOUT_REQUIRED_FIELDS)

    if _normalize_text(normalized["layout"].get("compendium_type")) not in COMPENDIUM_TYPES:
        errors.append({"field": "layout.compendium_type", "code": "invalid_value", "message": "compendium_type is not canonical"})
    for layer in _normalize_text_list(normalized["layout"].get("summary_layers")):
        if layer not in SUMMARY_LAYERS:
            errors.append({"field": "layout.summary_layers", "code": "invalid_value", "message": "summary layer is not canonical"})
    for section in _normalize_text_list(normalized["layout"].get("sections")):
        if section not in SECTIONS:
            errors.append({"field": "layout.sections", "code": "invalid_value", "message": "section is not canonical"})

    for forbidden in FORBIDDEN_COMPENDIUM_FIELDS:
        if forbidden in raw and raw.get(forbidden) not in (None, "", [], {}, ()):
            errors.append(
                {
                    "field": forbidden,
                    "code": "forbidden_inline_ownership",
                    "message": "compendia link raw notes and source records but do not embed raw bodies or mixed prose blobs",
                }
            )

    pack_refs_by_id: Dict[str, Mapping[str, Any]] = {}
    for index, pack_ref in enumerate(normalized["evidence_pack_refs"]):
        prefix = f"evidence_pack_refs[{index}]"
        _append_missing_errors(errors, prefix, pack_ref, PACK_REF_REQUIRED_FIELDS)
        pack_id = _normalize_text(pack_ref.get("pack_id"))
        if pack_id in pack_refs_by_id:
            errors.append({"field": f"{prefix}.pack_id", "code": "duplicate_id", "message": "pack_id must be unique in compendium refs"})
        if pack_id:
            pack_refs_by_id[pack_id] = pack_ref
        if _normalize_text(pack_ref.get("pack_schema_version")) != evidence_pack.PACK_SCHEMA_VERSION:
            errors.append({"field": f"{prefix}.pack_schema_version", "code": "invalid_value", "message": "pack_schema_version must reference evidence pack v1"})

    known_claim_ids_by_pack: Dict[str, set[str]] = {}
    known_source_ids_by_pack: Dict[str, set[str]] = {}
    known_contradiction_ids_by_pack: Dict[str, set[str]] = {}
    known_fingerprints_by_pack: Dict[str, str] = {}
    for pack in evidence_packs or ():
        pack_result = evidence_pack.validate_research_evidence_pack(pack)
        pack_id = _normalize_text(pack_result["normalized_pack"].get("pack_id"))
        if not pack_result["ok"]:
            errors.append({"field": f"evidence_pack:{pack_id or '<unknown>'}", "code": "invalid_evidence_pack", "message": "referenced evidence pack failed validation"})
            continue
        known_claim_ids_by_pack[pack_id] = {_normalize_text(row.get("claim_id")) for row in pack_result["normalized_pack"]["claims"]}
        known_source_ids_by_pack[pack_id] = {_normalize_text(row.get("source_id")) for row in pack_result["normalized_pack"]["sources"]}
        known_contradiction_ids_by_pack[pack_id] = {_normalize_text(row.get("contradiction_id")) for row in pack_result["normalized_pack"]["contradictions"]}
        known_fingerprints_by_pack[pack_id] = _normalize_text(pack_result.get("computed_fingerprint"))

    if evidence_packs:
        for index, pack_ref in enumerate(normalized["evidence_pack_refs"]):
            pack_id = _normalize_text(pack_ref.get("pack_id"))
            expected_fingerprint = known_fingerprints_by_pack.get(pack_id)
            if expected_fingerprint and _normalize_text(pack_ref.get("pack_fingerprint")) != expected_fingerprint:
                errors.append(
                    {
                        "field": f"evidence_pack_refs[{index}].pack_fingerprint",
                        "code": "fingerprint_mismatch",
                        "message": "pack_fingerprint must match the referenced evidence pack fingerprint",
                    }
                )

    def validate_refs(prefix: str, raw_refs: Any) -> None:
        refs = _evidence_refs(raw_refs)
        if not refs:
            errors.append({"field": prefix, "code": "missing_evidence_refs", "message": "compendium summaries require evidence refs"})
            return
        for ref_index, ref in enumerate(refs):
            ref_prefix = f"{prefix}[{ref_index}]"
            _append_missing_errors(errors, ref_prefix, ref, ("pack_id", "claim_ids", "source_ids"))
            pack_id = _normalize_text(ref.get("pack_id"))
            claim_ids = _normalize_text_list(ref.get("claim_ids"))
            source_ids = _normalize_text_list(ref.get("source_ids"))
            contradiction_ids = _normalize_text_list(ref.get("contradiction_ids"))
            if pack_id not in pack_refs_by_id:
                errors.append({"field": f"{ref_prefix}.pack_id", "code": "unknown_ref", "message": "evidence ref targets an unknown evidence pack"})
            if not claim_ids:
                errors.append({"field": f"{ref_prefix}.claim_ids", "code": "missing_claim_refs", "message": "compendium entries must cite claim ids"})
            if not source_ids:
                errors.append({"field": f"{ref_prefix}.source_ids", "code": "missing_source_refs", "message": "compendium entries must cite source ids"})
            if evidence_packs:
                for claim_id in claim_ids:
                    if claim_id not in known_claim_ids_by_pack.get(pack_id, set()):
                        errors.append({"field": f"{ref_prefix}.claim_ids", "code": "unknown_ref", "message": "claim id is not present in the referenced evidence pack"})
                for source_id in source_ids:
                    if source_id not in known_source_ids_by_pack.get(pack_id, set()):
                        errors.append({"field": f"{ref_prefix}.source_ids", "code": "unknown_ref", "message": "source id is not present in the referenced evidence pack"})
                for contradiction_id in contradiction_ids:
                    if contradiction_id and contradiction_id not in known_contradiction_ids_by_pack.get(pack_id, set()):
                        errors.append(
                            {
                                "field": f"{ref_prefix}.contradiction_ids",
                                "code": "unknown_ref",
                                "message": "contradiction id is not present in the referenced evidence pack",
                            }
                        )

    entry_ids: set[str] = set()
    for index, entry in enumerate(normalized["entries"]):
        prefix = f"entries[{index}]"
        _append_missing_errors(errors, prefix, entry, ENTRY_REQUIRED_FIELDS)
        entry_id = _normalize_text(entry.get("entry_id"))
        if entry_id in entry_ids:
            errors.append({"field": f"{prefix}.entry_id", "code": "duplicate_id", "message": "entry_id must be unique"})
        if entry_id:
            entry_ids.add(entry_id)
        if _normalize_text(entry.get("entry_type")) not in ENTRY_TYPES:
            errors.append({"field": f"{prefix}.entry_type", "code": "invalid_value", "message": "entry_type is not canonical"})
        if _normalize_text(entry.get("summary_layer")) not in SUMMARY_LAYERS:
            errors.append({"field": f"{prefix}.summary_layer", "code": "invalid_value", "message": "summary_layer is not canonical"})
        validate_refs(f"{prefix}.evidence_refs", entry.get("evidence_refs"))
        if "operator_commentary" in dict(raw.get("entries", [{}])[index] if isinstance(raw.get("entries"), list) and index < len(raw.get("entries")) else {}):
            errors.append({"field": f"{prefix}.operator_commentary", "code": "mixed_commentary_synthesis", "message": "operator commentary must stay in operator_commentary"})

    for index, conflict in enumerate(normalized["unresolved_conflicts"]):
        prefix = f"unresolved_conflicts[{index}]"
        _append_missing_errors(errors, prefix, conflict, CONFLICT_REQUIRED_FIELDS)
        if _normalize_text(conflict.get("state")) != "unresolved":
            errors.append({"field": f"{prefix}.state", "code": "invalid_value", "message": "unresolved_conflicts only carries unresolved conflict state"})
        validate_refs(f"{prefix}.evidence_refs", conflict.get("evidence_refs"))

    for index, commentary in enumerate(normalized["operator_commentary"]):
        prefix = f"operator_commentary[{index}]"
        _append_missing_errors(errors, prefix, commentary, COMMENTARY_REQUIRED_FIELDS)
        if _normalize_text(commentary.get("commentary_type")) not in COMMENTARY_TYPES:
            errors.append({"field": f"{prefix}.commentary_type", "code": "invalid_value", "message": "commentary_type is not canonical"})
        for entry_id in _normalize_text_list(commentary.get("related_entry_ids")):
            if entry_id not in entry_ids:
                errors.append({"field": f"{prefix}.related_entry_ids", "code": "unknown_ref", "message": "commentary targets an unknown compendium entry"})

    for index, raw_note in enumerate(normalized["raw_note_refs"]):
        prefix = f"raw_note_refs[{index}]"
        _append_missing_errors(errors, prefix, raw_note, RAW_NOTE_REF_REQUIRED_FIELDS)
        for pack_id in _normalize_text_list(raw_note.get("mapped_pack_ids")):
            if pack_id not in pack_refs_by_id:
                errors.append({"field": f"{prefix}.mapped_pack_ids", "code": "unknown_ref", "message": "raw note ref maps to an unknown evidence pack"})

    for key in raw.keys():
        if key not in TOP_LEVEL_REQUIRED_FIELDS:
            warnings.append({"field": key, "code": "unknown_field", "message": "field is outside the v1 literature compendium contract"})

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_compendium": normalized,
        "computed_fingerprint": _fingerprint(normalized),
    }


def validate_literature_compendium_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_literature_compendium_contract(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain literature_compendium_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260410-102638Z-23DF")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion literature compendium contract doc")
    if str(payload.get("reference_compendium_rel") or "") != REFERENCE_COMPENDIUM_REL.as_posix():
        issues.append("reference_compendium_rel must point at the canonical reference compendium")
    if tuple(payload.get("top_level_required_fields") or ()) != TOP_LEVEL_REQUIRED_FIELDS:
        issues.append("top_level_required_fields must list the canonical compendium fields in order")
    if tuple(payload.get("entry_required_fields") or ()) != ENTRY_REQUIRED_FIELDS:
        issues.append("entry_required_fields must match the runtime helper contract")
    if tuple(payload.get("evidence_ref_required_fields") or ()) != EVIDENCE_REF_REQUIRED_FIELDS:
        issues.append("evidence_ref_required_fields must match the runtime helper contract")
    if tuple(payload.get("unresolved_conflict_required_fields") or ()) != CONFLICT_REQUIRED_FIELDS:
        issues.append("unresolved_conflict_required_fields must match the runtime helper contract")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules must match the runtime helper contract")

    reference = load_reference_literature_compendium(repo_root=repo_root)
    reference_pack = evidence_pack.load_reference_evidence_pack(repo_root=repo_root)
    reference_result = validate_literature_compendium(reference, evidence_packs=[reference_pack])
    if not reference_result["ok"]:
        issues.append("reference literature compendium must validate under the runtime helper contract")

    return tuple(issues)
