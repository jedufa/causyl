from __future__ import annotations

import json

from infra.controlplane import campaign_phase_synthesis
from infra.controlplane import campaign_plan_advisory_handoff
from infra.controlplane import campaign_plan_enrichment


def _seed_plan() -> dict:
    return {
        "schema_version": "campaign_plan_v1",
        "plan_id": "plan_intent_to_campaign_demo_v4",
        "plan_version": "1.3.0",
        "stable_identity": {
            "canonicalization": "json_sort_keys_compact_utf8",
            "content_hash_algorithm": "sha256",
            "schema_migration_policy": "versioned_backward_compatible_or_explicit_migration",
        },
        "metadata": {
            "title": "Intent To Campaign Compiler Demo",
            "created_at_utc": "2026-04-11T14:10:03Z",
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "ledger_refs": [
                "PIS-20260411-113558Z-0B60",
                "PIS-20260411-113559Z-8B17",
                "PIS-20260411-113601Z-59F3",
            ],
        },
        "vocabulary_boundary": {
            "intent_sheet": ["operator goal framing", "mission non-goals", "allowed broad ambiguity"],
            "campaign_plan": ["phased plan structure", "review gate placement", "named ambiguity policy"],
            "pis": ["bounded implementation slice", "proof target", "narrow acceptance surface"],
            "job": ["router payload", "runtime receipt", "execution lifecycle"],
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
            "lineage_hash": "sha256:intent_to_campaign_demo_v4",
        },
    }


def _compiler_stack() -> tuple[dict, dict]:
    synthesis = campaign_phase_synthesis.compile_campaign_phase_plan(
        {
            "schema_version": "campaign_phase_synthesis_request_v1",
            "campaign_plan_seed": _seed_plan(),
            "mission_scope": "multi_stage_campaign",
            "intent_summary": "Turn broad operator research intent into a phased campaign plan with bounded advisory child-PIS handoffs.",
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
            "review_gate_topics": ["scope approval", "decomposition approval"],
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
                {
                    "topic": "Concrete validation surface for each child PIS must be fixed before handoff.",
                    "resolution_timing": "must_resolve_before_pis",
                },
            ],
        }
    )
    enrichment = campaign_plan_enrichment.enrich_campaign_plan(
        {
            "schema_version": "campaign_plan_enrichment_request_v1",
            "campaign_plan": synthesis["campaign_plan"],
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
        }
    )
    assert synthesis["ok"] is True
    assert enrichment["ok"] is True
    return synthesis, enrichment


def test_build_campaign_plan_advisory_handoff_artifact_emits_explainability_and_advisory_proposals() -> None:
    synthesis, enrichment = _compiler_stack()

    artifact = campaign_plan_advisory_handoff.build_campaign_plan_advisory_handoff_artifact(
        {
            "schema_version": campaign_plan_advisory_handoff.REQUEST_SCHEMA_VERSION,
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "phase_synthesis_report": synthesis,
            "enrichment_report": enrichment,
        }
    )

    assert campaign_plan_advisory_handoff.validate_campaign_plan_advisory_handoff_artifact(artifact) == []
    assert artifact["ok"] is True
    assert artifact["status"] == "advisory_ready"
    assert len(artifact["phase_derivations"]) == 3
    assert artifact["advisory_child_pis_handoff"]["proposal_count"] == 1
    assert artifact["compiled_work_order"]["status"] == "compiled_ready"
    proposal = artifact["advisory_child_pis_handoff"]["proposals"][0]
    assert proposal["advisory_only"] is True
    assert proposal["launch_authority"] == "forbidden"
    assert proposal["execution_owner"] == ""


def test_build_campaign_plan_advisory_handoff_artifact_rejects_missing_policy_receipts() -> None:
    synthesis, enrichment = _compiler_stack()
    enrichment["campaign_plan"]["policy_enrichment"]["enrichment_receipts"] = []

    artifact = campaign_plan_advisory_handoff.build_campaign_plan_advisory_handoff_artifact(
        {
            "schema_version": campaign_plan_advisory_handoff.REQUEST_SCHEMA_VERSION,
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "phase_synthesis_report": synthesis,
            "enrichment_report": enrichment,
        }
    )

    assert artifact["ok"] is False
    assert "missing_policy_enrichment_receipts" in artifact["reason_codes"]


def test_build_campaign_plan_advisory_handoff_artifact_rejects_missing_phase_generation_trace() -> None:
    synthesis, enrichment = _compiler_stack()
    synthesis["phase_generation_trace"] = []

    artifact = campaign_plan_advisory_handoff.build_campaign_plan_advisory_handoff_artifact(
        {
            "schema_version": campaign_plan_advisory_handoff.REQUEST_SCHEMA_VERSION,
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "phase_synthesis_report": synthesis,
            "enrichment_report": enrichment,
        }
    )

    assert artifact["ok"] is False
    assert "missing_phase_generation_trace" in artifact["reason_codes"]
    assert artifact["compiled_work_order"]["status"] == "fallback_legacy"


def test_validate_campaign_plan_advisory_handoff_artifact_rejects_non_advisory_proposal() -> None:
    synthesis, enrichment = _compiler_stack()
    artifact = campaign_plan_advisory_handoff.build_campaign_plan_advisory_handoff_artifact(
        {
            "schema_version": campaign_plan_advisory_handoff.REQUEST_SCHEMA_VERSION,
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "phase_synthesis_report": synthesis,
            "enrichment_report": enrichment,
        }
    )
    artifact["advisory_child_pis_handoff"]["proposals"][0]["execution_owner"] = "scheduler"
    artifact["artifact_fingerprint"] = "stale"

    issues = campaign_plan_advisory_handoff.validate_campaign_plan_advisory_handoff_artifact(artifact)

    assert any("must not claim execution_owner" in issue for issue in issues)
    assert any("artifact_fingerprint does not match payload" in issue for issue in issues)


def test_write_campaign_plan_advisory_handoff_artifact_persists_compiled_work_order(tmp_path) -> None:
    synthesis, enrichment = _compiler_stack()
    artifact = campaign_plan_advisory_handoff.build_campaign_plan_advisory_handoff_artifact(
        {
            "schema_version": campaign_plan_advisory_handoff.REQUEST_SCHEMA_VERSION,
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "phase_synthesis_report": synthesis,
            "enrichment_report": enrichment,
        }
    )

    path = campaign_plan_advisory_handoff.write_campaign_plan_advisory_handoff_artifact(artifact, repo_root=tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["compiled_work_order"]["status"] == "compiled_emitted"
    assert (tmp_path / payload["compiled_work_order"]["compiled_artifact_rel"]).exists()
