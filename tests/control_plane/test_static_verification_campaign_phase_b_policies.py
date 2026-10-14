from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_campaign_stop_rules import evaluate_campaign_stop
from infra.controlplane.static_verification.static_verification_dut_delta import DutDeltaError, compute_dut_health_delta
from infra.controlplane.static_verification.static_verification_repair_policy import RepairPolicyError, route_repairs_with_policy
from infra.controlplane.static_verification.static_verification_revalidation import RevalidationPolicyError, plan_post_repair_revalidation
from infra.controlplane.static_verification.static_verification_unattended_policy import evaluate_unattended_campaign_state


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_phase_b_policy_contract_artifacts_exist_for_next_five_children() -> None:
    roots = [
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170107Z-K4L5__define-repair-execution-route-safety-policy-and-autonomy-boundaries/artifacts/campaigns/repair_autonomy_policy_v1.json",
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170108Z-L5M6__require-post-repair-revalidation-and-family-level-rerun-policy/artifacts/campaigns/post_repair_revalidation_contract_v1.json",
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170109Z-M6N7__define-campaign-economic-stop-rules-and-non-yield-termination-policy/artifacts/campaigns/campaign_stop_rules_v1.json",
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170110Z-N7P8__define-dut-health-delta-contract-and-residual-risk-closeout/artifacts/campaigns/dut_health_delta_contract_v1.json",
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170111Z-P8Q9__define-unattended-safety-review-and-escalation-boundaries-for-autonomous-campaigns/artifacts/campaigns/unattended_campaign_policy_v1.json",
    ]
    for rel in roots:
        assert (REPO_ROOT / rel).exists(), rel


def test_k4l5_blocks_autonomous_policy_sensitive_repairs() -> None:
    with pytest.raises(RepairPolicyError) as excinfo:
        route_repairs_with_policy(
            {
                "repair_candidates": [
                    {
                        "repair_candidate_id": "repair::scheduler-runtime",
                        "safety_class": "auto_fix_allowed",
                        "policy_sensitive_surfaces": ["scheduler_runtime"],
                    }
                ]
            }
        )
    assert excinfo.value.code == "forbidden_autonomous_modification"

    routed = route_repairs_with_policy(
        {
            "repair_candidates": [
                {
                    "repair_candidate_id": "repair::patch-only",
                    "safety_class": "review_required",
                    "policy_sensitive_surfaces": ["host_truth"],
                }
            ]
        }
    )
    assert routed["routed_repairs"][0]["execution_route"] == "patch_proposal"
    assert routed["routed_repairs"][0]["review_state"] == "review_required"


def test_l5m6_revalidation_blocks_fix_claims_without_targeted_checks_or_reruns() -> None:
    with pytest.raises(RevalidationPolicyError) as excinfo:
        plan_post_repair_revalidation(
            {"routed_repairs": [{"repair_candidate_id": "repair::1", "expected_follow_up_validation": ["family_level_rerun"]}]}
        )
    assert excinfo.value.code == "missing_targeted_revalidation"

    with pytest.raises(RevalidationPolicyError) as excinfo2:
        plan_post_repair_revalidation(
            {
                "routed_repairs": [
                    {
                        "repair_candidate_id": "repair::2",
                        "expected_follow_up_validation": ["targeted_revalidation"],
                        "invalidates_prior_evidence": True,
                    }
                ]
            }
        )
    assert excinfo2.value.code == "missing_family_rerun_after_invalidation"


def test_m6n7_stops_campaign_when_repeated_clusters_replace_new_signal() -> None:
    result = evaluate_campaign_stop(
        {
            "budget": {"max_budget_units": 10},
            "actual": {
                "spent_budget_units": 6,
                "repeated_cluster_ratio": 0.9,
                "new_signal_ratio": 0.05,
                "dut_improvement_delta": 0.0,
                "repair_confidence": 0.9,
                "risk_score": 0.2,
                "completed_iterations": 3,
            },
        }
    )
    assert result["must_stop"] is True
    assert result["stop_reason"] == "repeated_same_cluster_non_yield"


def test_n7p8_blocks_broad_robustness_closeout_without_explicit_claim_blockers() -> None:
    with pytest.raises(DutDeltaError) as excinfo:
        compute_dut_health_delta(
            {
                "baseline_health_snapshot": {"open_bug_classes": 4},
                "post_campaign_health_snapshot": {"open_bug_classes": 2},
                "reduced_bug_classes": ["stale_truth"],
                "robustness_claim": "safe_unattended",
            }
        )
    assert excinfo.value.code == "unblocked_broad_robustness_claim"


def test_p8q9_forces_policy_stop_on_contradiction_or_dangerous_low_confidence() -> None:
    contradiction = evaluate_unattended_campaign_state(
        {
            "campaign_phase": "execution",
            "stop_reason": "continue",
            "contradictory_campaign_state": True,
            "routed_repairs": [],
        }
    )
    assert contradiction["operator_visible_state"] == "stopped_by_policy"

    review_blocked = evaluate_unattended_campaign_state(
        {
            "campaign_phase": "execution",
            "stop_reason": "continue",
            "routed_repairs": [{"review_state": "review_required"}],
        }
    )
    assert review_blocked["operator_visible_state"] == "review_blocked"


def test_policy_stack_composes_without_softening_unsafe_repair_story() -> None:
    routed = route_repairs_with_policy(
        {
            "repair_candidates": [
                {
                    "repair_candidate_id": "repair::watchdog-mask",
                    "safety_class": "review_required",
                    "repair_confidence": "medium",
                    "policy_sensitive_surfaces": ["watchdog_truth"],
                    "expected_follow_up_validation": ["targeted_revalidation"],
                    "rollback_requirements": ["revert_if_targeted_revalidation_fails"],
                }
            ]
        }
    )
    plan = plan_post_repair_revalidation(
        {
            "routed_repairs": [
                {
                    **routed["routed_repairs"][0],
                    "expected_follow_up_validation": ["targeted_revalidation"],
                    "invalidates_prior_evidence": True,
                    "broader_rerun_set": ["watchdog_family"],
                }
            ]
        }
    )
    stop = evaluate_campaign_stop(
        {
            "budget": {"max_budget_units": 5},
            "actual": {
                "spent_budget_units": 2,
                "repeated_cluster_ratio": 0.0,
                "new_signal_ratio": 0.5,
                "dut_improvement_delta": 0.3,
                "repair_confidence": 0.6,
                "risk_score": 0.2,
                "completed_iterations": 1,
            },
        }
    )
    unattended = evaluate_unattended_campaign_state(
        {
            "campaign_phase": "execution",
            "stop_reason": stop["stop_reason"],
            "routed_repairs": routed["routed_repairs"],
        }
    )
    delta = compute_dut_health_delta(
        {
            "baseline_health_snapshot": {"open_bug_classes": 3, "critical_open": 1},
            "post_campaign_health_snapshot": {"open_bug_classes": 2, "critical_open": 0},
            "reduced_bug_classes": ["active_busy_masking"],
            "unresolved_bug_classes": ["split_brain_detection_gap"],
            "residual_risk": "still_not_safe_unattended",
            "blocked_claims": ["broad_robustness_unproven"],
            "stop_reason": stop["stop_reason"],
            "execution_coverage": "one_repair_path",
            "revalidation_result": plan["revalidation_plans"][0]["promotion_rule"],
        }
    )
    assert unattended["operator_visible_state"] == "review_blocked"
    assert delta["net_dut_delta"] == "improved"
