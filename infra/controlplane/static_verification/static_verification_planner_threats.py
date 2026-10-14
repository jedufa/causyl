"""Deterministic threat-matrix and scenario-family compilation for the static planner."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Sequence


class ThreatModelError(ValueError):
    """Raised when scenario-family compilation would soften required harshness rules."""

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


def _get_path(payload: Mapping[str, Any], dotted: str) -> Any:
    current: Any = payload
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _value_has_signal(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_value_has_signal(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return bool(_normalized_list(value))
    return bool(_clean_text(value))


def _require_signal(payload: Mapping[str, Any], dotted: str, *, code: str) -> None:
    if not _value_has_signal(_get_path(payload, dotted)):
        raise ThreatModelError(code, f"missing required field: {dotted}")


def _ensure_list_signal(payload: Mapping[str, Any], dotted: str, *, code: str) -> List[str]:
    values = _normalized_list(_get_path(payload, dotted) or [])
    if not values:
        raise ThreatModelError(code, f"missing required list field: {dotted}")
    return values


def _priority_score(candidate: Mapping[str, Any]) -> int:
    score = 0
    reachability = _clean_text(_get_path(candidate, "production_reachability.classification"))
    readiness = _clean_text(_get_path(candidate, "execution_readiness.readiness_class"))
    if reachability == "reachable_in_normal_operation":
        score += 30
    elif reachability == "reachable_under_degraded_or_faulted_operation":
        score += 24
    elif reachability == "observation_only_impossible_but_useful":
        score += 12
    records = len(_normalized_list(candidate.get("independent_records_that_can_disagree") or []))
    score += min(records, 4) * 10
    if readiness == "runnable_now":
        score += 20
    elif readiness == "runnable_after_bounded_setup":
        score += 14
    elif readiness == "observation_only_honesty_check":
        score += 6
    if _clean_text(candidate.get("operator_claim_under_attack")):
        score += 10
    if _value_has_signal(candidate.get("repetition_overlap_accumulation_plan")):
        score += 8
    if _value_has_signal(candidate.get("negative_control_plan")):
        score += 4
    return score


def _priority_band(score: int) -> str:
    if score >= 78:
        return "critical"
    if score >= 58:
        return "high"
    if score >= 36:
        return "medium"
    return "low"


def _normalize_candidate(raw: Mapping[str, Any]) -> Dict[str, Any]:
    candidate = deepcopy(dict(raw or {}))
    for field in (
        "scenario_family_id",
        "threat_class",
        "operator_claim_under_attack",
        "negative_control_plan",
        "contamination_or_interference_notes",
        "silent_paralysis_path",
        "unsafe_action_path",
    ):
        if field in candidate:
            candidate[field] = _clean_text(candidate.get(field))
    for field in (
        "independent_records_that_can_disagree",
        "disqualifying_incidents",
        "scenario_examples",
    ):
        if field in candidate:
            candidate[field] = _normalized_list(candidate.get(field) or [])
    return candidate


def compile_threat_matrix(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    extraction = payload.get("extraction_output") or {}
    if _clean_text(extraction.get("schema_version")) != "complex_block_extraction_output_v1":
        raise ThreatModelError(
            "extraction_output_missing_or_wrong_schema",
            "compile_threat_matrix requires complex_block_extraction_output_v1 input",
        )

    candidates = payload.get("scenario_family_candidates") or []
    if not candidates:
        raise ThreatModelError(
            "scenario_family_candidates_missing",
            "at least one scenario family candidate is required",
        )

    normalized_families: List[Dict[str, Any]] = []
    matrix_rows: List[Dict[str, Any]] = []
    for raw_candidate in candidates:
        candidate = _normalize_candidate(raw_candidate)
        for field in (
            "scenario_family_id",
            "threat_class",
            "operator_claim_under_attack",
            "negative_control_plan",
            "contamination_or_interference_notes",
        ):
            _require_signal(candidate, field, code="required_family_field_missing")
        _ensure_list_signal(
            candidate,
            "independent_records_that_can_disagree",
            code="independent_records_missing",
        )
        _ensure_list_signal(candidate, "disqualifying_incidents", code="disqualifying_incidents_missing")
        _ensure_list_signal(candidate, "scenario_examples", code="scenario_examples_missing")

        for field in (
            "loophole_closure.lazy_pass_path",
            "loophole_closure.closure_mechanism",
            "loophole_closure.why_the_closure_forces_real_signal",
            "bug_yield_hypothesis.unknown_bug_class_sought",
            "bug_yield_hypothesis.why_existing_checks_miss_it",
            "bug_yield_hypothesis.evidence_of_failure_if_present",
            "production_reachability.classification",
            "production_reachability.why_this_classification_is_honest",
            "execution_readiness.readiness_class",
            "execution_readiness.why_not_just_theoretical",
        ):
            _require_signal(candidate, field, code="required_family_field_missing")

        stateful = bool(candidate.get("stateful_or_reliability_sensitive"))
        if stateful and not _value_has_signal(candidate.get("repetition_overlap_accumulation_plan")):
            if not _clean_text(candidate.get("why_repetition_overlap_accumulation_not_applicable")):
                raise ThreatModelError(
                    "missing_repetition_overlap_accumulation_plan",
                    f"{candidate.get('scenario_family_id', '<unknown>')} needs repetition/overlap/accumulation planning",
                )

        if bool(candidate.get("silent_paralysis_relevant")) and not _clean_text(candidate.get("silent_paralysis_path")):
            raise ThreatModelError(
                "missing_silent_paralysis_path",
                f"{candidate.get('scenario_family_id', '<unknown>')} marked silent-paralysis relevant without a path",
            )
        if bool(candidate.get("unsafe_action_relevant")) and not _clean_text(candidate.get("unsafe_action_path")):
            raise ThreatModelError(
                "missing_unsafe_action_path",
                f"{candidate.get('scenario_family_id', '<unknown>')} marked unsafe-action relevant without a path",
            )

        score = _priority_score(candidate)
        band = _priority_band(score)
        normalized = {
            "scenario_family_id": candidate["scenario_family_id"],
            "threat_class": candidate["threat_class"],
            "operator_claim_under_attack": candidate["operator_claim_under_attack"],
            "independent_records_that_can_disagree": candidate["independent_records_that_can_disagree"],
            "negative_control_plan": candidate["negative_control_plan"],
            "disqualifying_incidents": candidate["disqualifying_incidents"],
            "loophole_closure": candidate["loophole_closure"],
            "bug_yield_hypothesis": candidate["bug_yield_hypothesis"],
            "production_reachability": candidate["production_reachability"],
            "contamination_or_interference_notes": candidate["contamination_or_interference_notes"],
            "repetition_overlap_accumulation_plan": candidate.get("repetition_overlap_accumulation_plan") or "",
            "execution_readiness": candidate["execution_readiness"],
            "scenario_examples": candidate["scenario_examples"],
            "silent_paralysis_path": candidate.get("silent_paralysis_path") or "",
            "unsafe_action_path": candidate.get("unsafe_action_path") or "",
            "priority_score": score,
            "priority_band": band,
        }
        normalized_families.append(normalized)
        matrix_rows.append(
            {
                "scenario_family_id": normalized["scenario_family_id"],
                "threat_class": normalized["threat_class"],
                "priority_band": band,
                "priority_score": score,
                "lazy_pass_path": _clean_text(_get_path(candidate, "loophole_closure.lazy_pass_path")),
                "closure_mechanism": _clean_text(_get_path(candidate, "loophole_closure.closure_mechanism")),
                "records_under_pressure": normalized["independent_records_that_can_disagree"],
                "operator_claim_under_attack": normalized["operator_claim_under_attack"],
                "production_reachability_classification": _clean_text(
                    _get_path(candidate, "production_reachability.classification")
                ),
                "execution_readiness_class": _clean_text(_get_path(candidate, "execution_readiness.readiness_class")),
            }
        )

    normalized_families.sort(key=lambda row: (-int(row["priority_score"]), row["scenario_family_id"]))
    matrix_rows.sort(key=lambda row: (-int(row["priority_score"]), row["scenario_family_id"]))

    return {
        "schema_version": "static_verification_threat_matrix_v1",
        "planner_family": "static_verification_planner_v1",
        "maximum_supported_claim": "threat_families_defined_for_planning_only",
        "qualified_boundary_summary": extraction.get("bounded_block_summary") or {},
        "threat_matrix_rows": matrix_rows,
        "scenario_families": normalized_families,
    }
