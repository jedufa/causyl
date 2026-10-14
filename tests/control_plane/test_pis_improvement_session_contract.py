from __future__ import annotations

import copy
import json
from pathlib import Path

from infra.controlplane import pis_improvement_session_contract as contract


def _reference_examples() -> dict:
    return json.loads(Path("governance/pis_improvement_session_reference_examples_v1.json").read_text(encoding="utf-8"))


def _valid_session() -> dict:
    return copy.deepcopy(_reference_examples()["examples"][0]["session_record"])


def test_pis_improvement_session_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.pis_improvement_session_contract_v1()

    assert payload.version == "pis_improvement_session_contract_v1"
    assert payload.uid == "PIS-20260412-062100Z-A1C3"
    assert payload.canonical_artifact_rel.as_posix() == "governance/pis_improvement_session_contract_v1.json"
    assert payload.reference_examples_rel.as_posix() == "governance/pis_improvement_session_reference_examples_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_pis_improvement_session_v1.md"
    assert payload.critique_packet_required_fields == contract.CRITIQUE_PACKET_REQUIRED_FIELDS
    assert payload.reviser_packet_required_fields == contract.REVISER_PACKET_REQUIRED_FIELDS
    assert payload.candidate_selection_required_fields == contract.CANDIDATE_SELECTION_REQUIRED_FIELDS
    assert "critique_packets_must_be_specific_evidence_bearing_and_non_flattering" in payload.fail_closed_rules


def test_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/pis_improvement_session_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert payload["reference_examples_rel"] == contract.REFERENCE_EXAMPLES_REL.as_posix()
    assert tuple(payload["top_level_required_fields"]) == contract.TOP_LEVEL_REQUIRED_FIELDS
    assert tuple(payload["critique_packet_required_fields"]) == contract.CRITIQUE_PACKET_REQUIRED_FIELDS
    assert tuple(payload["reviser_packet_required_fields"]) == contract.REVISER_PACKET_REQUIRED_FIELDS
    assert tuple(payload["candidate_selection_required_fields"]) == contract.CANDIDATE_SELECTION_REQUIRED_FIELDS
    assert tuple(payload["revision_ledger_required_fields"]) == contract.REVISION_LEDGER_REQUIRED_FIELDS
    assert tuple(payload["fail_closed_rules"]) == contract.FAIL_CLOSED_RULES


def test_reference_examples_validate_against_runtime_contract() -> None:
    assert contract.validate_pis_improvement_session_contract_v1() == ()
    assert contract.validate_pis_improvement_session_reference_examples_v1() == ()


def test_validate_accepts_valid_recent_pis_session() -> None:
    result = contract.validate_pis_improvement_session(_valid_session())

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["computed_fingerprint"] != ""
    assert result["normalized_session"]["candidate_selection"]["selected_candidate_refs"] == ["cand_child_contract"]


def test_validate_rejects_hidden_context_candidate_selection() -> None:
    raw = _valid_session()
    raw["candidate_selection"]["selection_basis"] = "hidden_chat_context"

    result = contract.validate_pis_improvement_session(raw)

    assert result["ok"] is False
    assert any(
        row["field"] == "candidate_selection.selection_basis" and row["code"] == "invalid_selection_basis"
        for row in result["errors"]
    )


def test_validate_rejects_selected_candidate_outside_materialized_pool() -> None:
    raw = _valid_session()
    raw["candidate_selection"]["selected_candidate_refs"] = ["cand_missing"]

    result = contract.validate_pis_improvement_session(raw)

    assert result["ok"] is False
    assert any(row["code"] == "unknown_ref" and row["field"] == "candidate_selection.selected_candidate_refs" for row in result["errors"])


def test_validate_rejects_revision_history_gap_without_delta() -> None:
    raw = _valid_session()
    raw["revision_ledger"]["revision_deltas"] = []

    result = contract.validate_pis_improvement_session(raw)

    assert result["ok"] is False
    assert any(row["code"] == "missing_revision_delta" for row in result["errors"])


def test_validate_rejects_inline_mutable_source_payload() -> None:
    raw = _valid_session()
    raw["mutable_source_pis_body"] = {"title": "This should not be inline."}

    result = contract.validate_pis_improvement_session(raw)

    assert result["ok"] is False
    assert any(row["field"] == "mutable_source_pis_body" and row["code"] == "forbidden_inline_ownership" for row in result["errors"])


def test_validate_rejects_unsafe_repo_relative_paths() -> None:
    raw = _valid_session()
    raw["artifact_pointers"]["revision_ledger"] = "../escape/revision_ledger.json"

    result = contract.validate_pis_improvement_session(raw)

    assert result["ok"] is False
    assert any(row["field"] == "artifact_pointers.revision_ledger" and row["code"] == "invalid_ref" for row in result["errors"])


def test_validate_rejects_generic_praise_only_critique_packet() -> None:
    raw = _valid_session()
    critique_packet = raw["revision_ledger"]["critique_rounds"][0]["critique_packet"]
    critique_packet["summary"] = "Looks good."
    critique_packet["critique_items"][0]["finding"] = "Great work."
    critique_packet["critique_items"][0]["evidence"] = "Nice work."
    critique_packet["critique_items"][0]["proposed_resolution"] = "Ship it."

    result = contract.validate_pis_improvement_session(raw)

    assert result["ok"] is False
    assert any(row["code"] == "generic_praise" for row in result["errors"])


def test_validate_rejects_reviser_packet_that_skips_critique_resolution() -> None:
    raw = _valid_session()
    raw["revision_ledger"]["revision_deltas"][0]["reviser_packet"]["resolution_items"] = []

    result = contract.validate_pis_improvement_session(raw)

    assert result["ok"] is False
    assert any(row["code"] in {"missing_field", "missing_resolution"} for row in result["errors"])


def test_normalization_does_not_mutate_input() -> None:
    raw = _valid_session()
    before = copy.deepcopy(raw)

    contract.validate_pis_improvement_session(raw)

    assert raw == before
