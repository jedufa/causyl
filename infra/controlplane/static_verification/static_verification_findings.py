"""Normalize raw campaign findings into bounded defect candidates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Tuple


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_list(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in items:
            items.append(text)
    return items


def _dedupe_key(row: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (
        _clean_text(row.get("truth_boundary")),
        _clean_text(row.get("symptom_class")),
        _clean_text(row.get("operator_claim_under_attack")),
    )


def normalize_findings(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    findings = list(payload.get("raw_findings") or [])
    clusters: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in findings:
        key = _dedupe_key(row)
        if key not in clusters:
            clusters[key] = {
                "cluster_id": "::".join(part or "unknown" for part in key),
                "dedupe_key": list(key),
                "raw_finding_ids": [],
                "evidence_count": 0,
                "confidence": 0.0,
                "severity_hint": _clean_text(row.get("severity_hint")),
                "production_reachability_hint": _clean_text(row.get("production_reachability_hint")),
                "affected_truths": _normalized_list(row.get("affected_truths") or []),
                "repair_worthy": False,
                "stale_after_rules": ["repo_commit_sha_changed", "campaign_superseded"],
            }
        cluster = clusters[key]
        cluster["raw_finding_ids"].append(_clean_text(row.get("finding_id")))
        cluster["evidence_count"] += 1
        if _clean_text(row.get("repair_signal")) == "repair_worthy":
            cluster["repair_worthy"] = True
        cluster["confidence"] = round(min(1.0, 0.4 + 0.2 * cluster["evidence_count"]), 2)
    normalized = sorted(clusters.values(), key=lambda row: (-int(row["evidence_count"]), row["cluster_id"]))
    return {
        "schema_version": "normalized_finding_report_v1",
        "normalized_defect_candidates": normalized,
        "maximum_supported_claim": "finding_clusters_normalized_only",
    }
