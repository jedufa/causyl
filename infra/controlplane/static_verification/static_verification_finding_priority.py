"""Severity, reachability, and repair-priority policy for normalized findings."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def prioritize_findings(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    findings = list(payload.get("normalized_defect_candidates") or [])
    prioritized: List[Dict[str, Any]] = []
    for row in findings:
        severity = _clean_text(row.get("severity_hint"))
        reachability = _clean_text(row.get("production_reachability_hint"))
        score = 0
        if severity == "critical":
            score += 50
        elif severity == "high":
            score += 35
        elif severity == "medium":
            score += 20
        if reachability == "reachable_in_normal_operation":
            score += 25
        elif reachability == "reachable_under_degraded_or_faulted_operation":
            score += 18
        if "silent_paralysis" in row.get("cluster_id", ""):
            score += 20
        if "unsafe_action" in row.get("cluster_id", ""):
            score += 20
        score += min(int(row.get("evidence_count") or 0), 3) * 3
        unattended_blocked = score >= 65
        prioritized.append(
            {
                **row,
                "priority_score": score,
                "priority_class": "repair_now" if score >= 65 else "log_only" if score < 30 else "repair_soon",
                "unattended_continuation_blocked": unattended_blocked,
            }
        )
    prioritized.sort(key=lambda row: (-int(row["priority_score"]), row["cluster_id"]))
    return {
        "schema_version": "finding_priority_report_v1",
        "prioritized_findings": prioritized,
        "maximum_supported_claim": "finding_priority_defined_only",
    }
