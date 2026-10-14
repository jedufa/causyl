from __future__ import annotations

import copy
import json
from pathlib import Path

from infra.controlplane import literature_compendium_contract as compendium
from infra.controlplane import research_evidence_pack_contract as evidence_pack


def _reference_compendium() -> dict:
    return json.loads(Path("governance/literature_compendium_reference_v1.json").read_text(encoding="utf-8"))


def _reference_pack() -> dict:
    return json.loads(Path("governance/research_evidence_pack_reference_v1.json").read_text(encoding="utf-8"))


def test_literature_compendium_contract_v1_exposes_expected_metadata() -> None:
    payload = compendium.literature_compendium_contract_v1()

    assert payload.version == "literature_compendium_contract_v1"
    assert payload.uid == "PIS-20260410-102638Z-23DF"
    assert payload.canonical_artifact_rel.as_posix() == "governance/literature_compendium_contract_v1.json"
    assert payload.reference_compendium_rel.as_posix() == "governance/literature_compendium_reference_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_literature_compendium_v1.md"
    assert "entries" in payload.top_level_required_fields
    assert "raw_notes_may_be_linked_but_not_embedded" in payload.fail_closed_rules


def test_literature_compendium_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/literature_compendium_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == compendium.CONTRACT_DOC_REL.as_posix()
    assert payload["reference_compendium_rel"] == compendium.REFERENCE_COMPENDIUM_REL.as_posix()
    assert tuple(payload["top_level_required_fields"]) == compendium.TOP_LEVEL_REQUIRED_FIELDS
    assert tuple(payload["entry_required_fields"]) == compendium.ENTRY_REQUIRED_FIELDS
    assert tuple(payload["evidence_ref_required_fields"]) == compendium.EVIDENCE_REF_REQUIRED_FIELDS
    assert tuple(payload["unresolved_conflict_required_fields"]) == compendium.CONFLICT_REQUIRED_FIELDS


def test_reference_literature_compendium_accepts_topic_merged_and_unresolved_conflict_examples() -> None:
    result = compendium.validate_literature_compendium(_reference_compendium(), evidence_packs=[_reference_pack()])

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["computed_fingerprint"] != ""
    assert {row["entry_type"] for row in result["normalized_compendium"]["entries"]} == {"topic_summary", "merged_summary"}
    assert result["normalized_compendium"]["unresolved_conflicts"][0]["state"] == "unresolved"


def test_literature_compendium_rejects_unattributed_summary() -> None:
    raw = _reference_compendium()
    raw["entries"][0]["evidence_refs"][0]["claim_ids"] = []

    result = compendium.validate_literature_compendium(raw, evidence_packs=[_reference_pack()])

    assert result["ok"] is False
    assert any(row["code"] == "missing_claim_refs" and row["field"] == "entries[0].evidence_refs[0].claim_ids" for row in result["errors"])


def test_literature_compendium_rejects_unknown_pack_claim_or_source_refs() -> None:
    raw = _reference_compendium()
    raw["entries"][0]["evidence_refs"][0]["claim_ids"] = ["claim-missing"]
    raw["entries"][0]["evidence_refs"][0]["source_ids"] = ["src-missing"]

    result = compendium.validate_literature_compendium(raw, evidence_packs=[_reference_pack()])

    assert result["ok"] is False
    assert any(row["code"] == "unknown_ref" and row["field"] == "entries[0].evidence_refs[0].claim_ids" for row in result["errors"])
    assert any(row["code"] == "unknown_ref" and row["field"] == "entries[0].evidence_refs[0].source_ids" for row in result["errors"])


def test_literature_compendium_rejects_stale_pack_fingerprint() -> None:
    raw = _reference_compendium()
    raw["evidence_pack_refs"][0]["pack_fingerprint"] = "stale"

    result = compendium.validate_literature_compendium(raw, evidence_packs=[_reference_pack()])

    assert result["ok"] is False
    assert any(row["code"] == "fingerprint_mismatch" and row["field"] == "evidence_pack_refs[0].pack_fingerprint" for row in result["errors"])


def test_literature_compendium_rejects_mixed_commentary_and_sourced_entry() -> None:
    raw = _reference_compendium()
    raw["entries"][0]["operator_commentary"] = "This should remain in operator_commentary."

    result = compendium.validate_literature_compendium(raw, evidence_packs=[_reference_pack()])

    assert result["ok"] is False
    assert any(row["code"] == "mixed_commentary_synthesis" for row in result["errors"])


def test_literature_compendium_rejects_raw_note_body_embedding() -> None:
    raw = _reference_compendium()
    raw["raw_note_body"] = "Copied raw note text."

    result = compendium.validate_literature_compendium(raw, evidence_packs=[_reference_pack()])

    assert result["ok"] is False
    assert any(row["code"] == "forbidden_inline_ownership" and row["field"] == "raw_note_body" for row in result["errors"])


def test_literature_compendium_rejects_hidden_unresolved_conflict() -> None:
    raw = _reference_compendium()
    raw["unresolved_conflicts"] = []

    result = compendium.validate_literature_compendium(raw, evidence_packs=[_reference_pack()])

    assert result["ok"] is False
    assert any(row["code"] == "missing_field" and row["field"] == "unresolved_conflicts" for row in result["errors"])


def test_literature_compendium_contract_v1_returns_no_issues() -> None:
    assert evidence_pack.validate_research_evidence_pack_contract_v1() == ()
    assert compendium.validate_literature_compendium_contract_v1() == ()


def test_normalization_does_not_mutate_input_compendium() -> None:
    raw = _reference_compendium()
    before = copy.deepcopy(raw)

    compendium.validate_literature_compendium(raw, evidence_packs=[_reference_pack()])

    assert raw == before
