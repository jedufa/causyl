"""End-to-end Phase B anchor proving one generated scenario closes honestly."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping

from infra.controlplane.static_verification.static_verification_campaign_dag import build_campaign_dag
from infra.controlplane.static_verification.static_verification_campaign_jobs import compile_runnable_jobs
from infra.controlplane.static_verification.static_verification_campaign_stop_rules import evaluate_campaign_stop
from infra.controlplane.static_verification.static_verification_dut_delta import compute_dut_health_delta
from infra.controlplane.static_verification.static_verification_finding_priority import prioritize_findings
from infra.controlplane.static_verification.static_verification_findings import normalize_findings
from infra.controlplane.static_verification.static_verification_repair_policy import route_repairs_with_policy
from infra.controlplane.static_verification.static_verification_repairs import synthesize_repairs
from infra.controlplane.static_verification.static_verification_revalidation import plan_post_repair_revalidation
from infra.controlplane.static_verification.static_verification_unattended_policy import evaluate_unattended_campaign_state


class PhaseBAnchorError(ValueError):
    """Raised when the end-to-end Phase B anchor cannot close honestly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def run_phase_b_anchor(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    compiled = compile_runnable_jobs({"scenarios": payload.get("scenarios") or []})
    if not compiled["compiled_jobs"]:
        raise PhaseBAnchorError("no_runnable_job_compiled", "anchor produced no runnable work")

    dag = build_campaign_dag({"compiled_jobs": compiled["compiled_jobs"]})
    normalized = normalize_findings({"raw_findings": payload.get("raw_findings") or []})
    if not normalized["normalized_defect_candidates"]:
        raise PhaseBAnchorError("no_normalized_finding", "anchor produced no normalized defect candidate")

    prioritized = prioritize_findings({"normalized_defect_candidates": normalized["normalized_defect_candidates"]})
    repairs = synthesize_repairs({"prioritized_findings": prioritized["prioritized_findings"]})
    if not repairs["repair_candidates"]:
        raise PhaseBAnchorError("no_repair_candidate", "anchor produced no bounded repair candidate")

    routed = route_repairs_with_policy({"repair_candidates": repairs["repair_candidates"]})
    revalidation_input = []
    for row in routed["routed_repairs"]:
        revalidation_input.append(
            {
                **row,
                "expected_follow_up_validation": ["targeted_revalidation"],
                "invalidates_prior_evidence": True,
                "broader_rerun_set": ["anchor_family"],
            }
        )
    revalidation = plan_post_repair_revalidation({"routed_repairs": revalidation_input})

    stop = evaluate_campaign_stop(
        {
            "budget": payload.get("budget") or {"max_budget_units": 4},
            "actual": payload.get("actual") or {},
        }
    )
    unattended = evaluate_unattended_campaign_state(
        {
            "campaign_phase": "execution",
            "stop_reason": stop["stop_reason"],
            "routed_repairs": routed["routed_repairs"],
            "contradictory_campaign_state": bool(payload.get("contradictory_campaign_state")),
            "dangerous_low_confidence_finding": bool(payload.get("dangerous_low_confidence_finding")),
        }
    )
    delta = compute_dut_health_delta(
        {
            "baseline_health_snapshot": payload.get("baseline_health_snapshot") or {},
            "post_campaign_health_snapshot": payload.get("post_campaign_health_snapshot") or {},
            "reduced_bug_classes": payload.get("reduced_bug_classes") or [],
            "unresolved_bug_classes": payload.get("unresolved_bug_classes") or [],
            "residual_risk": payload.get("residual_risk") or "",
            "blocked_claims": payload.get("blocked_claims") or [],
            "stop_reason": stop["stop_reason"],
            "execution_coverage": "one_generated_scenario",
            "finding_yield": f"{len(normalized['normalized_defect_candidates'])}_normalized_clusters",
            "repair_yield": f"{len(repairs['repair_candidates'])}_bounded_repairs",
            "revalidation_result": revalidation["revalidation_plans"][0]["promotion_rule"],
        }
    )

    return {
        "schema_version": "phase_b_anchor_report_v1",
        "compiled_jobs": compiled["compiled_jobs"],
        "campaign_dag": dag,
        "normalized_defect_candidates": normalized["normalized_defect_candidates"],
        "prioritized_findings": prioritized["prioritized_findings"],
        "repair_candidates": repairs["repair_candidates"],
        "routed_repairs": routed["routed_repairs"],
        "revalidation_plans": revalidation["revalidation_plans"],
        "operator_state": unattended["operator_visible_state"],
        "dut_health_delta": delta,
        "stop_reason": stop["stop_reason"],
        "maximum_supported_claim": "one_end_to_end_phase_b_anchor_only",
    }
