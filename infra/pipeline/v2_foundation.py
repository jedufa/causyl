from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def _deepcopy(value: Any) -> Any:
    return deepcopy(value)


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _score_band(score: Optional[float]) -> str:
    if score is None:
        return "not_computed"
    if score >= 0.85:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def _merge_missing(target: Optional[Mapping[str, Any]], defaults: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(_deepcopy(dict(target or {})))
    for key, default_value in defaults.items():
        current = out.get(key)
        if key not in out:
            out[key] = _deepcopy(default_value)
            continue
        if isinstance(current, Mapping) and isinstance(default_value, Mapping):
            out[key] = _merge_missing(current, default_value)
    return out


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def confidence_payload(*, stage: str, score: Optional[float] = None, source: str = "phase0_placeholder", notes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    numeric_score = float(score) if _is_numeric(score) else None
    return {
        "stage": str(stage),
        "computed": numeric_score is not None,
        "score": numeric_score,
        "band": _score_band(numeric_score),
        "source": str(source),
        "notes": [str(item) for item in list(notes or [])],
    }


def intake_assessment_defaults() -> Dict[str, Any]:
    return {
        "task_families": [],
        "failure_taxonomy": {
            "operator_failure_modes": [],
            "system_failure_modes": [],
            "concurrency_failure_modes": [],
            "recovery_failure_modes": [],
        },
        "invariants": [],
        "derived_requirements": {
            "safety_requirements": [],
            "observability_requirements": [],
            "recovery_guarantees": [],
        },
        "confidence": confidence_payload(stage="script_1_job_intake"),
    }


def ensure_intake_assessment(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return _merge_missing(value, intake_assessment_defaults())


def task_profile_foundation_defaults(task_profile: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    score = None
    if task_profile and _is_numeric(task_profile.get("classifier_confidence")):
        score = float(task_profile.get("classifier_confidence") or 0.0)
    return {
        "task_archetype": "implementation_change",
        "planning_profiles": {
            "retrieval_profile": "owner_first_implementation",
            "ranking_profile": "edit_owner_focus",
            "bundle_profile": "execution_bundle",
            "output_profile": "patch_contract",
            "output_contract_mode": "patch_contract",
            "bundle_requirements": {},
            "role_weights": {},
        },
        "output_contract_mode": "patch_contract",
        "retrieval_bias": {"include": [], "exclude": []},
        "cross_cutting_flags": [],
        "confidence": confidence_payload(
            stage="script_2_task_classifier",
            score=score,
            source="classifier_confidence" if score is not None else "phase0_placeholder",
        ),
    }


def ensure_task_profile_foundation(task_profile: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return _merge_missing(task_profile, task_profile_foundation_defaults(task_profile))


def _derived_lexical_queries(search_plan: Optional[Mapping[str, Any]]) -> List[str]:
    queries = list((search_plan or {}).get("queries") or [])
    return _dedupe_strings([entry.get("query") for entry in queries if isinstance(entry, Mapping)])


def search_plan_foundation_defaults(
    search_plan: Optional[Mapping[str, Any]] = None,
    *,
    negative_targets: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "lexical_queries": _derived_lexical_queries(search_plan),
        "conceptual_queries": [],
        "negative_targets": _dedupe_strings(list(negative_targets or [])),
        "subsystem_hypotheses": [],
        "query_coverage_map": {},
        "confidence": confidence_payload(stage="script_3_search_plan"),
    }


def ensure_search_plan_foundation(
    search_plan: Optional[Mapping[str, Any]],
    *,
    negative_targets: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return _merge_missing(search_plan, search_plan_foundation_defaults(search_plan, negative_targets=negative_targets))


def _infer_match_origin(candidate: Optional[Mapping[str, Any]]) -> Optional[str]:
    sources = {str(item) for item in list((candidate or {}).get("evidence_sources") or []) if str(item).strip()}
    if not sources:
        return None
    if sources == {"semantic"}:
        return "semantic_only"
    if sources == {"deterministic"}:
        return "deterministic_only"
    if {"semantic", "deterministic"}.issubset(sources):
        return "mixed"
    return sorted(sources)[0]


def retrieval_candidate_foundation_defaults(candidate: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return {
        "file_role": None,
        "task_role_fit": None,
        "covers": [],
        "match_origin": _infer_match_origin(candidate),
        "coverage_score": None,
        "superficial_match_penalty": None,
        "candidate_role_guess": None,
        "candidate_role_confidence": None,
        "hub_family": None,
        "passive_surface_flag": None,
        "duplicate_family_key": None,
        "targetability_score": None,
        "annotation_reasons": [],
        "owner_signal_strength": None,
        "support_signal_strength": None,
        "hub_penalty": None,
        "path_specificity_score": None,
        "secondary_role_hints": [],
        "candidate_intent_lane": None,
        "context_intent_type": None,
        "structural_family_tags": [],
        "actionability_decision": {},
        "confidence": confidence_payload(stage="script_4_repo_retriever"),
    }


def ensure_retrieval_candidate_foundation(candidate: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return _merge_missing(candidate, retrieval_candidate_foundation_defaults(candidate))


def ranked_file_foundation_defaults(entry: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    edit_score = None
    if entry and _is_numeric(entry.get("targetability_score")):
        edit_score = float(entry.get("targetability_score") or 0.0)
    elif entry and _is_numeric(entry.get("score")):
        edit_score = float(entry.get("score") or 0.0)
    role = None
    if entry:
        role = str(entry.get("role") or entry.get("selection_lane") or "").strip() or None
    return {
        "role": role,
        "file_role": None,
        "bundle_role": None,
        "why_not_other_roles": [],
        "edit_necessity_score": edit_score,
        "passive_relevance": None,
        "primary_lane": None,
        "secondary_lanes": [],
        "lane_scores": {},
        "major_positive_signals": [],
        "major_penalties": [],
        "suppression_decisions": [],
        "candidate_intent_lane": None,
        "context_intent_type": None,
        "selection_cap_suppressed": False,
        "selection_cap_reasons": [],
        "confidence": confidence_payload(stage="script_5_context_ranker"),
    }


def ensure_ranked_file_foundation(entry: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return _merge_missing(entry, ranked_file_foundation_defaults(entry))


def ensure_ranked_context_foundation(ranked_context: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    out = _merge_missing(
        ranked_context,
        {"confidence": confidence_payload(stage="script_5_context_ranker")},
    )
    out["selected_files"] = [ensure_ranked_file_foundation(item) for item in list(out.get("selected_files") or [])]
    out["implementation_targets"] = [ensure_ranked_file_foundation(item) for item in list(out.get("implementation_targets") or [])]
    out["validation_targets"] = [ensure_ranked_file_foundation(item) for item in list(out.get("validation_targets") or [])]
    out["reference_context"] = [ensure_ranked_file_foundation(item) for item in list(out.get("reference_context") or [])]
    return out


def context_bundle_file_foundation_defaults(entry: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return {
        "file_role": None,
        "bundle_role": None,
        "responsibility": [],
        "covers_failure_modes": [],
        "candidate_intent_lane": None,
        "context_intent_type": None,
        "ownership_confidence": confidence_payload(stage="script_6_context_bundle.file"),
        "confidence": confidence_payload(stage="script_6_context_bundle.file"),
    }


def ensure_context_bundle_file_foundation(entry: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    return _merge_missing(entry, context_bundle_file_foundation_defaults(entry))


def context_bundle_foundation_defaults(context_bundle: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    bundle = dict(context_bundle or {})
    weakness_reasons = [str(item) for item in list(bundle.get("bundle_weakness_reasons") or [])]
    score = float(bundle.get("bundle_confidence") or 0.0) if _is_numeric(bundle.get("bundle_confidence")) else None
    coverage_validation = {
        "bundle_ready_for_execution": bool(bundle.get("bundle_ready_for_execution")),
        "weakness_reasons": weakness_reasons,
        "editable_target_count": len(list(bundle.get("editable_targets") or [])),
        "validation_target_count": len(list(bundle.get("validation_targets") or [])),
        "read_only_context_count": len(list(bundle.get("read_only_context") or [])),
    }
    return {
        "bundle_gap_warning": bool(weakness_reasons),
        "bundle_summary": {},
        "owner_uncertainty_flag": False,
        "owner_uncertainty_reason": "",
        "bundle_quality_status": "normal",
        "anti_collapse_actions_applied": [],
        "bundle_recovery_hints": [],
        "bundle_strategy": None,
        "bundle_gap_details": weakness_reasons,
        "coverage_validation": coverage_validation,
        "confidence": confidence_payload(
            stage="script_6_context_bundle",
            score=score,
            source="bundle_confidence" if score is not None else "phase0_placeholder",
        ),
    }


def ensure_context_bundle_foundation(context_bundle: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    out = _merge_missing(context_bundle, context_bundle_foundation_defaults(context_bundle))
    out["files"] = [ensure_context_bundle_file_foundation(item) for item in list(out.get("files") or [])]
    out["editable_targets"] = [ensure_context_bundle_file_foundation(item) for item in list(out.get("editable_targets") or [])]
    out["validation_targets"] = [ensure_context_bundle_file_foundation(item) for item in list(out.get("validation_targets") or [])]
    out["read_only_context"] = [ensure_context_bundle_file_foundation(item) for item in list(out.get("read_only_context") or [])]
    return out


def patch_plan_foundation_defaults(
    patch_plan: Optional[Mapping[str, Any]] = None,
    *,
    intake_assessment: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    plan = dict(patch_plan or {})
    intake = ensure_intake_assessment(intake_assessment)
    planning_score = float(plan.get("planning_confidence") or 0.0) if _is_numeric(plan.get("planning_confidence")) else None
    cross_cutting_contract = {
        "invariants": [str(item) for item in list(intake.get("invariants") or [])],
        "validation_rules": [],
        "retry_policy": [],
        "operator_safety": [str(item) for item in list(intake.get("derived_requirements", {}).get("safety_requirements") or [])],
        "observability_guarantees": [str(item) for item in list(intake.get("derived_requirements", {}).get("observability_requirements") or [])],
        "consistency_guarantees": [str(item) for item in list(intake.get("derived_requirements", {}).get("recovery_guarantees") or [])],
    }
    must_not_change = _dedupe_strings(
        list(plan.get("must_not_change") or [])
        + list(plan.get("files_read_only_context") or [])
        + list(plan.get("forbidden_targets") or [])
    )
    change_boundaries = list(plan.get("change_boundaries") or [])
    if not change_boundaries:
        change_boundaries = _dedupe_strings([entry.get("path") for entry in list(plan.get("modify_files") or []) if isinstance(entry, Mapping)])
    quality_metrics = dict(plan.get("quality_metrics") or {})
    if not quality_metrics:
        quality_metrics = {
            "modify_file_count": len(list(plan.get("modify_files") or [])),
            "required_test_count": len(list(plan.get("required_tests") or [])),
            "planning_confidence": planning_score,
        }
    return {
        "output_contract_mode": str(plan.get("output_contract_mode") or "patch_contract"),
        "design_execution_contract": dict(plan.get("design_execution_contract") or {}),
        "assessment_report_contract": dict(plan.get("assessment_report_contract") or {}),
        "retrieval_answer_contract": dict(plan.get("retrieval_answer_contract") or {}),
        "cross_cutting_contract": cross_cutting_contract,
        "must_not_change": must_not_change,
        "change_boundaries": change_boundaries,
        "quality_metrics": quality_metrics,
        "confidence": confidence_payload(
            stage="script_7_patch_plan",
            score=planning_score,
            source="planning_confidence" if planning_score is not None else "phase0_placeholder",
        ),
    }


def ensure_patch_plan_foundation(
    patch_plan: Optional[Mapping[str, Any]],
    *,
    intake_assessment: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return _merge_missing(patch_plan, patch_plan_foundation_defaults(patch_plan, intake_assessment=intake_assessment))
