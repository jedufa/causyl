"""DUT health delta and residual-risk closeout for Phase B campaigns."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class DutDeltaError(ValueError):
    """Raised when a DUT closeout would overclaim or omit required evidence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_list(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in items:
            items.append(text)
    return items


def compute_dut_health_delta(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    baseline = dict(payload.get("baseline_health_snapshot") or {})
    post = dict(payload.get("post_campaign_health_snapshot") or {})
    if not baseline or not post:
        raise DutDeltaError("missing_health_snapshot", "baseline and post-campaign snapshots are required")

    blocked_claims = _normalized_list(payload.get("blocked_claims"))
    robustness_claim = _clean_text(payload.get("robustness_claim"))
    if robustness_claim and not blocked_claims:
        raise DutDeltaError(
            "unblocked_broad_robustness_claim",
            "closeout may not imply unattended safety or broad robustness without explicit blocked claims or stronger proof",
        )

    reduced = _normalized_list(payload.get("reduced_bug_classes"))
    unresolved = _normalized_list(payload.get("unresolved_bug_classes"))

    return {
        "schema_version": "dut_health_delta_report_v1",
        "baseline_health_snapshot": baseline,
        "post_campaign_health_snapshot": post,
        "reduced_bug_classes": reduced,
        "unresolved_bug_classes": unresolved,
        "residual_risk": _clean_text(payload.get("residual_risk")) or "not_assessed",
        "confidence": _clean_text(payload.get("confidence")) or "bounded_phase_b_only",
        "planner_contribution": _clean_text(payload.get("planner_contribution")) or "phase_a_plan_used",
        "execution_coverage": _clean_text(payload.get("execution_coverage")) or "partial",
        "finding_yield": _clean_text(payload.get("finding_yield")) or "not_reported",
        "repair_yield": _clean_text(payload.get("repair_yield")) or "not_reported",
        "revalidation_result": _clean_text(payload.get("revalidation_result")) or "not_reported",
        "net_dut_delta": "improved" if reduced else "not_proven",
        "stop_reason": _clean_text(payload.get("stop_reason")) or "not_reported",
        "blocked_claims": blocked_claims,
        "remaining_proof_gaps": _normalized_list(payload.get("remaining_proof_gaps")),
        "maximum_supported_claim": "bounded_dut_delta_only",
    }
