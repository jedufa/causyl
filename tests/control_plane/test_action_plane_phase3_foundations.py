from __future__ import annotations

from experiments_shared import (
    build_operator_derived_state_view_v1,
    build_proof_gap_uncertainty_surface_v1,
    explain_operator_state_v1,
    recommend_next_safe_actions_v1,
    validate_next_safe_action_recommendation_payload,
    validate_operator_derived_state_view_payload,
    validate_proof_gap_uncertainty_surface_payload,
    validate_state_explanation_output,
)


def _blocked_patch_report() -> dict:
    return {
        "chain_id": "patch_apply_chain_v1::1",
        "playbook_id": "patch_apply_chain_v1",
        "action_family": "patch_apply",
        "final_chain_outcome": "blocked",
        "final_verifier_verdict": "",
        "terminal_reason": "patch_contract_invalid",
        "step_records": [
            {
                "node_id": "patch_ingest",
                "step_outcome": "failed_known",
                "proof_gaps": ["missing_validation_commands"],
                "decision": "block",
            }
        ],
        "failure_escalation_log": [],
    }


def test_phase3_foundations_derive_blocked_state_with_provenance() -> None:
    view = build_operator_derived_state_view_v1({
        "report": _blocked_patch_report(),
        "receipts": [{"status": "blocked"}],
        "watchdog_summary": {"status": "blocked"},
        "fallback_events": [{"action_family": "patch_apply", "failure_class": "patch_contract_invalid"}],
    })
    assert view["state_class"] == "blocked"
    assert view["proof_gap_present"] is True
    assert view["codex_used"] is True
    assert validate_operator_derived_state_view_payload(view)["ok"] is True


def test_phase3_uncertainty_surfaces_stale_and_contradictory_evidence() -> None:
    view = build_operator_derived_state_view_v1({
        "report": _blocked_patch_report(),
        "snapshot": {"status": "completed_with_pass", "freshness": "fresh"},
        "receipts": [{"status": "blocked", "freshness": "stale"}],
        "watchdog_summary": {"status": "blocked"},
    })
    uncertainty = build_proof_gap_uncertainty_surface_v1({"derived_state_view": view})
    assert uncertainty["contradiction_present"] is True
    assert uncertainty["stale_evidence_present"] is True
    assert uncertainty["confidence_bucket"] == "contradictory"
    assert validate_proof_gap_uncertainty_surface_payload(uncertainty)["ok"] is True


def test_phase3_explainer_reports_blocked_proof_gap_without_polite_lying() -> None:
    view = build_operator_derived_state_view_v1({"report": _blocked_patch_report()})
    uncertainty = build_proof_gap_uncertainty_surface_v1({"derived_state_view": view})
    explanation = explain_operator_state_v1({"derived_state_view": view, "uncertainty_surface": uncertainty})
    assert explanation["status_class"] == "blocked"
    assert explanation["proof_state"] == "proof_gap"
    assert "blocked" in explanation["summary"].lower()
    assert validate_state_explanation_output(explanation)["ok"] is True


def test_phase3_recommender_returns_only_approved_actions() -> None:
    view = build_operator_derived_state_view_v1({"report": _blocked_patch_report()})
    uncertainty = build_proof_gap_uncertainty_surface_v1({"derived_state_view": view})
    explanation = explain_operator_state_v1({"derived_state_view": view, "uncertainty_surface": uncertainty})
    recs = recommend_next_safe_actions_v1({
        "derived_state_view": view,
        "uncertainty_surface": uncertainty,
        "explanation_output": explanation,
    })
    action_ids = [row["action_id"] for row in recs["recommendations"]]
    assert action_ids[0] == "patch_apply_chain_v1"
    assert "pis_assess_proof_gap_v1" in action_ids
    assert validate_next_safe_action_recommendation_payload(recs)["ok"] is True


def test_phase3_recommender_prefers_reconcile_on_contradiction() -> None:
    view = build_operator_derived_state_view_v1({
        "report": _blocked_patch_report(),
        "snapshot": {"status": "completed_with_pass"},
        "receipts": [{"status": "blocked"}],
    })
    uncertainty = build_proof_gap_uncertainty_surface_v1({"derived_state_view": view})
    explanation = explain_operator_state_v1({"derived_state_view": view, "uncertainty_surface": uncertainty})
    recs = recommend_next_safe_actions_v1({
        "derived_state_view": view,
        "uncertainty_surface": uncertainty,
        "explanation_output": explanation,
    })
    assert recs["recommendations"][0]["action_id"] == "status_reconcile_v1"


def test_phase3_pass_complete_case_emits_no_action_recommendation() -> None:
    view = build_operator_derived_state_view_v1({
        "report": {
            "chain_id": "build_verify_loop_v1::1",
            "playbook_id": "build_verify_loop_v1",
            "action_family": "build_verify",
            "final_chain_outcome": "completed_with_pass",
            "final_verifier_verdict": "PASS",
            "terminal_reason": "terminal_verifier_complete",
            "step_records": [],
            "failure_escalation_log": [],
        }
    })
    uncertainty = build_proof_gap_uncertainty_surface_v1({"derived_state_view": view})
    explanation = explain_operator_state_v1({"derived_state_view": view, "uncertainty_surface": uncertainty})
    recs = recommend_next_safe_actions_v1({
        "derived_state_view": view,
        "uncertainty_surface": uncertainty,
        "explanation_output": explanation,
    })
    assert recs["recommendations"] == []
    assert recs["no_recommendation_reason"] == "state_pass_complete"
