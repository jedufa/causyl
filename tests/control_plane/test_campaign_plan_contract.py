from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import campaign_plan_contract as contract


def _base_plan() -> dict:
    return {
        "schema_version": "campaign_plan_v1",
        "plan_id": "plan_intent_to_campaign_demo_v1",
        "plan_version": "1.0.0",
        "stable_identity": {
            "canonicalization": "json_sort_keys_compact_utf8",
            "content_hash_algorithm": "sha256",
            "schema_migration_policy": "versioned_backward_compatible_or_explicit_migration",
        },
        "metadata": {
            "title": "Broad Intent to Campaign Plan Demo",
            "created_at_utc": "2026-04-11T11:35:58Z",
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "ledger_refs": ["PIS-20260411-113558Z-0B60"],
        },
        "vocabulary_boundary": {
            "intent_sheet": [
                "operator goal framing",
                "mission non-goals",
                "allowed broad ambiguity",
            ],
            "campaign_plan": [
                "phased plan structure",
                "named ambiguity policy",
                "policy-linked provenance",
            ],
            "pis": [
                "bounded implementation slice",
                "proof target",
                "narrow acceptance surface",
            ],
            "job": [
                "router payload",
                "runtime receipt",
                "execution lifecycle",
            ],
        },
        "ambiguity_policy": {
            "allowed_in_campaign_plan": [
                "Exact experiment sequence may remain open pending prior-knowledge review.",
                "Later child-PIS grouping may stay advisory until campaign phases are approved.",
            ],
            "must_resolve_before_pis": [
                "Target phase owner and acceptance boundary for each child slice.",
                "Concrete validation surface for each generated PIS.",
            ],
            "named_contract_violation": "campaign_plan_contract_violation",
        },
        "provenance": {
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "upstream_contract_refs": [
                "governance/campaign_record_contract_v1.json",
                "governance/scientific_campaign_method_template_v1.json",
                "governance/budget_adaptive_research_policy_v1.json",
            ],
            "supporting_artifact_refs": [
                "governance/ledger/intent_sheets/causyl/verified/2026/04/IS-20260403-212914Z-C623__intent-sheet-to-campaign-plan-compiler/intent_sheet.md"
            ],
            "lineage_hash": "sha256:intent_to_campaign_demo_v1",
        },
        "phases": [
            {
                "phase_id": "phase_shape_intent",
                "label": "Shape intent into campaign phases",
                "goal": "Translate broad operator intent into a phased plan without collapsing into one giant PIS.",
                "deliverables": [
                    "phase map",
                    "ambiguity register",
                    "policy-linked planning note",
                ],
                "method_template_phase_ids": [
                    "question_framing",
                    "prior_knowledge_consolidation",
                    "hypothesis_registry",
                ],
                "budget_mode": "campaign",
                "allowed_ambiguities": [
                    "Exact downstream PIS count may remain advisory at this stage."
                ],
                "must_resolve_before_pis": [
                    "Confirm which ambiguity markers are acceptable for each child PIS handoff."
                ],
                "pis_readiness": "advisory_only",
            },
            {
                "phase_id": "phase_prepare_child_slices",
                "label": "Prepare bounded child-PIS slices",
                "goal": "Identify candidate child-PIS boundaries and their proof surfaces from the approved phase map.",
                "deliverables": [
                    "candidate child-PIS set",
                    "phase-to-PIS mapping",
                    "readiness note",
                ],
                "method_template_phase_ids": [
                    "experiment_design",
                    "analysis_plan",
                    "adversarial_review",
                    "continuation",
                ],
                "budget_mode": "standard",
                "allowed_ambiguities": [],
                "must_resolve_before_pis": [],
                "pis_readiness": "ready_for_pis_generation",
            },
        ],
    }


def test_campaign_plan_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.campaign_plan_contract_v1()

    assert payload.version == "campaign_plan_contract_v1"
    assert payload.uid == "PIS-20260411-113558Z-0B60"
    assert payload.canonical_artifact_rel.as_posix() == "governance/campaign_plan_contract_v1.json"
    assert payload.reference_examples_rel.as_posix() == "governance/campaign_plan_reference_examples_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_campaign_plan_v1.md"
    assert payload.pis_readiness_states == contract.PIS_READINESS_STATES


def test_campaign_plan_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/campaign_plan_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert payload["reference_examples_rel"] == contract.REFERENCE_EXAMPLES_REL.as_posix()
    assert tuple(payload["top_level_required_fields"]) == contract.TOP_LEVEL_REQUIRED_FIELDS
    assert tuple(payload["optional_top_level_fields"]) == contract.OPTIONAL_TOP_LEVEL_FIELDS
    assert tuple(payload["phase_required_fields"]) == contract.PHASE_REQUIRED_FIELDS
    assert tuple(payload["pis_readiness_states"]) == contract.PIS_READINESS_STATES


def test_validate_campaign_plan_contract_and_reference_examples_return_no_issues() -> None:
    assert contract.validate_campaign_plan_contract_v1() == ()
    assert contract.validate_campaign_plan_reference_examples_v1() == ()


