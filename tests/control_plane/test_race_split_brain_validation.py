from __future__ import annotations

import json
from pathlib import Path

from tools.bin import race_split_brain_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_race_split_brain_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(out_dir=tmp_path)

    assert result["metrics"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "race_matrix.json").exists()
    assert (tmp_path / "split_brain_matrix.json").exists()
    assert (tmp_path / "race_truth_trace.jsonl").exists()
    assert (tmp_path / "race_split_brain_metrics_summary.json").exists()
    assert (tmp_path / "race_split_brain_truth_snapshots.json").exists()


def test_race_split_brain_validation_blocks_poisoned_truth_and_preserves_healthy_handoff(tmp_path: Path) -> None:
    validation.run_validation(out_dir=tmp_path)

    race_rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "race_matrix.json")["rows"]}
    split_rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "split_brain_matrix.json")["rows"]}
    metrics = _load_json(tmp_path / "race_split_brain_metrics_summary.json")

    assert race_rows["RACE_POISONED_DEPENDENCY_ADMISSION_V1"]["observed_classification"] == "poisoned_dependency_state_blocks_admission"
    assert race_rows["RACE_OPERATOR_TRIGGER_REJECTED_UNDER_DEGRADED_OBSERVATION_V1"]["observed_classification"] == "stale_operator_trigger_blocked_under_collision"
    assert race_rows["REPEATED_COLLISION_STORM_FAILS_CLOSED_V1"]["observed_classification"] == "collision_storm_remains_fail_closed"
    assert race_rows["HEALTHY_SINGLE_READY_NEGATIVE_CONTROL_V1"]["observed_classification"] == "healthy_dependency_handoff_still_progresses"
    assert split_rows["SPLIT_BRAIN_SHADOW_EVALUATION_BLOCKS_IMPOSSIBLE_RUNTIME_V1"]["observed_classification"] == "shadow_evaluation_blocks_impossible_runtime_truth"
    assert split_rows["SPLIT_BRAIN_MULTIPLE_ACTIVE_HEADS_WATCHDOG_V1"]["observed_classification"] == "multiple_active_heads_stay_ambiguous"
    assert metrics["pass_count"] == len(validation.SCENARIO_IDS)
    assert metrics["negative_controls_passed"] == 1
    assert metrics["bugs_found_and_fixed"][0]["issue"] == "scheduler_v2_admitted_dependency_from_terminal_success_and_active_run_split_brain"
