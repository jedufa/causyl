from __future__ import annotations

from experiments_shared import (
    build_codex_usage_transparency_v1,
    build_operator_action_surface_v1,
    build_phase3_regression_report,
    execute_phase3_blocked_chain_explain_and_recommend_v1,
    validate_codex_usage_transparency_payload,
    validate_operator_action_surface_payload,
    validate_phase3_operator_flow_registry,
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
                "checklist_complete": True,
            }
        ],
        "failure_escalation_log": [],
    }


def _pass_report() -> dict:
    return {
        "chain_id": "build_verify_loop_v1::1",
        "playbook_id": "build_verify_loop_v1",
        "action_family": "build_verify",
        "final_chain_outcome": "completed_with_pass",
        "final_verifier_verdict": "PASS",
        "terminal_reason": "verifier_pass",
        "step_records": [],
        "failure_escalation_log": [],
    }


def _fallback_event(*, cost: float, build_cost: float = 20.0, maintenance: float = 5.0) -> dict:
    return {
        "action_family": "patch_apply",
        "intent": "apply incoming patch",
        "failure_class": "semantic_conflict",
        "deterministic_steps_attempted": ["patch_ingest_repair_v1", "validation_subset_v1"],
        "fallback_reason": "semantic_merge_required",
        "pre_escalation_checklist": {
            "evidence_gathered": True,
            "failure_class_recorded": True,
            "deterministic_recovery_exhausted_or_inapplicable": True,
            "downstream_validation_contract_prepared": True,
        },
        "post_fallback_outcome": "success_after_codex_fallback",
        "solution_path_signature": "semantic_merge_then_validate",
        "bounded_authority_safe": True,
        "validator_path_clear": True,
        "expected_future_codex_cost": cost,
        "operator_friction_cost": 1.0,
        "estimated_build_cost": build_cost,
        "estimated_maintenance_cost": maintenance,
    }


def test_codex_usage_transparency_marks_repeated_uneconomic_usage() -> None:
    payload = {
        "report": _blocked_patch_report(),
        "fallback_events": [_fallback_event(cost=2.0, build_cost=50.0), _fallback_event(cost=2.5, build_cost=50.0)],
    }
    result = build_codex_usage_transparency_v1(payload)
    assert result["codex_used"] is True
    assert result["recurrence_class"] == "repeated_uneconomic"
    assert result["economic_recommendation_class"] == "keep_codex_fallback"
    assert validate_codex_usage_transparency_payload(result)["ok"] is True


def test_operator_surface_shows_blocked_state_uncertainty_and_codex() -> None:
    flow = execute_phase3_blocked_chain_explain_and_recommend_v1({
        "report": _blocked_patch_report(),
        "fallback_events": [_fallback_event(cost=10.0)],
    })
    surface = flow["operator_surface"]
    assert surface["status_class"] == "blocked"
    assert surface["executive_surface"]["state"] == "BLOCKED"
    assert surface["executive_surface"]["confidence"] == "Medium"
    assert surface["recommendations"]["primary"]["action_id"] == "patch_apply_chain_v1"
    assert all(row["action_id"] != surface["recommendations"]["primary"]["action_id"] for row in surface["recommendations"]["alternatives"])
    assert surface["proof_uncertainty"]["proof_gap_present"] is True
    assert surface["codex_transparency"]["codex_used"] is True
    assert surface["recommended_actions"][0]["action_id"] == "patch_apply_chain_v1"
    assert surface["codex_transparency"]["family"] == "patch_apply"
    assert validate_operator_action_surface_payload(surface)["ok"] is True


def test_phase3_concrete_flow_pass_case_is_minimal_and_truthful() -> None:
    flow = execute_phase3_blocked_chain_explain_and_recommend_v1({
        "report": _pass_report(),
    })
    assert flow["ok"] is True
    assert flow["operator_surface"]["recommended_actions"] == []
    assert flow["operator_surface"]["status_class"] == "pass"
    assert flow["operator_surface"]["executive_surface"]["state"] == "PASS"
    assert flow["operator_surface"]["executive_surface"]["confidence"] == "High"
    assert flow["operator_surface"]["recommendations"]["primary"] == {}
    assert flow["codex_usage_output"]["codex_used"] is False


def test_phase3_manual_review_boundary_becomes_primary_action() -> None:
    surface = build_operator_action_surface_v1(
        {
            "derived_state_view": {
                "schema_version": "operator_derived_state_view_v1",
                "view_id": "manual_review_case",
                "subject_kind": "chain",
                "action_family": "unknown",
                "state_class": "failed",
                "primary_reason_hint": "unsupported_failure_class",
                "final_verifier_verdict": "",
                "proof_gap_present": False,
                "unresolved_proof_gaps": [],
                "stale_evidence_present": False,
                "contradiction_present": False,
                "source_statuses": {},
                "freshness_by_source": {},
                "codex_used": False,
                "evidence_provenance": {"state_class": ["report.status"]},
            },
            "uncertainty_surface": {
                "schema_version": "proof_gap_uncertainty_surface_v1",
                "proof_gap_present": False,
                "unresolved_items": [],
                "stale_evidence_present": False,
                "contradiction_present": False,
                "confidence_bucket": "low",
                "required_next_proof_steps": [],
                "evidence_references": {},
            },
            "explanation_output": {
                "schema_version": "state_explanation_output_v1",
                "summary": "State is unknown because the available evidence is insufficient.",
                "status_class": "failed",
                "primary_reason": "unsupported_failure_class",
                "evidence_references": ["report.status"],
                "proof_state": "uncertain",
                "recommendation_eligible": True,
                "contradiction_present": False,
                "stale_evidence_present": False,
            },
            "recommendation_output": {
                "schema_version": "next_safe_action_recommendation_v1",
                "recommendations": [],
                "manual_review_required": True,
                "no_recommendation_reason": "no_safe_approved_action",
            },
            "codex_usage_output": {
                "schema_version": "codex_usage_transparency_v1",
                "codex_used": False,
                "action_family": "",
                "usage_reason": "",
                "fallback_event_count": 0,
                "recurrence_class": "none",
                "absorption_eligibility_class": "none",
                "economic_recommendation_class": "none",
                "evidence_references": [],
                "improvement_decision": "",
                "rejection_reason": "",
            },
        }
    )
    assert surface["executive_surface"]["state"] == "REVIEW_REQUIRED"
    assert surface["recommendations"]["primary"]["action_id"] == "manual_review"
    assert surface["recommendations"]["primary"]["label"] == "Manual review required"
    assert validate_operator_action_surface_payload(surface)["ok"] is True


def test_phase3_operator_flow_registry_and_regression_anchor_hold() -> None:
    assert validate_phase3_operator_flow_registry()["ok"] is True

    blocked = execute_phase3_blocked_chain_explain_and_recommend_v1({
        "report": _blocked_patch_report(),
        "fallback_events": [_fallback_event(cost=10.0), _fallback_event(cost=11.0, build_cost=5.0)],
    })
    degraded = execute_phase3_blocked_chain_explain_and_recommend_v1({
        "report": _blocked_patch_report(),
        "snapshot": {"status": "completed_with_pass", "freshness": "fresh"},
        "receipts": [{"status": "blocked", "freshness": "stale"}],
    })
    passed = execute_phase3_blocked_chain_explain_and_recommend_v1({"report": _pass_report()})
    regression = build_phase3_regression_report({
        "blocked": blocked,
        "degraded": degraded,
        "passed": passed,
    })
    assert regression["ok"] is True
    assert regression["checks"]["recommendations_approved"] is True
    assert regression["checks"]["codex_visibility_when_used"] is True
