#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SCHEMA_VERSION = "last_chance_unattended_scorecard_v1"
FAMILY_UID = "PIS-20260413-174938Z-R9N5"
S1P2_UID = "PIS-20260413-174939Z-S1P2"
T2Q3_UID = "PIS-20260413-174940Z-T2Q3"
U3R4_UID = "PIS-20260413-174941Z-U3R4"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_contract() -> Dict[str, Dict[str, Any]]:
    scorecard = {
        "schema_version": SCHEMA_VERSION,
        "family_uid": FAMILY_UID,
        "scorecard_policy": {
            "unsupported_unattended_window_defaults_to_zero": True,
            "listed_but_unrun_scenarios_are_blockers": True,
            "absence_of_signal_failures_are_first_class": True,
            "stitched_truth_impossibility_is_a_major_failure_class": True,
            "partial_child_execution_blocks_positive_unattended_window": True,
        },
        "child_execution_contracts_v1": [
            {
                "child_uid": T2Q3_UID,
                "minimum_duration_class": "materially_longer_than_prior_bounded_local_campaigns",
                "minimum_scenario_count": 3,
                "minimum_repetitions": 4,
                "requires_contamination_declarations": True,
                "requires_post_incident_quiet_windows": True,
                "required_failure_classes": [
                    "delayed_discovery_false_calm",
                    "restart_flap_carryover_contamination",
                    "unsupported_unattended_window_defaults_to_zero",
                ],
            },
            {
                "child_uid": U3R4_UID,
                "minimum_duration_class": "bounded_but_cross_time_hostile",
                "minimum_scenario_count": 3,
                "minimum_repetitions": 2,
                "requires_contamination_declarations": True,
                "requires_cross_surface_disagreement": True,
                "required_failure_classes": [
                    "cross_time_plausible_impossible_story",
                    "bundle_level_advisory_overrides_local_plausibility",
                    "operator_false_story_catalog_required",
                ],
            },
        ],
        "major_failure_classes_v1": [
            {
                "id": "delayed_discovery_false_calm",
                "required_child_uid": T2Q3_UID,
                "blocks_claims": ["safe_for_unsupervised_operation_within_declared_bounds"],
            },
            {
                "id": "restart_flap_carryover_contamination",
                "required_child_uid": T2Q3_UID,
                "blocks_claims": ["safe_for_unsupervised_operation_within_declared_bounds"],
            },
            {
                "id": "cross_time_plausible_impossible_story",
                "required_child_uid": U3R4_UID,
                "blocks_claims": ["safe_for_unsupervised_operation_within_declared_bounds"],
            },
            {
                "id": "absence_of_signal_failure",
                "required_child_uid": T2Q3_UID,
                "blocks_claims": ["safe_for_unsupervised_operation_within_declared_bounds"],
            },
            {
                "id": "stitched_truth_impossibility",
                "required_child_uid": U3R4_UID,
                "blocks_claims": ["safe_for_unsupervised_operation_within_declared_bounds"],
            },
        ],
        "claim_levels": {
            "improved": {"blocked_by": []},
            "safe_for_unsupervised_operation_within_declared_bounds": {
                "blocked_by": [
                    "blocker_unrun_major_failure_class",
                    "blocker_missing_false_calm_detection",
                    "blocker_missing_cross_surface_stitched_story_check",
                    "blocker_missing_contamination_or_quiet_window_declarations",
                    "blocker_no_positive_unattended_window_earned",
                ]
            },
        },
    }
    blockers = {
        "schema_version": "last_chance_claim_blockers_v1",
        "family_uid": FAMILY_UID,
        "blockers": [
            {
                "id": "blocker_unrun_major_failure_class",
                "machine_derived": True,
                "reason": "A major failure class was listed but not actually executed.",
            },
            {
                "id": "blocker_missing_false_calm_detection",
                "machine_derived": True,
                "reason": "False calm or delayed discovery was not explicitly attacked.",
            },
            {
                "id": "blocker_missing_cross_surface_stitched_story_check",
                "machine_derived": True,
                "reason": "No plausible-impossible cross-surface story was exercised.",
            },
            {
                "id": "blocker_missing_contamination_or_quiet_window_declarations",
                "machine_derived": True,
                "reason": "Dirty carryover, contamination, or post-incident quiet windows were not declared.",
            },
            {
                "id": "blocker_no_positive_unattended_window_earned",
                "machine_derived": True,
                "reason": "No measured unattended window survived stop-rule evaluation.",
            },
        ],
    }
    stop_rules = {
        "schema_version": "last_chance_stop_rules_v1",
        "family_uid": FAMILY_UID,
        "stop_rules": [
            {
                "id": "unsupported_unattended_window_defaults_to_zero",
                "trigger": "no_positive_window_earned_or_major_failure_class_unrun",
                "result": "supported_unattended_window_minutes=0",
            },
            {
                "id": "absence_of_signal_failure_blocks_progress",
                "trigger": "incident_exists_but_no_surface_warns_operator",
                "result": "stop_and_block_unattended_claim",
            },
            {
                "id": "stitched_truth_impossibility_blocks_progress",
                "trigger": "independent_surfaces_are_locally_plausible_but_globally_impossible",
                "result": "stop_and_mark_observation_honesty_failure",
            },
        ],
    }
    return {
        "scorecard": scorecard,
        "blockers": blockers,
        "stop_rules": stop_rules,
    }


def run_contract(out_dir: Path) -> Dict[str, Dict[str, Any]]:
    payloads = build_contract()
    _write_json(out_dir / "last_chance_scorecard_v1.json", payloads["scorecard"])
    _write_json(out_dir / "last_chance_claim_blockers_v1.json", payloads["blockers"])
    _write_json(out_dir / "last_chance_stop_rules_v1.json", payloads["stop_rules"])
    return payloads


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run_contract(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
