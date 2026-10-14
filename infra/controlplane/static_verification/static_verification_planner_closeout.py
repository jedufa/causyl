"""Closeout assessment for the Phase A static verification planner family."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping


class PlannerCloseoutError(ValueError):
    """Raised when Phase A closeout would overclaim or skip key evidence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def build_phase_a_closeout(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    before_after = list(payload.get("before_after_examples") or [])
    if not before_after:
        raise PlannerCloseoutError("before_after_examples_missing", "closeout requires before/after examples")
    if not payload.get("soft_baseline_bakeoff"):
        raise PlannerCloseoutError("soft_baseline_bakeoff_missing", "closeout requires an adversarial baseline bakeoff")
    if not payload.get("ambiguous_boundary_rejection_evidence"):
        raise PlannerCloseoutError("ambiguous_rejection_evidence_missing", "closeout requires ambiguous-boundary rejection evidence")

    delta_examples: List[Dict[str, str]] = []
    for row in before_after:
        delta_examples.append(
            {
                "baseline_gap": _clean_text(row.get("baseline_gap")),
                "planner_improvement": _clean_text(row.get("planner_improvement")),
                "remaining_escape": _clean_text(row.get("remaining_escape")),
            }
        )
    if any(not item["baseline_gap"] or not item["planner_improvement"] or not item["remaining_escape"] for item in delta_examples):
        raise PlannerCloseoutError("before_after_example_incomplete", "before/after examples must stay explicit")

    return {
        "schema_version": "verification_planner_assessment_v1",
        "closeout_decision": "closable",
        "claim_ceiling": "planning_quality_improved_only",
        "planner_value_summary": _clean_text(payload.get("planner_value_summary")),
        "soft_baseline_bakeoff": payload.get("soft_baseline_bakeoff"),
        "ambiguous_boundary_rejection_evidence": payload.get("ambiguous_boundary_rejection_evidence"),
        "residual_blind_spots": list(payload.get("residual_blind_spots") or []),
        "non_claims": [
            "No claim of scheduler execution proof.",
            "No claim of repair-loop effectiveness.",
            "No claim that the DUT is materially improved.",
        ],
        "before_after_examples": delta_examples,
    }
