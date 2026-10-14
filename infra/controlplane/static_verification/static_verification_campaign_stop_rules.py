"""Economic stop rules for autonomous verification campaigns."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def evaluate_campaign_stop(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    budgets = dict(payload.get("budget") or {})
    actual = dict(payload.get("actual") or {})
    repeated_cluster_ratio = float(actual.get("repeated_cluster_ratio") or 0.0)
    new_signal_ratio = float(actual.get("new_signal_ratio") or 0.0)
    dut_improvement_delta = float(actual.get("dut_improvement_delta") or 0.0)
    repair_confidence = float(actual.get("repair_confidence") or 0.0)
    risk_score = float(actual.get("risk_score") or 0.0)

    stop_reason = "continue"
    if int(actual.get("spent_budget_units") or 0) > int(budgets.get("max_budget_units") or 0):
        stop_reason = "budget_exhausted"
    elif repeated_cluster_ratio >= 0.8 and new_signal_ratio <= 0.1:
        stop_reason = "repeated_same_cluster_non_yield"
    elif risk_score >= 0.8:
        stop_reason = "excessive_risk"
    elif repair_confidence < 0.35 and int(actual.get("repair_attempts") or 0) >= 2:
        stop_reason = "repair_confidence_too_low"
    elif dut_improvement_delta <= 0.0 and int(actual.get("completed_iterations") or 0) >= 2:
        stop_reason = "dut_not_improving"

    return {
        "schema_version": "campaign_stop_rule_report_v1",
        "stop_reason": stop_reason,
        "must_stop": stop_reason != "continue",
        "economic_band": _clean_text(actual.get("economic_band")) or "unclassified",
        "maximum_supported_claim": "campaign_stop_decision_only",
    }
