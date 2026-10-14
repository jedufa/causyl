from __future__ import annotations

import copy
import json
from pathlib import Path

from infra.controlplane import scientific_campaign_method_template as contract


def _reference_template() -> dict:
    return json.loads(Path("governance/scientific_campaign_method_template_reference_v1.json").read_text(encoding="utf-8"))


def test_scientific_campaign_method_template_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.scientific_campaign_method_template_contract_v1()

    assert payload.version == "scientific_campaign_method_template_contract_v1"
    assert payload.uid == "PIS-20260410-193415Z-2A95"
    assert payload.canonical_artifact_rel.as_posix() == "governance/scientific_campaign_method_template_v1.json"
    assert payload.reference_template_rel.as_posix() == "governance/scientific_campaign_method_template_reference_v1.json"
    assert payload.pilot_fixtures_rel.as_posix() == "governance/scientific_campaign_method_template_pilot_fixtures_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_scientific_campaign_method_template_v1.md"
    assert payload.phase_ids[0] == "question_framing"
    assert payload.phase_extension_required_fields["hypothesis_registry"] == ("hypotheses",)
    assert "prose_only_templates_rejected" in payload.fail_closed_rules
    assert "missing_continuation_stop_conditions" in payload.advisory_method_posture_codes


def test_scientific_campaign_method_template_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/scientific_campaign_method_template_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert payload["reference_template_rel"] == contract.REFERENCE_TEMPLATE_REL.as_posix()
    assert payload["pilot_fixtures_rel"] == contract.PILOT_FIXTURES_REL.as_posix()
    assert tuple(payload["top_level_required_fields"]) == contract.TOP_LEVEL_REQUIRED_FIELDS
    assert tuple(payload["phase_ids"]) == contract.PHASE_IDS
    assert tuple(payload["phase_required_fields"]) == contract.PHASE_REQUIRED_FIELDS
    assert payload["phase_extension_required_fields"] == {
        key: list(value) for key, value in contract.PHASE_EXTENSION_REQUIRED_FIELDS.items()
    }
    assert tuple(payload["advisory_method_posture_codes"]) == contract.ADVISORY_METHOD_POSTURE_CODES


def test_reference_method_template_accepts_all_canonical_method_phases() -> None:
    result = contract.validate_scientific_campaign_method_template(_reference_template())

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["computed_fingerprint"] != ""
    assert [row["phase_id"] for row in result["normalized_template"]["phases"]] == list(contract.PHASE_IDS)
    prior_knowledge_phase = next(
        row for row in result["normalized_template"]["phases"] if row["phase_id"] == "prior_knowledge_consolidation"
    )
    assert [row["link_kind"] for row in prior_knowledge_phase["input_linkages"]] == [
        "campaign_phase_record",
        "evidence_pack_ref",
    ]
    hypothesis_phase = next(row for row in result["normalized_template"]["phases"] if row["phase_id"] == "hypothesis_registry")
    assert [row["claim_strength_tier"] for row in hypothesis_phase["hypotheses"]] == ["exploratory_hunch", "strong_claim"]
    outcome_phase = next(row for row in result["normalized_template"]["phases"] if row["phase_id"] == "outcome_documentation")
    assert outcome_phase["outcome_record"]["result_class"] == "negative_result"
    continuation_phase = next(row for row in result["normalized_template"]["phases"] if row["phase_id"] == "continuation")
    assert [row["link_kind"] for row in continuation_phase["output_linkages"]] == [
        "campaign_phase_record",
        "continuation_packet",
    ]


def test_method_template_rejects_missing_mandatory_phase_field() -> None:
    raw = _reference_template()
    raw["phases"][3]["required_outputs"] = []

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(row["field"] == "phases[3].required_outputs" and row["code"] == "missing_field" for row in result["errors"])


def test_method_template_rejects_non_reference_handoff_link_mode() -> None:
    raw = _reference_template()
    raw["phases"][1]["input_linkages"][1]["link_mode"] = "embedded_summary"

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(
        row["field"] == "phases[1].input_linkages[1].link_mode" and row["code"] == "invalid_link_mode"
        for row in result["errors"]
    )


def test_method_template_rejects_missing_mandatory_phase_row() -> None:
    raw = _reference_template()
    raw["phases"] = [row for row in raw["phases"] if row["phase_id"] != "continuation"]

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(row["field"] == "phases:continuation" and row["code"] == "missing_mandatory_phase" for row in result["errors"])


