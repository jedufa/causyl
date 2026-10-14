from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane.static_verification.static_verification_planner_harshness import score_planner_output, score_scenario_family


REPO_ROOT = Path(__file__).resolve().parents[2]
V9Y1_UID = "PIS-20260413-161607Z-V9Y1"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_v9y1_dir() -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{V9Y1_UID}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {V9Y1_UID}")


def test_v9y1_meta_points_to_required_harshness_artifacts() -> None:
    meta = _load_json(_find_v9y1_dir() / "meta.json")

    assert meta["required_evidence"] == [
        "artifacts/planning/harshness_rubric_v1.json",
        "artifacts/planning/fake_harsh_bans_v1.json",
    ]


def test_v9y1_rubric_requires_loophole_closure_bug_yield_and_reachability_fields() -> None:
    rubric = _load_json(_find_v9y1_dir() / "artifacts" / "planning" / "harshness_rubric_v1.json")

    assert set(rubric["required_per_scenario_family_fields"]) >= {
        "loophole_closure",
        "bug_yield_hypothesis",
        "production_reachability",
    }
    assert rubric["production_reachability_contract"]["theatrical_class_is_never_hard_to_fake"] is True

    dimensions = {entry["id"]: entry for entry in rubric["scoring_dimensions"]}
    assert set(dimensions) == {
        "loophole_closure_quality",
        "bug_yield_hypothesis_quality",
        "production_reachability_quality",
        "truth_surface_pressure",
        "multi_step_and_interference_pressure",
        "execution_viability",
    }
    assert rubric["score_bands"]["hard_to_fake_min_score"] == 70


def test_v9y1_fake_harsh_bans_enumerate_common_theatrical_failure_modes() -> None:
    bans = _load_json(_find_v9y1_dir() / "artifacts" / "planning" / "fake_harsh_bans_v1.json")

    rule_ids = {entry["id"] for entry in bans["ban_rules"]}
    assert rule_ids >= {
        "scary_wording_without_loophole_closure",
        "long_list_without_claim_discipline",
        "no_bug_yield_hypothesis",
        "missing_production_reachability_honesty",
        "impossible_state_theater",
        "no_independent_truth_pressure",
        "executionless_brutality_theater",
    }
    assert bans["decision_contract"]["any_active_ban_forces_classification"] == "sounds_harsh_only"


def test_v9y1_scoring_accepts_a_family_that_is_hard_to_fake() -> None:
    v9y1_dir = _find_v9y1_dir()
    rubric = _load_json(v9y1_dir / "artifacts" / "planning" / "harshness_rubric_v1.json")
    bans = _load_json(v9y1_dir / "artifacts" / "planning" / "fake_harsh_bans_v1.json")

    family = {
        "scenario_family_id": "scheduler-stale-reconcile",
        "threat_class": "stale_state_false_healthy",
        "operator_claim_under_attack": "The plane reports the run family as coherent after restart.",
        "independent_records_that_can_disagree": [
            "task register active task projection",
            "scheduler receipt history",
            "workflow closeout artifact",
        ],
        "negative_control_plan": "Seed a clean receipt chain and verify the mismatch marker stays absent.",
        "disqualifying_incidents": [
            "The stale receipt is silently accepted as current.",
            "The operator surface smooths the contradiction into healthy."
        ],
        "loophole_closure": {
            "lazy_pass_path": "Only assert that some restart warning exists somewhere in the output.",
            "closure_mechanism": "Force a contradictory receipt and require the operator claim plus receipt lineage to disagree explicitly.",
            "why_the_closure_forces_real_signal": "The family cannot pass by tone alone because it must expose the exact contradiction and the disqualifying incident."
        },
        "bug_yield_hypothesis": {
            "unknown_bug_class_sought": "Restart reconciliation may prefer stale receipts over the current task register snapshot.",
            "why_existing_checks_miss_it": "Current happy-path checks do not seed cross-record contradiction after restart.",
            "evidence_of_failure_if_present": "The operator surface stays healthy while the receipt lineage and task register disagree."
        },
        "production_reachability": {
            "classification": "reachable_under_degraded_or_faulted_operation",
            "why_this_classification_is_honest": "Receipt lag, restart, and stale cache carryover are real degraded conditions in the plane."
        },
        "contamination_or_interference_notes": "A prior restart test can hide the stale receipt unless the family resets receipts and snapshots between runs.",
        "repetition_overlap_accumulation_plan": "Repeat after one clean restart and one stale-cache restart to ensure the mismatch is not a one-off.",
        "execution_readiness": {
            "readiness_class": "runnable_after_bounded_setup",
            "why_not_just_theoretical": "The family uses existing restart fixtures plus one bounded contradictory receipt seed."
        },
        "scenario_examples": [
            "restart with stale receipt retained",
            "restart with task register newer than receipt lineage",
            "operator snapshot requested before reconciliation settles",
            "repeat run after cleanup to prove the harness can clear the mismatch"
        ],
    }

    result = score_scenario_family(family, rubric, bans)

    assert result["classification"] == "hard_to_fake"
    assert result["total_score"] >= 70
    assert result["active_ban_ids"] == []
    assert result["missing_required_fields"] == []


