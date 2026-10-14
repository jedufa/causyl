from __future__ import annotations

from pathlib import Path

from infra.controlplane import campaign_phase_synthesis
from infra.controlplane import campaign_plan_enrichment


def _seed_plan() -> dict:
    return {
        "schema_version": "campaign_plan_v1",
        "plan_id": "plan_intent_to_campaign_demo_v2",
        "plan_version": "1.1.0",
        "stable_identity": {
            "canonicalization": "json_sort_keys_compact_utf8",
            "content_hash_algorithm": "sha256",
            "schema_migration_policy": "versioned_backward_compatible_or_explicit_migration",
        },
        "metadata": {
            "title": "Intent To Campaign Compiler Demo",
            "created_at_utc": "2026-04-11T12:10:03Z",
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "ledger_refs": [
                "PIS-20260411-113558Z-0B60",
                "PIS-20260411-113559Z-8B17",
                "PIS-20260411-113601Z-59F3",
            ],
        },
        "vocabulary_boundary": {
            "intent_sheet": [
                "operator goal framing",
                "mission non-goals",
                "allowed broad ambiguity",
            ],
            "campaign_plan": [
                "phased plan structure",
                "review gate placement",
                "named ambiguity policy",
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
            "allowed_in_campaign_plan": [],
            "must_resolve_before_pis": [],
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
            "lineage_hash": "sha256:intent_to_campaign_demo_v2",
        },
    }


def _compiled_plan() -> dict:
    request = {
        "schema_version": "campaign_phase_synthesis_request_v1",
        "campaign_plan_seed": _seed_plan(),
        "mission_scope": "multi_stage_campaign",
        "intent_summary": "Turn broad operator research intent into a phased campaign plan with bounded child-PIS handoffs.",
        "operator_constraints": [
            "Keep the planning layer advisory and non-executable.",
            "Expose unresolved ambiguity instead of inventing certainty.",
            "Preserve explicit review gates before downstream PIS generation.",
        ],
        "required_outputs": [
            "phase map",
            "ambiguity register",
            "review-gate note",
            "child-PIS readiness note",
        ],
        "decomposition_triggers": [
            "broad intent spans more than one independent proof boundary",
            "later child slices need different acceptance targets",
        ],
        "review_gate_topics": [
            "scope approval",
            "decomposition approval",
        ],
        "requested_budget_mode": "campaign",
        "ambiguities": [
            {
                "topic": "Exact experiment sequence may remain open until prior-knowledge review is complete.",
                "resolution_timing": "allowed_in_campaign_plan",
            },
            {
                "topic": "Target phase owner must be named before any child PIS is emitted.",
                "resolution_timing": "must_resolve_before_pis",
            },
        ],
    }
    result = campaign_phase_synthesis.compile_campaign_phase_plan(request)
    assert result["ok"] is True
    return result["campaign_plan"]


def test_enrich_campaign_plan_binds_method_and_budget_receipts() -> None:
    result = campaign_plan_enrichment.enrich_campaign_plan(
        {
            "schema_version": "campaign_plan_enrichment_request_v1",
            "campaign_plan": _compiled_plan(),
            "method_template_ref": "governance/scientific_campaign_method_template_reference_v1.json",
            "budget_policy_ref": "governance/budget_adaptive_research_policy_v1.json",
            "selected_budget_mode": "campaign",
            "declared_constraints": [
                {
                    "constraint_id": "keep_continuation_visible",
                    "constraint_kind": "require_method_phase",
                    "phase_id": "phase_03_prepare_child_pis_handoff",
                    "method_phase_id": "continuation",
                    "advisory_only": False,
                    "rationale": "Continuation posture must remain visible before child-PIS handoff.",
                }
            ],
        },
        repo_root=Path("."),
    )

    assert result["ok"] is True
    assert result["status"] == "enriched"
    plan = result["campaign_plan"]
    receipts = plan["policy_enrichment"]["enrichment_receipts"]
    final_phase = plan["phases"][-1]
    assert final_phase["budget_mode"] == "campaign"
    assert "continuation packet recommendation" in final_phase["deliverables"]
    assert any(row["policy_source_kind"] == "method_template" for row in receipts)
    assert any(row["policy_source_kind"] == "budget_policy" for row in receipts)
    assert any(row["policy_source_kind"] == "declared_constraint" for row in receipts)


def test_enrich_campaign_plan_rejects_missing_upstream_inputs() -> None:
    result = campaign_plan_enrichment.enrich_campaign_plan(
        {
            "schema_version": "campaign_plan_enrichment_request_v1",
            "campaign_plan": _compiled_plan(),
            "budget_policy_ref": "governance/budget_adaptive_research_policy_v1.json",
            "selected_budget_mode": "campaign",
        },
        repo_root=Path("."),
    )

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "missing_field" in codes


def test_enrich_campaign_plan_rejects_budget_mode_contradiction() -> None:
    result = campaign_plan_enrichment.enrich_campaign_plan(
        {
            "schema_version": "campaign_plan_enrichment_request_v1",
            "campaign_plan": _compiled_plan(),
            "method_template_ref": "governance/scientific_campaign_method_template_reference_v1.json",
            "budget_policy_ref": "governance/budget_adaptive_research_policy_v1.json",
            "selected_budget_mode": "micro",
            "declared_constraints": [],
        },
        repo_root=Path("."),
    )

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "budget_mode_contradicts_phase_plan" in codes


def test_enrich_campaign_plan_rejects_forbidden_method_phase_constraint() -> None:
    result = campaign_plan_enrichment.enrich_campaign_plan(
        {
            "schema_version": "campaign_plan_enrichment_request_v1",
            "campaign_plan": _compiled_plan(),
            "method_template_ref": "governance/scientific_campaign_method_template_reference_v1.json",
            "budget_policy_ref": "governance/budget_adaptive_research_policy_v1.json",
            "selected_budget_mode": "campaign",
            "declared_constraints": [
                {
                    "constraint_id": "drop_continuation",
                    "constraint_kind": "forbid_method_phase",
                    "phase_id": "phase_03_prepare_child_pis_handoff",
                    "method_phase_id": "continuation",
                    "advisory_only": False,
                    "rationale": "Contradict the final continuation handoff.",
                }
            ],
        },
        repo_root=Path("."),
    )

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert "forbidden_method_phase_present" in codes