def test_method_template_rejects_prose_only_template() -> None:
    raw = {
        "schema_version": "scientific_campaign_method_template_v1",
        "template_id": "prose-only-demo",
        "template_version": "1.0.0",
        "stable_identity": {
            "canonicalization": "json_sort_keys_compact_utf8",
            "content_hash_algorithm": "sha256",
            "schema_migration_policy": "versioned_backward_compatible_or_explicit_migration",
        },
        "metadata": {
            "title": "Prose only pseudo-template",
            "created_at_utc": "2026-04-10T19:55:54Z",
            "source_intent_uid": "IS-20260403-212914Z-62FB",
            "ledger_refs": ["PIS-20260410-193415Z-2A95"],
        },
        "ownership_boundary": {
            "belongs_in_method_template": ["phase vocabulary"],
            "outside_method_template": ["campaign execution state"],
        },
        "prompt_prose_template": "First do good science, then write down what happened.",
    }

    result = contract.validate_scientific_campaign_method_template(raw)

    codes = {(row["field"], row["code"]) for row in result["errors"]}
    assert result["ok"] is False
    assert ("prompt_prose_template", "forbidden_prose_only_template") in codes
    assert ("phases", "missing_field") in codes


def test_method_template_rejects_inline_neighboring_payloads() -> None:
    raw = _reference_template()
    raw["phases"][1]["continuation_packet_body"] = {"schema_version": "continuation_packet_v1"}

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(row["field"] == "phases[1].continuation_packet_body" and row["code"] == "forbidden_inline_ownership" for row in result["errors"])


def test_method_template_rejects_unsupported_strong_claim_fixture() -> None:
    raw = _reference_template()
    raw["phases"][2]["hypotheses"][1]["support_basis"] = []

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(
        row["field"] == "phases[2].hypotheses[1].support_basis" and row["code"] == "unsupported_strong_claim"
        for row in result["errors"]
    )


def test_method_template_rejects_missing_adversarial_review_fixture() -> None:
    raw = _reference_template()
    raw["phases"][5]["review"] = {}

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(
        row["field"] == "phases[5].review.prompt" and row["code"] == "missing_field"
        for row in result["errors"]
    )


def test_method_template_rejects_negative_result_without_failure_notes() -> None:
    raw = _reference_template()
    raw["phases"][6]["outcome_record"]["failure_notes"] = []

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(
        row["field"] == "phases[6].outcome_record.failure_notes" and row["code"] == "missing_negative_result_failure_notes"
        for row in result["errors"]
    )


def test_method_template_rejects_missing_continuation_notes_fixture() -> None:
    raw = _reference_template()
    raw["phases"][7]["continuation_notes"] = {}

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(
        row["field"] == "phases[7].continuation_notes.next_decision" and row["code"] == "missing_field"
        for row in result["errors"]
    )


def test_method_template_rejects_prior_knowledge_phase_without_evidence_pack_link() -> None:
    raw = _reference_template()
    raw["phases"][1]["input_linkages"] = [raw["phases"][1]["input_linkages"][0]]
    raw["phases"][1]["output_linkages"] = [raw["phases"][1]["output_linkages"][0]]

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(row["code"] == "missing_evidence_pack_link" for row in result["errors"])


def test_method_template_rejects_continuation_phase_without_continuation_packet_link() -> None:
    raw = _reference_template()
    raw["phases"][7]["output_linkages"] = [raw["phases"][7]["output_linkages"][0]]

    result = contract.validate_scientific_campaign_method_template(raw)

    assert result["ok"] is False
    assert any(row["code"] == "missing_continuation_packet_link" for row in result["errors"])


def test_scientific_campaign_method_template_contract_v1_returns_no_issues() -> None:
    assert contract.validate_scientific_campaign_method_template_contract_v1() == ()


def test_scientific_campaign_method_template_pilot_fixtures_are_valid_and_advisory_complete() -> None:
    assert contract.validate_scientific_campaign_method_template_pilot_fixtures() == ()


def test_advisory_admission_warns_without_becoming_a_hard_gate() -> None:
    raw = _reference_template()
    raw["phases"][1]["input_linkages"] = [raw["phases"][1]["input_linkages"][0]]
    raw["phases"][1]["output_linkages"] = [raw["phases"][1]["output_linkages"][0]]
    raw["phases"][5]["review"]["challenge_questions"] = []
    raw["phases"][6]["outcome_record"]["uncertainty_notes"] = ""
    raw["phases"][7]["continuation_notes"]["stop_conditions"] = []

    advisory = contract.assess_scientific_campaign_method_advisory_admission(raw)
    validation = contract.validate_scientific_campaign_method_template(raw)

    assert advisory["admitted"] is True
    assert {row["code"] for row in advisory["warnings"]} == {
        "missing_prior_knowledge_evidence_chain",
        "missing_adversarial_review_challenge_questions",
        "missing_outcome_uncertainty_notes",
        "missing_continuation_stop_conditions",
    }
    assert validation["ok"] is False
    assert any(row["code"] == "missing_evidence_pack_link" for row in validation["errors"])
    assert any(row["field"] == "phases[5].review.challenge_questions" and row["code"] == "missing_field" for row in validation["errors"])
    assert any(
        row["field"] == "phases[7].continuation_notes.stop_conditions" and row["code"] == "missing_field"
        for row in validation["errors"]
    )


def test_normalization_does_not_mutate_input_template() -> None:
    raw = _reference_template()
    before = copy.deepcopy(raw)

    contract.validate_scientific_campaign_method_template(raw)

    assert raw == before