def test_v9y1_scoring_rejects_fake_harsh_output_that_only_sounds_severe() -> None:
    v9y1_dir = _find_v9y1_dir()
    rubric = _load_json(v9y1_dir / "artifacts" / "planning" / "harshness_rubric_v1.json")
    bans = _load_json(v9y1_dir / "artifacts" / "planning" / "fake_harsh_bans_v1.json")

    plan = {
        "scenario_families": [
            {
                "scenario_family_id": "dramatic-but-empty",
                "threat_class": "catastrophic_failure_everywhere",
                "operator_claim_under_attack": "",
                "independent_records_that_can_disagree": [],
                "negative_control_plan": "",
                "disqualifying_incidents": [],
                "loophole_closure": {},
                "bug_yield_hypothesis": {},
                "production_reachability": {
                    "classification": "theatrical_not_actionable",
                    "why_this_classification_is_honest": ""
                },
                "contamination_or_interference_notes": "",
                "repetition_overlap_accumulation_plan": "",
                "execution_readiness": {
                    "readiness_class": "purely_theoretical",
                    "why_not_just_theoretical": ""
                },
                "scenario_examples": [
                    "Destroy every boundary at once",
                    "Use terrifying chaos mode",
                    "Overwhelm all operators with impossible contradictions",
                    "Attack every subsystem simultaneously",
                    "Demand maximum brutality"
                ],
            }
        ]
    }

    report = score_planner_output(plan, rubric, bans)
    family = report["scenario_families"][0]

    assert report["overall_classification"] == "sounds_harsh_only"
    assert family["classification"] == "sounds_harsh_only"
    assert set(family["active_ban_ids"]) >= {
        "scary_wording_without_loophole_closure",
        "long_list_without_claim_discipline",
        "no_bug_yield_hypothesis",
        "missing_production_reachability_honesty",
        "impossible_state_theater",
        "no_independent_truth_pressure",
        "executionless_brutality_theater",
    }


def test_v9y1_scoring_treats_empty_nested_contract_objects_as_missing() -> None:
    v9y1_dir = _find_v9y1_dir()
    rubric = _load_json(v9y1_dir / "artifacts" / "planning" / "harshness_rubric_v1.json")
    bans = _load_json(v9y1_dir / "artifacts" / "planning" / "fake_harsh_bans_v1.json")

    family = {
        "scenario_family_id": "hollow-shape",
        "threat_class": "theater",
        "operator_claim_under_attack": "The plan pretends shape equals substance.",
        "independent_records_that_can_disagree": ["operator view", "receipt log"],
        "negative_control_plan": "Seed a hollow family and ensure the scorer reports missing contract substance.",
        "disqualifying_incidents": ["Empty contract objects count as present."],
        "loophole_closure": {},
        "bug_yield_hypothesis": {},
        "production_reachability": {},
        "contamination_or_interference_notes": "No cross-run contamination expected.",
        "repetition_overlap_accumulation_plan": "One run is enough because the loophole is structural.",
        "execution_readiness": {},
    }

    result = score_scenario_family(family, rubric, bans)

    assert result["classification"] == "sounds_harsh_only"
    assert set(result["missing_required_fields"]) >= {
        "loophole_closure",
        "bug_yield_hypothesis",
        "production_reachability",
        "execution_readiness",
    }
    assert set(result["active_ban_ids"]) >= {
        "scary_wording_without_loophole_closure",
        "no_bug_yield_hypothesis",
        "missing_production_reachability_honesty",
        "executionless_brutality_theater",
    }


def test_v9y1_scoring_rejects_single_record_truth_pressure_as_fake_harshness() -> None:
    v9y1_dir = _find_v9y1_dir()
    rubric = _load_json(v9y1_dir / "artifacts" / "planning" / "harshness_rubric_v1.json")
    bans = _load_json(v9y1_dir / "artifacts" / "planning" / "fake_harsh_bans_v1.json")

    family = {
        "scenario_family_id": "single-record-theater",
        "threat_class": "stale_operator_projection",
        "operator_claim_under_attack": "The plane reports the family as coherent while one record is stale.",
        "independent_records_that_can_disagree": [
            "operator summary projection",
        ],
        "negative_control_plan": "Use a clean projection snapshot and confirm the stale marker does not appear.",
        "disqualifying_incidents": [
            "The scorer accepts one self-confirming record as independent truth pressure.",
        ],
        "loophole_closure": {
            "lazy_pass_path": "Treat one operator-facing read model as sufficient evidence.",
            "closure_mechanism": "Require disagreement between distinct truth authorities, not one surface repeating itself.",
            "why_the_closure_forces_real_signal": "A single record cannot prove contradiction pressure because nothing independent can disagree with it."
        },
        "bug_yield_hypothesis": {
            "unknown_bug_class_sought": "A stale projection can look healthy when it is only reading its own cached state.",
            "why_existing_checks_miss_it": "Gentle checks often assert one rendered surface without comparing it to any independent source.",
            "evidence_of_failure_if_present": "The family earns a harsh score without naming a second authority that could contradict the stale projection."
        },
        "production_reachability": {
            "classification": "reachable_in_normal_operation",
            "why_this_classification_is_honest": "Cached operator summaries are normal runtime behavior, not an exotic fault injection."
        },
        "contamination_or_interference_notes": "No cross-run contamination expected; the defect is the missing second truth surface.",
        "repetition_overlap_accumulation_plan": "Repeat once with a fresh snapshot to show the loophole is structural rather than flaky.",
        "execution_readiness": {
            "readiness_class": "runnable_now",
            "why_not_just_theoretical": "The family only needs one cached operator projection and one independent truth comparison requirement."
        },
        "scenario_examples": [
            "score one projection against itself",
            "treat a cached operator view as the only authority",
            "claim contradiction pressure without a second record",
            "declare the family harsh because the wording sounds strict"
        ],
    }

    result = score_scenario_family(family, rubric, bans)

    assert result["classification"] == "sounds_harsh_only"
    assert "no_independent_truth_pressure" in result["active_ban_ids"]
    assert "independent_records_that_can_disagree" in result["weak_or_missing_ban_fields"]
