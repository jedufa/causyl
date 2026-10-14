"""Unattended safety and escalation boundaries for autonomous campaigns."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_list(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in items:
            items.append(text)
    return items


def evaluate_unattended_campaign_state(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    routed_repairs = list(payload.get("routed_repairs") or [])
    stop_reason = _clean_text(payload.get("stop_reason"))
    contradictory_state = bool(payload.get("contradictory_campaign_state"))
    dangerous_low_confidence = bool(payload.get("dangerous_low_confidence_finding"))

    review_blocked = any(_clean_text(row.get("review_state")) != "unattended_allowed" for row in routed_repairs)
    hard_stop = contradictory_state or dangerous_low_confidence or stop_reason in {"excessive_risk", "policy_stop"}

    if hard_stop:
        operator_state = "stopped_by_policy"
    elif review_blocked:
        operator_state = "review_blocked"
    elif stop_reason and stop_reason != "continue":
        operator_state = "stopped_by_economics"
    elif _clean_text(payload.get("campaign_phase")) == "planning_complete":
        operator_state = "planning_complete"
    else:
        operator_state = "campaign_running"

    return {
        "schema_version": "unattended_campaign_policy_report_v1",
        "operator_visible_state": operator_state,
        "review_required_transitions": _normalized_list(payload.get("review_required_transitions"))
        or ["patch_proposal_apply", "policy_sensitive_repair", "contradictory_campaign_state"],
        "hard_stop_boundaries": _normalized_list(payload.get("hard_stop_boundaries"))
        or ["policy_sensitive_autonomous_modify", "dangerous_low_confidence_finding", "contradictory_campaign_state"],
        "escalation_routes": _normalized_list(payload.get("escalation_routes"))
        or ["human_review_queue", "specialist_review_queue", "stop_the_plane_and_report"],
        "maximum_supported_claim": "unattended_policy_only",
    }
