"""Mandatory post-repair revalidation planning for Phase B campaigns."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class RevalidationPolicyError(ValueError):
    """Raised when a repair cannot be revalidated honestly."""

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


def plan_post_repair_revalidation(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    repairs = list(payload.get("routed_repairs") or [])
    plans: List[Dict[str, Any]] = []
    for row in repairs:
        repair_id = _clean_text(row.get("repair_candidate_id"))
        targeted = _normalized_list(row.get("expected_follow_up_validation"))
        if "targeted_revalidation" not in targeted:
            raise RevalidationPolicyError(
                "missing_targeted_revalidation",
                f"{repair_id or '<unknown>'} cannot close without targeted revalidation",
            )

        invalidated = bool(row.get("invalidates_prior_evidence"))
        broader = _normalized_list(row.get("broader_rerun_set"))
        if invalidated and not broader:
            raise RevalidationPolicyError(
                "missing_family_rerun_after_invalidation",
                f"{repair_id or '<unknown>'} invalidates prior evidence but provides no rerun set",
            )

        drift_tokens = _normalized_list(row.get("drift_invalidation_conditions"))
        if not drift_tokens:
            drift_tokens = ["repair_surface_commit_drift", "stale_pre_repair_finding_bundle"]

        plans.append(
            {
                "repair_candidate_id": repair_id,
                "targeted_checks": targeted,
                "broader_rerun_set": broader,
                "promotion_rule": "accepted_only_after_targeted_and_broader_revalidation_pass",
                "drift_invalidation_conditions": drift_tokens,
                "tentative_until": "targeted_checks_and_required_reruns_complete",
            }
        )

    return {
        "schema_version": "post_repair_revalidation_report_v1",
        "revalidation_plans": plans,
        "maximum_supported_claim": "post_repair_revalidation_planned_only",
    }
