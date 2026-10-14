from __future__ import annotations

import json
from pathlib import Path

from tools.bin import live_soak_failure_storm_validation as soak


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_live_soak_failure_storm_validation_writes_required_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"

    result = soak.run_validation(out_dir=out_dir)

    manifest_path = out_dir / "live_soak_scenario_manifest.json"
    summary_path = out_dir / "live_soak_metrics_summary.json"
    incident_path = out_dir / "live_soak_incident_log.json"
    trace_path = out_dir / "live_soak_truth_trace.jsonl"

    assert Path(result["summary_path"]) == summary_path.resolve()
    assert Path(result["trace_path"]) == trace_path.resolve()
    assert manifest_path.exists()
    assert summary_path.exists()
    assert incident_path.exists()
    assert trace_path.exists()

    manifest = _load_json(manifest_path)
    summary = _load_json(summary_path)
    incidents = _load_json(incident_path)
    trace_rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert manifest["pis_uid"] == "PIS-20260413-110626Z-B2M5"
    assert manifest["scenario_ids"] == list(soak.SCENARIO_IDS)
    assert summary["observed_metrics"]["scenario_count"] == len(soak.SCENARIO_IDS)
    assert summary["observed_metrics"]["pass_count"] == len(soak.SCENARIO_IDS)
    assert summary["observed_metrics"]["wall_clock_duration_seconds"] >= soak.MINIMUM_DURATION_SECONDS
    assert summary["observed_metrics"]["restart_storm_count"] >= soak.MINIMUM_RESTART_STORM_COUNT
    assert summary["observed_metrics"]["operator_mutation_count"] >= soak.MINIMUM_OPERATOR_MUTATION_COUNT
    assert summary["observed_metrics"]["incident_count"] == len(incidents["incidents"])
    assert summary["observed_metrics"]["negative_controls_passed"] == 2
    assert not incidents["incidents"]
    assert trace_rows


def test_live_soak_failure_storm_validation_exercises_repeated_storm_and_post_incident_window(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"
    soak.run_validation(out_dir=out_dir)

    summary = _load_json(out_dir / "live_soak_metrics_summary.json")
    rows_by_id = {row["scenario_id"]: row for row in summary["scenarios"]}

    assert rows_by_id["LIVE_SOAK_REPEAT_REPAIR_RESET_AFTER_SUCCESS_V1"]["pass"] is True
    assert rows_by_id["LIVE_SOAK_MAINTENANCE_HISTORY_RESETS_AFTER_HEALTHY_GAP_V1"]["pass"] is True
    assert rows_by_id["LIVE_SOAK_RESTART_STORM_FROM_ALREADY_DIRTY_STATE_V1"]["restart_count"] >= soak.MINIMUM_RESTART_STORM_COUNT
    assert rows_by_id["LIVE_SOAK_POST_INCIDENT_OBSERVATION_WINDOW_V1"]["incident_seen"] is True
    assert rows_by_id["LIVE_SOAK_POST_INCIDENT_OBSERVATION_WINDOW_V1"]["post_incident_window_seconds"] >= soak.MINIMUM_POST_INCIDENT_WINDOW_SECONDS
