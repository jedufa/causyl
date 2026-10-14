"""Deterministic output generation for static verification planner threat families."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class PlannerGenerationError(ValueError):
    """Raised when output generation would create an underspecified plan artifact."""

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


def _scenario_id(family_id: str, index: int) -> str:
    return f"{family_id}::scenario_{index:02d}"


def select_output_mode(threat_model: Mapping[str, Any]) -> Dict[str, Any]:
    families = list(threat_model.get("scenario_families") or [])
    if not families:
        raise PlannerGenerationError("scenario_families_missing", "threat model must contain scenario families")
    family_count = len(families)
    threat_classes = {str(family.get("threat_class") or "").strip() for family in families if family.get("threat_class")}
    max_examples = max(len(_normalized_list(family.get("scenario_examples") or [])) for family in families)
    if family_count >= 3 or len(threat_classes) >= 3:
        return {
            "mode": "child_pis_family",
            "reason": "multiple distinct risk classes require auditable child slices instead of one dense pack",
        }
    if family_count <= 2 and max_examples >= 3:
        return {
            "mode": "dense_test_pack",
            "reason": "bounded family count with concrete scenario density can stay in one serious pack",
        }
    return {
        "mode": "child_pis_family",
        "reason": "fallback fail-closed mode keeps risk classes bounded rather than flattening mixed scenarios",
    }


def _build_dense_test_pack(families: List[Mapping[str, Any]], mode_reason: str) -> Dict[str, Any]:
    scenarios: List[Dict[str, Any]] = []
    rejected = [
        {
            "alternative": "child_pis_family",
            "reason_rejected": "family count and scenario density stay bounded enough for one dense pack",
        }
    ]
    for family in families:
        examples = _normalized_list(family.get("scenario_examples") or [])
        if not examples:
            raise PlannerGenerationError(
                "scenario_examples_missing",
                f"{family.get('scenario_family_id', '<unknown>')} needs scenario examples for dense pack mode",
            )
        for index, example in enumerate(examples, start=1):
            scenarios.append(
                {
                    "scenario_id": _scenario_id(_clean_text(family.get("scenario_family_id")), index),
                    "source_scenario_family_id": _clean_text(family.get("scenario_family_id")),
                    "execution_class": _clean_text((family.get("execution_readiness") or {}).get("readiness_class")),
                    "required_evidence": [
                        "negative_control_observation",
                        "disqualifying_incident_outcome",
                        "operator_claim_under_attack_observation",
                    ],
                    "negative_control": _clean_text(family.get("negative_control_plan")),
                    "disqualifying_incident": _normalized_list(family.get("disqualifying_incidents") or [])[0],
                    "claim_ceiling": "planning_output_only_until_executed",
                    "anti_gentle_bans": [
                        "no_missing_negative_control",
                        "no_missing_disqualifying_incident",
                        "no_missing_operator_claim_under_attack",
                    ],
                    "scenario_summary": example,
                }
            )
    return {
        "schema_version": "dense_test_pack_output_v1",
        "output_mode": "dense_test_pack",
        "mode_selection_reason": mode_reason,
        "maximum_supported_claim": "dense_pack_defined_for_planning_only",
        "scenarios": scenarios,
        "rejected_alternatives": rejected,
        "what_still_escapes_even_if_all_generated_work_passes": [
            "runtime_robustness_under_live_campaign_load",
            "scheduler_execution_readiness",
            "dut_health_improved",
        ],
    }


def _build_child_pis_family(families: List[Mapping[str, Any]], mode_reason: str) -> Dict[str, Any]:
    children: List[Dict[str, Any]] = []
    rejected = [
        {
            "alternative": "dense_test_pack",
            "reason_rejected": "multiple distinct risk classes would flatten too much proof into one pack",
        }
    ]
    for family in families:
        scenarios = _normalized_list(family.get("scenario_examples") or [])
        if not scenarios:
            raise PlannerGenerationError(
                "scenario_examples_missing",
                f"{family.get('scenario_family_id', '<unknown>')} needs scenario examples for child-PIS mode",
            )
        children.append(
            {
                "child_key": _clean_text(family.get("scenario_family_id")),
                "title": f"{_clean_text(family.get('threat_class')).replace('_', ' ').title()} child",
                "dominant_failure_story": _clean_text(
                    (family.get("bug_yield_hypothesis") or {}).get("unknown_bug_class_sought")
                ),
                "bounded_proof_target": _clean_text(family.get("operator_claim_under_attack")),
                "explicit_split_rationale": (
                    "Keep one dominant failure story per child so the family stays auditable and non-theatrical."
                ),
                "max_scenario_breadth": len(scenarios),
                "required_evidence": [
                    "negative_control_result",
                    "disqualifying_incident_check",
                    "operator_claim_under_attack_observation",
                ],
                "claim_blockers": [
                    "planning_output_only_until_child_runs",
                    "no_runtime_or_dut_improvement_claim",
                ],
                "anti_gentle_bans": [
                    "no_multi_story_child_scope",
                    "no_missing_negative_control",
                    "no_missing_disqualifying_incident",
                ],
            }
        )
    return {
        "schema_version": "pis_family_output_v1",
        "output_mode": "child_pis_family",
        "mode_selection_reason": mode_reason,
        "maximum_supported_claim": "child_pis_family_defined_for_planning_only",
        "child_pis_family": children,
        "rejected_alternatives": rejected,
        "what_still_escapes_even_if_all_generated_work_passes": [
            "full_execution_campaign_behavior",
            "repair_loop_effectiveness",
            "dut_health_improved",
        ],
    }


def generate_planner_output(threat_model: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(threat_model or {}))
    if _clean_text(payload.get("schema_version")) != "static_verification_threat_matrix_v1":
        raise PlannerGenerationError(
            "wrong_threat_model_schema",
            "generate_planner_output requires static_verification_threat_matrix_v1 input",
        )
    families = list(payload.get("scenario_families") or [])
    if not families:
        raise PlannerGenerationError("scenario_families_missing", "threat model must contain scenario families")
    mode = select_output_mode(payload)
    if mode["mode"] == "dense_test_pack":
        return _build_dense_test_pack(families, mode["reason"])
    return _build_child_pis_family(families, mode["reason"])
