"""Static extraction helpers for one qualified verification-planner block."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping


class ExtractionError(ValueError):
    """Raised when a bounded block cannot be extracted honestly."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


ALLOWED_QUALIFICATION_DECISIONS = {"accept", "expand"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalized_list(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    for raw in values or []:
        text = _clean_text(raw)
        if text and text not in items:
            items.append(text)
    return items


def _require_keys(obj: Mapping[str, Any], keys: Iterable[str], *, context: str) -> None:
    missing = [key for key in keys if key not in obj]
    if missing:
        raise ExtractionError(
            "boundary_still_ambiguous_after_qualification",
            f"{context} missing required fields: {', '.join(missing)}",
        )


def _require_non_empty(values: Iterable[Any], *, code: str, message: str) -> List[str]:
    normalized = _normalized_list(values)
    if not normalized:
        raise ExtractionError(code, message)
    return normalized


def _normalize_seam_record(record: Mapping[str, Any]) -> Dict[str, str]:
    required = [
        "record_id",
        "record_kind",
        "source_of_truth_status",
        "how_it_could_disagree",
        "observation_path",
    ]
    _require_keys(record, required, context="major truth seam record")
    normalized = {field: _clean_text(record.get(field)) for field in required}
    if any(not value for value in normalized.values()):
        raise ExtractionError(
            "major_truth_seam_has_fewer_than_two_independent_records_and_no_explicit_unknown_note",
            "major truth seam record fields must be non-empty",
        )
    return normalized


def _normalize_exclusion(record: Mapping[str, Any]) -> Dict[str, str]:
    required = [
        "seam_id",
        "reason_excluded_for_this_child_scope",
        "what_claim_or_recovery_story_it_could_still_change",
        "which_sibling_or_later_phase_owns_it_if_known",
    ]
    _require_keys(record, required, context="intentional exclusion")
    normalized = {field: _clean_text(record.get(field)) for field in required}
    if any(not value for value in normalized.values()):
        raise ExtractionError(
            "boundary_still_ambiguous_after_qualification",
            "intentional exclusion records must remain explicit and non-empty",
        )
    return normalized


def _collect_intentional_exclusions(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, str]]:
    exclusions: List[Dict[str, str]] = []
    for raw in records or []:
        if raw.get("included_in_boundary") is False:
            exclusions.append(
                _normalize_exclusion(
                    {
                        "seam_id": raw.get("seam_id"),
                        "reason_excluded_for_this_child_scope": raw.get("reason_if_excluded"),
                        "what_claim_or_recovery_story_it_could_still_change": raw.get("misplanning_risk_if_excluded"),
                        "which_sibling_or_later_phase_owns_it_if_known": raw.get(
                            "which_sibling_or_later_phase_owns_it_if_known"
                        ),
                    }
                )
            )
    return exclusions


def extract_complex_block(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Compile one qualified bounded-block input into extraction output."""

    payload = deepcopy(dict(raw or {}))
    qualification = payload.get("qualified_boundary_summary") or {}
    _require_keys(
        qualification,
        [
            "decision",
            "selected_boundary_summary",
            "adjacent_truth_seams_reviewed",
            "seams_requiring_expansion",
            "maximum_supported_claim_after_qualification",
            "why_this_is_the_current_risk_unit",
        ],
        context="qualified boundary summary",
    )
    decision = _clean_text(qualification.get("decision")).lower()
    if decision not in ALLOWED_QUALIFICATION_DECISIONS:
        raise ExtractionError(
            "boundary_still_ambiguous_after_qualification",
            f"qualification decision {decision or '<missing>'} does not allow extraction",
        )

    authority_map = payload.get("seed_authority_map_or_explicit_unknowns") or {}
    truth_authorities = _require_non_empty(
        authority_map.get("truth_authorities") or [],
        code="truth_authorities_cannot_be_named",
        message="truth authorities must be named before extraction",
    )
    claimed_invariants = _require_non_empty(
        authority_map.get("claimed_invariants") or [],
        code="claimed_invariants_or_operator_claims_cannot_be_stated",
        message="claimed invariants must be stated before extraction",
    )
    operator_claims = _require_non_empty(
        payload.get("operator_facing_claims_under_attack") or [],
        code="claimed_invariants_or_operator_claims_cannot_be_stated",
        message="operator-facing claims must be stated before extraction",
    )

    entrypoints_and_mutations = payload.get("entrypoints_and_mutation_points") or {}
    _require_keys(
        entrypoints_and_mutations,
        ["files_touched", "entrypoints", "mutation_points"],
        context="entrypoints and mutation points",
    )
    files_touched = _normalized_list(entrypoints_and_mutations.get("files_touched") or [])
    entrypoints = _normalized_list(entrypoints_and_mutations.get("entrypoints") or [])
    if not files_touched or not entrypoints:
        raise ExtractionError(
            "boundary_still_ambiguous_after_qualification",
            "files_touched and entrypoints must be non-empty for a bounded block",
        )

    adjacent_review = payload.get("adjacent_truth_seam_review_records") or qualification.get(
        "adjacent_truth_seams_reviewed"
    )
    if not isinstance(adjacent_review, list) or not adjacent_review:
        raise ExtractionError(
            "boundary_still_ambiguous_after_qualification",
            "adjacent truth seam review records are required",
        )

    major_truth_seams = []
    for seam in payload.get("major_truth_seams") or []:
        _require_keys(
            seam,
            [
                "seam_id",
                "seam_kind",
                "local_code_shape",
                "broader_truth_seam",
                "truth_authorities",
                "authority_precedence_note",
                "operator_claims_affected",
                "independent_records",
                "ambiguity_window",
                "why_the_seam_is_in_scope",
            ],
            context="major truth seam",
        )
        records = [_normalize_seam_record(record) for record in seam.get("independent_records") or []]
        unresolved_note = _clean_text(seam.get("independent_records_unresolved_note"))
        if len(records) < 2 and not unresolved_note:
            raise ExtractionError(
                "major_truth_seam_has_fewer_than_two_independent_records_and_no_explicit_unknown_note",
                f"major truth seam {seam.get('seam_id', '<unknown>')} does not have enough independent records",
            )

        seam_exclusions = [_normalize_exclusion(item) for item in seam.get("intentionally_excluded_adjacent_seams") or []]
        major_truth_seams.append(
            {
                "seam_id": _clean_text(seam.get("seam_id")),
                "seam_kind": _clean_text(seam.get("seam_kind")),
                "local_code_shape": _clean_text(seam.get("local_code_shape")),
                "broader_truth_seam": _clean_text(seam.get("broader_truth_seam")),
                "truth_authorities": _normalized_list(seam.get("truth_authorities") or []),
                "authority_precedence_note": _clean_text(seam.get("authority_precedence_note")),
                "operator_claims_affected": _normalized_list(seam.get("operator_claims_affected") or []),
                "independent_records": records,
                "independent_records_unresolved_note": unresolved_note or None,
                "ambiguity_window": _clean_text(seam.get("ambiguity_window")),
                "intentionally_excluded_adjacent_seams": seam_exclusions,
                "why_the_seam_is_in_scope": _clean_text(seam.get("why_the_seam_is_in_scope")),
            }
        )

    if not major_truth_seams:
        raise ExtractionError(
            "boundary_still_ambiguous_after_qualification",
            "at least one major truth seam is required for extraction",
        )

    extracted_exclusions = _collect_intentional_exclusions(adjacent_review)
    durable_state = payload.get("durable_state_and_mutation_points") or {}
    observation_paths = payload.get("observation_paths") or {}
    existing_tests = payload.get("known_existing_tests_or_explicit_none") or {}

    return {
        "schema_version": "complex_block_extraction_output_v1",
        "planner_family": "static_verification_planner_v1",
        "qualified_boundary_decision": decision,
        "maximum_supported_claim": "risk_structure_extracted_for_planning",
        "qualification_claim_ceiling": _clean_text(qualification.get("maximum_supported_claim_after_qualification")),
        "bounded_block_summary": {
            "selected_boundary_summary": _clean_text(qualification.get("selected_boundary_summary")),
            "qualified_boundary_decision": decision,
            "files_touched": files_touched,
            "entrypoints": entrypoints,
            "why_this_is_the_current_risk_unit": _clean_text(qualification.get("why_this_is_the_current_risk_unit")),
            "intentionally_excluded_adjacent_seams": extracted_exclusions,
        },
        "authority_graph": {
            "truth_authorities": truth_authorities,
            "authority_precedence_edges": _normalized_list(authority_map.get("authority_precedence_edges") or []),
            "claimed_invariants": claimed_invariants,
            "operator_facing_claims": operator_claims,
            "ambiguity_windows": _normalized_list(authority_map.get("ambiguity_windows") or []),
            "authority_conflicts_or_unknowns": _normalized_list(
                authority_map.get("authority_conflicts_or_unknowns") or []
            ),
        },
        "major_truth_seams": major_truth_seams,
        "durable_state_and_mutation_points": {
            "durable_state_paths": _normalized_list(durable_state.get("durable_state_paths") or []),
            "mutation_points": _normalized_list(entrypoints_and_mutations.get("mutation_points") or []),
            "side_effects": _normalized_list(durable_state.get("side_effects") or []),
            "external_surfaces": _normalized_list(durable_state.get("external_surfaces") or []),
            "time_retry_lease_logic": _normalized_list(durable_state.get("time_retry_lease_logic") or []),
            "cleanup_and_recovery_paths": _normalized_list(
                durable_state.get("cleanup_and_recovery_paths") or []
            ),
        },
        "observation_paths": {
            "operator_visible_surfaces": _normalized_list(observation_paths.get("operator_visible_surfaces") or []),
            "internal_observation_paths": _normalized_list(observation_paths.get("internal_observation_paths") or []),
            "trust_boundaries": _normalized_list(observation_paths.get("trust_boundaries") or []),
            "where_observation_can_smooth_or_hide_failure": _normalized_list(
                observation_paths.get("where_observation_can_smooth_or_hide_failure") or []
            ),
        },
        "existing_tests_and_blind_spots": {
            "existing_tests_reviewed": _normalized_list(existing_tests.get("existing_tests_reviewed") or []),
            "likely_blind_spots": _normalized_list(existing_tests.get("likely_blind_spots") or []),
            "why_current_tests_may_miss_them": _normalized_list(
                existing_tests.get("why_current_tests_may_miss_them") or []
            ),
            "blind_spot_claim_impact": _normalized_list(existing_tests.get("blind_spot_claim_impact") or []),
            "missing_negative_controls_or_absence_note": _clean_text(
                existing_tests.get("missing_negative_controls_or_absence_note")
            ),
        },
    }
