from __future__ import annotations

import copy
import json
from pathlib import Path

from infra.controlplane import research_evidence_pack_contract as contract


def _reference_pack() -> dict:
    return json.loads(Path("governance/research_evidence_pack_reference_v1.json").read_text(encoding="utf-8"))


def test_research_evidence_pack_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.research_evidence_pack_contract_v1()

    assert payload.version == "research_evidence_pack_contract_v1"
    assert payload.uid == "PIS-20260410-102637Z-DCFE"
    assert payload.canonical_artifact_rel.as_posix() == "governance/research_evidence_pack_contract_v1.json"
    assert payload.reference_pack_rel.as_posix() == "governance/research_evidence_pack_reference_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_research_evidence_pack_v1.md"
    assert "claim_source_links" in payload.top_level_required_fields
    assert "unresolved_contradictions_require_unresolved_notes" in payload.fail_closed_rules


def test_research_evidence_pack_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/research_evidence_pack_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert payload["reference_pack_rel"] == contract.REFERENCE_PACK_REL.as_posix()
    assert tuple(payload["top_level_required_fields"]) == contract.TOP_LEVEL_REQUIRED_FIELDS
    assert tuple(payload["claim_required_fields"]) == contract.CLAIM_REQUIRED_FIELDS
    assert tuple(payload["claim_source_link_required_fields"]) == contract.CLAIM_LINK_REQUIRED_FIELDS
    assert tuple(payload["contradiction_required_fields"]) == contract.CONTRADICTION_REQUIRED_FIELDS


def test_reference_research_evidence_pack_accepts_clean_claim_and_unresolved_contradiction() -> None:
    result = contract.validate_research_evidence_pack(_reference_pack())

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["computed_fingerprint"] != ""
    assert result["normalized_pack"]["contradictions"][0]["status"] == "unresolved"


def test_research_evidence_pack_rejects_unsupported_claim() -> None:
    raw = _reference_pack()
    raw["claim_source_links"] = [row for row in raw["claim_source_links"] if row["claim_id"] != "claim-clean"]

    result = contract.validate_research_evidence_pack(raw)

    assert result["ok"] is False
    assert any(row["code"] == "unsupported_claim" and row["field"] == "claim:claim-clean" for row in result["errors"])


def test_research_evidence_pack_rejects_prose_only_provenance_link() -> None:
    raw = _reference_pack()
    raw["claim_source_links"][0]["evidence_locator"] = ""

    result = contract.validate_research_evidence_pack(raw)

    assert result["ok"] is False
    assert any(row["code"] == "missing_field" and row["field"] == "claim_source_links[0].evidence_locator" for row in result["errors"])


def test_research_evidence_pack_rejects_flattened_unresolved_contradiction() -> None:
    raw = _reference_pack()
    raw["unresolved_conflict_notes"] = []

    result = contract.validate_research_evidence_pack(raw)

    assert result["ok"] is False
    assert any(row["code"] == "unresolved_contradiction_without_note" for row in result["errors"])


def test_research_evidence_pack_rejects_inline_neighboring_artifact_payloads() -> None:
    raw = _reference_pack()
    raw["continuation_packet_body"] = {"schema_version": "continuation_packet_v1"}

    result = contract.validate_research_evidence_pack(raw)

    assert result["ok"] is False
    assert any(row["code"] == "forbidden_inline_ownership" for row in result["errors"])


def test_research_evidence_pack_contract_v1_returns_no_issues() -> None:
    assert contract.validate_research_evidence_pack_contract_v1() == ()


def test_normalization_does_not_mutate_input_pack() -> None:
    raw = _reference_pack()
    before = copy.deepcopy(raw)

    contract.validate_research_evidence_pack(raw)

    assert raw == before
