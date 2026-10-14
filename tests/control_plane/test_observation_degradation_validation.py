from __future__ import annotations

import json
from pathlib import Path

from tools.bin import observation_degradation_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_observation_degradation_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(out_dir=tmp_path)

    assert result["matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "observation_degradation_matrix.json").exists()
    assert (tmp_path / "operator_truth_trace.jsonl").exists()
    assert (tmp_path / "observation_negative_controls.json").exists()
    assert (tmp_path / "observation_truth_snapshots.json").exists()
    assert (tmp_path / "observation_metrics_summary.json").exists()


def test_observation_degradation_validation_blocks_false_smoothness_and_preserves_fresh_negative_control(tmp_path: Path) -> None:
    validation.run_validation(out_dir=tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "observation_degradation_matrix.json")["rows"]}
    assert rows["OBS_JOBS_UNAVAILABLE_WATCH_FALSE_SMOOTHNESS_V1"]["observed_classification"] == "degraded_observation_blocks_v2_primary_projection"
    assert rows["OBS_STALE_SNAPSHOT_WATCH_CROSS_TIME_INCOHERENCE_V1"]["observed_classification"] == "cross_time_incoherence_stays_explicit"
    assert rows["OBS_PAYLOAD_ADVISORY_WHEN_JOBS_UNAVAILABLE_V1"]["observed_classification"] == "payload_marks_v2_advisory_under_degraded_observation"
    assert rows["OBS_OPERATOR_SUMMARY_CACHE_CORRUPTION_EXPLICIT_V1"]["observed_classification"] == "task_register_summary_cache_corruption"
    assert rows["OBS_FRESH_SUBPROCESS_FALLBACK_NEGATIVE_CONTROL_V1"]["observed_classification"] == "fresh_subprocess_fallback_remains_usable"

    negatives = _load_json(tmp_path / "observation_negative_controls.json")
    assert all(bool(row["pass"]) for row in negatives["rows"])
