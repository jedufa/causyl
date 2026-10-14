"""Repair execution routing and autonomy boundaries for Phase B campaigns."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class RepairPolicyError(ValueError):
    """Raised when a repair candidate cannot be routed safely."""

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


FORBIDDEN_SURFACES = {
    "governance_ledger",
    "scheduler_runtime",
    "task_register",
    "secrets_boundary",
    "policy_boundary",
}


def route_repairs_with_policy(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    candidates = list(payload.get("repair_candidates") or [])
    routed: List[Dict[str, Any]] = []
    for row in candidates:
        repair_id = _clean_text(row.get("repair_candidate_id"))
        if not repair_id:
            raise RepairPolicyError("missing_repair_candidate_id", "repair candidate id is required")

        safety_class = _clean_text(row.get("safety_class")) or "review_required"
        surfaces = _normalized_list(
            row.get("policy_sensitive_surfaces")
            or [row.get("intended_repair_surface"), row.get("affected_truth_boundary")]
        )
        touches_forbidden = any(surface in FORBIDDEN_SURFACES for surface in surfaces)
        low_confidence = _clean_text(row.get("repair_confidence")) in {"low", "unknown", ""}

        if touches_forbidden and safety_class == "auto_fix_allowed":
            raise RepairPolicyError(
                "forbidden_autonomous_modification",
                f"{repair_id} touches policy-sensitive surfaces and may not auto-apply",
            )

        if touches_forbidden:
            route = "human_blocked"
            review_state = "human_review_required"
        elif safety_class == "auto_fix_allowed" and not low_confidence:
            route = "deterministic_local_fix"
            review_state = "unattended_allowed"
        elif safety_class in {"review_required", "patch_proposal_only"} or low_confidence:
            route = "patch_proposal"
            review_state = "review_required"
        else:
            route = "codex_escalation"
            review_state = "specialist_review_required"

        routed.append(
            {
                **row,
                "execution_route": route,
                "review_state": review_state,
                "verification_gates": [
                    "targeted_revalidation_required",
                    "family_rerun_required_when_evidence_invalidated",
                ],
                "rollback_requirements": _normalized_list(
                    row.get("rollback_requirements")
                    or ["revert_if_targeted_revalidation_fails", "record_policy_stop_if_rollback_fails"]
                ),
                "merge_boundary": "manual_merge_required" if route != "deterministic_local_fix" else "local_apply_only",
            }
        )

    return {
        "schema_version": "repair_autonomy_policy_report_v1",
        "routed_repairs": routed,
        "maximum_supported_claim": "repair_routing_and_autonomy_policy_only",
    }
