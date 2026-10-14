"""Seeded self-validation harness for the static verification planner family."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping

from infra.controlplane.static_verification.static_verification_planner_extraction import ExtractionError, extract_complex_block
from infra.controlplane.static_verification.static_verification_planner_generation import PlannerGenerationError, generate_planner_output
from infra.controlplane.static_verification.static_verification_planner_harshness import score_planner_output
from infra.controlplane.static_verification.static_verification_planner_threats import ThreatModelError, compile_threat_matrix


def run_seeded_validation(raw: Mapping[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(dict(raw or {}))
    results = []

    extraction_case = payload["seeded_bad_inputs"]["ambiguous_boundary_case"]
    try:
        extract_complex_block(extraction_case["input"])
        extraction_result = {"case_id": extraction_case["case_id"], "passed": False, "observed": "accepted_softly"}
    except ExtractionError as exc:
        extraction_result = {
            "case_id": extraction_case["case_id"],
            "passed": exc.code == extraction_case["expected_error_code"],
            "observed": exc.code,
        }
    results.append(extraction_result)

    threat_case = payload["seeded_bad_inputs"]["missing_repetition_case"]
    try:
        compile_threat_matrix(threat_case["input"])
        threat_result = {"case_id": threat_case["case_id"], "passed": False, "observed": "accepted_softly"}
    except ThreatModelError as exc:
        threat_result = {
            "case_id": threat_case["case_id"],
            "passed": exc.code == threat_case["expected_error_code"],
            "observed": exc.code,
        }
    results.append(threat_result)

    generation_case = payload["seeded_bad_inputs"]["missing_scenario_examples_case"]
    try:
        generate_planner_output(generation_case["input"])
        generation_result = {"case_id": generation_case["case_id"], "passed": False, "observed": "accepted_softly"}
    except PlannerGenerationError as exc:
        generation_result = {
            "case_id": generation_case["case_id"],
            "passed": exc.code == generation_case["expected_error_code"],
            "observed": exc.code,
        }
    results.append(generation_result)

    soft_baseline = payload["soft_baseline_case"]
    baseline_report = score_planner_output(
        soft_baseline["plan"],
        soft_baseline["rubric"],
        soft_baseline["bans"],
    )
    baseline_result = {
        "case_id": soft_baseline["case_id"],
        "passed": baseline_report["overall_classification"] == soft_baseline["expected_overall_classification"],
        "observed": baseline_report["overall_classification"],
    }
    results.append(baseline_result)

    passed = all(item["passed"] for item in results)
    return {
        "schema_version": "planner_seeded_validation_report_v1",
        "results": results,
        "all_cases_passed": passed,
        "negative_controls_caught": sum(1 for item in results if item["passed"]),
        "bug_finding_potential_focus": "reject bad inputs, reject soft baselines, and preserve planning-only claim ceilings",
    }
