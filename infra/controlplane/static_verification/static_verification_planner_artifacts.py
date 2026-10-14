"""Artifact persistence and review-gate helpers for static verification planning."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class PlannerArtifactError(ValueError):
    """Raised when a planner artifact bundle would be unauditable or misleading."""

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


def _require_non_empty(value: Any, *, code: str, field: str) -> str:
    text = _clean_text(value)
    if not text:
        raise PlannerArtifactError(code, f"missing required field: {field}")
    return text


def _normalize_plan_freshness(value: Any) -> Dict[str, Any]:
    payload = value if isinstance(value, Mapping) else {}
    status = _require_non_empty(
        payload.get("status"),
        code="plan_freshness_missing",
        field="plan_freshness.status",
    )
    if status not in {"current", "stale", "superseded"}:
        raise PlannerArtifactError(
            "plan_freshness_status_invalid",
            "plan_freshness.status must be current, stale, or superseded",
        )
    invalidation_triggers = _normalized_list(payload.get("invalidation_triggers") or [])
    if not invalidation_triggers:
        raise PlannerArtifactError(
            "plan_freshness_missing",
            "missing required field: plan_freshness.invalidation_triggers",
        )
    freshness = {
        "status": status,
        "status_message": _require_non_empty(
            payload.get("status_message"),
            code="plan_freshness_missing",
            field="plan_freshness.status_message",
        ),
        "invalidation_triggers": invalidation_triggers,
    }
    stale_reason = _clean_text(payload.get("stale_reason"))
    if status == "current":
        freshness["stale_reason"] = ""
        return freshness
    if not stale_reason:
        raise PlannerArtifactError(
            "plan_freshness_stale_reason_missing",
            "stale or superseded plans must preserve the invalidation reason",
        )
    freshness["stale_reason"] = stale_reason
    return freshness


def _require_traceability_rows(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    for raw in rows or []:
        row = {
            "extracted_fact_id": _clean_text(raw.get("extracted_fact_id")),
            "threat_family_id": _clean_text(raw.get("threat_family_id")),
            "generated_output_id": _clean_text(raw.get("generated_output_id")),
            "claim_blocker": _clean_text(raw.get("claim_blocker")),
        }
        if any(not value for value in row.values()):
            raise PlannerArtifactError(
                "traceability_row_incomplete",
                "every traceability row must link extracted_fact_id, threat_family_id, generated_output_id, and claim_blocker",
            )
        normalized.append(row)
    if not normalized:
        raise PlannerArtifactError("traceability_rows_missing", "planner artifacts require fact-to-output traceability rows")
    return normalized


def build_artifact_bundle(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    run_identity = payload.get("run_identity") or {}
    review_gate = payload.get("review_gate") or {}
    traceability_rows = _require_traceability_rows(payload.get("traceability_rows") or [])

    bundle = {
        "schema_version": "planner_artifact_bundle_v1",
        "run_identity": {
            "repo_commit_sha": _require_non_empty(
                run_identity.get("repo_commit_sha"),
                code="run_identity_missing",
                field="run_identity.repo_commit_sha",
            ),
            "selected_block_selector": _require_non_empty(
                run_identity.get("selected_block_selector"),
                code="run_identity_missing",
                field="run_identity.selected_block_selector",
            ),
            "selected_block_fingerprint": _require_non_empty(
                run_identity.get("selected_block_fingerprint"),
                code="run_identity_missing",
                field="run_identity.selected_block_fingerprint",
            ),
            "planning_mode": _require_non_empty(
                run_identity.get("planning_mode"),
                code="run_identity_missing",
                field="run_identity.planning_mode",
            ),
            "planner_generator_version": _require_non_empty(
                run_identity.get("planner_generator_version"),
                code="run_identity_missing",
                field="run_identity.planner_generator_version",
            ),
            "rerun_fingerprint": _require_non_empty(
                run_identity.get("rerun_fingerprint"),
                code="run_identity_missing",
                field="run_identity.rerun_fingerprint",
            ),
        },
        "artifact_schema_versions": {
            "qualification": _require_non_empty(
                payload.get("artifact_schema_versions", {}).get("qualification"),
                code="artifact_schema_versions_missing",
                field="artifact_schema_versions.qualification",
            ),
            "extraction": _require_non_empty(
                payload.get("artifact_schema_versions", {}).get("extraction"),
                code="artifact_schema_versions_missing",
                field="artifact_schema_versions.extraction",
            ),
            "threat_matrix": _require_non_empty(
                payload.get("artifact_schema_versions", {}).get("threat_matrix"),
                code="artifact_schema_versions_missing",
                field="artifact_schema_versions.threat_matrix",
            ),
            "generation": _require_non_empty(
                payload.get("artifact_schema_versions", {}).get("generation"),
                code="artifact_schema_versions_missing",
                field="artifact_schema_versions.generation",
            ),
        },
        "traceability_rows": traceability_rows,
        "review_gate": {
            "state": _require_non_empty(
                review_gate.get("state"),
                code="review_gate_missing",
                field="review_gate.state",
            ),
            "review_decision": _require_non_empty(
                review_gate.get("review_decision"),
                code="review_gate_missing",
                field="review_gate.review_decision",
            ),
            "planning_only_warning": _require_non_empty(
                review_gate.get("planning_only_warning"),
                code="review_gate_missing",
                field="review_gate.planning_only_warning",
            ),
            "maximum_supported_claim": _require_non_empty(
                review_gate.get("maximum_supported_claim"),
                code="review_gate_missing",
                field="review_gate.maximum_supported_claim",
            ),
        },
        "plan_freshness": _normalize_plan_freshness(payload.get("plan_freshness")),
        "artifact_locations": {
            "bundle_root": _require_non_empty(
                payload.get("artifact_locations", {}).get("bundle_root"),
                code="artifact_locations_missing",
                field="artifact_locations.bundle_root",
            ),
            "current_manifest": _require_non_empty(
                payload.get("artifact_locations", {}).get("current_manifest"),
                code="artifact_locations_missing",
                field="artifact_locations.current_manifest",
            ),
        },
    }
    if bundle["review_gate"]["state"] not in {"draft_generated", "operator_reviewed", "superseded"}:
        raise PlannerArtifactError("review_gate_state_invalid", "review_gate.state must remain bounded and explicit")
    return bundle


def build_operator_entrypoint(raw: Mapping[str, Any]) -> Dict[str, Any]:
    bundle = build_artifact_bundle(raw.get("artifact_bundle") or raw)
    return {
        "schema_version": "planner_entrypoint_view_v1",
        "selected_block": bundle["run_identity"]["selected_block_selector"],
        "planning_mode": bundle["run_identity"]["planning_mode"],
        "rerun_fingerprint": bundle["run_identity"]["rerun_fingerprint"],
        "artifact_location": bundle["artifact_locations"]["current_manifest"],
        "review_gate_state": bundle["review_gate"]["state"],
        "claim_ceiling_warning": bundle["review_gate"]["planning_only_warning"],
        "maximum_supported_claim": bundle["review_gate"]["maximum_supported_claim"],
        "plan_freshness_status": bundle["plan_freshness"]["status"],
        "stale_plan_message": bundle["plan_freshness"]["status_message"],
    }
