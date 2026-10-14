"""Compile planner-generated scenarios into runnable campaign jobs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class CampaignJobCompileError(ValueError):
    """Raised when a generated scenario cannot be promoted into runnable work honestly."""

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


def compile_runnable_jobs(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    scenarios = list(payload.get("scenarios") or [])
    if not scenarios:
        raise CampaignJobCompileError("scenarios_missing", "dense test pack scenarios are required")
    compiled: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    for scenario in scenarios:
        scenario_id = _clean_text(scenario.get("scenario_id"))
        execution_class = _clean_text(scenario.get("execution_class"))
        summary = _clean_text(scenario.get("scenario_summary"))
        if not scenario_id or not execution_class or not summary:
            raise CampaignJobCompileError("scenario_fields_missing", "scenario_id, execution_class, and scenario_summary are required")
        if execution_class in {"planning_only", "rejected", "purely_theoretical"}:
            rejected.append(
                {
                    "scenario_id": scenario_id,
                    "reason": "theatrically harsh or non-runnable scenario rejected before campaign compilation",
                }
            )
            continue
        if execution_class not in {"runnable_now", "runnable_after_bounded_setup", "runnable_after_support_work"}:
            raise CampaignJobCompileError("execution_class_invalid", f"{scenario_id} has invalid execution_class {execution_class}")
        compiled.append(
            {
                "job_id": f"verify::{scenario_id}",
                "scenario_id": scenario_id,
                "job_kind": "verifier" if "verify" in scenario_id or "scenario" in scenario_id else "builder",
                "execution_substrate": "builder_verifier",
                "setup_requirements": _normalized_list(scenario.get("setup_requirements") or ["bounded_fixture_reset"]),
                "reset_requirements": _normalized_list(scenario.get("reset_requirements") or ["reset_after_run"]),
                "expected_evidence": _normalized_list(scenario.get("required_evidence") or []),
                "retry_policy": {
                    "retry_class": "bounded_transient_only",
                    "max_retries": 1,
                },
                "invalidation_conditions": _normalized_list(
                    scenario.get("invalidation_conditions")
                    or ["repo_commit_sha_changed", "planner_rerun_fingerprint_changed"]
                ),
                "runtime_class": execution_class,
                "cost_band": "medium" if execution_class == "runnable_after_bounded_setup" else "low",
                "parallelization_allowance": "parallel_safe",
                "flake_sensitivity": "medium",
                "repair_triggerable": True,
            }
        )
    return {
        "schema_version": "runnable_scenario_compilation_v1",
        "compiled_jobs": compiled,
        "rejected_scenarios": rejected,
        "maximum_supported_claim": "runnable_job_templates_defined_only",
    }
