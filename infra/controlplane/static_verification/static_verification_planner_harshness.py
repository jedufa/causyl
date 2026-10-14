"""Deterministic harshness scoring for static verification planner scenario families."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Sequence


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


def _has_value(payload: Mapping[str, Any], dotted: str) -> bool:
    value = _get_path(payload, dotted)
    return _value_has_signal(value)


def _dimension_score(dimension_id: str, family: Mapping[str, Any]) -> float:
    reachability = _clean_text(_get_path(family, "production_reachability.classification"))

    if dimension_id == "loophole_closure_quality":
        return 1.0 if all(
            _has_value(family, path)
            for path in (
                "loophole_closure.lazy_pass_path",
                "loophole_closure.closure_mechanism",
                "loophole_closure.why_the_closure_forces_real_signal",
            )
        ) else 0.0

    if dimension_id == "bug_yield_hypothesis_quality":
        return 1.0 if all(
            _has_value(family, path)
            for path in (
                "bug_yield_hypothesis.unknown_bug_class_sought",
                "bug_yield_hypothesis.why_existing_checks_miss_it",
                "bug_yield_hypothesis.evidence_of_failure_if_present",
            )
        ) else 0.0

    if dimension_id == "production_reachability_quality":
        if not all(
            _has_value(family, path)
            for path in (
                "production_reachability.classification",
                "production_reachability.why_this_classification_is_honest",
            )
        ):
            return 0.0
        if reachability == "reachable_in_normal_operation":
            return 1.0
        if reachability in {
            "reachable_under_degraded_or_faulted_operation",
            "observation_only_impossible_but_useful",
        }:
            return 0.75
        return 0.0

    if dimension_id == "truth_surface_pressure":
        operator_claim = _has_value(family, "operator_claim_under_attack")
        independent_records = len(_normalized_list(_get_path(family, "independent_records_that_can_disagree") or []))
        if operator_claim and independent_records >= 2:
            return 1.0
        if operator_claim and independent_records == 1:
            return 0.5
        return 0.0

    if dimension_id == "multi_step_and_interference_pressure":
        repetition = _has_value(family, "repetition_overlap_accumulation_plan")
        contamination = _has_value(family, "contamination_or_interference_notes")
        if repetition and contamination:
            return 1.0
        if repetition or contamination:
            return 0.5
        return 0.0

    if dimension_id == "execution_viability":
        readiness_class = _clean_text(_get_path(family, "execution_readiness.readiness_class"))
        theory_note = _has_value(family, "execution_readiness.why_not_just_theoretical")
        if not readiness_class or not theory_note:
            return 0.0
        if readiness_class in {"runnable_now", "runnable_after_bounded_setup"}:
            return 1.0
        if readiness_class == "observation_only_honesty_check":
            return 0.5
        return 0.0

    raise KeyError(f"unknown harshness dimension: {dimension_id}")


def _evaluate_bans(
    family: Mapping[str, Any],
    bans: Mapping[str, Any],
) -> tuple[List[str], List[str]]:
    active: List[str] = []
    weak_fields: List[str] = []
    reachability = _clean_text(_get_path(family, "production_reachability.classification"))
    independent_records = _normalized_list(_get_path(family, "independent_records_that_can_disagree") or [])

    for rule in bans.get("ban_rules") or []:
        required_fields = rule.get("required_fields") or []
        missing = [field for field in required_fields if not _has_value(family, field)]
        forbidden_classes = set(rule.get("forbidden_reachability_classes") or [])

        triggered = False
        if missing:
            triggered = True
            weak_fields.extend(missing)
        if forbidden_classes and reachability in forbidden_classes:
            triggered = True

        if rule.get("id") == "long_list_without_claim_discipline":
            listed = len(_normalized_list(family.get("scenario_examples") or []))
            if listed < 4:
                triggered = False
        if rule.get("id") == "impossible_state_theater":
            if reachability != "theatrical_not_actionable":
                triggered = False
        if rule.get("id") == "no_independent_truth_pressure":
            minimum_records = int(rule.get("minimum_independent_records", 2))
            if len(independent_records) < minimum_records:
                triggered = True
                weak_fields.append("independent_records_that_can_disagree")

        if triggered:
            active.append(_clean_text(rule.get("id")))

    return active, sorted(set(weak_fields))


def score_scenario_family(
    family: Mapping[str, Any],
    rubric: Mapping[str, Any],
    bans: Mapping[str, Any],
) -> Dict[str, Any]:
    payload = deepcopy(dict(family or {}))
    missing_required = sorted(
        field for field in rubric.get("required_per_scenario_family_fields") or [] if not _has_value(payload, field)
    )

    dimension_scores: List[Dict[str, Any]] = []
    total_score = 0.0
    hard_fail_dimension_failed = False
    for dimension in rubric.get("scoring_dimensions") or []:
        ratio = _dimension_score(dimension["id"], payload)
        score = round(float(dimension["weight"]) * ratio, 2)
        if dimension.get("hard_fail_if_missing") and score <= 0:
            hard_fail_dimension_failed = True
        dimension_scores.append(
            {
                "id": dimension["id"],
                "weight": dimension["weight"],
                "score": score,
            }
        )
        total_score += score

    active_bans, weak_fields = _evaluate_bans(payload, bans)
    thresholds = rubric.get("score_bands") or {}
    reachability = _clean_text(_get_path(payload, "production_reachability.classification"))
    forbidden_reachability = set(thresholds.get("hard_to_fake_forbidden_reachability_classes") or [])

    if (
        active_bans
        or missing_required
        or hard_fail_dimension_failed
        or reachability in forbidden_reachability
        or total_score < float(thresholds.get("borderline_min_score", 0))
    ):
        classification = "sounds_harsh_only"
    elif total_score >= float(thresholds.get("hard_to_fake_min_score", 0)):
        classification = "hard_to_fake"
    else:
        classification = "borderline"

    return {
        "scenario_family_id": _clean_text(payload.get("scenario_family_id")),
        "total_score": round(total_score, 2),
        "classification": classification,
        "dimension_scores": dimension_scores,
        "missing_required_fields": missing_required,
        "active_ban_ids": active_bans,
        "weak_or_missing_ban_fields": weak_fields,
    }


def score_planner_output(
    plan: Mapping[str, Any],
    rubric: Mapping[str, Any],
    bans: Mapping[str, Any],
) -> Dict[str, Any]:
    families = [score_scenario_family(item, rubric, bans) for item in (plan.get("scenario_families") or [])]
    if not families:
        return {
            "schema_version": "harshness_score_report_v1",
            "overall_classification": "sounds_harsh_only",
            "scenario_families": [],
            "active_ban_ids": [],
        }

    if any(item["classification"] == "sounds_harsh_only" for item in families):
        overall = "sounds_harsh_only"
    elif all(item["classification"] == "hard_to_fake" for item in families):
        overall = "hard_to_fake"
    else:
        overall = "borderline"

    active_bans = sorted({ban_id for item in families for ban_id in item["active_ban_ids"]})
    return {
        "schema_version": "harshness_score_report_v1",
        "overall_classification": overall,
        "scenario_families": families,
        "active_ban_ids": active_bans,
    }
