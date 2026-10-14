"""Bounded repair-candidate synthesis from prioritized findings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class RepairSynthesisError(ValueError):
    """Raised when repair synthesis would create overbroad or incoherent work."""

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


def synthesize_repairs(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    prioritized = list(payload.get("prioritized_findings") or [])
    repairs: List[Dict[str, Any]] = []
    for row in prioritized:
        if _clean_text(row.get("priority_class")) == "log_only":
            continue
        truths = _normalized_list(row.get("affected_truths") or [])
        if len(truths) > 2:
            raise RepairSynthesisError(
                "overbroad_repair_bundle_rejected",
                f"{row.get('cluster_id', '<unknown>')} mixes too many truth boundaries for one bounded repair",
            )
        repairs.append(
            {
                "repair_candidate_id": f"repair::{_clean_text(row.get('cluster_id'))}",
                "source_finding_clusters": [_clean_text(row.get("cluster_id"))],
                "affected_truth_boundary": truths[0] if truths else "unknown_truth_boundary",
                "intended_repair_surface": truths[0] if truths else "unknown_surface",
                "suspected_root_cause_class": _clean_text(row.get("cluster_id")),
                "bounded_proof_target": _clean_text(row.get("cluster_id")),
                "expected_follow_up_validation": ["targeted_revalidation", "family_level_rerun"],
                "rollback_requirements": ["revert_patch_if_targeted_revalidation_fails"],
            }
        )
    return {
        "schema_version": "repair_candidate_report_v1",
        "repair_candidates": repairs,
        "maximum_supported_claim": "repair_candidates_synthesized_only",
    }