def test_validate_campaign_plan_accepts_valid_broad_intent_plan() -> None:
    result = contract.validate_campaign_plan(_base_plan(), repo_root=Path("."))

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["normalized_plan"]["phases"][0]["phase_id"] == "phase_prepare_child_slices"
    assert result["computed_fingerprint"] != ""


def test_validate_campaign_plan_accepts_policy_enrichment_receipts() -> None:
    raw = _base_plan()
    raw["phases"][1]["budget_mode"] = "campaign"
    raw["policy_enrichment"] = {
        "method_template_ref": "governance/scientific_campaign_method_template_reference_v1.json",
        "budget_policy_ref": "governance/budget_adaptive_research_policy_v1.json",
        "selected_budget_mode": "campaign",
        "declared_constraints": [
            {
                "constraint_id": "keep_continuation_visible",
                "constraint_kind": "require_method_phase",
                "phase_id": "phase_prepare_child_slices",
                "method_phase_id": "continuation",
                "advisory_only": False,
                "rationale": "Continuation posture must remain visible before handoff.",
            }
        ],
        "enrichment_receipts": [
            {
                "receipt_id": "method_phase_prepare_child_slices",
                "policy_source_kind": "method_template",
                "policy_ref": "governance/scientific_campaign_method_template_reference_v1.json",
                "target_phase_ids": ["phase_prepare_child_slices"],
                "changed_fields": ["deliverables"],
                "change_summary": "Added required method-template outputs to the campaign phase deliverables.",
                "reason_codes": ["method_required_outputs_bound"],
                "receipt_state": "applied",
            }
        ],
    }

    result = contract.validate_campaign_plan(raw, repo_root=Path("."))

    assert result["ok"] is True
    assert result["normalized_plan"]["policy_enrichment"]["selected_budget_mode"] == "campaign"


def test_validate_campaign_plan_rejects_contradictory_policy_constraints() -> None:
    raw = _base_plan()
    raw["policy_enrichment"] = {
        "method_template_ref": "governance/scientific_campaign_method_template_reference_v1.json",
        "budget_policy_ref": "governance/budget_adaptive_research_policy_v1.json",
        "selected_budget_mode": "campaign",
        "declared_constraints": [
            {
                "constraint_id": "must_keep_continuation",
                "constraint_kind": "require_method_phase",
                "phase_id": "phase_prepare_child_slices",
                "method_phase_id": "continuation",
                "advisory_only": False,
                "rationale": "Need continuation visibility.",
            },
            {
                "constraint_id": "must_drop_continuation",
                "constraint_kind": "forbid_method_phase",
                "phase_id": "phase_prepare_child_slices",
                "method_phase_id": "continuation",
                "advisory_only": False,
                "rationale": "No continuation allowed.",
            },
        ],
        "enrichment_receipts": [
            {
                "receipt_id": "constraint_mismatch",
                "policy_source_kind": "declared_constraint",
                "policy_ref": "governance/scientific_campaign_method_template_reference_v1.json",
                "target_phase_ids": ["phase_prepare_child_slices"],
                "changed_fields": [],
                "change_summary": "Conflicting constraints should be rejected.",
                "reason_codes": ["constraint_verified"],
                "receipt_state": "validated_no_change",
            }
        ],
    }

    result = contract.validate_campaign_plan(raw, repo_root=Path("."))

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "contradictory_constraint" in codes


def test_validate_campaign_plan_rejects_pseudo_pis_detail() -> None:
    raw = _base_plan()
    raw["patch_plan"] = ["edit file", "run tests"]
    raw["phases"][0]["file_paths"] = ["infra/controlplane/compiler.py"]

    result = contract.validate_campaign_plan(raw, repo_root=Path("."))

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "forbidden_pseudo_pis_detail" in codes


def test_validate_campaign_plan_rejects_under_specified_non_plan() -> None:
    raw = _base_plan()
    raw["vocabulary_boundary"]["pis"] = []
    raw["ambiguity_policy"]["must_resolve_before_pis"] = []
    raw["phases"] = []

    result = contract.validate_campaign_plan(raw, repo_root=Path("."))

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "under_specified_plan" in codes
    assert "missing_field" in codes


def test_validate_campaign_plan_rejects_vocab_overlap_and_bad_phase_vocabulary() -> None:
    raw = _base_plan()
    raw["vocabulary_boundary"]["job"] = ["proof target"]
    raw["phases"][0]["budget_mode"] = "mega"
    raw["phases"][0]["method_template_phase_ids"] = ["invented_phase"]

    result = contract.validate_campaign_plan(raw, repo_root=Path("."))

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "overlapping_vocabulary" in codes
    assert "invalid_value" in codes


def test_validate_campaign_plan_rejects_unresolved_ready_phase_and_unsafe_refs() -> None:
    raw = _base_plan()
    raw["phases"][1]["must_resolve_before_pis"] = ["still unresolved"]
    raw["provenance"]["upstream_contract_refs"] = ["/tmp/outside.json"]

    result = contract.validate_campaign_plan(raw, repo_root=Path("."))

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "invalid_value" in codes
    assert "invalid_ref" in codes
