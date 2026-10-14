"""Operator-facing Phase A workflow projection for static verification planning."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping

SUPPORTED_PLANNING_ONLY_CLAIMS = frozenset({"planning_quality_improved_only"})


class PlannerWorkflowError(ValueError):
    """Raised when the operator workflow would hide key planning truth."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _require(value: Any, *, code: str, field: str) -> str:
    text = _clean_text(value)
    if not text:
        raise PlannerWorkflowError(code, f"missing required field: {field}")
    return text


def _normalized_list(values: Iterable[Any], *, code: str, field: str) -> List[str]:
    items: List[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in items:
            items.append(text)
    if not items:
        raise PlannerWorkflowError(code, f"missing required field: {field}")
    return items


def _normalize_claim_ceiling(value: Any) -> str:
    ceiling = _require(value, code="workflow_field_missing", field="maximum_supported_claim")
    if ceiling not in SUPPORTED_PLANNING_ONLY_CLAIMS:
        raise PlannerWorkflowError(
            "maximum_supported_claim_invalid",
            "maximum_supported_claim must stay within planning-only ceilings",
        )
    return ceiling


def _normalize_plan_freshness(value: Any) -> Dict[str, Any]:
    payload = value if isinstance(value, Mapping) else {}
    status = _require(payload.get("status"), code="workflow_field_missing", field="plan_freshness.status")
    if status not in {"current", "stale", "superseded"}:
        raise PlannerWorkflowError(
            "plan_freshness_status_invalid",
            "plan_freshness.status must be current, stale, or superseded",
        )
    freshness = {
        "status": status,
        "status_message": _require(
            payload.get("status_message"),
            code="workflow_field_missing",
            field="plan_freshness.status_message",
        ),
        "invalidation_triggers": _normalized_list(
            payload.get("invalidation_triggers"),
            code="workflow_field_missing",
            field="plan_freshness.invalidation_triggers",
        ),
    }
    stale_reason = _clean_text(payload.get("stale_reason"))
    if status == "current":
        freshness["stale_reason"] = ""
        return freshness
    if not stale_reason:
        raise PlannerWorkflowError(
            "plan_freshness_stale_reason_missing",
            "stale or superseded plans must state the invalidation reason",
        )
    freshness["stale_reason"] = stale_reason
    return freshness


def build_phase_a_workflow_view(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    selector = _require(payload.get("block_selector"), code="workflow_field_missing", field="block_selector")
    mode = _require(payload.get("mode_selector"), code="workflow_field_missing", field="mode_selector")
    qualification = _require(
        payload.get("qualification_decision"),
        code="workflow_field_missing",
        field="qualification_decision",
    )
    if qualification not in {"accepted", "expanded", "rejected"}:
        raise PlannerWorkflowError("qualification_decision_invalid", "qualification_decision must be accepted, expanded, or rejected")
    phases = payload.get("visible_phases") or []
    if phases != [
        "qualify_block",
        "extract_truth_seams",
        "build_threat_matrix",
        "generate_plan",
        "review_gate",
        "publish_artifacts",
    ]:
        raise PlannerWorkflowError("visible_phases_invalid", "workflow must expose the canonical six planning phases in order")
    return {
        "schema_version": "planner_frontend_workflow_view_v1",
        "block_selector": selector,
        "mode_selector": mode,
        "optional_profile_controls": payload.get("optional_profile_controls") or [],
        "maximum_supported_claim": _normalize_claim_ceiling(payload.get("maximum_supported_claim")),
        "claim_ceiling_warning": _require(
            payload.get("claim_ceiling_warning"),
            code="workflow_field_missing",
            field="claim_ceiling_warning",
        ),
        "planning_only_warning": _require(
            payload.get("planning_only_warning"),
            code="workflow_field_missing",
            field="planning_only_warning",
        ),
        "qualification_decision": qualification,
        "run_id": _require(payload.get("run_id"), code="workflow_field_missing", field="run_id"),
        "rerun_fingerprint": _require(
            payload.get("rerun_fingerprint"),
            code="workflow_field_missing",
            field="rerun_fingerprint",
        ),
        "artifact_locations": payload.get("artifact_locations") or {},
        "plan_freshness": _normalize_plan_freshness(payload.get("plan_freshness")),
        "stale_plan_invalidation": _require(
            payload.get("stale_plan_invalidation"),
            code="workflow_field_missing",
            field="stale_plan_invalidation",
        ),
        "visible_phases": phases,
    }
